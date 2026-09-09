"""Subject identifiers, and the path they turn into.

A claim names what it is about with a subject like ``med:perindopril``. That
string decides which file under ``wiki/`` gets written, so it is the point where
an event payload — an ordinary line of text that a person can hand-edit and a
sync client can corrupt — reaches the filesystem. It is validated with the same
suspicion the raw store applies to recorded paths: a subject that would climb out
of ``wiki/`` is refused rather than sanitised, because a sanitised subject is a
claim silently filed under the wrong name.

The four kinds are the four entity directories the storage layout names. A claim
about anything else is not filed as an entity; low-consequence material like a
meal photo or a weight reading belongs to the timeline, which is where the spec
puts it ("auto-applies, reversible from the timeline").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

WIKI_DIRNAME = "wiki"

#: Subject kind -> the directory under ``wiki/`` it is filed in.
KIND_DIRS: dict[str, str] = {
    "med": "medications",
    "allergy": "allergies",
    "problem": "problems",
    "person": "people",
}

#: Deliberately narrow: lowercase, digits and internal hyphens. It excludes
#: ``.``, ``/`` and ``\`` outright, so ``med:../../etc/passwd`` cannot parse at
#: all rather than being cleaned up into something that looks reasonable.
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

_KIND_RE = re.compile(r"^[a-z]{1,16}$")


@dataclass(frozen=True)
class Subject:
    """A parsed, filesystem-safe subject identifier."""

    kind: str
    slug: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.slug}"

    @property
    def directory(self) -> str:
        return KIND_DIRS[self.kind]

    @property
    def rel_path(self) -> str:
        """Where this entity's file lives, relative to the vault root."""
        return str(PurePosixPath(WIKI_DIRNAME) / self.directory / f"{self.slug}.md")

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.id


def parse(value: object) -> Subject | None:
    """Parse ``kind:slug``, or return ``None``. Never raises, never repairs."""
    if not isinstance(value, str):
        return None
    kind, sep, slug = value.partition(":")
    if not sep:
        return None
    if not _KIND_RE.match(kind) or kind not in KIND_DIRS:
        return None
    if not _SLUG_RE.match(slug):
        return None
    return Subject(kind=kind, slug=slug)


#: Characters a name may contribute to a slug, once folded to ASCII.
_SLUGGABLE = re.compile(r"[^a-z0-9]+")

#: Accented Latin folded to ASCII by an explicit table. Never `unicodedata`
#: normalisation plus a strip: that silently deletes scripts it cannot fold, so
#: a name in Greek or Cyrillic would become an empty slug rather than a refusal.
_FOLD = str.maketrans(
    {
        "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a", "å": "a", "ā": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e", "ē": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i", "ī": "i",
        "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o", "ø": "o", "ō": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u", "ū": "u",
        "ç": "c", "ñ": "n", "ß": "ss", "æ": "ae", "œ": "oe",
        "'": "", "\u2019": "",
    }
)


def slugify(name: object) -> str | None:
    """A slug from a name a source wrote, or ``None`` if there is nothing usable.

    Used where a claim names its subject in words — "Perindopril", "Dr Nguyen" —
    and an id has to be derived. Deterministic and ASCII-explicit, because the
    slug becomes a filename and two machines rebuilding the same log must land on
    the same one.

    Returns ``None`` rather than a fallback. A name this cannot render is a claim
    that gets rejected and reported, not one filed under a slug nobody can
    connect back to the word on the page.
    """
    if not isinstance(name, str):
        return None
    folded = _SLUGGABLE.sub("-", name.strip().lower().translate(_FOLD)).strip("-")
    if not folded:
        return None
    # The id length limit lives in `_SLUG_RE`; truncating to fit would map two
    # different long names onto one entity, so an over-long name is refused.
    return folded if _SLUG_RE.match(folded) else None


def make_id(kind: object, name: object) -> str | None:
    """``("med", "Perindopril")`` -> ``"med:perindopril"``, or ``None``."""
    if not isinstance(kind, str) or kind not in KIND_DIRS:
        return None
    slug = slugify(name)
    return f"{kind}:{slug}" if slug else None


def describe_rejection(value: object) -> str:
    """Why :func:`parse` said no, in words a person can act on."""
    if not isinstance(value, str):
        return f"subject must be a string of the form kind:slug, got {type(value).__name__}"
    kind, sep, slug = value.partition(":")
    if not sep:
        return f"subject {value!r} has no kind prefix; expected one of {_kinds()}"
    if kind not in KIND_DIRS:
        return f"subject {value!r} has unknown kind {kind!r}; expected one of {_kinds()}"
    return (
        f"subject {value!r} has an unusable slug {slug!r}; slugs are lowercase letters, "
        f"digits and internal hyphens, which is what keeps a subject inside wiki/"
    )


def _kinds() -> str:
    return ", ".join(sorted(KIND_DIRS))


#: Human-readable words that are never capitalised mid-name when a display name
#: is derived from a slug. Fixed table, not a locale lookup.
_TITLE_EXCEPTIONS = {"and", "of", "the"}


def display_name(subject: Subject) -> str:
    """A fallback display name derived from the slug.

    Used only when no claim states a name. Case handling is ASCII-explicit
    rather than ``str.title()`` so that the result cannot vary with anything
    outside this function.
    """
    words = [w for w in subject.slug.split("-") if w]
    if not words:
        return subject.slug
    rendered = []
    for i, word in enumerate(words):
        if i > 0 and word in _TITLE_EXCEPTIONS:
            rendered.append(word)
        else:
            rendered.append(word[0].upper() + word[1:])
    return " ".join(rendered)
