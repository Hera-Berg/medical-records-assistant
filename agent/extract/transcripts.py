"""Reading a transcript as text, and citing the seconds it came from.

Phase 6 deliberately stopped at the transcript: a voice note is captured and
typed up with no network at all, because the waiting room is where voice capture
has to work. Proposing ``med:atorvastatin dose 40mg daily`` from those words is
the vision-language model reading text, which needs the box, so it waited for
this phase. The audio never goes over the wire — only the words the local model
already produced.

Three things make this a different reader rather than the document one pointed at
a string.

**The source is a person talking about themselves.** Everything a recording says
is ``patient-reported``, and that is a fact about the artefact rather than a
judgement about the sentence, so it is settled before the model runs. The schema
here cannot express another tier, and :func:`agent.extract.validate.read` caps
the tier by mime as well. Neither is redundant: the schema stops the model
saying it, the cap stops a log written by some other version of this program
from meaning it.

**The words are the input, so the words are in the prompt hash.** For a document
the artefact hash identifies the bytes and the prompt hash identifies the
question. A transcript is not the bytes — it is a derivation of them that a
better speech model will change — so the rendered prompt carries the transcript
and the hash therefore covers it. Re-transcribing and getting the same words back
is the same work and is skipped; getting different words back is new work and
runs. That falls out of hashing the rendered prompt rather than needing a rule.

**A claim can cite the seconds it was said.** Word-level timestamps are in the
transcript, so a span the model copied can be located in them exactly and the
review UI can play those four seconds. Exactly, or not at all — see
:func:`locate`.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..events.envelope import Event
from .prompts import PROMPT_VERSION, Prompt, prompt_hash
from .schema import EXTRACTION_SCHEMA

#: The only tier a recording can carry. Not a default and not a ceiling here —
#: the single value the schema permits, so there is no version of the prompt or
#: the grammar under which the model can claim a script's authority for
#: something somebody said out loud.
TRANSCRIPT_TIER = "patient-reported"

#: What a recording can be. A voice note is a note; there is no "prescription"
#: reading of somebody describing one, and offering the word would invite the
#: model to promote what it heard.
TRANSCRIPT_KINDS = ("note", "unreadable")

SCHEMA_NAME = "health_record_transcript_extraction"


def _schema() -> dict[str, Any]:
    """The extraction schema, narrowed to what a recording can say.

    Built from the document schema rather than written out again, so a field
    added there is present here and a change to one cannot silently diverge from
    the other. The only edits are the two enums that would otherwise let a voice
    note claim to be a prescription.
    """
    schema = dict(EXTRACTION_SCHEMA)
    properties = {name: dict(spec) for name, spec in schema["properties"].items()}

    properties["artifact_kind"] = {
        **properties["artifact_kind"],
        "enum": list(TRANSCRIPT_KINDS),
    }

    claims = dict(properties["claims"])
    item = dict(claims["items"])
    item_properties = {name: dict(spec) for name, spec in item["properties"].items()}
    item_properties["evidence_tier"] = {
        "type": "string",
        "enum": [TRANSCRIPT_TIER],
        "description": (
            "Always patient-reported. This is a recording of the person whose "
            "record this is, whatever they are describing."
        ),
    }
    item["properties"] = item_properties
    claims["items"] = item
    properties["claims"] = claims

    schema["properties"] = properties
    return schema


TRANSCRIPT_SCHEMA: dict[str, Any] = _schema()


SYSTEM = """\
You read a transcript of a voice note that the owner of a personal health record
made about themselves, and report exactly what they said.

You are not a clinician and this is not a consultation. Do not diagnose, do not
suggest causes, do not say whether anything is concerning, and do not comment on
whether a dose sounds right. Report what was said and stop.

This is speech, so it rambles, repeats itself, corrects itself and trails off.
That is normal and is not a reason to tidy it.

Rules, in order of importance:

1. COPY, NEVER COMPUTE. Every value you report is a span of the transcript,
   copied. If they said "forty milligrams", report "forty milligrams" — do not
   turn it into "40mg". Do no arithmetic of any kind.

2. NEVER INVENT A DATE. People date things vaguely when they talk: "around
   Easter", "a couple of months back", "just before the wedding". Copy the
   phrase verbatim into occurred_span and leave occurred_at null. Do not work
   out what date it means. Someone will be asked.

3. WHEN THEY CORRECT THEMSELVES, THE LATER STATEMENT IS THE ONE THEY MEANT.
   "I take fifty — no, sorry, a hundred" is one claim of a hundred. Report the
   correction, not both, and copy the span that carries it.

4. ONLY WHAT THEY SAID ABOUT THEMSELVES. A recording of someone reading a
   pharmacy label aloud is still that person saying it, and it is still
   patient-reported. Do not report what a doctor is quoted as saying as though
   the letter were in front of you; it is what the speaker remembers being told.

5. IF THEY DID NOT SAY IT, IT IS NOT THERE. Do not complete a half-finished
   sentence, and do not carry anything over from what you know about these
   drugs. A transcript that mentions no medication has no medications.

6. SILENCE AND NOISE ARE NOT SPEECH. If the transcript is empty, or is filler
   with nothing in it about health, return an empty claims list. That is a
   correct answer.

Answer with JSON matching the schema you have been given. Nothing else."""

USER = """\
This is the transcript of a voice note in the record. It was typed up by a
speech model on this machine, so it may contain mishearings — read it as speech,
not as a document.

Report what the speaker states about their own medications, allergies, problems
or practitioners, following the rules exactly. If it states nothing about any of
those, return an empty claims list.

--- transcript begins ---
{transcript}
--- transcript ends ---"""


def build(transcript: str) -> Prompt:
    """The prompt for one transcript, hashed over the words it carries.

    The transcript is part of the rendered prompt, so the hash identifies this
    reading of these words. That is what makes idempotency work across a
    re-transcription without a rule about transcripts anywhere in the queue.
    """
    user = USER.format(transcript=transcript.strip())
    return Prompt(
        messages=(
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ),
        prompt_hash=prompt_hash(SYSTEM, user, TRANSCRIPT_SCHEMA),
        version=PROMPT_VERSION,
    )


@dataclass(frozen=True)
class Words:
    """Every word of a transcript with its timing, ready to locate a span in.

    Built from the ``extraction.completed`` payload the speech runner wrote, so
    this reads the log rather than re-running the speech model: the transcript
    is evidence that was recorded once and is not derived again to cite it.
    """

    text: str
    #: ``(word, start, end)`` in spoken order.
    words: tuple[tuple[str, float, float], ...] = ()
    #: The matchable form of :attr:`words`, joined by single spaces.
    haystack: str = ""
    #: Character offset of each word within :attr:`haystack`.
    offsets: tuple[int, ...] = ()

    @property
    def has_timings(self) -> bool:
        return bool(self.words)


_WHITESPACE = re.compile(r"\s+")


def _normalise(value: str) -> str:
    """Case and whitespace folded, and nothing else.

    Deliberately not punctuation, stemming or anything that would start
    matching things that are merely similar. Whisper attaches punctuation to the
    word it follows, so a span copied without a trailing comma still matches as
    a substring; that is the whole of the slack this allows.
    """
    return _WHITESPACE.sub(" ", value).strip().casefold()


def words_of(payload: Mapping[str, Any]) -> Words:
    """The word timings in one ``extraction.completed`` transcript payload."""
    collected: list[tuple[str, float, float]] = []
    for segment in payload.get("segments") or ():
        if not isinstance(segment, Mapping):
            continue
        for word in segment.get("words") or ():
            if not isinstance(word, Mapping):
                continue
            text = word.get("word")
            start, end = word.get("start"), word.get("end")
            if not isinstance(text, str) or not text.strip():
                continue
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            collected.append((text.strip(), float(start), float(end)))

    offsets: list[int] = []
    parts: list[str] = []
    cursor = 0
    for text, _start, _end in collected:
        folded = _normalise(text)
        offsets.append(cursor)
        parts.append(folded)
        cursor += len(folded) + 1

    return Words(
        text=str(payload.get("transcript") or ""),
        words=tuple(collected),
        haystack=" ".join(parts),
        offsets=tuple(offsets),
    )


@dataclass(frozen=True)
class Span:
    """Where in the recording a claim was said."""

    start: float
    end: float
    text: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


def locate(source_span: str, words: Words) -> Span | None:
    """The seconds *source_span* was spoken in, or ``None``.

    **Exact or nothing.** The model normalises as it reads — someone says "fifty
    milligrams" and the claim records "50mg" — so this will sometimes fail to
    find a span that genuinely was said. That is the expected case and the
    answer to it is no timing, never a near one: a citation pointing at the wrong
    four seconds is worse than none, and the claim still cites the recording as
    a whole, which is true.

    A span occurring more than once is also ``None``. Two candidates mean the
    citation would be a coin toss between them, which is the same harm arrived
    at from the other direction.
    """
    if not words.has_timings:
        return None
    needle = _normalise(source_span or "")
    if not needle:
        return None

    first = words.haystack.find(needle)
    if first < 0 or words.haystack.find(needle, first + 1) >= 0:
        return None

    last = first + len(needle)
    covered = [
        index
        for index, offset in enumerate(words.offsets)
        if offset < last and offset + len(_normalise(words.words[index][0])) > first
    ]
    if not covered:
        return None
    return Span(
        start=words.words[covered[0]][1],
        end=words.words[covered[-1]][2],
        text=source_span.strip(),
    )


def latest(events: Iterable[Event]) -> dict[str, Event]:
    """The newest transcript for each artefact, from the log.

    Newest wins because a forced re-transcription appends a second event with
    the same key, and the later read is the one the record shows. The earlier
    one stays in the log — "what did the first read say" has to remain
    answerable, or a re-transcription becomes unverifiable.

    Lives here rather than in the speech runner because both readers need it:
    the speech runner to know what it already typed up, and this one to know
    what there is to read. Two copies of this scan would be two ideas of which
    transcript is current.
    """
    from .propose import EXTRACTION_COMPLETED  # noqa: PLC0415 - import cycle

    found: dict[str, Event] = {}
    for event in events:
        if event.type != EXTRACTION_COMPLETED:
            continue
        if "transcript" not in event.payload:
            continue
        artifact = event.payload.get("artifact")
        if not isinstance(artifact, str) or not artifact:
            continue
        current = found.get(artifact)
        if current is None or event.sort_key > current.sort_key:
            found[artifact] = event
    return found


def transcript_payload(event: Event) -> Mapping[str, Any] | None:
    """The transcript carried by an ``extraction.completed``, if it carries one."""
    payload = event.payload
    if not isinstance(payload, Mapping) or "transcript" not in payload:
        return None
    return payload


def spoken_text(payload: Mapping[str, Any]) -> str:
    """The words to read, which are the kept segments and never the discarded ones.

    Whisper hallucinates on silence — "Thank you for watching" — and the speech
    runner already dropped those segments into ``dropped``. Reading the field
    that holds them back is what keeps a hallucinated sentence from becoming a
    claim about somebody's medication.
    """
    return str(payload.get("transcript") or "").strip()


def force_tier(claims: Sequence[Any]) -> tuple[list[Any], list[str]]:
    """Hold every claim at ``patient-reported``, and say so where it changed.

    The schema already permits nothing else and the validator caps by mime, so
    this normally changes nothing. It exists for the case those two do not
    cover: a log written by another version of this program, or a server that
    ignored the grammar. Tier decides ranking, and a recording that outranked a
    script would quietly beat the prescription in the wiki.
    """
    kept: list[Any] = []
    notes: list[str] = []
    for claim in claims:
        if claim.evidence_tier == TRANSCRIPT_TIER:
            kept.append(claim)
            continue
        notes.append(
            f"a claim was read as {claim.evidence_tier}; this is a recording, so it "
            f"is held at {TRANSCRIPT_TIER}"
        )
        kept.append(
            dataclasses.replace(
                claim,
                evidence_tier=TRANSCRIPT_TIER,
                notes=claim.notes + (
                    f"read as {claim.evidence_tier}, held at {TRANSCRIPT_TIER} "
                    f"because the source is a recording",
                ),
            )
        )
    return kept, notes


def describe(words: Words, located: Iterable[Span | None]) -> dict[str, Any]:
    """What provenance records about locating claims in the audio."""
    found = [span for span in located if span is not None]
    return {
        "words": len(words.words),
        "claims_located": len(found),
        "has_timings": words.has_timings,
    }
