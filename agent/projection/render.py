"""Markdown out. Frontmatter is the machine-readable state, prose is for people.

Two rules are enforced here rather than hoped for.

**Every sentence carries a citation.** ``CLAUDE.md``: "A sentence in the wiki
without a footnote is a bug." A :class:`Sentence` cannot be constructed without
at least one citation, so an uncited sentence is a ``ProjectionError`` at build
time rather than a plausible-looking line in a medical record. The alternative —
writing prose freely and checking it afterwards with a regex — fails open the one
time the regex is wrong.

**Bytes are fully determined by the arguments.** Frontmatter keys are emitted in
a fixed order, lists are sorted by code point, dates come from
:mod:`.dates` rather than ``strftime`` ``%B``, and the result is encoded UTF-8
with ``\\n`` endings by the caller. Nothing here reads the clock, the locale, or
the environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Container, Iterable, Mapping, Sequence

from ..errors import ProjectionError
from .citations import Citation

#: Characters that force a YAML scalar to be quoted, plus the words that would
#: otherwise parse as something other than a string.
_YAML_SPECIAL = re.compile(
    r"""^[\s]|[\s]$|^[-?:,\[\]{}#&*!|>'"%@`]|: |\s#|[\n\r\t"]"""
)
_YAML_LOOKS_TYPED = re.compile(
    r"^(?:true|false|null|~|yes|no|on|off|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)$",
    re.IGNORECASE,
)
_SENTENCE_END = ".?!\u2026"
#: Closing marks that may sit *after* the full stop. A sentence ending in a
#: quotation — a transcript on the timeline, a label's own wording on an entity
#: page — is already terminated; appending a second stop after the quote gives
#: ``changed.\u201d.``, which is the kind of thing a reader notices and no
#: assertion was ever going to catch.
_CLOSERS = "\u201d\u2019\"')]\u00bb"


class Sentence:
    """A sentence and the evidence it rests on. Refuses to exist without one.

    Carrying its citations is not the same as printing them. Where a sentence
    ends up is what decides how its markers are written: a bullet prints its
    own, and a paragraph prints the markers once for the whole paragraph when
    every sentence in it rests on the same source. See :func:`_paragraph_line`.
    """

    __slots__ = ("text", "citations")

    def __init__(self, text: str, citations: Sequence[Citation]):
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ProjectionError("a sentence with no text cannot be rendered")
        if not citations:
            raise ProjectionError(
                f"refusing to render an uncited sentence: {cleaned!r}. Every sentence in "
                f"the wiki points at the artefact or event it came from."
            )
        self.text = cleaned
        self.citations = tuple(citations)

    @property
    def keys(self) -> tuple[str, ...]:
        """The footnote keys behind this sentence, deduplicated, in order."""
        seen: list[str] = []
        for citation in self.citations:
            if citation.key not in seen:
                seen.append(citation.key)
        return tuple(seen)

    def body(self) -> str:
        """The prose, terminated, with no markers."""
        return terminate(self.text)

    def markers(self, exclude: Container[str] = ()) -> str:
        return "".join(f"[^{key}]" for key in self.keys if key not in exclude)

    def render(self) -> str:
        return self.body() + self.markers()


def terminate(text: str) -> str:
    """*text* with a full stop, unless it already ends in one.

    Looks *through* a closing quote for the terminator, so a sentence ending in
    a quotation is not given a second stop outside it — ``changed.".`` is the
    kind of thing a reader notices immediately and no assertion was ever going
    to catch. Exported because two sentences are sometimes joined into one
    bullet, and the first of them has to be finished before the second starts.
    """
    if not text:
        return text
    terminal = text.rstrip(_CLOSERS)
    if not terminal or terminal[-1] not in _SENTENCE_END:
        return text + "."
    return text


def _paragraph_line(sentences: Sequence[Sentence]) -> str:
    """One paragraph, with every footnote marker written exactly once.

    ``CLAUDE.md`` requires that every claim be attributable, not that every full
    stop carry a marker. Three consecutive sentences off one script used to
    print the same marker three times, which teaches a reader to ignore all of
    them and so costs the citations the attention they are there to get.

    So: where the whole paragraph rests on the same sources, it is marked once,
    at the end. Markers are written per sentence only where the sources
    genuinely differ within the paragraph, and even then a key already written
    earlier in the paragraph is not written again.
    """
    if len({frozenset(sentence.keys) for sentence in sentences}) == 1:
        return " ".join(s.body() for s in sentences) + sentences[0].markers()
    parts: list[str] = []
    written: set[str] = set()
    for sentence in sentences:
        parts.append(sentence.body() + sentence.markers(exclude=written))
        written.update(sentence.keys)
    return " ".join(parts)


def quote_yaml(value: str) -> str:
    """Quote a YAML scalar only where it would otherwise change meaning."""
    if value == "" or _YAML_SPECIAL.search(value) or _YAML_LOOKS_TYPED.match(value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
        return f'"{escaped}"'
    return value


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):  # pragma: no cover - values never carry floats
        raise ProjectionError(
            "refusing to write a float into the wiki; use Decimal or the literal span"
        )
    if isinstance(value, str):
        return quote_yaml(value)
    raise ProjectionError(f"{type(value).__name__} cannot be written to frontmatter")


def frontmatter(pairs: Iterable[tuple[str, Any]]) -> list[str]:
    """Render ordered key/value pairs as a YAML frontmatter block.

    Supports exactly what the wiki needs: scalars, flow lists of scalars, and a
    block sequence of flat mappings. A small explicit writer rather than a YAML
    library, because these bytes have to be identical on every machine that
    rebuilds them and because it adds no dependency to a project that has none.
    """
    lines = ["---"]
    for key, value in pairs:
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            if all(isinstance(item, Mapping) for item in value):
                lines.append(f"{key}:")
                for item in value:
                    first = True
                    for name in item:
                        prefix = "  - " if first else "    "
                        lines.append(f"{prefix}{name}: {_render_inline(item[name])}")
                        first = False
            else:
                lines.append(f"{key}: [{', '.join(_scalar(item) for item in value)}]")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return lines


def _render_inline(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return f"[{', '.join(_scalar(item) for item in value)}]"
    return _scalar(value)


@dataclass
class Document:
    """A wiki page under construction, and the footnotes it has accumulated."""

    front: list[tuple[str, Any]] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    _citations: dict[str, Citation] = field(default_factory=dict)

    def field_(self, key: str, value: Any) -> None:
        """Add one frontmatter key.

        Refuses a duplicate. Frontmatter is the machine-readable canonical state,
        and a repeated key makes it ambiguous — a YAML reader takes one of the two
        and every reader may take a different one. This is caught here rather than
        left to a caller's discipline because the page builds its fields from two
        places, a loop over the entity's predicates and a list of dedicated
        fields, and a predicate named like a dedicated field is a collision
        nothing else would notice.
        """
        if any(existing == key for existing, _ in self.front):
            raise ValueError(
                f"frontmatter key {key!r} written twice; the canonical state cannot "
                f"say two things"
            )
        self.front.append((key, value))

    def optional_field(self, key: str, value: Any) -> None:
        """Add one frontmatter key, or nothing at all where there is no value.

        ``started: null`` asserts nothing that omitting the key does not, and on
        five medication pages out of six it is a line a reader learns to skip —
        which is a cost, because the keys around it are load-bearing.

        The event log's rule is the opposite, deliberately: an explicit ``null``
        there separates "we considered this and do not know" from "nobody ever
        asked", and that distinction is what stops one timestamp being quietly
        filled in from another. Frontmatter is derived from the log and has no
        such distinction to carry — it is a projection of what is known, so what
        is not known is simply absent.
        """
        if value is None:
            return
        self.field_(key, value)

    def heading(self, text: str, level: int = 2) -> None:
        if self.body and self.body[-1] != "":
            self.body.append("")
        self.body.append(f"{'#' * level} {text}")
        self.body.append("")

    def paragraph(self, *sentences: Sentence) -> None:
        if not sentences:
            return
        for sentence in sentences:
            self._remember(sentence)
        if self.body and self.body[-1] != "":
            self.body.append("")
        self.body.append(_paragraph_line(sentences))

    def bullet(self, sentence: Sentence, prefix: str = "") -> None:
        self._remember(sentence)
        text = sentence.render()
        self.body.append(f"- {prefix}{text}" if prefix else f"- {text}")

    def blank(self) -> None:
        if self.body and self.body[-1] != "":
            self.body.append("")

    def _remember(self, sentence: Sentence) -> None:
        for citation in sentence.citations:
            self._citations.setdefault(citation.key, citation)

    def render(self) -> bytes:
        """The finished page. UTF-8, ``\\n`` endings, one trailing newline.

        Never written through text mode anywhere: on Windows that would
        translate every ending to CRLF and a rebuild would differ from the same
        rebuild on Linux, for no reason visible in the log.
        """
        lines = frontmatter(self.front)
        lines.append("")
        lines.extend(self.body)
        if self._citations:
            if lines and lines[-1] != "":
                lines.append("")
            for key in sorted(self._citations):
                lines.append(self._citations[key].definition())
        while lines and lines[-1] == "":
            lines.pop()
        text = "\n".join(lines) + "\n"
        _assert_citations_are_sound(text)
        return text.encode("utf-8")


#: Lines that are not prose and are exempt from the citation rule.
_EXEMPT = re.compile(r"^(?:---|#|\[\^|\s*$|\||>)")

#: A footnote reference, as opposed to a footnote definition at the line start.
_MARKER = re.compile(r"\[\^([^\]]+)\](?!:)")


def _assert_citations_are_sound(text: str) -> None:
    """Belt and braces over the structural guarantee in :class:`Sentence`.

    Two properties, both about the rendered bytes rather than the objects that
    produced them, so that a future edit appending straight to ``body`` cannot
    quietly break either.

    **Every line of prose is attributed.** :class:`Sentence` already makes an
    uncited sentence unconstructable; this re-checks the output.

    **No line repeats a marker.** A paragraph that prints the same footnote
    three times is attributing nothing the first marker did not already
    attribute, and it costs the reader's attention to every other citation on
    the page. :func:`_paragraph_line` places markers so this holds; the check is
    here so it stays holding.
    """
    in_front = False
    for number, line in enumerate(text.split("\n"), start=1):
        if line == "---":
            in_front = not in_front
            continue
        if in_front or _EXEMPT.match(line):
            continue
        keys = _MARKER.findall(line)
        if not keys:
            raise ProjectionError(
                f"line {number} of a wiki page has no citation: {line!r}. Every "
                f"sentence must point at the artefact or event behind it."
            )
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ProjectionError(
                f"line {number} of a wiki page cites {', '.join(repeated)} more than "
                f"once: {line!r}. One marker attributes the whole paragraph; repeating "
                f"it teaches the reader to ignore all of them."
            )
