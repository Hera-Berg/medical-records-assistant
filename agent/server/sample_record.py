"""The sample record: a complete record served beside the person's own, never mixed with it.

The workspace switch offers "Demo patient" and "Demo clinician" beside the
person's own workspaces. They show a separate record that ships with the
application (``agent/demo/sample/record``), built from authored material by
``agent.demo.sample``. It exists so someone can see what a fully used record looks
like without putting anything of their own in it.

**Two apps, one per record.** The sample is served by its own instance of
:func:`agent.server.app.create_app`, with its own record state, projection, index,
clinician sessions and Ask pipeline. A request reaches exactly one of the two, and
every route reads ``request.app.state``, so a question asked of one record cannot
retrieve a passage from the other: there is no code path that holds both. The only
things the two share are the interface files, which are the same bytes, and, when
Ask runs, the reader process, which is sent only what the receiving app retrieved.

**Which app a request reaches is decided per tab, not per browser.** A cookie is
shared by every tab, so a cookie alone would let one tab switching to the sample
silently move another tab, still showing the person's own record, onto the sample:
a question typed there would be answered from invented data. So the interface
sends ``X-Health-Record: own | demo`` on every request it makes, fixed when the tab
loads, and that header wins. The ``care_record`` cookie decides only requests that
cannot carry a header (opening a page, or a document in a new tab), and with
neither the request goes to the person's own record.

**The copy on disk is rebuilt at every start**, from the committed files into this
machine's app data folder, never inside the person's record. Whatever a visitor
does to the sample lasts until the app restarts, and the sample always opens as
shipped.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import files
from ..clinician_auth import AuthStore, COOKIE as CLINICIAN_COOKIE, ITERATIONS, MAX_SECONDS, password_hash, vault_id
from ..errors import VaultError
from ..runtime import platforms
from .origin import OriginGuard

HEADER = "x-health-record"
COOKIE = "care_record"
OWN = "own"
SAMPLE = "demo"
#: The sample app's own clinician session, renamed at the boundary so signing in
#: to the sample never replaces a session on the person's own record.
SAMPLE_CLINICIAN_COOKIE = "care_sample_clinician_session"
#: The moment the sample record describes: 21:14 on 3 November 2026 in Sydney.
#: Its clock stays here, so "upcoming" appointments stay upcoming and "since your
#: last consultation" means what the record was written to show.
SNAPSHOT = datetime(2026, 11, 3, 10, 14, tzinfo=timezone.utc)

PACKAGE = Path(__file__).resolve().parents[1] / "demo" / "sample"
SOURCE = PACKAGE / "record"
NOTE_KEY = PACKAGE / "note-key.json"

#: Accounts on the sample record. The password is the same for both and is said
#: on the sign-in page; the "Demo clinician" option signs in as the first.
CLINICIANS = (
    ("james.smith", "Dr James Smith", "Usual GP", "Dr Smith"),
    ("olivia.bennett", "Dr Olivia Bennett", "Locum GP", "Dr Bennett"),
)
PASSWORD = "password"


def available() -> bool:
    """Whether this copy of the program carries the sample record."""
    return (SOURCE / "config.toml").is_file() and NOTE_KEY.is_file()


def location() -> Path:
    """Where the working copy lives: this machine's app data, beside the reader."""
    return platforms.data_home() / "sample-record"


def materialise(own_root: Path, target: Path | None = None) -> Path:
    """Copy the shipped sample to *target*, replacing any earlier copy.

    Refuses a target inside the person's record or containing it: the sample
    must never be written into, or written over, a real record.
    """
    target = Path(target or location())
    own_root = Path(own_root)
    if platforms.is_inside(target, own_root) or platforms.is_inside(own_root, target):
        raise VaultError(
            f"refusing to place the sample record at {target}: that is inside the "
            f"record at {own_root}, or contains it."
        )
    _remove(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", ".agent", "wiki"))
    # Git keeps no empty folders, so exports/ and bulk/ may not have shipped.
    from ..vault import scaffold

    scaffold(target)
    _install_note_key(target)
    return target


def _remove(path: Path) -> None:
    """Delete an earlier working copy, including originals stored read-only."""
    if not path.exists():
        return

    def writable(function, name, _exc):
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(name)

    shutil.rmtree(path, onexc=writable) if _has_onexc() else shutil.rmtree(path, onerror=writable)


def _has_onexc() -> bool:
    import sys

    return sys.version_info >= (3, 12)


def _install_note_key(root: Path) -> None:
    """Put the sample's progress-note key where the notes code looks for it.

    The sample's notes ship encrypted, so the key that opens them ships too. It is
    written outside the record, exactly where a real record's key lives, keyed to
    the working copy's path.
    """
    from ..progress_notes import key_path

    path = key_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | files.O_BINARY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(NOTE_KEY.read_bytes())
    os.replace(temporary, path)


class SampleAuthStore(AuthStore):
    """The sample's clinician accounts, held in memory and never written anywhere."""

    def __init__(self, root, clock=time.monotonic):
        super().__init__(root, clock)
        patient = vault_id(root)
        self.accounts = {}
        for username, name, role, salutation in CLINICIANS:
            salt = secrets.token_hex(16)
            self.accounts[username] = {
                "username": username, "name": name, "role": role, "salutation": salutation,
                "patients": [patient], "development_only": True,
                "password": {"algorithm": "pbkdf2-sha256", "iterations": ITERATIONS,
                             "salt": salt, "hash": password_hash(PASSWORD, salt)},
            }

    def open_session(self, username: str):
        """Sign in without a password. Exists only on the sample's store."""
        account = self.accounts[username]
        now = self.clock()
        with self.lock:
            token = secrets.token_urlsafe(32)
            session = {"account": account, "created": now, "used": now, "csrf": secrets.token_urlsafe(32)}
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
            return token, session


def build_app(root: Path):
    """The sample's own app, over the working copy at *root*."""
    from ..vault import Vault
    from .app import create_app

    vault = Vault.open(root)
    app = create_app(vault, worker=False, clock=lambda: SNAPSHOT, sample=True)
    app.state.clinician_auth = SampleAuthStore(vault.root)
    return app


class RecordRouter:
    """Sends each request to the person's own record or to the sample, never both.

    ``state`` is the own app's, so code holding the served app (the desktop
    launcher reads ``app.state.record``) still sees the person's record.
    """

    def __init__(self, own, own_root: Path, sample_root: Path | None = None):
        self.own = own
        self.state = own.state
        self.own_root = Path(own_root)
        self.sample_root = sample_root
        self._sample = None
        self._failure: str | None = None
        self._lock = threading.Lock()
        self._control = OriginGuard(
            Starlette(routes=[
                Route("/api/demo", self._status, methods=["GET"]),
                Route("/api/demo/enter", self._enter, methods=["POST"]),
                Route("/api/demo/leave", self._leave, methods=["POST"]),
            ])
        )

    # ---- which record -------------------------------------------------------

    @staticmethod
    def chosen(scope) -> str | None:
        """``own`` or ``demo`` for this request; ``None`` for a header naming neither."""
        named = _header(scope, HEADER.encode())
        if named is not None:
            value = named.strip().lower()
            return value if value in (OWN, SAMPLE) else None
        return SAMPLE if _cookies(scope).get(COOKIE) == SAMPLE else OWN

    def sample(self):
        """The sample's app, built on first use."""
        with self._lock:
            if self._sample is None and self._failure is None:
                try:
                    if not available():
                        raise VaultError("This copy of the app does not include the demo record.")
                    root = materialise(self.own_root, self.sample_root)
                    self._sample = build_app(root)
                except Exception as exc:  # noqa: BLE001 - reported to the screen, never retried in a loop
                    self._failure = str(exc) or exc.__class__.__name__
            if self._sample is None:
                raise VaultError(self._failure or "The demo record could not be opened.")
            return self._sample

    # ---- ASGI ---------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # Lifespan belongs to the person's record: its worker, its reader.
            # The sample has no worker, and its shutdown must not stop the reader.
            await self.own(scope, receive, send)
            return
        if scope["path"] in ("/api/demo", "/api/demo/enter", "/api/demo/leave"):
            await self._control(scope, receive, send)
            return
        which = self.chosen(scope)
        if which is None:
            await _json(send, 400, {"detail": "X-Health-Record must be 'own' or 'demo'."})
            return
        if which == OWN:
            await self.own(scope, receive, send)
            return
        try:
            app = await run_in_threadpool(self.sample)
        except VaultError as exc:
            await _json(send, 503, {"detail": str(exc)})
            return
        await app(_to_sample(scope), receive, _from_sample(send))

    # ---- control routes -----------------------------------------------------

    async def _status(self, request: Request):
        record = SAMPLE if request.cookies.get(COOKIE) == SAMPLE else OWN
        return _no_store(JSONResponse({"record": record, "available": available()}))

    async def _enter(self, request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = None
        workspace = body.get("workspace") if isinstance(body, dict) else None
        if workspace not in ("patient", "clinician"):
            return _no_store(JSONResponse({"detail": "Choose the patient or clinician workspace."}, status_code=400))
        try:
            app = await run_in_threadpool(self.sample)
        except VaultError as exc:
            return _no_store(JSONResponse({"detail": str(exc)}, status_code=503))
        response = JSONResponse({"record": SAMPLE, "path": "/clinician" if workspace == "clinician" else "/"})
        secure = request.url.scheme == "https"
        # A session cookie: closing the browser returns every tab to the
        # person's own record.
        response.set_cookie(COOKIE, SAMPLE, httponly=True, samesite="strict", secure=secure, path="/")
        if workspace == "clinician":
            token, _ = app.state.clinician_auth.open_session(CLINICIANS[0][0])
            response.set_cookie(SAMPLE_CLINICIAN_COOKIE, token, httponly=True, samesite="strict",
                                secure=secure, max_age=MAX_SECONDS, path="/")
        return _no_store(response)

    async def _leave(self, request: Request):
        response = JSONResponse({"record": OWN})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict",
                               secure=request.url.scheme == "https")
        return _no_store(response)


def wrap(app, vault):
    """Serve *app* (the person's record) with the sample beside it."""
    return RecordRouter(app, vault.root)


# ---- helpers ---------------------------------------------------------------


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", ()):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _cookies(scope) -> dict[str, str]:
    jar: dict[str, str] = {}
    for key, value in scope.get("headers", ()):
        if key.lower() != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            name, sep, content = part.strip().partition("=")
            if sep:
                jar.setdefault(name, content)
    return jar


def _to_sample(scope):
    """The request as the sample app sees it: its own session under the usual name.

    The person's own clinician session is removed, so the sample never receives
    it, and the sample's is presented under the name the sample app reads.
    """
    kept = []
    jar = []
    for key, value in scope.get("headers", ()):
        if key.lower() != b"cookie":
            kept.append((key, value))
            continue
        for part in value.decode("latin-1").split(";"):
            name, sep, content = part.strip().partition("=")
            if not sep or name == CLINICIAN_COOKIE:
                continue
            if name == SAMPLE_CLINICIAN_COOKIE:
                name = CLINICIAN_COOKIE
            jar.append(f"{name}={content}")
    if jar:
        kept.append((b"cookie", "; ".join(jar).encode("latin-1")))
    return {**scope, "headers": kept}


def _from_sample(send):
    """Rename the sample's session cookie on the way out, so the two never collide."""
    prefix = (CLINICIAN_COOKIE + "=").encode("latin-1")
    renamed = (SAMPLE_CLINICIAN_COOKIE + "=").encode("latin-1")

    async def forward(message):
        if message["type"] == "http.response.start":
            headers = []
            for key, value in message.get("headers", ()):
                if key.lower() == b"set-cookie" and value.startswith(prefix):
                    value = renamed + value[len(prefix):]
                headers.append((key, value))
            message = {**message, "headers": headers}
        await send(message)

    return forward


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


async def _json(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(data)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": data})


def new_note_key() -> bytes:
    """A note key in the format :mod:`agent.progress_notes` reads. Used by the builder."""
    body = {"version": 1, "id": secrets.token_hex(16), "key": base64.b64encode(os.urandom(32)).decode()}
    return json.dumps(body).encode("utf-8")
