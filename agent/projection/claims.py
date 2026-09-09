"""Reading a claim out of an event, or refusing to.

The event log is permissive on read by design: a line already on disk is
history, and phase 1 keeps anything it cannot understand rather than dropping it.
This module is where that permissiveness stops being useful. A claim decides
what a medication list says, so a payload that does not parse cleanly is
**excluded and reported**, never patched up into something usable.

``MODELS.md``: "Validate, then reject. Never repair. A malformed claim that gets
coerced into a valid one is how a dose becomes wrong silently."

Two things are deliberately not taken from the payload. The **consequence tier**
is looked up from the subject kind and predicate (see :mod:`.tiers`), because a
claim that could label itself low-consequence could route itself around review.
And no **timestamp is ever substituted for another**: ``captured_ts``,
``artifact_ts`` and ``occurred_at`` stay ``None`` when the payload leaves them
null, because ingest time is not capture time and the difference is invisible
once it has been written down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..events.envelope import Event, parse_ts_or_none
from . import dispense as dispense_mod
from .anomalies import Anomaly
from . import subjects, tiers, values
from .dates import FuzzyDate, parse_occurred_at
from .subjects import Subject
from .values import Value

PROPOSED = "proposed"
CORRECTED = "corrected"

_PREDICATE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")

#: A correction that does not say what evidence it rests on is the user's own
#: statement, which is what this tier means.
DEFAULT_CORRECTION_TIER = "patient-reported"


@dataclass(frozen=True)
class Claim:
    """One assertion that ``subject`` has ``value`` for ``predicate``."""

    event_id: str
    ts: str
    device: str
    kind: str  # PROPOSED | CORRECTED
    subject: Subject
    predicate: str
    value: Value
    evidence_tier: str
    consequence: str
    occurred_at: FuzzyDate | None = None
    artifact_ts: str | None = None
    captured_ts: str | None = None
    ingested_ts: str | None = None
    artifact: str | None = None
    confidence: float | None = None
    declared_consequence: str | None = None
    provenance: dict[str, Any] | None = None
    dispense: dispense_mod.Dispense | None = None
    target: str | None = None
    sort_key: tuple[Any, ...] = ()

    @property
    def is_correction(self) -> bool:
        return self.kind == CORRECTED

    @property
    def slot(self) -> tuple[str, str]:
        return (self.subject.id, self.predicate)

    @property
    def cite(self) -> str:
        """What this claim's footnote should point at.

        The artefact where there is one — a claim's authority is the page it was
        read off, not the event that recorded it. A user correction has no
        artefact, so it cites its own event.
        """
        return self.artifact or f"ev-{self.event_id}"


@dataclass(frozen=True)
class ClaimProblem:
    """A claim event that could not be read. Reported, never guessed at."""

    event_id: str
    ts: str
    reason: str

    def describe(self) -> str:
        return f"{self.event_id} ({self.ts}): {self.reason}"


def _canonical_or_none(payload: dict[str, Any], name: str) -> tuple[str | None, str | None]:
    """Read an optional timestamp field. Returns ``(value, problem)``.

    An absent field and an explicit ``null`` are the same thing and both mean
    "not established". A present-but-unparseable value is a problem rather than
    something to drop quietly, because the difference between "we never knew"
    and "we wrote something broken here" matters when reading the record back.
    """
    if name not in payload or payload[name] is None:
        return None, None
    raw = payload[name]
    if not isinstance(raw, str) or parse_ts_or_none(raw) is None:
        return None, f"{name} is present but is not a timestamp: {raw!r}"
    return raw, None


def _confidence(payload: dict[str, Any]) -> tuple[float | None, str | None]:
    raw = payload.get("confidence")
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, f"confidence must be a number between 0 and 1, got {raw!r}"
    if not 0.0 <= float(raw) <= 1.0:
        return None, f"confidence must be between 0 and 1, got {raw!r}"
    return float(raw), None


def _artifact_of(event: Event, payload: dict[str, Any]) -> str | None:
    for source in (event.provenance or {}, payload):
        candidate = source.get("artifact")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def parse(event: Event, target: Claim | None = None) -> Claim | ClaimProblem:
    """Read a ``claim.proposed`` or ``claim.corrected`` event.

    *target* is the claim a correction is amending, where it names one. A
    correction inherits its subject, predicate and evidence tier from that
    target when it does not restate them: it is a better reading of the same
    artefact, not a new source.
    """
    payload = event.payload
    kind = CORRECTED if event.type == "claim.corrected" else PROPOSED
    problem = lambda reason: ClaimProblem(event.id, event.ts, reason)  # noqa: E731

    if not isinstance(payload, dict) or not payload:
        return problem("claim payload is empty")

    subject_raw = payload.get("subject")
    if subject_raw is None and target is not None:
        subject = target.subject
    else:
        subject = subjects.parse(subject_raw)
        if subject is None:
            return problem(subjects.describe_rejection(subject_raw))

    predicate = payload.get("predicate")
    if predicate is None and target is not None:
        predicate = target.predicate
    if predicate is None and kind is CORRECTED and "status" in payload:
        predicate = "status"
    if not isinstance(predicate, str) or not _PREDICATE_RE.match(predicate):
        return problem(
            f"predicate {predicate!r} is unusable; expected a short lowercase name "
            f"such as 'dose' or 'status'"
        )

    raw_value = payload.get("value")
    if raw_value is None and kind is CORRECTED and isinstance(payload.get("status"), str):
        raw_value = payload["status"]
    value = values.parse(raw_value)
    if value is None:
        return problem(
            f"value {raw_value!r} could not be read; a claim states what a source said, "
            f"so an unreadable value is dropped rather than repaired"
        )

    evidence_tier = payload.get("evidence_tier")
    if evidence_tier is None:
        if kind is CORRECTED:
            evidence_tier = target.evidence_tier if target else DEFAULT_CORRECTION_TIER
        else:
            return problem(
                "evidence_tier is missing; a claim without a stated source tier cannot "
                "be ranked against one that has one"
            )
    if not tiers.is_evidence_tier(evidence_tier):
        return problem(
            f"evidence_tier {evidence_tier!r} is not one of {', '.join(tiers.EVIDENCE_TIERS)}"
        )

    confidence, bad_confidence = _confidence(payload)
    if bad_confidence:
        return problem(bad_confidence)

    stamps: dict[str, str | None] = {}
    for name in ("artifact_ts", "captured_ts", "ingested_ts"):
        stamp, bad = _canonical_or_none(payload, name)
        if bad:
            return problem(bad)
        stamps[name] = stamp

    occurred_at = parse_occurred_at(payload.get("occurred_at"))
    if payload.get("occurred_at") is not None and occurred_at is None:
        return problem(
            f"occurred_at {payload['occurred_at']!r} is present but unreadable; an "
            f"unknown date is written as null rather than approximated"
        )

    declared = payload.get("consequence")
    consequence = tiers.consequence_for(subject.kind, predicate)

    supply = dispense_mod.parse(
        payload.get("dispense"), fallback_frequency=value.fields.get("frequency")
    )

    target_id = payload.get("target")
    if target_id is not None and not isinstance(target_id, str):
        return problem(f"target must be an event id, got {target_id!r}")

    return Claim(
        event_id=event.id,
        ts=event.ts,
        device=event.device,
        kind=kind,
        subject=subject,
        predicate=predicate,
        value=value,
        evidence_tier=evidence_tier,
        consequence=consequence,
        occurred_at=occurred_at,
        artifact_ts=stamps["artifact_ts"],
        captured_ts=stamps["captured_ts"],
        ingested_ts=stamps["ingested_ts"],
        artifact=_artifact_of(event, payload),
        confidence=confidence,
        declared_consequence=declared if isinstance(declared, str) else None,
        provenance=event.provenance,
        dispense=supply,
        target=target_id,
        sort_key=event.sort_key,
    )


def declared_tier_anomaly(claim: Claim) -> Anomaly | None:
    """A payload that disagrees with the code's consequence lookup.

    Reported, never obeyed. The lookup in :mod:`.tiers` is what gates review;
    this exists so that a model or a hand-edit quietly claiming a lower tier is
    visible rather than merely ineffective.
    """
    if claim.declared_consequence is None:
        return None
    if claim.declared_consequence == claim.consequence:
        return None
    # Attached to the subject: this is a statement about one entity's claim, and
    # the page for that entity is where someone asking "is this gated correctly"
    # will look.
    return Anomaly(
        f"{claim.event_id}: payload says consequence {claim.declared_consequence!r} but "
        f"{claim.subject.kind}:{claim.predicate} is {claim.consequence!r}; the payload "
        f"value has no effect on review gating",
        subject_id=claim.subject.id,
        cite=claim.cite,
    )
