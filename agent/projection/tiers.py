"""Evidence tiers and consequence tiers.

Two independent rankings that are easy to confuse. **Evidence tier** says how
much authority a source carries and is stated by the claim. **Consequence tier**
says how much damage a wrong claim does and is *never* stated by the claim — it
is looked up here, from the subject kind and predicate.

That lookup is the whole of invariant 5. ``MODELS.md``: "The model never assigns
a consequence tier. If the model could label something low-consequence, a bad
extraction could route itself around review." So a ``consequence`` value in an
event payload is read for provenance and compared, but the value returned by
:func:`consequence_for` is the one that gates. A payload that disagrees is an
anomaly to report, never an instruction to follow.

The lookup fails **closed**. An unrecognised predicate is high-consequence, so
extending the vocabulary is a deliberate edit to this table rather than something
a novel predicate can do to itself at runtime.
"""

from __future__ import annotations

#: Ordered strongest first. Rendered distinctly everywhere; a clinician must be
#: able to tell them apart in a second.
EVIDENCE_TIERS: tuple[str, ...] = (
    "prescriber-issued",
    "lab-issued",
    "device-recorded",
    "patient-reported",
    "inferred",
)

_EVIDENCE_RANK = {tier: len(EVIDENCE_TIERS) - i for i, tier in enumerate(EVIDENCE_TIERS)}

#: How each tier is labelled in prose and in the timeline's marker column.
EVIDENCE_LABELS: dict[str, str] = {
    "prescriber-issued": "prescriber-issued",
    "lab-issued": "lab-issued",
    "device-recorded": "device-recorded",
    "patient-reported": "patient-reported",
    "inferred": "inferred",
}

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

CONSEQUENCE_TIERS: tuple[str, ...] = (HIGH, MEDIUM, LOW)
_CONSEQUENCE_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}


def is_evidence_tier(value: object) -> bool:
    return isinstance(value, str) and value in _EVIDENCE_RANK


def evidence_rank(tier: str) -> int:
    """Higher is stronger. An unknown tier ranks below every known one."""
    return _EVIDENCE_RANK.get(tier, 0)


def consequence_rank(tier: str) -> int:
    return _CONSEQUENCE_RANK.get(tier, _CONSEQUENCE_RANK[HIGH])


#: Subject kind -> consequence when no more specific rule applies.
#:
#: ``med`` and ``allergy`` are high unconditionally, per ``MODELS.md``:
#: "``allergy.*`` and ``medication.*`` are high, always". ``problem`` defaults
#: high because adding or removing a diagnosis is high; its genuinely medium
#: predicates are enumerated below. ``person`` is medium — "new practitioner"
#: sits in the spec's medium list.
_KIND_DEFAULT: dict[str, str] = {
    "med": HIGH,
    "allergy": HIGH,
    "problem": HIGH,
    "person": MEDIUM,
}

#: ``(kind, predicate)`` -> consequence, for the cases the kind default gets
#: wrong. Only ever consulted for kinds that permit it: see :data:`_ALWAYS_HIGH`.
_PREDICATE_OVERRIDES: dict[tuple[str, str], str] = {
    ("problem", "onset"): MEDIUM,
    ("problem", "symptom"): MEDIUM,
    ("problem", "severity"): MEDIUM,
    ("problem", "note"): LOW,
    ("person", "name"): MEDIUM,
    ("person", "role"): MEDIUM,
    ("person", "practice"): MEDIUM,
    ("person", "contact"): LOW,
    ("person", "note"): LOW,
}

#: Kinds where no override may lower the tier, whatever the predicate. This is
#: belt and braces around the table above: an editor adding a plausible-looking
#: ``("med", "note"): LOW`` row would otherwise open a route around review.
_ALWAYS_HIGH = frozenset({"med", "allergy"})


def consequence_for(kind: str, predicate: str) -> str:
    """The consequence tier of a claim, from its subject kind and predicate.

    Deterministic, total, and independent of the claim's contents: confidence,
    evidence tier, model, and anything the payload says about its own tier have
    no influence here.
    """
    if kind in _ALWAYS_HIGH:
        return HIGH
    override = _PREDICATE_OVERRIDES.get((kind, predicate))
    if override is not None:
        return override
    return _KIND_DEFAULT.get(kind, HIGH)


def is_known_predicate(kind: str, predicate: str) -> bool:
    """Whether this predicate has a considered entry rather than a fallback.

    An unknown predicate still gates correctly — it falls to the kind default,
    which is high everywhere but ``person`` — but it is worth reporting so the
    table gets extended deliberately.
    """
    if (kind, predicate) in _PREDICATE_OVERRIDES:
        return True
    return predicate in _KNOWN_BY_KIND.get(kind, frozenset())


_KNOWN_BY_KIND: dict[str, frozenset[str]] = {
    "med": frozenset(
        {"name", "dose", "frequency", "route", "status", "started", "stopped", "indication"}
    ),
    "allergy": frozenset({"name", "substance", "reaction", "severity", "status"}),
    "problem": frozenset({"name", "status", "diagnosis"}),
    "person": frozenset({"name"}),
}
