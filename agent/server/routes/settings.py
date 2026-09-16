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

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ... import config as config_mod
from ... import vault as vault_mod
from ...config import CONFIG_FILENAME, SyncProfile
from ...errors import (
    AuthRejected,
    ConfigError,
    CredentialError,
    EndpointNotConfigured,
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


#: Said above the endpoint fields. The reading to rule out here is that this is
#: a service being signed up to: it is a machine the person already owns, and
#: the app will not talk to anything else.
ENDPOINT_EXPLANATION = (
    "Your documents are read by a model running on a computer you own — a "
    "desktop at home, reached over your own private network. This says where "
    "that computer is. The record will not talk to anything outside your own "
    "network: not a company's service, not a website, nothing you would have to "
    "trust. If that computer is off or asleep, nothing breaks — what you add "
    "waits in your folder and is read when it comes back."
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
# Four routes, and the shape of each follows from one decision: **the test runs
# against the form, not against the file.** A test button that could only check
# what was already saved would make the person save a bad endpoint in order to
# find out it was bad — and saving is the step that rewrites `config.toml`. So
# the draft crosses the wire on every call, and only `POST /api/settings/endpoint`
# writes anything.


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
    """Draft plus whatever is already configured, as settings to test with.

    ``ctx``, the image budget and the timeouts are not on this screen. They are
    carried over so a test runs against the same numbers a real read would,
    rather than against defaults the vault does not use.
    """
    try:
        current = session.settings_for(state.vault).vlm
    except HealthAgentError:
        current = None
    return endpoint_check.settings_for(draft, current)


@router.post("/api/settings/endpoint")
def set_endpoint(
    base_url: str = Body(..., embed=True),
    model: str = Body(..., embed=True),
    header: str | None = Body(None, embed=True),
    scheme: str | None = Body(None, embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Write the endpoint into ``config.toml``. Four keys, one line each.

    **The address guard runs here**, before anything is written, and a public
    host is refused with the whole explanation rather than with a validation
    error. ``MODELS.md`` is explicit that reaching a commercial API is "a
    startup failure, not a config option"; refusing it at the moment it is typed
    is the same rule applied where it can still be read as a reason.

    The model id is stored **verbatim**. What the box reports has already
    differed from what a person typed in this project once, and identity is
    checked on every call, so an approximation stops the run rather than being
    quietly accepted.
    """
    draft = _draft(base_url, model, header, scheme)
    if not draft["model"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "choose a model. Use “Fetch the list” to get the names from "
                "the computer itself and pick one — the name has to match "
                "exactly, and the record checks it on every read, so a near-miss "
                "stops the run rather than being ignored."
            ),
        )

    # Parses the URL and resolves the host: userinfo, a non-http scheme and a
    # public address are all refused here, each with its own sentence.
    settings = _settings_for_draft(state, draft)
    settings.endpoint.verify()

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

    # What was last known about the old endpoint says nothing about this one.
    # Left standing, a green "working" from ten minutes ago would sit under a
    # freshly typed address that has never been contacted.
    state.set_endpoint(
        endpoint_state.EndpointState(
            state=endpoint_state.UNKNOWN,
            auth=endpoint_state.AUTH_MISSING,
            reason="unknown",
        )
    )
    worker = getattr(state, "worker", None)
    if worker is not None:
        worker.reconfigured()

    result = _settings(state)
    result["changed"] = "endpoint"
    return result


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


@router.post("/api/settings/endpoint/models")
def list_endpoint_models(
    base_url: str = Body(..., embed=True),
    header: str | None = Body(None, embed=True),
    scheme: str | None = Body(None, embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Ask the box what it is running, so the model can be picked and not typed.

    Every id comes back **verbatim**, which is the point of asking rather than
    typing: ``Jundot/Qwen3.8-Flash-Next-oQ4e-mtp`` is not
    ``Qwen3.8-Flash-Next-oQ4e-mtp``, that exact difference has already cost time
    on this project, and the id is checked against the server's answer on every
    call.

    A box that is asleep is not an error here. It is the ordinary condition, and
    it comes back as an empty list with the sentence that says so, so the field
    can fall back to being typed by hand.
    """
    draft = _draft(base_url, "", header, scheme)
    settings = _settings_for_draft(state, draft)
    settings.endpoint.verify()

    try:
        models = endpoint_check.available_models(state.vault, settings)
    except (AuthRejected, HealthAgentError) as exc:
        # Reported by *class*, never by the far end's text. Same rule as
        # /api/health: no message from the box crosses into the browser.
        reported = endpoint_state.from_error(exc, state.snapshot().built_ts)
        state.set_endpoint(reported)
        return {
            "models": [],
            "reached": False,
            "state": reported.state,
            "message": reported.to_dict()["message"],
        }

    return {
        "models": [redaction.scrub(name) for name in models],
        "reached": True,
        "state": endpoint_state.WORKING,
        "message": (
            f"The computer offers {len(models)} model(s)."
            if models
            else "The computer answered, but does not list any models. Type the "
            "name in by hand, exactly as that server spells it."
        ),
    }


@router.post("/api/settings/endpoint/test")
def test_endpoint(
    base_url: str = Body(..., embed=True),
    model: str = Body(..., embed=True),
    header: str | None = Body(None, embed=True),
    scheme: str | None = Body(None, embed=True),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Run the startup probe against the form, and report every step separately.

    The five things a person needs to be able to tell apart are a private
    address, a machine that answered, a key that was accepted, the right model,
    and **vision actually working** — a box that silently drops image content
    parts answers everything else perfectly and ignores every prescription
    photo, which reads as the model being bad at OCR and has already cost this
    project an evening.

    The result also updates what the rest of the app believes about the box, so
    a person who has just watched the test pass does not go back to a banner
    still saying it is asleep.
    """
    draft = _draft(base_url, model, header, scheme)
    settings = _settings_for_draft(state, draft)

    report = endpoint_check.run(state.vault, settings)
    state.set_endpoint(
        endpoint_check.endpoint_state_from(report, state.snapshot().built_ts)
    )

    result = report.to_dict()
    result["settings"] = _settings(state)
    return result
