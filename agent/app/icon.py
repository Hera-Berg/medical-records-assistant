"""The app's icon, drawn in code so the repository carries no binary image.

A plain page with a folded corner and two ruled lines: a record, not a brand,
and not a medical cross — that symbol has owners and a meaning this app does
not claim. Dark ink on a white page reads on a light menu bar and a dark one.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

INK = (31, 41, 36, 255)
PAPER = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = size / 32
    stroke = max(1, round(2 * unit))
    left, top, right, bottom = 6 * unit, 3 * unit, 26 * unit, 29 * unit
    fold = 7 * unit
    outline = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    pen.polygon(outline, fill=PAPER, outline=INK, width=stroke)
    pen.line([(right - fold, top), (right - fold, top + fold), (right, top + fold)], fill=INK, width=stroke)
    for row in (15, 20, 25):
        y = row * unit
        pen.line([(left + 4 * unit, y), (right - 4 * unit, y)], fill=INK, width=stroke)
    return image


def tray_image() -> Image.Image:
    return draw(64)


def save_app_icon(path: str) -> None:
    """A 1024 px PNG, which PyInstaller turns into .icns and .ico at build time."""
    draw(1024).save(path)
