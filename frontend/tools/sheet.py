"""Measure the consultation sheet on real A4, which is the only way to know.

``agent/summary/budget.py`` enforces the one-page limit from an arithmetic model
of the page: so many millimetres for a row, so many for a section heading. That
model cannot be derived from first principles — a table cell's line box is set by
the strut of its own type, so a note in smaller text still costs a full line, and
the first version of this budget was wrong by an entire page because it assumed
otherwise. It has to be **calibrated**, and this is what calibrates it.

    health-agent serve --vault /path/to/vault &
    python frontend/tools/sheet.py --out /tmp/sheet

For each summary in the record it renders ``/print/{id}`` through a real browser,
prints the sheet to an A4 PDF, and reports:

* how many pages actually came out;
* the measured height of the sheet against the budget's estimate;
* the measured height of every section and every row.

If the estimate and the measurement disagree, the constants in
:mod:`agent.summary.model` are what to change — never the assertion. And if the
page count is 2 on a record whose medications fit, something in the budget has
stopped working; if it is 2 on a record of forty medications, that is the limit
giving way on purpose and the sheet says so on its face.

``--pdf`` writes the PDFs out so they can be opened, and printed. The sheet is
the artefact this project exists to produce and it is worth looking at on paper
at least once: a PDF render answers "does it fit", and only paper answers "is
this a thing a person would read in a waiting room".

Needs ``playwright`` and its chromium (``python -m playwright install
chromium``), plus ``pypdfium2`` for the page images. None of them is a
dependency of the application.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ORIGIN = "http://127.0.0.1:7777"

#: CSS pixels per millimetre at the 96dpi the print pipeline uses.
PX_PER_MM = 96 / 25.4

#: A4 minus the margin the sheet's own stylesheet sets. The page is rendered at
#: exactly this width so the measurements mean something.
CONTENT_WIDTH_MM = 210 - 2 * 13

MEASURE = """() => {
  const mm = 96 / 25.4;
  const sheet = document.querySelector('.sheet');
  if (!sheet) return null;
  const blocks = [];
  for (const el of sheet.children) {
    blocks.push({
      tag: el.tagName,
      text: el.textContent.trim().slice(0, 44).replace(/\\s+/g, ' '),
      mm: el.getBoundingClientRect().height / mm,
    });
  }
  const rows = [...document.querySelectorAll('tr')].map((tr) => ({
    label: tr.children[0].textContent.slice(0, 22),
    mm: tr.getBoundingClientRect().height / mm,
  }));
  return { total: sheet.getBoundingClientRect().height / mm, blocks, rows };
}"""


def fetch(origin: str, path: str):
    with urllib.request.urlopen(f"{origin}{path}") as response:  # noqa: S310
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=ORIGIN)
    parser.add_argument("--out", default="sheet", help="where the PDFs and PNGs go")
    parser.add_argument(
        "--id",
        default=None,
        help="one summary id; otherwise every summary in the record",
    )
    parser.add_argument(
        "--pdf", action="store_true", help="keep the A4 PDFs as well as the images"
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ids = (
        [args.id]
        if args.id
        else [row["id"] for row in fetch(args.origin, "/api/summary")["summaries"]]
    )
    if not ids:
        print(
            "no summaries in this record yet — prepare one first:\n"
            "  health-agent summary --vault <vault> --question '...' --for gp"
        )
        return 1

    from playwright.sync_api import sync_playwright

    failures = 0
    with sync_playwright() as play:
        browser = play.chromium.launch()
        for summary_id in ids:
            failures += shoot(browser, args, out, summary_id)
        browser.close()
    return 1 if failures else 0


def shoot(browser, args, out: Path, summary_id: str) -> int:
    estimate = fetch(args.origin, f"/api/summary/{summary_id}")
    if estimate.get("summary", False) is None:
        print(f"{summary_id}: withdrawn — nothing to render")
        return 0

    page = browser.new_page(
        viewport={"width": int(CONTENT_WIDTH_MM * PX_PER_MM), "height": 1400}
    )
    page.goto(f"{args.origin}/print/{summary_id}")
    # The measurement has to be taken in print media: the screen view has a
    # ground behind it and padding around it, and neither is on the paper.
    page.emulate_media(media="print")
    page.wait_for_timeout(200)

    measured = page.evaluate(MEASURE)
    pdf_path = out / f"{summary_id}.pdf"
    page.pdf(path=str(pdf_path), format="A4", print_background=True)
    page.screenshot(path=str(out / f"{summary_id}-screen.png"), full_page=True)
    page.close()

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    pages = len(document)
    for index in range(pages):
        document[index].render(scale=2.2).to_pil().save(
            out / f"{summary_id}-page-{index + 1}.png"
        )
    document.close()
    if not args.pdf:
        pdf_path.unlink()

    predicted = estimate.get("height_mm")
    overflowed = estimate.get("overflowed")
    print(f"\n{summary_id}  ({estimate.get('label') or 'no label'})")
    print(f"  pages          {pages}{'  (expected: it overflowed)' if overflowed else ''}")
    print(f"  measured       {measured['total']:.1f} mm")
    print(f"  budget said    {predicted} mm")
    print(f"  drift          {measured['total'] - float(predicted or 0):+.1f} mm")
    for block in measured["blocks"]:
        print(f"    {block['mm']:6.1f}  {block['tag']:<6} {block['text']}")
    print("  rows:")
    for row in measured["rows"]:
        print(f"    {row['mm']:6.1f}  {row['label']}")

    # The budget must never *under*-estimate: an estimate below the truth is a
    # sheet that says it fits and does not.
    if predicted is not None and measured["total"] > float(predicted):
        print(
            f"  PROBLEM: the sheet is taller than the budget predicted. Raise the "
            f"constants in agent/summary/model.py until this stops."
        )
        return 1
    if pages > 1 and not overflowed:
        print(
            "  PROBLEM: two pages from a sheet the budget called one. The budget is "
            "out of calibration; nothing was dropped, but the limit is not holding."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
