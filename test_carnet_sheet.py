#!/usr/bin/env python3
"""Acceptance tests for carnet_sheet.py.

Parses the generated PDFs using only the standard library (zlib + regex)
so the suite needs no extra dependencies beyond the runtime ones.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import re
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carnet_sheet  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal PDF inspection helpers                                              #
# --------------------------------------------------------------------------- #

def _pdf_objects(data: bytes) -> dict[int, bytes]:
    """Map object number -> raw object body (between 'n obj' and 'endobj')."""
    objects: dict[int, bytes] = {}
    for match in re.finditer(rb"(\d+) 0 obj(.*?)endobj", data, re.DOTALL):
        objects[int(match.group(1))] = match.group(2)
    return objects


def _decode_stream(body: bytes) -> bytes | None:
    """Decode a PDF stream, honouring ASCII85Decode and FlateDecode filters."""
    m = re.search(rb"stream\r?\n(.*?)endstream", body, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    if b"ASCII85Decode" in body:
        raw = base64.a85decode(raw.strip(), adobe=True)
    if b"FlateDecode" in body:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw
    return raw


def _page_content(data: bytes) -> bytes:
    """Return the decompressed content stream of the first page."""
    objects = _pdf_objects(data)
    for body in objects.values():
        if b"/Type /Page" in body and b"/Type /Pages" not in body and b"/Contents" in body:
            contents = re.search(rb"/Contents (\d+) 0 R", body)
            if contents:
                stream = _decode_stream(objects[int(contents.group(1))])
                if stream is not None:
                    return stream
    raise AssertionError("No page content stream found in PDF")


def _xobject_images(data: bytes) -> list[dict]:
    """Return metadata for every image XObject in the PDF."""
    images = []
    for number, body in _pdf_objects(data).items():
        if b"/Subtype /Image" in body:
            width = int(re.search(rb"/Width (\d+)", body).group(1))
            height = int(re.search(rb"/Height (\d+)", body).group(1))
            images.append({"object": number, "width": width, "height": height})
    return images


def _page_mediabox(data: bytes) -> tuple[float, float]:
    m = re.search(rb"/MediaBox \[ ?([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) ?\]", data)
    if not m:
        raise AssertionError("No MediaBox found in PDF")
    x0, y0, x1, y1 = (float(g) for g in m.groups())
    return (x1 - x0, y1 - y0)


def _image_draw_operations(content: bytes) -> list[tuple[float, float, float, float]]:
    r"""Return (x, y, w, h) for every 'cm ... Do' image placement on the page.

    ReportLab emits, per placement, a transformation matrix immediately
    before the XObject invoke:  w 0 0 h x y cm  /Ixx Do
    The XObject name may contain dots (e.g. FormXob.<hash>), hence \S+.
    """
    ops = []
    pattern = re.compile(
        rb"([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm\s*/(\S+) Do"
    )
    for m in pattern.finditer(content):
        w, h, x, y = (float(g) for g in m.groups()[:4])
        ops.append((x, y, w, h))
    return ops


def _rect_stroke_operations(content: bytes) -> list[tuple[float, float, float, float]]:
    """Return (x, y, w, h) for every stroked rectangle (borders).

    ReportLab prefixes path construction with a no-op 'n', and fills use
    'f*', so only 're S' sequences match here.
    """
    ops = []
    pattern = re.compile(
        rb"(?:^|\n)n\s+([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re\s*S"
    )
    for m in pattern.finditer(content):
        x, y, w, h = (float(g) for g in m.groups())
        ops.append((x, y, w, h))
    return ops


def _unique_image_xobjects(content: bytes) -> set[str]:
    return set(re.findall(rb"/(\S+) Do", content))


# --------------------------------------------------------------------------- #
# Test fixtures                                                               #
# --------------------------------------------------------------------------- #

CARNET_ASPECT = 3 / 4  # width / height, e.g. 600 x 800 px
LETTER = (612.0, 792.0)


def make_photo(path: Path, size: tuple[int, int] = (600, 800),
               color=(120, 40, 40), fmt: str = "PNG") -> None:
    im = Image.new("RGB", size, color)
    im.save(path, fmt)


class BaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.photo = self.tmp / "photo.png"
        make_photo(self.photo)
        self.photo_sha = hashlib.sha256(self.photo.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def generate(self, output: Path | None = None, image: Path | None = None, **kwargs):
        config = carnet_sheet.LayoutConfig(**kwargs)
        out = output or (self.tmp / "out.pdf")
        return carnet_sheet.generate_pdf(image or self.photo, out, config)


class TestValidSinglePagePdf(BaseTestCase):
    def test_output_is_valid_single_page_pdf(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        data = out.read_bytes()
        self.assertTrue(data.startswith(b"%PDF-"), "missing PDF header")
        self.assertIn(b"%%EOF", data.strip()[-64:])
        # Exactly one page object.
        pages = _pdf_objects(data)
        page_objs = [b for b in pages.values() if b"/Type /Page" in b and b"/Pages" not in b]
        self.assertEqual(len(page_objs), 1)

    def test_page_is_letter_portrait(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        self.assertEqual(_page_mediabox(out.read_bytes()), LETTER)

    def test_landscape_swaps_dimensions(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out, orientation="landscape")
        self.assertEqual(_page_mediabox(out.read_bytes()), (792.0, 612.0))


class TestDefaultLayout(BaseTestCase):
    def test_exactly_six_placements(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(ops), 6)

    def test_all_placements_use_the_same_image(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        content = _page_content(out.read_bytes())
        self.assertEqual(len(_unique_image_xobjects(content)), 1)
        # And that image is the source photo's pixel data.
        images = _xobject_images(out.read_bytes())
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["width"], 600)
        self.assertEqual(images[0]["height"], 800)

    def test_each_photo_has_configured_dimensions(self) -> None:
        out = self.tmp / "sheet.pdf"
        # Box exactly 3:4 so the 3:4 fixture image fills it without containment.
        w, h = 114.14 * 3 / 4, 114.14
        layout = carnet_sheet.compute_layout(
            carnet_sheet.LayoutConfig(photo_width=w, photo_height=h), CARNET_ASPECT
        )
        self.assertAlmostEqual(layout.photo_width, w, places=6)
        self.assertAlmostEqual(layout.photo_height, h, places=6)

    def test_aspect_ratio_preserved(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        for _x, _y, dw, dh in ops:
            self.assertAlmostEqual(dw / dh, CARNET_ASPECT, places=4)

    def test_photos_inside_page_boundaries(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        pw, ph = _page_mediabox(out.read_bytes())
        content = _page_content(out.read_bytes())
        for x, y, w, h in _image_draw_operations(content):
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, pw + 1e-6)
            self.assertLessEqual(y + h, ph + 1e-6)

    def test_layout_horizontally_centered(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        xs = [x for x, _y, w, _h in ops]
        pw, _ph = _page_mediabox(out.read_bytes())
        left = min(xs)
        right = max(x + w for x, _y, w, _h in ops)
        self.assertAlmostEqual(left, pw - right, places=2)

    def test_borders_drawn_by_default_and_omitted_with_no_border(self) -> None:
        out1 = self.tmp / "bordered.pdf"
        self.generate(output=out1)
        self.assertGreaterEqual(
            len(_rect_stroke_operations(_page_content(out1.read_bytes()))), 6
        )
        out2 = self.tmp / "plain.pdf"
        self.generate(output=out2, border=False)
        self.assertEqual(len(_rect_stroke_operations(_page_content(out2.read_bytes()))), 0)

    def test_arrangement_near_top_of_page(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        top_y = max(y + h for _x, y, _w, h in ops)  # strip's top edge
        bottom_y = min(y for _x, y, _w, _h in ops)  # strip's bottom edge
        pw, ph = _page_mediabox(out.read_bytes())
        # 30 pt top margin, small tolerance.
        self.assertAlmostEqual(ph - top_y, 30.0, places=2)
        # PDF y grows upward: the strip sits entirely in the upper half.
        self.assertGreater(bottom_y, ph * 0.5)


class TestImageHandling(BaseTestCase):
    def test_original_image_unchanged(self) -> None:
        out = self.tmp / "sheet.pdf"
        self.generate(output=out)
        self.assertEqual(
            hashlib.sha256(self.photo.read_bytes()).hexdigest(), self.photo_sha
        )

    def test_output_defaults_to_sibling_pdf_not_overwriting_input(self) -> None:
        rc = carnet_sheet.main([str(self.photo)])
        self.assertEqual(rc, 0)
        expected = self.photo.with_name("photo_sheet.pdf")
        self.assertTrue(expected.exists())
        self.assertEqual(
            hashlib.sha256(self.photo.read_bytes()).hexdigest(), self.photo_sha
        )

    def test_refuses_to_overwrite_input_when_explicit(self) -> None:
        rc = carnet_sheet.main([str(self.photo), "-o", str(self.photo)])
        self.assertEqual(rc, 2)

    def test_exif_orientation_is_respected(self) -> None:
        # 800x600 pixels with orientation 6 -> becomes 600x800 portrait.
        im = Image.new("RGB", (800, 600), (40, 120, 40))
        exif = im.getexif()
        exif[0x0112] = 6
        path = self.tmp / "rotated.jpg"
        im.save(path, "JPEG", exif=exif)
        src = carnet_sheet.load_source_image(path)
        self.assertEqual(src.pixel_size, (600, 800))
        self.assertTrue(src.exif_applied)
        out = self.tmp / "rotated_sheet.pdf"
        self.generate(output=out)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        for _x, _y, w, h in ops:
            self.assertAlmostEqual(w / h, 600 / 800, places=4)

    def test_exif_rotated_photo_embeds_oriented_pixels(self) -> None:
        # Top half green, bottom half blue; EXIF 6 rotates 90° CW, so the
        # embedded (oriented) image must be 600x800 with green on the RIGHT.
        im = Image.new("RGB", (800, 600), (0, 0, 255))
        for x in range(800):
            for y in range(300):
                im.putpixel((x, y), (0, 255, 0))
        exif = im.getexif()
        exif[0x0112] = 6
        path = self.tmp / "halves.jpg"
        im.save(path, "JPEG", exif=exif)
        out = self.tmp / "halves_sheet.pdf"
        self.generate(output=out)
        images = _xobject_images(out.read_bytes())
        self.assertEqual(len(images), 1)
        # Oriented dimensions, not the raw 800x600.
        self.assertEqual(images[0]["width"], 600)
        self.assertEqual(images[0]["height"], 800)

    def test_no_exif_keeps_original_bytes_untouched(self) -> None:
        # Without an orientation tag the original JPEG is embedded as-is
        # (pass-through, no recompression).
        path = self.tmp / "plain.jpg"
        Image.new("RGB", (300, 400), (10, 20, 30)).save(path, "JPEG")
        out = self.tmp / "plain_sheet.pdf"
        self.generate(output=out, image=path)
        images = _xobject_images(out.read_bytes())
        self.assertEqual(len(images), 1)
        self.assertEqual((images[0]["width"], images[0]["height"]), (300, 400))

    def test_transparent_png_flattened_on_white(self) -> None:
        path = self.tmp / "alpha.png"
        im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
        im.save(path, "PNG")
        out = self.tmp / "alpha_sheet.pdf"
        self.generate(output=out)  # must not raise
        self.assertTrue(out.exists())


class TestErrors(BaseTestCase):
    def test_missing_input_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.generate(image=self.tmp / "does_not_exist.png")

    def test_corrupted_image(self) -> None:
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"this is not a png at all")
        with self.assertRaises(ValueError):
            self.generate(image=bad)

    def test_layout_too_wide_errors_with_actionable_message(self) -> None:
        # Box matches the 3:4 image aspect so the drawn size equals the box.
        with self.assertRaises(carnet_sheet.LayoutError) as ctx:
            self.generate(photo_width=120.0, photo_height=160.0)  # 6*120 > 552 pt
        self.assertIn("--fit-to-page", str(ctx.exception))

    def test_layout_too_tall_errors(self) -> None:
        # A huge top margin leaves less vertical space than one photo needs.
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(top_margin=700.0)

    def test_invalid_numeric_settings(self) -> None:
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(copies=0)
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(columns=0)
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(columns=7)  # more columns than copies
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(photo_width=-1)
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(spacing_x=-1)
        with self.assertRaises(carnet_sheet.LayoutError):
            self.generate(top_margin=-5)

    def test_fit_to_page_shrinks_preserving_aspect(self) -> None:
        out = self.tmp / "fit.pdf"
        summary = self.generate(
            output=out, photo_width=120.0, photo_height=160.0, fit_to_page=True
        )
        self.assertTrue(summary["scaled"])
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        pw, _ph = _page_mediabox(out.read_bytes())
        xs = [x for x, _y, w, _h in ops]
        left, right = min(xs), max(x + w for x, _y, w, _h in ops)
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, pw + 1e-6)
        for _x, _y, w, h in ops:
            self.assertAlmostEqual(w / h, CARNET_ASPECT, places=4)
        self.assertLess(w, 120.0)  # actually reduced from the requested size

    def test_unwritable_output_reports_error(self) -> None:
        out = self.tmp / "no_dir" / "x.pdf"  # parent directory does not exist
        with self.assertRaises(RuntimeError):
            self.generate(output=out)


class TestCliUnits(BaseTestCase):
    def test_mm_option_converts_only_user_provided_sizes(self) -> None:
        # 40 mm wide box with an aspect-exact height (40 / (3/4) mm) so the
        # fixture image fills it exactly; other values stay in points.
        height_mm = 40 / CARNET_ASPECT
        rc = carnet_sheet.main(
            [
                str(self.photo),
                "--mm", "--photo-width", "40",
                "--photo-height", f"{height_mm:.4f}",
                "--columns", "4", "--copies", "4",
                "-o", str(self.tmp / "mm.pdf"),
            ]
        )
        self.assertEqual(rc, 0)
        ops = _image_draw_operations(_page_content((self.tmp / "mm.pdf").read_bytes()))
        for _x, _y, w, h in ops:
            self.assertAlmostEqual(w, 40 / 25.4 * 72, places=2)
            self.assertAlmostEqual(h, height_mm / 25.4 * 72, places=2)

    def test_mm_defaults_unchanged_when_flags_absent(self) -> None:
        rc = carnet_sheet.main(
            [str(self.photo), "--mm", "-o", str(self.tmp / "mmdef.pdf")]
        )
        self.assertEqual(rc, 0)
        ops = _image_draw_operations(
            _page_content((self.tmp / "mmdef.pdf").read_bytes())
        )
        # Same geometry as the point-default run.
        self.assertAlmostEqual(ops[0][2], 114.14 * 3 / 4, places=2)
        self.assertAlmostEqual(ops[0][3], 114.14, places=2)


class TestZeroGapSpacing(BaseTestCase):
    """Adjacent carnet photos must have zero intentional spacing."""

    def test_default_spacing_is_zero(self) -> None:
        config = carnet_sheet.LayoutConfig()
        self.assertEqual(config.spacing_x, 0.0)
        self.assertEqual(config.spacing_y, 0.0)

    def test_adjacent_photos_touch_horizontally(self) -> None:
        config = carnet_sheet.LayoutConfig()
        layout = carnet_sheet.compute_layout(config, CARNET_ASPECT)
        rects = carnet_sheet.placement_rects(layout, config)
        self.assertEqual(len(rects), 6)
        for left, right in zip(rects, rects[1:]):
            gap = right[0] - (left[0] + left[2])
            self.assertAlmostEqual(gap, 0.0, places=6)

    def test_adjacent_rows_touch_vertically(self) -> None:
        config = carnet_sheet.LayoutConfig(copies=12, columns=6)
        layout = carnet_sheet.compute_layout(config, CARNET_ASPECT)
        rects = carnet_sheet.placement_rects(layout, config)
        ys = sorted({round(y, 6) for _x, y, _w, _h in rects}, reverse=True)
        self.assertEqual(len(ys), 2)
        # Both rows have the same photo height, so row gap = 0 means the
        # vertical distance between row tops equals the photo height.
        self.assertAlmostEqual(ys[0] - ys[1], rects[0][3], places=6)

    def test_cli_default_produces_zero_gap_pdf(self) -> None:
        out = self.tmp / "zero.pdf"
        rc = carnet_sheet.main([str(self.photo), "-o", str(out)])
        self.assertEqual(rc, 0)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(ops), 6)
        ops_sorted = sorted(ops, key=lambda op: op[0])
        for left, right in zip(ops_sorted, ops_sorted[1:]):
            gap = right[0] - (left[0] + left[2])
            self.assertAlmostEqual(gap, 0.0, places=4)

    def test_explicit_spacing_is_still_honoured(self) -> None:
        config = carnet_sheet.LayoutConfig(spacing_x=6.0)
        layout = carnet_sheet.compute_layout(config, CARNET_ASPECT)
        rects = carnet_sheet.placement_rects(layout, config)
        gap = rects[1][0] - (rects[0][0] + rects[0][2])
        self.assertAlmostEqual(gap, 6.0, places=6)

    def test_margins_preserved_with_zero_gap(self) -> None:
        config = carnet_sheet.LayoutConfig()
        layout = carnet_sheet.compute_layout(config, CARNET_ASPECT)
        pw, ph = config.paper_dimensions()
        # Top margin unchanged.
        self.assertAlmostEqual(layout.y_top, ph - 30.0, places=6)
        # Content strip still horizontally centered between the margins.
        left_gap = layout.x0 - config.left_margin
        right_gap = (pw - config.right_margin) - (layout.x0 + layout.content_width)
        self.assertAlmostEqual(left_gap, right_gap, places=6)


class TestMultiImage(BaseTestCase):
    """Multiple source images, each with its own copy quantity."""

    def setUp(self) -> None:
        super().setUp()
        self.photo_b = self.tmp / "photo_b.png"
        make_photo(self.photo_b, size=(300, 400), color=(40, 120, 40))  # 3:4
        self.photo_c = self.tmp / "photo_c.png"
        make_photo(self.photo_c, size=(600, 600), color=(40, 40, 120))  # 1:1

    def test_default_quantity_is_six(self) -> None:
        config = carnet_sheet.LayoutConfig()
        pages = carnet_sheet.placement_plan([[CARNET_ASPECT] * 6], config)
        self.assertEqual(sum(len(p.placements) for p in pages), 6)

    def test_requested_copies_for_every_image(self) -> None:
        out = self.tmp / "multi.pdf"
        summary = carnet_sheet.generate_pdf(
            [self.photo, self.photo_b, self.photo_c], out,
            carnet_sheet.LayoutConfig(), quantities=[6, 6, 3],
        )
        self.assertEqual(summary["placements"], 15)
        self.assertEqual(summary["pages"], 1)

    def test_sequential_row_major_ordering(self) -> None:
        config = carnet_sheet.LayoutConfig()
        pages = carnet_sheet.placement_plan(
            [[CARNET_ASPECT] * 6, [CARNET_ASPECT] * 4, [CARNET_ASPECT] * 2], config
        )
        sequence = [p.job_index for p in pages[0].placements]
        self.assertEqual(sequence, [0] * 6 + [1] * 4 + [2] * 2)

    def test_multiple_images_share_one_sheet(self) -> None:
        config = carnet_sheet.LayoutConfig()
        pages = carnet_sheet.placement_plan(
            [[CARNET_ASPECT] * 6, [CARNET_ASPECT] * 6, [CARNET_ASPECT] * 3], config
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0].placements), 15)
        # All inside the page.
        pw, ph = config.paper_dimensions()
        for p in pages[0].placements:
            self.assertGreaterEqual(p.x, 0)
            self.assertGreaterEqual(p.y, 0)
            self.assertLessEqual(p.x + p.width, pw + 1e-6)
            self.assertLessEqual(p.y + p.height, ph + 1e-6)

    def test_pagination_to_additional_pages(self) -> None:
        # 14+13+13 = 40 placements; a letter page below a 30 pt top margin
        # fits 6 rows of 114.14 pt → 36 per page → 2 pages (36 + 4).
        out = self.tmp / "two_pages.pdf"
        summary = carnet_sheet.generate_pdf(
            [self.photo, self.photo_b, self.photo_c], out,
            carnet_sheet.LayoutConfig(), quantities=[14, 13, 13],
        )
        self.assertEqual(summary["pages"], 2)
        self.assertEqual(summary["placements"], 40)
        # Page objects in the PDF.
        data = out.read_bytes()
        page_objs = [
            b for b in _pdf_objects(data).values()
            if b"/Type /Page" in b and b"/Pages" not in b
        ]
        self.assertEqual(len(page_objs), 2)

    def test_each_image_embedded_with_its_own_pixels(self) -> None:
        out = self.tmp / "embeds.pdf"
        carnet_sheet.generate_pdf(
            [self.photo, self.photo_c], out,
            carnet_sheet.LayoutConfig(), quantities=[2, 3],
        )
        images = _xobject_images(out.read_bytes())
        sizes = sorted((im["width"], im["height"]) for im in images)
        self.assertEqual(sizes, [(600, 600), (600, 800)])

    def test_single_image_with_explicit_quantities(self) -> None:
        out = self.tmp / "four.pdf"
        summary = carnet_sheet.generate_pdf(
            self.photo, out, carnet_sheet.LayoutConfig(), quantities=[4]
        )
        self.assertEqual(summary["placements"], 4)

    def test_quantities_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            carnet_sheet.generate_pdf(
                [self.photo, self.photo_b], self.tmp / "x.pdf",
                carnet_sheet.LayoutConfig(), quantities=[6],
            )

    def test_non_positive_quantity_raises(self) -> None:
        with self.assertRaises(carnet_sheet.LayoutError):
            carnet_sheet.generate_pdf(
                [self.photo, self.photo_b], self.tmp / "x.pdf",
                carnet_sheet.LayoutConfig(), quantities=[6, 0],
            )

    def test_missing_second_image_reports_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            carnet_sheet.generate_pdf(
                [self.photo, self.tmp / "missing.png"], self.tmp / "x.pdf",
                carnet_sheet.LayoutConfig(), quantities=[6, 6],
            )

    def test_multi_image_pdf_keeps_zero_gap_and_borders(self) -> None:
        out = self.tmp / "gap.pdf"
        carnet_sheet.generate_pdf(
            [self.photo, self.photo_b], out,
            carnet_sheet.LayoutConfig(), quantities=[6, 6],
        )
        content = _page_content(out.read_bytes())
        ops = sorted(
            _image_draw_operations(content), key=lambda op: (op[1], op[0])
        )
        self.assertEqual(len(ops), 12)
        # Same-row neighbours touch (rows share y within tolerance).
        for left, right in zip(ops, ops[1:]):
            same_row = abs(left[1] - right[1]) < 1e-4
            if same_row:
                gap = right[0] - (left[0] + left[2])
                self.assertAlmostEqual(gap, 0.0, places=4)
        self.assertGreaterEqual(len(_rect_stroke_operations(content)), 12)


class TestMultiImageCli(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.photo_b = self.tmp / "photo_b.png"
        make_photo(self.photo_b, size=(300, 400), color=(40, 120, 40))

    def test_cli_copies_list_per_image(self) -> None:
        out = self.tmp / "cli_multi.pdf"
        rc = carnet_sheet.main(
            [str(self.photo), str(self.photo_b), "--copies", "2,1", "-o", str(out)]
        )
        self.assertEqual(rc, 0)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(ops), 3)

    def test_cli_copies_count_mismatch_errors(self) -> None:
        rc = carnet_sheet.main(
            [str(self.photo), str(self.photo_b), "--copies", "5",
             "-o", str(self.tmp / "x.pdf")]
        )
        self.assertEqual(rc, 2)

    def test_cli_rejects_zero_quantity(self) -> None:
        rc = carnet_sheet.main(
            [str(self.photo), "--copies", "0", "-o", str(self.tmp / "x.pdf")]
        )
        self.assertEqual(rc, 2)

    def test_cli_single_image_still_defaults_to_six(self) -> None:
        out = self.tmp / "cli_single.pdf"
        rc = carnet_sheet.main([str(self.photo), "-o", str(out)])
        self.assertEqual(rc, 0)
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(ops), 6)


class TestGeometryHelpers(BaseTestCase):
    def test_mm_conversion_round_trip(self) -> None:
        pt = carnet_sheet.mm_to_pt(30.0)
        self.assertAlmostEqual(carnet_sheet.pt_to_mm(pt), 30.0, places=6)

    def test_wrap_to_second_row(self) -> None:
        config = carnet_sheet.LayoutConfig(copies=8, columns=6)
        layout = carnet_sheet.compute_layout(config, CARNET_ASPECT)
        rects = carnet_sheet.placement_rects(layout, config)
        self.assertEqual(len(rects), 8)
        # First six share the top row; last two sit below, left-aligned.
        ys = sorted({round(y, 4) for _x, y, _w, _h in rects}, reverse=True)
        self.assertEqual(len(ys), 2)
        top_row = [r for r in rects if abs(r[1] - ys[0]) < 1e-6]
        bottom_row = [r for r in rects if abs(r[1] - ys[1]) < 1e-6]
        self.assertEqual(len(top_row), 6)
        self.assertEqual(len(bottom_row), 2)
        self.assertAlmostEqual(top_row[0][0], bottom_row[0][0], places=6)

    def test_box_containment_preserves_image_aspect(self) -> None:
        # A 3:4 image inside the reference box (0.7514 aspect) is contained:
        # width shrinks slightly so the photo is never distorted.
        config = carnet_sheet.LayoutConfig()
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertAlmostEqual(layout.photo_height, 114.14, places=6)
        self.assertAlmostEqual(layout.photo_width, 114.14 * 3 / 4, places=6)
        self.assertFalse(layout.scaled)

    def test_matching_aspect_fills_configured_box_exactly(self) -> None:
        config = carnet_sheet.LayoutConfig(
            photo_width=114.14 * 3 / 4, photo_height=114.14
        )
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertAlmostEqual(layout.photo_width, 114.14 * 3 / 4, places=6)
        self.assertAlmostEqual(layout.photo_height, 114.14, places=6)
        self.assertFalse(layout.scaled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
