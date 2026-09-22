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
    # Config building                                                     #
    # ------------------------------------------------------------------ #

    def test_defaults_build_expected_config(self) -> None:
        config = self.gui.build_config()
        self.assertEqual(config.paper, "letter")
        self.assertEqual(config.orientation, "portrait")
        self.assertEqual(config.copies, 6)
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
        self.assertAlmostEqual(config.spacing_x, 6.0, places=3)
        self.assertAlmostEqual(config.spacing_y, 12.0, places=3)
        self.assertAlmostEqual(config.top_margin, 30.0, places=3)
        self.assertAlmostEqual(config.left_margin, 30.0, places=3)
        self.assertAlmostEqual(config.right_margin, 30.0, places=3)

    # ------------------------------------------------------------------ #
    # Generation                                                          #
    # ------------------------------------------------------------------ #

    def test_generation_produces_pdf_and_status(self) -> None:
        import time
        import tkinter.messagebox as messagebox

        # Never block on the "Open it now?" dialog or launch a viewer.
        messagebox.askyesno = lambda *a, **k: False
        self.gui.image_path = self.photo
        self.gui.output_path = self.tmp / "out.pdf"
        self.gui.on_generate()
        # Pump the Tk event loop with real waits until the worker thread
        # finishes and the result has been marshalled back.
        deadline = time.time() + 60
        while time.time() < deadline:
            self.root.update()
            if not self.gui._generation_lock.locked():
                break
            time.sleep(0.02)
        else:
            self.fail("generation did not finish within 60 s")
        out = self.gui.output_path
        self.assertTrue(out.exists(), "GUI generation did not produce the PDF")
        self.assertIn("PDF written", self.gui.status.get())
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
        self.gui.image_path = self.photo
        self.gui.output_path = self.photo  # same file: must be refused
        self.gui.on_generate()
        self.assertEqual(shown.get("title"), "Could not generate the PDF")
        self.assertFalse(self.gui._generation_lock.locked())

    # ------------------------------------------------------------------ #
    # Live preview                                                        #
    # ------------------------------------------------------------------ #

    def test_preview_draws_six_photo_rectangles(self) -> None:
        self.gui.draw_preview()
        rects = [
            item
            for item in self.gui.preview.find_all()
            if self.gui.preview.type(item) == "rectangle"
        ]
        # One page rectangle + six photo rectangles.
        self.assertEqual(len(rects), 7)

    def test_preview_tolerates_invalid_entries(self) -> None:
        self.gui.var_photo_width.set("not-a-number")
        self.gui.draw_preview()  # must not raise
        self.assertEqual(len(self.gui.preview.find_all()), 0)

    def test_preview_updates_when_settings_change(self) -> None:
        self.gui.draw_preview()
        before = self.gui.preview.coords(
            [i for i in self.gui.preview.find_all()][1]
        )
        self.gui.var_copies.set(4)
        self.gui.draw_preview()
        after = self.gui.preview.coords(
            [i for i in self.gui.preview.find_all()][1]
        )
        self.assertNotEqual(before, after)

    def test_preview_shows_real_thumbnails_when_photo_selected(self) -> None:
        self.gui.image_path = self.photo
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
        self.gui.image_path = bad
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

    # ------------------------------------------------------------------ #
    # Settings persistence                                                #
    # ------------------------------------------------------------------ #

    def test_settings_saved_and_restored_between_instances(self) -> None:
        import carnet_gui

        self.gui.var_paper.set("a4")
        self.gui.var_copies.set(4)
        self.gui.var_photo_width.set("40.0000")
        self.gui.output_path = self.tmp / "somewhere.pdf"
        self.gui.var_output.set(str(self.gui.output_path))
        self.gui.save_settings()

        # A second instance on the same store must pick the values up.
        gui2 = carnet_gui.CarnetSheetGUI(self.root, store=self.store)
        self.assertEqual(gui2.var_paper.get(), "a4")
        self.assertEqual(gui2.var_copies.get(), 4)
        self.assertAlmostEqual(float(gui2.var_photo_width.get()), 40.0, places=3)
        self.assertEqual(str(gui2.output_path), str(self.gui.output_path))

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
        store.save({"paper": "a4", "copies": 4})
        self.assertEqual(store.load(), {"paper": "a4", "copies": 4})

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
        store.save({"copies": 2})
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
