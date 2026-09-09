"""``GET /api/health`` — model reachable, vault writable, queue depth, anomalies.

This route is polled. It must therefore be **cheap and it must not block on the
network**: ``MODELS.md`` says "Health check separate from job execution. Poll the
endpoint cheaply, show reachability in the UI … and don't discover unreachability
by failing a 90-second job." So it reports the *last known* state of the box,
with the moment it was learned, and the worker is what learns it. A route that
opened a socket would make every poll wait out a three-second connect timeout
against a sleeping Mac.

Two things it deliberately does not do.

**It never reports a credential, a prefix of one, or its length.** ``auth`` is
``ok``, ``failed`` or ``missing``. The accompanying sentence comes from a fixed
table in :mod:`agent.server.endpoint_state`, selected by a code — no message
from the far end and no exception argument is interpolated into anything this
route returns. Scrubbing is a second line of defence here, not the mechanism.

**It never collapses unreachable into unauthorised.** They are different
problems: one drains by itself when the box wakes, the other needs a person to
set a new key. A single "offline" state is how a rotated key goes uninvestigated
for a week, and the count of queued items sitting behind it keeps growing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ... import vault as vault_mod
from ...extract import jobs as jobs_mod
from ...llm import redaction
from .. import endpoint_state
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


@router.get("/api/health")
def health(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    vault = state.vault
    snapshot = state.snapshot()
    queue = state.queue()
    endpoint = state.endpoint

    counts = queue.counts()
    blocked = counts.get(jobs_mod.BLOCKED_AUTH, 0)
    review = snapshot.projection.review

    by_tier: dict[str, int] = {}
    for item in review:
        by_tier[item.consequence] = by_tier.get(item.consequence, 0) + 1

    problems = _problems(state, snapshot, endpoint, blocked)

    return {
        "ok": not problems,
        "vault": {
            "root": str(vault.root),
            "writable": vault_mod.is_writable(vault.root),
            "sync_profile": vault.profile.value,
            "demo": vault.is_demo,
            "device": _device(vault),
        },
        "endpoint": endpoint.to_dict(),
        "queue": {
            "depth": queue.depth(),
            "parked": queue.is_parked,
            "blocked_auth": blocked,
            "counts": counts,
            "malformed": list(queue.malformed),
        },
        "record": {
            "events": len(snapshot.events),
            "artifacts": len(snapshot.artifacts),
            "entities": snapshot.projection.stats()["entities"],
            "as_of": snapshot.built_ts,
        },
        "review": {
            "total": len(review),
            "by_tier": {tier: by_tier.get(tier, 0) for tier in ("high", "medium", "low")},
        },
        # Regenerable from the log, so never persisted — but surfaced here so
        # they cannot scroll past unseen. See CLAUDE.md, "Anomalies live in the
        # rebuild report, not the wiki".
        "anomalies": {
            "count": snapshot.anomaly_count,
            "items": [redaction.scrub(str(note)) for note in snapshot.anomalies()],
        },
        "problems": problems,
    }


def _device(vault) -> dict[str, Any]:
    """This machine's identity, or why it cannot append.

    A cloned or restored identity file means this machine must not write to a
    shard another machine is also writing to. Reading is unaffected, so the
    server still serves — it says so here rather than failing a capture later.
    """
    try:
        identity = vault.identity
    except Exception as exc:  # noqa: BLE001 - reported, never raised from health
        return {"id": None, "appendable": False, "reason": str(exc)}
    mismatch = identity.mismatch_reason()
    return {
        "id": identity.id,
        "label": identity.label,
        "appendable": mismatch is None,
        "reason": mismatch,
    }


def _problems(state: RecordState, snapshot, endpoint, blocked: int) -> list[str]:
    """Things a person has to act on. Not a list of everything imperfect.

    An unreachable box is not a problem: captures queue and drain by themselves,
    which is the design. A rejected key is, and so is a vault that cannot be
    written to.
    """
    problems: list[str] = []
    if not vault_mod.is_writable(state.vault.root):
        problems.append(
            f"the vault at {state.vault.root} is not writable, so nothing can be "
            f"captured into it"
        )
    if endpoint.state == endpoint_state.UNAUTHORISED or blocked:
        problems.append(endpoint_state.MESSAGES["key-rejected"])
    elif endpoint.state in (endpoint_state.MISCONFIGURED, endpoint_state.BLIND):
        problems.append(endpoint.to_dict()["message"])
    for line in snapshot.read.malformed:
        problems.append(f"unreadable event line: {line.describe()}")
    for conflict in state.vault.conflicts():
        problems.append(conflict.describe(state.vault.root))
    return problems
