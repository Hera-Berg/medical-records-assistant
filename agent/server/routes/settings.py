"""``/api/settings`` — the small number of things the owner may change.

Two settings: ``sync_profile``, and the inference endpoint.

The endpoint half is a user interface over machinery that already exists — the
address guard, credential resolution, ``set-key`` and the startup probe are all
phase-4 code and none of it changes here. What it adds is that a person can
point their record at their own box without editing TOML, and that the one
mistake they are most likely to make while doing it is caught in a place where
it can be explained rather than in a stack trace at the next startup.

Three rules run through every route below.

**The key is set-only.** There is no route here or anywhere else that returns
it, a prefix of it, or its length. The field reports ``configured`` or
``not set`` and the place a key was found — "the keychain" is a place, not a
secret — and nothing more.

**It never goes in ``config.toml``.** That file is at the vault root and syncs
with the folder, so a key written there has been handed to a third party
whether or not anybody reads it. The config secret scan already refuses to load
such a file; what this adds is the sentence saying *why*, next to the field,
before the mistake rather than after it.

**The address guard runs at save time.** Not at first use: a refusal that
arrives when a photograph fails to be read, days later, is a refusal nobody can
connect to what they typed.

The first setting is ``sync_profile``. It is worth being clear about what that
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

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import config as config_mod
from ... import vault as vault_mod
from ...config import CONFIG_FILENAME, SyncProfile
from ...errors import (
    ConfigError,
    CredentialError,
    EndpointNotConfigured,
    EndpointNotPrivate,
    HealthAgentError,
)
from ...extract import session
from ...llm import credentials as credentials_mod
from ...llm import redaction
from ...llm.settings import (
    DEFAULT_AUTH_HEADER,
    DEFAULT_AUTH_SCHEME,
    ModelSettings,
    VlmSettings,
)
from .. import endpoint_check, endpoint_state
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
        "endpoint": _endpoint(state),
        "vault": {"root": str(vault.root), "demo": vault.is_demo},
    }


#: The one sentence in front of the fields.
#:
#: It was three paragraphs. Three paragraphs before the first input is not
#: reassurance, it is a wall, and the reader who most needs the reassurance is
#: the one least likely to read to the end of it. What it has to establish is
#: that this is a machine they already own and that nothing leaves their
#: network; everything else is true, useful, and answers a question nobody has
#: asked yet.
ENDPOINT_EXPLANATION = (
    "Your documents are read by a model on a computer you own, and nothing "
    "leaves your own network."
)

#: The rest, behind "What is this?". Not hidden — one tap away, and the tap is
#: taken by the person who wants it rather than paid for by everyone.
ENDPOINT_ABOUT = (
    "The record needs something that can read a photograph of a prescription. "
    "That is a model, and it runs on a computer you control — a desktop at home, "
    "reached over your own private network. It will not talk to a company's "
    "service or anything else outside that network: the app refuses to start if "
    "you point it at one, which is the whole reason your record can live in a "
    "folder you own.\n\n"
    "If that computer is off or asleep, nothing breaks. What you add waits in "
    "your folder and is read when it comes back."
)


def _key_state(vlm: VlmSettings | None, vault_root) -> dict[str, Any]:
    """Whether a key is available, and where from. Never the key.

    The source is reported because of a trap that is otherwise invisible: the
    environment beats the keychain, so on a machine where ``HEALTH_VLM_TOKEN``
    is exported, storing a new key in the keychain changes nothing and the old
    one keeps being sent. Saying which place answered is what makes that
    five seconds of confusion rather than an afternoon of it.
    """
    env = vlm.auth.api_key_env if vlm else None
    found, detail = credentials_mod.status(env, vault_root)
    if found == "ok":
        return {"state": "configured", "source": detail, "detail": None}
    if found == "missing":
        return {"state": "not set", "source": None, "detail": None}
    return {"state": "unusable", "source": None, "detail": redaction.scrub(detail)}


def _endpoint(state: RecordState) -> dict[str, Any]:
    """Where the box is and how to talk to it — with nothing secret in it."""
    vault = state.vault
    problem: str | None = None
    models: ModelSettings | None = None
    try:
        models = session.settings_for(vault)
    except EndpointNotConfigured:
        pass
    except HealthAgentError as exc:
        # A table that is there but will not load. Shown rather than swallowed:
        # the fields below would otherwise come back empty, which reads as "not
        # set up yet" when the truth is "set up wrongly, here is where".
        problem = redaction.scrub(str(exc))

    vlm = models.vlm if models else None
    return {
        "explanation": ENDPOINT_EXPLANATION,
        "about": ENDPOINT_ABOUT,
        "configured": vlm is not None,
        "base_url": vlm.endpoint.base_url if vlm else "",
        "model": vlm.model if vlm else "",
        "auth": {
            "header": vlm.auth.header if vlm else DEFAULT_AUTH_HEADER,
            "scheme": vlm.auth.scheme if vlm else DEFAULT_AUTH_SCHEME,
            # Named so a person can see *why* the environment is winning, when
            # it is. Only ever the name of a variable, never its contents.
            "api_key_env": vlm.auth.api_key_env if vlm else None,
        },
        "defaults": {"header": DEFAULT_AUTH_HEADER, "scheme": DEFAULT_AUTH_SCHEME},
        "key": _key_state(vlm, vault.root),
        "key_explanation": credentials_mod.NEVER_IN_CONFIG,
        # Shown only when storing a key fails for want of a keychain. Kept here
        # rather than in the error so that the error can be one sentence naming
        # one thing to do.
        "key_alternatives": credentials_mod.keychain_alternatives(),
        # The last thing anything learned about the box, from the worker's probe
        # or from the test button. Same sentences as /api/health, so the two
        # screens cannot say different things about one machine.
        "last_known": state.endpoint.to_dict(),
        "problem": problem,
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


# --- the inference endpoint -------------------------------------------------
#
# Two routes. There were four, and the extra two were a test button, a "fetch
# the models" button and a save button standing for one intention: point this at
# my computer. Three controls for one intention is three chances to do two of
# them and believe it is set up.
#
# So `connect` asks the box what it runs, checks it end to end, and writes the
# configuration **only if every check passed**. Saving an endpoint known not to
# work has no value: the result is a queue that never drains and a screen that
# says it is configured.
#
# The one thing lost with the save button is saving while the box is asleep. It
# is a real cost and a small one — nothing about the record needs the endpoint
# to be set, captures queue either way, and `config.toml` is a text file anyone
# can still edit by hand.


def _draft(
    base_url: str, model: str, header: str | None, scheme: str | None
) -> dict[str, Any]:
    return {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
        "header": (header if header is not None else DEFAULT_AUTH_HEADER).strip(),
        # `None` means "not sent, use the default"; `""` means "this server wants
        # the key on its own". They are different answers and the difference is
        # preserved all the way to the header on the wire.
        "scheme": None if scheme is None else scheme.strip(),
    }


def _settings_for_draft(state: RecordState, draft: dict[str, Any]) -> VlmSettings:
    """Draft plus whatever is already configured, as settings to connect with.

    ``ctx``, the image budget and the timeouts are not on this screen. They are
    carried over so a check runs against the same numbers a real read would,
    rather than against defaults the vault does not use.
    """
    try:
        current = session.settings_for(state.vault).vlm
    except HealthAgentError:
        current = None
    return endpoint_check.settings_for(draft, current)


def _write(state: RecordState, settings: VlmSettings) -> None:
    """The four keys, by the line rewrite that leaves the rest of the file alone.

    The model id goes in **verbatim**, as the box reported it: what a server
    calls itself has already differed from what a person typed in this project
    once, and identity is checked on every call, so an approximation stops the
    run rather than being quietly accepted. Fetching the name instead of asking
    for it is the reason that class of mistake cannot happen here any more.
    """
    path = state.vault.root / CONFIG_FILENAME
    with state.lock:
        try:
            config_mod.set_values(
                path,
                {
                    "models.vlm.base_url": settings.endpoint.base_url,
                    "models.vlm.model": settings.model,
                    "models.vlm.auth.header": settings.auth.header,
                    "models.vlm.auth.scheme": settings.auth.scheme,
                },
            )
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        state.reload_config()


def _refusal(state: RecordState, result) -> dict[str, Any]:
    """A failed connect, answered 200 with everything the screen needs."""
    body = result.to_dict()
    body["saved"] = False
    body["settings"] = _settings(state)
    return body


@router.post("/api/settings/endpoint/connect")
def connect_endpoint(
    base_url: str = Body(..., embed=True),
    model: str | None = Body(None, embed=True),
    header: str | None = Body(None, embed=True),
    scheme: str | None = Body(None, embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Reach the box, check it, and save it if it works. One press, one meaning.

    The address guard runs first and before any credential is read, so an
    address outside private space is refused having sent nothing anywhere —
    which is also why its refusal is the one message here allowed to speak in
    its own words: at that moment there is no far-end text in existence.

    Three outcomes reach the screen. ``connected`` wrote the configuration.
    ``choose-model`` means the box answered and offers more than one model, and
    is a question rather than a failure. ``failed`` carries one sentence saying
    what stopped it, the detail behind it, and every step for whoever opens
    them.
    """
    if not (base_url or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "type the address of the computer that reads your documents — the "
                "web address of the model server on it, ending in /v1."
            ),
        )

    draft = _draft(base_url, model or "", header, scheme)
    try:
        settings = _settings_for_draft(state, draft)
    except EndpointNotPrivate as exc:
        # Refused while the address was still being parsed — a credential in the
        # URL, a scheme that is not http. Onto the same surface as every other
        # failure rather than out through the generic error handler, which would
        # put it in a second place with a second shape.
        return _refusal(state, endpoint_check.address_failure(str(exc)))

    result = endpoint_check.connect(state.vault, settings)

    if result.ok:
        _write(state, replace(settings, model=result.model))

    # Whatever this found is what the rest of the app now believes about the
    # box: a person who has just watched it connect must not go back to a banner
    # still saying it is asleep.
    if result.report is not None:
        state.set_endpoint(
            endpoint_check.endpoint_state_from(result.report, state.snapshot().built_ts)
        )
    worker = getattr(state, "worker", None)
    if worker is not None and result.ok:
        worker.reconfigured()

    body = result.to_dict()
    body["saved"] = result.ok
    body["settings"] = _settings(state)
    return body


@router.post("/api/settings/endpoint/key")
def set_key(
    key: str = Body(..., embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Store the key in the OS keychain. **There is no route that reads it back.**

    The response says where the key *now resolves from*, which is not always the
    keychain: an exported environment variable wins, and on a machine with one
    set this route can succeed and change nothing that goes on the wire. Saying
    so is the difference between five seconds of confusion and an afternoon.

    Storing a key also un-parks the queue. A rotated key should cost one failed
    job, not a restart — MODELS.md — and the jobs that parked on the old one are
    exactly the work this key exists to let finish.
    """
    try:
        where = credentials_mod.store(key)
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=redaction.scrub(str(exc))) from None

    # Registered so that anything written from here on — a log line, an httpx
    # error carrying request headers — has it scrubbed out before it lands.
    redaction.register(key.strip())

    worker = getattr(state, "worker", None)
    if worker is not None:
        resumed = worker.resume()
    else:
        with state.lock:
            resumed = len(state.queue().resume())

    result = _settings(state)
    result["changed"] = "key"
    result["stored"] = where
    result["resumed"] = resumed
    return result
