"""How long a dispensed quantity lasts, and when it runs out.

Every number here is computed by Python from literal spans the model copied off
the page. ``MODELS.md`` is unambiguous: "The model never does arithmetic or date
math. It extracts literal strings — ``30 tablets``, ``twice daily``,
``1 repeat`` — and Python parses them deterministically... This applies to
expected-exhaustion dates, dose totals, and date normalisation without
exception." So a claim may carry the spans; it may never carry the answer.

The other half of the rule is that an unparseable span produces **no** date. An
expected-exhaustion date is the thing that later marks a medication stale, and a
guessed one would age a medication out of confidence on the strength of a
sentence nobody could read. As-needed dosing is the same case for a different
reason: the words are understood, the consumption rate genuinely is not, so
there is nothing to count.

Arithmetic is in :class:`~fractions.Fraction`, so "one tablet every other day"
is exact rather than a float that lands a day early once a year.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from . import values
from .dates import FuzzyDate

#: A quantity span: "30 tablets", "30", "1 x 30 tablets", "sixty capsules".
_QUANTITY_RE = re.compile(
    r"(?:(?P<packs>\d+)\s*[x×]\s*)?(?P<count>\d+|[a-z]+)\s*"
    r"(?P<unit>tablets?|tabs?|capsules?|caps?|puffs?|drops?|patches?|doses?|ml|units?)?\b",
    re.IGNORECASE,
)

#: A repeats span: "no repeats", "nil repeats", "1 repeat", "2 repeats", "0".
_REPEATS_RE = re.compile(r"(?P<count>\d+|[a-z]+)\s*(?:repeats?|refills?)?", re.IGNORECASE)


@dataclass(frozen=True)
class Dispense:
    """The literal spans a script yielded, and what they work out to."""

    quantity_literal: str | None = None
    frequency_literal: str | None = None
    repeats_literal: str | None = None
    dose_units_literal: str | None = None

    quantity: int | None = None
    repeats: int | None = None
    rate_per_day: Fraction | None = None
    dose_units: Fraction | None = None

    #: Why no supply could be computed, in words. ``None`` when one could.
    unreadable: str | None = None

    @property
    def days_supply(self) -> int | None:
        """Whole days the dispensed quantity covers, or ``None``.

        Floored, not rounded: a supply that runs out mid-day has run out. The
        answer is only ever as good as the spans it came from, which is why the
        literals stay attached to it.
        """
        if self.quantity is None or self.rate_per_day is None or self.repeats is None:
            return None
        if self.rate_per_day <= 0:
            return None
        per_day = self.rate_per_day * (self.dose_units or Fraction(1))
        if per_day <= 0:
            return None
        total = Fraction(self.quantity) * (1 + self.repeats)
        return int(total / per_day)

    @property
    def is_computable(self) -> bool:
        return self.days_supply is not None

    @property
    def has_spans(self) -> bool:
        """Whether a source actually stated a supply, as opposed to only a dose.

        :func:`parse` accepts the dose claim's own frequency as a fallback,
        because "5mg daily, 30 tablets" is how a script really reads and the
        dispense block does not always restate the frequency. On its own that
        fallback is not supply information — it is the dose, which the page has
        already rendered as the dose — so a Supply section built from it says
        only that there is nothing to say, which is noise on a page a clinician
        is skimming.
        """
        return any(
            (self.quantity_literal, self.repeats_literal, self.dose_units_literal)
        )

    def exhaustion(self, start: FuzzyDate | None) -> FuzzyDate | None:
        """When the supply is expected to run out, counted from *start*.

        The result inherits the start date's precision and uncertainty. A script
        dated "around April" cannot yield an exhaustion date known to the day,
        and pretending otherwise is how a medication gets marked stale on a date
        the record never actually established.
        """
        days = self.days_supply
        if days is None or start is None:
            return None
        return start.shifted(days)

    def describe(self) -> str:
        """The spans this was read from, for a footnoted sentence in the wiki."""
        parts = [
            part
            for part in (self.quantity_literal, self.frequency_literal, self.repeats_literal)
            if part
        ]
        return "; ".join(parts)


def _count_word(token: str, table: dict[str, int]) -> int | None:
    """A count from digits or from an explicitly permitted word. Never a guess."""
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return table.get(token)


def _best_quantity_match(text: str) -> tuple[int, int] | None:
    """Scan for the count and pack multiplier that a quantity span really states.

    A span reads left to right but its quantity does not: "one pack of 30
    tablets" states 30, not one. Matches carrying a recognised unit word are
    therefore preferred over bare numbers, and a span with neither — "a shoebox
    full" — yields nothing at all rather than the ``1`` that its article would
    otherwise contribute.
    """
    united: list[tuple[int, int]] = []
    bare: list[tuple[int, int]] = []
    for match in _QUANTITY_RE.finditer(text):
        has_unit = match.group("unit") is not None
        table = {**values.CARDINALS, **values.ARTICLES} if has_unit else values.CARDINALS
        count = _count_word(match.group("count"), table)
        if count is None or count <= 0:
            continue
        packs = _count_word(match.group("packs") or "1", values.CARDINALS) or 1
        (united if has_unit else bare).append((count, packs))
    for candidates in (united, bare):
        if candidates:
            return candidates[0]
    return None


def parse_quantity(span: object) -> int | None:
    """``"30 tablets"`` -> ``30``. ``"1 x 30 tablets"`` -> ``30``.

    Anything this cannot read yields ``None``, which becomes "no expected
    exhaustion date" rather than a number nobody can trace to the page.
    """
    if isinstance(span, int) and not isinstance(span, bool):
        return span if span > 0 else None
    if not isinstance(span, str) or not span.strip():
        return None
    best = _best_quantity_match(values.normalise_text(span))
    if best is None:
        return None
    count, packs = best
    return count * packs


def parse_repeats(span: object) -> int | None:
    """``"no repeats"`` -> ``0``, ``"1 repeat"`` -> ``1``. Absent stays absent.

    Zero words count here — "no repeats" is a real statement about the script —
    but articles do not: "a repeat" is not clear enough to build a staleness
    date on.
    """
    if isinstance(span, int) and not isinstance(span, bool):
        return span if span >= 0 else None
    if not isinstance(span, str) or not span.strip():
        return None
    match = _REPEATS_RE.search(values.normalise_text(span))
    if not match:
        return None
    count = _count_word(match.group("count"), {**values.CARDINALS, **values.ZERO_WORDS})
    return count if count is not None and count >= 0 else None


def parse_dose_units(span: object) -> Fraction | None:
    """``"two tablets"`` -> ``2``: how many units are taken each time."""
    if isinstance(span, int) and not isinstance(span, bool):
        return Fraction(span) if span > 0 else None
    if not isinstance(span, str) or not span.strip():
        return None
    best = _best_quantity_match(values.normalise_text(span))
    return Fraction(best[0] * best[1]) if best else None


def parse(payload: object, fallback_frequency: str | None = None) -> Dispense | None:
    """Read a claim payload's ``dispense`` block.

    ``{"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}``
    — literal spans only. *fallback_frequency* lets the dose claim's own
    frequency stand in when the block does not restate it, since "5mg daily,
    30 tablets" is how a script actually reads.
    """
    if payload is None and fallback_frequency is None:
        return None
    data = payload if isinstance(payload, dict) else {}
    if payload is not None and not isinstance(payload, dict):
        return Dispense(unreadable="the dispense block is not an object")

    quantity_literal = _span(data.get("quantity"))
    repeats_literal = _span(data.get("repeats"))
    dose_units_literal = _span(data.get("dose_units"))
    frequency_literal = _span(data.get("frequency")) or fallback_frequency

    if not any((quantity_literal, repeats_literal, frequency_literal, dose_units_literal)):
        return None

    quantity = parse_quantity(quantity_literal)
    repeats = parse_repeats(repeats_literal)
    dose_units = parse_dose_units(dose_units_literal)
    rate = None
    canonical = values.canonical_frequency(frequency_literal) if frequency_literal else None
    if canonical is not None:
        rate = canonical[1]

    unreadable = _why_unreadable(
        quantity_literal, quantity, frequency_literal, canonical, rate, repeats_literal, repeats
    )

    return Dispense(
        quantity_literal=quantity_literal,
        frequency_literal=frequency_literal,
        repeats_literal=repeats_literal,
        dose_units_literal=dose_units_literal,
        quantity=quantity,
        repeats=repeats,
        rate_per_day=rate,
        dose_units=dose_units,
        unreadable=unreadable,
    )


def _span(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _why_unreadable(
    quantity_literal: str | None,
    quantity: int | None,
    frequency_literal: str | None,
    canonical: tuple[str, Fraction | None] | None,
    rate: Fraction | None,
    repeats_literal: str | None,
    repeats: int | None,
) -> str | None:
    """A plain-language reason no supply could be counted, or ``None``."""
    if quantity_literal is None:
        return "no dispensed quantity was recorded"
    if quantity is None:
        return f"the dispensed quantity {quantity_literal!r} could not be read as a number"
    if frequency_literal is None:
        return "no dosing frequency was recorded"
    if canonical is None:
        return f"the dosing frequency {frequency_literal!r} is not one this build recognises"
    if rate is None:
        return f"{frequency_literal!r} has no fixed rate, so a supply cannot be counted"
    if repeats_literal is None:
        return "the number of repeats was not recorded"
    if repeats is None:
        return f"the repeats span {repeats_literal!r} could not be read as a number"
    return None
