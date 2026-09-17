"""The shape the model is allowed to answer in.

This is the guarded edge of the system, and most of the guarding is done by
**what the schema does not contain**.

**A page is read in groups — medications, then allergies, then problems and
people — and every group has an** ``unclear`` **list.** Leaving something out
because the page is hard to read is a correct answer and the schema gives it a
place, because a small model asked to fill a schema will fill it. An entry in
``unclear`` becomes a visible "could not be read" in the review queue; a guessed
dose becomes a wrong fact in a medical record.

**The name is only the name.** A medicine's strength, frequency and name are
separate fields, so a strength cannot leak into the identifier
(``med:atorvastatin-20``), and a dose is only ever proposed when both strength
and frequency were read — see :mod:`agent.extract.families`.

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

#: The groups a page is read in, in order, as turns of one conversation about the
#: same image. Measured on the bundled reader: the first turn pays the image
#: prefill (about 80 seconds on a laptop CPU) and each later turn reuses it from
#: the server's prompt cache (about 3 seconds). Asked as separate prompts instead,
#: every group paid the whole prefill again, which is why they are turns.
MEDICATIONS = "medications"
ALLERGIES = "allergies"
PROBLEMS = "problems"
FAMILIES = (MEDICATIONS, ALLERGIES, PROBLEMS)

#: What an ``unclear`` entry may say could not be read, per group.
UNCLEAR_FIELDS = {
    MEDICATIONS: ("name", "strength", "frequency", "stopped", "whole entry"),
    ALLERGIES: ("substance", "reaction", "whole entry"),
    PROBLEMS: ("name", "role", "whole entry"),
}


def _tier() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(tiers.EVIDENCE_TIERS),
        "description": "What kind of source this is. Judge the document, not the entry.",
    }


def _confidence() -> dict[str, Any]:
    return {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "description": (
            "How sure you are you read this correctly. It does not affect whether "
            "a person reviews it."
        ),
    }


_SOURCE_SPAN = _string(
    "The text on the page this was read from, copied verbatim, so a person can find it."
)
_OCCURRED_SPAN = _nullable_string(
    "The words the source uses about when this happened, copied verbatim, whenever "
    "they are not a plain calendar date: 'around Easter', 'last Christmas'. Copy the "
    "phrase; never turn it into a date."
)


def _unclear(family: str) -> dict[str, Any]:
    """Something on the page the model could not read. A first-class answer."""
    return {
        "type": "array",
        "maxItems": 20,
        "description": (
            "Everything in this group you can see on the page but cannot read with "
            "certainty. Putting an entry here is the correct answer whenever you are "
            "unsure — it sends the page to the person to check, which is exactly what "
            "should happen. A guess is never better than an entry here."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name_if_readable", "field", "reason", "source_span"],
            "properties": {
                "name_if_readable": _nullable_string(
                    "The name of the thing, if that part is clear. Null if even the "
                    "name cannot be read."
                ),
                "field": {"type": "string", "enum": list(UNCLEAR_FIELDS[family])},
                "reason": _string("What stops you reading it: 'handwriting', 'blurred'."),
                "source_span": _nullable_string("The words you can make out, if any."),
            },
        },
    }


_MEDICATION = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "name", "strength", "frequency", "stopped", "dispense", "evidence_tier",
        "occurred_at", "occurred_span", "source_span", "confidence",
    ],
    "properties": {
        "name": _string(
            "The medicine's name and nothing else, as written: 'Perindopril Arginine', "
            "'Atorvastatin'. Never a strength, a form or a count — not "
            "'Atorvastatin 20mg tablets', just 'Atorvastatin'."
        ),
        "strength": _nullable_string(
            "How much is in one dose, copied as written: '5mg', '500 mg'. Null if the "
            "page does not say, or you cannot read it — then add it to unclear."
        ),
        "frequency": _nullable_string(
            "How often it is taken, copied as written: 'daily', 'Take ONE tablet at "
            "night', 'twice daily with food'. Null if the page does not say, or you "
            "cannot read it — then add it to unclear."
        ),
        "stopped": _nullable_string(
            "Only if the source says this medicine was stopped: copy those words. "
            "Otherwise null."
        ),
        "dispense": _DISPENSE,
        "evidence_tier": _tier(),
        "occurred_at": _OCCURRED_AT,
        "occurred_span": _OCCURRED_SPAN,
        "source_span": _SOURCE_SPAN,
        "confidence": _confidence(),
    },
}

_ALLERGY = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "substance", "reaction", "evidence_tier", "occurred_at", "occurred_span",
        "source_span", "confidence",
    ],
    "properties": {
        "substance": _string(
            "What the person reacts to, and nothing else, as written: 'Penicillin'."
        ),
        "reaction": _nullable_string(
            "What happens, copied as written: 'rash', 'anaphylaxis'. Null if the page "
            "does not say."
        ),
        "evidence_tier": _tier(),
        "occurred_at": _OCCURRED_AT,
        "occurred_span": _OCCURRED_SPAN,
        "source_span": _SOURCE_SPAN,
        "confidence": _confidence(),
    },
}

_PROBLEM = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "name", "evidence_tier", "occurred_at", "occurred_span", "source_span",
        "confidence",
    ],
    "properties": {
        "name": _string(
            "A condition the source itself names as the person's, as written: "
            "'hypertension'. Never one you work out from a result or a medicine."
        ),
        "evidence_tier": _tier(),
        "occurred_at": _OCCURRED_AT,
        "occurred_span": _OCCURRED_SPAN,
        "source_span": _SOURCE_SPAN,
        "confidence": _confidence(),
    },
}

_PERSON = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "role", "evidence_tier", "source_span", "confidence"],
    "properties": {
        "name": _string("The practitioner's name, as written: 'Dr H Nguyen'."),
        "role": _nullable_string(
            "Their role or specialty, copied as written: 'Consultant', 'cardiology'. "
            "Null if the page does not say."
        ),
        "evidence_tier": _tier(),
        "source_span": _SOURCE_SPAN,
        "confidence": _confidence(),
    },
}


#: What a recording can be. A voice note is a note; offering "prescription" would
#: invite the model to promote what it heard.
TRANSCRIPT_KINDS = ("note", "unreadable")
TRANSCRIPT_TIER = "patient-reported"


def family_schema(family: str, transcript: bool = False) -> dict[str, Any]:
    """The answer shape for one group. The first group also reports the page itself.

    *transcript* narrows it for a voice note: the only tier the grammar can
    express is ``patient-reported``, and the only kinds are note and unreadable.
    The reader caps the tier by mime as well; neither is redundant, because the
    grammar stops the model saying it and the cap stops it meaning it.
    """
    built = _family_schema(family)
    return _for_transcript(built) if transcript else built


def _for_transcript(node: Any) -> Any:
    if isinstance(node, dict):
        narrowed = {name: _for_transcript(value) for name, value in node.items()}
        properties = narrowed.get("properties")
        if isinstance(properties, dict):
            if "evidence_tier" in properties:
                properties["evidence_tier"] = {
                    **properties["evidence_tier"],
                    "enum": [TRANSCRIPT_TIER],
                    "description": (
                        "Always patient-reported. This is a recording of the person "
                        "whose record this is, whatever they are describing."
                    ),
                }
            if "artifact_kind" in properties:
                properties["artifact_kind"] = {
                    **properties["artifact_kind"],
                    "enum": list(TRANSCRIPT_KINDS),
                }
        return narrowed
    if isinstance(node, list):
        return [_for_transcript(item) for item in node]
    return node


def _family_schema(family: str) -> dict[str, Any]:
    if family == MEDICATIONS:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "artifact_kind", "readable", "unreadable_reason", "document_date",
                "medications", "unclear",
            ],
            "properties": {
                "artifact_kind": {"type": "string", "enum": list(ARTIFACT_KINDS)},
                "readable": {
                    "type": "boolean",
                    "description": (
                        "False if you cannot read this page well enough to be sure of "
                        "what it says. That is a correct and useful answer, and always "
                        "better than a guess."
                    ),
                },
                "unreadable_reason": _nullable_string(
                    "If readable is false, what stops you reading it."
                ),
                "document_date": _OCCURRED_AT,
                "medications": {
                    "type": "array",
                    "maxItems": 30,
                    "items": _MEDICATION,
                    "description": "Every medicine you can read with certainty.",
                },
                "unclear": _unclear(MEDICATIONS),
            },
        }
    if family == ALLERGIES:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["allergies", "unclear"],
            "properties": {
                "allergies": {
                    "type": "array",
                    "maxItems": 20,
                    "items": _ALLERGY,
                    "description": "Every allergy or reaction you can read with certainty.",
                },
                "unclear": _unclear(ALLERGIES),
            },
        }
    if family == PROBLEMS:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["problems", "people", "unclear"],
            "properties": {
                "problems": {"type": "array", "maxItems": 20, "items": _PROBLEM},
                "people": {"type": "array", "maxItems": 10, "items": _PERSON},
                "unclear": _unclear(PROBLEMS),
            },
        }
    raise ValueError(f"unknown family {family!r}")


def schema_name(family: str) -> str:
    return f"health_record_{family}"


#: Every group's schema, in order. Hashed together into the prompt hash.
FAMILY_SCHEMAS: dict[str, dict[str, Any]] = {f: family_schema(f) for f in FAMILIES}
TRANSCRIPT_SCHEMAS: dict[str, dict[str, Any]] = {
    f: family_schema(f, transcript=True) for f in FAMILIES
}
