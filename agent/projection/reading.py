"""What has happened to one artefact, and what happens to it next.

A record that shows a document and says nothing else about it reads as finished.
Most of the time it is not: the artefact is queued behind nine others, or the
box is asleep, or — for a recording — it has been typed up on this machine and
is waiting for the box to read those words for medications. Each of those is a
normal state and none of them is a failure, but a screen that stays silent about
them turns "waiting" into "broken" in the reader's head, and turns "still to
come" into "done".

So every artefact carries a sentence saying where it has got to.

**This module computes only what the event log establishes**, and that boundary
matters more than it looks. The wiki is a pure function of ``(events, as_of)``:
delete ``wiki/`` and ``.agent/``, replay, and the bytes must match. The
extraction queue lives in ``.agent/jobs.jsonl``, which is a disposable cache of
*work*, not of the record — so "waiting behind four other files" and "the box is
asleep" cannot appear in a generated file without breaking the rebuild the
moment someone empties that folder.

The split is therefore:

* **Here**, from events: has anything read this, what did it find, and is it a
  recording that has been typed up but not yet read. Rendered into ``wiki/``.
* **In** :mod:`agent.server.reading`, from the live queue and the endpoint: *why*
  something not yet read is not yet read. Shown on screen, never written down.

The wiki consequently says "Not read yet" where a screen says "Waiting to be
read — the box is asleep." Both are true; only one of them is still true in five
years with the app uninstalled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..events.envelope import Event

EXTRACTION_COMPLETED = "extraction.completed"

#: Nothing has read this artefact.
NOT_READ = "not-read"
#: A recording that has been typed up but not yet read for medications. The
#: transcript is evidence and is cited; the reader that proposes claims from it
#: runs on the box, so this is where a recording waits when the box is asleep.
TRANSCRIBED = "transcribed"
#: Read, and it stated something the record tracks.
READ = "read"
#: Read, and it stated nothing the record tracks. A correct answer, not a fault.
NOTHING_FOUND = "nothing-found"
#: The model looked and could not make it out. Goes to a person.
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Reading:
    """How far one artefact has got, from the log alone."""

    short: str
    state: str = NOT_READ
    #: Claims proposed off this artefact, and how many are still undecided.
    claims: int = 0
    awaiting: int = 0
    is_recording: bool = False
    #: Why it could not be read, when it could not be. The model's own words.
    reason: str | None = None
    #: For a transcribed recording: whether there was any speech in it. A
    #: recording of silence has been read and holds nothing, which is a settled
    #: outcome — telling its owner that "what you said is kept and quoted here"
    #: would promise a quotation that does not exist.
    spoke: bool = True
    #: Parts the reader said it could not read, still waiting on a person. A
    #: document with these is never finished, whatever was decided about the
    #: rest of it.
    unclear: int = 0

    @property
    def is_finished(self) -> bool:
        """Whether there is nothing left for this artefact or its owner to do.

        Deliberately false for :data:`TRANSCRIBED`: the words are in the record
        but nothing has read them for medications yet, and a recording waiting
        on the box must not look settled.
        """
        if self.state == NOT_READ:
            return False
        if self.state == TRANSCRIBED:
            # Nothing was said, so there is nothing for a later version to read
            # out of it either. Silence is an answer.
            return not self.spoke
        return self.awaiting == 0 and self.unclear == 0

    def sentence(self) -> str:
        """One short sentence, in the words the owner of the record would use.

        **Short is a requirement, not a preference.** This sits on the timeline,
        which is the screen someone opens most and the one ``CLAUDE.md`` asks to
        be dense and scannable. A paragraph here turns every artefact row into
        five lines and buries the rows either side of it; the explaining is done
        on the artefact's own page, where there is room and where someone has
        gone deliberately to find out more.

        Never a bare status word either. "unread" needs a legend; "Not read yet"
        does not, and this text is read by the person whose record it is.
        """
        if self.state == TRANSCRIBED:
            if not self.spoke:
                return "No speech was found in it."
            # Said plainly rather than left as silence. Someone who has just
            # spoken a medication change into their record will otherwise assume
            # it is already in their medication list — and this state can last a
            # while, because reading the words needs the box.
            return "Typed up — waiting to be read for medications."
        if self.state == UNREADABLE:
            return f"Could not be read — {self.reason}" if self.reason else (
                "Could not be read."
            )
        if self.unclear and self.state in (READ, NOTHING_FOUND):
            # Said before anything else about it: the part that could not be
            # read is the part that needs the person.
            if self.awaiting:
                return (
                    f"Read in part — {self.awaiting} to review, and some of it could "
                    f"not be read."
                )
            return "Read in part — some of it could not be read, and is on your review list."
        if self.state == NOTHING_FOUND:
            return "Read — nothing in it is tracked here."
        if self.state == READ:
            if self.awaiting:
                thing = "thing" if self.awaiting == 1 else "things"
                return f"Read — {self.awaiting} {thing} added to your review list."
            thing = "thing" if self.claims == 1 else "things"
            return f"Read — {self.claims} {thing} taken from it, already decided."
        return "Not read yet."


def _is_speech_extraction(event: Event) -> bool:
    """Whether this extraction came from the speech model rather than the VLM."""
    reader = event.payload.get("reader")
    if isinstance(reader, str):
        return reader == "speech"
    # Events written before ``reader`` existed. A transcript key is the tell.
    return "transcript" in event.payload


def index(
    events: Iterable[Event],
    artifacts: Mapping[str, object],
    reviews: Mapping[str, object] | None = None,
) -> dict[str, Reading]:
    """A :class:`Reading` for every artefact in *artifacts*.

    *reviews* is :attr:`agent.projection.Projection.artifacts` — the per-artefact
    review tally reconciliation already computes — so the count of things still
    waiting for a tap is read from the same place the review queue reads it, and
    the two cannot disagree.
    """
    events = list(events)
    reviews = reviews or {}

    #: short -> whether the newest transcript for it found any speech.
    spoken: dict[str, bool] = {}
    looked: dict[str, Event] = {}
    heard: dict[str, tuple] = {}
    for event in events:
        if event.type != EXTRACTION_COMPLETED:
            continue
        short = event.payload.get("artifact")
        if not isinstance(short, str) or not short:
            continue
        if _is_speech_extraction(event):
            # Newest wins, the same rule the transcript itself follows: a forced
            # re-read is what the record shows.
            if short not in heard or event.sort_key > heard[short]:
                heard[short] = event.sort_key
                spoken[short] = bool(str(event.payload.get("transcript") or "").strip())
            continue
        # The latest vision read wins: a re-extraction under a better model is
        # what the artefact's state is now, and the earlier one stays in the log.
        current = looked.get(short)
        if current is None or event.sort_key > current.sort_key:
            looked[short] = event

    from . import unread as unread_mod  # noqa: PLC0415 - import cycle

    done = unread_mod.acknowledged(events)
    found: dict[str, Reading] = {}
    for short, artifact in artifacts.items():
        mime = str(getattr(artifact, "mime", "") or "")
        recording = mime.startswith(("audio/", "video/"))
        review = reviews.get(short)
        claims = int(getattr(review, "claims", 0) or 0)
        decided = int(getattr(review, "decided", 0) or 0)

        read = looked.get(short)
        if read is not None or claims:
            # Claims are the stronger evidence. `extraction.completed` is the
            # bookkeeping and `claim.proposed` is the result, and a log that
            # carries the second without the first — a hand-authored one, a
            # partial restore, the demo's own seeded stream — has still plainly
            # been read. Reporting "not read yet" beside two claims cited to
            # that very artefact is a contradiction on one page.
            readable = read.payload.get("readable") if read is not None else None
            if readable is False:
                found[short] = Reading(
                    short,
                    UNREADABLE,
                    claims=claims,
                    awaiting=max(claims - decided, 0),
                    is_recording=recording,
                    reason=_clean(read.payload.get("unreadable_reason")) if read else None,
                    # Not finished until a person has dealt with it: a page
                    # nobody could read is exactly the page that needs one.
                    unclear=0 if read.id in done else 1,
                )
                continue
            unclear = 0
            if read is not None and read.id not in done:
                unclear = len(unread_mod.open_abstentions(read))
            found[short] = Reading(
                short,
                READ if claims else NOTHING_FOUND,
                claims=claims,
                awaiting=max(claims - decided, 0),
                is_recording=recording,
                unclear=unclear,
            )
            continue

        if short in spoken:
            found[short] = Reading(
                short, TRANSCRIBED, is_recording=True, spoke=spoken[short]
            )
            continue

        found[short] = Reading(short, NOT_READ, is_recording=recording)
    return found


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed or None
