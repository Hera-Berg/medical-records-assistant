"""Salt and ester variants of one drug, as an explicit table.

A pharmacy label names the salt: ``LEVOTHYROXINE SODIUM``, ``METFORMIN
HYDROCHLORIDE``, ``PERINDOPRIL ARGININE``. The model reads what is on the page,
which is correct, and the subject it mints from that name is a different id from
the one a plainer label produces. Left alone that gives one entity per label
wording — a medication list showing each drug twice, and, worse, **staleness
computed on each half separately**, so one copy ages to `stale` while the other
stays `active` and nothing catches a medication that was genuinely dropped.

So variants alias onto the base drug here, in the projection, beside confirmed
merges: an improved table is picked up by ``health-agent rebuild`` with no
re-extraction and no model call, and the event log keeps exactly what the label
said. See CLAUDE.md, "Salt variants alias in the projection".

## Why this is a table and not a regex

The obvious implementation is to strip a trailing salt word. **Do not do that.**
It is wrong, and the case that makes it wrong is one of the three commonest
salt-bearing scripts in Australia:

    perindopril arginine 5 mg  ==  perindopril erbumine 4 mg

The salt changes the number. Blanket stripping puts both readings on one entity
where they either surface as a dose conflict that is not real, or — worse —
rank against each other so that whichever sorts higher silently becomes the
dose. Each pair below is a judgement that *this* drug's salts are the same
therapy at the same numbers, or that they are not and the difference has to stay
visible.

Two consequences of taking that seriously, both deliberate:

**Some variants are absent on purpose.** ``metoprolol tartrate`` and
``metoprolol succinate`` are not in the table. They are not two labels for one
therapy — tartrate is immediate release and succinate is modified release, they
are dosed differently and are not interchangeable, so two entities is the
*correct* answer and aliasing them would be a clinical error dressed as tidying.
Absence from this table is a decision, not an oversight.

**The salt is still rendered, even where the pair is aliased.** Perindopril and
naproxen are both in the table and both have salts whose strengths differ, so
their entity pages print which salt each reading came off (see
:func:`salt_of`). Two prescriber-issued readings that disagree on the number then
surface as ``conflicted`` with both salts named — a false alarm a person clears
in one tap, which is the outcome CLAUDE.md's rule 3 asks for and is far better
than showing 4 mg to somebody taking 5 mg.

If a later change makes this look like it wants to be
``re.sub(r'\\s+(sodium|hydrochloride|...)$', '', name)``: that refactor is the
bug this module exists to prevent, and the two paragraphs above are why.

## Scope

Medications only. ``allergy:sulfonamides`` and ``problem:hypothyroidism`` are
not drug products and nothing here touches them.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import subjects
from .subjects import Subject

#: ``(as a label writes it, the drug it is)``. The variant must begin with the
#: base name and a space: the salt is read off as the remainder, so the two
#: columns cannot drift apart, and a pair that is not a suffix fails loudly at
#: import rather than aliasing something onto the wrong drug.
#:
#: Grouped by why each entry is safe. Anything whose salts are *not* one therapy
#: at one set of numbers belongs in the comment above, not in this tuple.
_PAIRS: tuple[tuple[str, str], ...] = (
    # Dosed as the salt, universally. "metformin 500mg" means the
    # hydrochloride; there is no other metformin to mean.
    ("metformin hydrochloride", "metformin"),
    ("levothyroxine sodium", "levothyroxine"),
    ("sertraline hydrochloride", "sertraline"),
    ("amitriptyline hydrochloride", "amitriptyline"),
    ("venlafaxine hydrochloride", "venlafaxine"),
    ("duloxetine hydrochloride", "duloxetine"),
    ("oxycodone hydrochloride", "oxycodone"),
    ("tramadol hydrochloride", "tramadol"),
    ("metoclopramide hydrochloride", "metoclopramide"),
    ("ondansetron hydrochloride", "ondansetron"),
    ("ciprofloxacin hydrochloride", "ciprofloxacin"),
    ("atorvastatin calcium", "atorvastatin"),
    ("rosuvastatin calcium", "rosuvastatin"),
    ("montelukast sodium", "montelukast"),
    ("pantoprazole sodium", "pantoprazole"),
    ("esomeprazole magnesium", "esomeprazole"),
    ("warfarin sodium", "warfarin"),
    ("diclofenac sodium", "diclofenac"),
    ("alendronate sodium", "alendronate"),
    ("quetiapine fumarate", "quetiapine"),
    ("escitalopram oxalate", "escitalopram"),
    ("amlodipine besilate", "amlodipine"),
    ("amlodipine besylate", "amlodipine"),
    ("salbutamol sulfate", "salbutamol"),
    ("morphine sulfate", "morphine"),
    # Hydrates. The water of crystallisation is not the drug and the strength is
    # always stated as the base.
    ("amoxicillin trihydrate", "amoxicillin"),
    ("cefalexin monohydrate", "cefalexin"),
    ("cephalexin monohydrate", "cephalexin"),
    ("azithromycin dihydrate", "azithromycin"),
    # Prodrug esters written as part of the name. Universally dosed as the
    # ester, and the bare name never means anything else.
    ("candesartan cilexetil", "candesartan"),
    ("olmesartan medoxomil", "olmesartan"),
    ("fesoterodine fumarate", "fesoterodine"),
    # Strengths differ between the salt and the base — naproxen 500mg is
    # naproxen sodium 550mg — so these alias to one entity *and* rely on
    # `salt_of` printing which salt each reading came off. Same reasoning as
    # perindopril: one page per drug, with the difference visible on it.
    ("naproxen sodium", "naproxen"),
    ("perindopril arginine", "perindopril"),
    ("perindopril erbumine", "perindopril"),
)

#: Kinds this applies to. A drug vocabulary has nothing to say about an allergen
#: or a diagnosis, and running it over them would be how "sulfonamides" quietly
#: became something else.
_KIND = "med"


@dataclass(frozen=True)
class Variant:
    """One row of the table, as slugs the projection can match on."""

    #: The slug a label's own wording mints — ``perindopril-arginine``.
    variant: str
    #: The slug it is filed under — ``perindopril``.
    base: str
    #: The salt itself — ``arginine``. Printed beside a reading so two salts of
    #: one drug are never compared as though they were the same preparation.
    salt: str


def _build() -> dict[str, Variant]:
    table: dict[str, Variant] = {}
    for written, base in _PAIRS:
        if not written.startswith(base + " "):
            raise ValueError(
                f"salt table: {written!r} does not begin with {base!r}, so the salt "
                f"cannot be read off as the remainder. Add an explicit salt column "
                f"rather than loosening this check — the two halves silently drifting "
                f"apart is how a reading gets filed under the wrong drug."
            )
        variant_slug = subjects.slugify(written)
        base_slug = subjects.slugify(base)
        if variant_slug is None or base_slug is None:
            raise ValueError(f"salt table: {written!r} -> {base!r} does not slugify")
        existing = table.get(variant_slug)
        if existing is not None and existing.base != base_slug:
            raise ValueError(
                f"salt table: {variant_slug} is mapped to both {existing.base} and "
                f"{base_slug}"
            )
        table[variant_slug] = Variant(
            variant=variant_slug, base=base_slug, salt=written[len(base) + 1 :].strip()
        )
    return table


VARIANTS: dict[str, Variant] = _build()

#: Base slugs the table knows. A base is never itself a variant — a two-step
#: alias would mean the table disagreed with itself.
BASES: frozenset[str] = frozenset(variant.base for variant in VARIANTS.values())


def variant_for(subject: Subject | None) -> Variant | None:
    """The table row for *subject*, or ``None`` if it names no known variant."""
    if subject is None or subject.kind != _KIND:
        return None
    return VARIANTS.get(subject.slug)


def alias_for(subject: Subject | None) -> str | None:
    """The subject id *subject* should be filed under, or ``None`` to leave it."""
    found = variant_for(subject)
    return f"{_KIND}:{found.base}" if found is not None else None


def salt_of(subject: Subject | None) -> str | None:
    """The salt a subject's own wording named — ``arginine`` — or ``None``.

    Read from the subject rather than stored on the claim, because the subject
    id *is* where the label's wording landed and a second copy could disagree
    with it. Rendered beside a reading whose entity was aliased, so a page never
    presents two salts of one drug as interchangeable numbers.
    """
    found = variant_for(subject)
    return found.salt if found is not None else None
