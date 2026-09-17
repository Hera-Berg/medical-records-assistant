"""The golden corpus: synthetic artefacts and the claims they should yield.

Every artefact here is **invented**. ``CLAUDE.md``: "Fixture corpus of realistic
artefacts in ``tests/fixtures/``: ... Synthetic, never real patient data." No
part of this describes a real person, and the drug names are ordinary ones
chosen because they are the sort of thing that appears on a script — never
because anybody takes them.

They are **generated rather than committed** as binaries, which buys three
things: the generator is readable, so what is actually on each page can be
checked without opening an image viewer; the degraded variants are produced by
applying a named distortion to a clean page, so "blurry, at an angle" is
reproducible and adjustable rather than a photograph nobody can regenerate; and
the repository does not carry megabytes of JPEG.

Everything is deterministic. Same inputs, same bytes, on any machine — the same
requirement the projection has, for the same reason: a corpus that drifts makes
a recall regression indistinguishable from a re-render.
"""

from __future__ import annotations

import io
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

#: Rendered at a plausible phone-photo size, before preprocessing shrinks it.
PAGE = (1240, 1754)  # A4 at 150 DPI
_MARGIN = 90
_LINE = 46


@dataclass(frozen=True)
class Expected:
    """One claim a fixture must yield. Hand-written, never generated.

    ``value`` is compared normalised, so a model writing "5 mg daily" for "5mg
    daily" is a pass — the record renders the literal and compares the key, and
    the eval must use the same rule or it would fail correct readings.
    """

    subject: str
    predicate: str
    value: str
    #: Recall on these must be 100%. A false positive is caught in review; a
    #: missed medication is invisible, and is the failure this project exists to
    #: prevent.
    critical: bool = True


@dataclass(frozen=True)
class Fixture:
    """One artefact and everything it should produce."""

    name: str
    mime: str
    render: Callable[[], bytes]
    expected: tuple[Expected, ...] = ()
    #: What the page says, for the deterministic reader to find. Kept beside the
    #: renderer so the two cannot drift.
    text: str = ""
    document_date: str | None = None
    readable: bool = True
    notes: str = ""
    #: The earliest phase in which every reader this fixture needs exists. A
    #: recording needs two: the local speech model that types it up (phase 6)
    #: and the reader that proposes claims from those words (phase 7), so a
    #: voice note only scores from 7.
    phase: int = 4
    conflicts_with: str | None = None
    #: Terms that must survive transcription, for a recording. Scored apart
    #: from ``expected``, because the speech model's contract is the transcript
    #: and not the claims: reading "40mg daily" out of a transcript is the
    #: vision model's job and needs the box, and the ASR path deliberately does
    #: not. A drug name mangled here is an unmatched entity downstream, which is
    #: why these are the terms that are checked.
    transcript_expects: tuple[str, ...] = ()
    #: External programs this fixture's renderer needs. Speech cannot be
    #: synthesised in pure Python the way a JPEG can be drawn, so the two
    #: recordings are rendered with espeak-ng and ffmpeg and are reported as
    #: unavailable rather than failing where those are absent.
    requires: tuple[str, ...] = ()
    #: Claims that are true of the page but not required. A claim about a
    #: medication or an allergy that is neither expected nor listed here is
    #: scored wrong: nobody can tell a true extra from an invented one without a
    #: person having written it down.
    also_true: tuple[tuple[str, str, str], ...] = ()

    @property
    def missing_tools(self) -> tuple[str, ...]:
        return tuple(name for name in self.requires if shutil.which(name) is None)

    def bytes(self) -> bytes:
        return self.render()

    @property
    def critical(self) -> tuple[Expected, ...]:
        return tuple(item for item in self.expected if item.critical)


# --- drawing ---------------------------------------------------------------


def _page(lines: list[tuple[str, int]], size=PAGE) -> Image.Image:
    """A clean white page with black text at the given indents."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    y = _MARGIN
    for text, indent in lines:
        if text:
            draw.text((_MARGIN + indent, y), text, fill="black")
        y += _LINE
    return image


def _jpeg(image: Image.Image, quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _rotate(image: Image.Image, degrees: float) -> Image.Image:
    """Photographed at an angle. `expand` so nothing is cropped off."""
    return image.rotate(degrees, resample=Image.BICUBIC, expand=True, fillcolor="white")


def _blur(image: Image.Image, radius: float) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius))


def _uneven_light(image: Image.Image) -> Image.Image:
    """A shadow across the page, the way a phone photo on a desk actually looks."""
    width, height = image.size
    shade = Image.new("L", (width, height))
    pixels = shade.load()
    for x in range(0, width, 4):
        column = int(255 - 70 * math.sin(math.pi * x / width))
        for y in range(0, height, 4):
            for dx in range(min(4, width - x)):
                for dy in range(min(4, height - y)):
                    pixels[x + dx, y + dy] = column
    return Image.composite(image, Image.new("RGB", image.size, "black"), shade)


# --- recordings -------------------------------------------------------------
#
# Everything else in this corpus is drawn in pure Python. Speech cannot be, so
# these two shell out to espeak-ng for the voice and ffmpeg for the container.
# That is a real dependency and it is declared on the fixture rather than
# assumed: where the tools are absent the recording is reported as unavailable
# and the eval says so, instead of a corpus that silently scores nothing.
#
# Synthetic, and audibly so. The point is not to sound like a person — it is to
# put real drug names into real audio, so that "biasing is what makes `small`
# usable" is a claim the harness can check rather than one the documentation
# asserts.

#: Chrome records ``audio/webm;opus``, so that is what the fixtures are. A WAV
#: would skip the decode step the real path always takes.
_OPUS_ARGS = ("-c:a", "libopus", "-f", "webm")


def _speech(words: str, voice: str = "en-gb", speed: int = 145) -> bytes:
    """Synthesised speech, in the container a browser would have produced."""
    with tempfile.TemporaryDirectory() as directory:
        raw = f"{directory}/speech.wav"
        out = f"{directory}/speech.webm"
        subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(speed), "-w", raw, words],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", raw, *_OPUS_ARGS, out, "-y"],
            check=True,
            capture_output=True,
        )
        return open(out, "rb").read()


def _room_tone(seconds: int, cough_at: int | None = None) -> bytes:
    """Near-silence, optionally with one transient in it.

    The expected output is nothing at all. Whisper's training corpus is
    subtitles and over silence it produces subtitle-shaped text — "Thank you for
    watching" — which is fluent, confident and entirely invented. A cough is
    included because pure digital silence is an unrealistically easy case: real
    room tone with a transient in it is what a phone in a waiting room actually
    captures.
    """
    inputs = ["-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:a=0.0008"]
    if cough_at is None:
        chain = []
    else:
        inputs += ["-f", "lavfi", "-i", "anoisesrc=d=0.35:c=brown:a=0.35"]
        chain = [
            "-filter_complex",
            f"[1:a]adelay={cough_at * 1000}|{cough_at * 1000},"
            f"apad=whole_dur={seconds}[c];"
            f"[0:a][c]amix=inputs=2:duration=first:normalize=0",
        ]
    with tempfile.TemporaryDirectory() as directory:
        out = f"{directory}/tone.webm"
        subprocess.run(
            ["ffmpeg", "-v", "error", *inputs, *chain, *_OPUS_ARGS, out, "-y"],
            check=True,
            capture_output=True,
        )
        return open(out, "rb").read()


#: What the rambling voice note says. Kept beside the fixture that renders it,
#: for the same reason every other fixture's ``text`` is: the words and the
#: expectations cannot drift if they are in one place.
RAMBLING_NOTE = (
    "Right, so. I stopped the sertraline, that was, I think around Easter time, "
    "somewhere around then. And the doctor put the atorvastatin up to forty "
    "milligrams daily. That was Doctor Nguyen, at the Rosewood clinic. "
    "The headaches have been a bit better since the tablets changed."
)


# --- a minimal PDF with a real text layer ----------------------------------


def _pdf(lines: list[str], title: str = "Pathology") -> bytes:
    """A one-page PDF whose text is genuinely selectable.

    Written by hand rather than rasterised through Pillow, because the whole
    point of this fixture is the **text layer**: "pathology reports from patient
    portals almost always have one and running OCR over the rendered page
    instead makes them worse". A Pillow-saved PDF is a picture of text and would
    exercise the wrong path entirely.
    """
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td", "16 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_lines.append(f"({escaped}) Tj T*")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Title (" + title.encode("latin-1") + b") >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


# --- the corpus ------------------------------------------------------------

_PRINTED_SCRIPT = [
    ("ROSEWOOD MEDICAL CENTRE", 0),
    ("14 Bower Street, Northbridge", 0),
    ("", 0),
    ("Prescription", 0),
    ("Date: 4 June 2026", 0),
    ("", 0),
    ("1. PERINDOPRIL ARGININE 5mg tablets", 0),
    ("   Take ONE tablet daily in the morning", 0),
    ("   Quantity: 30 tablets      Repeats: 2", 0),
    ("", 0),
    ("2. ATORVASTATIN 20mg tablets", 0),
    ("   Take ONE tablet at night", 0),
    ("   Quantity: 30 tablets      Repeats: no repeats", 0),
    ("", 0),
    ("Dr H Nguyen", 0),
]

_HANDWRITTEN = [
    ("Rosewood Medical Centre", 0),
    ("", 0),
    ("Metformin 500mg", 0),
    ("twice daily with food", 0),
    ("Qty 60    nil repeats", 0),
    ("", 0),
    ("12/1/26   Dr Nguyen", 0),
]

_LETTER = [
    ("NORTHBRIDGE CARDIOLOGY", 0),
    ("Consultant: Dr H Nguyen", 0),
    ("", 0),
    ("Dear Doctor,", 0),
    ("", 0),
    ("Thank you for referring this patient, whom I saw on", 0),
    ("9 July 2026.", 0),
    ("", 0),
    ("Impression: hypertension, well controlled.", 0),
    ("", 0),
    ("I have started perindopril 5mg daily.", 0),
    ("The patient reports a rash with penicillin.", 0),
    ("", 0),
    ("Yours sincerely,", 0),
    ("Dr H Nguyen", 0),
]

_PATHOLOGY = [
    "NORTHBRIDGE PATHOLOGY",
    "Collected: 14 August 2026",
    "",
    "THYROID FUNCTION",
    "  TSH            8.4 mIU/L    (0.4 - 4.0)   HIGH",
    "  Free T4        11.2 pmol/L  (9.0 - 19.0)",
    "",
    "Comment: results forwarded to requesting doctor.",
]

_SECOND_LANGUAGE = [
    ("CENTRE MEDICAL DE ROSEWOOD", 0),
    ("Ordonnance", 0),
    ("Date : 4 juin 2026", 0),
    ("", 0),
    ("PERINDOPRIL 5 mg", 0),
    ("Un comprime par jour", 0),
    ("Quantite : 30 comprimes   Renouvellements : aucun", 0),
]


def _lines_text(lines) -> str:
    return "\n".join(text for text, _ in lines if text)


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="printed-script-two-medications",
        mime="image/jpeg",
        render=lambda: _jpeg(_page(_PRINTED_SCRIPT)),
        text=_lines_text(_PRINTED_SCRIPT),
        document_date="2026-06-04",
        expected=(
            Expected("med:perindopril-arginine", "dose", "5mg daily"),
            Expected("med:atorvastatin", "dose", "20mg at night"),
        ),
        notes="two medications and a repeat count, printed and square-on",
    ),
    Fixture(
        name="blurry-handwritten-script-at-an-angle",
        mime="image/jpeg",
        render=lambda: _jpeg(
            _uneven_light(_blur(_rotate(_page(_HANDWRITTEN), -7.5), 1.4)), quality=70
        ),
        text=_lines_text(_HANDWRITTEN),
        document_date="2026-01-12",
        expected=(Expected("med:metformin", "dose", "500mg twice daily"),),
        notes=(
            "the case deskew would help with, and does not exist. Also the case "
            "tesseract is close to useless on, so its claims come out uncorroborated"
        ),
    ),
    Fixture(
        name="scanned-specialist-letter-skewed",
        mime="image/jpeg",
        render=lambda: _jpeg(_rotate(_page(_LETTER), 2.5)),
        text=_lines_text(_LETTER),
        document_date="2026-07-09",
        expected=(
            Expected("med:perindopril", "dose", "5mg daily"),
            Expected("allergy:penicillin", "reaction", "rash"),
            Expected("problem:hypertension", "name", "hypertension", critical=False),
            Expected("person:dr-nguyen", "role", "cardiology", critical=False),
        ),
        notes="a letterhead, a skew, and an allergy that must never be missed",
    ),
    Fixture(
        name="pathology-pdf-with-text-layer",
        mime="application/pdf",
        render=lambda: _pdf(_PATHOLOGY),
        text="\n".join(_PATHOLOGY),
        document_date="2026-08-14",
        expected=(),
        notes=(
            "a real text layer, so the deterministic path reads it directly and "
            "never rasterises it. It names no medication, allergy or condition, so "
            "the correct reading proposes none — in particular no hypothyroidism: "
            "a condition worked out from a TSH result is interpretation. Lab results "
            "have no predicate yet, so the out-of-range flag is not scored"
        ),
    ),
    Fixture(
        name="contradictory-dose-a",
        mime="image/jpeg",
        render=lambda: _jpeg(
            _page([("ATORVASTATIN 20mg tablets", 0), ("One at night", 0),
                   ("Date: 5 August 2026", 0)])
        ),
        text="ATORVASTATIN 20mg tablets\nOne at night\nDate: 5 August 2026",
        document_date="2026-08-05",
        expected=(Expected("med:atorvastatin", "dose", "20mg at night"),),
        conflicts_with="contradictory-dose-b",
        notes="with its pair, must produce `conflicted` and never an average",
    ),
    Fixture(
        name="contradictory-dose-b",
        mime="image/jpeg",
        render=lambda: _jpeg(
            _page([("ATORVASTATIN 40mg tablets", 0), ("One at night", 0),
                   ("Date: 5 August 2026", 0)])
        ),
        text="ATORVASTATIN 40mg tablets\nOne at night\nDate: 5 August 2026",
        document_date="2026-08-05",
        expected=(Expected("med:atorvastatin", "dose", "40mg at night"),),
        conflicts_with="contradictory-dose-a",
    ),
    Fixture(
        name="second-language-script",
        mime="image/jpeg",
        render=lambda: _jpeg(_page(_SECOND_LANGUAGE)),
        text=_lines_text(_SECOND_LANGUAGE),
        document_date="2026-06-04",
        expected=(Expected("med:perindopril", "dose", "5 mg par jour"),),
        notes=(
            "the drug name is the same in both languages, which is the point: the "
            "entity must not fork because the instruction was written in French"
        ),
    ),
    Fixture(
        name="unreadable-photograph",
        mime="image/jpeg",
        render=lambda: _jpeg(_blur(_page([("...", 0)]), 24)),
        text="",
        readable=False,
        expected=(),
        notes=(
            "expected output: nothing, and `readable: false`. A plausible guess "
            "here is the specific harm the whole system is built to avoid"
        ),
    ),
    # These render as real audio and go through both readers: the local speech
    # model types them up, then the box reads the words. Scoring the second
    # stage against a hand-written transcript would test the reading prompt and
    # nothing else — a drug name has to survive Whisper *and* the reading, since
    # a name mangled in the first stage is an unmatched entity in the second and
    # nothing downstream notices.
    Fixture(
        name="rambling-voice-note",
        mime="audio/webm",
        render=lambda: _speech(RAMBLING_NOTE),
        text=RAMBLING_NOTE,
        expected=(
            Expected("med:sertraline", "status", "stopped"),
            Expected("med:atorvastatin", "dose", "40mg daily"),
        ),
        transcript_expects=(
            "sertraline",
            "atorvastatin",
            "Easter",
            "Nguyen",
        ),
        requires=("espeak-ng", "ffmpeg"),
        phase=7,
        notes=(
            "a vague date and two drug names; the date must stay a phrase. "
            "Unbiased, `small` hears 'search-reline' and 'at-or-vastatin' — "
            "which is the whole argument for the hotword list"
        ),
    ),
    Fixture(
        name="silence-and-a-cough",
        mime="audio/webm",
        render=lambda: _room_tone(40, cough_at=12),
        text="",
        readable=False,
        expected=(),
        transcript_expects=(),
        requires=("ffmpeg",),
        phase=7,
        notes=(
            "expected output: nothing at all. Whisper hallucinates on silence — "
            "'Thank you for watching' — and a hallucinated segment must never "
            "become a claim"
        ),
    ),
)


def for_phase(phase: int = 4) -> tuple[Fixture, ...]:
    """Fixtures whose readers exist. The rest are declared and skipped."""
    return tuple(fixture for fixture in FIXTURES if fixture.phase <= phase)


def by_name(name: str) -> Fixture:
    for fixture in FIXTURES:
        if fixture.name == name:
            return fixture
    raise KeyError(name)
