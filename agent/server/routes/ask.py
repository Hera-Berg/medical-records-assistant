"""``POST /api/ask`` — a question about the record, answered from the record.

The one route in this application that runs inference while somebody waits, and
that is deliberate. ``/api/capture`` must return before any model runs because
the person is in a waiting room and the work can be done later; a question is
the opposite case — the answer is the whole point of the request, and there is
nothing to queue it into because **answers are ephemeral**. A job queue here
would mean persisting answers, which is exactly what the spec forbids.

**POST, and not GET, for a read.** The question is content — "what did the
clinic say about my results" — and a GET would put it in the URL, where it lands
in browser history, in any proxy's log and in a screenshot of the address bar. A
read that carries sensitive text is a POST.

**This route writes nothing.** There is no ``state.append`` in this file and no
call to anything that appends. Queries are not events: asking changes nothing
and leaves nothing behind, including any record of having been asked. That last
part is a decision rather than an omission — a log of the patient's questions
would be the single most disclosing file in a folder that syncs to a third
party.

The only thing it records is what the box *failed* at, in the shared endpoint
state that ``/api/health`` reports — because a question that failed against a
sleeping machine is exactly as informative as a job that did, and the sidebar
should say so within five seconds either way. A success is not recorded; see the
comment where it would be.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from ... import query as query_mod
from ...errors import EndpointNotConfigured, HealthAgentError
from ...extract import session
from ...query import classify as classify_mod
from ...query.retrieve import Passage, Retrieval
from .. import endpoint_state, serialise
from ..deps import get_state
from ..state import RecordState

router = APIRouter()


@router.post("/api/ask")
def ask(
    body: dict[str, Any] = Body(default_factory=dict),
    state: RecordState = Depends(get_state),
) -> dict[str, Any]:
    """Answer one question. Appends nothing, writes nothing, keeps nothing."""
    snapshot = state.snapshot()
    record = query_mod.Record(
        projection=snapshot.projection, citer=snapshot.citer, as_of=snapshot.as_of
    )
    history = _history(body.get("history"))

    client = None
    box = state.endpoint.state
    try:
        client = session.open_client(state.vault)
    except EndpointNotConfigured:
        box = endpoint_state.NOT_CONFIGURED
        state.set_endpoint(endpoint_state.not_configured())
    except HealthAgentError as exc:
        # A public address, an unreadable credential file: configured wrongly
        # rather than asleep, and the sentence the answer carries says so.
        box = query_mod.box_for(exc)
        state.record_endpoint_error(exc)

    try:
        answer = query_mod.ask(
            str(body.get("question") or ""),
            record,
            client=client,
            history=history,
            box=box,
        )
    finally:
        if client is not None:
            client.close()

    if answer.error is not None:
        # What the box did is shared state: the sidebar and /api/health report
        # it, and a question is as good a probe as a job.
        state.record_endpoint_error(answer.error)
    # A *success* is deliberately not recorded. Answering a text question proves
    # the box is reachable and the key is good; it proves nothing about whether
    # it can read a picture, and "working" in this application means the startup
    # probe passed — vision included. Upgrading the state from here would put
    # "Ready to read new files" in the sidebar for a box that silently discards
    # every prescription photo, which is the exact failure `probe_vision` exists
    # to catch.

    return payload(answer, len(history), snapshot.built_ts)


def _history(raw: object) -> tuple[classify_mod.Turn, ...]:
    """The conversation so far, as the browser sent it.

    Only the words: the client sends questions and the answers it was shown, and
    the record's parse of them is recovered server-side. A client that could
    send facets could send facets no question ever produced.
    """
    if not isinstance(raw, list):
        return ()
    turns: list[classify_mod.Turn] = []
    for item in raw[-classify_mod.MAX_PRIOR_TURNS :]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        turns.append(
            classify_mod.Turn(
                question=question[: classify_mod.MAX_QUESTION_CHARS],
                answer=str(item.get("answer") or "")[: classify_mod.MAX_QUESTION_CHARS * 4],
            )
        )
    return tuple(turns)


def payload(answer, turns_used: int, built_ts: str) -> dict[str, Any]:
    """One answer as JSON.

    Every sentence carries its own citation, resolved exactly as the wiki
    resolves it, so the screen can offer the document behind each line without
    a second request and without deriving a link of its own.
    """
    retrieval = answer.retrieval
    return {
        "question": answer.question,
        "state": answer.state,
        "message": answer.message,
        "sentences": [
            {
                "text": sentence.text,
                "citation": serialise.citation_of(sentence.citation),
            }
            for sentence in answer.sentences
        ],
        "tally": answer.tally,
        # Something the record does not hold, said plainly. Written in code, like
        # the count: it is an assertion *about the record*, which the model is in
        # no position to make and has every incentive to paper over.
        "note": answer.note,
        # Why the question was refused, as a code as well as a sentence: the
        # screen groups its own copy by this, and an interface that had to match
        # on the sentence would break the moment the sentence was reworded.
        "refusal": answer.refusal,
        "refusal_next": answer.refusal_next,
        # What was found, each entry cited. Shown when there is no written
        # answer — the box asleep, or nothing that could be attributed — because
        # deterministic retrieval is most of the value and all of the citations.
        "found": [_passage(passage) for passage in _shown(retrieval)],
        "found_total": len(retrieval.passages) if retrieval is not None else 0,
        "box": answer.box,
        # How many more questions this conversation may carry before it starts
        # again. Sent rather than counted in the browser: the cap is the
        # server's, and a client counting to its own number would disagree with
        # it the moment either changed.
        "turns_left": max(0, classify_mod.MAX_TURNS - (turns_used + 1)),
        "as_of": built_ts,
    }


#: How many retrieved entries the screen is offered. The answer is what the
#: person reads; this is the evidence behind it, and a list of sixty is a screen
#: nobody reads at all.
SHOWN = 12


def _shown(retrieval: Retrieval | None) -> tuple[Passage, ...]:
    return retrieval.passages[:SHOWN] if retrieval is not None else ()


def _passage(passage: Passage) -> dict[str, Any]:
    """One retrieved entry, in the parts the screen renders separately.

    The tier travels as its own field rather than inside the text, because the
    screen says "Prescription" where the record says ``prescriber-issued`` — the
    words a patient uses, as everywhere else in this interface.
    """
    return {
        "title": passage.title,
        "text": passage.text,
        "tier": passage.tier,
        "corrected": passage.corrected,
        "when": passage.when,
        "subject_id": passage.subject_id,
        "citation": serialise.citation_of(passage.citation),
    }


__all__ = ["router", "payload"]
