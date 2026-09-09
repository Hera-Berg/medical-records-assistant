"""Serving the built SPA, and saying something useful when there isn't one.

The built files are **committed to the repository**. This project is meant to be
self-hosted by someone who has Python and nothing else, and a Vite build standing
between ``pip install`` and a working app puts a node toolchain in the install
story of a personal health record. Building the frontend is a developer step; the
output of it is a product artefact and ships like one.

Everything is served from one origin. No CDN, no webfont, no analytics endpoint,
no source-map host — invariant 3, enforced in the browser by the content security
policy in :mod:`agent.server.serving` rather than merely intended by the bundler
config.

When the build is absent — a fresh clone whose ``static_files`` was cleaned, a
developer mid-change — ``/`` serves a plain page saying so, with the command to
run. A 404 at the root of an app that starts successfully is the least helpful
thing this could do.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .serving import API_HEADERS, APP_CONTENT_SECURITY_POLICY

#: Where ``npm run build`` writes, and where the wheel carries it.
STATIC_DIRNAME = "static_files"

#: Written by the build so a running server can say which frontend commit its
#: bundle came from. A committed build can go stale against its source, and
#: staleness has to be detectable rather than discovered as a UI that does not
#: match the code.
BUILD_INFO_FILENAME = "build-info.json"

INDEX_FILENAME = "index.html"


def static_dir() -> Path:
    return Path(__file__).resolve().parent / STATIC_DIRNAME


def build_info() -> dict[str, str | None]:
    """What the committed bundle says about itself."""
    path = static_dir() / BUILD_INFO_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": False, "commit": None, "built": None, "source": None}
    return {
        "present": True,
        "commit": data.get("commit"),
        "built": data.get("built"),
        "source": data.get("source"),
    }


def is_built() -> bool:
    return (static_dir() / INDEX_FILENAME).is_file()


_NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Health record — interface not built</title>
<style>
 body {{ font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI",
        Roboto, Helvetica, Arial, sans-serif;
        max-width: 42rem; margin: 4rem auto; padding: 0 1.5rem; color: #111; }}
 h1 {{ font-size: 1.25rem; margin: 0 0 1rem; }}
 code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
 pre {{ background: #f4f4f4; padding: .75rem 1rem; overflow-x: auto; }}
 p {{ margin: 0 0 1rem; }}
</style></head><body>
<h1>The interface has not been built</h1>
<p>The server is running and the API is answering — your record is fine. What is
missing is the compiled frontend, which normally ships committed to the
repository at <code>{path}</code>.</p>
<p>Build it with:</p>
<pre>npm --prefix frontend ci
npm --prefix frontend run build</pre>
<p>Until then the record is reachable at
<a href="/api/health">/api/health</a>, and everything the interface does is
also a <code>health-agent</code> subcommand on the terminal.</p>
</body></html>
"""


def mount(app) -> None:
    """Attach the SPA routes. Registered last, so every API path wins."""
    router = APIRouter()
    directory = static_dir()

    @router.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/"):
            # Reached only when no API route matched. A bare 404 here would be
            # indistinguishable from the SPA fallback swallowing a typo.
            return JSONResponse(
                status_code=404,
                content={"detail": f"no API route at /{path}"},
                headers=API_HEADERS,
            )

        target = _safe_asset(directory, path)
        if target is not None:
            return FileResponse(
                target,
                headers={
                    "x-content-type-options": "nosniff",
                    "referrer-policy": "no-referrer",
                    # Hashed filenames from the bundler; the index below is not.
                    "cache-control": "public, max-age=31536000, immutable"
                    if _is_hashed(target.name)
                    else "no-cache",
                },
            )

        index = directory / INDEX_FILENAME
        if not index.is_file():
            return HTMLResponse(
                _NOT_BUILT.format(path=directory),
                status_code=503,
                headers={"cache-control": "no-store"},
            )
        return FileResponse(
            index,
            headers={
                "content-security-policy": APP_CONTENT_SECURITY_POLICY,
                "x-content-type-options": "nosniff",
                "referrer-policy": "no-referrer",
                "cache-control": "no-cache",
            },
        )

    app.include_router(router)


def _safe_asset(directory: Path, path: str) -> Path | None:
    """The file *path* names inside the bundle, or ``None``.

    Resolved and then checked to be under the bundle directory. The client
    controls this string, and ``../`` in it must not reach the vault — which is
    on the same disk and full of exactly the thing this program exists to keep
    private.
    """
    if not path or path.endswith("/"):
        return None
    try:
        target = (directory / path).resolve()
    except OSError:
        return None
    base = directory.resolve()
    if base not in target.parents:
        return None
    return target if target.is_file() else None


def _is_hashed(name: str) -> bool:
    """Whether a bundler put a content hash in this filename."""
    stem = name.rsplit(".", 1)[0]
    parts = stem.split("-")
    return len(parts) > 1 and len(parts[-1]) >= 8 and parts[-1].isalnum()
