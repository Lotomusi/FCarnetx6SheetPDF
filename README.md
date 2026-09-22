# Carnet Photo Sheet Maker

A small, local Python utility that takes one **already-finished carnet
photograph** and produces a **print-ready PDF** containing six identical
copies of it in a single horizontal strip near the top of a US Letter
portrait page — replacing the manual PowerPoint import → duplicate →
align → export workflow.

It performs **layout and document generation only**. It never edits the
photograph: no cropping, stretching, rotating, beautification, background
removal, or AI processing of any kind. Works fully offline.

Two interfaces share the same layout engine:

- **`carnet_gui.py`** — a minimal window: pick the photo, optionally adjust
  settings in millimetres, click **Generate PDF**, get the result (and a
  prompt to open it in your PDF viewer).
- **`carnet_sheet.py`** — the same thing from the command line.

Both run on **Linux (Fedora)** and **Windows 10/11**.

---

## Requirements

- Python 3.9+ (tested on Fedora with Python 3.12)
- [Pillow](https://pillow.readthedocs.io/) — image loading, EXIF orientation
- [ReportLab](https://www.reportlab.com/opensource/) — exact-dimension PDF output

## Installation (Fedora Linux)

Fedora marks the system Python as externally managed (PEP 668), so install
the dependencies into your user environment:

```bash
pip3 install --user --break-system-packages pillow reportlab
sudo dnf install python3-tkinter   # only needed for the GUI
```

Alternatively (and more cleanly), use a virtual environment:

```bash
python3 -m venv ~/.venvs/carnet      # Fedora: may require `sudo dnf install python3-venv` first
source ~/.venvs/carnet/bin/activate
pip install pillow reportlab
```

On Fedora KDE you can also get the libraries from the distribution repos:

```bash
sudo dnf install python3-pillow python3-reportlab python3-tkinter
```

## Installation (Windows 10/11)

1. Install Python from [python.org](https://www.python.org/downloads/) —
   the standard installer already includes **tkinter** (the GUI toolkit);
   keep "Add python.exe to PATH" ticked.
2. In a terminal (PowerShell or CMD):

   ```bat
   pip install pillow reportlab
   ```

3. Run the GUI:

   ```bat
   python carnet_gui.py
   ```

   Or the command line version:

   ```bat
   python carnet_sheet.py photo.jpg
   ```

No other dependencies, runtimes or internet access are required. The GUI
uses only the Python standard library plus the two packages above.

### Standalone Windows .exe (no Python needed on the target PC)

PyInstaller cannot cross-compile, so the exe must be built on Windows or in
CI. Two equivalent options:

**Option A — one-click build on any Windows 10/11 machine**

1. Install Python from [python.org](https://www.python.org/downloads/)
   (tick "Add python.exe to PATH").
2. Double-click **`build_windows_exe.bat`**. It creates an isolated build
   environment, installs pillow/reportlab/pyinstaller, runs the test suite,
   and produces **`dist\CarnetSheetMaker.exe`** — a single file you can
   copy anywhere and double-click. No Python is required to run it.

**Option B — build automatically on GitHub**

Push the project to GitHub and run the **"Build Windows exe"** workflow
(Actions tab → run workflow). It builds the exe on `windows-latest`,
smoke-tests that it launches, and uploads it as a downloadable artifact.

**Already-built artifact**

A binary built with Windows Python 3.12 + PyInstaller is included as
`CarnetSheetMaker.exe` (≈20 MB, single file, windowed, with icon and
version metadata). Its version resource reads 1.0.0.0. Rebuild any time
with either option above to pick up code changes.

> Note: Windows SmartScreen may warn about the exe ("Windows protected
> your PC"). Code signing removes this once the certificate has built
> reputation — see **`docs/CODE_SIGNING.md`** for the setup (Azure
> Artifact Signing in CI, or local signtool with your own certificate).
>
> Optional UPX compression exists in both build paths (`UPX=1`) but is
> off by default: it saves <1 % here (the PyInstaller archive is already
> compressed), slows startup, and can trigger antivirus heuristics.

## Using the GUI

```bash
python3 carnet_gui.py     # Linux
python carnet_gui.py      # Windows
```

1. **Choose photo…** — select the finished carnet photograph
   (JPEG, PNG or WebP). A default output name (`<photo>_sheet.pdf`) is
   filled in automatically.
2. Optionally adjust the settings (all sizes in millimetres), the paper
   size, orientation, copies and columns.
3. Click **Generate PDF**. Validation errors (missing photo, layout that
   does not fit, non-numeric entries) appear as dialogs, never as a broken
   PDF.
4. On success the status line reports the exact geometry, and you are
   offered to open the PDF in the system's default viewer
   (`os.startfile` on Windows, `xdg-open`/Okular/Evince on Linux).

The window is deliberately minimal; the checkbox **Shrink to fit page
instead of erroring** corresponds to the CLI's `--fit-to-page`.

### Live preview

A miniature of the sheet is drawn next to the settings and refreshes as
you type — page size, photo rectangles and their placement, or a
"layout does not fit" warning. Once a photograph is selected, each frame
shows a **real thumbnail** of it (EXIF-oriented, transparency flattened,
centred in its frame); unreadable files fall back to tinted rectangles.
The preview uses the same layout engine as the PDF, so what you see is
what will be generated.

### Remembered settings

Your layout settings and last output folder persist between sessions in
the per-user config directory:

- Windows: `%APPDATA%\CarnetSheetMaker\settings.json`
- Linux: `~/.config/CarnetSheetMaker/settings.json`

Persistence is best-effort: a missing or corrupt file simply falls back
to the defaults.

Tip (Windows): copy `carnet_gui.py` to `carnet_gui.pyw` and double-click
it — the `.pyw` extension launches the GUI without a console window.

## Usage

```bash
python3 carnet_sheet.py photo.jpg
```

That single command creates `photo_sheet.pdf` next to the photo — six
copies, horizontally centered, 30 pt (~10.6 mm) from the top edge, each
photo 85.61 × 114.14 pt (~30.2 × 40.3 mm), with thin cutting borders.

Preview before printing:

```bash
okular photo_sheet.pdf        # or evince, gwenview, etc.
```

### Common options

```bash
# Four copies in one row, photo 40 mm wide (aspect preserved):
python3 carnet_sheet.py photo.jpg --mm --copies 4 --columns 4 --photo-width 40

# Two rows of three, larger photos, no cutting borders:
python3 carnet_sheet.py photo.jpg --columns 3 --copies 6 \
    --photo-width 120 --photo-height 160 --no-border

# Layout that does not fit: shrink to fit instead of erroring:
python3 carnet_sheet.py photo.jpg --photo-width 110 --fit-to-page

# Explicit output path and A4 paper:
python3 carnet_sheet.py photo.jpg -o ~/Documents/sheet.pdf --paper a4
```

Run `python3 carnet_sheet.py --help` for the full list.

### All layout settings

| Option | Default | Meaning |
|---|---|---|
| `image` | — | Input photograph (JPEG, PNG, WebP) |
| `-o, --output` | `<image>_sheet.pdf` | Output PDF path |
| `--paper` | `letter` | `letter`, `a4`, or `legal` |
| `--orientation` | `portrait` | `portrait` or `landscape` |
| `--copies` | `6` | Total number of photo placements |
| `--columns` | `6` | Photos per row; extras wrap to a second row |
| `--photo-width` | `85.79` pt | Width of one photo box |
| `--photo-height` | `114.14` pt | Height of one photo box |
| `--spacing-x` | `6` pt | Horizontal gap between photos |
| `--spacing-y` | `12` pt | Vertical gap between rows |
| `--top-margin` | `30` pt | Distance from page top to the strip |
| `--left-margin` / `--right-margin` | `30` pt | Side margins |
| `--no-border` | borders on | Omit the thin rectangles around each photo |
| `--fit-to-page` | off | Shrink (never stretch) to fit the page instead of erroring |
| `--mm` | off | Interpret provided sizes/margins in millimetres |

Points are the PDF-native unit: 72 pt = 1 inch, so 85.79 × 114.14 pt ≈
30.3 × 40.3 mm. With `--mm`, only the values you actually pass on the
command line are treated as millimetres; unset options keep their
point defaults.

### How sizing works (aspect-ratio guarantee)

The photograph is always drawn at **its own aspect ratio**, contained
inside the configured photo box:

- If the image aspect matches the box (the expected case for a finished
  carnet photo), the photo is drawn at exactly the configured size.
- If not (e.g. a 3:4 image in a slightly wider box), the photo is
  *shrunk* along the longer box dimension so nothing is stretched or
  squashed. The drawn size is reported when the PDF is written.
- `--fit-to-page` applies the same guarantee at page level: the whole
  arrangement is scaled down uniformly and the final size is explained
  in the output message.

---

## Validation

Run the acceptance test suite (29 core tests plus GUI tests, stdlib
`unittest` only; GUI tests skip automatically when tkinter or a display
is unavailable):

```bash
python3 -m unittest discover -p "test_*.py" -v
```

The tests generate real PDFs and parse them back (page MediaBox,
content-stream operators, embedded image objects) to verify:

1. Output is a valid, single-page PDF.
2. Page is exactly US Letter: 612 × 792 pt.
3. Default layout contains exactly six photo placements.
4. All six placements reference the same embedded image.
5. Each photo is drawn at the configured physical dimensions.
6. The photo's aspect ratio is preserved everywhere (default, contained,
   fit-to-page, EXIF-rotated inputs).
7. All placements remain within the page boundaries.
8. The strip is horizontally centered (left gap = right gap).
9. Missing files, corrupt images, impossible layouts, invalid numbers and
   unwritable outputs all produce clear errors (exit code 2), never a
   malformed PDF.
10. The input file is byte-identical after generation.

A sample generated from `sample_photo.png` is included as
`sample_photo_sheet.pdf`.

## Assumptions that can affect physical print size

- **Print at 100% / "Actual size".** The PDF declares exact physical
  dimensions; print dialogs that use "Fit to page" will rescale the sheet
  and change the photo sizes.
- The reference document `saulelelegido.pdf` was **not available** in this
  workspace, so its recorded measurements (≈85.79 × 114.14 pt frames,
  six-photo top strip, mostly empty lower page) come from the project
  brief. Defaults reproduce those measurements on standard US Letter
  (612 × 792 pt), *not* the reference's non-standard 595.2 × 765.36 pt page.
- A 3:4 photograph in the default box is drawn at 85.61 × 114.14 pt rather
  than 85.79 pt wide, because the default box's aspect (0.7514) is slightly
  wider than 3:4 (0.75). Distortion is never introduced; pass a
  `--photo-width 85.605` if you need that last 0.2 mm.
- Printer hardware margins are not modeled; the 30 pt default top/side
  margins leave headroom for typical laser printers, but very
  borderless-unfriendly printers may clip slightly more.
- Transparency (e.g. RGBA PNG) is flattened onto white exactly where each
  photo is drawn; the rest of the page is plain white.

## Project files

- `CarnetSheetMaker.exe` — ready-to-run standalone Windows build
- `docs/CODE_SIGNING.md` — how to sign the exe (removes SmartScreen warnings)
- `carnet_gui.py` — minimal Tkinter GUI (file picker, live preview, Generate button)
- `carnet_sheet.py` — the layout engine, CLI, and PDF writer (shared by both)
- `make_icon.py` — regenerates the icon assets in `assets/`
- `assets/icon.ico`, `assets/icon_256.png` — application icon
- `assets/version_info.txt` — Windows version resource for the exe
- `build_windows_exe.bat` — one-click standalone exe build for Windows
- `.github/workflows/build-windows-exe.yml` — CI build of the same exe
- `test_carnet_sheet.py` — acceptance tests for the engine and CLI
- `test_carnet_gui.py` — tests for the GUI (skipped without a display)
- `sample_photo.png` / `sample_photo_sheet.pdf` — sample input and output
