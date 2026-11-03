"""Words the person chose to add to their record are read like a voice note.

The chat's "Add to my record" stores the message as a plain-text artefact in
``raw/`` — the person's own words, kept beside the record like any original — and
the extractor reads it with the transcript reader. What is asserted here:

- the words reach the box as text and nothing else does;
- every claim is ``patient-reported``, whatever the model says;
- the same words are read once, however often the job is retried;
- an empty note asks nothing.
"""

from __future__ import annotations

import json

from agent import ingest as ingest_mod
from agent.extract import propose, runner as extract_runner
from agent.extract.runner import Extractor
from agent.extract.validate import _cap_tier

from .test_transcript_claims import (  # noqa: F401 — the autouse credential fixture
    Box,
    _answer,
    _client,
    _credentials,
    _spoken_claim,
)

TYPED = "I stopped taking the atorvastatin last week. Still on forty milligrams daily before that."


def _typed(vault, text: str = TYPED):
    return ingest_mod.ingest_bytes(
        vault,
        text.encode("utf-8"),
        ingest_mod.CaptureContext(
            source="chat", original_filename="note.txt", declared_mime="text/plain"
        ),
    )


def test_a_typed_note_is_read_as_text_and_proposes_patient_reported_claims(vault):
    stored = _typed(vault)
    box = Box(_answer([_spoken_claim(source_span="forty milligrams daily")]))
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(stored.short)
    for event in outcome.events:
        vault.append(event)

    sent = json.dumps(box.requests)
    assert TYPED in sent
    assert "image_url" not in sent
    assert "a note the person typed" in sent
    proposed = [e for e in outcome.events if e.type == propose.CLAIM_PROPOSED]
    assert len(proposed) == 1
    assert {e.payload["evidence_tier"] for e in proposed} == {"patient-reported"}


def test_the_same_note_is_read_once(vault):
    stored = _typed(vault)
    box = Box(_answer([_spoken_claim(source_span="forty milligrams daily")]))
    with _client(vault, box) as client:
        first = Extractor(vault, client).run(stored.short)
        for event in first.events:
            vault.append(event)
        sent = len(box.requests)
        again = Extractor(vault, client).run(stored.short)

    assert again.reading == extract_runner.READ_ALREADY
    assert len(box.requests) == sent, "nothing was sent the second time"
    assert again.events == ()


def test_an_empty_note_asks_nothing(vault):
    stored = _typed(vault, "   \n")
    box = Box()
    with _client(vault, box) as client:
        outcome = Extractor(vault, client).run(stored.short)

    assert box.requests == []
    assert outcome.reading == extract_runner.READ_NOTHING


def test_text_is_capped_at_patient_reported():
    assert _cap_tier("prescriber-issued", "text/plain")[0] == "patient-reported"
    assert _cap_tier("prescriber-issued", "image/jpeg")[0] == "prescriber-issued"
