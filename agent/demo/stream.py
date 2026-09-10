"""The demo scenario: one hand-authored event stream, and the artefacts it cites.

Everything here is fiction. The point is to exercise the renderer on the shapes
that actually stress it — a conflict, a correction, a stop that transitions and a
stop that only annotates, staleness, a review queue with something in every tier —
so that they can be read by hand before phase 4 puts a model behind them, and so
that phases 7 and 8 have something to build an inbox and a consultation summary
against.

**Every date is an offset from an anchor**, never a literal. A fixture with
hardcoded dates is realistic for a month and then quietly wrong: the medication
that was meant to be three weeks from running out is two years past it, and
everything downstream of staleness stops demonstrating what it was written to
demonstrate. Offsets keep the scenario true whenever it is run.

The four timestamps are kept honest here rather than approximated, because this
is the stream people will read to learn what the fields mean. ``ingested_ts`` is
when the demo actually wrote the bytes — today, truthfully, since that is when
they entered the vault. ``captured_ts`` is when the photograph was taken.
``artifact_ts`` is the date on the document. ``occurred_at`` is when the thing
described happened. They differ from each other throughout, on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from ..events.envelope import Event, format_ts
from . import authoring

#: Artefacts the scenario cites. ``captured`` is days before the anchor — when
#: the photograph was taken or the PDF downloaded, which is genuinely known for a
#: camera capture and is what the timeline places an artefact row on.
@dataclass(frozen=True)
class Artifact:
    """One file the demo writes into ``raw/`` so its citations resolve."""

    name: str
    kind: str
    captured: int
    note: str


ARTIFACTS: tuple[Artifact, ...] = (
    Artifact("perindopril-script", "photo", 20, "Photo of a repeat script"),
    Artifact("metformin-script", "photo", 240, "Photo of an older script"),
    Artifact("discharge-summary", "pdf", 40, "Discharge summary, two pages"),
    Artifact("cardiology-letter", "scan", 62, "Scan of a specialist letter"),
    Artifact("atorvastatin-script-a", "photo", 34, "Photo of a script"),
    Artifact("atorvastatin-script-b", "photo", 33, "Photo of a second script"),
    Artifact("levothyroxine-script", "photo", 55, "Blurry photo, taken at an angle"),
    Artifact("pathology", "pdf", 26, "Pathology report from the patient portal"),
    Artifact("voice-note", "audio", 9, "Voice note recorded in a waiting room"),
)


def _fuzzy(iso: str, precision: str = "day", uncertainty: int = 0) -> dict[str, object]:
    return {"value": iso, "precision": precision, "uncertainty_days": uncertainty}


class Clock:
    """Days before the anchor, as canonical timestamps and plain dates."""

    def __init__(self, anchor: datetime):
        self.anchor = anchor

    def ts(self, days_ago: int, hour: int = 9) -> str:
        moment = (self.anchor - timedelta(days=days_ago)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        return format_ts(moment)

    def date(self, days_ago: int) -> str:
        return (self.anchor - timedelta(days=days_ago)).date().isoformat()


def build(
    device: str,
    anchor: datetime,
    shorts: Mapping[str, str],
    ingested_ts: Mapping[str, str],
    captured_ts: Mapping[str, str],
) -> list[Event]:
    """The whole scenario, as events.

    *shorts*, *ingested_ts* and *captured_ts* are keyed by artefact name and come
    from the real ingest, so every citation in the wiki resolves to a file that
    is actually on disk.
    """
    clock = Clock(anchor)
    events: list[Event] = []

    def propose(
        artifact: str,
        subject: str,
        predicate: str,
        value,
        days_ago: int,
        occurred: dict[str, object] | None = None,
        document_date: int | None = None,
        **extra,
    ):
        """One ``claim.proposed``, with all four timestamps set honestly."""
        event = authoring.claim(
            device,
            subject,
            predicate,
            value,
            ts=clock.ts(days_ago),
            artifact=shorts[artifact],
            occurred=occurred,
            ingested_ts=ingested_ts[artifact],
            captured_ts=captured_ts[artifact],
            artifact_ts=(
                clock.ts(document_date) if document_date is not None else None
            ),
            **extra,
        )
        events.append(event)
        return event

    def confirm(event: Event, days_ago: int, hour: int = 18):
        events.append(authoring.confirm(device, event.id, ts=clock.ts(days_ago, hour)))

    # --- perindopril: the ordinary case, active, with supply arithmetic -------
    #
    # 30 tablets, one daily, no repeats, dated three weeks ago: still inside its
    # expected exhaustion, so `active` with a date in the near future. This is
    # the shape most of the record looks like when nothing is wrong.
    # The label really does say PERINDOPRIL ARGININE, so the claim says so too.
    # It files under `med:perindopril` — one entry per drug, with the wording
    # disclosed on the page. The `started` claim below comes off the cardiology
    # letter, which writes the plain name, so the entity carries both.
    dose = propose(
        "perindopril-script", "med:perindopril-arginine", "dose", "5mg daily", 20,
        occurred=_fuzzy(clock.date(20)), document_date=20,
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
        subject_name="Perindopril Arginine",
    )
    confirm(dose, 20)
    started = propose(
        "cardiology-letter", "med:perindopril", "started", "November 2024", 62,
        occurred=_fuzzy("2024-11-02", "month", 15), document_date=62,
    )
    confirm(started, 62)

    # --- metformin: stale, because the supply ran out and nothing renewed it --
    #
    # Absence of evidence is not evidence of absence. It stays on the current
    # list, marked stale, with how long it has been since anything confirmed it.
    metformin = propose(
        "metformin-script", "med:metformin-hydrochloride", "dose", "500mg twice daily", 240,
        occurred=_fuzzy(clock.date(240)), document_date=240,
        dispense={"quantity": "60 tablets", "frequency": "twice daily", "repeats": "no repeats"},
        subject_name="Metformin Hydrochloride",
    )
    confirm(metformin, 240)

    # --- amitriptyline: stopped, by a prescriber-issued stop the user confirmed
    #
    # The rule's target is inference from silence, not a document that says to
    # cease. A discharge summary saying to stop is exactly the evidence that may
    # transition the status — once the user has tapped it.
    amitriptyline = propose(
        "cardiology-letter", "med:amitriptyline", "dose", "10mg at night", 62,
        occurred=_fuzzy(clock.date(62)), document_date=62,
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "2 repeats"},
    )
    confirm(amitriptyline, 62)
    stop = propose(
        "discharge-summary", "med:amitriptyline", "status", "stopped", 40,
        occurred=_fuzzy(clock.date(41)), document_date=41,
    )
    confirm(stop, 39)

    # --- sertraline: a patient-reported stop, which annotates and does not stop
    #
    # The patient is the authority on what they actually take and the prescriber
    # on what was prescribed. The record shows both and marks the discrepancy.
    sertraline = propose(
        "discharge-summary", "med:sertraline", "dose", "50mg daily", 40,
        occurred=_fuzzy(clock.date(41)), document_date=41,
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "1 repeat"},
    )
    confirm(sertraline, 40)
    reported = propose(
        "voice-note", "med:sertraline", "status", "stopped", 9,
        occurred=_fuzzy(clock.date(16), "month", 10), tier="patient-reported",
    )
    confirm(reported, 9, hour=19)

    # --- atorvastatin: two prescriber-issued sources, neither chosen ----------
    #
    # Same tier, same date band, different doses. Both render, the entity is
    # `conflicted`, and nothing is averaged or silently picked.
    first = propose(
        "atorvastatin-script-a", "med:atorvastatin", "dose", "20mg daily", 34,
        occurred=_fuzzy(clock.date(35)), document_date=35,
    )
    confirm(first, 34)
    second = propose(
        "atorvastatin-script-b", "med:atorvastatin", "dose", "40mg daily", 33,
        occurred=_fuzzy(clock.date(35)), document_date=35,
    )
    confirm(second, 33)

    # --- levothyroxine: a misread the user corrected -------------------------
    #
    # The capital O for a zero is the classic OCR failure on a blurry photo. The
    # original stays visible under "Earlier readings" with what replaced it,
    # which is the only way a mistyped correction could ever be caught.
    misread = propose(
        "levothyroxine-script", "med:levothyroxine-sodium", "dose", "5Omcg daily", 55,
        occurred=_fuzzy(clock.date(56)), document_date=56,
        dispense={"quantity": "90 tablets", "frequency": "one daily", "repeats": "no repeats"},
        subject_name="Levothyroxine Sodium",
    )
    events.append(
        authoring.correct(
            device,
            value="50mcg daily",
            target=misread.id,
            ts=clock.ts(54, hour=20),
            dispense={
                "quantity": "90 tablets",
                "frequency": "one daily",
                "repeats": "no repeats",
            },
            evidence_tier="prescriber-issued",
            occurred_at=_fuzzy(clock.date(56)),
        )
    )

    # --- allergy, problem, practitioner --------------------------------------
    allergy = propose(
        "discharge-summary", "allergy:penicillin", "reaction", "rash", 40,
        occurred=_fuzzy(clock.date(41)), document_date=41,
    )
    confirm(allergy, 40)
    problem = propose(
        "cardiology-letter", "problem:hypertension", "name", "Hypertension", 62,
        occurred=_fuzzy("2024-11-02", "month", 15), document_date=62,
    )
    confirm(problem, 62)
    # Medium consequence and untouched: it applied itself after the review week
    # and is marked `unreviewed` in the wiki until the user confirms it.
    propose(
        "cardiology-letter", "person:dr-nguyen", "role", "Cardiologist", 62,
        occurred=_fuzzy(clock.date(62)),
    )

    # --- things waiting on a person, one per consequence tier -----------------
    #
    # So the review queue is not empty and phase 7 has something to render. High
    # never applies on its own; medium is inside its seven-day window; low
    # applied itself and is reversible from the timeline.
    propose(
        "pathology", "allergy:sulfonamides", "reaction", "swelling", 4,
        occurred=_fuzzy(clock.date(26)), document_date=26,
    )
    # A source that dated something in words rather than with a date. The
    # phrase is kept verbatim and `occurred_at` stays null — the model never
    # resolves one, because the computus is arithmetic but the *year* is a
    # guess. The projection raises a dateable review item carrying the candidate
    # it can compute, for the user to confirm in one tap.
    #
    # It was seeded with a resolved date until phase 7, which made it a claim no
    # extractor following the rules could have produced.
    onset = propose(
        "pathology", "problem:hypothyroidism", "onset", "since around Easter", 6,
        occurred=None, occurred_span="around Easter", document_date=26,
    )
    # Confirmed, so the phrase is in the record and the only thing still open is
    # *when*. That is its own review kind: nothing is wrong and nothing is
    # waiting to be applied — only the person who was there can turn "around
    # Easter" into a date, and the queue offers the computed candidate rather
    # than adopting it.
    confirm(onset, 5)
    propose(
        "voice-note", "person:dr-nguyen", "contact", "the Rosewood clinic", 9,
        tier="patient-reported",
    )

    # --- a stop proposal nobody has decided ----------------------------------
    #
    # A prescriber-issued document saying to cease, waiting on a tap. It is the
    # one queue item that must never be a generic accept: the inbox renders it
    # as its own action, and the route refuses `confirm` against it.
    propose(
        "discharge-summary", "med:metformin-hydrochloride", "status", "stopped", 5,
        occurred=_fuzzy(clock.date(41)), document_date=41,
        subject_name="Metformin Hydrochloride",
    )

    # --- a reading confirmed and then rejected -------------------------------
    #
    # Two user decisions about the same reading of one document, pointing
    # opposite ways: the later one governs and the content comes out, but the
    # direction of the mistake is unknowable, so the queue asks which was meant
    # — naming the artefact and never reproducing what it said.
    heard = propose(
        "voice-note", "problem:insomnia", "name", "insomnia", 9,
        occurred=_fuzzy(clock.date(9)), tier="patient-reported",
    )
    confirm(heard, 8, hour=9)
    reheard = propose(
        "voice-note", "problem:insomnia", "name", "insomnia", 7,
        occurred=_fuzzy(clock.date(9)), tier="patient-reported",
    )
    events.append(authoring.reject(device, reheard.id, ts=clock.ts(6, hour=21)))

    # --- a rejected reading, which must appear in no generated file -----------
    #
    # Worth seeding rather than only testing: reading the folder by hand is how
    # someone would notice it had leaked into a page or the timeline.
    misheard = propose(
        "voice-note", "problem:alcohol-dependence", "name", "alcohol dependence", 9,
        occurred=_fuzzy(clock.date(9)), tier="patient-reported",
    )
    events.append(authoring.reject(device, misheard.id, ts=clock.ts(8, hour=20)))

    # --- a voice note, for the timeline --------------------------------------
    events.append(
        authoring.note(
            device,
            ts=clock.ts(9, hour=11),
            transcript=(
                "Asked about the statin dose again, the pharmacist said to bring both "
                "scripts in. Headaches have been better since the tablets changed."
            ),
            artifact=shorts["voice-note"],
        )
    )

    # --- a decision that names nothing, so the rebuild report is not empty ----
    #
    # Subject-less, so it stays in the report rather than the wiki: integrity
    # information about the log is not record content.
    events.append(
        authoring.confirm(device, target="", ts=clock.ts(2, hour=8))
    )

    events.sort(key=lambda event: event.sort_key)
    return events
