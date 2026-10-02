#!/usr/bin/env python3
"""Tests for the Tkinter GUI wrapper (skips when tkinter/a display is missing)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import tkinter as tk
    from tkinter import ttk  # noqa: F401
except ImportError:  # python3-tkinter not installed
    tk = None

import carnet_sheet  # noqa: E402


@unittest.skipIf(tk is None, "tkinter not available (python3-tkinter not installed)")
@unittest.skipIf(
    __import__("os").environ.get("DISPLAY") is None
    and __import__("os").environ.get("WAYLAND_DISPLAY") is None,
    "no display server available",
)
class TestCarnetGUI(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        from PIL import Image

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.photo = self.tmp / "photo.png"
        Image.new("RGB", (600, 800), (120, 40, 40)).save(self.photo)
        self.photo_b = self.tmp / "photo_b.png"
        Image.new("RGB", (300, 400), (40, 120, 40)).save(self.photo_b)
        self.root = tk.Tk()
        self.root.withdraw()  # never actually shown
        import carnet_gui

        # Isolated store so tests never touch the real user config, and a
        # clean slate at the start of each test.
        self.store = carnet_gui.SettingsStore(self.tmp / "settings.json")
        self.gui = carnet_gui.CarnetSheetGUI(self.root, store=self.store)

    def tearDown(self) -> None:
        self.root.destroy()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ #
    # Photo list                                                          #
    # ------------------------------------------------------------------ #

    def test_added_photo_defaults_to_six_copies(self) -> None:
        self.gui.add_photo(self.photo)
        self.assertEqual(len(self.gui.photos), 1)
        self.assertEqual(self.gui.photos[0]["copies"].get(), "6")

    def test_each_photo_has_independent_quantity(self) -> None:
        self.gui.add_photo(self.photo)          # default 6
        self.gui.add_photo(self.photo_b, 3)     # explicit 3
        self.gui.photos[0]["copies"].set("2")
        self.assertEqual(self.gui.photos[0]["copies"].get(), "2")
        self.assertEqual(self.gui.photos[1]["copies"].get(), "3")

    def test_job_quantities_reads_the_list(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.add_photo(self.photo_b, 3)
        self.assertEqual(
            self.gui._job_quantities(),
            [(self.photo, 6), (self.photo_b, 3)],
        )

    def test_invalid_quantity_is_reported_not_generated(self) -> None:
        import tkinter.messagebox as messagebox

        shown = {}
        messagebox.showerror = lambda title, msg, **k: shown.update(title=title)
        self.gui.add_photo(self.photo)
        self.gui.photos[0]["copies"].set("")
        self.assertEqual(self.gui._job_quantities(), [])
        self.gui.output_path = self.tmp / "out.pdf"
        self.gui.on_generate()
        self.assertEqual(shown.get("title"), "Could not generate the PDF")
        self.assertFalse(self.gui._generation_lock.locked())

    def test_remove_selected_photo(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.add_photo(self.photo_b)
        self.gui._selected_row = 0
        self.gui.remove_selected_photo()
        self.assertEqual(len(self.gui.photos), 1)
        self.assertEqual(self.gui.photos[0]["path"], self.photo_b)

    def test_clear_photos(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.add_photo(self.photo_b)
        self.gui.clear_photos()
        self.assertEqual(self.gui.photos, [])

    # ------------------------------------------------------------------ #
    # Config building                                                     #
    # ------------------------------------------------------------------ #

    def test_defaults_build_expected_config(self) -> None:
        config = self.gui.build_config()
        self.assertEqual(config.paper, "letter")
        self.assertEqual(config.orientation, "portrait")
        self.assertEqual(config.columns, 6)
        self.assertTrue(config.border)
        # The generation config can always shrink to fit: a tweaked size
        # must never make the export fail (regression: GUI error dialog).
        self.assertTrue(config.fit_to_page)
        # mm defaults convert back to the same point values as the CLI.
        self.assertAlmostEqual(config.photo_width, 85.79, places=2)
        self.assertAlmostEqual(config.photo_height, 114.14, places=2)
        self.assertAlmostEqual(config.top_margin, 30.0, places=2)

    def test_shrink_to_fit_checkbox_defaults_to_on(self) -> None:
        # New default: tweaking the size fields never blocks the export.
        self.assertTrue(self.gui.var_fit.get())

    # ------------------------------------------------------------------ #
    # Size-field UX: presets, validation, fit summary, nudging            #
    # ------------------------------------------------------------------ #

    def test_preset_picker_applies_sizes(self) -> None:
        self.gui.var_preset.set("ID-2 35×45")
        self.gui._on_preset_selected()
        self.assertEqual(self.gui.var_photo_width.get(), "35.0000")
        self.assertEqual(self.gui.var_photo_height.get(), "45.0000")
        config = self.gui.build_config()
        self.assertAlmostEqual(config.photo_width, 35 / 25.4 * 72, places=3)

    def test_preset_picker_returns_to_stock_default(self) -> None:
        self.gui.var_preset.set("ID-2 35×45")
        self.gui._on_preset_selected()
        self.gui.var_preset.set("Carnet 30×40 (default)")
        self.gui._on_preset_selected()
        config = self.gui.build_config()
        self.assertAlmostEqual(config.photo_width, 85.79, places=3)
        self.assertAlmostEqual(config.photo_height, 114.14, places=3)

    def test_editing_sizes_marks_preset_custom(self) -> None:
        self.gui._refresh_field_validation()  # starts matched to the default
        self.assertEqual(self.gui.var_preset.get(), "Carnet 30×40 (default)")
        self.gui.var_photo_width.set("36.0000")
        self.gui._refresh_field_validation()
        self.assertEqual(self.gui.var_preset.get(), "Custom")

    def test_invalid_field_turns_red(self) -> None:
        label = self.gui._size_labels[str(self.gui.var_photo_width)]
        self.gui.var_photo_width.set("35")
        self.gui._refresh_field_validation()
        self.assertEqual(str(label.cget("foreground")), "#404040")
        self.gui.var_photo_width.set("35 mm")  # not a number
        self.gui._refresh_field_validation()
        self.assertEqual(str(label.cget("foreground")), "#e02020")

    def test_zero_photo_size_turns_red_but_zero_spacing_is_fine(self) -> None:
        # A 0 mm photo is nonsense (the arrow-key nudge clamps at 0, so a
        # user can land there); zero spacing/margins are legitimate.
        width_label = self.gui._size_labels[str(self.gui.var_photo_width)]
        spacing_label = self.gui._size_labels[str(self.gui.var_spacing_x)]
        self.gui.var_photo_width.set("0")
        self.gui.var_spacing_x.set("0")
        self.gui._refresh_field_validation()
        self.assertEqual(str(width_label.cget("foreground")), "#e02020")
        self.assertEqual(str(spacing_label.cget("foreground")), "#404040")

    def test_zero_photo_size_rejected_by_build_config(self) -> None:
        # Exporting with a zero photo size must take the same
        # "ignored invalid settings" fallback as any other typo.
        self.gui.var_photo_height.set("0")
        with self.assertRaises(carnet_sheet.LayoutError):
            self.gui.build_config()

    def test_fit_summary_survives_zero_photo_size(self) -> None:
        # Regression: a 0 mm height used to crash the preview's background
        # callback with ZeroDivisionError (float floor division by zero).
        self.gui.add_photo(self.photo)
        self.gui.var_photo_height.set("0")
        self.gui._update_fit_summary()  # must not raise
        self.assertEqual(self.gui.var_fit_summary.get(), "")

    def test_comma_decimal_separator_is_accepted(self) -> None:
        # Spanish keyboards: "35,5" must parse as 35.5.
        self.gui.var_photo_width.set("35,5")
        config = self.gui.build_config()
        self.assertAlmostEqual(config.photo_width, 35.5 / 25.4 * 72, places=3)

    def test_fit_summary_reports_single_page(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui._update_fit_summary()
        text = self.gui.var_fit_summary.get()
        self.assertIn("6 photos", text)
        self.assertIn("fits on one page", text)
        self.assertIn("30.3 × 40.3", text)

    def test_fit_summary_reports_overflow_and_shrink(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("40.0000")
        self.gui.var_photo_height.set("53.3070")
        self.gui._update_fit_summary()
        text = self.gui.var_fit_summary.get()
        self.assertIn("too large", text)
        self.assertIn("shrink", text)

    def test_fit_summary_reports_multipage(self) -> None:
        self.gui.add_photo(self.photo, 40)  # 36 fit per page
        self.gui._update_fit_summary()
        text = self.gui.var_fit_summary.get()
        self.assertIn("40 photos", text)
        self.assertIn("2 pages", text)
        self.assertIn("36 per page", text)

    def test_fit_summary_empty_without_photos(self) -> None:
        self.gui._update_fit_summary()
        self.assertEqual(self.gui.var_fit_summary.get(), "")

    def test_arrow_keys_nudge_size_field(self) -> None:
        # Invoke the handler directly: synthetic <Up> events are not
        # delivered under wine/CI, so this tests the nudge math.
        var = self.gui.var_photo_width
        var.set("35.0000")
        self.gui._nudge_size(var, +0.5)
        self.assertEqual(var.get(), "35.5")
        self.gui._nudge_size(var, +0.1)
        self.assertEqual(var.get(), "35.6")
        self.gui._nudge_size(var, -0.5)
        self.assertEqual(var.get(), "35.1")
        self.gui._nudge_size(var, -0.1)
        self.assertEqual(var.get(), "35.0")

    def test_nudge_never_goes_negative(self) -> None:
        var = self.gui.var_spacing_x
        var.set("0.0000")
        self.gui._nudge_size(var, -0.5)
        self.assertEqual(var.get(), "0.0")

    def test_nudge_ignores_invalid_field(self) -> None:
        var = self.gui.var_photo_width
        var.set("35 mm")
        self.gui._nudge_size(var, +0.5)  # must not raise, must not change
        self.assertEqual(var.get(), "35 mm")

    def test_size_entries_have_nudge_bindings(self) -> None:
        # The key bindings stay wired even though the tests drive the
        # handler directly (wine does not deliver synthetic key events).
        # Tk reports <Up> as <Key-Up> in the binding list.
        entry = self.gui._size_entries[str(self.gui.var_photo_width)]
        self.assertIn("<Key-Up>", entry.bind())
        self.assertIn("<Shift-Key-Up>", entry.bind())

    def test_uxcheck_cli_mode_routes(self) -> None:
        # --uxcheck [OUT_PDF] [PHOTO] routes to run_uxcheck; the check run
        # itself needs a display + mainloop, so only the routing is tested.
        import carnet_gui

        with mock.patch.object(carnet_gui, "run_uxcheck", return_value=7) as run:
            code = carnet_gui.main(["--uxcheck", "out.pdf", "photo.png"])
        self.assertEqual(code, 7)
        run.assert_called_once_with("out.pdf", "photo.png")
        with mock.patch.object(carnet_gui, "run_uxcheck", return_value=0) as run2:
            self.assertEqual(carnet_gui.main(["--uxcheck", "out.pdf"]), 0)
        run2.assert_called_once_with("out.pdf", None)

    def test_uxcheck_photo_is_generated(self) -> None:
        # --uxcheck without a photo argument generates its own 3:4 image.
        import carnet_gui

        target = self.tmp / "generated.png"
        carnet_gui._make_uxcheck_photo(target)
        from PIL import Image

        with Image.open(target) as img:
            self.assertEqual(img.size, (600, 800))

    def test_invalid_number_raises_actionable_layout_error(self) -> None:
        self.gui.var_photo_width.set("abc")
        with self.assertRaises(carnet_sheet.LayoutError):
            self.gui.build_config()

    def test_mm_roundtrip_matches_cli_defaults(self) -> None:
        # The mm strings shown in the form must reproduce the exact CLI
        # point defaults (this is what keeps GUI and CLI output identical).
        config = self.gui.build_config()
        self.assertAlmostEqual(config.photo_width, 85.79, places=3)
        self.assertAlmostEqual(config.photo_height, 114.14, places=3)
        self.assertAlmostEqual(config.spacing_x, 0.0, places=3)
        self.assertAlmostEqual(config.spacing_y, 0.0, places=3)
        self.assertAlmostEqual(config.top_margin, 30.0, places=3)
        self.assertAlmostEqual(config.left_margin, 30.0, places=3)
        self.assertAlmostEqual(config.right_margin, 30.0, places=3)

    # ------------------------------------------------------------------ #
    # Generation                                                          #
    # ------------------------------------------------------------------ #

    def _wait_generation_done(self, deadline_seconds: int = 60) -> None:
        import time

        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            self.root.update()
            if not self.gui._generation_lock.locked():
                return
            time.sleep(0.02)
        self.fail("generation did not finish within 60 s")

    def test_generation_produces_pdf_and_status(self) -> None:
        import tkinter.messagebox as messagebox

        # Never block on the "Open it now?" dialog or launch a viewer.
        messagebox.askyesno = lambda *a, **k: False
        self.gui.add_photo(self.photo)
        self.gui.add_photo(self.photo_b, 3)
        self.gui.output_path = self.tmp / "out.pdf"
        self.gui.on_generate()
        self._wait_generation_done()
        out = self.gui.output_path
        self.assertTrue(out.exists(), "GUI generation did not produce the PDF")
        self.assertIn("PDF written", self.gui.status.get())
        self.assertIn("9 photos", self.gui.status.get())
        self.assertEqual(out.read_bytes()[:5], b"%PDF-")

    def test_generate_without_photo_shows_info(self) -> None:
        import tkinter.messagebox as messagebox

        shown = {}

        def fake_showinfo(title, message, **kwargs):
            shown["title"] = title

        messagebox.showinfo = fake_showinfo
        self.gui.on_generate()
        self.assertEqual(shown.get("title"), "No photograph selected")
        self.assertFalse(self.gui._generation_lock.locked())

    def test_overwrite_protection_via_shared_helper(self) -> None:
        import tkinter.messagebox as messagebox

        shown = {}
        messagebox.showerror = lambda title, msg, **k: shown.update(
            title=title
        )
        self.gui.add_photo(self.photo)
        self.gui.output_path = self.photo  # same file: must be refused
        self.gui.on_generate()
        self.assertEqual(shown.get("title"), "Could not generate the PDF")
        self.assertFalse(self.gui._generation_lock.locked())

    # ------------------------------------------------------------------ #
    # Live preview                                                        #
    # ------------------------------------------------------------------ #

    def test_preview_draws_photo_rectangles(self) -> None:
        self.gui.add_photo(self.photo)  # 6 copies by default
        self.gui.draw_preview()
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        # One page rectangle + six photo rectangles.
        self.assertEqual(len(rects), 7)

    def test_preview_reflects_every_photo_in_the_list(self) -> None:
        self.gui.add_photo(self.photo)        # 6
        self.gui.add_photo(self.photo_b, 3)   # 3
        self.gui.draw_preview()
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        self.assertEqual(len(rects), 1 + 9)

    def test_preview_tolerates_invalid_entries(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("not-a-number")
        self.gui.draw_preview()  # must not raise
        self.assertEqual(len(self.gui.preview.find_all()), 0)

    def test_preview_updates_when_quantity_changes(self) -> None:
        def rects():
            return [
                item
                for item in self.gui.preview.find_all()
                if self.gui.preview.type(item) == "rectangle"
            ]

        self.gui.add_photo(self.photo)  # 6 copies
        self.gui.draw_preview()
        before = rects()
        # Page rectangle + 6 photo rectangles.
        self.assertEqual(len(before), 7)
        before_last = self.gui.preview.coords(before[-1])
        self.gui.photos[0]["copies"].set("4")
        self.gui.draw_preview()
        after = rects()
        # Page rectangle + 4 photo rectangles.
        self.assertEqual(len(after), 5)
        after_last = self.gui.preview.coords(after[-1])
        self.assertNotEqual(before_last, after_last)

    def test_preview_shows_real_thumbnails_when_photo_selected(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.draw_preview()
        images = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "image"
        ]
        self.assertEqual(len(images), 6)

    def test_preview_falls_back_to_rects_for_unreadable_photo(self) -> None:
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"not a png")
        self.gui.add_photo(bad)
        self.gui.draw_preview()  # must not raise
        images = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "image"
        ]
        self.assertEqual(len(images), 0)
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        self.assertEqual(len(rects), 7)  # page + 6 tinted frames

    def test_preview_shows_page_count_for_multipage_jobs(self) -> None:
        self.gui.add_photo(self.photo, 40)  # 36 fit per page
        self.gui.draw_preview()
        # Exact label: page count, total placements, page-1 note.
        self.assertEqual(
            self.gui.preview_pages.get(), "2 pages · 40 photos (showing page 1)"
        )
        # The canvas itself still shows page 1 only: page rectangle + the
        # 36 placements that fit on the first page.
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        self.assertEqual(len(rects), 1 + 36)

    def test_preview_label_shows_single_page_totals(self) -> None:
        # One photo, default quantity.
        self.gui.add_photo(self.photo)
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "6 photos")
        # Several photos sharing one page: the total across the list.
        self.gui.add_photo(self.photo_b, 3)
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "9 photos")
        # Exactly at the per-page capacity: still a single page.
        self.gui.clear_photos()
        self.gui.add_photo(self.photo, 36)
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "36 photos")

    def test_preview_label_transitions_across_page_threshold(self) -> None:
        self.gui.add_photo(self.photo, 35)
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "35 photos")
        # One more photo pushes the job onto a second page.
        self.gui.photos[0]["copies"].set("37")
        self.gui.draw_preview()
        self.assertEqual(
            self.gui.preview_pages.get(), "2 pages · 37 photos (showing page 1)"
        )
        # Back below the threshold: the label returns to a plain total.
        self.gui.photos[0]["copies"].set("6")
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "6 photos")

    def test_preview_label_cleared_without_a_job(self) -> None:
        # No photos: just the empty page outline, no totals.
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "")

    def test_preview_label_cleared_for_invalid_entries(self) -> None:
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("not-a-number")
        self.gui.draw_preview()  # must not raise
        self.assertEqual(self.gui.preview_pages.get(), "")

    def test_preview_label_cleared_when_layout_does_not_fit(self) -> None:
        self.gui.add_photo(self.photo)
        # With shrink-to-fit unchecked, an overflowing tweak shows the
        # exact (overflowing) geometry and the "layout does not fit" note.
        self.gui.var_fit.set(False)
        # A 300 mm photo box is taller than the space below the top margin,
        # so no row can fit. (A too-wide box alone would not overflow.)
        self.gui.var_photo_width.set("40.0000")
        self.gui.var_photo_height.set("300.0000")
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "")
        texts = [
            self.gui.preview.itemcget(item, "text")
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "text"
        ]
        self.assertIn("layout does not fit", texts)

    def test_preview_shrinks_overflowing_tweak_by_default(self) -> None:
        # Default (shrink-to-fit on): the same overflowing tweak previews
        # the shrunk arrangement instead of warning.
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("40.0000")
        self.gui.var_photo_height.set("300.0000")
        self.gui.draw_preview()
        self.assertEqual(self.gui.preview_pages.get(), "6 photos")
        texts = [
            self.gui.preview.itemcget(item, "text")
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "text"
        ]
        self.assertNotIn("layout does not fit", texts)
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        # Page rectangle + 6 shrunk photo rectangles.
        self.assertEqual(len(rects), 7)

    def test_preview_label_cleared_for_unreadable_photo(self) -> None:
        bad = self.tmp / "bad2.png"
        bad.write_bytes(b"not a png")
        self.gui.add_photo(bad)
        self.gui.draw_preview()  # must not raise
        self.assertEqual(self.gui.preview_pages.get(), "")
        texts = [
            self.gui.preview.itemcget(item, "text")
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "text"
        ]
        self.assertIn("photo cannot be displayed", texts)

    def test_preview_empty_without_photos(self) -> None:
        self.gui.draw_preview()
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        # Just the page outline, no photo frames.
        self.assertEqual(len(rects), 1)

    # ------------------------------------------------------------------ #
    # Aspect-mismatch hint                                                #
    # ------------------------------------------------------------------ #

    def test_no_hint_when_photo_matches_the_cell(self) -> None:
        # 600×800 is exactly 3:4; the default cell is 0.7514 — hairline
        # slivers only, below the nagging threshold.
        self.gui.add_photo(self.photo)
        self.assertEqual(self.gui.var_aspect_hint.get(), "")

    def test_hint_reports_wider_photo_with_top_bottom_bars(self) -> None:
        from PIL import Image

        wide = self.tmp / "wide.png"
        Image.new("RGB", (800, 600), (40, 40, 120)).save(wide)  # 4:3
        self.gui.add_photo(wide)
        hint = self.gui.var_aspect_hint.get()
        self.assertIn("photo 1", hint)
        self.assertIn("top/bottom", hint)
        self.assertIn("mm", hint)

    def test_hint_reports_taller_photo_with_left_right_bars(self) -> None:
        from PIL import Image

        tall = self.tmp / "tall.png"
        Image.new("RGB", (600, 900), (40, 120, 40)).save(tall)  # 2:3
        self.gui.add_photo(tall)
        hint = self.gui.var_aspect_hint.get()
        self.assertIn("photo 1", hint)
        self.assertIn("left/right", hint)

    def test_hint_follows_cell_size_changes(self) -> None:
        from PIL import Image

        tall = self.tmp / "tall.png"
        Image.new("RGB", (600, 900), (40, 120, 40)).save(tall)  # 2:3
        self.gui.add_photo(tall)
        self.assertTrue(self.gui.var_aspect_hint.get())

        # Resize the cell to 30 × 45 mm (2:3): the photo now matches.
        self.gui.var_photo_width.set("30.0000")
        self.gui.var_photo_height.set("45.0000")
        self.gui._update_fit_summary()
        self.assertEqual(self.gui.var_aspect_hint.get(), "")

    def test_hint_clears_when_photos_are_removed(self) -> None:
        from PIL import Image

        wide = self.tmp / "wide.png"
        Image.new("RGB", (800, 600), (120, 40, 40)).save(wide)
        self.gui.add_photo(wide)
        self.assertTrue(self.gui.var_aspect_hint.get())
        self.gui.clear_photos()
        self.assertEqual(self.gui.var_aspect_hint.get(), "")

    def test_hint_lists_each_mismatched_photo(self) -> None:
        from PIL import Image

        wide = self.tmp / "wide.png"
        Image.new("RGB", (800, 600), (120, 40, 40)).save(wide)  # 4:3
        tall = self.tmp / "tall.png"
        Image.new("RGB", (600, 900), (40, 120, 40)).save(tall)   # 2:3
        self.gui.add_photo(self.photo)  # matches: not listed
        self.gui.add_photo(wide)
        self.gui.add_photo(tall)
        hint = self.gui.var_aspect_hint.get()
        self.assertIn("2 photos", hint)
        self.assertIn("photo 2", hint)
        self.assertIn("photo 3", hint)
        self.assertNotIn("photo 1", hint)

    # ------------------------------------------------------------------ #
    # Adjust… dialog (opt-in per-photo recipe)                            #
    # ------------------------------------------------------------------ #

    def _wide_photo(self):
        from PIL import Image

        path = self.tmp / "wide.png"
        if not path.exists():
            Image.new("RGB", (800, 600), (90, 40, 40)).save(path)  # 4:3
        return path

    def _open_dialog(self, path):
        import carnet_gui

        self.gui.add_photo(path)
        index = len(self.gui.photos) - 1
        return carnet_gui.AdjustPhotoDialog(
            self.root, self.gui.photos[index], self.gui.build_config(),
        )

    def test_adjust_dialog_ok_writes_recipe(self) -> None:
        dialog = self._open_dialog(self._wide_photo())
        dialog.mode.set("fill")
        dialog.zoom.set(1.5)
        dialog.offset_x = 0.25
        dialog.offset_y = 0.4
        dialog.rotation = 90
        dialog._ok()
        self.assertEqual(
            self.gui.photos[0]["adjustment"],
            carnet_sheet.Adjustment(
                mode="fill", zoom=1.5, offset_x=0.25,
                offset_y=0.4, rotation=90,
            ),
        )

    def test_adjust_dialog_cancel_keeps_previous_recipe(self) -> None:
        recipe = carnet_sheet.parse_adjustment("fill,zoom=1.4")
        self.gui.add_photo(self._wide_photo(), 6, adjustment=recipe)
        dialog = self._open_dialog(self._wide_photo())
        dialog.mode.set("fit")
        dialog.rotation = 180
        dialog._cancel()
        self.assertEqual(self.gui.photos[0]["adjustment"], recipe)

    def test_adjust_button_reflects_recipe_state(self) -> None:
        self.gui.add_photo(self._wide_photo())
        self.gui._rebuild_photo_rows()
        buttons = [
            w for w in self.gui.photo_list_frame.winfo_children()
            if isinstance(w, ttk.Button)
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]["text"], "Adjust…")
        self.gui.photos[0]["adjustment"] = carnet_sheet.parse_adjustment("fill")
        self.gui._rebuild_photo_rows()
        buttons = [
            w for w in self.gui.photo_list_frame.winfo_children()
            if isinstance(w, ttk.Button)
        ]
        self.assertEqual(buttons[0]["text"], "Adjusted ✓")

    def test_fill_recipe_clears_the_mismatch_hint(self) -> None:
        self.gui.add_photo(self._wide_photo())  # 4:3 in a 0.75 cell: hint
        self.assertTrue(self.gui.var_aspect_hint.get())
        # Opt-in fill: effective aspect == cell aspect -> no hint.
        self.gui.photos[0]["adjustment"] = carnet_sheet.parse_adjustment("fill")
        self.gui._update_fit_summary()
        self.assertEqual(self.gui.var_aspect_hint.get(), "")

    def test_rotation_swaps_the_hint_bar_sides(self) -> None:
        # self.photo is 600×800 (3:4): matches the default cell — no hint.
        self.gui.add_photo(self.photo)
        self.assertEqual(self.gui.var_aspect_hint.get(), "")
        # Turned 90° it is effectively 4:3 -> top/bottom bars.
        self.gui.photos[0]["adjustment"] = carnet_sheet.Adjustment(rotation=90)
        self.gui._update_fit_summary()
        self.assertIn("top/bottom", self.gui.var_aspect_hint.get())

    def test_on_generate_passes_adjustments(self) -> None:
        import carnet_gui

        self.gui.add_photo(
            self._wide_photo(), 2,
            adjustment=carnet_sheet.parse_adjustment("fill,zoom=1.5"),
        )
        self.gui.add_photo(self.photo, 3)  # no recipe: None entry
        self.gui.output_path = self.tmp / "gen.pdf"
        self.gui.var_output.set(str(self.gui.output_path))
        captured: dict = {}

        def fake_worker(gui_self, paths, copies, output_path, config,
                        adjustments=None):
            captured.update(
                paths=paths, copies=copies, adjustments=adjustments
            )

        class SyncThread:
            """Run the worker synchronously: no scheduling to wait for."""

            def __init__(self, target=None, args=(), daemon=False):
                self._target, self._args = target, args

            def start(self):
                self._target(*self._args)

        with mock.patch.object(
            carnet_gui.CarnetSheetGUI, "_generate_worker", fake_worker
        ), mock.patch.object(carnet_gui.threading, "Thread", SyncThread):
            self.gui.on_generate()
        self.assertEqual(captured["adjustments"][0].zoom, 1.5)
        self.assertIsNone(captured["adjustments"][1])
        self.assertEqual(captured["copies"], [2, 3])

    def test_adjustment_recipe_persists_between_instances(self) -> None:
        import carnet_gui

        recipe = carnet_sheet.parse_adjustment(
            "fill,zoom=1.25,offset_x=0.3,rotation=90"
        )
        self.gui.add_photo(self._wide_photo(), 4, adjustment=recipe)
        self.gui.save_settings()
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        self.assertEqual(len(gui2.photos), 1)
        self.assertEqual(gui2.photos[0]["adjustment"], recipe)

    def test_effective_thumbnail_honors_recipe(self) -> None:
        self.gui.add_photo(self._wide_photo())  # 800×600 (4:3)
        plain = self.gui._effective_thumbnail(self.gui.photos[0], 75, 100)
        self.gui.photos[0]["adjustment"] = carnet_sheet.parse_adjustment("fill")
        filled = self.gui._effective_thumbnail(self.gui.photos[0], 75, 100)
        self.assertIsNotNone(plain)
        self.assertIsNotNone(filled)
        # Plain: the 4:3 photo contained in the frame (wide, short).
        # Filled: cropped to the cell ratio — fills the frame's height.
        self.assertGreater(filled.height(), plain.height())
        self.assertAlmostEqual(
            filled.width() / filled.height(), 0.75, delta=0.02
        )

    def test_uxcheck_end_to_end_passes(self) -> None:
        """The exe's own --uxcheck hook runs green end to end (no dialog)."""
        import carnet_gui

        out = self.tmp / "ux.pdf"
        log = self.tmp / "ux.uxcheck.log"
        saved_out, saved_err = sys.stdout, sys.stderr
        try:
            with mock.patch.dict("os.environ", {"UXCHECK_NO_DIALOG": "1"}):
                rc = carnet_gui.run_uxcheck(str(out), photo=str(self.photo))
            # run_uxcheck leaves stdout pointing at its log file (windowed
            # exes may have unusable stdio); close it so tearDown can
            # remove the temp dir (Windows forbids unlinking open files),
            # then restore the runner's streams.
            if sys.stdout is not saved_out:
                try:
                    sys.stdout.close()
                except OSError:
                    pass
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        self.assertEqual(rc, 0)
        logged = log.read_text(encoding="utf-8")
        self.assertIn("UX-RESULT OK", logged)
        # The Adjust… section must be part of the passing run.
        for step in (
            "mismatched photo triggers aspect hint",
            "OK writes the fill recipe",
            "generation with fill recipe produces PDF",
        ):
            self.assertIn(f"PASS {step}", logged)
        self.assertNotIn(" FAIL ", logged)

    # ------------------------------------------------------------------ #
    # Settings persistence                                                #
    # ------------------------------------------------------------------ #

    def test_settings_saved_and_restored_between_instances(self) -> None:
        import carnet_gui

        self.gui.var_paper.set("a4")
        self.gui.var_photo_width.set("40.0000")
        self.gui.output_path = self.tmp / "somewhere.pdf"
        self.gui.var_output.set(str(self.gui.output_path))
        self.gui.add_photo(self.photo)        # default 6
        self.gui.add_photo(self.photo_b, 3)   # explicit 3
        self.gui.save_settings()

        # A second instance on the same store must pick the values up.
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        self.assertEqual(gui2.var_paper.get(), "a4")
        self.assertAlmostEqual(float(gui2.var_photo_width.get()), 40.0, places=3)
        self.assertEqual(str(gui2.output_path), str(self.gui.output_path))
        self.assertEqual(len(gui2.photos), 2)
        self.assertEqual(gui2.photos[0]["copies"].get(), "6")
        self.assertEqual(gui2.photos[1]["copies"].get(), "3")
        self.assertEqual(
            [str(e["path"]) for e in gui2.photos],
            [str(self.photo), str(self.photo_b)],
        )

    def test_settings_reject_invalid_persisted_entries(self) -> None:
        import carnet_gui

        self.store.save(
            {
                "photos": [
                    {"path": str(self.photo), "copies": 0},        # below range
                    {"path": str(self.photo_b), "copies": "six"},  # wrong type
                    {"copies": 6},                                 # missing path
                    "garbage",                                     # not a dict
                ]
            }
        )
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        # Invalid quantities fall back to the default 6; entries without a
        # usable path are skipped entirely. No crash, no lost valid photos.
        self.assertEqual(len(gui2.photos), 2)
        self.assertEqual(gui2.photos[0]["copies"].get(), "6")
        self.assertEqual(gui2.photos[1]["copies"].get(), "6")
        self.assertEqual(
            [str(e["path"]) for e in gui2.photos],
            [str(self.photo), str(self.photo_b)],
        )

    def test_older_settings_do_not_disable_shrink_to_fit(self) -> None:
        """An old stored fit=False must not re-break tweaked-size exports."""
        import carnet_gui

        self.store.save(
            {
                "settings_version": 2,
                "fit": False,
                "photo_width": 30.26,
                "photo_height": 40.26,
            }
        )
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        self.assertTrue(gui2.var_fit.get())
        # ...and the generation config stays shrink-safe.
        self.assertTrue(gui2.build_config().fit_to_page)

    def test_pre_zero_gap_settings_get_zero_gap_defaults(self) -> None:
        """Settings saved by the old (spaced) version must not pin old gaps."""
        import carnet_gui

        old_mm = carnet_gui.pt_to_mm
        self.store.save(
            {
                "settings_version": 1,  # pre-zero-gap release
                "photo_width": 30.26,
                "photo_height": 40.26,
                "spacing_x": old_mm(6.0),
                "spacing_y": old_mm(12.0),
                "top_margin": 10.58,
                "left_margin": 10.58,
                "right_margin": 10.58,
            }
        )
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        # Spacings fall back to the new zero-gap defaults; other values are
        # still restored so nothing else resets.
        self.assertAlmostEqual(float(gui2.var_spacing_x.get()), 0.0, places=6)
        self.assertAlmostEqual(float(gui2.var_spacing_y.get()), 0.0, places=6)
        self.assertAlmostEqual(float(gui2.var_top_margin.get()), 10.58, places=3)
        self.assertAlmostEqual(float(gui2.var_photo_width.get()), 30.26, places=3)

    def test_generation_survives_overflowing_size_tweak(self) -> None:
        """Problem-1 regression: a tweaked 40x53 mm box must still export.

        Six 40 mm-wide photos do not fit between letter margins; the GUI
        must shrink the sheet (like --fit-to-page) instead of failing.
        """
        import tkinter.messagebox as messagebox

        shown = {}
        messagebox.askyesno = lambda *a, **k: False
        messagebox.showerror = lambda title, msg, **k: shown.update(
            title=title, msg=msg
        )
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("40.0000")
        self.gui.var_photo_height.set("53.3070")
        self.gui.output_path = self.tmp / "tweaked.pdf"
        self.gui.on_generate()
        self.assertFalse(
            shown, f"unexpected error dialog: {shown.get('msg')}"
        )
        self._wait_generation_done()
        out = self.gui.output_path
        self.assertTrue(out.exists())
        self.assertEqual(out.read_bytes()[:5], b"%PDF-")
        self.assertIn("PDF written", self.gui.status.get())
        self.assertIn("shrunk to fit", self.gui.status.get())

    def test_generation_ignores_invalid_field_instead_of_erroring(
        self,
    ) -> None:
        """A blank/garbage field falls back to stock settings, not a dialog."""
        import tkinter.messagebox as messagebox

        shown = {}
        messagebox.askyesno = lambda *a, **k: False
        messagebox.showerror = lambda title, msg, **k: shown.update(
            title=title, msg=msg
        )
        self.gui.add_photo(self.photo)
        self.gui.var_photo_width.set("35 mm")  # not parseable as a number
        self.gui.output_path = self.tmp / "fallback.pdf"
        self.gui.on_generate()
        self.assertFalse(shown, f"unexpected error dialog: {shown.get('msg')}")
        self._wait_generation_done()
        out = self.gui.output_path
        self.assertTrue(out.exists())
        self.assertEqual(out.read_bytes()[:5], b"%PDF-")
        # The fallback is reported on the status line.
        self.assertIn("ignored invalid settings", self.gui.status.get())

    def test_store_survives_corrupt_file(self) -> None:
        import carnet_gui

        self.store.path.write_text("{not json", encoding="utf-8")
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        # Defaults, not a crash.
        self.assertEqual(gui2.var_paper.get(), "letter")


@unittest.skipIf(tk is None, "tkinter not available (python3-tkinter not installed)")
class TestSettingsStore(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roundtrip(self) -> None:
        import carnet_gui

        store = carnet_gui.SettingsStore(self.path)
        store.save({"paper": "a4", "columns": 4})
        self.assertEqual(store.load(), {"paper": "a4", "columns": 4})

    def test_corrupt_file_degrades_to_defaults(self) -> None:
        import carnet_gui

        self.path.write_text("{not json", encoding="utf-8")
        store = carnet_gui.SettingsStore(self.path)
        self.assertEqual(store.load(), {})

    def test_missing_file_degrades_to_defaults(self) -> None:
        import carnet_gui

        store = carnet_gui.SettingsStore(self.path / "missing" / "s.json")
        self.assertEqual(store.load(), {})

    def test_save_creates_parent_directories(self) -> None:
        import carnet_gui

        store = carnet_gui.SettingsStore(self.path)
        store.save({"columns": 2})
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
