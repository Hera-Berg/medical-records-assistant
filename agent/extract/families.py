"""One group's answer, turned into claims — or into things the reader could not read.

Every decision here fails in the visible direction. A medication whose name
carries a strength, a dose missing half of itself, an allergy with a number in
the substance: none of these becomes a claim, and none of them disappears. Each
becomes an :class:`~agent.extract.validate.Abstention`, which the projection puts
in front of the person as "could not be read". The measure this module answers
to is the eval's: **wrong must be zero** on medications, doses and allergies,
and everything that is not correct must be an abstention somebody can see.

Three rules the schema asks for and this module enforces, because a small model
does not always do what it is asked.

**The subject is the name only.** A name containing a digit or a unit —
``Atorvastatin 20mg tablets`` — is a strength that leaked into the identifier,
and would mint ``med:atorvastatin-20mg-tablets`` beside ``med:atorvastatin``. It
is refused, not trimmed: trimming is repair, and ``MODELS.md`` says never repair.

**A dose is its strength and its frequency together.** Both copied spans, joined
with a space. One without the other is not the dose on the page, so it is
proposed as neither, and the half that was missing is named as unread.

**Validate, then reject. Never repair.** A group whose answer does not match its
schema proposes nothing from that group, and says so.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..projection import subjects, tiers
from ..projection import values as values_mod
from . import dates as dates_mod
from .schema import ALLERGIES, MEDICATIONS, PROBLEMS, family_schema
from .validate import Abstention, Extraction, ReadClaim, Rejection, _cap_tier, _dispense, check

#: A digit anywhere, or a unit or dose form as a whole word. Either in a name
#: means the name carries something that is not the name.
_UNIT_WORDS = sorted(
    set(values_mod._UNIT_SYNONYMS) | set(values_mod._DOSE_FORMS), key=len, reverse=True
)
_NOT_A_NAME = re.compile(
    r"\d|\b(?:" + "|".join(re.escape(word) for word in _UNIT_WORDS) + r")\b",
    re.IGNORECASE,
)


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _subject(kind: str, name: str) -> str | None:
    subject_id = subjects.make_id(kind, name)
    subject = subjects.parse(subject_id) if subject_id else None
    return subject.id if subject is not None else None


def _when(raw: Mapping[str, Any], locale: str) -> tuple[dict | None, str | None, list[str]]:
    notes: list[str] = []
    occurred, problem = dates_mod.normalise(raw.get("occurred_at"), locale)
    if problem:
        notes.append(problem)
    span = _text(raw.get("occurred_span"))
    if occurred is None and problem and span is None:
        written = (raw.get("occurred_at") or {}).get("value")
        span = _text(written)
    return occurred, span, notes


def _claim(
    subject_id: str,
    name: str,
    predicate: str,
    value: str,
    raw: Mapping[str, Any],
    mime: str,
    locale: str,
    dispense: dict | None = None,
) -> ReadClaim:
    notes: list[str] = []
    tier, capped = _cap_tier(raw["evidence_tier"], mime)
    if capped:
        notes.append(capped)
    occurred, span, date_notes = _when(raw, locale) if "occurred_at" in raw else (None, None, [])
    notes.extend(date_notes)
    kind = subject_id.split(":", 1)[0]
    if not tiers.is_known_predicate(kind, predicate):
        notes.append(
            f"{kind}:{predicate} is not in the consequence table, so it is gated as "
            f"high-consequence until the table says otherwise"
        )
    return ReadClaim(
        subject=subject_id,
        subject_literal=name,
        predicate=predicate,
        value_literal=value,
        evidence_tier=tier,
        confidence=float(raw["confidence"]),
        source_span=str(raw["source_span"]).strip(),
        occurred_at=occurred,
        occurred_span=span,
        dispense=dispense,
        notes=tuple(notes),
    )


def _unclear(family: str, raw: Mapping[str, Any], kind: str) -> Abstention:
    name = _text(raw.get("name_if_readable"))
    subject = _subject(kind, name) if name and not _NOT_A_NAME.search(name) else None
    return Abstention(
        family=family,
        field=str(raw["field"]),
        reason=_text(raw.get("reason")) or "the reader could not read it",
        subject=subject,
        subject_name=name,
        source_span=_text(raw.get("source_span")),
    )


def _bad_name(family: str, field: str, name: str, raw: Mapping[str, Any]) -> Abstention:
    reason = (
        f"the name read off the page, {name!r}, has a number, unit or form in it, "
        f"so it is not filed under a name that may be wrong"
        if name
        else "the reader gave no name for it, so there is nothing to file it under"
    )
    return Abstention(
        family=family,
        field=field,
        reason=reason,
        subject=None,
        subject_name=None,
        source_span=_text(raw.get("source_span")),
    )


def _medications(payload: Mapping[str, Any], mime: str, locale: str) -> Extraction:
    claims: list[ReadClaim] = []
    abstentions: list[Abstention] = []
    for raw in payload["medications"]:
        name = raw["name"].strip()
        if not name or _NOT_A_NAME.search(name):
            abstentions.append(_bad_name(MEDICATIONS, "name", name, raw))
            continue
        subject_id = _subject("med", name)
        if subject_id is None:
            abstentions.append(_bad_name(MEDICATIONS, "name", name, raw))
            continue
        strength = _text(raw.get("strength"))
        frequency = _text(raw.get("frequency"))
        stopped = _text(raw.get("stopped"))
        said_something = False
        if strength and frequency:
            claims.append(
                _claim(
                    subject_id, name, "dose", f"{strength} {frequency}", raw, mime, locale,
                    dispense=_dispense(raw.get("dispense")),
                )
            )
            said_something = True
        elif strength or frequency:
            missing = "frequency" if strength else "strength"
            abstentions.append(
                Abstention(
                    family=MEDICATIONS,
                    field=missing,
                    reason=(
                        f"the {missing} was not read, and a dose without its "
                        f"{missing} is not the dose on the page, so no dose is proposed"
                    ),
                    subject=subject_id,
                    subject_name=name,
                    source_span=_text(raw.get("source_span")),
                )
            )
            said_something = True
        if stopped:
            claims.append(_claim(subject_id, name, "status", "stopped", raw, mime, locale))
            said_something = True
        if not said_something:
            # Named and nothing more. A medicine mentioned with no dose is still a
            # medicine on the page, and dropping it is the silent miss this
            # record exists to prevent.
            claims.append(_claim(subject_id, name, "name", name, raw, mime, locale))
    abstentions.extend(_unclear(MEDICATIONS, raw, "med") for raw in payload["unclear"])
    return Extraction(claims=tuple(claims), abstentions=tuple(abstentions))


def _allergies(payload: Mapping[str, Any], mime: str, locale: str) -> Extraction:
    claims: list[ReadClaim] = []
    abstentions: list[Abstention] = []
    for raw in payload["allergies"]:
        substance = raw["substance"].strip()
        subject_id = None if (not substance or _NOT_A_NAME.search(substance)) else _subject("allergy", substance)
        if subject_id is None:
            abstentions.append(_bad_name(ALLERGIES, "substance", substance, raw))
            continue
        reaction = _text(raw.get("reaction"))
        if reaction:
            claims.append(_claim(subject_id, substance, "reaction", reaction, raw, mime, locale))
        else:
            claims.append(_claim(subject_id, substance, "substance", substance, raw, mime, locale))
    abstentions.extend(_unclear(ALLERGIES, raw, "allergy") for raw in payload["unclear"])
    return Extraction(claims=tuple(claims), abstentions=tuple(abstentions))


def _problems(payload: Mapping[str, Any], mime: str, locale: str) -> Extraction:
    claims: list[ReadClaim] = []
    abstentions: list[Abstention] = []
    for raw in payload["problems"]:
        name = raw["name"].strip()
        subject_id = _subject("problem", name) if name else None
        if subject_id is None:
            abstentions.append(_bad_name(PROBLEMS, "name", name, raw))
            continue
        claims.append(_claim(subject_id, name, "name", name, raw, mime, locale))
    for raw in payload["people"]:
        name = raw["name"].strip()
        subject_id = _subject("person", name) if name else None
        if subject_id is None:
            abstentions.append(_bad_name(PROBLEMS, "name", name, raw))
            continue
        role = _text(raw.get("role"))
        claims.append(
            _claim(subject_id, name, "role" if role else "name", role or name, raw, mime, locale)
        )
    abstentions.extend(_unclear(PROBLEMS, raw, "problem") for raw in payload["unclear"])
    return Extraction(claims=tuple(claims), abstentions=tuple(abstentions))


_READERS = {MEDICATIONS: _medications, ALLERGIES: _allergies, PROBLEMS: _problems}


def read(
    family: str, payload: Any, mime: str = "", locale: str = "en", transcript: bool = False
) -> Extraction:
    """Validate one group's answer and turn what survives into claims and abstentions.

    Never raises on bad model output. A group whose answer does not match its
    schema proposes nothing and is marked refused; the first group also carries
    whether the page could be read at all.
    """
    problems = check(payload, family_schema(family, transcript=transcript))
    if problems:
        return Extraction(
            readable=False,
            refused=True,
            unreadable_reason=(
                f"the model's answer about {family} did not match its schema, so "
                f"nothing about {family} is proposed from it"
            ),
            rejections=tuple(Rejection(problem) for problem in problems),
        )

    notes: list[str] = []
    document_date = None
    artifact_kind = "other"
    if family == MEDICATIONS:
        document_date, date_problem = dates_mod.normalise(payload.get("document_date"), locale)
        if date_problem:
            notes.append(date_problem)
        artifact_kind = payload["artifact_kind"]
        if not payload["readable"]:
            return Extraction(
                artifact_kind=artifact_kind,
                readable=False,
                unreadable_reason=_text(payload.get("unreadable_reason"))
                or "the model could not read this page and did not say why",
                document_date=document_date,
                notes=tuple(notes),
            )

    part = _READERS[family](payload, mime, locale)
    return Extraction(
        artifact_kind=artifact_kind,
        readable=True,
        document_date=document_date,
        claims=part.claims,
        abstentions=part.abstentions,
        notes=tuple(notes),
    )
