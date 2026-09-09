"""Checking the model's reading against a reader that cannot hallucinate.

``MODELS.md``: "Where both agree on a drug name, dose or value, the claim carries
``cross-verified``... **Where they disagree on anything high-consequence, do not
pick a winner** — emit the claim as ``conflicted`` with both readings attached
and force human review."

The asymmetry between the two readers is the whole design, and it decides the
shape of this module. The VLM reads far better; it is also the one that will
invent a plausible dose that was never on the page. Tesseract on a blurry
handwritten script is close to useless on its own, so it is a **check on
hallucination rather than a reader in its own right**.

That is why there are three outcomes and not two. Silence from the deterministic
path is not disagreement — it is the expected result on most photographs, and
treating it as a contradiction would put every claim in the review queue and kill
the inbox. So:

``cross-verified``
    The deterministic text contains the normalised value. Two independent readers
    agree; the claim follows normal tier rules.

``uncorroborated``
    The deterministic path found nothing to compare, or is not installed. The
    default, and not a problem.

``contradicted``
    The deterministic path found the subject *and* a different value of the same
    unit family. Two readers disagreeing about a dose is exactly the signal the
    architecture exists to surface — so both readings are attached and nothing
    picks between them. Averaging it away, or trusting the more fluent one,
    defeats the point.

Comparison is on the **normalised** key and rendering is always the literal, per
the rule the projection already follows: ``5mg`` and ``5.0 mg`` are the same
reading and the record still prints what the page said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..projection import values
from .text import DeterministicRead
from .validate import ReadClaim

CROSS_VERIFIED = "cross-verified"
UNCORROBORATED = "uncorroborated"
CONTRADICTED = "contradicted"

#: How close to the subject's name a rival value has to appear to count as being
#: about the same thing. Deliberately small: a dose three lines down belongs to a
#: different drug, and treating it as a contradiction would manufacture conflicts
#: on any script listing more than one medication.
_WINDOW = 90


def _normalise(text: str) -> str:
    """Lowered, whitespace-collapsed, and with the spacing inside doses removed.

    ``5 mg`` and ``5mg`` are the same reading. OCR inserts and drops spaces
    almost at random, so comparing with them intact would report disagreement
    between two readers who read the same thing.
    """
    lowered = values.normalise_text(text)
    return re.sub(r"(?<=\d)\s+(?=[a-z])", "", lowered)


@dataclass(frozen=True)
class Verification:
    """What the deterministic reader had to say about one claim."""

    state: str
    #: The rival reading, when there is one. The literal span from the
    #: deterministic path, never a normalised form: it is evidence a person will
    #: read, and it has to be what that reader actually produced.
    rival: str | None = None
    reason: str | None = None

    @property
    def is_contradicted(self) -> bool:
        return self.state == CONTRADICTED

    def describe(self) -> dict[str, object]:
        return {"state": self.state, "rival": self.rival, "reason": self.reason}


def _subject_window(haystack: str, name: str) -> str | None:
    """The text around the first mention of *name*, or ``None`` if absent."""
    where = haystack.find(name)
    if where < 0:
        return None
    return haystack[max(0, where - 10) : where + len(name) + _WINDOW]


def verify(claim: ReadClaim, read: DeterministicRead) -> Verification:
    """Compare one claim against the deterministic reading of the same artefact."""
    if not read.has_text:
        return Verification(
            UNCORROBORATED,
            reason=read.unavailable
            or "the deterministic reader found no text to compare against",
        )

    haystack = _normalise(read.text)
    value = _normalise(claim.value_literal)

    if value and value in haystack:
        return Verification(CROSS_VERIFIED)

    # The value as a whole did not appear. Before calling anything a
    # contradiction, the deterministic reader has to have found the subject —
    # otherwise it simply did not read this part of the page.
    subject_name = _normalise(claim.subject.split(":", 1)[1].replace("-", " "))
    window = _subject_window(haystack, subject_name)
    if window is None:
        return Verification(
            UNCORROBORATED,
            reason=(
                f"the {read.method} reading does not mention {subject_name!r}, so "
                f"there was nothing to check this against"
            ),
        )

    claimed = values.amounts(claim.value_literal)
    if not claimed:
        return Verification(
            UNCORROBORATED,
            reason="this value carries no amount and unit to compare",
        )
    found = values.amounts(window)
    rivals = {
        (amount, unit)
        for amount, unit in found
        if unit in {unit for _, unit in claimed} and (amount, unit) not in claimed
    }
    if not rivals:
        return Verification(
            UNCORROBORATED,
            reason=(
                f"the {read.method} reading mentions {subject_name!r} but states no "
                f"competing value"
            ),
        )

    amount, unit = sorted(rivals)[0]
    return Verification(
        CONTRADICTED,
        rival=f"{amount}{unit}",
        reason=(
            f"the model read {claim.value_literal!r}; the {read.method} reading of "
            f"the same page says {amount}{unit}. Two independent readers disagree, "
            f"so neither is chosen"
        ),
    )


def verify_all(
    claims: Sequence[ReadClaim], read: DeterministicRead
) -> dict[int, Verification]:
    """Index by position in *claims*, so a caller can pair them up."""
    return {index: verify(claim, read) for index, claim in enumerate(claims)}
