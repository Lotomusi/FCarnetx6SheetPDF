#!/usr/bin/env python3
"""Carnet Photo Sheet Maker.

Takes one or more finished carnet photographs and produces a print-ready PDF
containing a configurable arrangement of copies of them (by default: six
copies of a single photo in a horizontal strip near the top of a US Letter
portrait page).

Multiple photographs can be combined in one job: each source image carries
its own copy quantity, and the requested placements are laid out as one
collection (sequential/row-major ordering) onto as many pages as needed.

This tool only performs layout and document generation. It never modifies
the appearance of the photograph itself: no cropping, stretching, rotating,
color or content changes of any kind.
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
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


# True zero-gap layout: adjacent carnet photos touch, so the printed sheet
# can be cut with a single straight line and no extra trimming.
DEFAULT_SPACING_X = 0.0
DEFAULT_SPACING_Y = 0.0


@dataclass
class LayoutConfig:
    """All tunable layout parameters, with defaults matching the reference.

    photo_width/photo_height describe the target box for one photograph.
    The image is always drawn at its own aspect ratio, contained inside that
    box, so it is never stretched or squashed.

    Copies are packed with zero intentional spacing between adjacent photos
    (spacing_x/spacing_y default to 0). They remain configurable (and
    non-negative) so existing scripts keep working, but the defaults now
    produce a true 0-gap layout.
    """

    paper: str = "letter"  # key of PAPER_SIZES
    orientation: str = "portrait"  # "portrait" | "landscape"
    copies: int = 6  # total number of photo placements (single-image mode)
    columns: int = 6  # photos per row
    photo_width: float = 85.79  # points (≈ 1.19 in / 30.3 mm, from reference)
    photo_height: float = 114.14  # points (≈ 1.59 in / 40.3 mm, from reference)
    spacing_x: float = DEFAULT_SPACING_X  # gap between adjacent photos (0 = cut-together)
    spacing_y: float = DEFAULT_SPACING_Y  # gap between rows (0 = cut-together)
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


@dataclass(frozen=True)
class Placement:
    """One concrete photo placement (image + rectangle) on a page."""

    job_index: int  # index into the list of source images for this placement
    x: float  # left edge, PDF coordinates (origin bottom-left)
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class PageLayout:
    """Resolved geometry of one page of a (possibly multi-page) sheet."""

    result: LayoutResult
    placements: tuple[Placement, ...]


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
# Multi-image layout planning                                                 #
# --------------------------------------------------------------------------- #


def _drawn_size(
    box_width: float, box_height: float, aspect: float
) -> tuple[float, float]:
    """Largest (w, h) with the given aspect that fits the box (containment)."""
    by_width = (box_width, box_width / aspect)
    by_height = (box_height * aspect, box_height)
    if by_width[1] <= box_height + 1e-9:
        return by_width
    return by_height


def placement_plan(
    job_aspects: Sequence[Sequence[float]], config: LayoutConfig
) -> list[PageLayout]:
    """Plan every page needed for a multi-image job.

    ``job_aspects`` holds the aspects of each source image's requested
    placements, in sequential order. The packing is sequential/row-major:
    finish the requested copies of the first image, then continue with the
    next image, wrapping onto additional pages with the same layout rules
    when the current page's columns × rows grid is full.
    """
    if not job_aspects:
        raise LayoutError("No photographs were requested.")

    page_w, page_h = config.paper_dimensions()
    avail_h = page_h - config.top_margin
    # Rows that physically fit below the top margin (drawn heights never
    # exceed the configured box height, so this never overfills a page).
    rows_that_fit = int(avail_h // config.photo_height)
    if rows_that_fit < 1:
        if not config.fit_to_page:
            raise LayoutError(
                f"One photo of {config.photo_height:.2f} pt "
                f"({pt_to_mm(config.photo_height):.1f} mm) needs more vertical "
                f"space than the {avail_h:.2f} pt ({pt_to_mm(avail_h):.1f} mm) "
                f"available below the top margin on {config.paper} "
                f"{config.orientation}. Reduce the photo height or top "
                "margin, or use --fit-to-page."
            )
        rows_that_fit = 1  # fit-to-page shrinks the single row below
    capacity = rows_that_fit * config.columns

    # Planning grid: a page always holds `capacity` slots (validate needs
    # copies >= columns, which capacity guarantees).
    grid = replace(config, copies=capacity)
    grid.validate()

    flat: list[float] = []
    job_indices: list[int] = []
    for job_index, aspects in enumerate(job_aspects):
        flat.extend(aspects)
        job_indices.extend([job_index] * len(aspects))
    if not flat:
        raise LayoutError("No photographs were requested.")

    pages: list[PageLayout] = []
    start = 0
    while start < len(flat):
        chunk = flat[start : start + capacity]
        chunk_jobs = job_indices[start : start + capacity]
        result, placements = _plan_page(chunk, page_w, page_h, grid, chunk_jobs)
        pages.append(PageLayout(result=result, placements=tuple(placements)))
        start += len(chunk)
    return pages


def _plan_page(
    aspects: Sequence[float],
    page_w: float,
    page_h: float,
    config: LayoutConfig,
    job_indices: Sequence[int] | None = None,
) -> tuple[LayoutResult, list[Placement]]:
    """Lay out one page of photo aspects, row-major, with zero gaps.

    Every photo keeps its own aspect ratio inside the configured photo box;
    each row's height is driven by the tallest drawn photo in that row and
    the page width by the widest row. Raises LayoutError if the page does
    not fit and fit-to-page is disabled (same rules as compute_layout).

    ``job_indices`` maps each aspect back to its source image; it defaults
    to the identity when a caller only cares about geometry.
    """
    if not aspects:
        raise LayoutError("No photographs were requested.")
    if job_indices is None:
        job_indices = range(len(aspects))
    drawn = [_drawn_size(config.photo_width, config.photo_height, a) for a in aspects]
    rows_count = math.ceil(len(drawn) / config.columns)

    scale = 1.0

    def measure() -> tuple[float, float, list[float], list[float]]:
        row_widths: list[float] = []
        row_heights: list[float] = []
        for r in range(rows_count):
            row_items = drawn[r * config.columns : (r + 1) * config.columns]
            row_widths.append(sum(w for w, _h in row_items))
            row_heights.append(max(h for _w, h in row_items))
        # Rows stack vertically, but each row is centred independently, so
        # the content width is the widest row (not the sum of all rows).
        return max(row_widths), sum(row_heights), row_heights, row_widths

    cw, ch, row_heights, _row_widths = measure()
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
        # Fit to page: shrink uniformly, preserving each image's aspect ratio.
        scale = min(avail_w / cw, avail_h / ch)
        drawn = [(w * scale, h * scale) for w, h in drawn]
        cw, ch, row_heights, _row_widths = measure()

    # --- Centering (same rule as compute_layout) ------------------------------
    free_w = page_w - config.left_margin - config.right_margin - cw
    x0 = config.left_margin + max(0.0, free_w / 2.0)
    y_top = page_h - config.top_margin

    # Zero-gap packing: photos sit edge to edge inside each row.
    placements: list[Placement] = []
    y_cursor = y_top
    index = 0
    for r in range(rows_count):
        row_count = min(config.columns, len(drawn) - r * config.columns)
        row_width = sum(w for w, _h in drawn[index : index + row_count])
        x_cursor = x0 + max(0.0, (cw - row_width) / 2.0)
        for _c in range(row_count):
            w, h = drawn[index]
            placements.append(
                Placement(
                    job_index=job_indices[index],
                    x=x_cursor,
                    y=y_cursor - h,
                    width=w,
                    height=h,
                )
            )
            x_cursor += w
            index += 1
        y_cursor -= row_heights[r]

    result = LayoutResult(
        page_width=page_w,
        page_height=page_h,
        photo_width=drawn[0][0],
        photo_height=drawn[0][1],
        x0=x0,
        y_top=y_top,
        scaled=scale != 1.0,
        content_width=cw,
        content_height=ch,
    )
    return result, placements


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
    image_path: Path | Sequence[Path],
    output_path: Path,
    config: LayoutConfig,
    quantities: Sequence[int] | None = None,
) -> dict:
    """Generate the sheet PDF. Returns a summary dict describing the output.

    Single-image mode (semantics unchanged): one ``image_path`` and no
    ``quantities`` produces ``config.copies`` placements — now with the
    zero-gap defaults, so adjacent photos touch.

    Multi-image mode: pass a list of image paths and a matching
    ``quantities`` list (same length). The requested placements are packed
    sequentially (row-major): all copies of the first image, then the next,
    continuing onto extra pages with the same layout rules when needed.
    """
    from reportlab.lib.colors import black
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    # --- Normalise arguments -------------------------------------------------
    if isinstance(image_path, (list, tuple)):
        paths = [Path(p) for p in image_path]
    else:
        paths = [Path(image_path)]
    if not paths:
        raise LayoutError("No photographs were requested.")
    if quantities is not None:
        quantities = [int(q) for q in quantities]
        if len(quantities) != len(paths):
            raise ValueError(
                "quantities must have one entry per source image "
                f"({len(paths)} images, {len(quantities)} quantities)."
            )
        if any(q < 1 for q in quantities):
            raise LayoutError("Each source image needs at least 1 copy.")

    output_path = Path(output_path)

    # --- Load sources --------------------------------------------------------
    sources: list[SourceImage] = [load_source_image(p) for p in paths]

    # --- Plan the pages ------------------------------------------------------
    if quantities is None:
        # Single-image mode: classic one-page layout, semantics unchanged.
        layout = compute_layout(config, sources[0].aspect)
        rects = placement_rects(layout, config)
        pages: list[PageLayout] = [
            PageLayout(
                result=layout,
                placements=tuple(
                    Placement(job_index=0, x=x, y=y, width=w, height=h)
                    for x, y, w, h in rects
                ),
            )
        ]
    else:
        job_aspects: list[list[float]] = []
        for source, quantity in zip(sources, quantities):
            job_aspects.append([source.aspect] * quantity)
        pages = placement_plan(job_aspects, config)

    first_page = pages[0].result

    # --- Embed buffers (EXIF-oriented pixels where needed) -------------------
    embeds: dict[int, io.BytesIO] = {}
    for index, source in enumerate(sources):
        if source.exif_applied:
            with Image.open(paths[index]) as im:
                im.load()
                buffer = io.BytesIO()
                # Save the transposed pixels losslessly; the original file on
                # disk is never touched.
                ImageOps.exif_transpose(im).save(buffer, format="PNG")
                buffer.seek(0)
            embeds[index] = buffer

    # --- Draw ----------------------------------------------------------------
    c = canvas.Canvas(
        str(output_path),
        pagesize=(first_page.page_width, first_page.page_height),
        pageCompression=1,
        invariant=1,
    )
    c.setTitle("Carnet photo sheet")
    c.setAuthor("carnet_sheet.py")
    try:
        for page in pages:
            for placement in page.placements:
                # White behind the image so transparency flattens cleanly.
                c.setFillColorRGB(1, 1, 1)
                c.rect(
                    placement.x, placement.y, placement.width, placement.height,
                    stroke=0, fill=1,
                )
                if placement.job_index in embeds:
                    embeds[placement.job_index].seek(0)
                    reader = ImageReader(embeds[placement.job_index])
                else:
                    reader = ImageReader(str(paths[placement.job_index]))
                c.drawImage(
                    reader,
                    placement.x,
                    placement.y,
                    width=placement.width,
                    height=placement.height,
                    preserveAspectRatio=False,  # box already matches image aspect
                    mask=None,
                )
                if config.border:
                    c.setStrokeColor(black)
                    c.setLineWidth(config.border_width)
                    c.rect(
                        placement.x, placement.y, placement.width, placement.height,
                        stroke=1, fill=0,
                    )
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

    total_placements = sum(len(page.placements) for page in pages)
    return {
        "output": output_path,
        "page_size": (first_page.page_width, first_page.page_height),
        "pages": len(pages),
        "placements": total_placements,
        "photo_size": (first_page.photo_width, first_page.photo_height),
        "scaled": first_page.scaled,
        "content_width": first_page.content_width,
        "x0": first_page.x0,
        "exif_applied": sources[0].exif_applied,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="carnet_sheet",
        description=(
            "Generate a print-ready PDF with copies of finished carnet "
            "photographs (by default six copies of one photo in a strip near "
            "the top of a US Letter portrait page). The photos are never "
            "modified; only the layout is produced. Adjacent photos have "
            "zero spacing so the sheet can be cut in straight lines."
        ),
        epilog=(
            "Examples:\n"
            "  python carnet_sheet.py foto.jpg  →  foto_sheet.pdf with six "
            "1.19 × 1.59 in copies near the top.\n"
            "  python carnet_sheet.py a.jpg b.jpg c.jpg --copies 6,6,3  →  "
            "one combined sheet with 15 photos (extra pages if needed).\n"
            "Sizes can be given in millimetres with --mm."
        ),
    )
    p.add_argument("image", nargs="+", type=Path,
                   help="finished carnet photograph(s) (JPEG, PNG, WebP); "
                        "several can be combined on the same sheet(s)")
    p.add_argument("-o", "--output", type=Path,
                   help="output PDF path (default: <first-image>_sheet.pdf "
                        "next to the first photo)")
    p.add_argument("--paper", choices=sorted(PAPER_SIZES), default="letter",
                   help="paper size (default: letter)")
    p.add_argument("--orientation", choices=["portrait", "landscape"],
                   default="portrait", help="page orientation (default: portrait)")
    p.add_argument("--copies", default="6",
                   help="number of copies: one number for a single image; with "
                        "several images, comma-separated quantities matching the "
                        "image order (default: 6 per image)")
    p.add_argument("--columns", type=int, default=6,
                   help="photos per row (default: 6; extra copies wrap to a "
                        "second row and extra pages)")
    p.add_argument("--photo-width", type=float, default=None, metavar="SIZE",
                   help="photo width (default: 85.79 pt ≈ 30.3 mm)")
    p.add_argument("--photo-height", type=float, default=None, metavar="SIZE",
                   help="photo height (default: 114.14 pt ≈ 40.3 mm)")
    p.add_argument("--spacing-x", type=float, default=None, metavar="SIZE",
                   help="horizontal gap between photos (default: 0 pt, zero-gap)")
    p.add_argument("--spacing-y", type=float, default=None, metavar="SIZE",
                   help="vertical gap between rows (default: 0 pt, zero-gap)")
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


def check_output_not_input(
    image_path: Path | Sequence[Path], output_path: Path
) -> None:
    """Raise ValueError if the output would overwrite any input photograph."""
    paths = (
        [Path(image_path)] if isinstance(image_path, Path)
        else [Path(p) for p in image_path]
    )
    try:
        output = output_path.resolve()
    except OSError:
        return  # let later validation report the problem
    for candidate in paths:
        try:
            if output == candidate.resolve():
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
        copies=6,  # set below from the parsed quantities
        columns=args.columns,
        photo_width=size(args.photo_width, 85.79),
        photo_height=size(args.photo_height, 114.14),
        spacing_x=size(args.spacing_x, DEFAULT_SPACING_X),
        spacing_y=size(args.spacing_y, DEFAULT_SPACING_Y),
        top_margin=size(args.top_margin, 30.0),
        left_margin=size(args.left_margin, 30.0),
        right_margin=size(args.right_margin, 30.0),
        border=not args.no_border,
        fit_to_page=args.fit_to_page,
    )

    image_paths: list[Path] = list(args.image)
    output_path: Path = args.output or default_output_path(image_paths[0])
    try:
        check_output_not_input(image_paths, output_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # --- Parse per-image quantities ------------------------------------------
    try:
        if "," in args.copies:
            parts = [part.strip() for part in args.copies.split(",") if part.strip()]
            quantities = [int(part) for part in parts]
            if len(quantities) != len(image_paths):
                raise ValueError(
                    "--copies takes one comma-separated quantity per image "
                    f"({len(image_paths)} images, {len(quantities)} quantities)."
                )
            if any(q < 1 for q in quantities):
                raise ValueError("Each image needs at least 1 copy.")
            config.copies = max(quantities)
        else:
            shared = int(args.copies)
            if shared < 1:
                raise ValueError("Each image needs at least 1 copy.")
            if len(image_paths) > 1:
                raise ValueError(
                    "--copies takes one comma-separated quantity per image "
                    f"({len(image_paths)} images), e.g. --copies 6,3,2."
                )
            quantities = [shared]
            config.copies = shared
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        summary = generate_pdf(image_paths, output_path, config, quantities)
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
    pages_note = f", {summary['pages']} page(s)" if summary["pages"] > 1 else ""
    print(f"PDF written: {summary['output'].resolve()}")
    print(
        f"  page {pw:.0f} × {ph:.0f} pt, {summary['placements']} photos of "
        f"{w:.2f} × {h:.2f} pt ({pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm) "
        f"in {config.rows()} row(s){pages_note}{note}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
