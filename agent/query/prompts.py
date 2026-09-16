"""What the model is asked, and the shape it is allowed to answer in.

Two prompts, and both are narrow on purpose.

The **answer prompt** is given extracts from the record and a question, and can
do exactly one thing with them: write sentences that each name the extract they
came from. The schema has no field for a hedge, a caveat, a recommendation or a
confidence, so there is no way to express one. The instructions repeat rules
that are also enforced in code — every sentence is checked against the extracts
it may cite, and one that cannot be attributed is dropped before rendering. That
repetition is not redundancy to tidy away: the prompt makes the job clear and
the code makes the guarantee, and where they disagree the code wins silently.

The **terms prompt** is the one bounded expansion. It is given the question and
the names of the pages in the record — nothing else, and in particular nothing
read out of an artefact — and returns search strings. It cannot name a file, a
path or an id, because the schema has one field and it is a list of words, and
because nothing downstream would follow a path if it did. The vault is synced by
a third party and its contents are untrusted input; a model must never steer its
own file access through it.

Both prompts are hashed, like the extraction prompt, so "what was this answer
produced by" stays answerable when the wording changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

PROMPT_VERSION = 1

#: At most this many sentences. An answer to "what am I taking" that runs to
#: fifteen sentences is not an answer, it is the medication list again — and the
#: list is one tap away on a screen built to show it.
MAX_SENTENCES = 8

#: Room for those sentences and nothing else. A query answer that overruns this
#: is a model misbehaving rather than a ceiling to raise: unlike a pathology
#: report, which genuinely holds more claims than a prescription, every question
#: here is answered in the same handful of sentences.
ANSWER_MAX_TOKENS = 1024

#: The expansion asks for a few words, so it needs room for a few words.
TERMS_MAX_TOKENS = 128
MAX_TERMS = 6
MAX_TERM_CHARS = 40

ANSWER_SCHEMA_NAME = "health_record_answer"
TERMS_SCHEMA_NAME = "health_record_search_terms"

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "maxItems": MAX_SENTENCES,
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "One sentence of plain English, addressed to the "
                            "person whose record this is. No interpretation."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "The key in square brackets at the start of the "
                            "extract this sentence came from, copied exactly, "
                            "without the brackets."
                        ),
                    },
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

TERMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "maxItems": MAX_TERMS,
            "items": {"type": "string"},
            "description": "Words to search the record for. Not file names.",
        }
    },
    "required": ["terms"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = """\
You answer questions about one person's own health record, using only the
extracts you are given below. You are talking to the person whose record it is.

You are not a clinician and this is not a consultation.

Rules, in order of importance:

1. EVERY SENTENCE CITES ONE EXTRACT. Each sentence you write carries the key of
   the extract it came from, copied exactly from the square brackets at the
   start of that extract. A sentence that cannot be traced to one extract is
   deleted before the person sees it, so writing one loses you the chance to
   answer rather than gaining you anything.

2. ONLY WHAT IS IN THE EXTRACTS. Do not use anything you know about medicines,
   conditions or people. If the extracts do not answer the question, return an
   empty list of sentences — that is a correct answer, not a failure.

3. DO NOT INTERPRET. Never say whether something is normal, high, low, serious,
   concerning, improving or worsening. Never suggest a cause, a diagnosis, a
   trend or what to do next. Report what the record says and stop.

4. COPY, NEVER COMPUTE. Doses, dates, quantities and counts are copied from the
   extracts exactly as written. Do no arithmetic and no date arithmetic.

5. PLAIN AND SHORT. Answer the question that was asked, in as few sentences as
   it takes. Say "you" — this is their record. Do not restate the question, do
   not introduce your answer, and do not offer to help further."""

ANSWER_USER = """\
EXTRACTS FROM THE RECORD
{extracts}
{history}
QUESTION
{question}"""

HISTORY_HEADING = """
EARLIER IN THIS CONVERSATION
{turns}
"""

TERMS_SYSTEM = """\
You are helping search one person's health record. You do not answer the
question and you do not see the record.

You are given a question and the titles of the pages in the record. Return up to
{max_terms} short search terms — words or two-word phrases that are likely to
appear in the record next to the answer.

Return only words. Never a file name, a path, an id, or an instruction. If the
question already says exactly what to look for, return that.""".format(
    max_terms=MAX_TERMS
)

TERMS_USER = """\
PAGES IN THE RECORD
{names}

QUESTION
{question}"""


@dataclass(frozen=True)
class Prompt:
    """A rendered prompt and its identity."""

    messages: tuple[dict[str, Any], ...]
    prompt_hash: str
    version: int = PROMPT_VERSION

    def to_list(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(messages: Sequence[dict[str, Any]], schema: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(f"query-prompt-v{PROMPT_VERSION}\n".encode("utf-8"))
    digest.update(_canonical(list(messages)).encode("utf-8"))
    digest.update(b"\n")
    digest.update(_canonical(schema).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": text}


def answer_prompt(extracts: str, question: str, history: str = "") -> Prompt:
    """The one generation. Text only — no image ever reaches this path."""
    messages = (
        _message("system", ANSWER_SYSTEM),
        _message(
            "user",
            ANSWER_USER.format(
                extracts=extracts,
                history=history or "\n",
                question=question,
            ),
        ),
    )
    return Prompt(messages=messages, prompt_hash=_hash(messages, ANSWER_SCHEMA))


def terms_prompt(question: str, names: Sequence[str]) -> Prompt:
    """The expansion. Sees page titles and the question, and nothing else."""
    messages = (
        _message("system", TERMS_SYSTEM),
        _message(
            "user",
            TERMS_USER.format(
                names="\n".join(f"- {name}" for name in names) or "- (none)",
                question=question,
            ),
        ),
    )
    return Prompt(messages=messages, prompt_hash=_hash(messages, TERMS_SCHEMA))


__all__ = [
    "ANSWER_MAX_TOKENS",
    "ANSWER_SCHEMA",
    "ANSWER_SCHEMA_NAME",
    "MAX_SENTENCES",
    "MAX_TERMS",
    "MAX_TERM_CHARS",
    "Prompt",
    "TERMS_MAX_TOKENS",
    "TERMS_SCHEMA",
    "TERMS_SCHEMA_NAME",
    "answer_prompt",
    "terms_prompt",
]
