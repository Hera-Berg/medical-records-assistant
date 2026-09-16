"""The one bounded expansion, and why it cannot become a loop.

When deterministic retrieval finds nothing or almost nothing, the question was
probably asked in words the record does not use — "my water tablet", "the heart
letter". One round is allowed to bridge that: the model is shown the question
and the **titles of the pages in the record**, and returns search terms. Code
then retrieves against those terms exactly as it retrieves against the
question's own words.

Three properties, each structural rather than promised.

**It is one round because it is a function call.** There is no loop here, no
state machine and nothing that can ask again. If a future question seems to want
a second round, that is the shape classifier being too weak, and the fix is in
:mod:`agent.query.classify`.

**The model proposes strings, never file access.** It sees no artefact text, no
paths and no ids. What comes back is folded to words before it is used — a fold
that removes ``/``, ``\\`` and ``.`` outright — so a "term" that was written to
look like a path arrives as the words in it and is matched against the
projection like any other word. The vault is synced by a third party and its
contents are untrusted input; nothing a document says may steer what gets read
next.

**A failure here is not a failure of the question.** If the box is asleep or the
answer is unusable, expansion contributes nothing and the original retrieval
stands. It is an improvement to retrieval, not a step it depends on.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from ..errors import InferenceError
from . import prompts
from .text import fold
from .vocab import Vocabulary

log = logging.getLogger("agent.query")

#: How many passages count as "enough" that expansion is not attempted. Below
#: this the question probably used words the record does not.
THIN = 2

#: How many page titles the model is shown. Enough to recognise a drug by its
#: brand name; bounded so the prompt cannot grow with the record.
MAX_NAMES = 120


def should_expand(found: int) -> bool:
    return found < THIN


def clean_terms(raw: object) -> tuple[str, ...]:
    """Whatever the model returned, as at most a few plain search words.

    Everything that is not a word is removed rather than rejected: the point is
    not to catch a malicious term but to make the category impossible, so a
    proposed ``../../events/2026-09.laptop.jsonl`` becomes the words in it and
    matches nothing.
    """
    if not isinstance(raw, list):
        return ()
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        folded = fold(item[: prompts.MAX_TERM_CHARS])
        if not folded or len(folded) < 3:
            continue
        if folded in cleaned:
            continue
        cleaned.append(folded)
        if len(cleaned) >= prompts.MAX_TERMS:
            break
    return tuple(cleaned)


def propose_terms(client, question: str, vocabulary: Vocabulary) -> tuple[str, ...]:
    """Ask the box for search terms. Returns ``()`` if it cannot or will not."""
    prompt = prompts.terms_prompt(question, vocabulary.names_for_prompt(MAX_NAMES))
    try:
        completion = client.complete(
            prompt.to_list(),
            schema=prompts.TERMS_SCHEMA,
            schema_name=prompts.TERMS_SCHEMA_NAME,
            max_tokens=prompts.TERMS_MAX_TOKENS,
        )
    except InferenceError:
        # Deliberately swallowed, and only here. The expansion is an
        # improvement to retrieval; the question is still answered from what
        # deterministic retrieval found, and the generation that follows will
        # report the box's state on its own behalf if it is still down.
        log.info("the expansion call did not complete; answering from what was found")
        return ()
    if completion.truncated:
        return ()
    return clean_terms(_terms_of(completion.content))


def _terms_of(content: str) -> Any:
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get("terms")


def apply(
    client,
    question: str,
    vocabulary: Vocabulary,
    found: int,
) -> tuple[str, ...]:
    """The whole expansion decision, in one call that can happen once."""
    if client is None or not should_expand(found):
        return ()
    return propose_terms(client, question, vocabulary)


__all__ = ["MAX_NAMES", "THIN", "apply", "clean_terms", "propose_terms", "should_expand"]
