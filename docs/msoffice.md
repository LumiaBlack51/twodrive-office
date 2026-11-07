# Microsoft Office Windows preview

Word / Excel / PowerPoint COM add-in candidate, assembly 0.4.0.0. This is separate
from the existing 0.3.0 LibreOffice/ONLYOFFICE packages.

**Not yet a completed OneDrive takeover.** Installation requires a production
TwoDrive Development CFAPI root with a verified account-bound identity. Current
dev's generated CFAPI fixture is deliberately rejected. The pending native sync
and migration implementation must be reviewed and validated before installation.

The add-in saves existing named DOCX/XLSX/PPTX documents inside its configured root
using each editor's own Save operation, normally every 30 seconds. It runs on the
Office UI thread, skips clean documents, revalidates the root before each save,
and retains the editor's unsaved state when a save is refused, cancelled or fails.
TwoDrive's ribbon provides per-document pause and Save now. A local save does not
claim a completed cloud upload. Office's ordinary close/save prompt remains active
for edits not yet saved by the timer. It never sets `Saved=true` itself.

This preview does not export copies of unnamed/outside-root documents, implement
the earlier plugins' three-minute private-snapshot queue, or replace Office's
built-in AutoSave switch. Unsupported/legacy/macro, read-only, rights-managed,
signed and known encrypted files are refused. PowerPoint's masked Password getter
cannot classify encryption; an existing unencrypted PPTX package is required.
Unknown object-model capability fails closed. Simultaneous unrelated local
writers are not a supported coauthoring scenario; metadata changes stop automatic
overwrites, but this is not an atomic filesystem comparison-and-swap guarantee.

## Build and install

Windows .NET Framework 4.x compiler is used; no NuGet or Office PIA download is
needed. Current-user registration supports installed Click-to-Run x86/x64 Office.
The current native validation used Microsoft 365 x64 16.0.20527.20112.

```powershell
powershell -NoProfile -File msoffice/build.ps1 -Test
# Only after a production CFAPI root is available and Office is closed:
powershell -NoProfile -File dist/msoffice/install.ps1 -Root C:\TwoDrive\OneDrive
# Remove registration, preserving settings and user files:
powershell -NoProfile -File dist/msoffice/install.ps1 -Uninstall
```

Settings and installed binaries live under `%LOCALAPPDATA%\TwoDrive-Office`.
The settings bind the configured root identity; an account/root replacement is
not silently accepted. The installer does not launch an editor. Development tests
do not register the add-in or change the normal Office profile.

## Validation

- Eleven controller scenarios pass using generated files: clean open, interval,
  repeated root validation, disconnect, persistent pause, native failure/cancel,
  reentrancy, external-file conflict, Save As retargeting and unsupported format.
- Existing Linux Python tests: 40 passed; ONLYOFFICE Node tests: 21 passed.
- `tests/msoffice/run-host-smoke.ps1`: hidden, newly created Word/Excel/PowerPoint
  instances saved generated documents via the actual Office adapter. Dirty-state
  acknowledgement, persisted OOXML content and DisplayAlerts restoration passed
  for all three hosts. The actual STA timer loop also enumerated each generated
  document and automatically saved a second edit; its persisted OOXML was
  checked. Owned instances exited and generated files were removed.
- Not yet validated: registered COM loading/ribbon interaction, real CFAPI
  root/account binding, real cloud upload, x86
  Office, other Office versions, large/complex document corpus and crash recovery.
  Host smoke attaches the real host loop with an internal generated-root guard;
  it does not install COM registration or bypass the normal connection's CFAPI
  validation. Hosted CI compiles/tests without an installed Office license.

Early native checks found and corrected a dynamic COM dispatch issue caused by
passing COM objects through dynamically bound managed constructor/cleanup calls;
explicit object casts keep those managed calls statically bound. The hidden Word
Final property returned an unavailable-object error and is not used; read-only,
protection and signature checks remain. PowerPoint has no Presentation.FileFormat
property and returns a masked Password string even for a generated unencrypted
presentation; validation now inspects the existing plain OOXML package instead.
The first ZIP verification incorrectly opened with restrictive sharing; it now
uses read/write/delete sharing while inspecting the already-saved generated file.
All earlier failures are excluded from the successful final host-smoke result.

Sources: [COM add-ins](https://support.microsoft.com/en-gb/excel/excel-com-add-ins-and-automation-add-ins),
[Word Save](https://learn.microsoft.com/en-us/office/vba/api/word.document.save),
[Excel Save](https://learn.microsoft.com/en-us/office/vba/api/excel.workbook.save),
[PowerPoint Save](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentation.save),
[CFAPI root information](https://learn.microsoft.com/en-us/windows/win32/api/cfapi/nf-cfapi-cfgetsyncrootinfobypath).
