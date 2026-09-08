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
