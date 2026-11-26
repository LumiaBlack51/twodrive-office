# Microsoft Office on Wine / Soda

This adapter runs a small native x64 COM DLL inside Word, Excel and PowerPoint.
The existing Linux Python bridge validates paths and persists local revisions.
It does not require .NET, VSTO, Microsoft Graph access or changes to TwoDrive.
Download the [v0.3.2 Linux preview](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.2). For native Windows, see the [CFAPI add-in guide](https://github.com/LumiaBlack51/twodrive-office/blob/main/docs/msoffice.md).

Named DOCX/XLSX/PPTX files inside the configured TwoDrive mount enable on open.
The Office STA timer checks documents once per second. Timer captures normally
wait for about 1.5 seconds without keyboard or pointer input. The per-document
gap between attempts grows from five to thirty seconds for slow exports.
Continuous input can defer an eligible capture until the document has been
observed dirty for thirty seconds or its publication interval is due. These
are scheduling thresholds. Office being busy or a write failing can delay a
durable recovery version beyond them. Word uses Document.WordOpenXML (Flat OPC), preserving
namespace bindings, package parts and binary media. Excel and PowerPoint use
SaveCopyAs into the private adapter directory. A worker reads the private export, encodes Word XML, hashes the bytes and
waits for the bridge to validate and fsync a complete local revision outside
the mount. Only one transfer per document runs at a time. The ribbon reports
local success after the reply. Office object-model export and native Save
still execute on the host thread; file size and content can affect input latency. Clean documents aren't exported.

The default interval is 180 seconds. Original-file publication validates the mount,
source path and target fingerprint, then uses Office's own Save on its STA thread.
Linux verifies the just-saved bytes before retiring the local revision. Office's
native saved flag is preserved until native Save succeeds; this differs from the
ONLYOFFICE/LibreOffice local-snapshot acknowledgement. Ctrl+S retains its ordinary
native behavior and its observed save event reconciles the local queue. Local
saving and mount publication do not prove completed OneDrive upload.

The Word export failure guard preserves Office's exception code and
description instead of showing only `80020009`. A returned `WordOpenXML`
serializer exception pauses that document's automatic saving, retaining its
native dirty flag and any earlier durable revision. It no longer retries the
failed serializer every second or repeatedly vetoes a user-initiated close;
Word keeps its ordinary save/discard/cancel choice. Use native Ctrl+S to save
the current edits, and explicitly re-enable automatic saving to try again.
Transient busy/transport failures retain their retry path; failed capture
attempts are spaced at least five seconds apart. Export also suppresses alerts
temporarily and restores the prior value on failure. This guard acts only after
the COM call returns: it cannot interrupt an export that never returns. The
reported exception has not been reproduced with authorized document copies.
Their strict text checks exposed a separate Wine xmllite Unicode reading bug,
reproducible without Office through IXmlReader and with this add-in absent.
The local Soda Office v14 runtime fixes that reading bug and passes exact text,
image and edited native-save checks on those copies. This runtime fix is separate
from the COM adapter package; see the verification record for its focused patch,
tests and remaining compatibility limits.

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
edits only this add-in's eleven registry sections in the explicit prefix's
`system.reg` and its private `%LOCALAPPDATA%\TwoDrive-Office-Wine` directory;
it does not launch Wine or an editor, change macro trust, run a login poller, or
alter another bottle. This installation needs the machine-level keys **inside the
bottle**: this Wine setup does not merge these per-user classes into HKCR, and
Office did not enumerate the per-user Addins entries. It needs no host root access.
The v0.3.2 installer also updates this add-in's three Click-to-Run virtual
machine Addins keys. A retained `LoadBehavior=2` there can override the ordinary
registration's `3`, leaving the component installed but disconnected. Other
add-ins, trust settings and security policies are not changed. Startup tests now
require an already-connected add-in and never force `COMAddIn.Connect=true` or
refresh the collection to hide a failed automatic connection.

```bash
python3 -m twodrive_office.cli configure
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge
# For isolated cpak networking with event-driven reactivation
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge --sandbox-wakeup
# Remove only this COM registration, keeping settings and pending user data:
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --uninstall
```

The bridge shares the existing authenticated loopback socket with ONLYOFFICE.
The systemd user manager retains just the socket; Python starts on connection
and exits after 30 seconds without authenticated
traffic and with no request in flight. An orphaned Office PID never retains it;
failed pending data stays on disk for a later connection or explicit recovery.
Editor presence still blocks unsafe recovery, not process exit. The DLL's existing
ten-second session heartbeat keeps live sessions available and now distinguishes
expired sessions so the next poll can reopen through normal validation. Recovery
data blocks unsafe reopening. Existing installations must update the Linux runtime,
Wine DLL and any installed ONLYOFFICE plugin together. Credentials remain private.

The bridge resolves the live `dosdevices` mapping on Linux before each capture
and native publication. It does not assume that Z: maps to /. Windows network/UNC,
relative and ambiguous case-folded paths are refused. This also avoids the installed
Office/AppV process's failure to use Wine's Unix-name API, which worked in a standalone
Wine probe but failed inside Office.

<a id="cpak-isolated-networking-unreleased-fix"></a>

### cpak isolated networking

The ordinary loopback installation requires Office to reach the host's
`127.0.0.1`. cpak uses a separate network namespace and can launch slirp4netns
with `--disable-host-loopback`; changing the address to `10.0.2.2` does not enable
that blocked route ([slirp4netns reference](https://github.com/rootless-containers/slirp4netns/blob/master/slirp4netns.1.md#filtering-connections)).
The Linux runtime provides a relay for **only the configured
bridge port**. It binds `127.0.0.1` in the owned container namespace, transfers
the listener descriptor to a host process, and forwards to the existing host
bridge. Authentication, Host/Origin checks, sessions and save policy stay in
that single bridge. It does not enable host networking or expose a LAN listener.
The relay exits when that container PID or namespace disappears. In v0.3.2 the startup and idle windows are 60 and 30 seconds respectively.
On the main branch, after wakeup it waits up to 90 seconds for the first connection. Once it accepts a connection,
the relay exits when all forwarding workers have finished and 90 seconds have
elapsed since the last accepted connection. These windows allow for Office's
60-second retry backoff and the next timer tick. A retained container or
orphaned Office PID alone cannot keep the relay resident. Shutdown signals forwarding workers;
an idle peer cannot keep a worker waiting forever.

cpak can run Office in a nested user namespace while keeping the outer
container's network namespace. The relay obtains that network's actual owning
user namespace with Linux `NS_GET_USERNS`, pins both namespace descriptors, and
enters the owner before creating the loopback listener. Entering Office's own
user namespace instead fails with `Operation not permitted` in this setup and
leaves the DLL reporting WinHTTP 12029. Linux namespace ioctls and `nsenter`
with explicit namespace-file arguments are required; host root privileges and
changes to cpak's network policy are unnecessary.

Install cpak wakeup support using `--bridge --sandbox-wakeup` with
the bottle stopped. It installs a per-prefix systemd user `.path` unit and an
on-demand relay service. On a failed connection, the DLL creates an empty private
`relay.request` directory beside itself, at most once every five seconds. The
path unit starts the host helper, which consumes that directory, verifies the
existing connection credentials/prefix and selects the live owned Office
namespace. It never launches Wine or Office. The service exits with the idle
relay, while the user manager retains only its path watch. Opening a document
from an already running empty Office can therefore reactivate the connection
after the relay has exited. No permanent launcher monitor is required.

The directory trigger cannot truncate a user file; symlink triggers are rejected.
The helper canonicalizes Wine user-directory aliases such as `steamuser` before
checking uniqueness. Multiple distinct namespaces for the same prefix are
refused rather than guessed. `--uninstall` removes this prefix's path/service
units along with its COM registration and keeps recovery data.

For manual connection diagnostics, the older host entry point remains available:

```bash
cd ~/.local/lib/twodrive-office
PYTHONDONTWRITEBYTECODE=1 python3 -m twodrive_office.wine_namespace \
  --cpak ~/.local/bin/cpak
```

This command starts/connects the Bottles container with `@true`, identifies its
unique init PID, and creates or reuses the port relay; it does not launch Office.
For another instance, add `--instance NAME`; an already identified owned sandbox
can instead use `--pid PID`. This requires Linux user/network namespaces and
`nsenter`. A manual launcher hook belongs **after** grant creation/idle-container
refresh and **before** the final Office exec. It is unnecessary with the installed
path activation. Update the
Linux runtime and DLL together, with the bottle fully stopped; an already loaded
DLL is not upgraded by replacing its file.

Granted `/run/cpak/grants/...` paths are translated using the live Office process's
mount table and the host mount table. The resolver requires the same owned Wine
prefix and a unique matching host file inode, rejects symlinks/ambiguity, and
rechecks this correspondence for every capture/publication. A stale FUSE bind
does not authorize writing to a newly mounted host file. No grant paths, document
copies, credentials, or extra save queues are persisted by the relay.

The DLL now keeps its STA timer after a failed initial settings request and
retries every five seconds. No document is processed until settings are valid;
original versus copy mode comes from the bridge response. Connection errors no
longer claim that a revision was retained before any snapshot reached Linux.

## Compatibility and reproduction

Only native **x64** Office is supported by this package. Early checks used Microsoft 365 Office16 on Soda `11.0-37-office-v7`. Later checks and their runtime versions are recorded in [verification.md](verification.md). The COM loader,
Office timer, save events and callback methods are tested against real applications,
not just an Office model mock. Actual mouse-click ribbon interaction is distinct
from directly invoking its registered callback methods.

The November 8–9 lifecycle checks recorded real Word and PowerPoint runs with isolated
network/mount namespaces and path activation, including native PowerPoint window
closure and natural process exit. Excel disconnected through COM in the current
copied profile; the untouched published 0.3.1 DLL also failed its control run.
This does not establish a regression or a fix, and Excel is not counted as a
successful real-application rerun for this patch. Daily Wayland interaction and
the cause of an earlier already-terminated process residue remain unverified.

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
# Add --sandbox to test real isolated net/mount namespaces, bind grants, native
# first-connection failure, systemd path activation, empty-editor idle and reopen.
# With explicit permission for an input file, --host Word --sandbox
# --word-document /private/path/input.docx tests only a temporary copy and
# checks its existing text and image bytes. No document fixture is packaged.
```

The integration runner copies the stopped prefix, removes external user-folder
links from the copy, creates a mock mount and isolated settings, and uses an
authenticated Xvfb with no TCP listener or Wayland connection. It verifies the
actual Office process environment before creating documents. It also requires
Office processes to exit naturally after verified zero documents and Quit,
before cleaning up Wine background services. An existing PID alone is not
reported as proof of an open window or document; installer diagnostics identify
unreleased Wine/Office processes, including possible exit residue, and ignore
zombies. Cleanup terminates
only verified test-prefix process groups, removes the temporary prefix/documents,
and checks that the original Wine registry bytes did not change.

Sources: [Word Flat OPC](https://learn.microsoft.com/en-us/office/vba/api/word.document.wordopenxml),
[Excel SaveCopyAs](https://learn.microsoft.com/en-us/office/vba/api/excel.workbook.savecopyas),
[PowerPoint SaveCopyAs](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentation.savecopyas).
