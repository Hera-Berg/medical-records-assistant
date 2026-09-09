"""Real files for the demo vault: images that carry text, a PDF with a text
layer, and audio that actually decodes.

The demo used to write format headers followed by padding. Every citation
resolved, which was what that was for, but the consequence was that a demo vault
could not exercise a single thing downstream of ingest: nine artefacts, all of
them unreadable, so ``extract`` had nothing to read, the deterministic reader had
nothing to cross-check against, and the speech route had no audio to decode.

So these are real documents. A prescription image really says ``PERINDOPRIL 5mg
— ONE DAILY``, the pathology PDF really has a text layer ``pdfplumber`` can pull,
and the voice note really is a decodable 16 kHz mono WAV. What they say matches
the claims :mod:`agent.demo.stream` seeds against them, which is the point: a
person can run ``extract`` over a demo vault, read what the model proposed, and
compare it against a hand-authored answer that is on the same page.

Three things they are deliberately not.

**Not real patient data.** Every name, drug, dose and result is invented, and
``DEMO-DATA.md`` says so at the vault root.

**Not speech.** No text-to-speech runs here, so the voice note is a decaying tone
rather than a person talking. That is enough to prove the speech route end to end
— the file opens, resamples, and reaches the model — and it will transcribe to
nothing, which is the honest outcome and is written down rather than left to
surprise someone.

**Not fonts from the host.** Everything is drawn with Pillow's own bundled
default face at an explicit size, never a system TTF found by searching. A demo
that rendered differently on each machine would be a demo whose artefact hashes,
and therefore whose ``raw/`` filenames, depended on which fonts happened to be
installed.
"""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..projection import dates as dates_mod

#: Long edge of a rendered page, in pixels. Above the 1280 px working-copy
#: budget on purpose, so preparing one of these actually downscales and the
#: ops list in the extraction event has something in it.
PAGE_WIDTH = 1400
PAGE_HEIGHT = 1980

#: A4 at 72 points to the inch, for the PDF path.
PDF_WIDTH = 595
PDF_HEIGHT = 842

#: Whisper's input rate. Writing the file at it means the speech route can read
#: the demo's audio without a resample step standing between them.
WAV_SAMPLE_RATE = 16000
WAV_SECONDS = 4


@dataclass(frozen=True)
class Rendered:
    """One artefact's bytes, and the filename they should arrive under."""

    data: bytes
    extension: str
    #: What a browser or camera would have declared. Only ever *narrows* what
    #: the bytes already say — see :mod:`agent.ingest.mime`.
    declared_mime: str | None = None


# --- images ----------------------------------------------------------------


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Pillow's bundled face at *size*, never a font found on this machine."""
    return ImageFont.load_default(size=size)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    top: int,
    left: int = 90,
    size: int = 40,
    leading: int = 58,
) -> int:
    """Write *lines* down the page. A bare ``"—"`` draws a rule instead."""
    font = _font(size)
    y = top
    for line in lines:
        if line == "—":
            draw.line([(left, y + leading // 3), (PAGE_WIDTH - left, y + leading // 3)],
                      fill=(90, 90, 90), width=3)
        elif line:
            draw.text((left, y), line, font=font, fill=(20, 20, 20))
        y += leading
    return y


def page_image(
    heading: str,
    lines: list[str],
    fmt: str = "JPEG",
    skew: float = 0.0,
    blur: float = 0.0,
) -> bytes:
    """A sheet of paper with words on it, as a photograph or a scan.

    *skew* and *blur* exist for the one artefact that is meant to read badly.
    A blurry script photographed at an angle is what the OCR misread in the
    scenario actually came from, and rendering it crisp would have the demo
    demonstrate a correction against a document nobody could plausibly misread.
    """
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), (252, 251, 248))
    draw = ImageDraw.Draw(image)
    draw.text((90, 110), heading, font=_font(54), fill=(10, 10, 10))
    draw.line([(90, 200), (PAGE_WIDTH - 90, 200)], fill=(60, 60, 60), width=4)
    _draw_lines(draw, lines, top=260)

    if skew:
        # `expand=False` keeps the page size fixed; the corners that rotate out
        # of frame are what a photograph taken at an angle actually loses.
        image = image.rotate(skew, resample=Image.BICUBIC, fillcolor=(238, 236, 232))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))

    buffer = io.BytesIO()
    if fmt == "PNG":
        image.save(buffer, format="PNG", optimize=True)
    else:
        image.save(buffer, format="JPEG", quality=82, optimize=True)
    return buffer.getvalue()


# --- PDF -------------------------------------------------------------------


def _pdf_escape(text: str) -> str:
    """PDF string escaping, over the WinAnsi range only.

    Anything outside it becomes ``?`` rather than a mojibake byte: the demo
    writes plain clinical English and a character that needed more than this
    would be a sign the text had drifted somewhere it should not.
    """
    out: list[str] = []
    for char in text:
        if char in "()\\":
            out.append("\\" + char)
        elif 32 <= ord(char) < 127:
            out.append(char)
        else:
            out.append("?")
    return "".join(out)


def page_pdf(pages: list[list[str]], size: int = 11, leading: int = 15) -> bytes:
    """A PDF with a genuine text layer, written out by hand.

    No ``reportlab``. A generator this project depends on for a demo would be a
    dependency carried into every install for the sake of a scratch vault, and
    the subset of PDF needed for "Helvetica, one column, no images" is small
    enough to write: catalog, page tree, one Type 1 base font, one uncompressed
    content stream per page, and an ``xref`` table of real byte offsets.

    Uncompressed on purpose. ``pdfplumber`` reads it either way, and someone
    opening the file in a text editor to see how the demo was made can read the
    words in it.
    """
    count = len(pages)
    page_ids = [4 + 2 * index for index in range(count)]
    content_ids = [5 + 2 * index for index in range(count)]

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {count} >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    for page_id, content_id, lines in zip(page_ids, content_ids, pages):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PDF_WIDTH} {PDF_HEIGHT}] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        body = [
            "BT",
            f"/F1 {size} Tf",
            f"{leading} TL",
            f"1 0 0 1 56 {PDF_HEIGHT - 72} Tm",
        ]
        body.extend(f"({_pdf_escape(line)}) Tj T*" for line in lines)
        body.append("ET")
        stream = "\n".join(body).encode("ascii")
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


# --- audio -----------------------------------------------------------------


def tone_wav(seconds: int = WAV_SECONDS, rate: int = WAV_SAMPLE_RATE) -> bytes:
    """A valid 16-bit mono PCM WAV: a decaying tone, not speech and not silence.

    Silence would be a legitimate artefact — the fixture corpus has one, and the
    expected transcript there is nothing — but as the demo's only recording it
    would leave "the audio never decoded" and "the audio decoded and was empty"
    looking identical. A tone rules the first one out.
    """
    frames = bytearray()
    total = seconds * rate
    for index in range(total):
        t = index / rate
        envelope = math.exp(-1.8 * t)
        sample = int(9000 * envelope * math.sin(2 * math.pi * 220.0 * t))
        frames += struct.pack("<h", sample)

    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(frames))
    return header + bytes(frames)


# --- the demo's own documents ----------------------------------------------


def _date(anchor: date, days_ago: int) -> str:
    return dates_mod.render_date(date.fromordinal(anchor.toordinal() - days_ago))


def _script(
    prescriber: str,
    written: str,
    drug: str,
    directions: str,
    quantity: str,
    repeats: str,
    extra: list[str] | None = None,
) -> list[str]:
    """The lines of a dispensing script, in the order one is laid out."""
    return [
        prescriber,
        "Rosewood Family Practice",
        "—",
        "Patient: A. Demo   DOB 14/03/1971",
        f"Date written: {written}",
        "",
        drug,
        directions,
        "",
        f"Quantity: {quantity}",
        f"Repeats: {repeats}",
        *(extra or []),
        "",
        "—",
        "This is invented. It is not a prescription.",
    ]


def render(name: str, anchor: date) -> Rendered:
    """The bytes for one demo artefact, by name.

    Keyed by name rather than by kind because the words on each page have to
    match the claims :mod:`agent.demo.stream` seeds against that artefact. A
    generic "script image" would put the demo's own documents and its own event
    stream quietly out of step, which is the failure the whole vault exists to
    let someone catch by reading.
    """
    if name == "perindopril-script":
        return Rendered(
            page_image(
                "PRESCRIPTION",
                _script(
                    "Dr H. Nguyen",
                    _date(anchor, 20),
                    "PERINDOPRIL ARGININE 5 mg tablets",
                    "ONE DAILY in the morning",
                    "30 tablets",
                    "No repeats",
                ),
            ),
            "jpg",
        )

    if name == "metformin-script":
        return Rendered(
            page_image(
                "PRESCRIPTION",
                _script(
                    "Dr H. Nguyen",
                    _date(anchor, 240),
                    "METFORMIN HYDROCHLORIDE 500 mg tablets",
                    "TWICE DAILY with food",
                    "60 tablets",
                    "No repeats",
                ),
            ),
            "jpg",
        )

    if name == "atorvastatin-script-a":
        return Rendered(
            page_image(
                "PRESCRIPTION",
                _script(
                    "Dr H. Nguyen",
                    _date(anchor, 35),
                    "ATORVASTATIN 20 mg tablets",
                    "ONE DAILY at night",
                    "30 tablets",
                    "No repeats",
                ),
            ),
            "jpg",
        )

    if name == "atorvastatin-script-b":
        # Same drug, same date, a different dose, and nothing on either page
        # says which is right. That is the whole point of this pair.
        return Rendered(
            page_image(
                "PRESCRIPTION",
                _script(
                    "Dr S. Okafor",
                    _date(anchor, 35),
                    "ATORVASTATIN 40 mg tablets",
                    "ONE DAILY at night",
                    "30 tablets",
                    "No repeats",
                ),
            ),
            "jpg",
        )

    if name == "levothyroxine-script":
        # Skewed and blurred: this is the artefact the scenario's OCR misread
        # came off, and a crisp render would make the correction look invented.
        return Rendered(
            page_image(
                "PRESCRIPTION",
                _script(
                    "Dr H. Nguyen",
                    _date(anchor, 56),
                    "LEVOTHYROXINE SODIUM 50 mcg tablets",
                    "ONE DAILY before breakfast",
                    "90 tablets",
                    "No repeats",
                ),
                skew=-4.5,
                blur=1.6,
            ),
            "jpg",
        )

    if name == "cardiology-letter":
        return Rendered(
            page_image(
                "ROSEWOOD CARDIOLOGY",
                [
                    "Dr H. Nguyen, Cardiologist",
                    f"Letter dated {_date(anchor, 62)}",
                    "—",
                    "Dear Colleague,",
                    "",
                    "Re: A. Demo, DOB 14/03/1971",
                    "",
                    "Reviewed today for hypertension, which has been",
                    "managed since November 2024. Perindopril 5 mg daily",
                    "was commenced at that time and is unchanged.",
                    "",
                    "Also taking amitriptyline 10 mg at night.",
                    "",
                    "Yours sincerely,",
                    "H. Nguyen",
                    "",
                    "—",
                    "This is invented. It describes no real person.",
                ],
                fmt="PNG",
            ),
            "png",
        )

    if name == "discharge-summary":
        return Rendered(
            page_pdf(
                [
                    [
                        "ROSEWOOD DISTRICT HOSPITAL - DISCHARGE SUMMARY",
                        "",
                        "Patient: A. Demo    DOB: 14/03/1971",
                        f"Discharged: {_date(anchor, 41)}",
                        "",
                        "ALLERGIES",
                        "  Penicillin - rash",
                        "",
                        "MEDICATIONS ON DISCHARGE",
                        "  Sertraline 50 mg - one daily",
                        "  Quantity 30 tablets, 1 repeat",
                        "",
                        "Page 1 of 2",
                    ],
                    [
                        "ROSEWOOD DISTRICT HOSPITAL - DISCHARGE SUMMARY",
                        "",
                        "CHANGES TO MEDICATION",
                        "  CEASE amitriptyline 10 mg at night.",
                        "  Stopped during this admission.",
                        "",
                        "FOLLOW UP",
                        "  GP review in two weeks.",
                        "",
                        "This document is invented. It is not a medical record.",
                        "",
                        "Page 2 of 2",
                    ],
                ]
            ),
            "pdf",
        )

    if name == "pathology":
        return Rendered(
            page_pdf(
                [
                    [
                        "ROSEWOOD PATHOLOGY - THYROID FUNCTION",
                        "",
                        "Patient: A. Demo    DOB: 14/03/1971",
                        f"Collected: {_date(anchor, 26)}",
                        f"Reported:  {_date(anchor, 26)}",
                        "",
                        "TSH          6.8 mIU/L    (0.4 - 4.0)    HIGH",
                        "Free T4      11.1 pmol/L  (9.0 - 19.0)",
                        "",
                        "COMMENT",
                        "  Consistent with hypothyroidism. Patient reports",
                        "  symptoms since around March.",
                        "",
                        "ALLERGIES RECORDED AT COLLECTION",
                        "  Sulfonamides - swelling",
                        "",
                        "This report is invented. It describes no real person.",
                    ]
                ]
            ),
            "pdf",
        )

    if name == "voice-note":
        # A WAV rather than the WebM a browser would produce. Muxing a valid
        # WebM by hand needs an EBML writer, and a subtly invalid one would put
        # the demo back where it started — a file that looks right and cannot be
        # read. Phase 6 records WebM from `MediaRecorder`; this stands in for it.
        return Rendered(tone_wav(), "wav")

    raise KeyError(f"no demo document is authored for {name!r}")
