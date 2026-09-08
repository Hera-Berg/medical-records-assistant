"""Canonical JSON serialisation.

One function produces every byte that reaches an event file. Phase 3's
byte-identical rebuild guarantee, and every hash computed over an event later,
rest on this being stable across machines, Python versions and runs.

Canonical form: keys sorted by code point, no insignificant whitespace, UTF-8
with non-ASCII written literally rather than escaped, and exactly one trailing
newline on a serialised line.

Nothing is coerced. A value JSON cannot represent, a non-string key, or a NaN
raises instead of being quietly rewritten into something valid, because a
silently rewritten value in this system is a silently wrong dose.
"""

from __future__ import annotations

import json
from typing import Any

_SCALARS = (str, int, float, bool, type(None))


def _check(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{path}: NaN and Infinity are not representable in JSON")
        return
    if isinstance(value, (str, int)):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"{path}: object keys must be strings, got {type(key).__name__} "
                    f"({key!r}); json would coerce this silently"
                )
            _check(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    raise TypeError(f"{path}: {type(value).__name__} is not JSON-representable")


def dumps(value: Any) -> str:
    """Serialise *value* to canonical JSON, with no trailing newline."""
    _check(value)
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def dump_line(value: Any) -> bytes:
    """Serialise *value* as one UTF-8 JSONL line, newline terminated."""
    return (dumps(value) + "\n").encode("utf-8")


def loads(text: str) -> Any:
    """Parse JSON text. Raises ``json.JSONDecodeError`` on malformed input."""
    return json.loads(text)
