"""What the inference box is doing, in words chosen here rather than caught.

``MODELS.md`` is emphatic that **unreachable and unauthorised are different
states**: "Collapsing them into one 'offline' state means a rotated key looks
like a sleeping Mac and nobody investigates for a week." So they stay apart all
the way to the pixel, and so do the two that are neither — a vault with no
endpoint configured at all, and a box that answers text while silently
discarding every image.

Nothing in this module is derived from an exception's text. That is deliberate
and it is the whole design. ``MODELS.md`` requires that ``/api/health`` reports
``auth: ok | failed | missing`` "and nothing more", and that no route returns the
key or any prefix of it. The redaction filter exists and works, but it declines
to scrub anything shorter than eight characters — a short key would pass through
it — so this layer never *relies* on scrubbing. Every sentence the frontend can
display is written below, selected by a code, and contains no interpolation from
anything the far end said.

The detail an operator needs to fix a broken endpoint is not lost; it is what
``health-agent probe`` prints, on a terminal, where no browser is involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..runtime import states as reader_states

#: The box answers, accepts the key, runs the pinned model, and reads images.
WORKING = "working"
#: Asleep, or this machine is off the tailnet. Transient. Retry silently.
UNREACHABLE = "unreachable"
#: The key was rejected. **Terminal.** Needs a person, never a retry.
UNAUTHORISED = "unauthorised"
#: Configured wrongly: no credential, a public address, a model mismatch.
MISCONFIGURED = "misconfigured"
#: Answers text perfectly and ignores every image. Its own state because it
#: reads as "the model is bad at OCR" and can cost an evening.
BLIND = "vision-not-working"
#: This vault has no ``[models.vlm]`` table. Not a fault — a demo vault is
#: deliberately one of these, and capture works fine without an endpoint.
NOT_CONFIGURED = "not-configured"
#: Nothing has asked yet. The state at startup, before the first probe lands.
UNKNOWN = "unknown"

# The reader on this computer has states a remote box does not. Each is its own
# word for the same reason unreachable and unauthorised are: they send a person
# to different places, or tell them to go nowhere at all.

#: Its files are not here. Needs a person to start the download.
NOT_DOWNLOADED = "not-downloaded"
#: Idle and unloaded to free memory. Wakes by itself. Not a fault.
SLEEPING = "sleeping"
#: Loading, or restarting after a crash. Transient.
STARTING = "starting"
#: Stopped and staying stopped — repeated crashes, the wrong build, no vision.
#: Needs a person.
STOPPED = "stopped"

THIS_COMPUTER = "this-computer"
ANOTHER_COMPUTER = "another-computer"

AUTH_OK = "ok"
AUTH_FAILED = "failed"
AUTH_MISSING = "missing"

#: Every sentence this layer can put in front of a user, by code. Fixed text:
#: no server message, no exception argument and no configuration value is
#: interpolated into any of them.
MESSAGES: dict[str, str] = {
    "ok": "The box is up and reading.",
    "unknown": "Not checked yet.",
    "not-configured": (
        "This vault has no inference endpoint configured, so nothing reads "
        "artefacts. Capture still works and everything you add is kept."
    ),
    "no-credential": (
        "No credential is set for the inference box. Run `health-agent set-key` "
        "to store one in the OS keychain."
    ),
    "credential-unreadable": (
        "A credential was found but could not be used — most often a file whose "
        "permissions are wider than 0600. Run `health-agent probe` for the "
        "detail."
    ),
    "address-not-private": (
        "The configured endpoint does not resolve inside private address space. "
        "The record only talks to machines you control, so this is refused "
        "rather than warned about."
    ),
    "asleep": (
        "The box did not answer. Anything you capture keeps queuing and will be "
        "read when it is back."
    ),
    "key-rejected": (
        "Authentication was rejected by the inference box — the key may have "
        "rotated. Set a new one, then resume the queue."
    ),
    "rate-limited": (
        "The box is rate-limiting requests. Something else may be using it."
    ),
    "model-mismatch": (
        "The box reported a different model than config.toml pins. Nothing will "
        "run until they agree: which model produced a claim has to stay "
        "answerable."
    ),
    "vision-blind": (
        "The box answers text but did not read known text out of a test image, "
        "so it is most likely discarding images. Every document would be "
        "silently ignored. Run `health-agent probe` for what to check."
    ),
    "grammar-not-enforced": (
        "The box answers, but it is not holding replies to the shape the record "
        "asks for, so nothing it reads can be filed. Run `health-agent probe` "
        "for what to change on the server."
    ),
    "endpoint-unusable": (
        "The endpoint could not be used. Run `health-agent probe` for the "
        "detail, which is not shown here because it can quote what was sent."
    ),
    **reader_states.MESSAGES,
}


@dataclass(frozen=True)
class EndpointState:
    """The last known state of the box, and when it was last known."""

    state: str = UNKNOWN
    auth: str = AUTH_MISSING
    reason: str = "unknown"
    model: str | None = None
    checked_ts: str | None = None
    #: Which computer this state is about. The interface says nothing about
    #: keys or addresses for the reader on this computer, which has neither.
    where: str = ANOTHER_COMPUTER

    @property
    def is_terminal(self) -> bool:
        """Whether retrying would achieve anything.

        ``unauthorised`` and ``misconfigured`` need a person. ``unreachable``
        does not — that is the difference the whole module exists to keep.
        """
        return self.state in (UNAUTHORISED, MISCONFIGURED, BLIND, STOPPED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            # `ok | failed | missing`, and nothing more. Never the key, never a
            # prefix of it, never its length. See MODELS.md, "The browser never
            # sees the key".
            "auth": self.auth,
            "reason": self.reason,
            "message": MESSAGES.get(self.reason, MESSAGES["endpoint-unusable"]),
            "model": self.model,
            "checked_ts": self.checked_ts,
            "where": self.where,
        }


def not_configured() -> EndpointState:
    return EndpointState(
        state=NOT_CONFIGURED, auth=AUTH_MISSING, reason="not-configured"
    )


#: Probe report states (:mod:`agent.extract.probe`) to ours. The probe already
#: draws the same distinctions; this maps its vocabulary onto the one the
#: frontend reads, and picks the reason code from the state alone.
_FROM_PROBE = {
    "working": (WORKING, "ok"),
    "unreachable": (UNREACHABLE, "asleep"),
    "unauthorised": (UNAUTHORISED, "key-rejected"),
    "vision-not-working": (BLIND, "vision-blind"),
    # Misconfigured rather than a state of its own: unlike a blind box, which
    # looks like a working one until someone reads the wiki, this fails every
    # extraction loudly and immediately. It is a server setting to change.
    "grammar-not-enforced": (MISCONFIGURED, "grammar-not-enforced"),
}


def from_probe(report, checked_ts: str) -> EndpointState:
    """Turn a :class:`agent.extract.probe.ProbeReport` into a reportable state.

    A ``misconfigured`` probe can mean several different things and the probe
    says which in prose. Prose is exactly what may not cross this boundary, so
    the failing check's *name* selects a code from the table above instead.
    """
    state, reason = _FROM_PROBE.get(report.state, (MISCONFIGURED, None))
    if reason is None:
        reason = _misconfiguration_reason(report)
    return EndpointState(
        state=state,
        auth=report.auth,
        reason=reason,
        model=report.model_reported,
        checked_ts=checked_ts,
    )


def _misconfiguration_reason(report) -> str:
    """Which misconfiguration, from the name of the check that failed."""
    failed = next((check.name for check in report.checks if not check.ok), "")
    return {
        "endpoint": "address-not-private",
        "credential": "no-credential"
        if report.auth == AUTH_MISSING
        else "credential-unreadable",
        "model": "model-mismatch",
        "model identity": "model-mismatch",
        "grammar": "grammar-not-enforced",
    }.get(failed, "endpoint-unusable")


#: Exception class names to ``(state, auth, reason)``. Keyed on the class rather
#: than on the message, because the class is what this codebase controls and the
#: message is what the far end wrote.
_FROM_ERROR: dict[str, tuple[str, str, str]] = {
    "EndpointNotConfigured": (NOT_CONFIGURED, AUTH_MISSING, "not-configured"),
    "EndpointNotPrivate": (MISCONFIGURED, AUTH_MISSING, "address-not-private"),
    "CredentialError": (MISCONFIGURED, AUTH_MISSING, "no-credential"),
    "EndpointUnreachable": (UNREACHABLE, AUTH_OK, "asleep"),
    "AuthRejected": (UNAUTHORISED, AUTH_FAILED, "key-rejected"),
    "RateLimited": (WORKING, AUTH_OK, "rate-limited"),
    "ModelIdentityMismatch": (MISCONFIGURED, AUTH_OK, "model-mismatch"),
}


#: The reader's own states to ours. A reader that is ``ready`` is only
#: ``working`` once the probe has said so — see :func:`from_reader`.
_FROM_READER = {
    reader_states.NOT_DOWNLOADED: NOT_DOWNLOADED,
    reader_states.SLEEPING: SLEEPING,
    reader_states.STARTING: STARTING,
    reader_states.READY: WORKING,
    reader_states.STOPPED: STOPPED,
    reader_states.ELSEWHERE: STOPPED,
    reader_states.UNSUPPORTED: STOPPED,
}

#: Reader reasons that are a state of their own rather than a stop.
_READER_REASON_STATES = {
    "not-downloaded": NOT_DOWNLOADED,
    "downloading": NOT_DOWNLOADED,
    "sleeping": SLEEPING,
    "starting": STARTING,
    "restarting": STARTING,
    "ready": WORKING,
}


def from_reader(state: str, reason: str, checked_ts: str | None, model: str | None = None) -> EndpointState:
    """The state of the reader on this computer, in the vocabulary health reports.

    ``auth`` is ``ok`` throughout: the reader's key is made by this app for each
    launch, and "missing" would send a person looking for a key that does not
    exist.
    """
    return EndpointState(
        state=_FROM_READER.get(state, STOPPED),
        auth=AUTH_OK,
        reason=reason if reason in MESSAGES else "failed-to-start",
        model=model,
        checked_ts=checked_ts,
        where=THIS_COMPUTER,
    )


def from_reader_error(exc: BaseException, checked_ts: str) -> EndpointState:
    reason = getattr(exc, "reason", "failed-to-start")
    return EndpointState(
        state=_READER_REASON_STATES.get(reason, STOPPED),
        auth=AUTH_OK,
        reason=reason if reason in MESSAGES else "failed-to-start",
        checked_ts=checked_ts,
        where=THIS_COMPUTER,
    )


def from_error(exc: BaseException, checked_ts: str) -> EndpointState:
    """The state an inference failure implies, chosen by exception class.

    The exception's own text is discarded here rather than carried and scrubbed.
    """
    if type(exc).__name__ == "ReaderUnavailable":
        return from_reader_error(exc, checked_ts)
    for klass in type(exc).__mro__:
        found = _FROM_ERROR.get(klass.__name__)
        if found is not None:
            state, auth, reason = found
            return EndpointState(
                state=state, auth=auth, reason=reason, checked_ts=checked_ts
            )
    return EndpointState(
        state=MISCONFIGURED,
        auth=AUTH_MISSING,
        reason="endpoint-unusable",
        checked_ts=checked_ts,
    )
