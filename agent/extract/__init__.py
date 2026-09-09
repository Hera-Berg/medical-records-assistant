"""Reading an artefact, and proposing what it says.

The pipeline is deterministic end to end. An artefact goes in; an
``extraction.completed`` event holding the model's raw answer and zero or more
``claim.proposed`` events come out. Nothing here writes to ``wiki/``, decides a
reconciliation, computes a number that reaches the record, or assigns a
consequence tier.

There is no agent loop and no planner. ``CLAUDE.md`` is explicit that ingest must
stay a deterministic pipeline, because a nondeterministic ingest path breaks
byte-identical rebuild, which every other guarantee in the project depends on.
"""

from __future__ import annotations

from . import (
    crossverify,
    dates,
    evaluate,
    images,
    jobs,
    probe,
    prompts,
    propose,
    runner,
    session,
    schema,
    text,
    validate,
)

__all__ = [
    "crossverify",
    "dates",
    "evaluate",
    "images",
    "jobs",
    "probe",
    "propose",
    "runner",
    "session",
    "prompts",
    "schema",
    "text",
    "validate",
]
