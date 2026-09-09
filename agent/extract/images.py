"""Getting an artefact small enough to read, without touching the original.

``MODELS.md`` calls the image token budget "the most likely cause of an
out-of-memory crash, and it is not obvious". Qwen's vision encoder tokenises by
pixel count, so a 12-megapixel phone photo of a prescription becomes thousands of
vision tokens and blows past the context cap and the RAM budget before the model
has generated a single output token. **The image, not the prompt, is what kills
you.**

So every image is downscaled, oriented, cropped and greyscaled before it reaches
the model, and the token estimate is recorded per artefact — a sudden spike is
the signal that preprocessing was bypassed.

**The original is never sent and never modified.** It stays in ``raw/`` at mode
``0o400``; what the model sees is a derived working copy that lives in memory and
is never persisted to the vault.

**Deskew is deliberately not implemented.** ``MODELS.md`` lists it, and doing it
properly wants numpy or OpenCV; a projection-profile deskew on a blurry
handwritten script can rotate a page *away* from level and make it harder to
read, which is worse than leaving it alone. Rather than a silent gap, the
capability is reported: :data:`DESKEW_NOTE` is printed by ``health-agent probe``
and travels in the ops list on every prepared image, so a page that read badly
can be traced to a step that never ran.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from ..errors import ExtractionError

log = logging.getLogger("agent.extract")

#: What the model is sent. JPEG rather than PNG: base64 in JSON inflates by a
#: third and this goes over WireGuard, so bandwidth is a real constraint.
OUTPUT_MEDIA_TYPE = "image/jpeg"
OUTPUT_QUALITY = 85

#: Pages are rendered at 150 DPI, not 300. Higher DPI does not improve VLM
#: reading the way it improves tesseract, and it costs quadratically.
PDF_RENDER_DPI = 150

#: Qwen's encoder packs a 28x28 patch grid into one token, four patches to a
#: token. An estimate, used to spot a preprocessing bypass rather than to budget
#: precisely.
_PIXELS_PER_VISION_TOKEN = 28 * 28 * 4

DESKEW_NOTE = (
    "deskew is not implemented: it needs numpy or OpenCV, and a projection-profile "
    "deskew on a blurry handwritten script can rotate a page away from level and make "
    "it read worse. Photograph scripts square-on where you can. Every prepared image "
    "records this in its ops list, so a bad read can be traced to a step that did not "
    "run."
)


@dataclass(frozen=True)
class PreparedImage:
    """A working copy on its way to the model. Never written to the vault."""

    data: bytes
    media_type: str
    width: int
    height: int
    source_width: int
    source_height: int
    ops: tuple[str, ...] = ()
    page: int | None = None

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def vision_tokens(self) -> int:
        """Roughly what this will cost the encoder. Logged, never trusted."""
        return max(1, (self.width * self.height) // _PIXELS_PER_VISION_TOKEN)

    def describe(self) -> dict[str, Any]:
        """What the extraction event records about preprocessing."""
        return {
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "bytes": len(self.data),
            "vision_tokens_estimate": self.vision_tokens,
            "ops": list(self.ops),
            "page": self.page,
        }


@dataclass(frozen=True)
class Document:
    """Everything an artefact yields for the model: one prepared image per page.

    **One page per prompt, always.** Concatenating pages inflates the image
    budget, degrades reading accuracy, and destroys per-page citation
    granularity — a claim that cites "the discharge summary" without saying which
    page is a citation a reader cannot follow.
    """

    pages: tuple[PreparedImage, ...] = ()
    #: Set when the artefact could not be turned into anything the model can
    #: read. A reported outcome, never an exception: "could not read — review
    #: manually" is a legitimate result for a photograph of a thumb.
    unreadable: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_readable(self) -> bool:
        return bool(self.pages) and self.unreadable is None


def _autocrop(image: Image.Image) -> tuple[Image.Image, bool]:
    """Trim uniform borders — the desk, the shadow, the edge of the folder.

    Conservative by construction: the bounding box is taken from a heavy
    greyscale threshold, and a crop that would remove more than a third of the
    page is discarded on the grounds that it has probably found the document's
    own white space rather than its edge.
    """
    grey = ImageOps.grayscale(image)
    # Anything near-white is background. autocontrast first so a dim photograph
    # is not read as uniformly dark and left uncropped.
    mask = ImageOps.autocontrast(grey).point(lambda value: 255 if value < 235 else 0)
    box = mask.getbbox()
    if box is None:
        return image, False
    left, top, right, bottom = box
    area = (right - left) * (bottom - top)
    if area <= 0 or area < (image.width * image.height) * 0.66:
        return image, False
    if (left, top, right, bottom) == (0, 0, image.width, image.height):
        return image, False
    return image.crop(box), True


def _downscale(image: Image.Image, long_edge: int) -> tuple[Image.Image, bool]:
    longest = max(image.width, image.height)
    if longest <= long_edge:
        return image, False
    scale = long_edge / longest
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.LANCZOS), True


def prepare(
    data: bytes,
    long_edge: int,
    greyscale: bool = True,
    page: int | None = None,
) -> PreparedImage:
    """One image, ready for the model. *data* is never modified in place."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            image = opened.convert("RGB")
    except Exception as exc:
        raise ExtractionError(f"this does not open as an image: {exc}") from None

    source_width, source_height = image.width, image.height
    ops: list[str] = []

    # EXIF orientation first: a script photographed in portrait and stored
    # sideways is unreadable, and every later step would work on the wrong axis.
    oriented = ImageOps.exif_transpose(image)
    if oriented is not None and oriented.size != image.size:
        ops.append("exif-orientation")
        image = oriented
    elif oriented is not None:
        image = oriented

    image, cropped = _autocrop(image)
    if cropped:
        ops.append("autocrop")

    if greyscale:
        # Smaller and easier to read. A prescription loses nothing to it; the
        # colour of the paper is not evidence.
        image = ImageOps.grayscale(image)
        ops.append("greyscale")

    image, scaled = _downscale(image, long_edge)
    if scaled:
        ops.append(f"downscale-to-{long_edge}px")

    ops.append("deskew-not-implemented")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=OUTPUT_QUALITY, optimize=True)
    prepared = PreparedImage(
        data=buffer.getvalue(),
        media_type=OUTPUT_MEDIA_TYPE,
        width=image.width,
        height=image.height,
        source_width=source_width,
        source_height=source_height,
        ops=tuple(ops),
        page=page,
    )
    log.info(
        "prepared image page=%s %dx%d -> %dx%d, ~%d vision tokens",
        page,
        source_width,
        source_height,
        prepared.width,
        prepared.height,
        prepared.vision_tokens,
    )
    return prepared


def prepare_pdf(data: bytes, long_edge: int) -> Document:
    """Render each page at 150 DPI and prepare it. One page per prompt.

    ``pdfplumber`` is optional; without it a PDF is reported unreadable rather
    than guessed at. That is a real outcome and it is what the install extra is
    for — ``pip install 'health-agent[documents]'``.
    """
    try:
        import pdfplumber  # noqa: PLC0415 - optional dependency
    except ImportError:
        return Document(
            unreadable=(
                "this build cannot render PDF pages: pdfplumber is not installed. "
                "Install it with `pip install 'health-agent[documents]'` and "
                "re-extract; nothing is lost in the meantime, the artefact stays "
                "queued."
            )
        )

    pages: list[PreparedImage] = []
    notes: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                rendered = page.to_image(resolution=PDF_RENDER_DPI)
                buffer = io.BytesIO()
                rendered.original.save(buffer, format="PNG")
                pages.append(prepare(buffer.getvalue(), long_edge, page=number))
    except Exception as exc:
        return Document(unreadable=f"this PDF could not be rendered: {exc}")

    if not pages:
        return Document(unreadable="this PDF has no pages")
    return Document(pages=tuple(pages), notes=tuple(notes))


def prepare_artifact(path: Path, mime: str, long_edge: int) -> Document:
    """Whatever this artefact is, as pages the model can read."""
    if mime.startswith("audio/") or mime.startswith("video/"):
        return Document(
            unreadable=(
                "this is a recording, which the speech model transcribes rather than "
                "the vision model reading it"
            )
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExtractionError(f"cannot read {path}: {exc}") from None

    if mime == "application/pdf":
        return prepare_pdf(data, long_edge)
    if mime.startswith("image/"):
        try:
            return Document(pages=(prepare(data, long_edge, page=1),))
        except ExtractionError as exc:
            return Document(unreadable=str(exc))
    return Document(
        unreadable=f"there is no reader for {mime or 'an unknown file type'}"
    )
