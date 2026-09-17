"""Keeping the key out of everything that gets written down.

``httpx`` will happily include request headers in some error representations,
and this project writes an event log, a job file and rotating logs under
``.agent/logs/``. Any one of those is inside the vault, which syncs to Dropbox.
A key that reaches a log line has been uploaded exactly as surely as one written
into ``config.toml``.

So every secret this process resolves is registered here, and three things scrub
against that register: a ``logging.Filter`` on the ``agent`` logger, an explicit
:func:`scrub` applied to anything crossing out of :mod:`agent.llm`, and the
exception wrapper in the client, which re-raises with ``from None`` so the
original ``httpx`` exception — headers and all — never reaches a traceback.

The register holds values, never sources, and is process-local. It grows as keys
rotate, which is deliberate: a log line written before a rotation still needs
the old value scrubbed out of it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

REDACTED = "••••"

#: Every secret value this process has resolved. Values only — nothing here
#: records where a key came from or what it was for.
_SECRETS: set[str] = set()

#: Below this, a "secret" is more likely to be a placeholder than a key, and
#: scrubbing it would replace innocent substrings across every log line.
_MIN_LENGTH = 8


def register(value: str | None) -> None:
    """Remember a secret so it can be scrubbed from anything written later."""
    if isinstance(value, str) and len(value.strip()) >= _MIN_LENGTH:
        _SECRETS.add(value.strip())


def known() -> frozenset[str]:
    return frozenset(_SECRETS)


def forget_all() -> None:
    """Empty the register. For tests; never called in normal operation."""
    _SECRETS.clear()


def scrub(text: Any) -> Any:
    """Replace every registered secret in *text*.

    Also catches a percent-encoded or ``Bearer``-prefixed appearance, because a
    key that reaches a URL or a header repr does not always arrive verbatim.
    Non-strings are returned untouched — callers pass exception arguments
    through here and those are not always text.
    """
    if not isinstance(text, str) or not _SECRETS:
        return text
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, REDACTED)
        quoted = _percent_encode(secret)
        if quoted != secret and quoted in text:
            text = text.replace(quoted, REDACTED)
    return text


def _percent_encode(value: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415 - only needed on the slow path

    return quote(value, safe="")


class SecretFilter(logging.Filter):
    """Scrubs every registered secret out of a log record before it is written.

    Applied to the message, the interpolation arguments and the formatted
    exception text. Attached to the ``agent`` logger by :func:`install`, so a
    handler added later — a file under ``.agent/logs/``, say — is covered
    without having to remember.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _SECRETS:
            return True
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            record.args = _scrub_args(record.args)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exc.args = tuple(scrub(arg) for arg in exc.args)
        if isinstance(getattr(record, "exc_text", None), str):
            record.exc_text = scrub(record.exc_text)
        return True


def _scrub_args(args: Any) -> Any:
    if isinstance(args, dict):
        return {key: scrub(value) for key, value in args.items()}
    if isinstance(args, tuple):
        return tuple(scrub(value) for value in args)
    return scrub(args)


def scrub_structure(value: Any) -> Any:
    """Scrub every string inside a nested structure, keeping its shape.

    For the places where what gets written down is an object rather than a line:
    a validation error carrying the request body back to the caller, a report
    serialised to JSON. Recursion is depth-first over lists, tuples and dicts,
    and dictionary keys are scrubbed too — a secret can end up as a key when a
    payload is echoed back verbatim.
    """
    if isinstance(value, dict):
        return {scrub_structure(k): scrub_structure(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_structure(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_structure(item) for item in value)
    return scrub(value)


_FACTORY_LOCK = threading.Lock()
_FACTORY_INSTALLED = False


def install(logger_name: str = "agent") -> logging.Logger:
    """Scrub every log record in the process, and attach the filter to *logger_name*. Idempotent.

    The filter on a logger is not enough on its own, and for a long time it was
    all there was. Python consults a logger's filters only for records logged on
    that logger *itself*: a record from ``agent.runtime`` or ``agent.llm.client``
    propagates up past ``agent`` to the handlers without ever meeting the filter
    attached there. So every record is also scrubbed as it is created, through
    the log record factory, which no logger name and no handler added later can
    step around.
    """
    global _FACTORY_INSTALLED
    with _FACTORY_LOCK:
        if not _FACTORY_INSTALLED:
            previous = logging.getLogRecordFactory()
            scrubber = SecretFilter()

            def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
                record = previous(*args, **kwargs)
                scrubber.filter(record)
                return record

            logging.setLogRecordFactory(factory)
            _FACTORY_INSTALLED = True
    logger = logging.getLogger(logger_name)
    if not any(isinstance(existing, SecretFilter) for existing in logger.filters):
        logger.addFilter(SecretFilter())
    return logger


def assert_absent(text: str, where: str, secrets: Iterable[str] | None = None) -> None:
    """Raise if a secret survived into *text*. Used at the edges and in tests."""
    for secret in secrets if secrets is not None else _SECRETS:
        if secret and secret in text:
            raise AssertionError(
                f"a credential reached {where}. This is the failure the whole "
                f"credential design exists to prevent; treat the key as disclosed "
                f"and rotate it."
            )
