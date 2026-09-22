#!/usr/bin/env python3
"""Minimal Tkinter GUI for the Carnet Photo Sheet Maker.

The ideal workflow from the project brief: select the finished photograph,
optionally adjust a few layout settings, click Generate, receive the PDF.

Extras:
- A live visual preview of the sheet layout that updates as settings change.
- Settings persist between sessions in the platform's user config directory.
- A window icon (and, when frozen with PyInstaller, the exe carries the
  same icon and version metadata).

This window performs layout only — it never modifies the photograph.
The heavy lifting (validation, layout math, PDF writing) lives in
carnet_sheet.py and is shared with the command-line interface.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

# Allow running the GUI directly from a checkout (python carnet_gui.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from carnet_sheet import (  # noqa: E402
    LayoutConfig,
    LayoutError,
    PAPER_SIZES,
    check_output_not_input,
    compute_layout,
    default_output_path,
    generate_pdf,
    load_source_image,
    mm_to_pt,
    placement_rects,
    pt_to_mm,
)

APP_NAME = "Carnet Photo Sheet Maker"
APP_VERSION = "1.0.0"

# Settings shown in the GUI. Units always display in millimetres; the
# point-based defaults are converted for display and converted back on
# generation, so the generated geometry is identical to the CLI defaults.
DEFAULTS_MM = {
    "photo_width": pt_to_mm(85.79),
    "photo_height": pt_to_mm(114.14),
    "spacing_x": pt_to_mm(6.0),
    "spacing_y": pt_to_mm(12.0),
    "top_margin": pt_to_mm(30.0),
    "left_margin": pt_to_mm(30.0),
    "right_margin": pt_to_mm(30.0),
}

# Decimal places used when displaying/persisting mm fields. 4 keeps the
# round-trip lossless to ~0.0003 pt for every default (1 mm ≈ 2.83 pt), so
# the GUI reproduces the CLI geometry exactly.
MM_DECIMALS = 4


def format_mm(key: str, value_mm: float) -> str:
    return f"{value_mm:.{MM_DECIMALS}f}"




IMAGE_FILETYPES = [
    ("Image files", "*.jpg *.jpeg *.png *.webp"),
    ("JPEG", "*.jpg *.jpeg"),
    ("PNG", "*.png"),
    ("WebP", "*.webp"),
    ("All files", "*.*"),
]


# --------------------------------------------------------------------------- #
# Settings persistence                                                        #
# --------------------------------------------------------------------------- #


def config_file() -> Path:
    """Per-user config path: %APPDATA% on Windows, XDG config on Linux."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
    return base / "CarnetSheetMaker" / "settings.json"


class SettingsStore:
    """Tiny JSON-backed store; all failures degrade to defaults."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            pass  # persistence is best-effort; never block generation


# --------------------------------------------------------------------------- #
# Icon loading                                                                #
# --------------------------------------------------------------------------- #


def load_icon() -> tk.PhotoImage | None:
    """Load the window icon from the bundled assets, if available."""
    candidates = []
    base = Path(__file__).resolve().parent
    candidates.append(base / "assets" / "icon_256.png")
    # When frozen with PyInstaller --onefile, assets are unpacked here:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, Path(meipass) / "assets" / "icon_256.png")
    for candidate in candidates:
        if candidate.exists():
            try:
                return tk.PhotoImage(file=str(candidate))
            except tk.TclError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Main window                                                                 #
# --------------------------------------------------------------------------- #


class CarnetSheetGUI:
    def __init__(self, root: tk.Tk, store: SettingsStore | None = None) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.resizable(False, False)
        self._generation_lock = threading.Lock()
        self.store = store or SettingsStore()

        self.image_path: Path | None = None
        self.output_path: Path | None = None
        self._preview_job: str | None = None
        self._save_job: str | None = None
        self._thumb_cache: dict = {}
        self._thumb_refs: list = []
        self._results: queue.Queue = queue.Queue()

        self._build_variables()
        self._build_layout()
        self._attach_traces()
        self._apply_window_icon()
        self.load_settings()

    # ------------------------------------------------------------------ #
    # UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_variables(self) -> None:
        self.var_image = tk.StringVar(value="")
        self.var_output = tk.StringVar(value="")

        self.var_paper = tk.StringVar(value="letter")
        self.var_orientation = tk.StringVar(value="portrait")
        self.var_copies = tk.IntVar(value=6)
        self.var_columns = tk.IntVar(value=6)
        self.var_border = tk.BooleanVar(value=True)
        self.var_fit = tk.BooleanVar(value=False)

        self.var_photo_width = tk.StringVar(
            value=format_mm("photo_width", DEFAULTS_MM["photo_width"])
        )
        self.var_photo_height = tk.StringVar(
            value=format_mm("photo_height", DEFAULTS_MM["photo_height"])
        )
        self.var_spacing_x = tk.StringVar(
            value=format_mm("spacing_x", DEFAULTS_MM["spacing_x"])
        )
        self.var_spacing_y = tk.StringVar(
            value=format_mm("spacing_y", DEFAULTS_MM["spacing_y"])
        )
        self.var_top_margin = tk.StringVar(
            value=format_mm("top_margin", DEFAULTS_MM["top_margin"])
        )
        self.var_left_margin = tk.StringVar(
            value=format_mm("left_margin", DEFAULTS_MM["left_margin"])
        )
        self.var_right_margin = tk.StringVar(
            value=format_mm("right_margin", DEFAULTS_MM["right_margin"])
        )

        # Numeric variables are a convenience for tests/scripts.
        self.var_copies.trace_add("write", lambda *_: self._schedule_preview())
        self.var_columns.trace_add("write", lambda *_: self._schedule_preview())

    def _build_layout(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.grid(sticky="nsew")

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")

        # --- Left column: photo, output, settings -----------------------
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="nw")

        box = ttk.LabelFrame(left, text="Photograph", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Entry(box, textvariable=self.var_image, width=42, state="readonly").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(box, text="Choose photo…", command=self.choose_photo).grid(
            row=0, column=1, padx=(8, 0)
        )
        box.columnconfigure(0, weight=1)

        box = ttk.LabelFrame(left, text="Output PDF", padding=8)
        box.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Entry(box, textvariable=self.var_output, width=42).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(box, text="Save as…", command=self.choose_output).grid(
            row=0, column=1, padx=(8, 0)
        )
        box.columnconfigure(0, weight=1)

        box = ttk.LabelFrame(left, text="Layout settings (millimetres)", padding=8)
        box.grid(row=2, column=0, sticky="ew", **pad)

        ttk.Label(box, text="Paper:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            box, textvariable=self.var_paper, values=sorted(PAPER_SIZES),
            state="readonly", width=10,
        ).grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(box, text="Orientation:").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            box, textvariable=self.var_orientation,
            values=["portrait", "landscape"], state="readonly", width=10,
        ).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(box, text="Copies:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(box, from_=1, to=36, textvariable=self.var_copies, width=6).grid(
            row=1, column=1, sticky="w", padx=(4, 16), pady=(6, 0)
        )
        ttk.Label(box, text="Columns:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        tk.Spinbox(box, from_=1, to=36, textvariable=self.var_columns, width=6).grid(
            row=1, column=3, sticky="w", padx=4, pady=(6, 0)
        )

        size_fields = [
            ("Photo width (mm):", self.var_photo_width),
            ("Photo height (mm):", self.var_photo_height),
            ("Horizontal spacing (mm):", self.var_spacing_x),
            ("Vertical spacing (mm):", self.var_spacing_y),
            ("Top margin (mm):", self.var_top_margin),
            ("Left margin (mm):", self.var_left_margin),
            ("Right margin (mm):", self.var_right_margin),
        ]
        for row, (label, variable) in enumerate(size_fields, start=2):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(box, textvariable=variable, width=10).grid(
                row=row, column=1, sticky="w", padx=(4, 16)
            )

        ttk.Checkbutton(
            box, text="Draw thin borders (cutting guides)",
            variable=self.var_border,
        ).grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Checkbutton(
            box, text="Shrink to fit page instead of erroring",
            variable=self.var_fit,
        ).grid(row=3, column=2, columnspan=2, sticky="w")

        # --- Right column: live preview ---------------------------------
        preview_box = ttk.LabelFrame(top, text="Preview", padding=6)
        preview_box.grid(row=0, column=1, sticky="nw", padx=(12, 0))
        self.preview = tk.Canvas(
            preview_box, width=210, height=272, bg="#f0f0f0", highlightthickness=1,
            highlightbackground="#b0b0b0",
        )
        self.preview.pack()

        # --- Generate ----------------------------------------------------
        buttons = ttk.Frame(main, padding=(8, 4))
        buttons.grid(row=1, column=0, sticky="ew")
        self.button_generate = ttk.Button(
            buttons, text="Generate PDF", command=self.on_generate
        )
        self.button_generate.grid(row=0, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)

        self.status = tk.StringVar(
            value="Select a photograph, then click Generate PDF."
        )
        ttk.Label(main, textvariable=self.status, wraplength=560).grid(
            row=2, column=0, sticky="w", **pad
        )

    def _apply_window_icon(self) -> None:
        icon = load_icon()
        if icon is not None:
            self._icon_ref = icon  # keep a reference; Tk images are GC'd otherwise
            self.root.iconphoto(True, icon)

    def _attach_traces(self) -> None:
        traced = [
            self.var_paper, self.var_orientation, self.var_border, self.var_fit,
            self.var_photo_width, self.var_photo_height,
            self.var_spacing_x, self.var_spacing_y,
            self.var_top_margin, self.var_left_margin, self.var_right_margin,
        ]
        for var in traced:
            var.trace_add("write", lambda *_: self._schedule_preview())
        self.var_output.trace_add("write", lambda *_: self._save_settings_deferred())

    # ------------------------------------------------------------------ #
    # Live preview                                                        #
    # ------------------------------------------------------------------ #

    def _schedule_preview(self) -> None:
        """Debounce redraws while the user types."""
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(120, self.draw_preview)

    def _preview_thumbnail(self, width_px: int, height_px: int):
        """Return a tk.PhotoImage of the photo at the given preview size.

        The thumbnail matches what the PDF will contain: EXIF-oriented and
        flattened onto white if it has transparency. Cached by (path, size).
        """
        from PIL import Image, ImageOps

        if self.image_path is None:
            return None
        key = (str(self.image_path), width_px, height_px)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        try:
            with Image.open(self.image_path) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    background = Image.new("RGB", im.size, (255, 255, 255))
                    to_rgb = (
                        im.convert("RGBA") if im.mode != "RGBA" else im
                    )
                    background.paste(to_rgb, mask=to_rgb.split()[-1])
                    im = background
                else:
                    im = im.convert("RGB")
                im.thumbnail((width_px, height_px), Image.LANCZOS)
                thumb = tk.PhotoImage(
                    width=im.width, height=im.height
                )
                # Tk's PhotoImage has no native PNG decoder in older builds;
                # writing through PPM keeps this dependency-free and fast.
                import io as _io

                buffer = _io.BytesIO()
                im.save(buffer, format="PPM")
                buffer.seek(0)
                thumb = tk.PhotoImage(data=buffer.read(), format="PPM")
        except Exception:
            return None  # unreadable photo: caller falls back to a plain rect
        self._thumb_cache[key] = thumb
        return thumb

    def draw_preview(self) -> None:
        """Redraw the miniature sheet; on any problem, degrade gracefully."""
        self._preview_job = None
        canvas = self.preview
        canvas.delete("all")
        try:
            config = self.build_config()
        except Exception:
            return  # incomplete/invalid entry: show an empty canvas

        # An unreadable photo must fall back to tinted rectangles, not be
        # confused with a layout that does not fit.
        aspect: float | None = None
        if self.image_path is not None:
            try:
                aspect = load_source_image(self.image_path).aspect
            except Exception:
                aspect = None
        if aspect is None and self.image_path is not None:
            self._draw_tinted_frames(config, canvas)
            canvas.create_text(
                105, 136, text="photo cannot be displayed",
                font=("TkDefaultFont", 8), fill="#a03030", width=180,
            )
            return

        try:
            layout = compute_layout(config, aspect if aspect is not None else 3 / 4)
        except Exception:
            # e.g. layout does not fit: show the page with a warning tint
            self._draw_page_outline(config)
            canvas.create_text(
                105, 136, text="layout does not fit",
                font=("TkDefaultFont", 8), fill="#a03030", width=180,
            )
            return

        pw, ph = layout.page_width, layout.page_height
        cw = int(canvas["width"])
        ch = int(canvas["height"])
        scale = min((cw - 20) / pw, (ch - 20) / ph)

        def X(page_x: float) -> float:
            return 10 + (page_x) * scale

        def Y(page_y: float) -> float:
            # PDF origin is bottom-left; canvas origin is top-left.
            return 10 + (ph - page_y) * scale

        # Page
        canvas.create_rectangle(
            X(0), Y(ph), X(pw), Y(0), fill="white", outline="#808080"
        )
        # Photos: real thumbnails when a readable photo is selected,
        # tinted rectangles otherwise.
        for x, y, w, h in placement_rects(layout, config):
            cx0, cy0, cx1, cy1 = X(x), Y(y + h), X(x + w), Y(y)
            thumb = self._preview_thumbnail(
                max(1, round(w * scale)), max(1, round(h * scale))
            )
            if thumb is not None:
                # Center the thumbnail inside the frame rectangle.
                tx = round(cx0 + ((cx1 - cx0) - thumb.width()) / 2)
                ty = round(cy0 + ((cy1 - cy0) - thumb.height()) / 2)
                canvas.create_rectangle(
                    cx0, cy0, cx1, cy1, fill="white", outline="#404040", width=1
                )
                canvas.create_image(tx, ty, anchor="nw", image=thumb)
                # Keep a reference or Tk garbage-collects the image.
                self._thumb_refs.append(thumb)
            else:
                fill = "#c8c8c8" if self.image_path is None else "#b84040"
                canvas.create_rectangle(
                    cx0, cy0, cx1, cy1,
                    fill=fill, outline="#404040",
                    width=1,
                )

    def _draw_tinted_frames(self, config: LayoutConfig, canvas: tk.Canvas) -> None:
        """Page outline + tinted frames for an unreadable photo."""
        try:
            box_aspect = config.photo_width / config.photo_height
            layout = compute_layout(config, box_aspect)
        except Exception:
            self._draw_page_outline(config)
            return
        pw, ph = layout.page_width, layout.page_height
        cw = int(canvas["width"])
        ch = int(canvas["height"])
        scale = min((cw - 20) / pw, (ch - 20) / ph)
        canvas.create_rectangle(
            10, 10, 10 + pw * scale, 10 + ph * scale,
            fill="white", outline="#808080",
        )
        for x, y, w, h in placement_rects(layout, config):
            canvas.create_rectangle(
                10 + x * scale, 10 + (ph - (y + h)) * scale,
                10 + (x + w) * scale, 10 + (ph - y) * scale,
                fill="#b84040", outline="#404040", width=1,
            )

    def _draw_page_outline(self, config: LayoutConfig) -> None:
        canvas = self.preview
        pw, ph = config.paper_dimensions()
        cw = int(canvas["width"])
        ch = int(canvas["height"])
        scale = min((cw - 20) / pw, (ch - 20) / ph)
        canvas.create_rectangle(
            10, 10, 10 + pw * scale, 10 + ph * scale,
            fill="white", outline="#808080",
        )

    # ------------------------------------------------------------------ #
    # Actions                                                             #
    # ------------------------------------------------------------------ #

    def choose_photo(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select the finished carnet photograph",
            filetypes=IMAGE_FILETYPES,
        )
        if not filename:
            return
        self.image_path = Path(filename)
        self.var_image.set(str(self.image_path))
        if not self.output_path:
            self.output_path = default_output_path(self.image_path)
            self.var_output.set(str(self.output_path))
        self._thumb_cache.clear()  # new photo: drop stale thumbnails
        self._schedule_preview()

    def choose_output(self) -> None:
        filename = filedialog.asksaveasfilename(
            title="Save the sheet PDF as",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")],
            initialfile=(self.output_path.name if self.output_path else "sheet.pdf"),
        )
        if not filename:
            return
        self.output_path = Path(filename)
        self.var_output.set(str(self.output_path))

    def on_generate(self) -> None:
        if self.image_path is None:
            messagebox.showinfo(
                "No photograph selected",
                "Choose the finished carnet photograph first.",
            )
            return
        # Read ALL Tk state on the main thread, before spawning the worker:
        # Tk Variables (and even event_generate) must never be touched from
        # a worker thread — on Windows those calls raise
        # "main thread is not in main loop".
        try:
            config = self.build_config()
            output = self.output_path or default_output_path(self.image_path)
            check_output_not_input(self.image_path, output)
        except (LayoutError, ValueError) as exc:
            messagebox.showerror("Could not generate the PDF", str(exc))
            return
        if not self._generation_lock.acquire(blocking=False):
            return  # a generation is already running
        self.button_generate.config(state="disabled")
        self.status.set("Generating…")
        self.save_settings()
        self._results = queue.Queue()
        threading.Thread(
            target=self._generate_worker,
            args=(self.image_path, output, config),
            daemon=True,
        ).start()
        # Poll for the result from the main thread only.
        self.root.after(50, self._poll_results)

    def _generate_worker(self, image_path: Path, output_path: Path, config) -> None:
        # Runs pure file/CPU work only: no Tk objects here.
        try:
            summary = generate_pdf(image_path, output_path, config)
            error = None
        except Exception as exc:  # reported to the user below
            summary = None
            error = exc
        # Queue is thread-safe; the main thread picks this up via after().
        self._results.put((summary, error))

    def _poll_results(self) -> None:
        """Main-thread poll: hand the worker result to _finish_generation."""
        try:
            summary, error = self._results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll_results)
            return
        self._finish_generation(summary, error)

    def _finish_generation(self, summary: dict | None, error: Exception | None) -> None:
        self.button_generate.config(state="normal")
        self._generation_lock.release()
        if error is not None:
            self.status.set("Generation failed — see the message.")
            messagebox.showerror("Could not generate the PDF", str(error))
            return
        assert summary is not None
        w, h = summary["photo_size"]
        pw, ph = summary["page_size"]
        note = ""
        if summary["scaled"]:
            note = (
                " (shrunk to fit, aspect ratio preserved: "
                f"{pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm)"
            )
        self.status.set(
            f"PDF written: {summary['output']} — "
            f"page {pw:.0f} × {ph:.0f} pt, {summary['placements']} photos of "
            f"{pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm{note}"
        )
        if messagebox.askyesno(
            "PDF ready",
            f"Saved:\n{summary['output']}\n\nOpen it now?",
        ):
            self._open_pdf(summary["output"])

    def _open_pdf(self, path: Path) -> None:
        """Best-effort open with the platform's default PDF viewer.

        Windows 10/11: os.startfile (Shell association).
        macOS: `open`. Linux: okular/evince/xdg-open.
        """
        if sys.platform == "win32":
            try:
                os.startfile(path)  # type: ignore[attr-defined]  # Windows only
            except OSError:
                messagebox.showinfo("PDF ready", f"Saved:\n{path}")
            return
        if sys.platform == "darwin" and shutil.which("open"):
            subprocess.Popen(["open", str(path)])
            return
        for viewer in ("okular", "evince", "xdg-open"):
            if shutil.which(viewer):
                subprocess.Popen([viewer, str(path)])
                return
        messagebox.showinfo(
            "PDF ready", f"Saved:\n{path}\n\n(no PDF viewer found to open it)"
        )

    # ------------------------------------------------------------------ #
    # Settings persistence                                                #
    # ------------------------------------------------------------------ #

    def load_settings(self) -> None:
        saved = self.store.load()
        try:
            if saved.get("output"):
                self.output_path = Path(str(saved["output"]))
                self.var_output.set(str(self.output_path))
            if saved.get("paper") in PAPER_SIZES:
                self.var_paper.set(saved["paper"])
            if saved.get("orientation") in ("portrait", "landscape"):
                self.var_orientation.set(saved["orientation"])
            for key, var, lo, hi in (
                ("copies", self.var_copies, 1, 99),
                ("columns", self.var_columns, 1, 99),
            ):
                value = saved.get(key)
                if isinstance(value, int) and lo <= value <= hi:
                    var.set(value)
            for key, var in (
                ("border", self.var_border),
                ("fit", self.var_fit),
            ):
                if isinstance(saved.get(key), bool):
                    var.set(saved[key])
            for key, var in (
                ("photo_width", self.var_photo_width),
                ("photo_height", self.var_photo_height),
                ("spacing_x", self.var_spacing_x),
                ("spacing_y", self.var_spacing_y),
                ("top_margin", self.var_top_margin),
                ("left_margin", self.var_left_margin),
                ("right_margin", self.var_right_margin),
            ):
                value = saved.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    var.set(format_mm(key, float(value)))
        except Exception:
            pass  # corrupt or partially valid settings: keep defaults
        self.draw_preview()

    def save_settings(self) -> None:
        data: dict = {}
        if self.output_path is not None:
            data["output"] = str(self.output_path)
        try:
            data.update(
                paper=self.var_paper.get(),
                orientation=self.var_orientation.get(),
                copies=int(self.var_copies.get()),
                columns=int(self.var_columns.get()),
                border=bool(self.var_border.get()),
                fit=bool(self.var_fit.get()),
            )
            for key, var in (
                ("photo_width", self.var_photo_width),
                ("photo_height", self.var_photo_height),
                ("spacing_x", self.var_spacing_x),
                ("spacing_y", self.var_spacing_y),
                ("top_margin", self.var_top_margin),
                ("left_margin", self.var_left_margin),
                ("right_margin", self.var_right_margin),
            ):
                data[key] = float(var.get())
        except (tk.TclError, ValueError):
            pass  # skip whatever is currently invalid
        self.store.save(data)

    def _save_settings_deferred(self) -> None:
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self.save_settings)

    # ------------------------------------------------------------------ #
    # Config building                                                     #
    # ------------------------------------------------------------------ #

    def build_config(self) -> LayoutConfig:
        """Translate the form into a LayoutConfig (mm entries → points)."""
        try:
            copies = int(self.var_copies.get())
            columns = int(self.var_columns.get())
        except (tk.TclError, ValueError):
            raise LayoutError("Copies and columns must be whole numbers.") from None

        def mm_entry(var: tk.StringVar, name: str) -> float:
            raw = var.get().strip()
            try:
                value = float(raw)
            except ValueError:
                raise LayoutError(f"{name} must be a number (got {raw!r}).") from None
            return mm_to_pt(value)

        return LayoutConfig(
            paper=self.var_paper.get(),
            orientation=self.var_orientation.get(),
            copies=copies,
            columns=columns,
            photo_width=mm_entry(self.var_photo_width, "Photo width"),
            photo_height=mm_entry(self.var_photo_height, "Photo height"),
            spacing_x=mm_entry(self.var_spacing_x, "Horizontal spacing"),
            spacing_y=mm_entry(self.var_spacing_y, "Vertical spacing"),
            top_margin=mm_entry(self.var_top_margin, "Top margin"),
            left_margin=mm_entry(self.var_left_margin, "Left margin"),
            right_margin=mm_entry(self.var_right_margin, "Right margin"),
            border=self.var_border.get(),
            fit_to_page=self.var_fit.get(),
        )


def _enable_windows_dpi_awareness() -> None:
    """Keep Tk crisp on high-DPI Windows 10/11 displays (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass  # older Windows version or awareness already set


def _selftest_log_stream(out_pdf: Path):
    """Open the selftest log next to the output PDF (always, not stdout).

    Windowed exes may have unusable/None stdio handles (wine raises
    WinError 6 on redirected pipes), so results go to a file that the
    test harness can read regardless of how the exe was launched.
    """
    log_path = Path(
        os.environ.get("SELFTEST_LOG", str(out_pdf.with_suffix(".selftest.log")))
    )
    try:
        stream = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        return stream
    except OSError:
        return None


def run_selftest(photo: str, out_pdf: str, timeout: float = 60.0) -> int:
    """End-to-end test hook for the frozen exe.

    Builds the real window, points it at `photo`, invokes the REAL
    on_generate (the Generate button command: threaded worker, queue,
    after-poll), and pumps the actual Tk mainloop until done. The
    "Open it now?" dialog is auto-declined; nothing else is mocked.
    Prints SELFTEST-OK on success.
    """
    import tkinter.messagebox as messagebox
    import time as _time

    _selftest_log_stream(Path(out_pdf))
    print(f"selftest start: photo={photo} out={out_pdf}")

    root = tk.Tk()
    gui = CarnetSheetGUI(root)

    declined: dict = {}
    messagebox.askyesno = lambda *a, **k: declined.update(
        title=str(a[0]), answer=False
    ) or False

    gui.image_path = Path(photo)
    gui.output_path = Path(out_pdf)
    gui.var_image.set(str(gui.image_path))
    gui.var_output.set(str(gui.output_path))

    deadline = _time.monotonic() + timeout

    def check_done() -> None:
        if not gui._generation_lock.locked() and gui.output_path.exists():
            print("conditions met: quitting mainloop")
            root.quit()
            return
        if _time.monotonic() > deadline:
            print("deadline reached: quitting mainloop")
            root.quit()
            return
        root.after(50, check_done)

    gui.on_generate()
    check_done()
    root.mainloop()

    if gui.output_path.exists() and gui.output_path.read_bytes()[:5] == b"%PDF-":
        print(f"SELFTEST-OK {gui.output_path}")
        print(f"SELFTEST-DIALOG {declined.get('title', 'none')}")
        print("gui-status:", gui.status.get())
        return 0
    print(
        f"SELFTEST-FAIL: pdf_exists={gui.output_path.exists()} "
        f"status={gui.status.get()}"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    _enable_windows_dpi_awareness()

    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0] == "--selftest":
        return run_selftest(args[1], args[2])
    if args:
        print("usage: CarnetSheetMaker.exe [--selftest PHOTO OUT_PDF]", file=sys.stderr)
        return 2

    root = tk.Tk()
    CarnetSheetGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
