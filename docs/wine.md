# Microsoft Office on Wine / Soda (0.3.1 Preview)

This adapter runs a small native x64 COM DLL inside Word, Excel and PowerPoint.
The existing Linux Python bridge validates paths and persists local revisions.
It does not require .NET, VSTO, Microsoft Graph access or changes to TwoDrive.
The Windows/CFAPI candidate in PR #7 is a separate, unfinished integration.

Named DOCX/XLSX/PPTX files inside the configured TwoDrive mount enable on open.
The Office STA timer checks documents once per second and exports dirty content
at most once per five seconds. Word uses Document.WordOpenXML (Flat OPC), preserving
namespace bindings, package parts and binary media. Excel and PowerPoint use
SaveCopyAs into the private adapter directory. Linux validates the package and
fsyncs its complete local revision outside the mount. Clean documents aren't exported.

The default interval is 180 seconds. Original-file publication validates the mount,
source path and target fingerprint, then uses Office's own Save on its STA thread.
Linux verifies the just-saved bytes before retiring the local revision. Office's
native saved flag is preserved until native Save succeeds; this differs from the
ONLYOFFICE/LibreOffice local-snapshot acknowledgement. Ctrl+S retains its ordinary
native behavior and its observed save event reconciles the local queue. Local
saving and mount publication do not prove completed OneDrive upload.

The TwoDrive ribbon offers per-document pause and Save now. Outside-root or
unnamed documents stay off until explicitly enabled; they save a copy in the
configured copy directory and retain the original's dirty flag. Choose that
shared default with `twodrive-office configure --copy-directory ...`.

Excel cannot reliably export/save while its BeforeClose callback is on the stack.
Enabled close events are cancelled briefly and posted back to the same STA thread;
that callback captures the final content, saves, then closes. Failure leaves the
document open and reports the error in the ribbon without an automatic dialog.
Copy mode retains Office's ordinary source-save prompt. Application force-kill,
OS shutdown and crashes can leave the latest unsampled edits only in Office's own
recovery state. Successful local snapshots remain under the existing private
pending directory and must never be deleted as cache.

If the editor disappears or fails native publication, the revision remains queued.
It is only recovered after **all** supported editors have exited, using the same
`pending` / `recover` commands as the other adapters. A pending target is locked
against another editor/session. Rename/Save As, external changes, disconnected
mounts and changed Wine drive mappings fail closed. No coauthoring or atomic
cross-process compare-and-swap guarantee is claimed between the final check and
Office's native Save.

## Installation

Unzip the Wine x64 release package. Fully exit the chosen bottle first. Installation
edits only this add-in's eight registry sections in the explicit prefix's
`system.reg` and its private `%LOCALAPPDATA%\TwoDrive-Office-Wine` directory;
it does not launch Wine or an editor, change macro trust, run a login poller, or
alter another bottle. This installation needs the machine-level keys **inside the
bottle**: this Wine setup does not merge these per-user classes into HKCR, and
Office did not enumerate the per-user Addins entries. It needs no host root access.

```bash
python3 -m twodrive_office.cli configure
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge
# Remove only this COM registration, keeping settings and pending user data:
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --uninstall
```

The bridge shares the existing authenticated loopback socket with ONLYOFFICE.
The systemd user manager retains just the socket; Python starts on connection
and exits after editors/requests/pending work permit it. Existing installations
must update both the Linux runtime and Wine DLL. Credentials remain private.

The bridge resolves the live `dosdevices` mapping on Linux before each capture
and native publication. It does not assume that Z: maps to /. Windows network/UNC,
relative and ambiguous case-folded paths are refused. This also avoids the installed
Office/AppV process's failure to use Wine's Unix-name API, which worked in a standalone
Wine probe but failed inside Office.

## Compatibility and reproduction

Only native **x64** Office is supported by this package. The current installation
is Microsoft 365 Office16 on Soda `11.0-37-office-v7`; exact actual verification
results are recorded in [verification.md](verification.md). The COM loader,
Office timer, save events and callback methods are tested against real applications,
not just an Office model mock. Actual mouse-click ribbon interaction is distinct
from directly invoking its registered callback methods.

The adapter rejects unsupported extensions, known password/protection/macro and
signed packages, read-only files and known IRM state. This Wine installation's
Permission/MSIPC getter is unavailable on ordinary documents: plain unsigned
OOXML source/export validation is used, but dynamically applying IRM and broader
protected-document workflows remain unsupported/unverified. PowerPoint's Password
getter is masked even for plain files, so source-container validation is required.
Complex documents, legacy Office, x86, other Wine runners/versions, embedded audio
and video, strict-format variants, rights management and real cloud round trips
are outside the verified claim.

```bash
CXX_WINE=x86_64-w64-mingw32-g++ python3 scripts/build_wine.py
python3 scripts/build.py
python3 tests/wine_integration.py \
  --prefix /absolute/path/to/installed-office-prefix \
  --runner /absolute/path/to/soda/bin --xvfb /absolute/path/to/Xvfb \
  --compiler x86_64-w64-mingw32-g++ --report /tmp/wine-result.json
```

The integration runner copies the stopped prefix, removes external user-folder
links from the copy, creates a mock mount and isolated settings, and uses an
authenticated Xvfb with no TCP listener or Wayland connection. It verifies the
actual Office process environment before creating documents. Cleanup terminates
only verified test-prefix process groups, removes the temporary prefix/documents,
and checks that the original Wine registry bytes did not change.

Sources: [Word Flat OPC](https://learn.microsoft.com/en-us/office/vba/api/word.document.wordopenxml),
[Excel SaveCopyAs](https://learn.microsoft.com/en-us/office/vba/api/excel.workbook.savecopyas),
[PowerPoint SaveCopyAs](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentation.savecopyas).
