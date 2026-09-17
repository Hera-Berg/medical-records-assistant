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

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..errors import EndpointNotConfigured, HealthAgentError
from ..extract import session
from ..llm import redaction
from . import endpoint_state, static
from .origin import OriginGuard
from .index import Index
from .routes import (
    artifact,
    ask,
    capture,
    files,
    health,
    reader,
    rebuild,
    record,
    review,
    settings,
    summary,
    welcome,
)
from .serving import API_HEADERS
from .state import RecordState, under_pytest
from .worker import Worker
from ..runtime import choice as choice_mod
from ..runtime import supervisor


def check_endpoint(vault) -> bool:
    """Verify the configured endpoint before anything binds a port.

    Returns whether one is configured at all. Raises
    :class:`agent.errors.EndpointNotPrivate` if one is configured and points
    somewhere public — which is a refusal to start, not a warning and not a
    toggle buried in settings.
    """
    if session.reads_here(vault):
        # Loopback, chosen by this app, on a port nothing configured. There is
        # no address here for anyone to have pasted a commercial API into.
        return False
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

    @asynccontextmanager
    async def lifespan(built: FastAPI):
        """Start the worker with the server, and stop it with the server.

        The worker owns a thread and a socket to the box; tying both ends to the
        app's lifetime is what stops a reloaded process leaving one behind,
        holding a job marked `running` that nothing will ever finish.
        """
        if built.state.worker is not None:
            built.state.worker.start()
        try:
            yield
        finally:
            if built.state.worker is not None:
                built.state.worker.stop()
            # The reader is a child process holding gigabytes. It goes when the
            # server goes, whether or not anything ever woke it.
            running = supervisor.install(None)
            if running is not None:
                running.shutdown()

    app = FastAPI(
        lifespan=lifespan,
        title="Patient-held health record",
        # Not a public API and never will be. The schema routes stay because
        # they cost nothing and make the surface legible to its one user.
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    state = RecordState(vault, clock=clock)
    if session.reads_here(vault):
        # Written once, so a synced config.toml gaining a [models.vlm] table from
        # another machine cannot move this one's documents to a box.
        if not under_pytest():
            choice_mod.settle(vault)
        local_reader = supervisor.get(vault)
        local_reader.has_work = lambda: bool(state.queue().ready(state.now()))
        status = local_reader.status()
        state.set_endpoint(
            endpoint_state.from_reader(status.state, status.reason, None)
        )
    elif not configured:
        state.set_endpoint(endpoint_state.not_configured())

    app.state.record = state
    app.state.index = Index.open(vault.root / ".agent")

    wanted = (not under_pytest()) if worker is None else worker
    app.state.worker = Worker(state, probe_vision=probe_vision) if wanted else None
    # Reachable from the routes without another import, and the capture route
    # nudges it by name.
    state.worker = app.state.worker  # type: ignore[attr-defined]

    _install_handlers(app)

    for module in (
        health,
        ask,
        capture,
        reader,
        record,
        review,
        artifact,
        rebuild,
        files,
        settings,
        summary,
        welcome,
    ):
        app.include_router(module.router)

    @app.get("/api/build", include_in_schema=False)
    def build() -> dict[str, Any]:
        """Which frontend commit the served bundle was built from.

        A committed build can drift from the source it came from, and this is
        what makes that detectable from a running server rather than by reading
        the interface and wondering.
        """
        # Read fresh, so a rebuild while the server runs is reflected without
        # a restart and the answer cannot be stale in the one place whose whole
        # job is saying whether the bundle is stale.
        info = dict(static.build_info())
        info["worker"] = app.state.worker.status() if app.state.worker else None
        return info

    # Registered last: its catch-all path must lose to every API route above.
    static.mount(app)

    # Outermost, so a rebinding page or a cross-site form is refused before any
    # route, handler or other middleware has touched the request.
    app.add_middleware(OriginGuard)

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
