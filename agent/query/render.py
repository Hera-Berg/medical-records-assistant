"""Checking the model's answer, and every state the screen can end up in.

The rule this file exists for: **a sentence that cannot be attributed is
dropped, not shipped with a hedge.** Not warned about, not marked uncertain, not
rendered in grey — removed, before anyone reads it. A hedge on a medical record
is read as a fact with a caveat, and a caveat is the first thing that gets lost
when a page is skimmed, photographed or read aloud.

So validation is a filter and not a score. A sentence survives if its ``source``
is one of the keys that were actually put in front of the model, and it does not
survive otherwise — whether the key is invented, hallucinated from another
record, copied out of the question, or simply absent.

The states below are the whole vocabulary of this feature's outcomes, and two of
them exist because collapsing them would be a lie:

- **empty** is "your record does not cover this". It is the answer to a question
  about something that is not in the folder, and it never falls through to what
  the model happens to know.
- **unattributed** is "the model answered and none of it could be traced". The
  record may well cover the question; what failed is the answer. Showing the
  same sentence for both would tell somebody their record is silent about
  something it is not silent about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..projection.citations import Citation
from .classify import REFUSAL_NEXT, REFUSALS, Question
from .retrieve import Retrieval

# Outcomes.
ANSWERED = "answered"
EMPTY = "empty"
UNATTRIBUTED = "unattributed"
REFUSED = "refused"
OFFLINE = "offline"
CUT_OFF = "cut-off"
UNREADABLE = "unreadable"
NO_QUESTION = "no-question"
TOO_LONG = "too-long"

#: The sentence for each outcome that is not an answer. Fixed text, chosen by a
#: code, with nothing from the question or from the far end interpolated into
#: it — the same rule ``/api/health`` and the endpoint check follow.
MESSAGES: dict[str, str] = {
    EMPTY: "Nothing in your record covers that.",
    UNATTRIBUTED: (
        "Nothing came back that could be traced to one of your documents, so "
        "none of it is shown. These are the entries that match your question."
    ),
    CUT_OFF: (
        "The answer stopped before it was finished, so none of it is shown. "
        "These are the entries that match your question."
    ),
    UNREADABLE: (
        "The answer came back in a shape this app could not read, so none of it "
        "is shown. These are the entries that match your question."
    ),
    NO_QUESTION: "Ask a question about your record.",
    TOO_LONG: (
        "That is too long to be a question. Ask in a sentence — this is for "
        "asking about your record, not for adding things to it."
    ),
}

#: When the box cannot be reached, by why. Unreachable and unauthorised stay
#: apart here as everywhere else in this program: one drains by itself and the
#: other needs a person, and a single word for both is how a rotated key looks
#: like a sleeping laptop for a week.
BOX_MESSAGES: dict[str, str] = {
    "unreachable": (
        "The computer that writes answers is asleep or off your network, so "
        "there is no answer written out. Everything below is from your record "
        "and was found without it."
    ),
    "unauthorised": (
        "The computer that writes answers rejected this app's password, so "
        "there is no answer written out. That needs you to set a new one in "
        "Settings. Everything below is from your record and was found without "
        "it."
    ),
    "not-configured": (
        "No computer is set up to write answers yet, so there is none written "
        "out. Everything below is from your record and was found without one."
    ),
    "misconfigured": (
        "The computer that writes answers is not set up correctly, so there is "
        "no answer written out. Everything below is from your record and was "
        "found without it."
    ),
    "vision-not-working": (
        "The computer that writes answers is not working properly, so there is "
        "no answer written out. Everything below is from your record and was "
        "found without it."
    ),
    "unknown": (
        "The computer that writes answers could not be reached, so there is no "
        "answer written out. Everything below is from your record and was found "
        "without it."
    ),
    # Its own sentence, because it is its own thing: nobody was asked and
    # nothing failed. Reporting a deliberate choice as "no computer is set up"
    # sends someone to fix a setting that is already correct.
    "asked-not-to": (
        "Not contacting the computer that writes answers, because you asked not "
        "to. Everything below is from your record."
    ),
}


@dataclass(frozen=True)
class Sentence:
    """One sentence of an answer, and the document it rests on."""

    text: str
    key: str
    citation: Citation


@dataclass(frozen=True)
class Answer:
    """One question's outcome. Never written anywhere — see the package docstring."""

    question: str
    state: str
    message: str = ""
    sentences: tuple[Sentence, ...] = ()
    retrieval: Retrieval | None = None
    #: The refusal's own explanation, where the question was refused.
    refusal: str | None = None
    refusal_next: str = ""
    #: A count the question asked for, written by code. The model never
    #: produces a number that reaches the person.
    tally: str = ""
    #: How many sentences were dropped for citing something they were not shown.
    dropped: int = 0
    #: What the box was doing, where that is why there is no written answer.
    box: str | None = None
    #: The inference failure behind an ``offline`` answer, so the caller can
    #: record what the box did without this module knowing what a server is.
    #: Never rendered and never serialised — its text is the far end's, and the
    #: sentence on screen is chosen from the table above by a code.
    error: BaseException | None = field(default=None, repr=False, compare=False)
    #: Provenance for the terminal and the tests. Nothing here is persisted.
    model: str | None = None
    prompt_hash: str | None = None
    expanded_terms: tuple[str, ...] = ()

    @property
    def sources(self) -> tuple[Citation, ...]:
        return self.retrieval.sources() if self.retrieval is not None else ()

    @property
    def text(self) -> str:
        """The answer as one paragraph, for carrying into a follow-up."""
        return " ".join(sentence.text for sentence in self.sentences)


def read_sentences(content: str) -> list[dict[str, Any]] | None:
    """The model's answer as a list of sentence objects, or ``None``.

    Validate, never repair. A malformed answer is reported as one; it is not
    coerced, regex-scraped or partially recovered. The same rule the extraction
    path follows, for the same reason: a repaired answer is an answer nobody
    checked.
    """
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    sentences = parsed.get("sentences")
    if not isinstance(sentences, list):
        return None
    return [item for item in sentences if isinstance(item, dict)]


def validate(
    raw: Sequence[dict[str, Any]], retrieval: Retrieval
) -> tuple[tuple[Sentence, ...], int]:
    """Keep the sentences that cite something they were actually shown."""
    allowed = retrieval.keys
    kept: list[Sentence] = []
    dropped = 0
    for item in raw:
        text = item.get("text")
        key = item.get("source")
        if not isinstance(text, str) or not text.strip():
            dropped += 1
            continue
        if not isinstance(key, str):
            dropped += 1
            continue
        cleaned = key.strip().strip("[]").strip()
        if cleaned not in allowed:
            dropped += 1
            continue
        citation = retrieval.citation_for(cleaned)
        if citation is None:
            dropped += 1
            continue
        kept.append(Sentence(text=" ".join(text.split()), key=cleaned, citation=citation))
    return tuple(kept), dropped


def tally_for(question: Question | None, retrieval: Retrieval) -> str:
    """The count a "how many" question asked for, written in code.

    ``MODELS.md`` does not let the model produce a number that reaches the
    record, and a number that reaches the person asking is no different. So the
    counting is done here, over the passages retrieval actually matched, and the
    sentence is written here too.
    """
    if question is None or not question.counting or not retrieval.term_hits:
        return ""
    count = retrieval.term_hits
    if count == 1:
        return "1 entry in your record mentions what you asked about."
    return f"{count} entries in your record mention what you asked about."


def refusal(question: Question) -> Answer:
    code = question.refusal or ""
    return Answer(
        question=question.text,
        state=REFUSED,
        message=REFUSALS.get(code, REFUSALS["meaning"]),
        refusal=code,
        refusal_next=REFUSAL_NEXT,
    )


def empty(question: Question, retrieval: Retrieval) -> Answer:
    return Answer(
        question=question.text,
        state=EMPTY,
        message=MESSAGES[EMPTY],
        retrieval=retrieval,
    )


def offline(question: Question, retrieval: Retrieval, box: str) -> Answer:
    return Answer(
        question=question.text,
        state=OFFLINE,
        message=BOX_MESSAGES.get(box, BOX_MESSAGES["unknown"]),
        retrieval=retrieval,
        box=box,
        tally=tally_for(question, retrieval),
    )


def failed(question: Question, retrieval: Retrieval, state: str) -> Answer:
    return Answer(
        question=question.text,
        state=state,
        message=MESSAGES[state],
        retrieval=retrieval,
        tally=tally_for(question, retrieval),
    )


__all__ = [
    "ANSWERED",
    "Answer",
    "BOX_MESSAGES",
    "CUT_OFF",
    "EMPTY",
    "MESSAGES",
    "NO_QUESTION",
    "OFFLINE",
    "REFUSED",
    "Sentence",
    "TOO_LONG",
    "UNATTRIBUTED",
    "UNREADABLE",
    "empty",
    "failed",
    "offline",
    "read_sentences",
    "refusal",
    "tally_for",
    "validate",
]
