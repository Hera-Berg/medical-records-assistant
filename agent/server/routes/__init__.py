"""The HTTP routes, one module per group.

Split by what each answers rather than by verb: reading the record, taking
something into it, checking its state, serving one artefact's bytes, and
replaying the log. Each module's docstring carries the rules that route has to
hold, next to the code that holds them.
"""

from __future__ import annotations

from . import artifact, capture, files, health, rebuild, record, settings

__all__ = [
    "artifact",
    "capture",
    "files",
    "health",
    "rebuild",
    "record",
    "settings",
]
