<p align="center">
  <img src="onlyoffice/icon.svg" width="72" height="72" alt="TwoDrive cloud icon">
</p>
<h1 align="center">TwoDrive Office AutoSave</h1>
<p align="center"><strong>Save in your editor. TwoDrive syncs the file to OneDrive.</strong></p>
<p align="center"><a href="README.md">简体中文</a> · English</p>
<p align="center">
  <a href="#get-started"><strong>Get started</strong></a> ·
  <a href="https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.2">Linux preview downloads</a> ·
  <a href="docs/usage.md">Usage and recovery (中文)</a> ·
  <a href="docs/verification.md">Verification record</a>
</p>

Open a document in your TwoDrive mount on Linux and AutoSave turns on for that document. Edits are staged outside the mount. By default, the latest revision is written to the mount every **180 seconds**. Use **Save now** to request an earlier write, then check TwoDrive for upload progress.

| Editor | What it saves | Guide |
| --- | --- | --- |
| **LibreOffice** Writer, Calc and Impress | In-place documents in the mount, or a separate copy when enabled for an untitled or outside file. | [Get started](#get-started) |
| **ONLYOFFICE Desktop Editors** | A desktop model snapshot through the local bridge. The TwoDrive panel shows per-document status. | [Get started](#get-started) |
| **Microsoft 365 x64 on Wine / Soda** | Private revisions of Word, Excel and PowerPoint files, followed by a native Office save for in-place documents. | [Wine installation](docs/wine.md#installation) |
| **Microsoft Office on Windows** | Source preview. Saves named DOCX, XLSX and PPTX files in an account-bound TwoDrive Development CFAPI root, with a default interval of **30 seconds**. | [Windows build and install](docs/msoffice.md#build-and-install) |
| **WordCraft, GridCraft and DeckCraft** | Protocol preview for copying versions the editor has written to disk. Installation awaits a verified official host. | [Craft protocol and recovery (中文)](docs/artcraft.md) |

## Get started

For LibreOffice and ONLYOFFICE, use Python 3.10+ and an active TwoDrive mount. From the source checkout, configure the mount and run the installer for your editor. Restart the editor when convenient. [Set up the TwoDrive client](https://github.com/LumiaBlack51/twodrive/blob/main/doc/getting-started.md) · [Download v0.3.2 source with the installers](https://github.com/LumiaBlack51/twodrive-office/archive/refs/tags/v0.3.2.zip)

```bash
python3 -m twodrive_office.cli configure

# LibreOffice
python3 scripts/install.py --libreoffice

# ONLYOFFICE Desktop Editors
python3 scripts/install.py --onlyoffice --bridge
```

The v0.3.2 release includes the OXT, the ONLYOFFICE plugin, the Wine x64 ZIP and SHA-256 checksums. ONLYOFFICE also needs the bridge and its connection settings. For Wine, exit the target bottle and follow the instructions in the extracted package. [Downloads](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.2) · [Wine guide](docs/wine.md#installation)

## Where your changes are

**Saved locally** means the private queue holds a recoverable revision. **Submitted to TwoDrive** means the file was written to the mount. TwoDrive reports cloud transfer progress separately. If the mount disconnects or the destination changes, keep the pending revision. From the source checkout, `python3 -m twodrive_office.cli pending` lists revisions. Close the editors normally before running `python3 -m twodrive_office.cli recover`. [Linux usage and recovery (中文)](docs/usage.md)

LibreOffice and ONLYOFFICE target DOCX, XLSX, PPTX, ODT, ODS and ODP. Support varies by host and format. The ONLYOFFICE adapter depends on native Desktop interfaces and the bundled x2t converter. Macro-enabled, encrypted and legacy binary documents are outside the supported scope. [Tested versions and limits](docs/verification.md)

Original code is MIT licensed. The bundled ONLYOFFICE SDK retains its upstream AGPL-3.0 license and additional terms. [License](LICENSE) · [Third-party notice](onlyoffice/vendor/NOTICE.txt)
