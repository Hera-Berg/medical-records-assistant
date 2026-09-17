"""Citing the seconds a claim was said in — exactly, or not at all.

Word-level timestamps are in the transcript, so a claim read out of a voice note
can cite the four seconds it came from and the review UI can play them. The rule
this file exists for is what happens when the span cannot be found.

**A citation pointing at the wrong four seconds is worse than none.** The model
normalises as it reads: someone says "forty milligrams" and the claim records
"40mg". That match will fail, and the answer is no timing rather than the
nearest thing — the claim still cites the recording as a whole, which is true,
and a precise-looking timestamp that plays something unrelated destroys trust in
the ones that are right.
"""

from __future__ import annotations

from agent.extract import transcripts

SPOKEN = (
    "Right, so. I stopped the sertraline, that was, I think around Easter time, "
    "somewhere around then. And the doctor put the atorvastatin up to forty "
    "milligrams daily."
)


def _payload(text: str = SPOKEN, start: float = 0.0, step: float = 0.4):
    """A transcript payload with a timing on every word, as the ASR runner writes."""
    words = []
    at = start
    for word in text.split():
        words.append({"word": word, "start": round(at, 3), "end": round(at + step, 3)})
        at += step
    return {
        "transcript": text,
        "segments": [{"start": start, "end": at, "text": text, "words": words}],
    }


def test_a_span_the_speaker_actually_said_is_located():
    words = transcripts.words_of(_payload())
    span = transcripts.locate("I stopped the sertraline", words)

    assert span is not None
    # Four words in, at 0.4s each.
    assert span.start == 0.8
    assert round(span.end, 1) == 2.4
    assert span.text == "I stopped the sertraline"


def test_trailing_punctuation_does_not_defeat_a_match():
    """Whisper attaches punctuation to the word before it.

    A span copied without the comma is the same words, and the whole of the
    slack this allows: whitespace and case, nothing else.
    """
    words = transcripts.words_of(_payload())
    assert transcripts.locate("the sertraline", words) is not None
    assert transcripts.locate("The Sertraline", words) is not None
    assert transcripts.locate("the   sertraline", words) is not None


def test_a_normalised_value_yields_no_span_rather_than_a_near_one():
    """"40mg" against "forty milligrams" is the expected failure, not a bug.

    This is the case the rule exists for. The model tidies as it reads, the
    match fails, and nothing is emitted — the claim's citation to the recording
    still holds.
    """
    words = transcripts.words_of(_payload())
    assert transcripts.locate("40mg daily", words) is None
    assert transcripts.locate("atorvastatin 40mg", words) is None


def test_a_span_occurring_twice_yields_nothing():
    """Two candidates make the citation a coin toss, which is the same harm."""
    words = transcripts.words_of(_payload("I take it daily and I mean daily"))
    assert transcripts.locate("daily", words) is None
    # The unambiguous part of the same transcript still resolves.
    assert transcripts.locate("I take it", words) is not None


def test_a_transcript_with_no_word_timings_locates_nothing():
    """An older or minimal transcript is not an error; it just cannot be cited."""
    words = transcripts.words_of({"transcript": SPOKEN, "segments": []})
    assert words.has_timings is False
    assert transcripts.locate("I stopped the sertraline", words) is None


def test_an_empty_span_locates_nothing():
    words = transcripts.words_of(_payload())
    assert transcripts.locate("", words) is None
    assert transcripts.locate("   ", words) is None


def test_malformed_word_entries_are_skipped_rather_than_trusted():
    """A hand-edited or sync-corrupted payload must not produce a wrong timing."""
    payload = _payload()
    payload["segments"][0]["words"].insert(0, {"word": "ghost", "start": None, "end": 1})
    payload["segments"][0]["words"].insert(1, {"start": 0, "end": 1})

    words = transcripts.words_of(payload)
    span = transcripts.locate("I stopped the sertraline", words)
    assert span is not None
    assert span.start == 0.8


def test_the_transcript_is_in_the_prompt_hash():
    """Different words are different work; the same words are not.

    Idempotency is keyed on ``(artifact, prompt_hash, model)``, and for a
    recording the artefact hash identifies bytes that a better speech model will
    read differently. Putting the transcript in the rendered prompt is what
    makes a re-transcription re-read and a repeat not.
    """
    one = transcripts.build(SPOKEN)
    same = transcripts.build(SPOKEN)
    other = transcripts.build(SPOKEN.replace("forty", "fourteen"))

    assert one.prompt_hash == same.prompt_hash
    assert one.prompt_hash != other.prompt_hash


def test_the_prompt_carries_no_image_part():
    """Text in, text out. The audio never leaves the machine."""
    (system, user) = transcripts.build(SPOKEN).messages
    assert system["role"] == "system"
    assert isinstance(user["content"], str)
    assert SPOKEN in user["content"]
