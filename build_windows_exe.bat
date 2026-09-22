@echo off
rem ============================================================
rem  Build a standalone Windows executable for the
rem  Carnet Photo Sheet Maker GUI (carnet_gui.py).
rem
rem  Requirements on this machine:
rem    * Python 3.9+ from python.org (tkinter included)
rem      - during install, keep "Add python.exe to PATH" ticked
rem
rem  Output: dist\CarnetSheetMaker.exe  (single file, no Python needed)
rem ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/4] Creating an isolated build environment .venv-build ...
python -m venv .venv-build
if errorlevel 1 goto :failed

echo [2/4] Installing pillow, reportlab, pyinstaller ...
".venv-build\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv-build\Scripts\python.exe" -m pip install --quiet pillow reportlab pyinstaller
if errorlevel 1 goto :failed

echo [3/4] Running the test suite before packaging ...
".venv-build\Scripts\python.exe" -m unittest discover -p "test_*.py"
if errorlevel 1 goto :failed

echo [4/4] Building dist\CarnetSheetMaker.exe ...
".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
    --name CarnetSheetMaker ^
    --icon assets\icon.ico ^
    --version-file assets\version_info.txt ^
    --add-data "assets\icon_256.png;assets" ^
    carnet_gui.py
if errorlevel 1 goto :failed

rem -----------------------------------------------------------
rem  Optional: compress with UPX. Opt-in because PyInstaller's archive is
rem  already zlib-compressed (measured gain here: ~0.8%), startup gets
rem  slower, and some antivirus tools flag UPX-packed binaries.
rem  Enable with:  set UPX=1
rem  Optional:     set UPX_PATH=C:\tools\upx.exe   (default: upx.exe on PATH)
rem  NOTE: --force is required because the PyInstaller bootloader enables
rem  Control Flow Guard (GUARD_CF), which UPX refuses to pack otherwise.
rem  Compress BEFORE signing: any later change breaks the signature.
rem -----------------------------------------------------------
if not "%UPX%"=="1" goto :no_upx
if not defined UPX_PATH set "UPX_PATH=upx.exe"
"%UPX_PATH%" --version >nul 2>nul
if errorlevel 1 (
    echo [WARN] UPX enabled but upx.exe not found on PATH ^(or UPX_PATH^); skipping compression.
    goto :no_upx
)
echo Compressing dist\CarnetSheetMaker.exe with UPX ...
"%UPX_PATH%" --best --lzma --force "dist\CarnetSheetMaker.exe"
if errorlevel 1 goto :failed

:no_upx
rem -----------------------------------------------------------
rem  Optional: sign the exe if signing variables are set.
rem  SIGNTOOL_PATH   - full path to signtool.exe (Windows SDK)
rem  SIGN_PFX_PATH   - path to the code-signing certificate .pfx
rem  SIGN_PFX_PASSWORD - password for the .pfx
rem  See docs\CODE_SIGNING.md
rem -----------------------------------------------------------
if not defined SIGNTOOL_PATH goto :done
if not defined SIGN_PFX_PATH goto :done
if not exist "%SIGNTOOL_PATH%" (
    echo [WARN] SIGNTOOL_PATH is set but signtool.exe was not found there; skipping signing.
    goto :done
)
if not exist "%SIGN_PFX_PATH%" (
    echo [WARN] SIGN_PFX_PATH is set but the .pfx file was not found; skipping signing.
    goto :done
)
echo Signing dist\CarnetSheetMaker.exe ...
"%SIGNTOOL_PATH%" sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f "%SIGN_PFX_PATH%" /p "%SIGN_PFX_PASSWORD%" "dist\CarnetSheetMaker.exe"
if errorlevel 1 goto :failed
"%SIGNTOOL_PATH%" verify /pa "dist\CarnetSheetMaker.exe"
echo Signing complete.

:done
echo.
echo ============================================================
echo  Build finished: dist\CarnetSheetMaker.exe
echo  Copy that single file anywhere; double-click to run.
echo  No Python installation is required on the target PC.
echo ============================================================
pause
exit /b 0

:failed
echo.
echo [ERROR] The build failed. Read the messages above.
pause
exit /b 1
