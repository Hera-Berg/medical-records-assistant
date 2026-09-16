"""Folding words so two spellings of one thing match.

Retrieval matches what a person typed against what their record holds, and the
two are written by different hands: a pharmacy label says ``PERINDOPRIL
ARGININE``, the wiki page says ``Perindopril``, and the question says ``the
perindopril``. Everything here exists to make those meet without a model call.

The rule the rest of the codebase already follows applies unchanged: **render
the literal, compare the normalised.** Nothing in this module ever produces text
that reaches the screen. It produces keys for comparison, and the thing that is
shown is always the source's own wording.

Folding is explicit rather than ``unicodedata``-based, for the same reason
:mod:`agent.projection.subjects` gives: a normalisation-and-strip silently
deletes any script it cannot fold, so a question typed in Greek would become an
empty string and match everything rather than nothing.
"""

from __future__ import annotations

import re

#: Accented Latin folded to ASCII. Same table as the subject slugifier, kept
#: here rather than imported from it: that module is path safety and this one is
#: matching, and the two would be edited for different reasons.
_FOLD = str.maketrans(
    {
        "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a", "å": "a", "ā": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e", "ē": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i", "ī": "i",
        "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o", "ø": "o", "ō": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u", "ū": "u",
        "ç": "c", "ñ": "n", "ß": "ss", "æ": "ae", "œ": "oe",
        "'": "", "’": "", "ʼ": "",
    }
)

_NON_WORD = re.compile(r"[^a-z0-9]+")

#: Words that carry no retrieval signal. Kept short on purpose: a long stop list
#: starts removing words that matter in a medical record ("off", "on", "up"),
#: and the cost of leaving a word in is one term that matches nothing.
STOPWORDS = frozenset(
    """
    a an and any are as at be been by can did do does for from had has have how
    i im in is it its me my of on or our so than that the their them then there
    these they this those to was were what whats when where which who whom why
    will with you your
    """.split()
)


def fold(value: object) -> str:
    """Lowercase, de-accented, punctuation-free, single-spaced.

    ``"Dr. Nguyễn's letter"`` and ``"dr nguyen s letter"`` fold to the same
    thing. Returns an empty string for anything that is not a usable string,
    rather than raising: this is fed straight from an HTTP body.
    """
    if not isinstance(value, str):
        return ""
    lowered = value.strip().lower().translate(_FOLD)
    return _NON_WORD.sub(" ", lowered).strip()


def words(value: object) -> tuple[str, ...]:
    """The folded words of *value*, in order, including stopwords."""
    folded = fold(value)
    return tuple(folded.split()) if folded else ()


def content_words(value: object) -> tuple[str, ...]:
    """The folded words worth searching on."""
    return tuple(word for word in words(value) if word not in STOPWORDS)


def contains_phrase(haystack: str, needle: str) -> bool:
    """Whether *needle* occurs in *haystack* as whole words.

    Word-bounded rather than a bare substring test, because a bare one makes
    ``"ras"`` match ``"rash"`` and — the case that matters — makes a short
    rejected value match text that has nothing to do with it.
    """
    folded_needle = fold(needle)
    if not folded_needle:
        return False
    folded_hay = fold(haystack)
    if not folded_hay:
        return False
    return f" {folded_needle} " in f" {folded_hay} "


def phrase_key(value: object) -> str:
    """A folded key with spaces removed, for matching a name against a slug.

    ``"dr nguyen"`` and the slug ``dr-nguyen`` both become ``drnguyen``.
    """
    return fold(value).replace(" ", "")


__all__ = [
    "STOPWORDS",
    "contains_phrase",
    "content_words",
    "fold",
    "phrase_key",
    "words",
]
