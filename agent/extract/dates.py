"""Turning a date the model copied into a date the record can hold.

The model never does date math, so what arrives here is a span of text as it
appeared on the page — ``04/06/2026``, ``4 June 2026``, ``June 2026``. This
normalises those, deterministically, over an **enumerated vocabulary**. Anything
outside it yields nothing rather than a guess.

That is the same conservatism the number-word parser already applies: "a wrong
quantity ages a medication to ``stale`` on fiction", and a wrong date does the
same to a timeline. A date this cannot read becomes an explicit null and a
reported span, which the projection turns into a review item.

**Day/month order comes from the configured locale**, and an ambiguous numeric
date is only accepted when the locale settles it. ``04/06/2026`` is 4 June in
Australia and 6 April in the United States, and a record that silently picks one
is wrong two months of the year in a way nothing surfaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..projection.dates import MONTH_NAMES, PRECISIONS

#: Locales that write the day first. Everything not listed is treated as
#: month-first only where it is explicitly US-style; otherwise an ambiguous
#: numeric date is refused rather than assumed.
_DAY_FIRST_LOCALES = frozenset(
    {"en", "en-au", "en-gb", "en-nz", "en-ie", "en-in", "en-za", "fr", "de", "es", "it", "pt", "nl"}
)
_MONTH_FIRST_LOCALES = frozenset({"en-us", "en-ph"})

_MONTH_BY_NAME = {name.lower(): number for number, name in enumerate(MONTH_NAMES, start=1)}
_MONTH_BY_NAME.update({name.lower()[:3]: number for number, name in enumerate(MONTH_NAMES, start=1)})
_MONTH_BY_NAME["sept"] = 9

_MONTH_WORDS = "|".join(sorted(_MONTH_BY_NAME, key=len, reverse=True))

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")
_DAY_MONTH_YEAR = re.compile(
    rf"^(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_WORDS})\.?,?\s+(\d{{4}})$", re.IGNORECASE
)
_MONTH_DAY_YEAR = re.compile(
    rf"^({_MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})$", re.IGNORECASE
)
_MONTH_YEAR = re.compile(rf"^({_MONTH_WORDS})\.?,?\s+(\d{{4}})$", re.IGNORECASE)
_YEAR = re.compile(r"^(\d{4})$")

#: "early June 2026", "mid-June 2026", "late June 2026". A real way documents
#: write a date, and each maps to a stated part of the month rather than a
#: chosen day — the precision stays `month` and the uncertainty says the rest.
_PART_OF_MONTH = re.compile(
    rf"^(early|mid|middle|late)[\s-]+({_MONTH_WORDS})\.?,?\s+(\d{{4}})$", re.IGNORECASE
)
_PART_DAYS = {"early": 5, "mid": 15, "middle": 15, "late": 25}


@dataclass(frozen=True)
class ReadDate:
    """A date read off a span, with the precision the span itself supports."""

    value: date
    precision: str = "day"
    uncertainty_days: int = 0

    def payload(self) -> dict[str, object]:
        """The ``occurred_at`` object the claim payload carries."""
        return {
            "value": self.value.isoformat(),
            "precision": self.precision,
            "uncertainty_days": self.uncertainty_days,
        }


def _clean(span: object) -> str | None:
    if not isinstance(span, str):
        return None
    text = " ".join(span.strip().split())
    return text or None


def _day_first(locale: str) -> bool | None:
    """Whether this locale writes 4/6 as 4 June. ``None`` when it does not say."""
    key = locale.strip().lower().replace("_", "-")
    if key in _MONTH_FIRST_LOCALES:
        return False
    if key in _DAY_FIRST_LOCALES:
        return True
    base = key.split("-", 1)[0]
    if base in _MONTH_FIRST_LOCALES:
        return False
    if base in _DAY_FIRST_LOCALES:
        return True
    return None


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def read(span: object, locale: str = "en") -> ReadDate | None:
    """A calendar date from a span the model copied, or ``None``.

    Never partially successful: a span this does not fully recognise yields
    nothing, because half a date is a date with an invented half.
    """
    text = _clean(span)
    if text is None:
        return None

    match = _ISO.match(text)
    if match:
        value = _safe(int(match[1]), int(match[2]), int(match[3]))
        return ReadDate(value) if value else None

    match = _ISO_MONTH.match(text)
    if match:
        value = _safe(int(match[1]), int(match[2]), 15)
        return ReadDate(value, "month") if value else None

    match = _DAY_MONTH_YEAR.match(text)
    if match:
        value = _safe(int(match[3]), _MONTH_BY_NAME[match[2].lower()], int(match[1]))
        return ReadDate(value) if value else None

    match = _MONTH_DAY_YEAR.match(text)
    if match:
        value = _safe(int(match[3]), _MONTH_BY_NAME[match[1].lower()], int(match[2]))
        return ReadDate(value) if value else None

    match = _PART_OF_MONTH.match(text)
    if match:
        month = _MONTH_BY_NAME[match[2].lower()]
        value = _safe(int(match[3]), month, _PART_DAYS[match[1].lower()])
        # Month precision with a few days of slack: the document said which part
        # of the month, which is more than "June" and less than a date.
        return ReadDate(value, "month", 5) if value else None

    match = _MONTH_YEAR.match(text)
    if match:
        value = _safe(int(match[2]), _MONTH_BY_NAME[match[1].lower()], 15)
        return ReadDate(value, "month") if value else None

    match = _YEAR.match(text)
    if match:
        value = _safe(int(match[1]), 7, 1)
        return ReadDate(value, "year") if value else None

    match = _NUMERIC.match(text)
    if match:
        return _numeric(match, locale)

    return None


def _numeric(match: re.Match[str], locale: str) -> ReadDate | None:
    """``04/06/2026``. Refused outright where the locale does not settle the order."""
    first, second = int(match[1]), int(match[2])
    year = int(match[3])
    if year < 100:
        # A two-digit year in a health record is a scanned form. 70 is the usual
        # pivot and this is only ever used for a document date, never a birth date.
        year += 2000 if year < 70 else 1900

    if first > 12 and second <= 12:
        return _from(year, second, first)   # unambiguous: 25/06 is 25 June
    if second > 12 and first <= 12:
        return _from(year, first, second)   # unambiguous: 06/25 is 25 June

    day_first = _day_first(locale)
    if day_first is None:
        # Both readings are valid dates and nothing settles which. Refusing is
        # the only honest answer: guessing is wrong two months in twelve, and
        # nothing downstream would ever surface it.
        return None
    return _from(year, second, first) if day_first else _from(year, first, second)


def _from(year: int, month: int, day: int) -> ReadDate | None:
    value = _safe(year, month, day)
    return ReadDate(value) if value else None


def normalise(payload: object, locale: str = "en") -> tuple[dict[str, object] | None, str | None]:
    """Read the model's ``occurred_at`` object. Returns ``(payload, problem)``.

    The model copies the date as written and states the precision the *source*
    used; this turns the written form into ISO and refuses anything it cannot
    read outright. Precision is taken from the model only where it is one of the
    schema's own values, and is never allowed to sharpen what the span supports —
    a span reading "June 2026" stays month precision however the model labelled
    it.
    """
    if payload is None:
        return None, None
    if not isinstance(payload, dict):
        return None, f"occurred_at is not an object: {payload!r}"

    parsed = read(payload.get("value"), locale)
    if parsed is None:
        return None, (
            f"the date {payload.get('value')!r} is not in a form this build reads, so "
            f"it is recorded as unknown rather than guessed at"
        )

    stated = payload.get("precision")
    precision = stated if isinstance(stated, str) and stated in PRECISIONS else parsed.precision
    # The span is the authority on how sharp the date can be. A model calling
    # "June 2026" day-precise does not make it day-precise.
    if PRECISIONS.index(precision) < PRECISIONS.index(parsed.precision):
        precision = parsed.precision

    uncertainty = payload.get("uncertainty_days", parsed.uncertainty_days)
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, int) or uncertainty < 0:
        uncertainty = parsed.uncertainty_days
    uncertainty = max(uncertainty, parsed.uncertainty_days)

    return (
        ReadDate(parsed.value, precision, uncertainty).payload(),
        None,
    )
