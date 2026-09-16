"""Asking the record about itself.

Phase 10, and deferred to it deliberately: this depends on the wiki, the index
and the citations all being solid, and building it earlier produces a system
that sounds impressive and cites nothing.

The whole pipeline, in the order it runs, with what is and is not allowed at
each step:

1. **Classify, in code.** A question asking what something *means* is refused
   here, before anything is read — cheap, and legible as a table of phrasings
   rather than a judgement made inside a prompt. Everything else is parsed into
   facets: entities, kinds, people, predicates, a date window, kinds of
   document, literal terms.
2. **Retrieve, in code**, from the projection only, under fixed caps.
3. **One bounded expansion**, and only if step 2 found almost nothing: the model
   proposes search *terms* and code retrieves against them. Never file paths,
   never which files to open. Exactly one round, because it is a function call
   and not a loop.
4. **One generation**, grammar-constrained, with the retrieved extracts.
5. **Validate**: every sentence must cite an extract it was actually shown, or
   it is dropped before rendering.

Four rules hold across all of it, and each is enforced by code here rather than
requested in a prompt:

**Empty retrieval never reaches the answering model.** It returns "nothing in
your record covers that" — it does not fall through to what a model happens to
know about medicine.

**Queries are not events.** Nothing in this package appends, writes or mutates
anything. It takes a projection and returns a value. There is no writer to pass
it and no path to one.

**Answers are ephemeral.** Nothing here is written to ``wiki/``, to ``exports/``
or to the log. The record holds what artefacts support, not what a model once
said about them — and there is deliberately no local record of the questions
either: that file would accumulate what the patient asked, inside a folder that
syncs to a third party, and questions disclose more than the record does.

**Rejected content appears in no answer.** Retrieval reads the projection, which
has already suppressed it, and withholds any remaining free text carrying a
rejected reading's own wording. See :mod:`agent.query.retrieve`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..errors import (
    AuthRejected,
    CredentialError,
    EndpointNotConfigured,
    EndpointNotPrivate,
    EndpointUnreachable,
    InferenceError,
    ModelIdentityMismatch,
    RateLimited,
)
from ..events.envelope import Event
from ..projection import Projection, project
from ..projection import citations as citations_mod
from ..projection.citations import Citer
from . import classify as classify_mod
from . import context as context_mod
from . import expand, prompts, render, retrieve
# Names, not the `classify` function: binding that here would shadow the
# submodule of the same name on this package, so `agent.query.classify`
# would resolve to a function for anything importing it from outside.
from .classify import MAX_PRIOR_TURNS, MAX_QUESTION_CHARS, Question, Turn
from .render import Answer, Sentence
from .retrieve import Retrieval
from .vocab import Vocabulary

#: Which box state each failure means, for the sentence the screen shows. The
#: mapping is here rather than in the client because it is a *user-facing*
#: distinction: unreachable drains by itself, unauthorised needs a person.
_BOX_FOR: tuple[tuple[type, str], ...] = (
    (AuthRejected, "unauthorised"),
    (EndpointUnreachable, "unreachable"),
    (RateLimited, "unreachable"),
    (EndpointNotConfigured, "not-configured"),
    (EndpointNotPrivate, "misconfigured"),
    (CredentialError, "misconfigured"),
    (ModelIdentityMismatch, "misconfigured"),
)


@dataclass(frozen=True)
class Record:
    """One read of the record, and everything a question needs from it.

    Deliberately not the server's ``Snapshot``: the CLI answers questions with
    no server running, and a query has no business knowing about a job queue, an
    endpoint state or a lock. The server builds one of these from its snapshot
    in a line.
    """

    projection: Projection
    citer: Citer
    as_of: datetime

    @classmethod
    def of(cls, events: Iterable[Event], as_of: datetime | str | None = None) -> Record:
        collected = list(events)
        projected = project(collected, as_of)
        artifacts = citations_mod.index_artifacts(collected)
        return cls(
            projection=projected,
            citer=Citer(artifacts, collected),
            as_of=projected.as_of,
        )


def box_for(exc: BaseException) -> str:
    for kind, word in _BOX_FOR:
        if isinstance(exc, kind):
            return word
    return "misconfigured"


def _history_with_facets(
    history: Sequence[Turn], vocabulary: Vocabulary, today
) -> tuple[Turn, ...]:
    """Recover each earlier question's facets by parsing it again.

    The browser sends the words of the conversation and nothing else — it has no
    business holding the record's parse of them, and a client that could send
    facets could send facets that were never derived from a question. Parsing
    again is deterministic and costs nothing.
    """
    recovered: list[Turn] = []
    for turn in list(history)[-MAX_PRIOR_TURNS:]:
        if turn.facets is not None:
            recovered.append(turn)
            continue
        parsed = classify_mod.classify(turn.question, vocabulary, today)
        recovered.append(
            Turn(question=turn.question, answer=turn.answer, facets=parsed)
        )
    return tuple(recovered)


def ask(
    question: str,
    record: Record,
    client=None,
    history: Sequence[Turn] = (),
    box: str | None = None,
) -> Answer:
    """Answer one question about *record*. Writes nothing, appends nothing.

    *client* is an inference client or ``None``. ``None`` is an ordinary state,
    not an error: retrieval is deterministic and runs with the box asleep, so
    the answer becomes "here is what your record holds about this, found without
    it" — which is most of the value and all of the citations.
    """
    text = (question or "").strip()
    if not text:
        return Answer(question="", state=render.NO_QUESTION, message=render.MESSAGES[render.NO_QUESTION])
    if len(text) > MAX_QUESTION_CHARS:
        return Answer(question=text[:MAX_QUESTION_CHARS], state=render.TOO_LONG,
                      message=render.MESSAGES[render.TOO_LONG])

    vocabulary = Vocabulary.of(record.projection.entities)
    today = record.as_of.date()
    turns = _history_with_facets(history, vocabulary, today)

    parsed = classify_mod.classify(text, vocabulary, today, turns)
    if parsed.is_refused:
        return render.refusal(parsed)

    found = retrieve.retrieve(parsed, record.projection, record.citer)

    # The one expansion. It runs before the emptiness check, because finding
    # nothing is the case it exists for — and it still cannot answer anything:
    # it returns words, and this line is the only place they are used.
    terms = expand.apply(client, text, vocabulary, len(found.passages))
    if terms:
        found = retrieve.retrieve(
            parsed, record.projection, record.citer, extra_terms=terms
        )

    if found.is_empty:
        # Never the model's general knowledge, whatever the box is doing.
        return render.empty(parsed, found)

    if client is None:
        return render.offline(parsed, found, box or "unknown")

    return _generate(parsed, found, record, client, turns)


def _generate(
    parsed: Question,
    found: Retrieval,
    record: Record,
    client,
    history: Sequence[Turn],
) -> Answer:
    """The single generation, and the validation that decides what survives it."""
    built = context_mod.build(found, history)
    prompt = prompts.answer_prompt(built.extracts, parsed.text, built.history)

    try:
        completion = client.complete(
            prompt.to_list(),
            schema=prompts.ANSWER_SCHEMA,
            schema_name=prompts.ANSWER_SCHEMA_NAME,
            max_tokens=prompts.ANSWER_MAX_TOKENS,
        )
    except InferenceError as exc:
        answer = render.offline(parsed, found, box_for(exc))
        return _with(answer, error=exc, expanded_terms=found.expanded_terms)

    if completion.truncated:
        # Its own state, never "the answer was malformed". They arrive looking
        # identical and want opposite investigations.
        return _with(
            render.failed(parsed, found, render.CUT_OFF),
            model=completion.model,
            prompt_hash=prompt.prompt_hash,
            expanded_terms=found.expanded_terms,
        )

    raw = render.read_sentences(completion.content)
    if raw is None:
        return _with(
            render.failed(parsed, found, render.UNREADABLE),
            model=completion.model,
            prompt_hash=prompt.prompt_hash,
            expanded_terms=found.expanded_terms,
        )

    sentences, dropped = render.validate(raw, found)
    if not sentences:
        return _with(
            render.failed(parsed, found, render.UNATTRIBUTED),
            model=completion.model,
            prompt_hash=prompt.prompt_hash,
            dropped=dropped,
            expanded_terms=found.expanded_terms,
        )

    return Answer(
        question=parsed.text,
        state=render.ANSWERED,
        sentences=sentences,
        retrieval=found,
        tally=render.tally_for(parsed, found),
        dropped=dropped,
        model=completion.model,
        prompt_hash=prompt.prompt_hash,
        expanded_terms=found.expanded_terms,
    )


def _with(answer: Answer, **changes) -> Answer:
    from dataclasses import replace

    return replace(answer, **changes)


__all__ = [
    "Answer",
    "MAX_PRIOR_TURNS",
    "MAX_QUESTION_CHARS",
    "Record",
    "Sentence",
    "Turn",
    "ask",
    "box_for",
]
