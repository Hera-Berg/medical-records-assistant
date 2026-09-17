"""The server the app runs before there is a record to serve.

Three situations reach it, and the page says which:

- **No folder chosen yet** — the first run. The page offers places to create the
  record, a folder on this computer first.
- **The folder cannot be opened** — it has moved, a drive is not plugged in, the
  settings file will not load. The refusal is the sentence the record itself
  wrote, with a way to try again or choose where the folder is now.
- **This computer's identity came from another computer** — a restored backup, a
  cloned disk. Appending under it is how two computers end up writing to one
  event file, so it is refused until the person chooses to give this computer
  a new one. The old file is kept.

It is the same origin, the same interface bundle and the same guard as the
record's own server, on the same port; the interface asks ``/api/setup`` first
and shows these pages when it answers. When the person has chosen, the launcher
stops this server and starts the record's in its place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .. import distribution
from ..errors import HealthAgentError
from ..llm import redaction
from ..runtime import platforms
from ..server import static
from ..server.origin import OriginGuard
from ..server.serving import API_HEADERS
from . import locations as locations_mod
from . import setup as setup_mod

CHOOSE = "choose"
PROBLEM = "problem"

FOLDER_MISSING = "folder-missing"
IDENTITY_ELSEWHERE = "identity-elsewhere"
CANNOT_OPEN = "cannot-open"


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": redaction.scrub(self.message), "root": self.root}


def create_setup_app(
    problem: Problem | None,
    on_chosen: Callable[[], None],
    on_retry: Callable[[], None],
) -> FastAPI:
    """*on_chosen* and *on_retry* hand control back to the launcher, after the reply."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    state: dict[str, Problem | None] = {"problem": problem}

    @app.get("/api/setup")
    def describe() -> JSONResponse:
        current = state["problem"]
        platform = platforms.current()
        return JSONResponse(
            {
                "stage": PROBLEM if current is not None else CHOOSE,
                "problem": current.to_dict() if current is not None else None,
                "packaged": distribution.packaged(),
                "platform": platform,
                "platform_label": platforms.LABELS.get(platform or "", "this computer"),
                "locations": [location.to_dict() for location in locations_mod.detect()],
                "default_name": locations_mod.DEFAULT_FOLDER_NAME,
            },
            headers=API_HEADERS,
        )

    @app.post("/api/setup/examine")
    def examine(parent: str = Body(..., embed=True), name: str = Body(..., embed=True)):
        return JSONResponse(setup_mod.examine(parent, name).to_dict(), headers=API_HEADERS)

    @app.post("/api/setup/choose")
    def choose(
        parent: str = Body(..., embed=True),
        name: str = Body(..., embed=True),
        action: str = Body(..., embed=True),
    ):
        plan = setup_mod.examine(parent, name)
        if not plan.ok:
            raise HTTPException(status_code=409, detail=plan.refusal)
        if plan.action != action:
            # What the screen showed is not what would happen now — the folder
            # changed between looking and agreeing. Say so rather than doing
            # the other thing.
            raise HTTPException(
                status_code=409,
                detail=(
                    "That folder has changed since it was checked. Look at what it "
                    "says now before choosing it."
                ),
            )
        try:
            setup_mod.carry_out(plan)
        except (HealthAgentError, OSError) as exc:
            raise HTTPException(status_code=409, detail=redaction.scrub(str(exc))) from None
        state["problem"] = None
        on_chosen()
        return JSONResponse({"done": True, "target": str(plan.target)}, headers=API_HEADERS)

    @app.post("/api/setup/retry")
    def retry():
        on_retry()
        return JSONResponse({"retrying": True}, headers=API_HEADERS)

    @app.post("/api/setup/new-identity")
    def new_identity():
        current = state["problem"]
        if current is None or current.code != IDENTITY_ELSEWHERE:
            raise HTTPException(
                status_code=409,
                detail="This computer's identity is not the problem, so it was left as it is.",
            )
        try:
            setup_mod.replace_identity()
        except (HealthAgentError, OSError) as exc:
            raise HTTPException(status_code=409, detail=redaction.scrub(str(exc))) from None
        on_retry()
        return JSONResponse({"retrying": True}, headers=API_HEADERS)

    static.mount(app)
    app.add_middleware(OriginGuard)
    return app
