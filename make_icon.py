#!/usr/bin/env python3
"""One-time generator for the application icon files (assets/)."""

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent / "assets"
ASSETS.mkdir(exist_ok=True)


def make_icon(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # Sheet background (white page with soft border).
    m = round(size * 0.06)
    d.rounded_rectangle(
        (m, m, size - m, size - m), radius=round(size * 0.10),
        fill=(255, 255, 255, 255), outline=(60, 60, 60, 255),
        width=max(1, round(size * 0.02)),
    )

    # Six vertical photo frames in a strip on the upper part of the page.
    pad = round(size * 0.14)
    strip_top = round(size * 0.18)
    strip_h = round(size * 0.34)
    gap = round(size * 0.035)
    n = 6
    frame_w = round((size - 2 * pad - (n - 1) * gap) / n)

    for i in range(n):
        x0 = pad + i * (frame_w + gap)
        # A warm dark red reminiscent of Venezuelan carnet documents.
        d.rectangle(
            (x0, strip_top, x0 + frame_w, strip_top + strip_h),
            fill=(150, 30, 40, 255),
            outline=(40, 40, 40, 255),
            width=max(1, round(size * 0.012)),
        )

    return im


base = make_icon(1024)
base.save(ASSETS / "icon_256.png", sizes=[(256, 256)])
base.save(
    ASSETS / "icon.ico",
    format="ICO",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("wrote", ASSETS / "icon.ico", "and", ASSETS / "icon_256.png")
