"""Suggesting a date for a phrase, without ever adopting one.

``CLAUDE.md``: "Unresolvable temporal references are preserved, never dropped and
never auto-resolved. 'Around Easter', 'last Christmas', 'the week before the
wedding' — the model copies the phrase verbatim and emits ``occurred_at: null``.
It never computes a date. The projection then raises a *dateable* review item
carrying a candidate computed in code where one exists — the computus is
deterministic, the year is not — for the user to confirm in one tap."

The split this module exists to hold: **which day Easter falls on in a given year
is arithmetic; which year the speaker meant is a guess.** So the computation is
done here, in code, and offered as a candidate the user taps. Nothing here writes
a date into the record, and no caller may treat a candidate as an answer.

Everything is deterministic and locale-free. A candidate depends only on the
phrase and the reference date it is measured from, so two machines projecting the
same log offer the same suggestions.

**A phrase with no candidate is the normal case, not a failure.** "The week
before the wedding" is perfectly good evidence and this module has nothing to add
to it; the review item stands on the phrase alone and asks the only person who
can answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import dates as dates_mod
from .dates import FuzzyDate

#: How far either side of a named occasion "around" reaches. A fortnight, which
#: is the same band the spec uses for "around Easter" and wide enough that the
#: user is confirming a period rather than being nudged onto a day.
_AROUND_DAYS = 14


def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday, by the anonymous computus.

    Pure arithmetic with no table and no locale. Included because a candidate
    that can be computed should be offered — the alternative is asking the user
    to look up a date the machine already knows.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _good_friday(year: int) -> date:
    return easter(year) - timedelta(days=2)


def _fixed(month: int, day: int):
    return lambda year: date(year, month, day)


#: Occasions whose date in a *given year* is computable. The year is never
#: computable, which is the whole reason these are candidates rather than values.
_OCCASIONS: tuple[tuple[re.Pattern[str], object, str], ...] = (
    (re.compile(r"\beaster\b"), easter, "Easter Sunday"),
    (re.compile(r"\bgood friday\b"), _good_friday, "Good Friday"),
    (re.compile(r"\bchristmas\b|\bxmas\b"), _fixed(12, 25), "Christmas Day"),
    (re.compile(r"\bboxing day\b"), _fixed(12, 26), "Boxing Day"),
    (re.compile(r"\bnew year'?s? (?:day|eve)?\b"), _fixed(1, 1), "New Year's Day"),
    (re.compile(r"\bhalloween\b"), _fixed(10, 31), "Halloween"),
)

#: Words that place the occasion in the past relative to the reference date.
_BACKWARD = re.compile(r"\blast\b|\bprevious\b|\bprior\b")


@dataclass(frozen=True)
class Candidate:
    """One date the user might mean, and why it is being offered.

    Never applied. This exists so that answering costs a tap rather than a
    calendar lookup, and a user who disagrees ignores it.
    """

    date: FuzzyDate
    label: str
    reason: str

    @property
    def iso(self) -> str:
        return self.date.iso

    def describe(self) -> str:
        return f"{self.label} — {self.date.render()}"


def candidates(phrase: str, reference: date | None) -> tuple[Candidate, ...]:
    """Dates *phrase* might mean, measured from *reference*. Possibly none.

    *reference* is the date the phrase was said or written near — the document
    date for a letter, the recording date for a voice note. It is used only to
    pick which year to compute, and a caller must pass the most reliable one it
    has rather than substituting ingest time silently; see
    :func:`describe_reference`.
    """
    if not isinstance(phrase, str) or not phrase.strip() or reference is None:
        return ()
    text = phrase.strip().lower()

    for pattern, resolve, label in _OCCASIONS:
        if not pattern.search(text):
            continue
        return _for_occasion(resolve, label, text, reference)
    return ()


def _for_occasion(resolve, label: str, text: str, reference: date) -> tuple[Candidate, ...]:
    """The occasion in the reference year, and in the year that suits the words.

    Two candidates at most, and only where both are genuinely plausible. "Last
    Christmas" said in March means the December three months back, not the one
    nine months ahead — but "around Easter" said in June is ambiguous between
    this year's, which has passed, and no other, so only one is offered.
    """
    this_year = resolve(reference.year)
    previous = resolve(reference.year - 1)

    if _BACKWARD.search(text):
        # Explicitly past. The most recent occurrence strictly before the
        # reference is the only reading the words support.
        chosen = this_year if this_year < reference else previous
        return (
            Candidate(
                _band(chosen, text),
                f"{label} {chosen.year}",
                f"the most recent {label} before {dates_mod.render_date(reference)}",
            ),
        )

    if this_year <= reference:
        return (
            Candidate(
                _band(this_year, text),
                f"{label} {this_year.year}",
                f"the {label} that had passed by {dates_mod.render_date(reference)}",
            ),
        )
    # The reference falls before this year's occasion, so the speaker is most
    # likely looking back at last year's — but this year's is close enough ahead
    # to be worth offering too. Both, in order of likelihood, for one tap.
    return (
        Candidate(
            _band(previous, text),
            f"{label} {previous.year}",
            f"the last {label} before {dates_mod.render_date(reference)}",
        ),
        Candidate(
            _band(this_year, text),
            f"{label} {this_year.year}",
            f"the {label} following {dates_mod.render_date(reference)}",
        ),
    )


def _band(value: date, text: str) -> FuzzyDate:
    """The candidate as a date, with a band where the words are vague.

    "On Christmas Day" is a day. "Around Christmas" is not, and offering it as
    one would be the timeline faking precision that the whole date design
    exists to prevent.
    """
    if re.search(r"\baround\b|\babout\b|\bnear\b|\bsomewhere\b|\bish\b", text):
        return FuzzyDate(value, "day", _AROUND_DAYS)
    return FuzzyDate(value)


#: Which timestamp a reference date came from, named so the user can weigh it.
#: The four never mean the same thing, and a suggestion computed from ingest
#: time deserves less trust than one computed from the date on the document.
REFERENCE_LABELS = {
    "artifact_ts": "the date on the document",
    "captured_ts": "when this was recorded",
    "ingested_ts": "when this was added to the record",
}


def describe_reference(source: str | None) -> str:
    return REFERENCE_LABELS.get(source or "", "an unknown date")
