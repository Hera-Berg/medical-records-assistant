"""``/api/settings`` — the small number of things the owner may change.

One setting so far: ``sync_profile``. It is worth being clear about what that
setting *is*, because the obvious reading of it is wrong and the obvious reading
is dangerous.

**It describes where the folder already is. It does not move it.** Nothing here
copies a file, and no sync service's API is called from anywhere in this
codebase — there are no backend classes and there never will be, because a
backend interface is the seam through which someone later adds a real API client
and silently breaks the privacy model. Every profile is a folder on disk. The
profile selects four behaviours and nothing else: which conflict filename
patterns the scan reports, whether an append is read back before being called
done, whether files that are listed but not downloaded are expected, and what
the setup warning says.

**Choosing a synced profile is an admission, not an instruction.** The user is
telling the record that their whole medical history is already inside someone
else's storage. So the answer to that is a warning, shown before the change is
applied rather than after — and the warning is about the account and about
never putting a key in ``config.toml``, which is the specific mistake that turns
"synced" into "disclosed".

Writing ``config.toml`` at all is the one exception to the rule that this
program never writes that file. See :func:`agent.config.set_sync_profile` for
why the exception is narrow and how the write protects the file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import config as config_mod
from ... import vault as vault_mod
from ...config import CONFIG_FILENAME, SyncProfile
from ...errors import ConfigError
from ..deps import get_state
from ..state import RecordState

router = APIRouter()

#: Said on the settings screen, above the choice. The interface has to rule out
#: the reading a person will otherwise arrive at on their own — that picking
#: "Dropbox" here puts their record into Dropbox.
EXPLANATION = (
    "This says where your folder already is. It does not move anything, it does "
    "not copy anything, and it never signs in to Dropbox, Google or Nextcloud — "
    "this app only ever reads and writes files, and lets your own sync app do "
    "the syncing. Telling it the truth here means it can spot the mess a sync "
    "app makes when two computers save at once."
)


def _option(profile: SyncProfile, current: SyncProfile) -> dict[str, Any]:
    return {
        "value": profile.value,
        "label": profile.label,
        "effects": list(profile.effects),
        "warning": profile.setup_warning,
        "current": profile is current,
    }


def _settings(state: RecordState) -> dict[str, Any]:
    vault = state.vault
    path = vault.root / CONFIG_FILENAME
    forks = config_mod.conflict_forks(path)
    current = vault.profile
    return {
        "explanation": EXPLANATION,
        "sync_profile": {
            "current": current.value,
            "options": [_option(profile, current) for profile in SyncProfile],
            "warning": current.setup_warning,
        },
        "config": {
            "path": str(path),
            "writable": vault_mod.is_writable(vault.root),
            # Named, not counted. "A sync client has forked your settings file"
            # is only actionable if the reader is told which file to go and look
            # at, and the name carries the date the fork happened.
            "conflict_forks": [fork.name for fork in forks],
        },
        "vault": {"root": str(vault.root), "demo": vault.is_demo},
    }


@router.get("/api/settings")
def settings(state: RecordState = Depends(get_state)) -> dict[str, Any]:
    """What can be changed, what it is now, and what changing it would mean."""
    return _settings(state)


@router.post("/api/settings/sync-profile")
def set_sync_profile(
    profile: str = Body(..., embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Record where the folder lives. Moves nothing, contacts nothing.

    The refusals come back as the sentences :mod:`agent.config` writes, because
    each of them names the file to go and fix and what will happen if it is not
    fixed — a fork beside ``config.toml`` in particular.
    """
    try:
        chosen = SyncProfile(profile)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown storage profile {profile!r}; expected one of "
                f"{', '.join(p.value for p in SyncProfile)}"
            ),
        ) from None

    path = state.vault.root / CONFIG_FILENAME
    with state.lock:
        try:
            config_mod.set_sync_profile(path, chosen)
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        state.reload_config()

    result = _settings(state)
    result["changed"] = chosen.value
    # The scan runs with the new patterns immediately, so a fork this profile
    # newly cares about is reported on the same response that changed it rather
    # than on the next poll.
    result["conflicts"] = [
        conflict.describe(state.vault.root) for conflict in state.vault.conflicts()
    ]
    return result
