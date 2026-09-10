"""Speech: the one model that runs on this machine.

``MODELS.md`` splits the two models deliberately. The vision-language model runs
on a box over the tailnet because it is the part that needs the hardware; speech
stays here because it is small, because voice notes are the most time-sensitive
capture and must work in a waiting room with no tailnet, and because it avoids
shipping audio over the wire at all. Everything in this package follows from
that: **nothing here opens a socket, and nothing here needs the endpoint.**

``faster-whisper`` ``small`` at ``int8``. Not ``tiny`` — the drug-name error rate
makes those transcripts actively misleading rather than merely rough — and not
``medium``, which does not fit the laptop's budget beside everything else.

Because ``small`` is the weaker model, the biasing in :mod:`agent.asr.hotwords`
is not an optimisation. It is what makes the transcripts usable: Whisper renders
"perindopril" as "peran doprol" and "frusemide" as "for semide", and a mangled
drug name is an unmatched entity, which becomes a duplicate page in the wiki.

What this package does **not** do is propose claims. A transcript is evidence,
stored verbatim and cited by the second; turning one into
``med:atorvastatin dose 40mg daily`` needs the vision-language model reading it
as text, which needs the box, which would put a network dependency back into the
one capture path that must survive its absence. That is phase 7's, and phase 7's
scope note says so.

Four rules this package holds, each from ``MODELS.md``:

**The audio is kept forever.** The transcript is derived and re-derivable; the
recording is the artefact. Nothing here deletes audio, on any path, including
re-transcription with a better model. The 16 kHz working copy is a temporary
file outside the vault and is deleted in a ``finally``.

**A hallucinated segment never becomes a claim.** Whisper invents subtitle
artefacts over silence — "Thank you for watching" — so segments are dropped by
VAD, by ``no_speech_prob``, by compression ratio, by average log probability,
and by an exact match against known artefacts. Dropped segments are *recorded*
in the extraction event, because "the model said this and it was discarded" is
provenance, but they are not part of the transcript and reach nothing derived.

**Word-level timestamps are stored.** A claim can then cite not "voice note,
2 September" but the four seconds it came from, and the review UI can play
exactly that. Citation granularity at the second is a feature here, not polish.

**A re-transcription is an ``extraction.completed`` like any other.** It is
subject to the same rule as every other model output: a user correction outranks
it, always, regardless of which came first.

Re-transcription and the hotword trade-off
------------------------------------------

The idempotency key is ``(artifact, settings digest, model)`` and the **hotword
list is deliberately not in the digest**. If it were, every new medication in
the wiki would change the digest for every recording in the vault and the queue
would re-transcribe the entire history — repeatedly, for the lifetime of the
record, on a laptop.

The cost of that choice is real and worth stating plainly: **improving the
hotword list never re-transcribes anything on its own.** A recording made when
the record was empty keeps whatever the unbiased model made of its drug names,
even after those drugs are in the wiki and would now be recognised. That is
exactly the recording most likely to be wrong, because it was made when there
was least to bias against.

The escape hatch is explicit rather than automatic::

    health-agent transcribe --force --artifact a3f91c

which re-reads that recording with the current hotwords and appends a second
``extraction.completed`` recording that it superseded the first. Both stay in
the log — "what did the earlier read say" must remain answerable — and a user
correction still outranks whichever is newer.
"""

from __future__ import annotations

# The submodules are the surface. The transcribing *function* is deliberately
# not re-exported here: it is called ``transcribe``, as is the module it lives
# in, and binding the function onto the package would shadow the module for
# every ``from . import transcribe`` inside the package.
from . import audio, hotwords, transcribe
from .hotwords import HOTWORD_LIMIT, collect
from .transcribe import (
    DROPPED_ARTEFACT,
    DROPPED_LOGPROB,
    DROPPED_RATIO,
    DROPPED_SILENCE,
    Dropped,
    Segment,
    Transcript,
    Word,
    settings_digest,
)

__all__ = [
    "DROPPED_ARTEFACT",
    "DROPPED_LOGPROB",
    "DROPPED_RATIO",
    "DROPPED_SILENCE",
    "Dropped",
    "HOTWORD_LIMIT",
    "Segment",
    "Transcript",
    "Word",
    "audio",
    "collect",
    "hotwords",
    "settings_digest",
    "transcribe",
]
