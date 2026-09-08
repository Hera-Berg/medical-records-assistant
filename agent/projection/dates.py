"""Dates that know how sure they are.

"The headaches started around Easter" is not a date, and storing it as one is
worse than storing nothing: a timeline that fakes precision reads as fact. Every
date here carries a precision and an uncertainty, renders as a band rather than a
point wherever it is fuzzy, and refuses to be ordered against another date when
their bands overlap.

That refusal is load-bearing. Reconciliation breaks ties within an evidence tier
by taking the more recent ``occurred_at``; two dates that cannot be distinguished
must not silently resolve in favour of whichever parsed larger, because that
turns "we don't know which script is newer" into "this is the dose".

**Nothing here reads the clock or the locale.** Month names come from the table
below rather than from ``strftime`` ``%B``, which is translated under a different
``LANG`` and would make generated files differ between machines.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

#: Fixed English month names. Never ``%B``: that is locale-dependent, and the
#: whole wiki has to be byte-identical on any machine that rebuilds it.
MONTH_NAMES: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

#: Ordered coarsest last. A precision the record does not recognise is treated
#: as the coarsest rather than the finest, so an unknown value never sharpens a
#: date it should have blurred.
PRECISIONS: tuple[str, ...] = ("day", "month", "year")

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class FuzzyDate:
    """A date, its precision, and how far either side it might really be."""

    value: date
    precision: str = "day"
    uncertainty_days: int = 0

    @property
    def band(self) -> tuple[date, date]:
        """The earliest and latest this date could actually be.

        Precision widens the band to the containing month or year first, then
        ``uncertainty_days`` widens it further in both directions. The two are
        independent: "around Easter" is month precision *and* a fortnight of
        slack, and collapsing them would understate the fuzz.
        """
        start, end = self.value, self.value
        if self.precision == "month":
            start = self.value.replace(day=1)
            end = self.value.replace(day=calendar.monthrange(self.value.year, self.value.month)[1])
        elif self.precision == "year":
            start = date(self.value.year, 1, 1)
            end = date(self.value.year, 12, 31)
        slack = timedelta(days=max(0, self.uncertainty_days))
        return start - slack, end + slack

    @property
    def is_exact(self) -> bool:
        return self.precision == "day" and self.uncertainty_days == 0

    @property
    def iso(self) -> str:
        return self.value.isoformat()

    def render(self) -> str:
        """Human form, showing the fuzz wherever there is any."""
        if self.is_exact:
            return render_date(self.value)
        if self.precision == "year":
            base = str(self.value.year)
        elif self.precision == "month":
            base = f"{MONTH_NAMES[self.value.month - 1]} {self.value.year}"
        else:
            base = render_date(self.value)
        if self.uncertainty_days:
            return f"around {base} (±{self.uncertainty_days} days)"
        return f"around {base}" if self.precision != "day" else base

    def shifted(self, days: int) -> FuzzyDate:
        """This date moved by *days*, keeping its precision and uncertainty.

        Used for expected exhaustion, which is exactly as uncertain as the
        script date it is counted from.
        """
        return FuzzyDate(
            value=self.value + timedelta(days=days),
            precision=self.precision,
            uncertainty_days=self.uncertainty_days,
        )


def render_date(value: date) -> str:
    """``4 June 2026``. Locale-free by construction."""
    return f"{value.day} {MONTH_NAMES[value.month - 1]} {value.year}"


def render_month(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month - 1]} {year}"


def parse_iso_date(value: object) -> date | None:
    """Parse ``YYYY-MM-DD`` strictly. ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    match = _ISO_DATE_RE.match(value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_occurred_at(payload: object) -> FuzzyDate | None:
    """Read the spec's ``occurred_at`` object. Never guesses a missing date.

    ``{"value": "2026-04-05", "precision": "month", "uncertainty_days": 14}``.
    An explicit ``null``, an absent field, or an unparseable value all yield
    ``None`` — an unknown date stays unknown rather than acquiring a plausible
    one from somewhere nearby.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        parsed = parse_iso_date(payload)
        return FuzzyDate(parsed) if parsed else None
    if not isinstance(payload, dict):
        return None
    parsed = parse_iso_date(payload.get("value"))
    if parsed is None:
        return None
    precision = payload.get("precision", "day")
    if not isinstance(precision, str) or precision not in PRECISIONS:
        precision = "year"  # unrecognised means blurrier, never sharper
    uncertainty = payload.get("uncertainty_days", 0)
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, int) or uncertainty < 0:
        uncertainty = 0
    return FuzzyDate(value=parsed, precision=precision, uncertainty_days=uncertainty)


def can_order(left: FuzzyDate | None, right: FuzzyDate | None) -> bool:
    """Whether these two dates can be put in order at all.

    ``False`` when either is unknown or their bands overlap. Callers must treat
    that as "no answer" rather than falling back to a comparison of the nominal
    values — an unknown ordering is a conflict for a human, not a tie to break.
    """
    if left is None or right is None:
        return False
    left_start, left_end = left.band
    right_start, right_end = right.band
    return left_end < right_start or right_end < left_start


def is_later(left: FuzzyDate, right: FuzzyDate) -> bool:
    """Whether *left* is unambiguously later. Only valid when :func:`can_order`."""
    return left.band[0] > right.band[1]


def sort_key(value: FuzzyDate | None) -> tuple[int, str, str, int]:
    """A deterministic key for stable ordering. Not a substitute for :func:`can_order`."""
    if value is None:
        return (0, "", "", 0)
    return (1, value.value.isoformat(), value.precision, value.uncertainty_days)
