"""Composing the actual pages: one file per entity, one file per month.

Per-entity files rather than one big list, because that gives clean diffs and
per-fact citation granularity. "Current medications" is a generated view, not a
stored file — see :mod:`.views`.

The prose here is deliberately flat. This is a medical record: it reports what a
source said, names the source, and stops. It does not characterise a trend, rank
a risk, or suggest what anything means.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..events.envelope import parse_ts_or_none
from . import dates, entities as entities_mod, reconcile, timeline as timeline_mod
from .citations import Citation, Citer
from .entities import Entity
from .reconcile import Slot
from .render import Document, Sentence, terminate

#: Frontmatter keys the page writes itself. A predicate sharing one of these
#: names is rendered in the body only: the dedicated field is the normalised,
#: machine-readable one, and writing both would put the same key in twice.
_FRONTMATTER_SKIP = frozenset(
    {
        "id",
        "name",
        "status",
        "stale",
        "started",
        "last_confirmed",
        "expected_exhaustion",
        "evidence_tier",
        "reviewed",
        "conflicts",
        "merged_from",
        "merged_into",
        "sources",
        "stop_reported",
        "stop_reported_tier",
        "also_labelled",
    }
)


def _label(predicate: str) -> str:
    words = predicate.replace("_", " ").replace(".", " ").split()
    return " ".join(w[0].upper() + w[1:] if w else w for w in words[:1] + words[1:])


def _document_date(claim):
    """The date on the artefact itself — ``artifact_ts`` — as a plain date."""
    parsed = parse_ts_or_none(claim.artifact_ts) if claim.artifact_ts else None
    return parsed.date() if parsed else None


def _when_phrases(claim) -> list[str]:
    """When a claim is from, with each date saying which timestamp it is.

    The four timestamps never mean the same thing, so an unlabelled date in the
    body can be read against a footnote quoting a different one and look like a
    contradiction the page has no way to explain. Two prescriber-issued scripts
    both described as "5 August 2026" above footnotes reading "photographed 6
    August" and "photographed 7 August" are ``artifact_ts`` against
    ``captured_ts`` — both correct, and the page never said so.

    So ``artifact_ts`` is written here in the footnote's own words, "document
    dated", and ``occurred_at`` keeps the idiom the rest of the page already
    uses: "on" before an exact day, and nothing before a band, which
    :meth:`FuzzyDate.render` already opens with "around".

    ``occurred_at`` is dropped only where it is an exact day the document date
    already states. Never where it carries a band — the band is uncertainty the
    value itself does not hold, and losing it would be a timeline faking
    precision.

    Where there is no ``occurred_at`` but the source dated the thing in words,
    the **words go in the slot the date would have occupied**. Without that, an
    onset claim off a letter renders as "(patient-reported, document dated 1
    June 2026)" and a reader skimming it takes 1 June for the onset — on a page
    whose next section says the onset has no date at all. The phrase is both
    more honest and more informative than the gap it fills.
    """
    document = _document_date(claim)
    phrases: list[str] = []
    occurred = claim.occurred_at
    if occurred is not None and not (occurred.is_exact and occurred.value == document):
        phrases.append(
            f"on {occurred.render()}" if occurred.is_exact else occurred.render()
        )
    elif occurred is None and claim.occurred_span:
        phrases.append(f"dated only as \u201c{claim.occurred_span}\u201d")
    if document is not None:
        phrases.append(f"document dated {dates.render_date(document)}")
    return phrases


def _source_date_phrase(claim) -> str | None:
    """How a sentence about the source document names that document's date.

    ``artifact_ts`` only. A sentence saying "the source for this is dated ..."
    is a statement about the paper, and ``occurred_at`` is a statement about the
    thing the paper describes — substituting one for the other is the mismatch
    :func:`_when_phrases` exists to stop, and it does not become acceptable
    because a sentence would otherwise have no date in it. A claim with no
    document date simply does not get this clause.
    """
    document = _document_date(claim)
    if document is None:
        return None
    return f"a document dated {dates.render_date(document)}"


def _supply_source_phrase(claim) -> str:
    """What to call whatever the supply was counted from.

    A correction is not a source. It carries its target's evidence tier so that
    it can outrank a later re-extraction, but it is something the user typed,
    and a page calling it "the most recent prescriber-issued source" is
    attributing the user's own words to a prescriber.
    """
    if claim.is_correction:
        return "Your correction"
    return f"The most recent {claim.evidence_tier} source for this"


def _claim_phrase(claim, citer: Citer, extra: str | None = None) -> tuple[str, Citation]:
    """"5mg daily (prescriber-issued, document dated 4 June 2026)" and its footnote.

    The value is always the literal span the source used. The parenthesis says
    where it came from and when, so the tier is visible on every line without a
    reader having to hold the page's structure in their head.

    The salt joins that list where the source named one. Perindopril arginine
    5mg is the equivalent of perindopril erbumine 4mg, so two readings on one
    page can differ by a whole milligram and still describe the same therapy —
    and a reader has no way to tell unless each line says which salt it came
    off. This is what makes filing both under one drug safe.
    """
    citation = citer.cite(claim.cite, _correction_description(claim))
    qualifiers = [claim.evidence_tier]
    if claim.salt:
        qualifiers.append(f"labelled {claim.subject_literal}")
    qualifiers.extend(_when_phrases(claim))
    if extra:
        qualifiers.append(extra)
    return f"{claim.value.literal} ({', '.join(qualifiers)})", citation


def _correction_description(claim) -> str:
    return "Your correction" if claim.is_correction else "Recorded claim"


def _slot_bullet(document: Document, slot: Slot, citer: Citer) -> None:
    if slot.winner is not None:
        extra = (
            "unreviewed" if slot.review_state == reconcile.REVIEW_UNREVIEWED else None
        )
        phrase, citation = _claim_phrase(slot.winner, citer, extra)
        document.bullet(Sentence(f"**{_label(slot.predicate)}** — {phrase}", [citation]))
        return
    citations = [citer.cite(c.cite, _correction_description(c)) for c in slot.readings]
    document.bullet(
        Sentence(
            f"**{_label(slot.predicate)}** — not resolved: "
            f"{len(slot.readings)} sources of equal standing disagree",
            citations,
        )
    )


def _bare_entity_note(document: Document, entity: Entity, citer: Citer) -> None:
    """The whole body of a page whose only recorded fact is that it exists."""
    claims: list = []
    for predicate in sorted(entity.slots):
        claims.extend(entity.slots[predicate].supporting)
    if not claims:
        return
    citations = [citer.cite(c.cite, _correction_description(c)) for c in claims]
    document.paragraph(
        Sentence(
            "This entry is on the record by name, and nothing further about it has "
            "been recorded",
            citations,
        )
    )


def _stop_when_phrase(report) -> str:
    """" on 4 June 2026" or " around August 2026 (±10 days)", or nothing.

    "on" only before an exact day. :meth:`FuzzyDate.render` already opens a band
    with "around", and "on around August 2026" reads as a typo.
    """
    if report.when is None:
        return ""
    return (
        f" on {report.when.render()}"
        if report.when.is_exact
        else f" {report.when.render()}"
    )


def _supply_section(document: Document, entity: Entity, citer: Citer, as_of_date) -> None:
    """What the last script dispensed, and when that runs out.

    Three things this section must not do. It must not exist merely to report
    that it has nothing to report: a dose claim's own frequency reaches
    :class:`Dispense` as a fallback, and a "Supply" heading over the single word
    "daily" is noise on a page being skimmed — see :attr:`Dispense.has_spans`.

    It must not advertise a supply for a medication that has been stopped.
    ``expected_exhaustion`` is already dropped for a stopped entity, so the
    forward-looking sentences fall away on their own; what is left goes into the
    past tense, because the script and its quantities are history the record
    keeps rather than a supply anyone is still counting down.

    And where a stop was *reported* but not acted on, it must say so here rather
    than only below. Both sections were correct in isolation and the page was
    not: "the supply runs out 28 September 2026" sat above "you confirmed a
    patient-reported source saying this was stopped around August 2026" with
    nothing joining them, so a clinician skimming saw a live prescription and a
    stop notice that did not know about each other. The reconciliation rule is
    unchanged — the entry stays on the current list, because only a
    prescriber-issued or lab-issued source takes it off — but the arithmetic is
    stated for what it is: a count of what was prescribed, not a claim about what
    is being taken.
    """
    supply = entity.dispense
    claim = entity.dispense_claim
    if supply is None or claim is None or not supply.has_spans:
        return
    stopped = entity.status == entities_mod.STOPPED
    report = entity.stop_report
    citation = citer.cite(claim.cite, _correction_description(claim))
    document.heading("Supply")
    sentences = []

    dated = _source_date_phrase(claim)
    source = _supply_source_phrase(claim)
    records = "recorded" if stopped else "records"
    was = "was" if stopped else "is"
    if dated is not None:
        sentences.append(Sentence(f"{source} {was} {dated}", [citation]))
        # "It" refers to the document named in the sentence above. Without that
        # sentence the subject has to be restated rather than left dangling.
        source = "It"

    spans = supply.describe()
    days = supply.days_supply
    if days is not None and spans:
        sentences.append(
            Sentence(
                f"{source} {records} {spans}, which {was} {days} days of supply",
                [citation],
            )
        )
    elif supply.unreadable and not stopped:
        # Suppressed once stopped: the reason there is no exhaustion date is the
        # stop, which the page states above with its own citation, and offering a
        # second reason invites a reader to weigh them against each other.
        sentences.append(
            Sentence(
                f"No expected exhaustion date is recorded here because "
                f"{supply.unreadable}",
                [citation],
            )
        )

    if entity.expected_exhaustion is not None:
        # The qualifier is not hedging. A reported stop means the count below is
        # of tablets dispensed, and whether they were swallowed is exactly what
        # the patient has told the record they were not.
        qualifier = "" if report is None else ", if it is still being taken"
        sentences.append(
            Sentence(
                f"On that reading the supply runs out "
                f"{entity.expected_exhaustion.render()}{qualifier}",
                [citation],
            )
        )
    if entity.stale and entity.last_confirmed is not None:
        elapsed = entities_mod.elapsed_phrase(entity.last_confirmed.value, as_of_date)
        sentences.append(
            Sentence(
                f"Nothing since has confirmed it, so this entry is marked stale — last "
                f"confirmed {elapsed}",
                [citation],
            )
        )
    if report is not None:
        # Cited to the stop, not to the script: this sentence is the patient's
        # statement, and attributing it to the prescribing document would be the
        # same substitution `_supply_source_phrase` refuses for corrections.
        sentences.append(
            Sentence(
                f"This counts what was prescribed, and you have since confirmed a "
                f"{report.tier} stop — see “Reported stopped” below",
                [citer.cite(report.claim.cite, _correction_description(report.claim))],
            )
        )
    document.paragraph(*sentences)


def _reported_stop_section(document: Document, entity: Entity, citer: Citer) -> None:
    """The patient said they stopped; the prescriber's record says otherwise.

    Both are stated, both are cited, and neither is resolved here. The patient is
    the authority on what they actually take and the prescriber is the authority
    on what was prescribed, so a clinician reading both learns something that
    either one alone would hide.
    """
    report = entity.stop_report
    if report is None:
        return
    document.heading("Reported stopped")
    citation = citer.cite(report.claim.cite, _correction_description(report.claim))
    when = _stop_when_phrase(report)
    document.paragraph(
        Sentence(
            f"You confirmed a {report.tier} source saying this was stopped{when}",
            [citation],
        ),
        Sentence(
            f"It is recorded here and the entry stays on the current list as "
            f"`{entity.status}`, because only a prescriber-issued or lab-issued source "
            f"can take a medication off that list",
            [citation],
        ),
    )


def _salt_names_section(document: Document, entity: Entity, citer: Citer) -> None:
    """"The label read X, filed under Y", cited to the label that said it.

    An aliased variant gets no page of its own — see CLAUDE.md, "An aliased
    entity gets no stub page" — so this section and the artefact citation are
    the whole of the disclosure. Normalisation the reader cannot see is
    indistinguishable from the record having lost the wording.

    Naming the salt is not pedantry. Salt choice can change the number:
    perindopril arginine 5mg is the equivalent of perindopril erbumine 4mg, so a
    reader comparing two doses on this page has to be able to see that they came
    off differently salted products.
    """
    if not entity.salt_names:
        return
    document.heading("Names on sources")
    for name in entity.salt_names:
        citation = citer.cite(name.claim.cite, _correction_description(name.claim))
        document.bullet(
            Sentence(
                f"A source labelled this \u201c{name.literal}\u201d — the {name.salt} "
                f"salt — and it is filed here under `{entity.id}`, so the drug has one "
                f"entry rather than one per label",
                [citation],
            )
        )


def _conflicts_section(document: Document, entity: Entity, citer: Citer) -> None:
    conflicts = entity.conflicts
    if not conflicts:
        return
    document.heading("Conflicting sources")
    for slot in conflicts:
        citations = [citer.cite(c.cite, _correction_description(c)) for c in slot.readings]
        document.paragraph(
            Sentence(
                f"{len(slot.readings)} sources of equal standing disagree about the "
                f"{_label(slot.predicate).lower()}, and neither has been chosen",
                citations,
            )
        )
        document.blank()
        for claim in slot.readings:
            phrase, citation = _claim_phrase(claim, citer)
            document.bullet(Sentence(phrase, [citation]))


def _dateable_paragraphs(document: Document, entity: Entity, citer: Citer) -> None:
    """Claims whose source said *when* in words rather than with a date.

    The phrase is printed. That is the point of the rule: "around Easter" is
    real evidence, and a record that dropped it because it could not be parsed
    would lose something it exists to keep. What is not printed is a resolved
    date — the computus is deterministic but the year is a guess, and the
    candidate lives in the review queue where a person confirms it, not here
    where it would read as established.
    """
    for item in entity.review:
        if item.kind != reconcile.DATEABLE:
            continue
        for claim in item.claims:
            citation = citer.cite(claim.cite, _correction_description(claim))
            document.paragraph(
                Sentence(
                    f"The {_label(claim.predicate).lower()} is dated only as "
                    f"\u201c{claim.occurred_span}\u201d, which is not a date this "
                    f"record can place on a timeline; it is waiting for you to say "
                    f"when",
                    [citation],
                )
            )


def _merge_paragraphs(document: Document, entity: Entity, citer: Citer) -> None:
    """Merges proposed against this entity, waiting on a decision.

    Nothing emits :data:`reconcile.MERGE_PROPOSED` yet — a brand-name proposer
    is phase 7 work, and salt variants are handled by the table without a tap.
    The branch is here so the page already accounts for the kind: a review item
    the queue counts and no page renders is a user act with nowhere to be seen,
    which is the failure rule 3 exists to stop.

    Like a pending change, it states that a decision is waiting and not what the
    decision would do. A merge is an assertion that two entries are one thing,
    and printing it as though it had happened is exactly the confusion the tap
    is there to resolve.
    """
    for item in entity.review:
        if item.kind != reconcile.MERGE_PROPOSED:
            continue
        citations = [citer.cite(c.cite, _correction_description(c)) for c in item.claims]
        if not citations and item.cite:
            citations = [citer.cite(item.cite, "Proposed merge")]
        document.paragraph(Sentence(item.summary, citations))


def _review_section(document: Document, entity: Entity, citer: Citer) -> None:
    contradictions = entity.contradictions
    pending = [
        item for item in entity.review if item.kind == reconcile.AWAITING
    ]
    dateable = [item for item in entity.review if item.kind == reconcile.DATEABLE]
    merges = [item for item in entity.review if item.kind == reconcile.MERGE_PROPOSED]
    if not contradictions and not pending and not dateable and not merges:
        return
    document.heading("Needs review")

    for slot in contradictions:
        winner = slot.winner
        winner_citation = citer.cite(winner.cite, _correction_description(winner))
        disagree_citations = [
            citer.cite(c.cite, _correction_description(c)) for c in slot.contradicted_by
        ]
        document.paragraph(
            Sentence(
                f"A later reading disagrees with your correction of the "
                f"{_label(slot.predicate).lower()}; your correction stands until you "
                f"decide otherwise",
                [winner_citation] + disagree_citations,
            )
        )
        document.blank()
        for claim in slot.contradicted_by:
            phrase, citation = _claim_phrase(claim, citer)
            document.bullet(Sentence(f"read instead as {phrase}", [citation]))

    for item in pending:
        # Deliberately does not state the proposed value. A high-consequence
        # change has not been accepted, and a page that prints it beside the
        # current one invites it to be read as current.
        citations = [citer.cite(c.cite, _correction_description(c)) for c in item.claims]
        document.paragraph(
            Sentence(
                f"A proposed change to the {_label(item.predicate).lower()} is waiting "
                f"for you to review it in the inbox",
                citations,
            )
        )

    _merge_paragraphs(document, entity, citer)
    _dateable_paragraphs(document, entity, citer)


def _replacement_phrase(slot: Slot, citer: Citer) -> tuple[str, list[Citation]]:
    """"replaced by your correction to 5mg daily", and the footnote for it.

    Naming the replacement is what makes the history auditable rather than
    merely present: "what did I correct, and from what" needs both halves on the
    same line, each pointing at its own source.
    """
    winner = slot.winner
    if winner is None:
        return "superseded, and no value currently stands in its place", []
    citation = citer.cite(winner.cite, _correction_description(winner))
    if winner.is_correction:
        return f"replaced by your correction to {winner.value.literal}", [citation]
    phrase, _ = _claim_phrase(winner, citer)
    return f"replaced by {phrase}", [citation]


def _anomalies_section(document: Document, entity: Entity, citer: Citer) -> None:
    """Anomalies that resolve to this entity.

    Stated flatly and cited, like everything else here. An anomaly is a fact
    about the log — a payload that disagreed with the code, a correction whose
    target is missing — and it says what the record did about it, never what the
    reader should conclude.
    """
    if not entity.anomalies:
        return
    document.heading("Anomalies")
    for note in entity.anomalies:
        citations = [citer.cite(note.cite, "Recorded claim")] if note.cite else []
        document.bullet(Sentence(str(note), citations))


def _is_restatement(claim, winner) -> bool:
    """Whether a superseded reading says exactly what replaced it.

    A repeat script stating the dose the last one stated has not corrected
    anything — it corroborates, and the `sources:` list already records that it
    exists. Printing it under "Earlier readings" as "5mg daily, replaced by 5mg
    daily" tells a reader a change happened where none did, and teaches them to
    skim the one section whose whole job is to make a real change auditable.

    Salt is part of the comparison, deliberately. Two readings of one number off
    differently salted products are *not* the same statement — perindopril
    arginine 5mg and erbumine 5mg are different therapies — so those stay
    printed even though their values match.
    """
    if winner is None or winner.is_correction:
        return False
    return claim.value.agrees_with(winner.value) and claim.salt == winner.salt


def _history_section(document: Document, entity: Entity, citer: Citer) -> None:
    rows: list[tuple[str, list[Citation]]] = []
    for predicate in sorted(entity.slots):
        slot = entity.slots[predicate]
        replacement, replacement_citations = _replacement_phrase(slot, citer)
        for claim in slot.superseded:
            if _is_restatement(claim, slot.winner):
                continue
            phrase, citation = _claim_phrase(claim, citer)
            rows.append(
                (
                    f"**{_label(predicate)}** — {phrase}, {replacement}",
                    [citation] + replacement_citations,
                )
            )
    if not rows:
        return
    document.heading("Earlier readings")
    for text, citations in rows:
        document.bullet(Sentence(text, citations))


def entity_page(entity: Entity, citer: Citer, as_of_date) -> bytes:
    """Render one entity file."""
    document = Document()
    document.field_("id", entity.id)
    document.field_("name", entity.name)

    if entity.is_stub:
        document.field_("merged_into", entity.merged_into)
        # Cite the merge decision itself. A merge is an event the user made, not
        # something an artefact said, and it is reversible — the footnote points
        # at the line in the log that can be reverted.
        citation = citer.cite(f"ev-{entity.merge_event}", "Your merge decision")
        document.paragraph(
            Sentence(
                f"This entry was merged into `{entity.merged_into}` and is kept so that "
                f"older citations and filenames still resolve",
                [citation],
            )
        )
        return document.render()

    document.field_("status", entity.status)
    if entity.stale:
        document.field_("stale", True)
    if entity.salt_names:
        # The label's own words, in the machine-readable half of the page. An
        # aliased variant has no page of its own, so a reader searching the
        # folder for "perindopril arginine" has to be able to find it here.
        document.field_(
            "also_labelled", [name.literal for name in entity.salt_names]
        )
    if entity.stop_report is not None:
        # Beside the status, never instead of it. The medication is still on the
        # list and the frontmatter says so; this says the patient reported
        # otherwise, and a reader gets both facts without having to open the log.
        document.field_("stop_reported", entity.stop_report.iso)
        document.field_("stop_reported_tier", entity.stop_report.tier)

    for predicate in sorted(entity.slots):
        if predicate in _FRONTMATTER_SKIP:
            continue
        slot = entity.slots[predicate]
        if slot.winner is not None:
            document.field_(predicate, slot.winner.value.literal)

    # Absent keys are omitted, not written as null: see `Document.optional_field`.
    # `expected_exhaustion` needs no kind guard now — only a medication ever has
    # one, and a stopped medication no longer has one at all.
    document.optional_field("started", entity.started.iso if entity.started else None)
    document.optional_field(
        "last_confirmed", entity.last_confirmed.iso if entity.last_confirmed else None
    )
    document.optional_field(
        "expected_exhaustion",
        entity.expected_exhaustion.iso if entity.expected_exhaustion else None,
    )
    if entity.evidence_tier:
        document.field_("evidence_tier", entity.evidence_tier)

    unreviewed = any(
        slot.review_state == reconcile.REVIEW_UNREVIEWED for slot in entity.slots.values()
    )
    document.field_("reviewed", "unreviewed" if unreviewed else "confirmed")

    if entity.conflicts:
        document.field_(
            "conflicts",
            [
                {
                    "predicate": slot.predicate,
                    "readings": [c.value.literal for c in slot.readings],
                }
                for slot in entity.conflicts
            ],
        )
    if entity.merged_from:
        document.field_("merged_from", list(entity.merged_from))
    document.field_("sources", list(entity.sources))

    shown = [
        predicate
        for predicate in sorted(entity.slots)
        # `name` is the page's title and its frontmatter; repeating it as a
        # bullet says nothing. A "Status — stopped" bullet under a page whose
        # status field reads active is a flat contradiction, and the
        # reported-stop section below says the same thing with the context that
        # makes it true.
        if predicate != "name"
        and not (predicate == "status" and entity.stop_report is not None)
    ]
    if shown:
        document.heading("Current")
        for predicate in shown:
            _slot_bullet(document, entity.slots[predicate], citer)

    if not shown:
        # An entity whose only claim is its name would otherwise render as
        # frontmatter and nothing else — no prose, and no footnote, so the file
        # does not say where it came from. That is a page that has lost its
        # citation, which is the one thing every page must keep.
        _bare_entity_note(document, entity, citer)

    if entity.subject.kind == "med":
        _supply_section(document, entity, citer, as_of_date)
    _reported_stop_section(document, entity, citer)
    _salt_names_section(document, entity, citer)
    _conflicts_section(document, entity, citer)
    _review_section(document, entity, citer)
    _history_section(document, entity, citer)
    _anomalies_section(document, entity, citer)

    return document.render()


def timeline_page(month: str, rows: Sequence[timeline_mod.Row], citer: Citer) -> bytes:
    """Render one month of the timeline, newest first."""
    document = Document()
    document.field_("month", month)
    year, _, month_number = month.partition("-")
    document.field_("title", dates.render_month(int(year), int(month_number)))
    document.field_("entries", len(rows))

    current_heading: str | None = None
    for row in rows:
        if row.heading != current_heading:
            document.heading(row.heading, level=2)
            current_heading = row.heading
        citation = citer.cite(row.cite, row.cite_description)
        # The second sentence says where the artefact has got to, and it is
        # written into the file rather than only onto a screen: someone reading
        # the folder in five years has to be able to tell a document that was
        # read from one that was merely filed. It says *what*, never *why* — the
        # reason lives in a queue that is a disposable cache, and putting it
        # here would break the byte-identical rebuild the moment that cache is
        # deleted. See agent/projection/reading.py.
        line = f"**{row.marker}** — {row.text}"
        if row.reading_text:
            # The first sentence is finished before the second begins. A
            # transcript that ends in a closing quote leaves the two running
            # together otherwise: `"...tablets changed." Transcripts aren't`.
            line = f"{terminate(line)} {row.reading_text}"
        document.bullet(Sentence(line, [citation]))
    return document.render()


def month_range(months: Iterable[str]) -> list[str]:
    return sorted(set(months))
