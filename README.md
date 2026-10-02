# Carnet Photo Sheet Maker

A small, local Python utility that takes one or more **already-finished
carnet photographs** and produces a **print-ready PDF** containing copies of
them arranged edge to edge (zero gaps between adjacent photos, so the sheet
can be cut in straight lines) near the top of a US Letter portrait page —
replacing the manual PowerPoint import → duplicate → align → export
workflow.

It performs **layout and document generation only**. It never edits the
photographs: no cropping, stretching, rotating, beautification, background
removal, or AI processing of any kind. Works fully offline. The one opt-in
exception is the per-photo **Adjust…** recipe (v1.3): when you explicitly
ask for it, a photo can be cropped to the cell ratio ("fill") or
quarter-turned — in memory only, never on disk — so a photo whose ratio
does not match the cell can still fill it exactly.

Two interfaces share the same layout engine:

- **`carnet_gui.py`** — a minimal window: add one or more photos (each with
  its own copy quantity, default 6), optionally adjust settings in
  millimetres, click **Generate PDF**, get the result (and a prompt to open
  it in your PDF viewer).
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
version metadata). Its version resource reads 1.3.0.0. Rebuild any time
with either option above to pick up code changes.

#### Verifying the exe on a Windows machine

The exe has two built-in test hooks (no Python needed):

- **`CarnetSheetMaker.exe --selftest PHOTO OUT_PDF`** — full export smoke
  test: builds the window, generates a real PDF through the Generate
  button, writes `SELFTEST-OK` to `OUT_PDF.selftest.log`.
- **`CarnetSheetMaker.exe --uxcheck OUT_PDF`** — 47-point UX check of the
  size-field controls: presets, arrow-key nudging (±0.5 mm, Shift ±0.1 mm),
  red invalid-field labels, comma decimals, the live fit summary, and three
  real exports (default, overflowing tweak, invalid settings). Pops a
  summary dialog (`UX-CHECK-OK`) and writes per-step results to
  `OUT_PDF.uxcheck.log`. Runs against an isolated settings store, so your
  own remembered settings are untouched. An optional third argument uses
  your own photo instead of a generated one, and `UXCHECK_NO_DIALOG=1`
  skips the summary dialog (used by automated runs).

Quick manual spot-check after downloading a new build: open the exe →
pick the "ID-2 35×45" preset (fields become 35.0000/45.0000) → press ↑ on
a size field (value +0.5 mm) → type "abc" in spacing (label turns red) →
add a photo, Generate (overflowing sizes shrink to fit instead of
erroring).

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

1. **Add photo…** — select one or more finished carnet photographs
   (JPEG, PNG or WebP). Each gets its own **copies** box (default 6) and a
   default output name (`<first-photo>_sheet.pdf`) is filled in
   automatically.
2. Optionally adjust the settings (all sizes in millimetres), the paper
   size, orientation and columns. **Remove selected** / **Clear all**
   manage the list; a photo's copies can be edited at any time.

   The size fields are forgiving: a **size preset** picker offers the
   stock 30×40 mm carnet geometry plus common ID sizes; typing a
   non-number turns the field red instead of failing the export; comma
   decimal separators ("35,5") are accepted; **↑/↓** nudges a field by
   0.5 mm (**Shift** for 0.1 mm); and a live line under the fields shows
   whether the tweak fits the page, the resulting photo size, and the
   page count before you commit to generating.

   An amber line under that summary warns when a photo's ratio will not
   fill the cell exactly (a photo that is not truly 3:4, or after a size
   tweak), naming the white bars it would print with. Fix it with the
   per-row **Adjust…** button: *Fit whole photo* (default — the photo is
   used exactly as provided, white bars and all) or *Fill & crop*, which
   crops the photo to the cell ratio so it fills it exactly — you drag the
   preview to choose the crop window, zoom to crop tighter, and can
   quarter-turn sideways photos (90° steps). The recipe is applied in
   memory only (the file on disk is never touched), remembered per photo,
   and shown in the main preview, so what you see is what prints. Rows
   carrying a recipe show **Adjusted ✓**.
3. Click **Generate PDF**. Missing photos and unreadable files appear as
   dialogs, never as a broken PDF. Size tweaks that do not fit the page
   are shrunk to fit automatically (the status line tells you the final
   size); a field left blank or non-numeric falls back to the stock
   setting with a note instead of blocking the export.
4. On success the status line reports the exact geometry, and you are
   offered to open the PDF in the system's default viewer
   (`os.startfile` on Windows, `xdg-open`/Okular/Evince on Linux).

The window is deliberately minimal; the checkbox **Shrink to fit page
instead of erroring** (on by default) keeps exports working when tweaked
sizes overflow the page — untick it to preview the exact, possibly
overflowing geometry with a warning. The CLI always previews/errors
exactly and needs `--fit-to-page` for the same behaviour.

### Live preview

A miniature of the first sheet page is drawn next to the settings and
refreshes as you type — page size, photo rectangles and their placement,
or a "layout does not fit" warning. Once photographs are added, each frame
shows a **real thumbnail** of its photo (EXIF-oriented, transparency
flattened, centred in its frame); unreadable files fall back to tinted
rectangles. When the job needs more than one page, the label under the
preview reports the page count and total photos. The preview uses the same
layout engine as the PDF, so what you see is what will be generated.

### Remembered settings

Your layout settings, the photo list (paths and per-photo quantities) and
the last output folder persist between sessions in the per-user config
directory (the shrink-to-fit choice is deliberately not persisted: it is
always on for generation):

- Windows: `%APPDATA%\CarnetSheetMaker\settings.json`
- Linux: `~/.config/CarnetSheetMaker/settings.json`

Persistence is best-effort: a missing or corrupt file simply falls back
to the defaults. Settings saved by older versions (which had fixed gaps
between photos) are migrated: the spacing fields fall back to the new
zero-gap defaults while everything else is restored.

Tip (Windows): copy `carnet_gui.py` to `carnet_gui.pyw` and double-click
it — the `.pyw` extension launches the GUI without a console window.

## Usage

```bash
python3 carnet_sheet.py photo.jpg
```

That single command creates `photo_sheet.pdf` next to the photo — six
copies packed edge to edge, horizontally centered, 30 pt (~10.6 mm) from
the top edge, each photo 85.61 × 114.14 pt (~30.2 × 40.3 mm), with thin
cutting borders.

Preview before printing:

```bash
okular photo_sheet.pdf        # or evince, gwenview, etc.
```

### Multiple photos in one job

Pass several photographs to combine them on the same sheet(s):

```bash
# 6 copies of a.jpg, 6 of b.jpg and 3 of c.jpg — 15 photos on one page:
python3 carnet_sheet.py a.jpg b.jpg c.jpg --copies 6,6,3

# Quantities are per image, in the same order as the files:
python3 carnet_sheet.py a.jpg b.jpg c.jpg --copies 2,1,6

# One number for a single image (default 6):
python3 carnet_sheet.py a.jpg --copies 10
```

The requested photos are placed sequentially (all copies of the first
image, then the next), filling each page row by row. When a page is full,
the layout continues onto additional PDF pages with the same rules.

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
| `image…` | — | Input photograph(s) (JPEG, PNG, WebP); several allowed |
| `-o, --output` | `<first-image>_sheet.pdf` | Output PDF path |
| `--paper` | `letter` | `letter`, `a4`, or `legal` |
| `--orientation` | `portrait` | `portrait` or `landscape` |
| `--copies` | `6` | Copies of a single image; with several images, comma-separated quantities in file order |
| `--columns` | `6` | Photos per row; extras wrap to further rows and pages |
| `--photo-width` | `85.79` pt | Width of one photo box |
| `--photo-height` | `114.14` pt | Height of one photo box |
| `--spacing-x` | `0` pt | Horizontal gap between photos (0 = cut-together) |
| `--spacing-y` | `0` pt | Vertical gap between rows (0 = cut-together) |
| `--top-margin` | `30` pt | Distance from page top to the strip |
| `--left-margin` / `--right-margin` | `30` pt | Side margins |
| `--no-border` | borders on | Omit the thin rectangles around each photo |
| `--fit-to-page` | off | Shrink (never stretch) to fit the page instead of erroring |
| `--adjust` | off | Opt-in per-photo crop applied in memory (files untouched), to every input image: `fill` (crop to the cell ratio, photo fills the cell exactly) or `fit` (default, whole photo). Extra keys: `zoom=N` (≥1), `offset_x=N`, `offset_y=N` (0–1, 0.5 centres), `rotation=N` (0/90/180/270). Example: `--adjust fill,zoom=1.2,offset_y=0.4` |
| `--mm` | off | Interpret provided sizes/margins in millimetres |

Points are the PDF-native unit: 72 pt = 1 inch, so 85.79 × 114.14 pt ≈
30.3 × 40.3 mm. With `--mm`, only the values you actually pass on the
command line are treated as millimetres; unset options keep their
point defaults.

### Zero-gap layout (why there are no gaps)

Adjacent carnet photos touch: there is **no intentional whitespace**
between neighbouring photos or rows. The photos are cut from the printed
sheet, so every gap used to mean extra cutting work. The margins (30 pt
around the content strip) are untouched, the configured photo dimensions
are unchanged, and `--spacing-x` / `--spacing-y` still exist for anyone
who wants a gap — they simply default to 0 now. Thin cutting borders
(on by default) remain the visual guide for scissors.

### How sizing works (exact cells, aspect-true photos)

Every photo cell is laid out at **exactly** the configured photo size —
whatever the photograph's aspect ratio is. A 30 × 40 mm box produces
30 × 40 mm cells for a 3:4 photo, a 4:3 photo or a square one; six of them
always measure exactly 6 × their width on the printed sheet.

Inside its cell, the photograph is embedded at **its own aspect ratio**
(the largest aspect-true fit, centred):

- If the image aspect matches the cell (the expected case for a finished
  carnet photo), the photo fills the cell completely.
- If not (e.g. a square photo in a portrait cell), the photo is
  letterboxed/pillarboxed on white — centred, never stretched, squashed
  or cropped.
- The opt-in fix for unwanted bars is the per-photo **Adjust…** recipe:
  *Fill & crop* trims the photo (in memory, never on disk) to the cell
  ratio — chosen visually in the dialog, or `--adjust fill` on the CLI —
  so the cell is filled exactly without ever stretching the photo.
- The whole arrangement fits the page: by default the GUI shrinks it
  uniformly when the tweaked sizes do not fit (and says so in the status
  line). The CLI errors instead unless `--fit-to-page` is passed.

> Historical note: versions ≤ 1.1 shrank the *cell* to the image aspect
> (a 4:3 photo got a smaller-than-configured box). That is why a modified
> width could “miraculously” fit and why the minimum 4×3 cm came out
> smaller for some photos. Since 1.2 the configured size is honoured
> exactly.

---

## Validation

Run the acceptance test suite (81 engine/CLI tests plus 71 GUI tests,
stdlib `unittest` only; GUI tests skip automatically when tkinter or a
display is unavailable):

```bash
python3 -m unittest discover -p "test_*.py" -v
```

The tests generate real PDFs and parse them back (page MediaBox,
content-stream operators, embedded image objects) to verify:

1. Output is a valid, single-page PDF for a single-image job.
2. Page is exactly US Letter: 612 × 792 pt.
3. Default layout contains exactly six photo placements.
4. All six placements reference the same embedded image.
5. Each photo cell is drawn at exactly the configured physical dimensions
   (for every image aspect ratio).
6. The photo's aspect ratio is preserved everywhere (default, contained,
   fit-to-page, EXIF-rotated inputs).
7. All placements remain within the page boundaries.
8. The strip is horizontally centered (left gap = right gap).
9. Adjacent photos have zero intentional spacing (default) and explicit
   spacing values are still honoured when requested.
10. Multiple source images each get their requested number of copies,
    placed sequentially, sharing one sheet and continuing onto extra
    pages when needed; every image is embedded with its own pixels.
11. Missing files, corrupt images, impossible layouts, invalid numbers,
    quantity mismatches and unwritable outputs all produce clear errors
    (exit code 2), never a malformed PDF.
12. The input files are byte-identical after generation.
13. Tweaked size fields never make the GUI export fail: a layout that
    overflows the page is shrunk uniformly (the status line explains the
    resulting size), and an unparsable field falls back to the stock
    settings with a note — never an error dialog.
14. Cells keep the exact configured size for any image aspect; the image
    content stays aspect-true inside its cell.

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
  wider than 3:4 (0.75). Distortion is never introduced; pass a    `--photo-width 85.605` if you need that last 0.2 mm.
- The drawn cell is always exactly the configured size; a photo whose
  aspect differs from the cell is centred inside it on white (pillar-
  or letterboxed). With the default 85.79 pt-wide box, a 3:4 photo shows
  hairline white slivers on left/right (85.79 − 85.605 pt total); the
  cutting borders still trace the cell, which keeps the grid exact.
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
