#!/usr/bin/env python3
"""Minimal Tkinter GUI for the Carnet Photo Sheet Maker.

The ideal workflow from the project brief: select the finished photograph(s),
optionally adjust a few layout settings, click Generate, receive the PDF.

Extras:
- A list of photographs, each with its own copy quantity (default 6).
- A live visual preview of the sheet layout that updates as settings change.
- Settings persist between sessions in the platform's user config directory.
- A window icon (and, when frozen with PyInstaller, the exe carries the
  same icon and version metadata).

This window performs layout only — it never modifies the photographs.
The heavy lifting (validation, layout math, PDF writing) lives in
carnet_sheet.py and is shared with the command-line interface.
"""

from __future__ import annotations

import json
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
    default_output_path,
    generate_pdf,
    load_source_image,
    mm_to_pt,
    placement_plan,
    pt_to_mm,
)

APP_NAME = "Carnet Photo Sheet Maker"
APP_VERSION = "1.1.0"

# Settings shown in the GUI. Units always display in millimetres; the
# point-based defaults are converted for display and converted back on
# generation, so the generated geometry is identical to the CLI defaults.
# Spacing defaults are 0 (zero-gap layout: adjacent photos touch so the
# printed sheet can be cut in straight lines).
DEFAULTS_MM = {
    "photo_width": pt_to_mm(85.79),
    "photo_height": pt_to_mm(114.14),
    "spacing_x": pt_to_mm(0.0),
    "spacing_y": pt_to_mm(0.0),
    "top_margin": pt_to_mm(30.0),
    "left_margin": pt_to_mm(30.0),
    "right_margin": pt_to_mm(30.0),
}

# Decimal places used when displaying/persisting mm fields. 4 keeps the
# round-trip lossless to ~0.0003 pt for every default (1 mm ≈ 2.83 pt), so
# the GUI reproduces the CLI geometry exactly.
MM_DECIMALS = 4

# Per-image copy quantity: the default for a newly added image, the spinbox
# range, and the key used in the persisted settings file.
DEFAULT_COPIES = 6
COPIES_MIN = 1
COPIES_MAX = 99

# Bumped when the meaning of persisted settings changes. Version 2 is the
# zero-gap release: settings saved by older versions (spacing 6/12 pt) are
# not restored for spacing, so existing users get the new zero-gap defaults.
SETTINGS_VERSION = 2


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

        # Photo list: each entry is {"path": Path, "copies": tk.StringVar,
        # "select": tk.BooleanVar}.
        self.photos: list[dict] = []
        self._selected_row: int | None = None
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
        self.var_output = tk.StringVar(value="")

        self.var_paper = tk.StringVar(value="letter")
        self.var_orientation = tk.StringVar(value="portrait")
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

    def _build_layout(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.grid(sticky="nsew")

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")

        # --- Left column: photos, output, settings -----------------------
        left = ttk.Frame(top)
        left.grid(row=0, column=0, sticky="nw")

        # Photographs: a scrollable list of path + quantity rows.
        box = ttk.LabelFrame(left, text="Photographs", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)

        container = ttk.Frame(box)
        container.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.photo_canvas = tk.Canvas(container, height=120, highlightthickness=0)
        photo_scroll = ttk.Scrollbar(
            container, orient="vertical", command=self.photo_canvas.yview
        )
        self.photo_list_frame = ttk.Frame(self.photo_canvas)
        self.photo_list_frame.bind(
            "<Configure>",
            lambda e: self.photo_canvas.configure(
                scrollregion=self.photo_canvas.bbox("all")
            ),
        )
        self.photo_canvas.create_window(
            (0, 0), window=self.photo_list_frame, anchor="nw", tags="inner"
        )
        self.photo_canvas.bind(
            "<Configure>",
            lambda e: self.photo_canvas.itemconfigure("inner", width=e.width),
        )
        self.photo_canvas.configure(yscrollcommand=photo_scroll.set)
        self.photo_canvas.pack(side="left", fill="both", expand=True)
        photo_scroll.pack(side="right", fill="y")
        # Wheel scrolling over the list (Windows/macOS + Linux conventions).
        self.photo_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.photo_canvas.yview_scroll(-e.delta // 120, "units"),
        )
        self.photo_canvas.bind_all(
            "<Button-4>", lambda e: self.photo_canvas.yview_scroll(-1, "units")
        )
        self.photo_canvas.bind_all(
            "<Button-5>", lambda e: self.photo_canvas.yview_scroll(1, "units")
        )

        list_buttons = ttk.Frame(box)
        list_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(
            list_buttons, text="Add photo…", command=self.choose_photo
        ).pack(side="left")
        ttk.Button(
            list_buttons, text="Remove selected", command=self.remove_selected_photo
        ).pack(side="left", padx=(8, 0))
        ttk.Button(list_buttons, text="Clear all", command=self.clear_photos).pack(
            side="left", padx=(8, 0)
        )

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

        ttk.Label(box, text="Columns:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(box, from_=1, to=36, textvariable=self.var_columns, width=6).grid(
            row=1, column=1, sticky="w", padx=(4, 16), pady=(6, 0)
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
        preview_box = ttk.LabelFrame(top, text="Preview (page 1)", padding=6)
        preview_box.grid(row=0, column=1, sticky="nw", padx=(12, 0))
        self.preview = tk.Canvas(
            preview_box, width=210, height=272, bg="#f0f0f0", highlightthickness=1,
            highlightbackground="#b0b0b0",
        )
        self.preview.pack()
        self.preview_pages = tk.StringVar(value="")
        # Fixed width so the multipage string never resizes the window when
        # it appears/disappears as quantities change.
        ttk.Label(
            preview_box, textvariable=self.preview_pages, font=("TkDefaultFont", 8),
            width=38, anchor="center",
        ).pack()

        # --- Generate ----------------------------------------------------
        buttons = ttk.Frame(main, padding=(8, 4))
        buttons.grid(row=1, column=0, sticky="ew")
        self.button_generate = ttk.Button(
            buttons, text="Generate PDF", command=self.on_generate
        )
        self.button_generate.grid(row=0, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)

        self.status = tk.StringVar(
            value="Add a photograph, then click Generate PDF."
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
        self.var_columns.trace_add("write", lambda *_: self._schedule_preview())
        self.var_output.trace_add("write", lambda *_: self._save_settings_deferred())

    # ------------------------------------------------------------------ #
    # Photo list management                                               #
    # ------------------------------------------------------------------ #

    def add_photo(self, path: Path, copies: int | None = None) -> None:
        """Append a photo to the list with its own quantity (default 6)."""
        entry = {
            "path": Path(path),
            "copies": tk.StringVar(
                value=str(DEFAULT_COPIES if copies is None else copies)
            ),
        }
        # Live preview + persistence follow every quantity edit.
        entry["copies"].trace_add("write", lambda *_: self._schedule_preview())
        self.photos.append(entry)
        self._rebuild_photo_rows()

    def remove_selected_photo(self) -> None:
        # Selection is tracked per row (see _rebuild_photo_rows); remove the
        # marked row, or the last one when nothing is selected.
        if self._selected_row is not None:
            del self.photos[self._selected_row]
            self._selected_row = None
        elif self.photos:
            del self.photos[-1]
        self._rebuild_photo_rows()

    def clear_photos(self) -> None:
        self.photos.clear()
        self._selected_row = None
        self._rebuild_photo_rows()

    def _rebuild_photo_rows(self) -> None:
        """Recreate the path+quantity rows from self.photos."""
        for child in self.photo_list_frame.winfo_children():
            child.destroy()
        if self._selected_row is not None and self._selected_row >= len(self.photos):
            self._selected_row = None

        for row, entry in enumerate(self.photos):
            select_var = tk.BooleanVar(value=False)
            entry["select"] = select_var

            def make_select(variable: tk.BooleanVar, index: int):
                def toggle(*_args) -> None:
                    if variable.get():
                        # Radio-like behaviour: exactly one row selected.
                        for other in self.photos:
                            if other["select"] is not variable:
                                other["select"].set(False)
                        self._selected_row = index
                    elif self._selected_row == index:
                        self._selected_row = None

                return toggle

            ttk.Checkbutton(
                self.photo_list_frame, variable=select_var,
                command=make_select(select_var, row), width=1,
            ).grid(row=row, column=0, sticky="w")

            path_text = str(entry["path"])
            ttk.Label(
                self.photo_list_frame,
                text=path_text if len(path_text) <= 40 else "…" + path_text[-39:],
                relief="groove", padding=(4, 2),
            ).grid(row=row, column=1, sticky="ew", pady=2)

            ttk.Spinbox(
                self.photo_list_frame,
                from_=COPIES_MIN, to=COPIES_MAX, width=4,
                textvariable=entry["copies"],
            ).grid(row=row, column=2, padx=(6, 0))
            ttk.Label(self.photo_list_frame, text="copies").grid(
                row=row, column=3, sticky="w", padx=(2, 0)
            )

        self.photo_list_frame.columnconfigure(1, weight=1)
        self._schedule_preview()
        self._save_settings_deferred()

    # ------------------------------------------------------------------ #
    # Live preview                                                        #
    # ------------------------------------------------------------------ #

    def _schedule_preview(self) -> None:
        """Debounce redraws while the user types."""
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(120, self.draw_preview)

    def _preview_thumbnail(self, path: Path, width_px: int, height_px: int):
        """Return a tk.PhotoImage of the photo at the given preview size.

        The thumbnail matches what the PDF will contain: EXIF-oriented and
        flattened onto white if it has transparency. Cached by (path, size).
        """
        from PIL import Image, ImageOps

        key = (str(path), width_px, height_px)
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        try:
            with Image.open(path) as im:
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

    def _job_quantities(self) -> list[tuple[Path, int]]:
        """Read the photo list as [(path, copies), ...]; None when invalid."""
        jobs: list[tuple[Path, int]] = []
        for entry in self.photos:
            try:
                copies = int(entry["copies"].get())
            except (tk.TclError, ValueError):
                return []  # a quantity is blank/garbage: treat as no job yet
            if copies < 1:
                return []
            jobs.append((entry["path"], copies))
        return jobs

    def _preview_plan(self, config: LayoutConfig):
        """Plan pages for the current job, or (None, error) when it cannot.

        Returns (pages, None) on success, (None, reason) when the job is
        empty or the photos cannot be read, and (None, LayoutError) when the
        layout genuinely does not fit (fit-to-page disabled).
        """
        jobs = self._job_quantities()
        if not jobs:
            return None, "empty"
        unreadable = False
        aspects: list[list[float]] = []
        for path, copies in jobs:
            try:
                aspect = load_source_image(path).aspect
            except Exception:
                unreadable = True
                break
            aspects.append([aspect] * copies)
        if unreadable:
            return None, "unreadable"
        try:
            return placement_plan(aspects, config), None
        except LayoutError as exc:
            return None, exc

    def draw_preview(self) -> None:
        """Redraw the miniature sheet; on any problem, degrade gracefully."""
        # Debounced after-callbacks can fire re-entrantly while this method
        # runs (Tk pumps events inside widget calls); never interleave two
        # draws or the canvas ends up with duplicated content.
        if getattr(self, "_drawing_preview", False):
            return
        self._drawing_preview = True
        try:
            self._draw_preview_inner()
        finally:
            self._drawing_preview = False

    def _draw_preview_inner(self) -> None:
        self._preview_job = None
        canvas = self.preview
        canvas.delete("all")
        try:
            config = self.build_config()
        except Exception:
            self.preview_pages.set("")
            return  # incomplete/invalid entry: show an empty canvas

        pages, plan_error = self._preview_plan(config)
        if pages is None:
            self.preview_pages.set("")
            if isinstance(plan_error, LayoutError):
                self._draw_page_outline(config)
                canvas.create_text(
                    105, 136, text="layout does not fit",
                    font=("TkDefaultFont", 8), fill="#a03030", width=180,
                )
            elif plan_error == "unreadable":
                # Tinted frames (includes the page outline) + a warning.
                self._draw_tinted_frames(config)
                canvas.create_text(
                    105, 136, text="photo cannot be displayed",
                    font=("TkDefaultFont", 8), fill="#a03030", width=180,
                )
            else:
                self._draw_page_outline(config)
            return

        total = sum(len(p.placements) for p in pages)
        if len(pages) > 1:
            self.preview_pages.set(
                f"{len(pages)} pages · {total} photos (showing page 1)"
            )
        else:
            self.preview_pages.set(f"{total} photos")

        layout = pages[0].result
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
        for placement in pages[0].placements:
            path = self.photos[placement.job_index]["path"]
            x, y, w, h = (
                placement.x, placement.y, placement.width, placement.height
            )
            cx0, cy0, cx1, cy1 = X(x), Y(y + h), X(x + w), Y(y)
            thumb = self._preview_thumbnail(
                path, max(1, round(w * scale)), max(1, round(h * scale))
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
                fill = "#c8c8c8" if not self.photos else "#b84040"
                canvas.create_rectangle(
                    cx0, cy0, cx1, cy1,
                    fill=fill, outline="#404040",
                    width=1,
                )

    def _draw_tinted_frames(self, config: LayoutConfig) -> None:
        """Page outline + tinted frames for unreadable photos."""
        canvas = self.preview
        jobs = self._job_quantities()
        try:
            # Box aspect is exact for the frames; heights use the box.
            box_aspect = config.photo_width / config.photo_height
            total = sum(copies for _path, copies in jobs) or 1
            aspects: list[list[float]] = [
                [box_aspect] * copies for _path, copies in jobs
            ]
            if total * config.photo_height > (792.0 - config.top_margin):
                first_only = [aspects[0]]
                pages = placement_plan(first_only, config)
            else:
                pages = placement_plan(aspects, config)
        except Exception:
            self._draw_page_outline(config)
            return
        layout = pages[0].result
        pw, ph = layout.page_width, layout.page_height
        cw = int(canvas["width"])
        ch = int(canvas["height"])
        scale = min((cw - 20) / pw, (ch - 20) / ph)
        canvas.create_rectangle(
            10, 10, 10 + pw * scale, 10 + ph * scale,
            fill="white", outline="#808080",
        )
        for placement in pages[0].placements:
            canvas.create_rectangle(
                10 + placement.x * scale,
                10 + (ph - (placement.y + placement.height)) * scale,
                10 + (placement.x + placement.width) * scale,
                10 + (ph - placement.y) * scale,
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
        filenames = filedialog.askopenfilenames(
            title="Select the finished carnet photograph(s)",
            filetypes=IMAGE_FILETYPES,
        )
        if not filenames:
            return
        for filename in filenames:
            self.add_photo(Path(filename))  # each starts at the default 6
        if not self.output_path and self.photos:
            self.output_path = default_output_path(self.photos[0]["path"])
            self.var_output.set(str(self.output_path))
        self._thumb_cache.clear()  # new photos: drop stale thumbnails

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
        if not self.photos:
            messagebox.showinfo(
                "No photograph selected",
                "Add the finished carnet photograph(s) first.",
            )
            return
        # Read ALL Tk state on the main thread, before spawning the worker:
        # Tk Variables (and even event_generate) must never be touched from
        # a worker thread — on Windows those calls raise
        # "main thread is not in main loop".
        try:
            config = self.build_config()
            jobs = self._job_quantities()
            if not jobs:
                raise LayoutError(
                    "Each photograph needs a whole number of copies (1–99)."
                )
            output = self.output_path or default_output_path(jobs[0][0])
            check_output_not_input([path for path, _ in jobs], output)
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
            args=([path for path, _ in jobs], [copies for _, copies in jobs], output, config),
            daemon=True,
        ).start()
        # Poll for the result from the main thread only.
        self.root.after(50, self._poll_results)

    def _generate_worker(
        self,
        paths: list[Path],
        copies: list[int],
        output_path: Path,
        config,
    ) -> None:
        # Runs pure file/CPU work only: no Tk objects here.
        try:
            summary = generate_pdf(paths, output_path, config, quantities=copies)
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
            if os.environ.get("SELFTEST_LOG") or getattr(sys, "frozen", False):
                # In selftest/frozen runs stdout may be unusable; the selftest
                # has already redirected it to its log file.
                import traceback

                traceback.print_exception(type(error), error, error.__traceback__)
                print(f"GENERATION-ERROR: {error}")
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
        pages_note = (
            f", {summary['pages']} pages" if summary.get("pages", 1) > 1 else ""
        )
        self.status.set(
            f"PDF written: {summary['output']} — "
            f"page {pw:.0f} × {ph:.0f} pt, {summary['placements']} photos of "
            f"{pt_to_mm(w):.1f} × {pt_to_mm(h):.1f} mm{pages_note}{note}"
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
            value = saved.get("columns")
            if isinstance(value, int) and 1 <= value <= 99:
                self.var_columns.set(value)
            for key, var in (
                ("border", self.var_border),
                ("fit", self.var_fit),
            ):
                if isinstance(saved.get(key), bool):
                    var.set(saved[key])
            migrated = saved.get("settings_version") != SETTINGS_VERSION
            for key, var in (
                ("photo_width", self.var_photo_width),
                ("photo_height", self.var_photo_height),
                ("spacing_x", self.var_spacing_x),
                ("spacing_y", self.var_spacing_y),
                ("top_margin", self.var_top_margin),
                ("left_margin", self.var_left_margin),
                ("right_margin", self.var_right_margin),
            ):
                if migrated and key in ("spacing_x", "spacing_y"):
                    continue  # pre-zero-gap spacing: keep the new 0 defaults
                value = saved.get(key)
                if isinstance(value, (int, float)) and value >= 0:
                    var.set(format_mm(key, float(value)))
            # Photo list: persisted as [{"path": ..., "copies": ...}, ...].
            entries = saved.get("photos")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    path = entry.get("path")
                    copies = entry.get("copies", DEFAULT_COPIES)
                    if not isinstance(path, str) or not path:
                        continue
                    if isinstance(copies, bool) or not isinstance(copies, int):
                        copies = DEFAULT_COPIES
                    if not COPIES_MIN <= copies <= COPIES_MAX:
                        copies = DEFAULT_COPIES
                    self.add_photo(Path(path), copies)
        except Exception:
            pass  # corrupt or partially valid settings: keep defaults
        self._rebuild_photo_rows()
        self.draw_preview()

    def save_settings(self) -> None:
        data: dict = {}
        if self.output_path is not None:
            data["output"] = str(self.output_path)
        try:
            data.update(
                settings_version=SETTINGS_VERSION,
                paper=self.var_paper.get(),
                orientation=self.var_orientation.get(),
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
            data["photos"] = [
                {"path": str(entry["path"]), "copies": int(entry["copies"].get())}
                for entry in self.photos
            ]
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
            columns = int(self.var_columns.get())
        except (tk.TclError, ValueError):
            raise LayoutError("Columns must be a whole number.") from None

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
            copies=0,  # unused by the GUI: quantities come from the photo list
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

    gui.add_photo(Path(photo))  # default quantity 6, like the real UI flow
    gui.output_path = Path(out_pdf)
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
