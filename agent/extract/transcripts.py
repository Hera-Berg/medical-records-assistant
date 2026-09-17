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
judgement about the sentence, so it is settled before the model runs. The reader
caps the tier by mime, and :func:`force_tier` holds anything that still gets
through. Neither is redundant: the cap stops the model's label taking effect, and
the hold stops a log written by some other version of this program meaning it.

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

#: The only tier a recording can carry. Not a default and not a ceiling here —
#: the single value the schema permits, so there is no version of the prompt or
#: the grammar under which the model can claim a script's authority for
#: something somebody said out loud.
TRANSCRIPT_TIER = "patient-reported"

SYSTEM = """\
You read a transcript of a voice note that the owner of a personal health record
made about themselves, and report exactly what they said.

You are not a clinician and this is not a consultation. Do not diagnose, do not
suggest causes, do not say whether anything is concerning, and do not comment on
whether a dose sounds right. Report what was said and stop.

This is speech, so it rambles, repeats itself, corrects itself and trails off.
That is normal and is not a reason to tidy it. You will be asked about it in
parts: first medications, then allergies, then problems and practitioners.

Rules, in order of importance:

1. UNSURE MEANS UNCLEAR. Whenever you cannot tell with certainty what was said —
   a name, a strength, how often — put it in the unclear list and leave it out
   of the rest of your answer. That sends it to the person to check. A guess is
   the one answer that does harm here.

2. A NAME IS ONLY A NAME. "Atorvastatin", never "atorvastatin forty milligrams".
   The strength goes in strength and how often goes in frequency.

3. COPY, NEVER COMPUTE. Every value is a span of the transcript, copied. If they
   said "forty milligrams", report "forty milligrams" — do not turn it into
   "40mg". Do no arithmetic of any kind.

4. NEVER INVENT A DATE. Copy vague phrases — "around Easter" — verbatim into
   occurred_span and leave occurred_at null.

5. WHEN THEY CORRECT THEMSELVES, THE LATER STATEMENT IS THE ONE THEY MEANT.

6. ONLY WHAT THEY SAID ABOUT THEMSELVES. It is always patient-reported, whatever
   they are describing. If they did not say it, it is not there.

7. SILENCE AND NOISE ARE NOT SPEECH. Filler with nothing about health means empty
   lists. That is a correct answer.

Answer with JSON matching the schema you have been given. Nothing else."""

OPENING = """\
This is the transcript of a voice note in the record. It was typed up by a
speech model on this machine, so it may contain mishearings — read it as speech,
not as a document.

--- transcript begins ---
{transcript}
--- transcript ends ---

First, the medications. Report every medicine they talk about with certainty,
with the strength and how often copied exactly as said. Anything you cannot tell
with certainty goes in unclear. If they mention no medicines, return an empty
list."""


def build(transcript: str) -> Prompt:
    """The conversation about one transcript, hashed over the words it carries.

    The transcript is part of the rendered prompt, so the hash identifies this
    reading of these words. That is what makes idempotency work across a
    re-transcription without a rule about transcripts anywhere in the queue.
    The groups and their schemas are the document reader's, so a field added
    there is asked of a recording too.
    """
    from .prompts import FOLLOW_UPS, prompt_hash  # noqa: PLC0415
    from .schema import FAMILIES, TRANSCRIPT_SCHEMAS  # noqa: PLC0415

    opening = OPENING.format(transcript=transcript.strip())
    words = "\n\n".join([opening, *(FOLLOW_UPS[f] for f in FAMILIES[1:])])
    return Prompt(
        messages=(
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": opening},
        ),
        prompt_hash=prompt_hash(SYSTEM, words, TRANSCRIPT_SCHEMAS),
        follow_ups=tuple((f, FOLLOW_UPS[f]) for f in FAMILIES[1:]),
        version=PROMPT_VERSION,
        transcript=True,
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
