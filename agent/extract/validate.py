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
    #: What the source called it — "Perindopril Arginine" — beside the
    #: normalised id. The projection files salt variants under the base drug,
    #: and without the label's own wording travelling with the claim there
    #: would be no rendered copy of what the page actually said.
    subject_literal: str
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
class Abstention:
    """Something the reader could see on the page and said it could not read.

    A first-class outcome, not a failure. It becomes a visible "could not be
    read" item in the review queue, so the person photographs it again or types
    it in — where a guessed dose would have become a wrong fact nobody sees.
    """

    family: str
    field: str
    reason: str
    #: The entity id, when the name was readable and resolves; ``None`` when even
    #: the name could not be read.
    subject: str | None = None
    subject_name: str | None = None
    source_span: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "field": self.field,
            "reason": self.reason,
            "subject": self.subject,
            "subject_name": self.subject_name,
            "source_span": self.source_span,
        }

    def describe(self) -> str:
        what = self.subject_name or f"one entry in {self.family}"
        return f"{what}: {self.field} could not be read ({self.reason})"


@dataclass(frozen=True)
class Extraction:
    """Everything one page's answer yielded, including what it did not."""

    artifact_kind: str = "other"
    readable: bool = True
    unreadable_reason: str | None = None
    #: Whether *this code* refused the answer, as against the model reporting
    #: that it could not read the page. Both leave ``readable`` false and no
    #: claims, and they are entirely different problems: one is a photograph of
    #: a thumb, the other is a server emitting thinking narration into
    #: ``content`` or a schema the grammar is not constraining. A run that
    #: reports them with the same word sends the user looking in the wrong
    #: place, which is what "0 claims proposed" used to do for both.
    refused: bool = False
    #: Whether the answer was cut off at a token ceiling rather than finished.
    #: Narrower than ``refused`` and reported separately, because the fix is a
    #: different one: "the model ran out of room" is a cap to raise, while a
    #: refused answer is a server setting or a prompt to look at. Sending
    #: someone to check grammar settings when a token cap was the whole story
    #: costs an evening.
    truncated: bool = False
    document_date: dict[str, Any] | None = None
    claims: tuple[ReadClaim, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: What the reader, or the checks after it, declined to assert. Each one is a
    #: visible item for a person, never a silent gap.
    abstentions: tuple[Abstention, ...] = ()

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


def merge(extractions: Sequence[Extraction]) -> Extraction:
    """Fold one artefact's pages into a single result.

    An artefact is readable if any page was: a discharge summary whose second
    page is a blank scan still has a first page worth reading.
    """
    if not extractions:
        return Extraction(readable=False, unreadable_reason="there were no pages to read")
    cut_off = [item for item in extractions if item.truncated]
    if cut_off:
        # One page cut off makes the whole artefact unread, even where other
        # pages answered in full. Filing the pages that fit would put a partial
        # list of results in the record wearing the same frontmatter as a
        # complete one, and nothing downstream could tell the difference. The
        # raw output of every page is still recorded; none of it becomes a claim.
        return Extraction(
            artifact_kind=extractions[0].artifact_kind,
            readable=False,
            refused=True,
            truncated=True,
            unreadable_reason=cut_off[0].unreadable_reason,
            rejections=tuple(r for item in extractions for r in item.rejections),
            notes=tuple(note for item in extractions for note in item.notes),
        )
    readable = [item for item in extractions if item.readable]
    if not readable:
        return Extraction(
            artifact_kind=extractions[0].artifact_kind,
            readable=False,
            # Any page whose answer was refused makes the artefact a refusal:
            # "one page could not be read" and "one page's answer was not
            # usable" want different things done about them, and the second is
            # the one that needs looking at.
            refused=any(item.refused for item in extractions),
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
        abstentions=tuple(a for item in extractions for a in item.abstentions),
    )
