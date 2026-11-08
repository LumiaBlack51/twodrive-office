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
  checked. The same checks also passed with the real CFAPI root guard against an
  explicitly created generated-data native root. The root identity includes its
  directory file ID; junctions and unsupported reparse points are rejected without
  following them. Owned instances exited and generated files were removed; the
  fixture owner confirmed unregistration and removed its empty root.
- Not yet validated: registered COM loading/ribbon interaction, a production
  account's native root, real cloud upload, x86
  Office, other Office versions, large/complex document corpus and crash recovery.
  Host smoke attaches the real host loop with an internal generated-root guard;
  it does not install COM registration or bypass the normal connection's CFAPI
  validation. Hosted CI compiles/tests without an installed Office license.

The optional `tests/msoffice/run-cfapi-smoke.ps1 -FixtureOwner ABSOLUTE_EXE`
uses TwoDrive's `experiments/windows-cfapi/examples/office_fixture.rs` built with
`native-sync,native-test-hooks`. It creates a new generated root, saves only its
three generated documents, and requires the owner to confirm empty-root cleanup.
This is not the installed client, a production account or a real cloud test.

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

### Open-document capture boundary (2026-11-08)

The hidden generated CFAPI host test now measures the synchronizer access while
the document remains open after the actual timer save. Word, Excel and PowerPoint
all refused a share-none read handle, while `CfOpenFileWithOplock` with read-only
share-all flags succeeded. All native saves, OOXML checks and fixture cleanup
passed. These are access diagnostics, not proof of a consistent read during
concurrent edits, and do not authorize weaker capture or a cloud-sync claim.

The current companion exclusive capture must wait while an editor retains its
file handle. Local timer saving remains available; the add-in already reports
local save separately from TwoDrive cloud completion. No registered COM/ribbon or
real Office-to-cloud transfer was exercised, and no normal Office profile was
modified.

### Generated Office-to-cloud pipeline (2026-11-08)

`tests/msoffice/run-cloud-smoke.ps1` coordinates hidden owned Office instances
with the companion `native_office_cloud_fixture` executable. Initial native-save
baselines are copied before edits; the actual STA timer persists the final OOXML.
After the three editors close, those generated final documents are captured under
the real CFAPI root and conditionally uploaded over their initial personal-OneDrive
versions. Full cloud bytes and native in-sync acknowledgement passed for all three
hosts. The generated remote folder/files, documents, baselines and native root were
completely retired. The current personal account was verified independently.

This verifies the actual component path after documents unlock. It does not
establish registered COM/ribbon loading, continuous uploads while an editor holds
its file, or whole-root takeover. The host preserves an unexpected visible/existing
Office instance and only quits an owned instance once its generated document is
closed and no other documents remain.

The installer now rejects known generated root identities before modifying the
normal profile; production registration must carry the account-bound digest form.
The host-test guard still accepts its explicit generated roots. This corrects the
earlier description that implied every generated native-prefix fixture would be
rejected by the old prefix-only installer. No normal registration was performed.


### 管理员且关闭 UAC 的设备

普通安装使用当前用户组件注册。若设备配置使 Windows 忽略用户 COM 注册，
安装器会在修改前提示使用 `install.ps1 -Root <同步根> -MachineCom`。该方式需
管理员权限，仅共享代码组件进入受保护 Program Files 并注册 COM 类；Word/
Excel/PowerPoint 启用项及 30 秒根设置仍限当前用户。不改变 UAC 等系统配置。
`-Uninstall` 移除当前用户 Office 启用，保留组件/设置；显式 `-MachineCom
-Uninstall` 才移除共享组件注册。程序文件与用户文档保留。

2026-11-09 已在接管后的个人目录完成三宿主实际 COM 连接验证；详见验证记录。
