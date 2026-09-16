"""Connecting to the inference endpoint from the settings screen.

This is a view over :mod:`agent.extract.probe`, which already does the work and
already draws the distinctions that matter. What this adds is **one headline and
a step list underneath it**: the probe answers with a state and a trail of
checks, and the screen needs to say in one sentence what went wrong while still
being able to show which of seven things were true. "It could not be reached"
and "it answered but would not take the key" send a person to two different
places, and a single red box saying "connection failed" sends them to neither —
but seven rows of prose in front of someone who just wanted to type an address
is the opposite mistake. So: a sentence, and the rest folded away.

**Connecting is one action, not two.** :func:`connect` asks the box what it
runs, runs the probe against it, and hands back a configuration to save only if
every check passed. There is no route that saves an endpoint known not to work,
because a saved endpoint that does not work is a queue that silently never
drains.

The vision step is the one this exists for. A server that accepts ``image_url``
content parts and silently discards them answers every text prompt perfectly
while ignoring every prescription photo, which reads as "the model is bad at
OCR" and has already cost this project an evening. It is therefore its own step
with its own sentence, and a connection that passes everything else and fails
that one is reported as a failure rather than as a qualified success.

**No sentence here is derived from what the far end said.** The same rule as
:mod:`agent.server.endpoint_state`, for the same reason: ``httpx`` puts request
headers into some error representations, and the redaction filter declines to
scrub anything shorter than eight characters, so a short key would pass through
it. Every sentence below is written here and selected by a code. Three local
facts are interpolated and they are named, so the list cannot quietly grow: the
URL the person typed into the form, the addresses this machine's own resolver
returned for it, and model identity strings, which come from the ``id`` field of
``/v1/models`` rather than from any error path. All three are scrubbed anyway.

There are exactly two exceptions and they share one property: **they happen
before the first byte leaves this machine.** The address guard refuses a host
that resolves outside private space, and the credential resolver refuses when
there is no usable key — both compose their message from the URL that was typed,
this machine's own resolver and this machine's own filesystem, and at the moment
either speaks, nothing has been sent to anything and there is no far-end text in
existence to leak. Their messages are worth passing through verbatim because
they name the address, the file and the mode to fix. Everything after a request
has gone out uses the fixed table below.

The detail an operator needs beyond this is what ``health-agent probe`` prints,
on a terminal, where no browser is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..errors import (
    AuthRejected,
    CredentialError,
    EndpointNotPrivate,
    EndpointUnreachable,
    InferenceError,
)
from ..extract import probe as probe_mod
from ..llm import redaction
from ..llm.client import Client
from ..llm.endpoint import parse as parse_endpoint
from ..llm.settings import (
    DEFAULT_AUTH_HEADER,
    DEFAULT_AUTH_SCHEME,
    AuthSettings,
    VlmSettings,
)
from . import endpoint_state

OK = "ok"
FAILED = "failed"
NOT_CHECKED = "not-checked"

#: The steps, in the order they are attempted, with the question each answers.
#: A step that was never reached still appears — a list that shortened itself
#: would make "it stopped here" indistinguishable from "this is not checked".
STEPS: tuple[tuple[str, str], ...] = (
    ("address", "The address is on your own network"),
    ("credential", "A password is stored for it"),
    ("reachable", "The computer answered"),
    ("authentication", "It accepted the password"),
    ("model", "It is running the model you chose"),
    ("grammar", "It answers in the shape the record needs"),
    ("vision", "It can read words out of a picture"),
)


@dataclass(frozen=True)
class Step:
    """One check, its outcome, and a sentence written here rather than caught."""

    name: str
    title: str
    state: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "state": self.state,
            "detail": redaction.scrub(self.detail),
        }


@dataclass(frozen=True)
class CheckReport:
    """What the test found: one state, and every step that was or was not run."""

    state: str
    steps: tuple[Step, ...]
    auth: str
    model_reported: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == endpoint_state.WORKING

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ok": self.ok,
            # `ok | failed | missing`, and nothing more. Never the key, never a
            # prefix of it, never its length.
            "auth": self.auth,
            "model_reported": redaction.scrub(self.model_reported),
            "message": endpoint_state.MESSAGES.get(
                _REASON_FOR.get(self.state, "endpoint-unusable"),
                endpoint_state.MESSAGES["endpoint-unusable"],
            ),
            "steps": [step.to_dict() for step in self.steps],
        }


def endpoint_state_from(report: CheckReport, checked_ts: str):
    """The check, as the state the rest of the app reports.

    Testing from the settings screen is a probe like any other, so the banner,
    the sidebar and ``/api/health`` all learn from it: a person who has just
    watched the test pass should not go back to a page still saying the box is
    asleep.
    """
    return endpoint_state.EndpointState(
        state=report.state,
        auth=report.auth,
        reason=_REASON_FOR.get(report.state, "endpoint-unusable"),
        model=report.model_reported,
        checked_ts=checked_ts,
    )


#: The one-line verdict for each state, reusing the sentences ``/api/health``
#: already shows so a person is not told two different things about one box.
_REASON_FOR = {
    endpoint_state.WORKING: "ok",
    endpoint_state.UNREACHABLE: "asleep",
    endpoint_state.UNAUTHORISED: "key-rejected",
    endpoint_state.BLIND: "vision-blind",
    endpoint_state.MISCONFIGURED: "endpoint-unusable",
    endpoint_state.NOT_CONFIGURED: "not-configured",
}

#: What each step says when it passes. ``{fact}`` is one of the three
#: allowlisted local facts named in the module docstring, never a server's text.
_PASSED = {
    "address": "{fact}",
    "credential": "Found {fact}. The password itself is never shown here.",
    "reachable": "It replied straight away.",
    "authentication": "The password was accepted.",
    "model": "{fact}",
    "grammar": "It was asked for a fixed shape and answered in it.",
    "vision": "It was sent a picture with words in it and read them back.",
}

#: The one sentence a failed step becomes. This is the whole of what a person
#: sees unless they open the detail, so each says *what* is wrong in the words
#: they would use, and never what to do about it — that is the detail's job.
_HEADLINE = {
    "address": "That address is not on your own network.",
    "credential": "No password is stored for that computer.",
    "reachable": "That computer did not answer.",
    "authentication": "That computer refused the password.",
    "model": "That computer is running a different model.",
    "grammar": "That computer does not answer in the shape the record needs.",
    "vision": "That computer cannot read words out of a picture.",
}

#: What each step says when it fails. Fixed: nothing the far end wrote appears.
_FAILED = {
    "address": (
        "This address is not on your own network. The record only ever talks to "
        "a computer you control — on your Tailscale network, your home network, "
        "or this machine — because anything else means handing your medical "
        "documents to a company to read. A name like "
        "macbook-pro.tailnet.ts.net is fine; it points at your own machine."
    ),
    # No "below" or "above" anywhere in this table: the form has been
    # rearranged once already and copy that points at a position is copy that
    # quietly starts lying. Name the field instead.
    "credential": (
        "No password is stored for this computer, so there is nothing to send. "
        "Put one in the Password field. It goes into this computer's keychain, "
        "never into your settings file."
    ),
    "reachable": (
        "No answer. The computer is probably asleep, or this machine is off the "
        "network it is on. Nothing is wrong with your record: anything you add "
        "keeps waiting and is read when it comes back."
    ),
    "authentication": (
        "It answered, but refused the password. That usually means the password "
        "on the computer has been changed since this one was stored. Put the new "
        "one in the Password field — nothing is retried until you do, so it "
        "cannot lock you out."
    ),
    "model": (
        "It is running something other than the model you chose. Pick the one it "
        "actually offers from the Model list. The record checks this on every "
        "read, because which model produced a claim has to stay answerable in a "
        "year's time."
    ),
    "grammar": (
        "It answers, but not in the fixed shape the record asks for, so nothing "
        "it reads could be filed. This is a setting on that computer — its "
        "guided-grammar or structured-output option needs turning on, and its "
        "thinking mode turning off. Run `health-agent probe` on the machine for "
        "the detail."
    ),
    "vision": (
        "It answered, but it did not read the words out of a test picture, so it "
        "is throwing pictures away. This is the one failure that looks like "
        "nothing being wrong: it would answer everything else perfectly and "
        "quietly ignore every photograph of a prescription. The usual cause is a "
        "text-only server — `mlx_lm.server` is one, and `mlx-vlm` is what you "
        "want — or, on llama.cpp, weights loaded without their separate mmproj "
        "file."
    ),
}

#: Probe check names to step names. The probe's vocabulary is the operator's.
_FROM_PROBE = {
    "endpoint": "address",
    "credential": "credential",
    "reachability": "reachable",
    "authentication": "authentication",
    "model": "model",
    "model identity": "model",
    "grammar": "grammar",
    "vision": "vision",
}


def open_client(vault, settings: VlmSettings) -> Client:
    """A client for *settings*, which may not be what ``config.toml`` says yet.

    The seam the tests replace. Deliberately not
    :func:`agent.extract.session.open_client`: this one tests what is *in the
    form*, before it is saved, which is the whole point of a test button.
    """
    return Client(settings, vault_root=vault.root)


def settings_for(draft: Mapping[str, Any], current: VlmSettings | None) -> VlmSettings:
    """Build settings from what the form holds, keeping everything it does not.

    ``ctx``, the image budget and the timeouts are not on this screen and are
    carried over from the saved configuration, so a test runs against the same
    numbers a real read would. A vault with nothing configured yet gets the
    defaults, which is what saving would write.
    """
    endpoint = parse_endpoint(draft.get("base_url"))
    model = str(draft.get("model") or "").strip()
    auth = AuthSettings(
        api_key_env=current.auth.api_key_env if current else None,
        header=str(draft.get("header") or DEFAULT_AUTH_HEADER).strip(),
        # Empty is a real answer: a server wanting `X-API-Key: <key>` with no
        # word in front of the value is asking for exactly this.
        scheme=str(
            draft.get("scheme") if draft.get("scheme") is not None else DEFAULT_AUTH_SCHEME
        ).strip(),
    )
    if current is None:
        return VlmSettings(endpoint=endpoint, model=model, auth=auth)
    return VlmSettings(
        endpoint=endpoint,
        model=model,
        ctx=current.ctx,
        max_pixels=current.max_pixels,
        connect_timeout_s=current.connect_timeout_s,
        read_timeout_s=current.read_timeout_s,
        temperature=current.temperature,
        presence_penalty=current.presence_penalty,
        thinking=current.thinking,
        auth=auth,
    )


def available_models(vault, settings: VlmSettings) -> list[str]:
    """Every model id the box offers, **verbatim**.

    Verbatim is the whole point of fetching this rather than typing it. The id
    a server reports has already differed from what a person typed in this
    project once — ``Jundot/Qwen3.8-Flash-Next-oQ4e-mtp`` against
    ``Qwen3.8-Flash-Next-oQ4e-mtp`` — and identity is checked on every call, so
    an approximation stops the run rather than being quietly accepted.
    """
    with open_client(vault, settings) as client:
        return client.list_models()


def run(vault, settings: VlmSettings, skip_vision: bool = False) -> CheckReport:
    """Probe *settings* and lay the result out as steps.

    Failures that stop the probe before it starts — an address that will not
    resolve, an unusable credential — are caught here and become a failed step
    rather than an exception, because this route's job is to report a broken
    endpoint rather than to have one.
    """
    try:
        with open_client(vault, settings) as client:
            report = probe_mod.run(client, skip_vision=skip_vision)
    except EndpointNotPrivate:
        return _stopped_at("address", endpoint_state.MISCONFIGURED, "missing")
    except CredentialError:
        return _stopped_at("credential", endpoint_state.MISCONFIGURED, "missing")
    except AuthRejected:
        return _stopped_at("authentication", endpoint_state.UNAUTHORISED, "failed")
    except EndpointUnreachable:
        return _stopped_at("reachable", endpoint_state.UNREACHABLE, "ok")
    except InferenceError:
        return _stopped_at("reachable", endpoint_state.MISCONFIGURED, "ok")

    state = endpoint_state.from_probe(report, checked_ts="").state
    return CheckReport(
        state=state,
        steps=_steps_from(report, skip_vision=skip_vision),
        auth=report.auth,
        model_reported=report.model_reported,
    )


def _facts(report) -> dict[str, str]:
    """The three local facts a passing step may quote. Nothing else is allowed.

    Each is lifted out of a check the probe wrote from something this machine
    knows — the addresses its own resolver returned, the *name* of the place the
    credential came from, the model id out of ``/v1/models`` — never out of an
    error body and never out of a message the far end composed.
    """
    found: dict[str, str] = {}
    for check in report.checks:
        if not check.ok:
            continue
        if check.name == "endpoint":
            # "https://box/v1 resolves to 100.94.1.2 — private", assembled by
            # the probe from this machine's own resolver.
            _, _, after = check.detail.partition(" resolves to ")
            addresses = after.split(" \u2014 ")[0].strip()
            found["address"] = (
                f"It points at {addresses}, which is on your own network."
                if addresses
                else "It is on your own network."
            )
        elif check.name == "credential":
            # The *source* — "keychain", "environment (HEALTH_VLM_TOKEN)" — and
            # never the value. `Credential.source` is a place, not a secret.
            _, _, source = check.detail.partition("found in ")
            found["credential"] = f"it in the {source.strip()}" if source else "it"
        elif check.name == "model":
            # The id as the box reports it, which is the whole reason the list
            # is fetched rather than typed.
            name = check.detail.removesuffix(" is available").strip()
            found["model"] = f"It is running {name}."
    return found


def _steps_from(report, skip_vision: bool) -> tuple[Step, ...]:
    """Every step, with the ones the probe never reached marked as such."""
    outcomes: dict[str, tuple[str, str]] = {}
    facts = _facts(report)
    for check in report.checks:
        name = _FROM_PROBE.get(check.name)
        if name is None:  # pragma: no cover - the probe's names are a closed set
            continue
        if check.ok:
            template = _PASSED[name]
            outcomes[name] = (OK, template.format(fact=facts.get(name, "")).strip())
        else:
            outcomes[name] = (FAILED, _FAILED[name])

    # A box that refused the key still *answered*, which is the distinction the
    # whole endpoint-state design turns on. The probe learns both from one
    # request, so reachability is inferred from having got an answer at all.
    if "authentication" in outcomes and "reachable" not in outcomes:
        outcomes["reachable"] = (OK, _PASSED["reachable"])

    steps: list[Step] = []
    stopped = False
    for name, title in STEPS:
        outcome = outcomes.get(name)
        if outcome is None:
            detail = (
                "Not checked — the test stopped above this."
                if stopped
                else _not_reached(name, skip_vision)
            )
            steps.append(Step(name, title, NOT_CHECKED, detail))
            continue
        state, detail = outcome
        steps.append(Step(name, title, state, detail))
        if state == FAILED:
            stopped = True
    return tuple(steps)


def _not_reached(name: str, skip_vision: bool) -> str:
    if name == "vision" and skip_vision:
        return "Skipped for this test."
    return "Not checked — the test stopped above this."


def _stopped_at(name: str, state: str, auth: str) -> CheckReport:
    """A report for a failure raised before the probe could record its own.

    Every other step is ``not-checked`` rather than ``ok``, including the ones
    above the failure. They may well have passed, but this path did not watch
    them do it, and a tick beside something nothing verified is the one thing a
    diagnostic screen must never print.
    """
    steps: list[Step] = []
    for step_name, title in STEPS:
        if step_name == name:
            steps.append(Step(step_name, title, FAILED, _FAILED[step_name]))
        else:
            steps.append(Step(step_name, title, NOT_CHECKED, "Not checked."))
    return CheckReport(state=state, steps=tuple(steps), auth=auth)


# --- connecting, which is one action ---------------------------------------


@dataclass(frozen=True)
class ConnectResult:
    """What one press of Connect found, and whether it is worth saving.

    ``outcome`` is the whole branch a screen needs:

    ``connected``    every check passed. The caller writes the configuration.
    ``choose-model`` the box answered and offers more than one model, and
                     nobody has said which. Not a failure — a question, and one
                     that cannot be asked before the box has been reached.
    ``failed``       something stopped it. ``status`` says what in a sentence,
                     ``detail`` says what to do about it, and ``report`` carries
                     every step for whoever wants to open it.
    """

    outcome: str
    status: str
    model: str = ""
    models: tuple[str, ...] = ()
    report: CheckReport | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "connected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "status": redaction.scrub(self.status),
            "model": redaction.scrub(self.model),
            "models": [redaction.scrub(name) for name in self.models],
            "detail": redaction.scrub(self.detail),
            "check": self.report.to_dict() if self.report is not None else None,
        }


#: Said when the box answers but lists nothing. Rare, and a dead end unless the
#: name can be typed, so the screen falls back to a text field on this.
NO_MODELS_LISTED = (
    "That computer answered but did not say which models it runs. Type the name "
    "in yourself, exactly as that server spells it."
)


def _failed(
    step: str, state: str, auth: str, detail: str = "", models: tuple[str, ...] = ()
) -> ConnectResult:
    return ConnectResult(
        outcome="failed",
        status=_HEADLINE[step],
        models=models,
        report=_stopped_at(step, state, auth),
        detail=detail or _FAILED[step],
    )


def address_failure(detail: str) -> ConnectResult:
    """A refusal raised while the address was still being parsed.

    ``https://user:key@box/v1`` is the one that matters: a credential typed into
    the address bar, which would land in ``config.toml`` and sync out of the
    house. It is refused by the parser, before settings exist to connect with,
    so it needs a way onto the same single failure surface as everything else.
    """
    return _failed("address", endpoint_state.MISCONFIGURED, "missing", detail)


def _reached_failure(exc: BaseException, models: tuple[str, ...] = ()) -> ConnectResult:
    """Classify a failure raised while asking the box what it runs.

    By exception class, never by message. The two that speak before anything is
    sent — the address guard and the credential resolver — hand their own text
    on as the detail, because at that moment there is no far-end text to leak
    and their sentences name the address, the file and the mode to fix.
    """
    if isinstance(exc, EndpointNotPrivate):
        return _failed("address", endpoint_state.MISCONFIGURED, "missing", str(exc))
    if isinstance(exc, CredentialError):
        return _failed("credential", endpoint_state.MISCONFIGURED, "missing", str(exc))
    if isinstance(exc, AuthRejected):
        return _failed("authentication", endpoint_state.UNAUTHORISED, "failed", models=models)
    if isinstance(exc, EndpointUnreachable):
        return _failed("reachable", endpoint_state.UNREACHABLE, "ok", models=models)
    return _failed("reachable", endpoint_state.MISCONFIGURED, "ok", models=models)


def connect(
    vault, settings: VlmSettings, skip_vision: bool = False
) -> ConnectResult:
    """Ask the box what it runs, then check it end to end.

    In that order, and the order is the point: the model list cannot be offered
    before the box has been reached, and the box cannot be checked against a
    model until one has been picked from what it actually has. A screen that
    asked for the model name first would be asking a person to know, verbatim,
    a string this function can simply go and read.
    """
    # The address, before a credential is read and before anything is sent.
    try:
        settings.endpoint.verify()
    except EndpointNotPrivate as exc:
        return _failed("address", endpoint_state.MISCONFIGURED, "missing", str(exc))

    try:
        available = tuple(available_models(vault, settings))
    except (AuthRejected, InferenceError) as exc:
        return _reached_failure(exc)

    chosen = settings.model
    if not chosen:
        if len(available) == 1:
            # One answer to a question with one answer. Asking would be
            # ceremony, and the id is recorded verbatim either way.
            chosen = available[0]
        elif available:
            return ConnectResult(
                outcome="choose-model",
                status=(
                    f"Connected. That computer runs "
                    f"{_spell(len(available))} models — choose which one should read "
                    f"your documents."
                ),
                models=available,
            )
        else:
            return ConnectResult(
                outcome="choose-model", status=NO_MODELS_LISTED, models=()
            )

    checked = replace(settings, model=chosen)
    report = run(vault, checked, skip_vision=skip_vision)
    if report.ok:
        return ConnectResult(
            outcome="connected",
            status=f"Connected — reading with {chosen}.",
            model=chosen,
            models=available,
            report=report,
        )

    failed = next((step for step in report.steps if step.state == FAILED), None)
    name = failed.name if failed is not None else "reachable"
    return ConnectResult(
        outcome="failed",
        status=_HEADLINE.get(name, _HEADLINE["reachable"]),
        model=chosen,
        models=available,
        report=report,
        detail=failed.detail if failed is not None else _FAILED["reachable"],
    )


def _spell(number: int) -> str:
    words = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    return words[number] if number < len(words) else str(number)
