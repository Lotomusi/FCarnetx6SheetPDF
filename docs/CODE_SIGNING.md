# Code signing the Windows exe

This document explains how to sign `CarnetSheetMaker.exe`, what signing
does and — importantly — **what it does not do** to SmartScreen.

## The honest summary about SmartScreen

- A valid signature from a certificate with **established reputation**
  removes the *"Windows protected your PC"* warning for most users.
- A **brand-new certificate has zero reputation**. SmartScreen will still
  warn at first; reputation builds from downloads/installs over time
  (typically weeks). Signing is necessary but not instantly sufficient.
- Signing also gives you **tamper protection** (any modification breaks
  the signature) and lets users verify the publisher name.

## Route A — Azure Artifact Signing (formerly Trusted Signing) — recommended

Microsoft's managed signing service: the certificate never leaves Azure,
individual developers are accepted, ~$9.99/month basic tier.

1. In the Azure portal, create a **Code Signing** account
   (service: *Artifact Signing / Trusted Signing*) and a **certificate
   profile** (Public Trust). Note the **endpoint** (e.g.
   `https://eus.codesigning.azure.net/`), **account name** and
   **profile name**.
2. Create an **App Registration** with a client secret, and grant it the
   **Artifact Signing Certificate Profile Signer** role on the profile.
3. Add these GitHub repository secrets:

   | Secret | Meaning |
   |---|---|
   | `AZURE_TENANT_ID` | Entra tenant ID |
   | `AZURE_CLIENT_ID` | App registration (client) ID |
   | `AZURE_CLIENT_SECRET` | App registration secret |
   | `AZURE_SIGNING_ENDPOINT` | e.g. `https://eus.codesigning.azure.net/` |
   | `AZURE_SIGNING_ACCOUNT` | Signing account name |
   | `AZURE_SIGNING_PROFILE` | Certificate profile name |

4. Run the **"Build Windows exe"** workflow. When the secrets exist, the
   workflow signs the exe automatically; otherwise it skips signing.

## Route B — self-managed certificate (signtool)

Buy an **OV or EV code-signing certificate** (Sectigo, DigiCert, SSL.com,
Certum — EV gives immediate SmartScreen reputation, OV builds it). Then
sign locally on Windows by setting environment variables before running
`build_windows_exe.bat`:

```bat
set SIGNTOOL_PATH=C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe
set SIGN_PFX_PATH=C:\secrets\mycert.pfx
set SIGN_PFX_PASSWORD=...
build_windows_exe.bat
```

The script signs `dist\CarnetSheetMaker.exe` after a successful build
(SHA-256 digest + RFC 3161 timestamp).

## Verifying a signature

```powershell
Get-AuthenticodeSignature .\CarnetSheetMaker.exe | Format-List
```

`Status` should be `Valid` and `SignerCertificate` should show the
publisher identity. Unsigned files report `NotSigned`.

## About UPX compression

Both build scripts support optional UPX compression (CI variable
`UPX=1`, locally `set UPX=1`). It runs **before** signing, because any
change after signing breaks the signature.

It is **off by default** on purpose: PyInstaller's archive is already
zlib-compressed, so UPX only squeezes the bootloader — measured on this
app at roughly **0.8 %** (20.3 MB → 20.1 MB). Against that small gain:

- slower startup (the exe unpacks itself in memory),
- some antivirus engines flag UPX-packed binaries (false positives),
- `--force` is required because the PyInstaller bootloader enables
  Control Flow Guard, which UPX does not support.

## What is set up in this repository

- `.github/workflows/build-windows-exe.yml` — signs with the official
  `azure/artifact-signing-action@v2` when the Azure secrets are present
  (skipped silently otherwise, so the build never breaks without them).
- `build_windows_exe.bat` — optional local signtool signing via the
  `SIGN_*` environment variables above.
