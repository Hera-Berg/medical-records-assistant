"""Reading model output, or refusing to.

``MODELS.md``: "**Validate, then reject. Never repair.** A malformed claim that
gets coerced into a valid one is how a dose becomes wrong silently. Log the raw
output, emit no claim, surface the artefact as 'could not read — review
manually'."

Nothing here fixes anything up. There is no default that fills a missing value,
no coercion of a string into a number, and no regex that fishes JSON out of prose.

Rejection is at two levels and the distinction matters. A **structural** failure
— not JSON, wrong top-level shape, a claim that is not an object — rejects the
whole extraction, because at that point nothing about the answer can be trusted.
A **single bad claim** is dropped with its reason recorded and the rest still
land: one unreadable line should not discard a correctly read allergy from the
same page.

The validator is written out rather than pulled in, over exactly the subset of
JSON Schema :mod:`agent.extract.schema` uses. That keeps the project's
dependency list honest and, more usefully, lets every rejection say what a person
should do about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..projection import subjects, tiers
from . import dates as dates_mod
from .schema import EXTRACTION_SCHEMA

#: The strongest tier an artefact of each kind may support, whatever the model
#: says. A voice note cannot be prescriber-issued however confidently it is
#: labelled: the tier describes where a statement came from, and that is a fact
#: about the artefact, which is known before the model runs.
_TIER_CEILING_BY_MIME = (
    ("audio/", "patient-reported"),
    ("video/", "patient-reported"),
)


@dataclass(frozen=True)
class Rejection:
    """One thing that could not be read, and what it was."""

    reason: str
    index: int | None = None

    def describe(self) -> str:
        where = f"claim {self.index}" if self.index is not None else "the extraction"
        return f"{where}: {self.reason}"


@dataclass(frozen=True)
class ReadClaim:
    """One claim the model made, validated and normalised. Not yet an event."""

    subject: str
    predicate: str
    value_literal: str
    evidence_tier: str
    confidence: float
    source_span: str
    occurred_at: dict[str, Any] | None = None
    occurred_span: str | None = None
    dispense: dict[str, Any] | None = None
    #: Coercions and refusals that did not invalidate the claim but that a reader
    #: should see — an unreadable date, a tier capped by the artefact type.
    notes: tuple[str, ...] = ()

    @property
    def consequence(self) -> str:
        """Looked up, never taken from the model. It has no field for it."""
        kind = self.subject.split(":", 1)[0]
        return tiers.consequence_for(kind, self.predicate)


@dataclass(frozen=True)
class Extraction:
    """Everything one page's answer yielded, including what it did not."""

    artifact_kind: str = "other"
    readable: bool = True
    unreadable_reason: str | None = None
    document_date: dict[str, Any] | None = None
    claims: tuple[ReadClaim, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_usable(self) -> bool:
        return self.readable and not any(r.index is None for r in self.rejections)

    def describe_rejections(self) -> list[str]:
        return [rejection.describe() for rejection in self.rejections]


# --- the schema walker -----------------------------------------------------


def _type_matches(value: Any, expected: Any) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        if name == "null" and value is None:
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "object" and isinstance(value, dict):
            return True
    return False


def check(value: Any, schema: Mapping[str, Any], path: str = "") -> list[str]:
    """Every way *value* fails *schema*. Empty means it is valid.

    All the problems rather than the first, because a person reading a rejection
    should learn everything wrong with the answer in one go.
    """
    where = path or "the answer"
    problems: list[str] = []

    if "type" in schema and not _type_matches(value, schema["type"]):
        names = schema["type"]
        expected = " or ".join(names) if isinstance(names, list) else names
        return [f"{where} must be {expected}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(option) for option in schema["enum"])
        return [f"{where} must be one of {allowed}, got {value!r}"]

    if isinstance(value, dict):
        for name in schema.get("required", ()):
            if name not in value:
                problems.append(f"{where} is missing {name!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    problems.append(f"{where} has an unexpected field {name!r}")
        for name, sub in properties.items():
            if name in value:
                problems.extend(check(value[name], sub, f"{where}.{name}" if path else name))

    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(
                f"{where} has {len(value)} items, more than the {schema['maxItems']} allowed"
            )
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                problems.extend(check(item, items, f"{where}[{index}]"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{where} must be at least {schema['minimum']}, got {value}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{where} must be at most {schema['maximum']}, got {value}")

    return problems


# --- reading one answer ----------------------------------------------------


def _tier_ceiling(mime: str) -> str | None:
    for prefix, ceiling in _TIER_CEILING_BY_MIME:
        if mime.startswith(prefix):
            return ceiling
    return None


def _cap_tier(tier: str, mime: str) -> tuple[str, str | None]:
    ceiling = _tier_ceiling(mime or "")
    if ceiling is None or tiers.evidence_rank(tier) <= tiers.evidence_rank(ceiling):
        return tier, None
    return ceiling, (
        f"the model called this {tier}, but the artefact is {mime}; a recording is "
        f"what the patient said, so the tier is held at {ceiling}"
    )


def _dispense(payload: Any) -> dict[str, Any] | None:
    """Keep only the literal spans. A block of all-nulls is no block at all."""
    if not isinstance(payload, dict):
        return None
    kept = {
        name: value
        for name, value in payload.items()
        if isinstance(value, str) and value.strip()
    }
    return kept or None


def _read_claim(
    raw: Mapping[str, Any], index: int, mime: str, locale: str
) -> tuple[ReadClaim | None, Rejection | None]:
    kind = raw["subject_kind"]
    name = raw["subject_name"]
    subject_id = subjects.make_id(kind, name)
    subject = subjects.parse(subject_id) if subject_id else None
    if subject is None:
        return None, Rejection(
            f"subject {kind}:{name!r} does not resolve to a usable entity id, so the "
            f"claim has nothing to attach to",
            index,
        )

    notes: list[str] = []
    tier, capped = _cap_tier(raw["evidence_tier"], mime)
    if capped:
        notes.append(capped)

    occurred, problem = dates_mod.normalise(raw.get("occurred_at"), locale)
    if problem:
        notes.append(problem)

    span = raw.get("occurred_span")
    occurred_span = span.strip() if isinstance(span, str) and span.strip() else None
    if occurred is None and problem and occurred_span is None:
        # The date could not be read and the model offered no phrase. Keep the
        # written form as the span so the record still holds what the page said
        # — the projection turns it into a question rather than losing it.
        written = (raw.get("occurred_at") or {}).get("value")
        if isinstance(written, str) and written.strip():
            occurred_span = written.strip()

    if not tiers.is_known_predicate(subject.kind, raw["predicate"]):
        notes.append(
            f"{subject.kind}:{raw['predicate']} is not in the consequence table, so it "
            f"is gated as high-consequence until the table says otherwise"
        )

    return (
        ReadClaim(
            subject=subject.id,
            predicate=raw["predicate"],
            value_literal=raw["value_literal"].strip(),
            evidence_tier=tier,
            confidence=float(raw["confidence"]),
            source_span=raw["source_span"].strip(),
            occurred_at=occurred,
            occurred_span=occurred_span,
            dispense=_dispense(raw.get("dispense")),
            notes=tuple(notes),
        ),
        None,
    )


def read(payload: Any, mime: str = "", locale: str = "en") -> Extraction:
    """Validate one page's answer and normalise what survives.

    Never raises on bad model output: the caller has already recorded the raw
    answer, and a page nobody can read is a reportable outcome rather than an
    error. What it returns is what may become claims, plus everything that may
    not and why.
    """
    structural = check(payload, EXTRACTION_SCHEMA)
    if structural:
        return Extraction(
            readable=False,
            unreadable_reason=(
                "the model's answer did not match the extraction schema, so no claim "
                "is made from it"
            ),
            rejections=tuple(Rejection(problem) for problem in structural),
        )

    document_date, date_problem = dates_mod.normalise(payload.get("document_date"), locale)
    notes = [date_problem] if date_problem else []

    if not payload["readable"]:
        return Extraction(
            artifact_kind=payload["artifact_kind"],
            readable=False,
            unreadable_reason=payload.get("unreadable_reason")
            or "the model could not read this page and did not say why",
            document_date=document_date,
            notes=tuple(notes),
        )

    claims: list[ReadClaim] = []
    rejections: list[Rejection] = []
    for index, raw in enumerate(payload["claims"]):
        claim, rejection = _read_claim(raw, index, mime, locale)
        if rejection is not None:
            rejections.append(rejection)
        elif claim is not None:
            claims.append(claim)

    return Extraction(
        artifact_kind=payload["artifact_kind"],
        readable=True,
        document_date=document_date,
        claims=tuple(claims),
        rejections=tuple(rejections),
        notes=tuple(notes),
    )


def merge(extractions: Sequence[Extraction]) -> Extraction:
    """Fold one artefact's pages into a single result.

    An artefact is readable if any page was: a discharge summary whose second
    page is a blank scan still has a first page worth reading.
    """
    if not extractions:
        return Extraction(readable=False, unreadable_reason="there were no pages to read")
    readable = [item for item in extractions if item.readable]
    if not readable:
        return Extraction(
            artifact_kind=extractions[0].artifact_kind,
            readable=False,
            unreadable_reason=extractions[0].unreadable_reason,
            rejections=tuple(r for item in extractions for r in item.rejections),
            notes=tuple(note for item in extractions for note in item.notes),
        )
    document_date = next(
        (item.document_date for item in readable if item.document_date is not None), None
    )
    return Extraction(
        artifact_kind=readable[0].artifact_kind,
        readable=True,
        document_date=document_date,
        claims=tuple(claim for item in readable for claim in item.claims),
        rejections=tuple(r for item in extractions for r in item.rejections),
        notes=tuple(note for item in extractions for note in item.notes),
    )
