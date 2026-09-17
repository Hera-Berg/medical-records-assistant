"""The deterministic half of dual-path extraction.

``pdfplumber`` for a PDF's text layer where one exists, ``pytesseract`` otherwise.
Text layer **first** for PDFs: pathology reports from patient portals almost
always have one, and running OCR over a rendered page instead makes them worse.

This is not the primary reader and must not be mistaken for one. Tesseract on a
blurry phone photo of a handwritten script is close to useless on its own. Its
job is to be a **check on VLM hallucination** — the VLM reads far better and is
also the one that will invent a plausible dose that was never on the page — so
what matters here is corroboration, not coverage.

Both libraries are optional. Without them this path reports itself unavailable
and claims come out ``uncorroborated`` rather than ``cross-verified``. That is a
weaker record, not a broken one, and it never blocks extraction: the install
extras are ``health-agent[documents]`` and ``health-agent[ocr]``.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from ..distribution import missing_library, packaged

log = logging.getLogger("agent.extract")

METHOD_TEXT_LAYER = "pdf-text-layer"
METHOD_OCR = "tesseract"
METHOD_NONE = "unavailable"


@dataclass(frozen=True)
class DeterministicRead:
    """What the non-model reader saw, and how."""

    text: str = ""
    method: str = METHOD_NONE
    #: Why there is no text, when there is none. A reported state, never an error.
    unavailable: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def digest(self) -> str | None:
        """A hash of what this path read, recorded in the extraction event.

        The text itself can be long and is re-derivable from the artefact, but
        the hash makes "did the deterministic path see the same bytes last time"
        answerable when a re-extraction disagrees.
        """
        if not self.has_text:
            return None
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def describe(self) -> dict[str, object]:
        return {
            "method": self.method,
            "characters": len(self.text),
            "digest": self.digest,
            "unavailable": self.unavailable,
        }


def _pdf_text(data: bytes) -> DeterministicRead:
    try:
        import pdfplumber  # noqa: PLC0415 - optional dependency
    except ImportError:
        return DeterministicRead(
            method=METHOD_NONE,
            unavailable=missing_library(
                "pdfplumber", "documents", "the PDF's text layer was not read",
                stored=True,
            )
            + " Claims from this artefact are uncorroborated rather than cross-verified.",
        )
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        return DeterministicRead(
            method=METHOD_NONE, unavailable=f"the PDF text layer could not be read: {exc}"
        )
    text = "\n".join(pages).strip()
    if not text:
        return DeterministicRead(
            method=METHOD_NONE,
            unavailable="this PDF has no text layer — it is a scan, not a digital document",
        )
    return DeterministicRead(text=text, method=METHOD_TEXT_LAYER)


def _ocr(data: bytes) -> DeterministicRead:
    try:
        import pytesseract  # noqa: PLC0415 - optional dependency
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return DeterministicRead(
            method=METHOD_NONE,
            # Not bundled with the app, deliberately: tesseract is a separate
            # program per platform, and this path is a check on the reader rather
            # than a reader. So in the app this is a fact, not a fault.
            unavailable=(
                "this app does not include a second reader for photographs"
                if packaged()
                else "pytesseract is not installed on the computer that tried"
            )
            + ", so nothing checked the model's reading of this image. Claims are "
            "uncorroborated rather than cross-verified.",
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            text = pytesseract.image_to_string(image)
    except Exception as exc:
        # Includes tesseract not being on PATH, which is the common case and is
        # a setup problem rather than a bad artefact.
        return DeterministicRead(
            method=METHOD_NONE, unavailable=f"tesseract could not read this image: {exc}"
        )
    return (
        DeterministicRead(text=text.strip(), method=METHOD_OCR)
        if text.strip()
        else DeterministicRead(
            method=METHOD_NONE, unavailable="tesseract found no text in this image"
        )
    )


def read(path: Path, mime: str) -> DeterministicRead:
    """Read *path* without a model. Never raises; an unreadable file is a state."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return DeterministicRead(method=METHOD_NONE, unavailable=f"cannot read {path}: {exc}")

    if mime == "application/pdf":
        found = _pdf_text(data)
        if found.has_text:
            return found
        # A scanned PDF has no text layer. OCR over the rendered page is the
        # fallback, not the first choice — see the module docstring.
        from .images import prepare_pdf  # noqa: PLC0415 - avoids an import cycle

        document = prepare_pdf(data, long_edge=2000)
        if not document.is_readable:
            return found
        pages = [_ocr(page.data) for page in document.pages]
        text = "\n".join(page.text for page in pages if page.has_text).strip()
        if text:
            return DeterministicRead(text=text, method=METHOD_OCR)
        return found

    if mime.startswith("image/"):
        return _ocr(data)

    return DeterministicRead(
        method=METHOD_NONE,
        unavailable=f"there is no deterministic reader for {mime or 'this file type'}",
    )
