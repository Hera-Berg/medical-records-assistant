"""Exception hierarchy.

Every message here is read by a human who is trying to get their own health
record working, often on a machine that is misbehaving in a way they did not
cause. Messages say what is wrong and what to do about it.
"""


class HealthAgentError(Exception):
    """Base for every error this package raises deliberately."""


class VaultError(HealthAgentError):
    """The vault could not be located, opened, or written to."""


class ConfigError(HealthAgentError):
    """config.toml is missing, unparseable, or invalid."""


class EndpointNotConfigured(ConfigError):
    """config.toml names no inference endpoint at all.

    Distinct from a *bad* endpoint. Nothing is wrong here — a vault with no
    ``[models.vlm]`` table is simply not one extraction can run against, and a
    demo vault is deliberately one of those. Its own class so the caller can add
    what it knows about the vault before the message reaches a person.
    """


class SecretInConfigError(ConfigError):
    """A credential was found in config.toml.

    config.toml lives at the vault root and the vault syncs to Dropbox, Drive or
    Nextcloud. A secret written there has already been handed to a third party,
    so this is a hard load failure rather than a warning.
    """


class DeviceIdentityError(HealthAgentError):
    """The device identity is missing, malformed, or belongs to another machine."""


class DeviceIdentityMismatch(DeviceIdentityError):
    """The identity file records a different machine than the one running.

    Means the file was cloned or restored from a backup. Appending under a
    device id that another machine is also using is precisely the failure the
    one-shard-per-device rule exists to prevent, so appends are refused until
    the identity is re-issued. Reads are unaffected.
    """


class EventValidationError(HealthAgentError):
    """An event envelope failed validation on the way into the log."""


class AppendError(HealthAgentError):
    """A write to a shard did not land."""


class IngestError(HealthAgentError):
    """A file could not be taken into the raw store.

    Raised only where nothing has been recorded yet. Once bytes are on disk the
    ingest path stops raising and starts reporting: raw bytes are never removed
    to tidy up after a failure, because a file with no event is recoverable and
    an event with no file is a broken citation.
    """


class ProjectionError(HealthAgentError):
    """The wiki could not be derived from the log.

    Raised for a fault in this code — an uncited sentence, a value that cannot
    be serialised — never for bad data in the log. Bad data is reported and the
    claim is excluded; a projection that raised on it would let one malformed
    line take the whole record offline.
    """


class WikiWriteError(ProjectionError):
    """Writing the derived files would have destroyed something it did not write."""


class InferenceError(HealthAgentError):
    """The inference endpoint could not be used.

    The subclasses matter more than the base: collapsing "the box is asleep"
    into "processing failed" is how a rotated key looks like a sleeping Mac and
    goes uninvestigated for a week.
    """


class EndpointNotPrivate(InferenceError):
    """The configured endpoint resolves outside private address space.

    A startup failure, never a warning and never a toggle. Pasting a commercial
    API base URL into ``config.toml`` would end the project's privacy premise
    with no visible change in behaviour, so the guard refuses rather than asks.
    """


class CredentialError(InferenceError):
    """No usable credential for the inference endpoint.

    Includes a credential that resolved to an empty string: that is a
    misconfiguration to report, never a blank header to send.
    """


class EndpointUnreachable(InferenceError):
    """The box did not answer. Transient — retry with backoff, drain later."""


class ReadTimedOut(EndpointUnreachable):
    """The answer did not finish arriving within the read timeout.

    Still an :class:`EndpointUnreachable` to everything that only knows that
    word, because for a box across a network it may well mean the box went to
    sleep mid-answer. The reader on this computer is different — its process is
    alive and simply still writing — and :mod:`agent.extract.reader` reports
    that as an answer that ran on, not as a sleeping machine.
    """


class AuthRejected(InferenceError):
    """The box answered 401 or 403. **Terminal.**

    Not retried. Retrying a rotated key fifty times achieves nothing and may
    trip lockout on the far end. The queue parks and says specifically that the
    key was rejected, because this is the state that needs a person.
    """


class RateLimited(InferenceError):
    """The box answered 429. Back off, retry, and log it.

    A self-hosted box rate-limiting its owner usually means something else is
    hammering it, so this is worth a line in the log even when the retry works.
    """


class OutputTruncated(InferenceError):
    """The answer stopped because it hit the token ceiling, not because it ended.

    Its own class because it is a different problem with a different fix from
    malformed output. "The model ran out of room" is a cap to raise; "the model
    produced invalid output" is a server's grammar setting to check. They arrive
    looking identical — both present as JSON that will not parse — and the
    generic message sends someone to read server documentation about guided
    decoding when the cap was the whole story.

    A truncated answer is never parsed even when it happens to parse. It is a
    *partial* list, and a silently short list of medications is precisely the
    failure the eval harness's 100% recall rule exists to prevent.
    """


class ModelIdentityMismatch(InferenceError):
    """The server reported a different model than ``config.toml`` pins.

    Stops the run. "Which model produced this claim" has to be answerable a year
    later, and a config file describing a server's past state is worse than no
    record — so the config is never auto-updated to match.
    """


class ExtractionError(HealthAgentError):
    """Model output could not be turned into claims.

    Raised only where nothing has been recorded. Once an ``extraction.completed``
    event holds the raw output, a bad reading stops raising and starts being
    reported: the artefact surfaces as "could not read — review manually".
    """


class ReaderUnavailable(EndpointUnreachable):
    """The reader on this computer cannot answer right now, and ``reason`` says why.

    A subclass of :class:`EndpointUnreachable` on purpose: everything that
    already degrades gracefully when a box is asleep — the queue waiting, a
    question answered from the record alone — does the right thing here without
    being taught a second word for "not now". ``reason`` is a code from
    :mod:`agent.runtime.states`, so what reaches a screen is chosen from a fixed
    table rather than read out of this message.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class DownloadError(HealthAgentError):
    """The one-time download of the reader's files did not complete.

    ``reason`` is a code with a fixed sentence, for the same reason as
    :class:`ReaderUnavailable`: the detail can quote a far end's error text.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
