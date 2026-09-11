"""Running out of room, and how that is told apart from answering badly.

Both arrive at the parser looking identical — JSON that will not load — and they
want opposite investigations. "The model ran out of room" is a ceiling to raise.
"The model produced invalid output" is the server's guided decoding to check.
The message that sends someone to read about grammar settings when a token cap
was the whole story costs an evening, so the distinction is asserted here at
every level it has to survive: the exception, the outcome, the queue state, and
the event payload a year later.

The other half of the same problem is the ceiling itself. A prescription yields
three claims and a pathology report yields four result tables, and one fixed
number cannot be right for both — so the ceiling is raised on the server's own
``finish_reason`` and never on a guess, and a page that overruns the top of the
ladder is reported rather than half-read.

The box is an ``httpx.MockTransport``. Nothing here touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.errors import OutputTruncated
from agent.extract import budget, jobs as jobs_mod, propose, runner
from agent.extract.runner import Extractor
from agent.llm import client as client_mod
from agent.llm import credentials, redaction

from .test_extraction_pipeline import (
    ENV,
    KEY,
    MODEL,
    _answer_body,
    _client,
    _ingest,
)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setenv(ENV, KEY)
    monkeypatch.setattr(credentials, "_from_keychain", lambda: None)
    redaction.forget_all()
    yield
    redaction.forget_all()


def _cut_off(content='{"claims": [{"subject_kind": "med", "subject_na', prompt_tokens=900):
    """One answer the box stopped part-way through."""
    return {
        "id": "chatcmpl-cut",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens},
    }


def _box(*, cuts_below=None, prompt_tokens=900, body=None):
    """A box that cuts every answer off below *cuts_below* tokens of room.

    ``cuts_below=None`` cuts every answer off however much room it is given,
    which is the page nothing can read in one pass.
    """
    seen: list[int] = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload["max_tokens"])
        if cuts_below is None or payload["max_tokens"] < cuts_below:
            return httpx.Response(200, json=_cut_off(prompt_tokens=prompt_tokens))
        return httpx.Response(200, json=body or _answer_body())

    handler.ceilings = seen
    return handler


# --- the distinction itself ------------------------------------------------


def test_truncated_output_is_not_reported_as_malformed():
    """The whole point: a token cap and invalid output are different problems."""
    completion = client_mod.Completion(
        content='{"claims": [{"subject_na',
        model=MODEL,
        raw={},
        latency_s=0.1,
        sampling={"max_tokens": 2048},
        finish_reason="length",
    )

    with pytest.raises(OutputTruncated) as raised:
        client_mod.parse_completion(completion)

    message = str(raised.value)
    assert "2048-token ceiling" in message
    assert "finish_reason 'length'" in message
    assert "token cap, not a grammar problem" in message
    # The message the *other* failure carries, which would send someone to the
    # server's schema settings while the ceiling sat there unexamined.
    assert "response_format json_schema" not in message


def test_the_message_still_says_the_output_is_kept_and_no_claim_made():
    """The part of the old message that was right, kept verbatim in substance."""
    completion = client_mod.Completion(
        content="{", model=MODEL, raw={}, latency_s=0.1,
        sampling={"max_tokens": 2048}, finish_reason="length",
    )

    with pytest.raises(OutputTruncated) as raised:
        client_mod.parse_completion(completion)

    message = str(raised.value)
    assert "raw output is recorded" in message
    assert "no claim is made from it" in message


def test_a_truncated_answer_is_refused_even_when_it_happens_to_parse():
    """A server can stop mid-array and leave something structurally valid.

    The claims it dropped are the ones nobody would notice were missing, so the
    ceiling is checked before the content is read at all.
    """
    completion = client_mod.Completion(
        content='{"readable": true, "claims": []}',
        model=MODEL,
        raw={},
        latency_s=0.1,
        sampling={"max_tokens": 2048},
        finish_reason="length",
    )

    with pytest.raises(OutputTruncated):
        client_mod.parse_completion(completion)


def test_an_answer_that_finished_is_parsed_normally():
    completion = client_mod.Completion(
        content='{"claims": []}', model=MODEL, raw={}, latency_s=0.1,
        sampling={"max_tokens": 2048}, finish_reason="stop",
    )

    assert client_mod.parse_completion(completion) == {"claims": []}


def test_a_server_that_reports_no_finish_reason_is_not_treated_as_truncated():
    """Plenty of OpenAI-compatible servers omit the field. That is not a fault."""
    completion = client_mod.Completion(
        content='{"claims": []}', model=MODEL, raw={}, latency_s=0.1,
        sampling={"max_tokens": 2048},
    )

    assert completion.truncated is False
    assert client_mod.parse_completion(completion) == {"claims": []}


# --- the ladder ------------------------------------------------------------


def test_the_ladder_doubles_and_stops_at_the_ceiling():
    assert budget.next_budget(2048, 16384, 900) == 4096
    assert budget.next_budget(4096, 16384, 900) == 8192
    assert budget.next_budget(budget.CEILING, 16384, 900) is None


def test_the_ladder_never_asks_for_more_than_the_context_window_holds():
    """``ctx`` is the real limit; the ladder is only a policy inside it."""
    assert budget.next_budget(2048, 4096, 900) == 4096 - budget.MARGIN - 900
    # No room at all left: the next step would be smaller than the current one.
    assert budget.next_budget(2048, 4096, 2500) is None


def test_with_no_usage_reported_the_ladder_still_advances():
    """A server that reports no usage costs one more truncated answer, not a stall."""
    assert budget.next_budget(2048, 16384, None) == 4096


# --- what the pipeline does with it ----------------------------------------


def test_a_page_that_overruns_is_asked_again_with_more_room(vault):
    short = _ingest(vault)
    handler = _box(cuts_below=4096)

    with _client(vault, handler) as client:
        outcome = Extractor(vault, client).run(short)

    assert handler.ceilings == [2048, 4096], "raised once, on the server's own word"
    assert outcome.reading == runner.READ_CLAIMS
    assert outcome.claims == 1
    assert any("asked again with room for 4096" in note for note in outcome.notes)


def test_an_ordinary_page_costs_exactly_one_call(vault):
    """The ladder advances on evidence. A page that fits never pays for it."""
    short = _ingest(vault)
    handler = _box(cuts_below=0)

    with _client(vault, handler) as client:
        Extractor(vault, client).run(short)

    assert handler.ceilings == [2048]


def test_the_ceiling_that_answered_is_what_the_event_records(vault):
    """Provenance: which request produced this reading, at what ceiling."""
    short = _ingest(vault)

    with _client(vault, _box(cuts_below=4096)) as client:
        outcome = Extractor(vault, client).run(short)

    completed = next(e for e in outcome.events if e.type == propose.EXTRACTION_COMPLETED)
    assert completed.payload["sampling"]["max_tokens"] == 4096
    assert completed.payload["truncated"] is False
    assert completed.payload["finish_reason"] is None


def test_a_page_nothing_can_hold_is_its_own_outcome_not_an_unreadable_one(vault):
    """Nothing is wrong with the artefact; telling someone to retake it is wrong."""
    short = _ingest(vault)
    handler = _box(cuts_below=None)

    with _client(vault, handler) as client:
        outcome = Extractor(vault, client).run(short)

    assert handler.ceilings == [2048, 4096, 8192], "walked the ladder, then stopped"
    assert outcome.reading == runner.READ_TRUNCATED
    assert outcome.state == jobs_mod.NEEDS_ATTENTION
    assert outcome.claims == 0
    assert "ran out of room" in outcome.describe()
    assert "could not read" not in outcome.describe()


def test_the_ceiling_being_reached_says_which_limit_and_what_makes_room(vault):
    short = _ingest(vault)

    with _client(vault, _box(cuts_below=None)) as client:
        outcome = Extractor(vault, client).run(short)

    assert "no more room to give" in outcome.reason
    assert "top of the ladder" in outcome.reason


def test_no_room_in_the_context_window_names_ctx_rather_than_the_ladder(vault):
    """A different limit, a different thing to change."""
    short = _ingest(vault)
    handler = _box(cuts_below=None, prompt_tokens=14000)

    with _client(vault, handler) as client:
        outcome = Extractor(vault, client).run(short)

    assert handler.ceilings == [2048], "no room to raise it into"
    assert "models.vlm.ctx" in outcome.reason
    assert "max_pixels" in outcome.reason


def test_a_truncated_read_records_the_raw_output_and_proposes_nothing(vault):
    """Nothing is thrown away; nothing is made a claim of."""
    short = _ingest(vault)

    with _client(vault, _box(cuts_below=None)) as client:
        outcome = Extractor(vault, client).run(short)

    kinds = [event.type for event in outcome.events]
    assert kinds.count(propose.EXTRACTION_COMPLETED) == 1
    assert propose.CLAIM_PROPOSED not in kinds
    completed = next(e for e in outcome.events if e.type == propose.EXTRACTION_COMPLETED)
    assert completed.payload["truncated"] is True
    assert completed.payload["finish_reason"] == "length"
    assert completed.payload["raw_output"].startswith("{")


def test_a_truncated_read_can_be_re_read_after_the_box_is_given_more_room(vault):
    """An answer the model never finished is not a settled answer.

    ``ctx`` and the image budget are both outside the idempotency key, so
    changing one genuinely changes the outcome — and the truncated read proposed
    no claims, so asking again duplicates nothing.
    """
    short = _ingest(vault)
    with _client(vault, _box(cuts_below=None)) as client:
        for event in Extractor(vault, client).run(short).events:
            vault.append(event)

    events = list(vault.read().events)
    assert not propose.completed_keys(events), "not recorded as work already done"

    with _client(vault, _box(cuts_below=0)) as client:
        second = Extractor(vault, client).run(short, events=events)

    assert second.claims == 1, "re-reading it actually re-reads it"


def test_a_finished_read_is_still_never_done_twice(vault):
    """The exception above must not have widened into a general re-read."""
    short = _ingest(vault)
    with _client(vault, _box(cuts_below=0)) as client:
        for event in Extractor(vault, client).run(short).events:
            vault.append(event)

    events = list(vault.read().events)
    with _client(vault, _box(cuts_below=0)) as client:
        second = Extractor(vault, client).run(short, events=events)

    assert second.reading == runner.READ_ALREADY
    assert second.claims == 0


def test_nothing_re_queues_a_truncated_artefact_on_its_own(vault):
    """Terminal, so a drain does not spend the box's evening on the same page."""
    short = _ingest(vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    queue.add(short)

    with _client(vault, _box(cuts_below=None)) as client:
        report = runner.drain(vault, Extractor(vault, client), queue)

    assert report.outcomes[0].state == jobs_mod.NEEDS_ATTENTION
    assert runner.enqueue_unread(vault, queue) == ()


def test_one_cut_off_page_does_not_let_the_rest_file_a_partial_document(vault):
    """A half-read discharge summary must not look like a whole one.

    The pages that fit would carry ordinary citations and ordinary frontmatter,
    and nothing downstream could tell that the results on page two were never
    read. So the artefact is reported, and none of it is proposed.
    """
    from agent.extract.validate import Extraction, merge

    whole = Extraction(readable=True, claims=(), rejections=(), notes=("page one read",))
    cut = Extraction(
        readable=False, refused=True, truncated=True, unreadable_reason="cut off"
    )

    merged = merge([whole, cut])

    assert merged.truncated is True
    assert merged.readable is False
    assert merged.claims == ()
    assert "page one read" in merged.notes


def test_the_page_that_was_cut_off_is_named_when_there_is_more_than_one(vault):
    """"Something overran" is not actionable; "page 2 overran" is."""
    with _client(vault, _box(cuts_below=0)) as client:
        extractor = Extractor(vault, client)
        cut = extractor._read_one(
            client_mod.Completion(
                content="{",
                model=MODEL,
                raw={},
                latency_s=0.1,
                sampling={"max_tokens": 8192},
                finish_reason="length",
            ),
            "image/jpeg",
            where="page 2",
        )

    assert cut.truncated is True
    assert cut.unreadable_reason.startswith("page 2: ")


def test_an_answer_refused_for_any_other_reason_is_still_reported_that_way(vault):
    """The new outcome must not have swallowed the old one.

    Thinking narration landing in ``content`` is a server setting to change, and
    it still says so rather than talking about room.
    """
    short = _ingest(vault)

    def narrating(request):
        return httpx.Response(
            200,
            json={
                "id": "c",
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Sure! Here goes:"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with _client(vault, narrating) as client:
        outcome = Extractor(vault, client).run(short)

    assert outcome.reading == runner.READ_REFUSED
    assert outcome.state == jobs_mod.UNREADABLE
    assert "response_format json_schema" in outcome.reason
    assert "ran out of room" not in outcome.describe()
