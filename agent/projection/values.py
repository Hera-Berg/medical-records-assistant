"""What a claim asserts: the literal span, and a normalised form beside it.

Two forms, two jobs, and they must not be confused.

The **literal** is what the source actually said — ``5mg``, ``2.5 mg``,
``twice daily``. It is the only thing ever rendered, because the wiki is meant to
be a faithful quotation of the artefact behind it. A script that says ``5mg``
renders ``5mg`` and never ``5.0mg``.

The **normalised key** is what machines compare: it decides whether two readings
agree, whether a slot is conflicted, and what ``dispense`` counts with. It is
derived from the literal and never replaces it.

Numbers go through :class:`~decimal.Decimal`, never ``float``. ``2.5`` that has
round-tripped through binary floating point can surface as
``2.5000000000000004``, which in a medical record is both wrong-looking and
non-deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Mapping

#: Unit spellings that mean the same thing. Comparison only — the literal keeps
#: whatever spelling the source used.
_UNIT_SYNONYMS: dict[str, str] = {
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "mcg": "mcg", "microgram": "mcg", "micrograms": "mcg", "µg": "mcg", "ug": "mcg",
    "g": "g", "gram": "g", "grams": "g",
    "ml": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml",
    "milliliters": "ml",
    "unit": "unit", "units": "unit", "iu": "unit",
    "tablet": "tablet", "tablets": "tablet", "tab": "tablet", "tabs": "tablet",
    "capsule": "capsule", "capsules": "capsule", "cap": "capsule", "caps": "capsule",
    "puff": "puff", "puffs": "puff",
    "drop": "drop", "drops": "drop",
    "patch": "patch", "patches": "patch",
}

#: Frequency wording -> a canonical label. Two spellings of the same schedule
#: must produce the same label, or "5mg daily" and "5mg once daily" would read
#: as a contradiction and mark a perfectly consistent medication conflicted.
_FREQUENCY_CANON: dict[str, str] = {
    "daily": "1/day", "once daily": "1/day", "once a day": "1/day",
    "one daily": "1/day", "1 daily": "1/day", "every day": "1/day", "od": "1/day",
    "mane": "1/day", "nocte": "1/day", "at night": "1/day",
    "in the morning": "1/day", "every 24 hours": "1/day",
    "twice daily": "2/day", "twice a day": "2/day", "two times a day": "2/day",
    "2 daily": "2/day", "bd": "2/day", "bid": "2/day", "every 12 hours": "2/day",
    "three times daily": "3/day", "three times a day": "3/day",
    "3 times a day": "3/day", "3 times daily": "3/day",
    "tds": "3/day", "tid": "3/day", "every 8 hours": "3/day",
    "four times daily": "4/day", "four times a day": "4/day",
    "4 times a day": "4/day", "4 times daily": "4/day",
    "qds": "4/day", "qid": "4/day", "every 6 hours": "4/day",
    "every other day": "1/2days", "alternate days": "1/2days",
    "every second day": "1/2days",
    "weekly": "1/week", "once a week": "1/week", "every week": "1/week",
    "fortnightly": "1/2weeks", "monthly": "1/month",
    "as needed": "prn", "as required": "prn", "prn": "prn",
    "when required": "prn", "when needed": "prn",
}

#: Canonical label -> doses per day, as an exact fraction. ``None`` means the
#: rate is genuinely unknowable from the words, which is different from absent:
#: "as needed" has no rate, so no exhaustion date can be computed from it.
_FREQUENCY_RATES: dict[str, Fraction | None] = {
    "1/day": Fraction(1),
    "2/day": Fraction(2),
    "3/day": Fraction(3),
    "4/day": Fraction(4),
    "1/2days": Fraction(1, 2),
    "1/week": Fraction(1, 7),
    "1/2weeks": Fraction(1, 14),
    "1/month": Fraction(1, 30),
    "prn": None,
}

#: Number words, so "one daily" and "1 daily" agree.
#:
#: Split deliberately. ``CARDINALS`` are words that unambiguously name a count.
#: ``ARTICLES`` mean one only when they precede a unit — "a tablet" is one
#: tablet, but reading "a shoebox full" as a quantity of one is exactly the
#: fluent wrong answer this record cannot tolerate. ``ZERO_WORDS`` are counted
#: separately because "no repeats" is a real, meaningful zero.
CARDINALS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "ninety": 90, "hundred": 100,
}

ARTICLES: dict[str, int] = {"a": 1, "an": 1}

ZERO_WORDS: dict[str, int] = {"no": 0, "nil": 0, "none": 0, "zero": 0}

#: The union, used only where any of the three readings is acceptable — such as
#: normalising "one daily" to "1 daily" before a frequency lookup.
NUMBER_WORDS: dict[str, int] = {**CARDINALS, **ARTICLES, **ZERO_WORDS}

_UNIT_ALTERNATION = "|".join(
    sorted((re.escape(u) for u in _UNIT_SYNONYMS), key=len, reverse=True)
)
_AMOUNT_RE = re.compile(
    rf"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})\b",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def normalise_text(value: str) -> str:
    """Casefold-free lowering, collapsed whitespace, no trailing stop.

    ``str.lower`` is Unicode-defined and not locale-sensitive, so this is stable
    on any machine. Sorting elsewhere is by code point for the same reason.
    """
    text = _WS_RE.sub(" ", value.strip()).lower()
    return text.rstrip(".").strip()


def format_number(value: object) -> str | None:
    """Render a number without ever going through binary floating point."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (str, float, Decimal)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if number == number.to_integral_value():
            return str(number.quantize(Decimal(1)))
        return str(number.normalize())
    return None


def amounts(text: str) -> set[tuple[str, str]]:
    """Every ``(amount, unit)`` a span states, normalised for comparison.

    ``"5mg"``, ``"5 mg"`` and ``"5.0 milligrams"`` all yield ``{("5", "mg")}``.
    Public because the extraction layer compares two independent readings of the
    same page and must use exactly the normalisation the projection uses — two
    readers agreeing must never look like a disagreement because one of them
    said "milligrams".

    For comparison only. The literal is what gets rendered, always.
    """
    found: set[tuple[str, str]] = set()
    for match in _AMOUNT_RE.finditer(normalise_text(text)):
        amount = format_number(match.group("amount"))
        if amount is not None:
            found.add((amount, _UNIT_SYNONYMS[match.group("unit").lower()]))
    return found


def canonical_frequency(text: str) -> tuple[str, Fraction | None] | None:
    """Map frequency wording to its canonical label and rate per day.

    ``None`` when the wording is not recognised at all — which is reported, not
    guessed at. A rate of ``None`` inside a returned pair is different: the
    schedule is understood ("as needed") and genuinely has no rate.
    """
    key = normalise_text(text)
    words = [str(NUMBER_WORDS[w]) if w in NUMBER_WORDS else w for w in key.split()]
    for candidate in (key, " ".join(words)):
        label = _FREQUENCY_CANON.get(candidate)
        if label is not None:
            return label, _FREQUENCY_RATES[label]
    return None


@dataclass(frozen=True)
class Value:
    """One claim value: what the source said, and how to compare it."""

    literal: str
    key: str
    fields: Mapping[str, str] = field(default_factory=dict)

    @property
    def rate_per_day(self) -> Fraction | None:
        """Doses per day where the value states a frequency, else ``None``."""
        frequency = self.fields.get("frequency")
        if frequency is None:
            return None
        return _FREQUENCY_RATES.get(frequency)

    def agrees_with(self, other: "Value") -> bool:
        return self.key == other.key


def _fields_from_text(text: str) -> dict[str, str]:
    """Pull amount, unit and frequency out of a free-text dose span."""
    fields: dict[str, str] = {}
    remainder = text
    match = _AMOUNT_RE.search(text)
    if match:
        amount = format_number(match.group("amount"))
        if amount is not None:
            fields["amount"] = amount
        fields["unit"] = _UNIT_SYNONYMS[match.group("unit").lower()]
        remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    frequency = canonical_frequency(remainder) if remainder else None
    if frequency is not None:
        fields["frequency"] = frequency[0]
    elif remainder and not fields:
        return {}
    return fields


def _compose_literal(data: Mapping[str, Any]) -> str | None:
    """Build a display string from a structured value that carries no literal.

    Only used when the source gave us no span of its own. Mirrors the spec's
    ``dose: 5mg daily`` shape and formats every number through
    :func:`format_number`.
    """
    amount = format_number(data.get("amount"))
    unit = data.get("unit")
    parts: list[str] = []
    if amount is not None and isinstance(unit, str) and unit.strip():
        parts.append(f"{amount}{unit.strip()}")
    elif amount is not None:
        parts.append(amount)
    for name in ("frequency", "route", "status"):
        item = data.get(name)
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
    if parts:
        return " ".join(parts)
    if len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, str) and only.strip():
            return only.strip()
        return format_number(only)
    return None


def parse(payload: object) -> Value | None:
    """Turn a claim payload's ``value`` into a :class:`Value`, or refuse.

    Accepts the spec's structured object, a bare string, or a number. Anything
    else — a list, an empty object, a null — is refused so the claim is reported
    as malformed rather than coerced into something renderable.
    """
    if isinstance(payload, str):
        literal = payload.strip()
        if not literal:
            return None
        fields = _fields_from_text(literal)
        key = _key_from_fields(fields) if fields else normalise_text(literal)
        return Value(literal=literal, key=key, fields=fields)

    if isinstance(payload, bool):
        return None
    if isinstance(payload, (int, float, Decimal)):
        literal = format_number(payload)
        if literal is None:
            return None
        return Value(literal=literal, key=literal, fields={"amount": literal})

    if isinstance(payload, dict) and payload:
        stated = payload.get("literal")
        literal = stated.strip() if isinstance(stated, str) and stated.strip() else None
        fields: dict[str, str] = {}
        amount = format_number(payload.get("amount"))
        if amount is not None:
            fields["amount"] = amount
        unit = payload.get("unit")
        if isinstance(unit, str) and unit.strip():
            fields["unit"] = _UNIT_SYNONYMS.get(normalise_text(unit), normalise_text(unit))
        for name in ("frequency", "route", "status", "reaction", "severity", "name"):
            item = payload.get(name)
            if isinstance(item, str) and item.strip():
                if name == "frequency":
                    canonical = canonical_frequency(item)
                    fields[name] = canonical[0] if canonical else normalise_text(item)
                else:
                    fields[name] = normalise_text(item)
        if literal is None:
            literal = _compose_literal(payload)
        if literal is None:
            return None
        if not fields:
            fields = _fields_from_text(literal)
        key = _key_from_fields(fields) if fields else normalise_text(literal)
        return Value(literal=literal, key=key, fields=fields)

    return None


def _key_from_fields(fields: Mapping[str, str]) -> str:
    """Sorted ``name=value`` pairs. Code-point ordering, never locale ordering."""
    return "|".join(f"{name}={fields[name]}" for name in sorted(fields))
