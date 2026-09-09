"""The HTTP layer: one origin, bound to loopback, serving the SPA and the record.

Phases 1 to 4 built a vault, an ingest path, a projection and an extractor, all
driven from a terminal. This puts a window on them. It adds no new authority: the
routes read the same projection the CLI reads, capture takes the same ingest path
``health-agent ingest`` takes, and nothing here writes to ``wiki/`` except the
one route whose whole job is to replay the log into it.

Three things about this layer are load-bearing rather than incidental.

**One origin.** The SPA is built to static files and served by the same FastAPI
process at ``/``. No dev proxy in production, no second port, no CORS. A browser
that has loaded the page can reach the API and nothing else can, because nothing
else can reach the port.

**Loopback only.** ``127.0.0.1`` is not a default that can be widened in config.
There is no auth system here and there is never going to be one — the record is
single-user and the port is the boundary. Reaching it from another machine is
the user's VPN problem, and :func:`serve` refuses any host that is not loopback.

**Capture never waits for a model.** ``POST /api/capture`` writes bytes into
``raw/``, appends one event, queues a job and returns. The user is in a waiting
room. Whether the inference box is awake is not their problem and must never
become their latency.
"""

from __future__ import annotations

from .app import create_app
from .runtime import LOOPBACK_HOSTS, serve

__all__ = ["LOOPBACK_HOSTS", "create_app", "serve"]
