"""UNO extension entrypoint. Model operations run only on LibreOffice's UI thread."""
import sys
import os
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uno
import unohelper
from com.sun.star.awt import XCallback
from com.sun.star.document import XDocumentEventListener
from com.sun.star.frame import XDispatch, XDispatchProvider, XStatusListener, FeatureStateEvent
from com.sun.star.lang import XInitialization, XServiceInfo
from com.sun.star.task import XJob, XJobExecutor, XInteractionHandler
from com.sun.star.util import XModifyListener

from twodrive_office.policy import Settings, SaveError, check_source, fingerprint, plan_save, prepare_target
from twodrive_office.scheduler import PendingSave

IMPLEMENTATION = "org.twodrive.office.AutoSave"
PROTOCOL = "twodrive:"
_manager = None


def prop(name, value):
    result = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    result.Name, result.Value = name, value
    return result


class AbortInteraction(unohelper.Base, XInteractionHandler):
    def handle(self, request):
        # Never display password, overwrite, format or network dialogs on a timer.
        for continuation in request.getContinuations():
            abort = continuation.queryInterface(uno.getTypeByName("com.sun.star.task.XInteractionAbort"))
            if abort:
                abort.select()
                return


class DocumentState(unohelper.Base, XModifyListener):
    def __init__(self, manager, document):
        self.manager, self.document = manager, document
        self.document_id = str(uuid.uuid4())
        self.enabled = self.saving = self.closed = False
        self.plan = self.expected = None
        self.error = ""
        self.status = "自动保存：关"
        self.pending = PendingSave(manager.settings.debounce_seconds, manager.settings.max_wait_seconds)
        document.addModifyListener(self)

    def modified(self, event):
        if self.enabled and not self.saving and not self.closed:
            self.pending.changed(time.monotonic())

    def disposing(self, event):
        self.closed = True
        self.enabled = False
        self.manager.states = [state for state in self.manager.states if state is not self]

    def source(self):
        url = self.document.getURL()
        if not url:
            return None
        if not url.startswith("file:"):
            raise SaveError("此文档不是本地文件，请先保存为本地文件")
        return Path(uno.fileUrlToSystemPath(url))

    def format(self):
        source = self.source()
        if source:
            return source.suffix.lstrip(".").lower()
        if self.document.supportsService("com.sun.star.text.TextDocument"):
            return "odt"
        if self.document.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
            return "ods"
        if self.document.supportsService("com.sun.star.presentation.PresentationDocument"):
            return "odp"
        raise SaveError("目前支持 Writer、Calc、Impress")

    def enable(self, copy_directory=None):
        self.manager.settings = Settings.load()
        source = self.source()
        self.plan = plan_save(self.manager.settings, source, self.document.getTitle(),
                              self.document_id, self.format(), copy_directory)
        if self.plan.in_place and self.document.isReadonly():
            raise SaveError("当前文档只读，请以可编辑方式打开")
        # Existing encryption credentials cannot be retained in a standalone exported copy.
        if any(p.Name in ("Password", "EncryptionData") and p.Value for p in self.document.getArgs()):
            raise SaveError("加密文档暂不支持自动保存，请手动保存")
        self.expected = fingerprint(self.plan.target)
        self.enabled_source = source
        self.pending = PendingSave(self.manager.settings.debounce_seconds, self.manager.settings.max_wait_seconds)
        self.enabled, self.error = True, ""
        self.pending.changed(time.monotonic())
        self.status = "自动保存：开，待保存"

    def save(self):
        if not self.enabled or self.closed or self.saving:
            return
        now, revision = time.monotonic(), self.pending.revision
        self.saving = True
        try:
            # A native Save As changes identity. Stop instead of saving to the old file.
            current_source = self.source()
            if current_source != self.enabled_source:
                self.enabled = False
                raise SaveError("文档已另存为新文件，请重新开启自动保存")
            check_source(current_source)
            prepare_target(self.manager.settings, self.plan)
            if fingerprint(self.plan.target) != self.expected:
                # A normal Ctrl+S is legitimate; OnSaveDone refreshes expected below.
                raise SaveError("目标文件被其他程序修改，请检查后重新开启自动保存")
            common = (prop("InteractionHandler", AbortInteraction()),)
            if self.plan.in_place:
                self.document.storeSelf(common)
            else:
                filters = {"odt": "writer8", "ods": "calc8", "odp": "impress8",
                           "docx": "Office Open XML Text", "xlsx": "Calc MS Excel 2007 XML",
                           "pptx": "Impress MS PowerPoint 2007 XML"}
                args = common + (prop("Overwrite", True), prop("FilterName", filters[self.plan.extension]))
                was_modified = self.document.isModified()
                try:
                    self.document.storeToURL(uno.systemPathToFileUrl(str(self.plan.target)), args)
                finally:
                    # Preserve the original's state even if an export filter changes it.
                    if self.document.isModified() != was_modified:
                        self.document.setModified(was_modified)
            self.expected = fingerprint(self.plan.target)
            self.pending.succeeded(revision, now)
            self.status, self.error = "已本地保存，云端由 TwoDrive 同步", ""
        except Exception as exc:
            if os.environ.get("TWODRIVE_OFFICE_DEBUG") == "1":
                import traceback
                traceback.print_exc()
            self.error = str(exc) if isinstance(exc, SaveError) else "保存失败，修改仍待保存；请检查挂载、权限和文件格式"
            self.status = self.error
            self.pending.failed(now)
        finally:
            self.saving = False
            self.manager.refresh()


class Manager(unohelper.Base, XCallback, XDocumentEventListener):
    def __init__(self, context):
        self.context = context
        self.settings = Settings.load()
        services = context.ServiceManager
        self.desktop = services.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        self.callback = services.createInstanceWithContext("com.sun.star.awt.AsyncCallback", context)
        self.broadcaster = services.createInstanceWithContext("com.sun.star.frame.GlobalEventBroadcaster", context)
        self.broadcaster.addDocumentEventListener(self)
        self.states, self.dispatchers = [], []
        self.queued, self.running = False, True
        components = self.desktop.getComponents().createEnumeration()
        while components.hasMoreElements():
            self.track(components.nextElement())
        self.thread = threading.Thread(target=self.timer, name="TwoDrive-AutoSave", daemon=True)
        self.thread.start()

    def track(self, document):
        try:
            supported = any(document.supportsService(s) for s in (
                "com.sun.star.text.TextDocument", "com.sun.star.sheet.SpreadsheetDocument",
                "com.sun.star.presentation.PresentationDocument"))
            if supported and not any(s.document == document for s in self.states):
                self.states.append(DocumentState(self, document))
        except Exception:
            pass

    def state_for(self, document):
        self.track(document)
        return next((s for s in self.states if s.document == document), None)

    def current(self, frame=None):
        controller = frame.getController() if frame else None
        document = controller.getModel() if controller else self.desktop.getCurrentComponent()
        return self.state_for(document) if document else None

    def timer(self):
        while self.running:
            time.sleep(0.25)
            if not self.queued and any(s.enabled for s in self.states):
                self.queued = True
                try:
                    self.callback.addCallback(self, None)
                except Exception:
                    self.running = False

    def notify(self, data):
        self.queued = False
        now = time.monotonic()
        for state in list(self.states):
            if state.enabled and not state.closed and state.pending.due(now):
                state.save()

    def documentEventOccured(self, event):
        if event.EventName in ("OnLoad", "OnNew", "OnViewCreated"):
            self.track(event.Source)
        elif event.EventName == "OnPrepareUnload":
            state = next((s for s in self.states if s.document == event.Source), None)
            if state and state.enabled and state.pending.revision > state.pending.saved_revision:
                state.save()
        elif event.EventName == "OnSaveDone":
            state = next((s for s in self.states if s.document == event.Source), None)
            if state and state.enabled and state.plan.in_place and not state.saving:
                state.expected = fingerprint(state.plan.target)
                state.pending.succeeded(state.pending.revision, time.monotonic())
                state.status, state.error = "已本地保存，云端由 TwoDrive 同步", ""
        self.refresh()

    def disposing(self, event):
        self.running = False

    def refresh(self):
        for dispatcher in list(self.dispatchers):
            dispatcher.update_status()

    def choose_directory(self, initial):
        self.settings.check_mount()
        initial.mkdir(parents=True, exist_ok=True)
        picker = self.context.ServiceManager.createInstanceWithContext("com.sun.star.ui.dialogs.FolderPicker", self.context)
        picker.setTitle("选择 TwoDrive 中的自动保存副本目录")
        picker.setDisplayDirectory(uno.systemPathToFileUrl(str(initial)))
        return Path(uno.fileUrlToSystemPath(picker.getDirectory())) if picker.execute() else None

    def message(self, state, message):
        # Explicit user actions only. Automatic saves report through status, never dialogs.
        try:
            window = state.document.getCurrentController().getFrame().getContainerWindow()
            box_type = uno.Enum("com.sun.star.awt.MessageBoxType", "INFOBOX")
            window.getToolkit().createMessageBox(window, box_type, 1, "TwoDrive 自动保存", message).execute()
        except Exception:
            pass


def manager(context):
    global _manager
    if _manager is None:
        _manager = Manager(context)
    return _manager


class AutoSave(unohelper.Base, XJob, XJobExecutor, XDispatchProvider, XDispatch, XInitialization, XServiceInfo):
    def __init__(self, context):
        self.context, self.frame = context, None
        self.listeners = []

    def initialize(self, arguments):
        if arguments:
            self.frame = arguments[0]

    def execute(self, arguments):
        manager(self.context)
        return None

    def trigger(self, event):
        manager(self.context)

    def queryDispatch(self, url, target, flags):
        if url.Protocol == PROTOCOL:
            instance = manager(self.context)
            if self not in instance.dispatchers:
                instance.dispatchers.append(self)
            return self
        return None

    def queryDispatches(self, descriptors):
        return tuple(self.queryDispatch(d.FeatureURL, d.FrameName, d.SearchFlags) for d in descriptors)

    def dispatch(self, url, arguments):
        instance = manager(self.context)
        state = instance.current(self.frame)
        if state is None:
            return
        try:
            action = url.Path
            if action == "Toggle":
                if state.enabled:
                    state.enabled, state.status = False, "自动保存：关"
                else:
                    source = state.source()
                    plan = plan_save(instance.settings, source, state.document.getTitle(), state.document_id, state.format())
                    directory = None
                    if not plan.in_place:
                        directory = instance.choose_directory(instance.settings.copy_directory)
                        if directory is None:
                            return
                    state.enable(directory)
                    state.enabled_source = source
            elif action == "ChooseDestination":
                directory = instance.choose_directory(instance.settings.copy_directory)
                if directory:
                    state.enable(directory)
                    state.enabled_source = state.source()
            elif action == "SaveNow":
                if state.enabled:
                    state.pending.changed(time.monotonic())
                    state.save()
            elif action == "Status":
                target = str(state.plan.target) if state.plan else "尚未开启"
                instance.message(state, state.status + "\n\n保存位置：" + target)
        except Exception as exc:
            instance.message(state, str(exc) if isinstance(exc, SaveError) else "操作失败，请检查 TwoDrive 设置")
        instance.refresh()

    def addStatusListener(self, listener, url):
        self.listeners.append((listener, url))
        self.update_status()

    def removeStatusListener(self, listener, url):
        self.listeners = [(l, u) for l, u in self.listeners if l != listener or u.Complete != url.Complete]

    def update_status(self):
        try:
            state = manager(self.context).current(self.frame)
        except Exception:
            state = None
        for listener, url in list(self.listeners):
            event = FeatureStateEvent()
            event.Source, event.FeatureURL = self, url
            event.IsEnabled = state is not None
            event.Requery = False
            event.FeatureDescriptor = state.status if state else "TwoDrive 自动保存"
            event.State = bool(state and state.enabled) if url.Path == "Toggle" else event.FeatureDescriptor
            try:
                listener.statusChanged(event)
            except Exception:
                self.listeners.remove((listener, url))

    def getImplementationName(self):
        return IMPLEMENTATION

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getSupportedServiceNames(self):
        return (IMPLEMENTATION, "com.sun.star.frame.ProtocolHandler")


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(AutoSave, IMPLEMENTATION, (IMPLEMENTATION, "com.sun.star.frame.ProtocolHandler"))
