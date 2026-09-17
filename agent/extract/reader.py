"""Reading one page, or one transcript, in groups — the one implementation.

Extraction and the eval harness both call :func:`read`. An eval that asked its
own question in its own way would score something extraction never does, which
is how the harness came to report a recording as failed that extraction would
have read on a second call.

**One conversation per page.** The page is attached once, with the question
about medications. Allergies and then problems are asked as later turns of the
same conversation, with the model's earlier answers in it. That shape is not a
style choice: measured on the bundled reader, a later turn reuses the page from
the server's prompt cache and costs about three seconds, where the same question
as a separate prompt paid the whole image prefill again — about eighty. It is a
fixed sequence of questions, not a loop: nothing the model says decides what is
asked next, except that a page it says it cannot read is not asked about again.

**Every turn is recorded.** Its raw answer verbatim, how it finished, the
ceiling it answered under and what it cost. A page whose medications turn was
cut off is not partly read: nothing is proposed from any turn of it, exactly as
one cut-off page leaves a whole artefact unread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import InferenceError, OutputTruncated, ReadTimedOut
from ..llm.client import Completion, parse_completion
from . import budget as budget_mod
from . import families
from .prompts import Prompt
from .schema import FAMILIES, MEDICATIONS, family_schema, schema_name
from .validate import Extraction, merge


@dataclass(frozen=True)
class Turn:
    """One question asked about a page, and exactly what came back."""

    family: str
    completion: Completion
    notes: tuple[str, ...] = ()
    page: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "family": self.family,
            # Verbatim. A model swap is diffed against this, and a parse is a
            # lossy view of it.
            "raw_output": self.completion.content,
            "finish_reason": self.completion.finish_reason,
            "truncated": self.completion.truncated,
            "max_tokens": self.completion.max_tokens,
            "usage": self.completion.usage,
            "latency_s": round(self.completion.latency_s, 3),
            "stripped_reasoning": self.completion.stripped_reasoning,
        }


@dataclass(frozen=True)
class Read:
    """What reading one page or transcript produced."""

    extraction: Extraction
    turns: tuple[Turn, ...]
    notes: tuple[str, ...] = ()

    @property
    def last(self) -> Completion:
        return self.turns[-1].completion


def _refused(
    family: str, exc: Exception, where: str | None, completion: Completion | None, ctx: int
) -> Extraction:
    prefix = f"{where}: " if where else ""
    if isinstance(exc, ReadTimedOut):
        return Extraction(
            readable=False,
            refused=True,
            truncated=True,
            unreadable_reason=(
                f"{prefix}the reader on this computer was still writing its answer "
                f"about {family} when the time allowed for it ran out. "
                + budget_mod.describe_run_on()
            ),
        )
    if isinstance(exc, OutputTruncated):
        if budget_mod.is_run_on(completion.content):
            return Extraction(
                readable=False,
                refused=True,
                truncated=True,
                unreadable_reason=f"{prefix}{budget_mod.describe_run_on()}",
            )
        # Separated from every other refusal on purpose. The answer is not
        # wrong, it is unfinished, and the fix is room rather than a server
        # setting — the generic message would send someone to read about guided
        # decoding while the token cap sat there unexamined.
        return Extraction(
            readable=False,
            refused=True,
            truncated=True,
            unreadable_reason=(
                prefix
                + str(exc)
                + " "
                + budget_mod.describe_exhausted(
                    completion.max_tokens or budget_mod.START, ctx, completion.prompt_tokens
                )
            ),
        )
    # An answer that is not JSON is a refusal, not a page the model could not
    # read: thinking narration landing in `content` is a server setting to
    # change, and calling it an unreadable photograph sends the user elsewhere.
    return Extraction(readable=False, refused=True, unreadable_reason=prefix + str(exc))


def read(
    client,
    prompt: Prompt,
    mime: str = "",
    locale: str = "en",
    page: int | None = None,
    where: str | None = None,
) -> Read:
    """Ask every group about one page, in one conversation, and merge the answers."""
    messages = prompt.to_list()
    follow_ups = dict(prompt.follow_ups)
    turns: list[Turn] = []
    parts: list[Extraction] = []
    notes: list[str] = []

    for family in FAMILIES:
        if family != MEDICATIONS:
            messages = messages + [
                {"role": "assistant", "content": turns[-1].completion.content},
                {"role": "user", "content": follow_ups[family]},
            ]
        try:
            completion, raised = budget_mod.ask(
                client, messages, family_schema(family, transcript=prompt.transcript),
                schema_name(family),
                where=f"{where}, {family}" if where else family,
            )
        except ReadTimedOut as exc:
            if (getattr(client, "runtime", None) or {}).get("kind") != "bundled":
                # A box across a network may have gone to sleep mid-answer: that
                # stays unreachable, retried later, as it always was.
                raise
            # The reader on this computer is alive and was still writing. That is
            # an answer that ran on, parked with that reason — not a sleeping box
            # to retry for ever.
            return Read(_refused(family, exc, where, None, client.settings.ctx), tuple(turns), tuple(notes))
        turns.append(Turn(family=family, completion=completion, notes=raised, page=page))
        notes.extend(raised)
        try:
            payload = parse_completion(completion)
        except (OutputTruncated, InferenceError) as exc:
            # Nothing from any turn of this page. A page cut off while listing
            # allergies has not been read, and filing its medications alone
            # would look exactly like a page with no allergies on it.
            return Read(
                _refused(family, exc, where, completion, client.settings.ctx),
                tuple(turns),
                tuple(notes),
            )
        part = families.read(
            family, payload, mime=mime, locale=locale, transcript=prompt.transcript
        )
        if part.refused:
            return Read(part, tuple(turns), tuple(notes))
        parts.append(part)
        if family == MEDICATIONS and not part.readable:
            # The reader says it cannot read the page. That is the answer; asking
            # it about allergies on a page it cannot read invites a guess.
            return Read(part, tuple(turns), tuple(notes))

    merged = merge(parts)
    return Read(merged, tuple(turns), tuple(notes))
