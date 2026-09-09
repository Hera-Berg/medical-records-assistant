"""Building the application: one vault, one app, one origin.

The endpoint guard runs here rather than on the first inference call. ``MODELS.md``
is explicit that reaching a commercial API is "a startup failure, not a config
option": paste ``https://api.openai.com/v1`` into ``config.toml`` and the entire
privacy premise of the project evaporates with no visible change in behaviour. So
a configured endpoint that resolves outside private address space stops the
server from being built at all, before a port is bound and before a credential is
read — the ordering that means the key never leaves the machine even if the guard
were to fail open later.

A vault with **no** endpoint configured is a different thing and is not an error.
A demo vault is deliberately one. Capture works, the record serves, and
``/api/health`` says the box is not configured rather than pretending it is
asleep.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..errors import EndpointNotConfigured, HealthAgentError
from ..extract import session
from ..llm import redaction
from . import endpoint_state, static
from .index import Index
from .routes import artifact, capture, health, rebuild, record
from .serving import API_HEADERS
from .state import RecordState, under_pytest
from .worker import Worker


def check_endpoint(vault) -> bool:
    """Verify the configured endpoint before anything binds a port.

    Returns whether one is configured at all. Raises
    :class:`agent.errors.EndpointNotPrivate` if one is configured and points
    somewhere public — which is a refusal to start, not a warning and not a
    toggle buried in settings.
    """
    try:
        settings = session.settings_for(vault)
    except EndpointNotConfigured:
        return False
    # Resolves the host and requires it in Tailscale CGNAT, RFC1918 or
    # loopback. Raises with the whole explanation if it is anywhere else.
    settings.vlm.endpoint.verify()
    return True


def create_app(
    vault,
    worker: bool | None = None,
    probe_vision: bool = True,
    clock=None,
) -> FastAPI:
    """Build the application for *vault*.

    ``worker`` defaults to on, except under pytest where it defaults to off: a
    test that passes because a daemon thread happened to be scheduled first is a
    test that fails on a slower machine for reasons nobody can reproduce. A test
    that wants the worker drives :meth:`agent.server.worker.Worker.run_once`
    directly instead.
    """
    redaction.install()
    configured = check_endpoint(vault)

    app = FastAPI(
        title="Patient-held health record",
        # Not a public API and never will be. The schema routes stay because
        # they cost nothing and make the surface legible to its one user.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    state = RecordState(vault, clock=clock)
    if not configured:
        state.set_endpoint(endpoint_state.not_configured())

    app.state.record = state
    app.state.index = Index.open(vault.root / ".agent")
    app.state.build_info = static.build_info()

    wanted = (not under_pytest()) if worker is None else worker
    app.state.worker = Worker(state, probe_vision=probe_vision) if wanted else None
    # Reachable from the routes without another import, and the capture route
    # nudges it by name.
    state.worker = app.state.worker  # type: ignore[attr-defined]

    _install_handlers(app)

    for module in (health, capture, record, artifact, rebuild):
        app.include_router(module.router)

    @app.get("/api/build", include_in_schema=False)
    def build() -> dict[str, Any]:
        """Which frontend commit the served bundle was built from.

        A committed build can drift from the source it came from, and this is
        what makes that detectable from a running server rather than by reading
        the interface and wondering.
        """
        info = dict(app.state.build_info)
        info["worker"] = app.state.worker.status() if app.state.worker else None
        return info

    # Registered last: its catch-all path must lose to every API route above.
    static.mount(app)

    @app.on_event("startup")
    def _start() -> None:
        if app.state.worker is not None:
            app.state.worker.start()

    @app.on_event("shutdown")
    def _stop() -> None:
        if app.state.worker is not None:
            app.state.worker.stop()

    return app


def _install_handlers(app: FastAPI) -> None:
    """Error responses that say what happened, with nothing sensitive in them."""

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") and not request.url.path.startswith(
            "/api/artifact/"
        ):
            for name, value in API_HEADERS.items():
                response.headers.setdefault(name, value)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": redaction.scrub(exc.detail)},
            headers=API_HEADERS,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "the request was not in the expected shape",
                     "errors": redaction.scrub_structure(exc.errors())},
            headers=API_HEADERS,
        )

    @app.exception_handler(HealthAgentError)
    async def _agent_error(request: Request, exc: HealthAgentError):
        """Every refusal in this program is written to be read.

        These messages name the vault, the shard, the artefact and what to do
        next, so they are passed through rather than replaced with "internal
        error" — scrubbed first, because a message assembled anywhere near the
        inference client could quote a request.
        """
        return JSONResponse(
            status_code=400,
            content={"detail": redaction.scrub(str(exc))},
            headers=API_HEADERS,
        )
