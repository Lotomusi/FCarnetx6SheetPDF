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

    def test_placement_plan_rejects_zero_photo_size(self) -> None:
        # Zero cell sizes are nonsense input (reachable in the GUI by
        # nudging a size down to 0, which clamps at 0). placement_plan
        # must raise the standard LayoutError instead of crashing with
        # ZeroDivisionError inside the preview's background callback.
        for kwargs in ({"photo_width": 0.0}, {"photo_height": 0.0}):
            with self.subTest(**kwargs):
                config = carnet_sheet.LayoutConfig(**kwargs)
                with self.assertRaises(carnet_sheet.LayoutError):
                    carnet_sheet.placement_plan([[0.75] * 6], config)

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
        # The image content inside each cell is aspect-true; a 3:4 image in
        # the (slightly wider) reference box is inset by a hair on each
        # side, and neighbours still touch cell-edge to cell-edge.
        cell = 85.79
        drawn_inset = (cell - ops[0][2]) / 2.0
        ops_sorted = sorted(ops, key=lambda op: op[0])
        for left, right in zip(ops_sorted, ops_sorted[1:]):
            gap = right[0] - (left[0] + left[2])
            self.assertAlmostEqual(gap, 2 * drawn_inset, places=4)
        # ...and the cells themselves still sit at an exact zero gap.
        for i, (left, right) in enumerate(zip(ops_sorted, ops_sorted[1:])):
            self.assertAlmostEqual(
                right[0] - drawn_inset - (left[0] - drawn_inset) - cell,
                0.0, places=4,
                msg=f"cell gap at index {i} is not zero",
            )

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
        # Same-row neighbours touch (rows share y within tolerance). The
        # image content is aspect-true inside each cell, so a 3:4 image in
        # the wider reference box leaves a symmetrical sliver on each side:
        # cell-edge gaps stay 0 while content gaps equal the two insets.
        drawn_inset = (85.79 - ops[0][2]) / 2.0
        for left, right in zip(ops, ops[1:]):
            same_row = abs(left[1] - right[1]) < 1e-4
            if same_row:
                gap = right[0] - (left[0] + left[2])
                self.assertAlmostEqual(gap, 2 * drawn_inset, places=4)
        self.assertGreaterEqual(len(_rect_stroke_operations(content)), 12)


class TestExactCellSizing(BaseTestCase):
    """Problem-2 regression: the configured size is honoured for any image.

    Every cell must measure exactly photo_width x photo_height whatever the
    image's aspect ratio is; the image itself is embedded aspect-true inside
    the cell (letterboxed/pillarboxed on white, never distorted).
    """

    def test_default_box_is_exact_for_every_aspect(self) -> None:
        aspects = {
            "3:4": 3 / 4,
            "4:3": 4 / 3,
            "1:1": 1.0,
            "2:3": 2 / 3,
            "passport-cam (1000x1299)": 1000 / 1299,
        }
        for name, aspect in aspects.items():
            with self.subTest(image=name):
                config = carnet_sheet.LayoutConfig()
                layout = carnet_sheet.compute_layout(config, aspect)
                self.assertAlmostEqual(layout.photo_width, 85.79, places=6)
                self.assertAlmostEqual(layout.photo_height, 114.14, places=6)

    def test_tweaked_sizes_keep_exact_six_by_width_math(self) -> None:
        # A user widening the box to 35 mm must get exactly 35 mm cells:
        # with 5 columns the strip is 5 x 35 mm (previously the engine
        # silently shrank the cells to 33.75 mm for a 3:4 image).
        config = carnet_sheet.LayoutConfig(
            photo_width=carnet_sheet.mm_to_pt(35),
            photo_height=carnet_sheet.mm_to_pt(45),
            columns=5,
            copies=5,
        )
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertAlmostEqual(
            layout.content_width, carnet_sheet.mm_to_pt(175), places=3
        )

    def test_oversized_tweak_shrinks_with_fit_to_page(self) -> None:
        # 6 x 35 mm does not fit between letter margins; the CLI/GUI
        # "shrink to fit" path must scale it down uniformly instead of
        # failing, and report the actual (smaller) cell size.
        config = carnet_sheet.LayoutConfig(
            photo_width=carnet_sheet.mm_to_pt(35),
            photo_height=carnet_sheet.mm_to_pt(45),
            fit_to_page=True,
        )
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertTrue(layout.scaled)
        self.assertLess(layout.photo_width, carnet_sheet.mm_to_pt(35))

    def test_pdf_cells_exact_when_image_aspect_differs(self) -> None:
        # 4:3 landscape image inside the default portrait box: the drawn
        # cells (border rectangles) must still be exactly 85.79 x 114.14 pt,
        # with the image pillarboxed on white inside each one.
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600), color=(40, 40, 120))  # 4:3
        out = self.tmp / "wide_sheet.pdf"
        summary = carnet_sheet.generate_pdf(
            wide, out, carnet_sheet.LayoutConfig()
        )
        self.assertEqual(summary["photo_size"], (85.79, 114.14))
        borders = _rect_stroke_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(borders), 6)
        for _x, _y, w, h in borders:
            self.assertAlmostEqual(w, 85.79, places=4)
            self.assertAlmostEqual(h, 114.14, places=4)

    def test_pdf_image_content_stays_aspect_true(self) -> None:
        # ...and the embedded content keeps the image's own aspect ratio.
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600), color=(40, 40, 120))  # 4:3
        out = self.tmp / "wide_content.pdf"
        carnet_sheet.generate_pdf(wide, out, carnet_sheet.LayoutConfig())
        ops = _image_draw_operations(_page_content(out.read_bytes()))
        for _x, _y, w, h in ops:
            self.assertAlmostEqual(w / h, 4 / 3, places=4)  # not 85.79/114.14
            self.assertLessEqual(w, 85.79 + 1e-6)
            self.assertLessEqual(h, 114.14 + 1e-6)

    def test_multimix_cells_share_exact_geometry(self) -> None:
        # Mixed-aspect images on one sheet: identical cell sizes for all
        # (verified via the border rectangles, which trace the cells).
        square = self.tmp / "square.png"
        make_photo(square, size=(600, 600), color=(40, 120, 40))  # 1:1
        out = self.tmp / "mix.pdf"
        carnet_sheet.generate_pdf(
            [self.photo, square], out,
            carnet_sheet.LayoutConfig(), quantities=[2, 2],
        )
        borders = _rect_stroke_operations(_page_content(out.read_bytes()))
        self.assertEqual(len(borders), 4)
        widths = {round(w, 4) for _x, _y, w, _h in borders}
        heights = {round(h, 4) for _x, _y, _w, h in borders}
        self.assertEqual(widths, {85.79})
        self.assertEqual(heights, {114.14})


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
        # The cell is always exactly the configured box (here the reference
        # 0.7514-aspect box); the image keeps its own aspect *inside* it.
        config = carnet_sheet.LayoutConfig()
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertAlmostEqual(layout.photo_height, 114.14, places=6)
        self.assertAlmostEqual(layout.photo_width, 85.79, places=6)
        self.assertFalse(layout.scaled)

    def test_matching_aspect_fills_configured_box_exactly(self) -> None:
        config = carnet_sheet.LayoutConfig(
            photo_width=114.14 * 3 / 4, photo_height=114.14
        )
        layout = carnet_sheet.compute_layout(config, 3 / 4)
        self.assertAlmostEqual(layout.photo_width, 114.14 * 3 / 4, places=6)
        self.assertAlmostEqual(layout.photo_height, 114.14, places=6)
        self.assertFalse(layout.scaled)


class TestAdjustments(BaseTestCase):
    """Opt-in per-photo crop recipes (Adjustment / prepare_image / CLI)."""

    CELL_ASPECT = 85.79 / 114.14  # the default photo box

    def make_gradient(self, size: tuple[int, int] = (100, 80)) -> Image.Image:
        """Image whose pixel (x, y) is (2x, 2y, 0) — crop position visible."""
        im = Image.new("RGB", size)
        px = im.load()
        for x in range(size[0]):
            for y in range(size[1]):
                px[x, y] = (x * 2, y * 2, 0)
        return im

    # ---------------- recipe parsing / validation ---------------------- #

    def test_parse_adjustment_specs(self) -> None:
        self.assertEqual(carnet_sheet.parse_adjustment("fill"),
                         carnet_sheet.Adjustment(mode="fill"))
        self.assertEqual(
            carnet_sheet.parse_adjustment("fill,zoom=1.3,offset_x=0.2,rotation=90"),
            carnet_sheet.Adjustment(mode="fill", zoom=1.3,
                                    offset_x=0.2, rotation=90),
        )
        self.assertEqual(
            carnet_sheet.parse_adjustment(" fit , zoom = 2 "),
            carnet_sheet.Adjustment(mode="fit", zoom=2.0),
        )

    def test_parse_adjustment_rejects_garbage(self) -> None:
        for bad in ("bogus", "fill,zoom=abc", "fill,unknown=1",
                    "fill,zoom=0.5", "fill,offset_x=2", "fill,rotation=45"):
            with self.assertRaises(carnet_sheet.LayoutError, msg=bad):
                carnet_sheet.parse_adjustment(bad)

    def test_identity_recipe_and_effective_aspect(self) -> None:
        self.assertTrue(carnet_sheet.Adjustment().is_identity())
        self.assertTrue(
            carnet_sheet.Adjustment(mode="fit", zoom=3.0).is_identity()
        )  # zoom/offset are irrelevant when the whole photo is kept
        self.assertFalse(carnet_sheet.Adjustment(mode="fill").is_identity())
        self.assertFalse(
            carnet_sheet.Adjustment(mode="fit", rotation=90).is_identity()
        )
        self.assertAlmostEqual(
            carnet_sheet.Adjustment(mode="fill")
            .effective_aspect(4 / 3, self.CELL_ASPECT),
            self.CELL_ASPECT,
        )
        self.assertAlmostEqual(
            carnet_sheet.Adjustment(mode="fit", rotation=90)
            .effective_aspect(3 / 4, self.CELL_ASPECT),
            4 / 3,
        )
        self.assertAlmostEqual(
            carnet_sheet.Adjustment(mode="fit", rotation=180)
            .effective_aspect(3 / 4, self.CELL_ASPECT),
            3 / 4,
        )

    def test_adjustment_from_dict_is_tolerant(self) -> None:
        recipe = carnet_sheet.Adjustment(mode="fill", zoom=1.5, rotation=90)
        self.assertEqual(
            carnet_sheet.Adjustment.from_dict(recipe.to_dict()), recipe
        )
        self.assertIsNone(carnet_sheet.Adjustment.from_dict("nonsense"))
        self.assertIsNone(carnet_sheet.Adjustment.from_dict(None))
        self.assertIsNone(carnet_sheet.Adjustment.from_dict(
            {"mode": "fill", "zoom": "x"}))
        self.assertIsNone(carnet_sheet.Adjustment.from_dict(
            {"mode": "bogus"}))

    def test_adjustment_validate_errors(self) -> None:
        for bad in (
            carnet_sheet.Adjustment(mode="bogus"),
            carnet_sheet.Adjustment(zoom=0.5),
            carnet_sheet.Adjustment(zoom=11.0),
            carnet_sheet.Adjustment(offset_x=-0.1),
            carnet_sheet.Adjustment(offset_y=1.5),
            carnet_sheet.Adjustment(rotation=45),
        ):
            with self.assertRaises(carnet_sheet.LayoutError):
                bad.validate()

    # ---------------- crop_to_aspect window math ----------------------- #

    def test_crop_window_is_largest_aspect_true_fit(self) -> None:
        out = carnet_sheet.crop_to_aspect(self.make_gradient(), 0.75)
        self.assertEqual(out.size, (60, 80))          # 100×80 → 60×80

    def test_crop_window_zoom_shrinks_uniformly(self) -> None:
        out = carnet_sheet.crop_to_aspect(self.make_gradient(), 0.75, zoom=2.0)
        self.assertEqual(out.size, (30, 40))          # exactly half

    def test_crop_window_offsets_slide_and_clamp(self) -> None:
        g = self.make_gradient()
        # zoom 1: vertical slack is zero, offset_x 1.0 → x0 = 40
        right = carnet_sheet.crop_to_aspect(g, 0.75, 1.0, 1.0, 1.0)
        self.assertEqual(right.size, (60, 80))
        self.assertEqual(right.getpixel((0, 0)), (80, 0, 0))
        left = carnet_sheet.crop_to_aspect(g, 0.75, 1.0, 0.0, 0.0)
        self.assertEqual(left.getpixel((0, 0)), (0, 0, 0))
        # zoom 2: window 30×40, slacks 70/40
        corner = carnet_sheet.crop_to_aspect(g, 0.75, 2.0, 1.0, 1.0)
        self.assertEqual(corner.getpixel((0, 0)), (140, 80, 0))
        centre = carnet_sheet.crop_to_aspect(g, 0.75, 2.0, 0.5, 0.5)
        self.assertEqual(centre.getpixel((0, 0)), (70, 40, 0))

    def test_crop_matches_return_the_image_unchanged(self) -> None:
        g = self.make_gradient((75, 100))
        out = carnet_sheet.crop_to_aspect(g, 0.75)
        self.assertEqual(out.size, g.size)
        self.assertEqual(list(out.getdata()), list(g.getdata()))

    # ---------------- prepare_image (file in, pixels out) --------------- #

    def test_prepare_fill_crops_to_target_aspect(self) -> None:
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600))                      # 4:3
        out = carnet_sheet.prepare_image(
            wide, carnet_sheet.parse_adjustment("fill"), self.CELL_ASPECT)
        self.assertAlmostEqual(out.width / out.height, self.CELL_ASPECT,
                               delta=0.01)
        zoomed = carnet_sheet.prepare_image(
            wide, carnet_sheet.parse_adjustment("fill,zoom=2"), self.CELL_ASPECT)
        self.assertAlmostEqual(zoomed.width / out.width, 0.5, delta=0.01)

    def test_prepare_fit_rotation_is_lossless_quarter_turns(self) -> None:
        quad = self.tmp / "quad.png"
        im = Image.new("RGB", (2, 2))
        im.putdata(
            [(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)]
        )
        im.save(quad)
        rot90 = carnet_sheet.prepare_image(
            quad, carnet_sheet.parse_adjustment("fit,rotation=90"))
        self.assertEqual(rot90.size, (2, 2))  # square: only pixels move
        self.assertEqual(
            list(rot90.getdata()),
            [(3, 3, 3), (1, 1, 1), (4, 4, 4), (2, 2, 2)],
        )
        tall = self.tmp / "tall.png"
        make_photo(tall, size=(600, 800))
        turned = carnet_sheet.prepare_image(
            tall, carnet_sheet.parse_adjustment("fit,rotation=90"))
        self.assertEqual(turned.size, (800, 600))
        turned2 = carnet_sheet.prepare_image(
            tall, carnet_sheet.parse_adjustment("fit,rotation=270"))
        self.assertEqual(turned2.size, (800, 600))
        full = carnet_sheet.prepare_image(
            tall, carnet_sheet.parse_adjustment("fit"))
        self.assertEqual(full.size, (600, 800))

    def test_prepare_flattens_transparency_on_white(self) -> None:
        rgba = self.tmp / "rgba.png"
        im = Image.new("RGBA", (10, 10), (255, 0, 0, 0))     # transparent
        im.save(rgba)
        out = carnet_sheet.prepare_image(rgba, carnet_sheet.Adjustment())
        self.assertEqual(out.mode, "RGB")
        self.assertEqual(out.getpixel((5, 5)), (255, 255, 255))

    def test_prepare_never_modifies_the_original_file(self) -> None:
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600))
        before = hashlib.sha256(wide.read_bytes()).hexdigest()
        carnet_sheet.prepare_image(
            wide, carnet_sheet.parse_adjustment("fill,zoom=1.4,offset_x=0.2"),
            self.CELL_ASPECT)
        after = hashlib.sha256(wide.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_fill_without_target_aspect_is_an_error(self) -> None:
        with self.assertRaises(carnet_sheet.LayoutError):
            carnet_sheet.prepare_image(
                self.photo, carnet_sheet.parse_adjustment("fill"))

    # ---------------- generate_pdf integration ------------------------- #

    def embedded_sizes(self, pdf: Path) -> list[tuple[int, int]]:
        return [(img["width"], img["height"])
                for img in _xobject_images(pdf.read_bytes())]

    def test_fill_recipe_embeds_cell_aspect_pixels(self) -> None:
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600))                      # 4:3
        out = self.tmp / "filled.pdf"
        carnet_sheet.generate_pdf(
            wide, out, carnet_sheet.LayoutConfig(),
            adjustments=[carnet_sheet.parse_adjustment("fill")])
        data = out.read_bytes()
        sizes = self.embedded_sizes(out)
        self.assertEqual(len(sizes), 1)
        self.assertAlmostEqual(sizes[0][0] / sizes[0][1], self.CELL_ASPECT,
                               delta=0.01)
        # six placements of that one image, cells still exactly sized
        self.assertEqual(len(_image_draw_operations(_page_content(data))), 6)

    def test_identity_recipe_matches_no_adjustment_output(self) -> None:
        plain = self.tmp / "plain.pdf"
        ident = self.tmp / "ident.pdf"
        carnet_sheet.generate_pdf(self.photo, plain, carnet_sheet.LayoutConfig())
        carnet_sheet.generate_pdf(
            self.photo, ident, carnet_sheet.LayoutConfig(),
            adjustments=[carnet_sheet.Adjustment()])
        self.assertEqual(self.embedded_sizes(plain), self.embedded_sizes(ident))
        self.assertEqual(
            _image_draw_operations(_page_content(plain.read_bytes())),
            _image_draw_operations(_page_content(ident.read_bytes())),
        )

    def test_fit_rotation_embeds_rotated_pixels(self) -> None:
        tall = self.tmp / "tall.png"
        make_photo(tall, size=(600, 800))
        out = self.tmp / "turned.pdf"
        carnet_sheet.generate_pdf(
            tall, out, carnet_sheet.LayoutConfig(),
            adjustments=[carnet_sheet.parse_adjustment("fit,rotation=90")])
        self.assertEqual(self.embedded_sizes(out), [(800, 600)])

    def test_multi_image_adjustments_apply_per_photo(self) -> None:
        tall = self.tmp / "tall.png"
        wide = self.tmp / "wide.png"
        make_photo(tall, size=(600, 800))                      # 3:4
        make_photo(wide, size=(800, 600))                       # 4:3
        out = self.tmp / "mixed.pdf"
        carnet_sheet.generate_pdf(
            [tall, wide], out, carnet_sheet.LayoutConfig(),
            quantities=[3, 3],
            adjustments=[
                None,
                carnet_sheet.parse_adjustment("fill,zoom=2,offset_x=0.25"),
            ])
        sizes = set(self.embedded_sizes(out))
        self.assertEqual(len(self.embedded_sizes(out)), 2)     # two objects
        self.assertIn((600, 800), sizes)                       # untouched
        for w, h in sizes:
            if (w, h) != (600, 800):
                self.assertAlmostEqual(w / h, self.CELL_ASPECT, delta=0.01)

    def test_adjustment_count_mismatch_is_an_error(self) -> None:
        out = self.tmp / "bad.pdf"
        with self.assertRaises(ValueError):
            carnet_sheet.generate_pdf(
                self.photo, out, carnet_sheet.LayoutConfig(),
                adjustments=[])
        with self.assertRaises(carnet_sheet.LayoutError):
            carnet_sheet.generate_pdf(
                self.photo, out, carnet_sheet.LayoutConfig(),
                adjustments=[carnet_sheet.Adjustment(mode="bogus")])

    # ---------------- CLI --adjust -------------------------------------- #

    def test_cli_adjust_fill_crops_to_cell(self) -> None:
        wide = self.tmp / "wide.png"
        make_photo(wide, size=(800, 600))
        out = self.tmp / "cli_fill.pdf"
        rc = carnet_sheet.main(
            [str(wide), "-o", str(out), "--adjust", "fill"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        for w, h in self.embedded_sizes(out):
            self.assertAlmostEqual(w / h, self.CELL_ASPECT, delta=0.01)

    def test_cli_adjust_fit_rotation_turns_photo(self) -> None:
        out = self.tmp / "cli_rot.pdf"
        rc = carnet_sheet.main(
            [str(self.photo), "-o", str(out), "--adjust", "fit,rotation=90"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.embedded_sizes(out), [(800, 600)])

    def test_cli_adjust_bogus_spec_is_actionable_error(self) -> None:
        for spec in ("bogus", "fill,zoom=abc", "fill,rotation=45"):
            with contextlib.redirect_stderr(io.StringIO()) as captured:
                rc = carnet_sheet.main(
                    [str(self.photo), "-o",
                     str(self.tmp / f"bad_{hash(spec) % 9973}.pdf"),
                     "--adjust", spec])
            self.assertEqual(rc, 2, spec)
            self.assertIn("Error", captured.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
