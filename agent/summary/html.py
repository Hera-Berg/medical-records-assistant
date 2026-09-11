"""The sheet that is actually printed: standalone A4 HTML, no script, no server.

Two places this page is read, and it has to work in both:

* served at ``/print/{id}``, on a phone held up in a consulting room;
* opened from ``exports/`` by double-clicking it, with nothing running at all.

The second is why everything is inline. No stylesheet link, no font file, no
image, no script — not as a privacy measure here so much as because a file that
needs a server to look like itself is not an archival copy of anything. The only
difference between the two renderings is how a source links to its original:
relative into ``../raw/`` for the file, and ``/api/artifact/…`` for the served
page. Both point at the same bytes.

**No script, deliberately.** A print button would need one line of JavaScript
and is not worth it: browsers print with Ctrl+P, and a page with no script at
all is a page that cannot behave differently on paper than it did on screen.

The design rules are the project's, applied to paper rather than to a screen.
Black on white, no shadows and no gradients — a printer renders those as nothing
or as grey mud. Every evidence tier and every status is a **word**, because a
photocopy of this page in a folder is the reading that matters and hue does not
survive it. Three type sizes and two weights, and the smallest of them is the
source column, which is the only thing here a reader consults rather than reads.
"""

from __future__ import annotations

from html import escape

from .model import DEMO_WARNING, STANDFIRST, Line, Section, Summary
from .markdown import QUESTION_HEADING

#: How a source links to the document behind it.
#:
#: ``file`` is what the export carries: a path relative to ``exports/``, which
#: resolves inside the vault with no server and survives the folder being
#: copied, synced or restored. ``server`` is what ``/print/{id}`` serves, where
#: the same artefact is one route away.
FILE_LINKS = "file"
SERVER_LINKS = "server"

ARTIFACT_ROUTE = "/api/artifact/{short}"

#: 10.5pt body on A4 with a 13mm margin. The margin is the printer's safe area
#: on almost every consumer laser; going narrower risks the source column being
#: clipped on exactly the sheet someone hands over.
STYLE = """
:root { color-scheme: light; }
@page { size: A4; margin: 13mm; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #e9eae6;
  color: #000;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, "Noto Sans", sans-serif;
  font-size: 10.5pt;
  line-height: 1.35;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
.sheet {
  background: #fff;
  /* A whole sheet of A4, padding included, so the text column on screen is the
     same 184mm it will be on paper. At 184mm the padding came out of the
     content instead and the column was 26mm narrower than the print view —
     enough to wrap the year of every source onto its own line in the one place
     this file is most often read, which is somebody double-clicking it in a
     folder. */
  max-width: 210mm;
  margin: 8mm auto;
  padding: 13mm;
}
h1 { font-size: 15pt; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
h2 {
  font-size: 10.5pt;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 3.5mm 0 0.8mm;
  padding-bottom: 0.6mm;
  border-bottom: 1px solid #000;
}
p { margin: 0; }
.masthead { display: flex; align-items: baseline; gap: 6mm; }
.for { margin-left: auto; font-weight: 600; white-space: nowrap; }
.dateline { margin-top: 1mm; }
.small { font-size: 8.75pt; }
.muted { color: #454545; }
.warning {
  border: 1.6pt solid #000;
  padding: 2mm 2.5mm;
  margin: 3mm 0 0;
  font-weight: 600;
}
.rule { border: 0; border-top: 1px solid #000; margin: 3mm 0 0; }
table { width: 100%; border-collapse: collapse; }
tr { page-break-inside: avoid; break-inside: avoid; }
td {
  vertical-align: top;
  padding: 0.75mm 3mm 0.75mm 0;
  border-bottom: 1px solid #d2d4ce;
}
tr:last-child td { border-bottom: 0; }
/* The source column is sized to hold "Prescription · 2 September 2026" on one
   line. At 24% it was a millimetre short, which broke the year onto a second
   line on every row of a long medication list and doubled the height of the
   one section that may never be shortened. */
td.name { width: 24%; font-weight: 600; }
td.said { width: 49%; }
td.src { width: 27%; padding-right: 0; text-align: right; }
.alt { font-style: normal; }
.or { font-variant: small-caps; letter-spacing: 0.04em; }
.state { font-weight: 600; }
.empty { padding: 1mm 0; }
/* A sentence explaining a section sits above its table, and one counting what
   the section left out sits below it. Both need air, or they read as a row. */
.subnote { margin-bottom: 1.2mm; }
.omitted { margin-top: 1.2mm; }
.question {
  border-left: 2.2pt solid #000;
  padding: 0.5mm 0 0.5mm 3mm;
  margin-top: 1mm;
  font-size: 11.5pt;
}
.foot { margin-top: 4mm; padding-top: 1.5mm; border-top: 1px solid #000; }
a { color: #000; text-decoration: none; }
a:hover { text-decoration: underline; }
@media print {
  body { background: #fff; }
  .sheet { margin: 0; padding: 0; max-width: none; }
  a { text-decoration: none; }
}
"""


def _href(source, mode: str) -> str | None:
    if mode == SERVER_LINKS:
        return ARTIFACT_ROUTE.format(short=source.artifact) if source.artifact else None
    return source.rel


def _source_cell(line: Line, mode: str) -> str:
    """The rightmost column: the tier and the document's own date.

    The citation in the form a clinician can use. The vault-relative path that
    the Markdown export footnotes is on the link rather than on the page — on
    paper it is noise, and the tier and the date are what let someone decide how
    much weight to give the row.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for source in line.sources:
        if source.text in seen:
            # Two documents of the same tier written on the same day. One span,
            # linked to the first of them; the Markdown export's footnotes are
            # where both remain individually addressable.
            continue
        seen.add(source.text)
        text = escape(source.text)
        href = _href(source, mode)
        title = escape(source.citation.text)
        if href:
            parts.append(f'<a href="{escape(href, quote=True)}" title="{title}">{text}</a>')
        else:
            parts.append(f'<span title="{title}">{text}</span>')
    return "<br>".join(parts)


def _said_cell(line: Line) -> str:
    value = escape(line.value)
    if line.alternatives:
        # Both readings, joined by a word rather than a comma. A list separated
        # by commas reads as things that are all true; two doses from two
        # sources are a disagreement, and exactly one of them is right.
        for alternative in line.alternatives:
            value += f' <span class="or">or</span> {escape(alternative)}'
    extras = [escape(part) for part in (line.note,) if part]
    if line.state:
        extras.append(f'<span class="state">{escape(line.state)}</span>')
    if extras:
        value += f'<br><span class="small muted">{" · ".join(extras)}</span>'
    return value


def _section(section: Section, mode: str) -> str:
    out = [f"<h2>{escape(section.heading)}</h2>"]
    if section.subnote:
        out.append(f'<p class="subnote small muted">{escape(section.subnote)}</p>')
    if not section.lines:
        # What the page dropped, where it dropped everything — never the empty
        # note, which would assert that nothing happened over the top of it.
        note = section.omitted_note
        text = f"{note}." if note else section.empty_note
        out.append(f'<p class="empty muted">{escape(text)}</p>')
        return "\n".join(out)
    rows = []
    for line in section.lines:
        rows.append(
            "<tr>"
            f'<td class="name">{escape(line.label)}</td>'
            f'<td class="said">{_said_cell(line)}</td>'
            f'<td class="src small">{_source_cell(line, mode)}</td>'
            "</tr>"
        )
    out.append("<table>" + "".join(rows) + "</table>")
    note = section.omitted_note
    if note:
        out.append(f'<p class="omitted small muted">{escape(note)}.</p>')
    return "\n".join(out)


def render(summary: Summary, mode: str = FILE_LINKS) -> str:
    """The whole sheet as one self-contained HTML document."""
    body: list[str] = []
    body.append(
        '<div class="masthead">'
        f"<h1>{escape(summary.title)}</h1>"
        + (f'<p class="for">for {escape(summary.label)}</p>' if summary.label else "")
        + "</div>"
    )
    body.append(
        f'<p class="dateline small">{escape(summary.dateline)}. '
        f"{escape(STANDFIRST)}</p>"
    )
    if summary.demo:
        # Above everything that reads like a clinical fact, and boxed. A
        # synthetic sheet taken for a real one is the one export this project
        # could produce that would actually cause harm.
        body.append(f'<p class="warning">{escape(DEMO_WARNING)}</p>')
    body.append('<hr class="rule">')

    for section in summary.sections:
        body.append(_section(section, mode))

    body.append(f"<h2>{escape(QUESTION_HEADING)}</h2>")
    if summary.question:
        body.append(f'<p class="question">{escape(summary.question)}</p>')
    else:
        body.append(
            '<p class="empty muted">I did not write a question before this '
            "appointment.</p>"
        )

    footer = []
    if summary.overflowed:
        footer.append(
            "This sheet runs past one page. Nothing was dropped from my "
            "medications or allergies to make it fit."
        )
    footer.append(summary.waiting.sentence)
    body.append(
        '<div class="foot small muted">'
        + "".join(f"<p>{escape(line)}</p>" for line in footer)
        + "</div>"
    )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f"<title>{escape(summary.title)}</title>\n"
        f"<style>{STYLE}</style>\n"
        "</head>\n<body>\n"
        '<main class="sheet">\n'
        + "\n".join(body)
        + "\n</main>\n</body>\n</html>\n"
    )
