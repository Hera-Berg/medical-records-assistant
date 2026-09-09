"""The shape the model is allowed to answer in.

This is the guarded edge of the system, and most of the guarding is done by
**what the schema does not contain**.

There is no ``consequence`` field. ``MODELS.md``: "The model never assigns a
consequence tier. If the model could label something low-consequence, a bad
extraction could route itself around review." A schema without the field is
stronger than a validator that strips it, because there is no version of the
prompt, the grammar or a future edit under which the model can express one.

There is no ``days_supply``, no ``expected_exhaustion`` and no ``total``. The
model never does arithmetic; it copies ``"30 tablets"``, ``"twice daily"`` and
``"1 repeat"``, and Python computes from the literals. Every quantity in the wiki
must be traceable to a span the model copied rather than a number it produced.

And ``occurred_at`` carries no computed date. A date the page states plainly is
copied as a span and normalised in code; a phrase like "around Easter" is copied
**verbatim** into ``occurred_span`` with ``occurred_at`` left null. The model
never resolves it — the computus is deterministic but the year is a guess — and
the projection raises a dateable review item so the user supplies the date in one
tap. Discarding the phrase would lose information the record exists to keep.

``strict`` with ``additionalProperties: false`` throughout, so guided decoding
has an exact grammar to constrain to.
"""

from __future__ import annotations

from typing import Any

from ..projection import tiers

#: What an artefact can be. `unreadable` is a first-class answer: a blurry
#: photograph of a thumb is a legitimate outcome, and "could not read — review
#: manually" is a better one than a plausible invented dose.
ARTIFACT_KINDS = (
    "prescription",
    "pathology-report",
    "imaging-report",
    "specialist-letter",
    "discharge-summary",
    "referral",
    "medication-list",
    "note",
    "other",
    "unreadable",
)

#: Predicates the extractor may propose. A closed list, because an invented
#: predicate becomes an invented row in the record. Unknown predicates still gate
#: high — `tiers.consequence_for` fails closed — but they would go unreviewed as
#: a *vocabulary* question, and the answer to that is a deliberate edit here.
PREDICATES = (
    "name",
    "dose",
    "frequency",
    "route",
    "status",
    "started",
    "indication",
    "substance",
    "reaction",
    "severity",
    "diagnosis",
    "onset",
    "symptom",
    "role",
    "practice",
    "contact",
    "note",
)

#: Exactly the four entity directories the storage layout names. There is no
#: fifth: `agent.projection.subjects` files a claim by its kind, and a kind with
#: no directory is a claim with nowhere to live. Low-consequence material — a
#: weight, a meal photo — belongs to the timeline, which is where the spec puts
#: it, and reaches the record as a note rather than an entity.
SUBJECT_KINDS = ("med", "allergy", "problem", "person")

_DATE_PRECISIONS = ("day", "month", "year")


def _string(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _nullable_string(description: str) -> dict[str, Any]:
    return {"type": ["string", "null"], "description": description}


_OCCURRED_AT = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "description": (
        "Only when the source states a calendar date plainly. Never computed, "
        "never inferred from context, and never filled in to satisfy this schema "
        "— leave it null and put the words in occurred_span instead."
    ),
    "required": ["value", "precision", "uncertainty_days"],
    "properties": {
        "value": _string("The date as written on the source, copied verbatim."),
        "precision": {
            "type": "string",
            "enum": list(_DATE_PRECISIONS),
            "description": (
                "How precisely the source states it. 'June 2026' is month, not a "
                "day you have chosen within June."
            ),
        },
        "uncertainty_days": {
            "type": "integer",
            "minimum": 0,
            "description": "How many days either side the source itself allows.",
        },
    },
}

_DISPENSE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "description": (
        "Literal spans off the script. Never a total, never a number of days, "
        "never an exhaustion date — those are computed from these spans."
    ),
    "required": ["quantity", "frequency", "repeats", "dose_units"],
    "properties": {
        "quantity": _nullable_string("As written: '30 tablets', '1 x 100mL'."),
        "frequency": _nullable_string("As written: 'twice daily', 'as needed'."),
        "repeats": _nullable_string("As written: 'no repeats', '2 repeats'."),
        "dose_units": _nullable_string("As written: 'two tablets' taken each time."),
    },
}

_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "subject_kind",
        "subject_name",
        "predicate",
        "value_literal",
        "evidence_tier",
        "occurred_at",
        "occurred_span",
        "dispense",
        "source_span",
        "confidence",
    ],
    "properties": {
        "subject_kind": {"type": "string", "enum": list(SUBJECT_KINDS)},
        "subject_name": _string(
            "The thing this is about, as the source names it: 'Perindopril', "
            "'Penicillin', 'Dr Nguyen'."
        ),
        "predicate": {"type": "string", "enum": list(PREDICATES)},
        "value_literal": _string(
            "The value exactly as the source writes it: '5mg daily', not '5.0 mg'. "
            "Copy it; do not tidy it, convert it, or complete it."
        ),
        "evidence_tier": {
            "type": "string",
            "enum": list(tiers.EVIDENCE_TIERS),
            "description": "What kind of source this is. Judge the document, not the claim.",
        },
        "occurred_at": _OCCURRED_AT,
        "occurred_span": _nullable_string(
            "The words the source uses about when this happened, copied verbatim, "
            "whenever they are not a plain calendar date: 'around Easter', 'last "
            "Christmas', 'the week before the wedding'. Copy the phrase; never turn "
            "it into a date."
        ),
        "dispense": _DISPENSE,
        "source_span": _string(
            "The text on the page this claim was read from, copied verbatim, so a "
            "person can find it."
        ),
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": (
                "How sure you are you read this correctly. It does not affect "
                "whether a person reviews the claim."
            ),
        },
    },
}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["artifact_kind", "readable", "document_date", "claims", "unreadable_reason"],
    "properties": {
        "artifact_kind": {"type": "string", "enum": list(ARTIFACT_KINDS)},
        "readable": {
            "type": "boolean",
            "description": (
                "False if you cannot read this well enough to be sure of what it "
                "says. That is a useful answer and is preferred to a guess."
            ),
        },
        "unreadable_reason": _nullable_string(
            "If readable is false, what stops you reading it."
        ),
        "document_date": _OCCURRED_AT,
        "claims": {
            "type": "array",
            "maxItems": 40,
            "items": _CLAIM,
            "description": (
                "Everything the source states. An empty list is correct for a "
                "document that states nothing about medications, allergies, "
                "problems or practitioners."
            ),
        },
    },
}

SCHEMA_NAME = "health_record_extraction"
