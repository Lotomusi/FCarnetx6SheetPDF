#!/usr/bin/env python3
"""Tests for the Tkinter GUI wrapper (skips when tkinter/a display is missing)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
        self.assertFalse(config.fit_to_page)
        # mm defaults convert back to the same point values as the CLI.
        self.assertAlmostEqual(config.photo_width, 85.79, places=2)
        self.assertAlmostEqual(config.photo_height, 114.14, places=2)
        self.assertAlmostEqual(config.top_margin, 30.0, places=2)

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
        # A 300 mm photo box is taller than the space below the top margin,
        # so no row can fit; fit-to-page is off. (A too-wide box alone would
        # not overflow: the 3:4 image is contained inside it.)
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
