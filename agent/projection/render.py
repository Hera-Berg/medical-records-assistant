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
from typing import Any, Iterable, Mapping, Sequence

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
_SENTENCE_END = ".?!"


class Sentence:
    """A sentence and the evidence it rests on. Refuses to exist without one."""

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

    def render(self) -> str:
        text = self.text
        if text[-1] not in _SENTENCE_END:
            text += "."
        seen: list[str] = []
        for citation in self.citations:
            if citation.key not in seen:
                seen.append(citation.key)
        return text + "".join(f"[^{key}]" for key in seen)


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
        self.front.append((key, value))

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
        self.body.append(" ".join(sentence.render() for sentence in sentences))

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
        _assert_every_sentence_cited(text)
        return text.encode("utf-8")


#: Lines that are not prose and are exempt from the citation rule.
_EXEMPT = re.compile(r"^(?:---|#|\[\^|\s*$|\||>)")


def _assert_every_sentence_cited(text: str) -> None:
    """Belt and braces over the structural guarantee in :class:`Sentence`.

    :class:`Sentence` already makes an uncited sentence unconstructable. This
    re-checks the rendered bytes, so a future edit that appends a line to
    ``body`` directly cannot quietly reintroduce uncited prose.
    """
    in_front = False
    for number, line in enumerate(text.split("\n"), start=1):
        if line == "---":
            in_front = not in_front
            continue
        if in_front or _EXEMPT.match(line):
            continue
        if "[^" not in line:
            raise ProjectionError(
                f"line {number} of a wiki page has no citation: {line!r}. Every "
                f"sentence must point at the artefact or event behind it."
            )
