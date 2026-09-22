#!/usr/bin/env python3
"""Carnet Photo Sheet Maker.

Takes one finished carnet photograph and produces a print-ready PDF
containing a configurable arrangement of identical copies of it on a
single page (by default: six copies in a horizontal strip near the top
of a US Letter portrait page).

This tool only performs layout and document generation. It never modifies
the appearance of the photograph itself: no cropping, stretching, rotating,
color or content changes of any kind.
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# --------------------------------------------------------------------------- #
# Physical units                                                              #
# --------------------------------------------------------------------------- #

MM_PER_INCH = 25.4
MM_PER_POINT = MM_PER_INCH / 72.0  # 1 pt = 25.4/72 mm

# US Letter portrait, in points (1/72 inch).
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "letter": (612.0, 792.0),
    "a4": (595.2755905511812, 841.8897637795277),
    "legal": (612.0, 1008.0),
}


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_POINT


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_POINT


# --------------------------------------------------------------------------- #
# Layout configuration                                                        #
# --------------------------------------------------------------------------- #


class LayoutError(ValueError):
    """Raised when the requested layout cannot be produced as specified."""


@dataclass
class LayoutConfig:
    """All tunable layout parameters, with defaults matching the reference.

    photo_width/photo_height describe the target box for one photograph.
    The image is always drawn at its own aspect ratio, contained inside that
    box, so it is never stretched or squashed.
    """

    paper: str = "letter"  # key of PAPER_SIZES
    orientation: str = "portrait"  # "portrait" | "landscape"
    copies: int = 6  # total number of photo placements
    columns: int = 6  # photos per row
    photo_width: float = 85.79  # points (≈ 1.19 in / 30.3 mm, from reference)
    photo_height: float = 114.14  # points (≈ 1.59 in / 40.3 mm, from reference)
    spacing_x: float = 6.0  # horizontal gap between photos, points
    spacing_y: float = 12.0  # vertical gap between rows, points
    top_margin: float = 30.0  # page top margin, points
    left_margin: float = 30.0  # page left margin, points
    right_margin: float = 30.0  # page right margin, points
    border: bool = True  # draw thin rectangles around each photo
    border_width: float = 0.4  # border line width, points
    fit_to_page: bool = False  # shrink to fit instead of erroring

    def paper_dimensions(self) -> tuple[float, float]:
        """Return (width, height) in points for the chosen paper/orientation."""
        try:
            w, h = PAPER_SIZES[self.paper]
        except KeyError:
            valid = ", ".join(sorted(PAPER_SIZES))
            raise LayoutError(
                f"Unknown paper size {self.paper!r}. Valid sizes: {valid}."
            ) from None
        if self.orientation == "landscape":
            return (h, w)
        if self.orientation == "portrait":
            return (w, h)
        raise LayoutError(
            f"Unknown orientation {self.orientation!r}. "
            "Use 'portrait' or 'landscape'."
        )

    def rows(self) -> int:
        return math.ceil(self.copies / self.columns)

    def validate(self) -> None:
        """Raise LayoutError with an actionable message if the config is invalid."""
        if self.paper not in PAPER_SIZES:
            valid = ", ".join(sorted(PAPER_SIZES))
            raise LayoutError(
                f"Unknown paper size {self.paper!r}. Valid sizes: {valid}."
            )
        if self.orientation not in ("portrait", "landscape"):
            raise LayoutError(
                f"Unknown orientation {self.orientation!r}. "
                "Use 'portrait' or 'landscape'."
            )
        if self.copies < 1:
            raise LayoutError("Number of copies must be at least 1.")
        if self.columns < 1:
            raise LayoutError("Number of columns must be at least 1.")
        if self.columns > self.copies:
            raise LayoutError(
                f"Columns ({self.columns}) cannot exceed the number of "
                f"copies ({self.copies})."
            )
        if self.photo_width <= 0 or self.photo_height <= 0:
            raise LayoutError("Photo width and height must be positive numbers.")
        if self.spacing_x < 0 or self.spacing_y < 0:
            raise LayoutError("Spacing cannot be negative.")
        if self.top_margin < 0 or self.left_margin < 0 or self.right_margin < 0:
            raise LayoutError("Margins cannot be negative.")


@dataclass(frozen=True)
class LayoutResult:
    """Fully resolved geometry for one page."""

    page_width: float
    page_height: float
    photo_width: float  # actual drawn size (image aspect, inside the box)
    photo_height: float
    x0: float  # left edge of the content strip
    y_top: float  # top edge of the content strip (PDF coords, origin bottom-left)
    scaled: bool  # True if fit-to-page reduced the requested size
    content_width: float
    content_height: float


def _row_col(index: int, columns: int) -> tuple[int, int]:
    return index // columns, index % columns


def compute_layout(config: LayoutConfig, image_aspect: float) -> LayoutResult:
    """Resolve the full page geometry, preserving the image's aspect ratio.

    The photo is drawn at the largest size that (a) keeps the image's own
    aspect ratio and (b) fits inside the configured photo box. When the image
    aspect equals the box aspect (the expected case for a finished carnet
    photo), the drawn size is exactly the configured size.

    Raises LayoutError if the arrangement does not fit the page and
    fit-to-page is disabled.
    """
    config.validate()

    page_w, page_h = config.paper_dimensions()
    rows = config.rows()

    # --- Drawn photo size: contain the image inside the configured box. -----
    by_width = (config.photo_width, config.photo_width / image_aspect)
    by_height = (config.photo_height * image_aspect, config.photo_height)
    # Choose the containment that uses the most area without exceeding the box.
    if by_width[1] <= config.photo_height + 1e-9:
        draw_w, draw_h = by_width
    else:
        draw_w, draw_h = by_height

    scale = 1.0

    def content(w: float, h: float) -> tuple[float, float]:
        cw = config.columns * w + (config.columns - 1) * config.spacing_x
        ch = rows * h + (rows - 1) * config.spacing_y
        return cw, ch

    cw, ch = content(draw_w, draw_h)
    avail_w = page_w - config.left_margin - config.right_margin
    avail_h = page_h - config.top_margin

    if cw > avail_w + 1e-6 or ch > avail_h + 1e-6:
        if not config.fit_to_page:
            if cw > avail_w + 1e-6:
                raise LayoutError(
                    f"The {config.columns}-column layout needs {cw:.2f} pt "
                    f"({pt_to_mm(cw):.1f} mm) of horizontal space, but only "
                    f"{avail_w:.2f} pt ({pt_to_mm(avail_w):.1f} mm) is available "
                    f"between the margins on {config.paper} {config.orientation}. "
                    "Reduce the photo width/spacing or the column count, or use "
                    "--fit-to-page."
                )
            raise LayoutError(
                f"The layout needs {ch:.2f} pt ({pt_to_mm(ch):.1f} mm) of "
                f"vertical space, but only {avail_h:.2f} pt "
                f"({pt_to_mm(avail_h):.1f} mm) is available below the top margin "
                f"on {config.paper} {config.orientation}. Reduce the photo "
                "height/spacing or the number of rows, or use --fit-to-page."
            )
        # Fit to page: shrink uniformly, preserving the image aspect ratio.
        s = min(
            avail_w / cw,
            avail_h / ch,
        )
        scale = s
        draw_w *= s
        draw_h *= s
        cw, ch = content(draw_w, draw_h)

    # --- Centering -----------------------------------------------------------
    free_w = page_w - config.left_margin - config.right_margin - cw
    x0 = config.left_margin + max(0.0, free_w / 2.0)
    y_top = page_h - config.top_margin

    return LayoutResult(
        page_width=page_w,
        page_height=page_h,
        photo_width=draw_w,
        photo_height=draw_h,
        x0=x0,
        y_top=y_top,
        scaled=scale != 1.0,
        content_width=cw,
        content_height=ch,
    )


def placement_rects(
    layout: LayoutResult, config: LayoutConfig
) -> list[tuple[float, float, float, float]]:
    """Return (x, y, w, h) for every photo placement, in PDF coordinates."""
    rects: list[tuple[float, float, float, float]] = []
    for index in range(config.copies):
        row, col = _row_col(index, config.columns)
        x = layout.x0 + col * (layout.photo_width + config.spacing_x)
        y = (
            layout.y_top
            - (row + 1) * layout.photo_height
            - row * config.spacing_y
        )
        rects.append((x, y, layout.photo_width, layout.photo_height))
    return rects


# --------------------------------------------------------------------------- #
# Image handling                                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceImage:
    """The photograph, exactly as it will be embedded on every placement."""

    path: Path
    pixel_size: tuple[int, int]  # (width, height) after EXIF orientation
    aspect: float  # width / height
    exif_applied: bool


def load_source_image(path: Path) -> SourceImage:
    """Open and inspect the image without modifying it.

    Returns the pixel size after applying EXIF orientation in-memory, which
    is the orientation-aware aspect ratio used for layout calculations.
    """
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Input path is not a file: {path}")

    try:
        with Image.open(path) as im:
            im.load()
            exif = getattr(im, "getexif", lambda: {})()
            exif_applied = bool(exif.get(0x0112))
            oriented = ImageOps.exif_transpose(im)
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Could not open {path.name} as an image ({exc.__class__.__name__}: "
            f"{exc}). Make sure the file is a valid JPEG, PNG or WebP "
            "photograph."
        ) from exc

    width, height = oriented.size
    if width <= 0 or height <= 0:
        raise ValueError(f"Image {path.name} has no readable pixels.")
    return SourceImage(
        path=path,
        pixel_size=(width, height),
        aspect=width / height,
        exif_applied=bool(exif_applied),
    )


# --------------------------------------------------------------------------- #
# PDF generation                                                              #
# --------------------------------------------------------------------------- #


def generate_pdf(
    image_path: Path,
    output_path: Path,
    config: LayoutConfig,
) -> dict:
    """Generate the sheet PDF. Returns a summary dict describing the output."""
    from reportlab.lib.colors import black
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image_path = Path(image_path)
    output_path = Path(output_path)

    src = load_source_image(image_path)
    layout = compute_layout(config, src.aspect)
    rects = placement_rects(layout, config)

    # Embed the EXIF-oriented pixels. Without EXIF rotation the original
    # file is passed to ReportLab untouched (no recompression, no quality
    # loss for JPEG); with an orientation tag the raw pixels would appear
    # sideways, so a one-time oriented buffer is prepared instead.
    embed_path = image_path
    oriented_buffer: io.BytesIO | None = None
    if src.exif_applied:
        with Image.open(image_path) as im:
            im.load()
            oriented_buffer = io.BytesIO()
            # Save the transposed pixels losslessly; the original file on
            # disk is never touched.
            ImageOps.exif_transpose(im).save(oriented_buffer, format="PNG")
        oriented_buffer.seek(0)

    c = canvas.Canvas(
        str(output_path),
        pagesize=(layout.page_width, layout.page_height),
        pageCompression=1,
        invariant=1,
    )
    c.setTitle("Carnet photo sheet")
    c.setAuthor("carnet_sheet.py")
    try:
        for x, y, w, h in rects:
            # White behind the image so transparency flattens cleanly.
            c.setFillColorRGB(1, 1, 1)
            c.rect(x, y, w, h, stroke=0, fill=1)
            if oriented_buffer is not None:
                oriented_buffer.seek(0)
                reader = ImageReader(oriented_buffer)
            else:
                reader = ImageReader(str(image_path))
            c.drawImage(
                reader,
                x,
                y,
                width=w,
                height=h,
                preserveAspectRatio=False,  # box already matches image aspect
                mask=None,
            )
            if config.border:
                c.setStrokeColor(black)
                c.setLineWidth(config.border_width)
                c.rect(x, y, w, h, stroke=1, fill=0)
        c.showPage()
        c.save()
    except Exception as exc:
        # Never leave a broken file behind claiming success.
        try:
            output_path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"PDF generation failed: {exc.__class__.__name__}: {exc}"
        ) from exc

    return {
        "output": output_path,
        "page_size": (layout.page_width, layout.page_height),
        "placements": len(rects),
        "photo_size": (layout.photo_width, layout.photo_height),
        "scaled": layout.scaled,
        "content_width": layout.content_width,
        "x0": layout.x0,
        "exif_applied": src.exif_applied,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="carnet_sheet",
        description=(
            "Generate a print-ready PDF with six identical copies of a finished "
            "carnet photograph on one US Letter portrait page. The photo is "
            "never modified; only the layout is produced."
        ),
        epilog=(
            "Example: python carnet_sheet.py foto.jpg  →  foto_sheet.pdf "
            "with six 1.19 × 1.59 in copies in a centered strip near the top. "
            "Sizes can be given in millimetres with --mm."
        ),
    )
    p.add_argument("image", type=Path,
                   help="finished carnet photograph (JPEG, PNG, WebP)")
    p.add_argument("-o", "--output", type=Path,
                   help="output PDF path (default: <image>_sheet.pdf next to the photo)")
    p.add_argument("--paper", choices=sorted(PAPER_SIZES), default="letter",
                   help="paper size (default: letter)")
    p.add_argument("--orientation", choices=["portrait", "landscape"],
                   default="portrait", help="page orientation (default: portrait)")
    p.add_argument("--copies", type=int, default=6, help="number of copies (default: 6)")
    p.add_argument("--columns", type=int, default=6,
                   help="photos per row (default: 6; extra copies wrap to a second row)")
    p.add_argument("--photo-width", type=float, default=None, metavar="SIZE",
                   help="photo width (default: 85.79 pt ≈ 30.3 mm)")
    p.add_argument("--photo-height", type=float, default=None, metavar="SIZE",
                   help="photo height (default: 114.14 pt ≈ 40.3 mm)")
    p.add_argument("--spacing-x", type=float, default=None, metavar="SIZE",
                   help="horizontal gap between photos (default: 6 pt)")
    p.add_argument("--spacing-y", type=float, default=None, metavar="SIZE",
                   help="vertical gap between rows (default: 12 pt)")
    p.add_argument("--top-margin", type=float, default=None, metavar="SIZE",
                   help="top margin (default: 30 pt)")
    p.add_argument("--left-margin", type=float, default=None, metavar="SIZE",
                   help="left margin (default: 30 pt)")
    p.add_argument("--right-margin", type=float, default=None, metavar="SIZE",
                   help="right margin (default: 30 pt)")
    p.add_argument("--no-border", action="store_true",
                   help="omit the thin rectangles around each photo")
    p.add_argument("--fit-to-page", action="store_true",
                   help="shrink photos (aspect ratio preserved) to fit the page "
                        "instead of failing when the layout does not fit")
    p.add_argument("--mm", action="store_true",
                   help="interpret sizes and margins in millimetres instead of points")
    return p


def default_output_path(image_path: Path) -> Path:
    """Default output: <image>_sheet.pdf next to the photograph."""
    return image_path.with_name(image_path.stem + "_sheet.pdf")


def check_output_not_input(image_path: Path, output_path: Path) -> None:
    """Raise ValueError if the output would overwrite the input photograph."""
    try:
        if output_path.resolve() == image_path.resolve():
            raise ValueError(
                "The output PDF would overwrite the input photograph. "
                "Choose a different output path."
            )
    except OSError:
        pass  # let later validation report the problem


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # With --mm, only explicitly provided sizes are interpreted as
    # millimetres; unset options keep their point defaults.
    factor = mm_to_pt(1.0) if args.mm else 1.0

    def size(value: float | None, default_pt: float) -> float:
        return value * factor if value is not None else default_pt

    config = LayoutConfig(
        paper=args.paper,
        orientation=args.orientation,
        copies=args.copies,
        columns=args.columns,
        photo_width=size(args.photo_width, 85.79),
        photo_height=size(args.photo_height, 114.14),
        spacing_x=size(args.spacing_x, 6.0),
        spacing_y=size(args.spacing_y, 12.0),
        top_margin=size(args.top_margin, 30.0),
        left_margin=size(args.left_margin, 30.0),
        right_margin=size(args.right_margin, 30.0),
        border=not args.no_border,
        fit_to_page=args.fit_to_page,
    )

    image_path: Path = args.image
    output_path: Path = args.output or default_output_path(image_path)
    try:
        check_output_not_input(image_path, output_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        summary = generate_pdf(image_path, output_path, config)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except IsADirectoryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (LayoutError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"Error: cannot write output ({exc}).", file=sys.stderr)
        return 2

    w, h = summary["photo_size"]
    pw, ph = summary["page_size"]
    note = ""
    if summary["scaled"]:
        note = (
            " (--fit-to-page reduced the requested size to "
            f"{pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm, aspect ratio preserved)"
        )
    print(f"PDF written: {summary['output'].resolve()}")
    print(
        f"  page {pw:.0f} × {ph:.0f} pt, {summary['placements']} photos of "
        f"{w:.2f} × {h:.2f} pt ({pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm) "
        f"in {config.rows()} row(s){note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
