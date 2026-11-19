// Fault-injection tests of the real COM wrapper and add-in callbacks. No Office.
#define TWODRIVE_TEST_ACCESS
#include "../../wine/AddIn.cpp"

void require(bool value, const char *message) {
  if (!value) throw std::runtime_error(message);
}
struct FakeDispatch : IDispatch {
  ULONG refs = 1;
  long alerts = -1;
  int exports = 0, deferred = 0, nativeSaves = 0;
  HRESULT exportError = DISP_E_EXCEPTION;
  std::vector<std::wstring> names;
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **out) override {
    if (id != IID_IUnknown && id != IID_IDispatch) return E_NOINTERFACE;
    *out = this; AddRef(); return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++refs; }
  ULONG STDMETHODCALLTYPE Release() override { return --refs; }
  HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT *n) override { *n = 0; return S_OK; }
  HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo **) override { return E_NOTIMPL; }
  HRESULT STDMETHODCALLTYPE GetIDsOfNames(REFIID, LPOLESTR *items, UINT count, LCID, DISPID *ids) override {
    for (UINT i = 0; i < count; ++i) {
      if (!wcscmp(items[i], L"Permission")) return DISP_E_UNKNOWNNAME;
      names.emplace_back(items[i]); ids[i] = (DISPID)names.size();
    }
    return S_OK;
  }
  static HRESULT STDMETHODCALLTYPE fill(EXCEPINFO *info) {
    auto self = (FakeDispatch *)info->pvReserved;
    ++self->deferred;
    info->scode = (SCODE)0x800a1066;
    info->bstrSource = SysAllocString(L"Synthetic Word");
    info->bstrDescription = SysAllocString(L"Synthetic serializer failure 汉字");
    info->bstrHelpFile = SysAllocString(L"synthetic.chm");
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE Invoke(DISPID id, REFIID, LCID, WORD flags, DISPPARAMS *params,
                                   VARIANT *result, EXCEPINFO *info, UINT *) override {
    auto name = names.at(id - 1);
    if (name == L"WordOpenXML") {
      ++exports;
      if (exportError == DISP_E_EXCEPTION) {
        info->pvReserved = this;
        info->pfnDeferredFillIn = fill;
      }
      return exportError;
    }
    if (name == L"Save") { ++nativeSaves; return E_UNEXPECTED; }
    if (name == L"DisplayAlerts" && flags & DISPATCH_PROPERTYPUT) {
      alerts = V(*(V *)&params->rgvarg[0]).num(); return S_OK;
    }
    if (name == L"Path" || name == L"FullName" || name == L"Name") {
      result->vt = VT_BSTR;
      result->bstrVal = SysAllocString(name == L"Path" ? L"" : L"Synthetic.docx");
    } else {
      result->vt = VT_I4;
      result->lVal = name == L"ProtectionType" ? -1 : name == L"DisplayAlerts" ? alerts : 0;
    }
    return S_OK;
  }
};
struct AddInSmoke {
  static void run() {
    Record pacing;
    pacing.dirtySince = 1000; pacing.due = 181000;
    require(!pacing.timerCaptureDue(1500, true), "Captured during a burst of input");
    require(pacing.timerCaptureDue(1500, false), "Idle first edit was not captured");
    require(pacing.timerCaptureDue(31000, true), "Continuous input starved the recovery capture");
    pacing.captureGap = 20000; pacing.lastCaptureAttempt = 1000;
    require(!pacing.timerCaptureDue(6100, false), "Slow export was repeated after only five seconds");
    require(pacing.timerCaptureDue(21100, false), "Idle slow document remained deferred");
    pacing.due = 8000;
    require(pacing.timerCaptureDue(8000, true), "Publication deadline was ignored");
    pacing.lastCaptureAttempt = 7000;
    require(!pacing.timerCaptureDue(8000, true), "Due publication caused an immediate retry");

    pacing.quietSnapshot = true; pacing.captureInput = 42; pacing.due = 180000;
    require(!pacing.timerCaptureDue(50000, false, 42), "Idle PowerPoint repeated an unchanged capture");
    require(pacing.timerCaptureDue(50000, false, 43), "New input failed to wake capture");
    require(pacing.timerCaptureDue(67000, false, 42), "Silent COM edits starved");
    for (int i = 0; i < 12; ++i) {
      auto now = pacing.retryAfter;
      pacing.deferRetry(now);
      require(pacing.retryAfter >= now + 5000 && pacing.retryAfter <= now + 60000,
              "Automatic errors retried without bounded backoff");
    }
    require(pacing.retryDelay == 60000, "Repeated errors did not back off");
    FakeDispatch application, document;
    D doc(&document);
    try { doc.str(L"WordOpenXML"); require(false, "Missing COM failure"); }
    catch (const ComError &error) {
      require(error.hresult == DISP_E_EXCEPTION && error.scode == (SCODE)0x800a1066,
              "Lost HRESULT or Office error code");
      require(std::string(error.what()).find("Synthetic serializer failure 汉字") != std::string::npos,
              "Lost deferred Unicode exception description");
      require(document.deferred == 1, "Deferred exception was not filled exactly once");
    }
    document.exportError = E_FAIL;
    try { doc.str(L"WordOpenXML"); require(false, "Missing generic COM failure"); }
    catch (const ComError &error) {
      require(std::string(error.what()) == "COM WordOpenXML error 80004005", "Generic COM failure changed");
    }
    document.exportError = DISP_E_EXCEPTION;
    AddIn addin;
    addin.busy = true;
    addin.closing["test"] = doc;
    addin.finishClose();
    MSG queued;
    require(!PeekMessageW(&queued, nullptr, WM_APP + 1, WM_APP + 1, PM_NOREMOVE) &&
            addin.closing.count("test"), "Busy close reposted itself or discarded the request");
    addin.closing.clear();
    addin.busy = false;
    addin.app = D(&application); addin.host = L"Word"; addin.settingsReady = true;
    auto &record = addin.records[doc.key()];
    record.identity = doc; record.session = "synthetic-session";
    record.lastHeartbeat = GetTickCount64(); record.due = GetTickCount64() + 180000;
    record.revision = 7; record.lastSnapshot = "earlier-durable-revision";
    struct KeepOffline { Record &record; ~KeepOffline() { record.session.clear(); } } offline{record};
    auto before = document.exports;
    try { addin.process(doc, true); require(false, "Serializer exception was swallowed"); }
    catch (const WordSnapshotError &) {}
    require(record.paused && document.exports == before + 1, "Failed serializer did not pause autosave");
    require(application.alerts == -1, "Word alerts were not restored after failure");
    require(record.revision == 7 && record.lastSnapshot == "earlier-durable-revision",
            "Export failure changed earlier durable revision");
    addin.process(doc);
    require(document.exports == before + 1, "Paused serializer kept retrying");
    require(document.nativeSaves == 0 && doc.num(L"Saved") == 0, "Export failure saved or acknowledged document");

    METHODDATA method = {};
    method.szName = const_cast<wchar_t *>(L"DocumentBeforeClose");
    method.dispid = 1; method.cc = CC_STDCALL; method.wFlags = DISPATCH_METHOD; method.vtReturn = VT_EMPTY;
    INTERFACEDATA description{&method, 1};
    ITypeInfo *coclass = nullptr;
    check(CreateDispTypeInfo(&description, LOCALE_USER_DEFAULT, &coclass));
    HREFTYPE reference;
    check(coclass->GetRefTypeOfImplType(0, &reference));
    check(coclass->GetRefTypeInfo(reference, &addin.eventType));
    coclass->Release();
    BSTR eventName = nullptr;
    check(addin.eventType->GetDocumentation(1, &eventName, nullptr, nullptr, nullptr));
    require(eventName && std::wstring(eventName) == L"DocumentBeforeClose", "Synthetic event type has the wrong name");
    SysFreeString(eventName);
    VARIANT_BOOL cancelled = VARIANT_FALSE;
    V args[2]; args[0].vt = VT_BOOL | VT_BYREF; args[0].pboolVal = &cancelled; args[1] = V(doc.p);
    DISPPARAMS params{args, nullptr, 2, 0};
    // First serializer failure can happen inside BeforeClose itself.
    record.paused = false; record.lastCaptureAttempt = 0;
    addin.event(1, &params);
    require(cancelled == VARIANT_FALSE && record.paused, "Serializer failure trapped the user's close request");
    require(addin.status.find("800a1066") != std::string::npos, "Close lost detailed failure status");
    before = document.exports;
    cancelled = VARIANT_TRUE; // Never override another add-in/user's cancellation.
    addin.event(1, &params);
    require(cancelled == VARIANT_TRUE && document.exports == before, "Paused close modified native cancellation");

    record.paused = false; record.lastCaptureAttempt = 0; document.exportError = RPC_E_CALL_REJECTED;
    try { addin.process(doc, true); require(false, "Missing transient busy failure"); }
    catch (const ComError &) {}
    require(!record.paused, "Transient busy response permanently paused autosave");
    before = document.exports;
    addin.process(doc);
    require(document.exports == before, "Failed capture was retried before five seconds");
    puts("PASS COM exception details, paused export, retained revision, native close and retry pacing");
  }
};
int main() {
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  try { AddInSmoke::run(); CoUninitialize(); return 0; }
  catch (const std::exception &error) { fprintf(stderr, "FAIL %s\n", error.what()); CoUninitialize(); return 1; }
}
