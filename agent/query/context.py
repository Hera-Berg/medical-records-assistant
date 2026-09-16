"""Fitting the retrieved record into a prompt, under a cap that is not advisory.

``MODELS.md``: "Cap the prompt at 8–16K and retrieve narrowly: the artefact, the
2–3 wiki entities it plausibly touches, nothing else. Never dump the whole wiki
into a prompt. If a prompt exceeds the cap, that is a retrieval bug, not a reason
to raise the cap."

So the cap is enforced here and it raises. Trimming happens first and it drops
**whole passages**, never half of one: half a passage is a dose with no unit or
a date with no year, and a model reading it will complete it plausibly. The
order things yield in is the design:

1. **Conversation history goes first.** The user's rule, and it is the right
   one: the current question's evidence outranks the conversation's memory. A
   follow-up that loses its history still answers from the record; a follow-up
   that loses the record answers from nothing.
2. **Then the worst-ranked passages**, which are the ones retrieved by the
   loosest facet — a literal word match rather than a named entity.

If it is still over after all of that, something upstream retrieved without a
bound and the answer is to fix that, not to send it anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..errors import HealthAgentError
from .classify import MAX_PRIOR_TURNS, Turn
from .prompts import HISTORY_HEADING
from .retrieve import Passage, Retrieval

#: The cap, in tokens, from ``MODELS.md``'s 8–16K band. Deliberately nearer the
#: bottom of it: small models degrade on long context regardless of the window,
#: and an answer assembled from sixty extracts is not better than one assembled
#: from twenty.
PROMPT_TOKEN_CAP = 12_000

#: Characters per token, assumed low so the estimate errs towards *over*-counting
#: and the real prompt is smaller than the cap rather than larger. Counting real
#: tokens would mean shipping a tokeniser for a model the client is deliberately
#: ignorant of.
CHARS_PER_TOKEN = 3

MAX_PROMPT_CHARS = PROMPT_TOKEN_CAP * CHARS_PER_TOKEN

#: Everything outside the extracts — the system prompt, the question, the
#: headings. Reserved so the extracts cannot fill the cap on their own.
RESERVED_CHARS = 4_000


class ContextTooLarge(HealthAgentError):
    """Retrieval produced more than a prompt can hold. A bug, not a limit."""


@dataclass(frozen=True)
class Context:
    """The prompt text, and what the answer is allowed to cite."""

    extracts: str
    history: str
    passages: tuple[Passage, ...]
    dropped: int = 0
    dropped_history: int = 0

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(passage.key for passage in self.passages)

    @property
    def is_empty(self) -> bool:
        return not self.passages

    def char_count(self) -> int:
        return len(self.extracts) + len(self.history)


def render_history(history: Sequence[Turn]) -> str:
    """The last turns, as they were actually shown.

    Only answers that survived validation are carried forward — a sentence that
    was dropped for lacking a citation was never shown to the person and must
    not reach the model as though it had been said.
    """
    turns = [turn for turn in list(history)[-MAX_PRIOR_TURNS:] if turn.question.strip()]
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        lines.append(f"Q: {turn.question.strip()}")
        answer = " ".join(turn.answer.split())
        lines.append(f"A: {answer}" if answer else "A: (nothing in the record covered it)")
    return HISTORY_HEADING.format(turns="\n".join(lines))


def build(retrieval: Retrieval, history: Sequence[Turn] = ()) -> Context:
    """Assemble the prompt body for one question, under the cap."""
    passages = list(retrieval.passages)
    rendered_history = render_history(history)
    dropped_history = 0
    dropped = 0

    budget = MAX_PROMPT_CHARS - RESERVED_CHARS

    def size(items: Sequence[Passage], history_text: str) -> int:
        return sum(len(item.line) + 1 for item in items) + len(history_text)

    if size(passages, rendered_history) > budget and rendered_history:
        # History yields first, whole. Half a conversation is worse than none:
        # a question answered against the wrong earlier turn is answered wrongly
        # and confidently.
        dropped_history = len([t for t in list(history)[-MAX_PRIOR_TURNS:] if t.question.strip()])
        rendered_history = ""

    while passages and size(passages, rendered_history) > budget:
        passages.pop()
        dropped += 1

    if size(passages, rendered_history) > budget:
        raise ContextTooLarge(
            f"the retrieved record does not fit in a {PROMPT_TOKEN_CAP}-token "
            f"prompt even with nothing left to drop. That is a retrieval bug — "
            f"something selected without a bound — and the fix is upstream in "
            f"agent/query/retrieve.py, never a larger cap."
        )

    extracts = "\n".join(passage.line for passage in passages) or "(nothing)"
    return Context(
        extracts=extracts,
        history=rendered_history,
        passages=tuple(passages),
        dropped=dropped,
        dropped_history=dropped_history,
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "Context",
    "ContextTooLarge",
    "MAX_PROMPT_CHARS",
    "PROMPT_TOKEN_CAP",
    "build",
    "render_history",
]
