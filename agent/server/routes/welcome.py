"""``POST /api/welcome/done`` — the first run's second question has been answered.

Choosing the record folder leaves a marker on this computer (see
:mod:`agent.firstrun`), and ``/api/health`` reports it as ``welcome``. The
interface then shows which computer reads documents before anything else, and
this route removes the marker when the person moves on. Nothing in the vault is
touched: the choice it follows is this machine's.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ... import firstrun

router = APIRouter()


@router.post("/api/welcome/done")
def done() -> dict[str, Any]:
    firstrun.finish()
    return {"welcome": False}
