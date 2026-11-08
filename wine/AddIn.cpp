#include "com.hpp"
#include <cstdio>
#include <map>
#include <memory>
#include <set>
#include <winhttp.h>

// Native Wine COM entry point. No CLR, VSTO, Office PIA or Linux polling
// daemon.
const CLSID CLSID_AddIn = {0xc53a8f6a,
                           0xd977,
                           0x4d1f,
                           {0xa8, 0x32, 0x6c, 0x40, 0x20, 0x3d, 0x64, 0x19}};
const IID IID_Ext = {
    0xb65ad801, 0xabaf, 0x11d0, {0xbb, 0x8b, 0, 0xa0, 0xc9, 0x0f, 0x27, 0x44}};
const IID IID_Ribbon = {0x000c0396, 0, 0, {0xc0, 0, 0, 0, 0, 0, 0, 0x46}};
struct Ext : IDispatch {
  virtual HRESULT STDMETHODCALLTYPE OnConnection(IDispatch *, int, IDispatch *,
                                                 SAFEARRAY **) = 0;
  virtual HRESULT STDMETHODCALLTYPE OnDisconnection(int, SAFEARRAY **) = 0;
  virtual HRESULT STDMETHODCALLTYPE OnAddInsUpdate(SAFEARRAY **) = 0;
  virtual HRESULT STDMETHODCALLTYPE OnStartupComplete(SAFEARRAY **) = 0;
  virtual HRESULT STDMETHODCALLTYPE OnBeginShutdown(SAFEARRAY **) = 0;
};
struct Ribbon : IDispatch {
  virtual HRESULT STDMETHODCALLTYPE GetCustomUI(BSTR, BSTR *) = 0;
};
HMODULE moduleHandle;
LONG objects = 0;
struct Internet {
  HINTERNET h;
  explicit Internet(HINTERNET h) : h(h) {
    if (!h)
      throw std::runtime_error("Bridge connection failed");
  }
  ~Internet() { WinHttpCloseHandle(h); }
};
struct Http {
  int port = 0;
  std::string token;
  std::string request(const wchar_t *verb, const std::string &path,
                      const std::string &body = {},
                      const std::string &headers = {}) {
    Internet session(WinHttpOpen(L"TwoDrive-Wine/0.3.1",
                                 WINHTTP_ACCESS_TYPE_NO_PROXY, nullptr, nullptr,
                                 0));
    WinHttpSetTimeouts(session.h, 2000, 2000, 5000, 5000);
    Internet connection(
        WinHttpConnect(session.h, L"127.0.0.1", (INTERNET_PORT)port, 0));
    Internet req(WinHttpOpenRequest(connection.h, verb, wide(path).c_str(),
                                    nullptr, WINHTTP_NO_REFERER,
                                    WINHTTP_DEFAULT_ACCEPT_TYPES, 0));
    DWORD policy = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
    WinHttpSetOption(req.h, WINHTTP_OPTION_REDIRECT_POLICY, &policy,
                     sizeof(policy));
    std::wstring hs =
        wide("Authorization: Bearer " + token +
             "\r\nContent-Type: application/octet-stream\r\n" + headers);
    if (!WinHttpSendRequest(req.h, hs.c_str(), (DWORD)-1L,
                            body.empty() ? nullptr : (void *)body.data(),
                            (DWORD)body.size(), (DWORD)body.size(), 0) ||
        !WinHttpReceiveResponse(req.h, nullptr))
      throw std::runtime_error(
          "Bridge request failed; pending revision retained");
    DWORD status = 0, n = sizeof(status);
    if (!WinHttpQueryHeaders(
            req.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            nullptr, &status, &n, nullptr))
      throw std::runtime_error("Invalid HTTP reply");
    std::string result;
    char buf[4096];
    DWORD got = 0;
    while (WinHttpReadData(req.h, buf, sizeof(buf), &got) && got) {
      result.append(buf, got);
      if (result.size() > 65536)
        throw std::runtime_error("Bridge response too large");
    }
    if (status != 200) {
      try {
        throw std::runtime_error(field(result, "error"));
      } catch (const std::runtime_error &e) {
        throw std::runtime_error(e.what());
      }
    }
    return result;
  }
};
struct Record {
  D identity;
  std::wstring autoName;
  std::string session, source;
  bool paused = false, inPlace = false, nativeObserved = false;
  unsigned long long revision = 0;
  ULONGLONG due = 0, lastStage = 0, lastHeartbeat = 0, lastAutoCheck = 0;
  std::string lastSnapshot;
};
class AddIn;
class EventSink : public IDispatch {
  LONG refs = 1;
  AddIn *owner;
  IID sourceIID;

public:
  explicit EventSink(AddIn *o, const IID &source)
      : owner(o), sourceIID(source) {}
  virtual ~EventSink() = default;
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **p) override {
    if (!p)
      return E_POINTER;
    *p = nullptr;
    if (id != IID_IUnknown && id != IID_IDispatch && id != sourceIID)
      return E_NOINTERFACE;
    *p = (IDispatch *)this;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override {
    return InterlockedIncrement(&refs);
  }
  ULONG STDMETHODCALLTYPE Release() override {
    auto n = InterlockedDecrement(&refs);
    if (!n)
      delete this;
    return n;
  }
  HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT *n) override {
    *n = 0;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo **) override {
    return E_NOTIMPL;
  }
  HRESULT STDMETHODCALLTYPE GetIDsOfNames(REFIID, LPOLESTR *, UINT, LCID,
                                          DISPID *) override {
    return DISP_E_UNKNOWNNAME;
  }
  HRESULT STDMETHODCALLTYPE Invoke(DISPID, REFIID, LCID, WORD, DISPPARAMS *,
                                   VARIANT *, EXCEPINFO *, UINT *) override;
};
class AddIn : public Ext, public Ribbon {
  LONG refs = 1;
  D app, ribbon;
  HWND window = nullptr;
  bool busy = false;
  std::wstring host;
  std::string root, prefix, status = "等待打开 TwoDrive 文档";
  Http http;
  std::map<std::string, Record> records;
  std::map<std::string, D> closing;
  std::wstring directory;
  ULONGLONG interval = 180000;
  IConnectionPoint *point = nullptr;
  DWORD cookie = 0;
  ITypeInfo *eventType = nullptr;
  static LRESULT CALLBACK proc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    auto self = (AddIn *)GetWindowLongPtrW(h, GWLP_USERDATA);
    if (msg == WM_NCCREATE) {
      self = (AddIn *)((CREATESTRUCTW *)lp)->lpCreateParams;
      SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)self);
    }
    if (msg == WM_TIMER && self)
      self->poll();
    if (msg == WM_APP + 1 && self)
      self->finishClose();
    return DefWindowProcW(h, msg, wp, lp);
  }
  std::string source(const D &doc) {
    auto path = doc.str(L"Path");
    if (path.empty())
      return {};
    return field(http.request(L"POST", "/wine-path",
                              "{\"prefix\":" + quote(prefix) + ",\"source\":" +
                                  quote(utf8(doc.str(L"FullName"))) + "}"),
                 "path");
  }
  std::string extension() const {
    return host == L"Word" ? "docx" : host == L"Excel" ? "xlsx" : "pptx";
  }
  std::string documentKey(const D &doc) {
    auto identity = doc.key();
    if (records.count(identity))
      return identity;
    // Some Wine PowerPoint dispatch wrappers have transient IUnknown addresses.
    // Keep a live reference and match its current path/name; this also keeps a
    // Save As attached to its original session, where source checks stop it.
    for (auto &pair : records) {
      try {
        if (pair.second.identity.p &&
            pair.second.identity.str(L"FullName") == doc.str(L"FullName") &&
            pair.second.identity.str(L"Path") == doc.str(L"Path"))
          return pair.first;
      } catch (const std::exception &) {
      }
    }
    return identity;
  }
  bool within(const std::string &s) const {
    return s == root ||
           (s.size() > root.size() && s.compare(0, root.size(), root) == 0 &&
            s[root.size()] == '/');
  }
  bool eligible(const D &doc) {
    if (doc.num(L"ReadOnly") || doc.num(L"HasVBProject"))
      return false;
    // This Wine Office installation does not expose Permission (MSIPC).
    // Reject any known IRM state; the Linux side also requires a plain,
    // unsigned OOXML source and validates the complete exported package.
    try {
      if (doc.obj(L"Permission").num(L"Enabled"))
        return false;
    } catch (const std::exception &) {
    }
    auto path = doc.str(L"Path");
    auto name = doc.str(L"FullName");
    auto suffix = L"." + wide(extension());
    if (!path.empty() &&
        (name.size() < suffix.size() ||
         _wcsicmp(name.c_str() + name.size() - suffix.size(), suffix.c_str())))
      return false;
    if (host == L"Word")
      return !doc.num(L"HasPassword") && doc.num(L"ProtectionType") == -1 &&
             app.num(L"BackgroundSavingStatus") == 0;
    if (host == L"Excel")
      return !doc.num(L"HasPassword") && !doc.num(L"MultiUserEditing") &&
             app.num(L"Ready") && app.num(L"CalculationState") == 0 &&
             (path.empty() || doc.num(L"FileFormat") == 51);
    return app.obj(L"SlideShowWindows").num(L"Count") == 0;
  }
  struct Alerts {
    D app;
    V old;
    Alerts(const D &a, const std::wstring &h)
        : app(a), old(a.call(L"DisplayAlerts")) {
      app.put(L"DisplayAlerts",
              h == L"Excel" ? V(false) : V(h == L"Word" ? 0L : 1L));
    }
    ~Alerts() {
      try {
        app.put(L"DisplayAlerts", old);
      } catch (...) {
      }
    }
  };
  std::string snapshot(const D &doc) {
    if (host == L"Word")
      return utf8(doc.str(L"WordOpenXML"));
    wchar_t temp[MAX_PATH];
    if (!GetTempFileNameW(directory.c_str(), L"tdo", 0, temp))
      throw std::runtime_error("Cannot allocate private export");
    DeleteFileW(temp);
    std::wstring path = std::wstring(temp) + L"." + wide(extension());
    try {
      Alerts alerts(app, host);
      if (host == L"Excel")
        doc.method(L"SaveCopyAs", {V(path)});
      else
        doc.method(L"SaveCopyAs", {V(path), V(24L)});
      auto result = read_file(path);
      DeleteFileW(path.c_str());
      return result;
    } catch (...) {
      DeleteFileW(path.c_str());
      throw;
    }
  }
  void close(Record &record) {
    if (record.session.empty())
      return;
    try {
      http.request(L"DELETE", "/sessions/" + record.session);
      record.session.clear();
    } catch (...) { /* Server retains durable pending data. */
    }
  }
  void stage(const D &doc, Record &record) {
    if (doc.num(L"Saved") != 0)
      return;
    if (!eligible(doc))
      throw std::runtime_error("文档只读、受保护或格式不受支持");
    std::string current = source(doc);
    if (current != record.source)
      throw std::runtime_error("源文档位置已改变，请暂停后重新开启");
    auto content = snapshot(doc);
    auto checksum = digest(content);
    if (checksum == record.lastSnapshot &&
        GetTickCount64() - record.lastStage < 10000)
      return;
    auto headers =
        "X-TwoDrive-Source: " + hex(current) +
        "\r\nX-TwoDrive-Revision: " + std::to_string(++record.revision) +
        "\r\nX-TwoDrive-Encoding: " + (host == L"Word" ? "flatopc" : "ooxml") +
        "\r\n";
    http.request(L"POST", "/sessions/" + record.session + "/office", content,
                 headers);
    record.lastSnapshot = checksum;
    record.lastStage = GetTickCount64();
    status = "已本地保存，待提交 TwoDrive";
  }
  void publish(const D &doc, Record &record) {
    if (source(doc) != record.source)
      throw std::runtime_error("源文档位置已改变，请重新开启");
    if (record.inPlace) {
      if (!eligible(doc))
        throw std::runtime_error("Office 暂时无法安全保存");
      auto approval = http.request(L"POST", "/sessions/" + record.session +
                                                "/native-prepare");
      {
        Alerts alerts(app, host);
        doc.method(L"Save");
      }
      if (doc.num(L"Saved") == 0)
        throw std::runtime_error("Office 尚有未保存修改，本地版本已保留");
      record.nativeObserved = true;
      http.request(L"POST", "/sessions/" + record.session + "/native-complete",
                   read_file(doc.str(L"FullName")),
                   "X-TwoDrive-Native-Token: " +
                       field(approval, "native_token") + "\r\n");
      record.nativeObserved = false;
    } else
      http.request(L"POST", "/sessions/" + record.session + "/flush");
    record.lastSnapshot.clear();
    record.due = GetTickCount64() + interval;
    status = "已保存至 TwoDrive，上传由 TwoDrive 管理";
  }
  D active() {
    try {
      return app.obj(host == L"Word"    ? L"ActiveDocument"
                     : host == L"Excel" ? L"ActiveWorkbook"
                                        : L"ActivePresentation");
    } catch (const std::exception &) {
      // PowerPoint automation may have one presentation without any window.
      D docs = app.obj(host == L"Word"    ? L"Documents"
                       : host == L"Excel" ? L"Workbooks"
                                          : L"Presentations");
      if (docs.num(L"Count") != 1)
        throw;
      return D(
          docs.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
    }
  }
  void process(const D &doc, bool force = false, bool manual = false) {
    auto key = documentKey(doc);
    auto &rec = records[key];
    rec.identity = doc;
    if (rec.paused && !manual)
      return;
    if (rec.session.empty()) {
      auto name = doc.str(L"FullName");
      if (!manual && name == rec.autoName && rec.lastAutoCheck &&
          GetTickCount64() - rec.lastAutoCheck < 10000)
        return;
      rec.autoName = name;
      rec.lastAutoCheck = GetTickCount64();
      rec.source = source(doc);
      if (!manual && !within(rec.source))
        return;
      if (!eligible(doc))
        return;
      auto payload = "{\"source_path\":" + quote(rec.source) +
                     ",\"document_id\":" +
                     quote(key + "-" + std::to_string(GetTickCount64())) +
                     ",\"title\":" + quote(utf8(doc.str(L"Name"))) +
                     ",\"extension\":" + quote(extension()) +
                     ",\"confirm_source\":true,\"native_publish\":true}";
      auto result = http.request(L"POST", "/sessions", payload);
      rec.session = field(result, "session_id");
      rec.lastSnapshot.clear();
      rec.lastStage = 0;
      rec.inPlace = within(rec.source);
      rec.due = GetTickCount64() + interval;
      rec.paused = false;
    }
    if (GetTickCount64() - rec.lastHeartbeat >= 10000) {
      auto response = http.request(L"GET", "/sessions/" + rec.session);
      rec.lastHeartbeat = GetTickCount64();
      auto error = field(response, "sync_error");
      if (!error.empty())
        status = error;
    }
    if (rec.nativeObserved && doc.num(L"Saved") != 0 && rec.inPlace) {
      if (source(doc) != rec.source)
        throw std::runtime_error("另存为后请重新开启自动保存");
      auto approval = http.request(L"POST", "/sessions/" + rec.session +
                                                "/native-explicit");
      http.request(L"POST", "/sessions/" + rec.session + "/native-complete",
                   read_file(doc.str(L"FullName")),
                   "X-TwoDrive-Native-Token: " +
                       field(approval, "native_token") + "\r\n");
      rec.lastSnapshot.clear();
      rec.lastStage = 0;
      rec.nativeObserved = false;
      rec.due = GetTickCount64() + interval;
      status = "Office 手动保存已确认";
    }
    if (doc.num(L"Saved") == 0 &&
        (force || !rec.lastStage || GetTickCount64() - rec.lastStage >= 5000))
      stage(doc, rec);
    if (force || GetTickCount64() >= rec.due) {
      if (!rec.lastSnapshot.empty())
        publish(doc, rec);
      else
        rec.due = GetTickCount64() + interval;
    }
  }
  void connectEvents() {
    IConnectionPointContainer *container = nullptr;
    check(app.p->QueryInterface(IID_IConnectionPointContainer,
                                (void **)&container));
    IID eventIID;
    check(IIDFromString(
        host == L"Word"    ? L"{00020A01-0000-0000-C000-000000000046}"
        : host == L"Excel" ? L"{00024413-0000-0000-C000-000000000046}"
                           : L"{914934C2-5A91-11CF-8700-00AA0060263B}",
        &eventIID));
    HRESULT hr = container->FindConnectionPoint(eventIID, &point);
    container->Release();
    check(hr);
    ITypeInfo *ti = nullptr;
    ITypeLib *lib = nullptr;
    UINT index = 0;
    check(app.p->GetTypeInfo(0, LOCALE_USER_DEFAULT, &ti));
    hr = ti->GetContainingTypeLib(&lib, &index);
    ti->Release();
    check(hr);
    hr = lib->GetTypeInfoOfGuid(eventIID, &eventType);
    lib->Release();
    check(hr);
    auto sink = new EventSink(this, eventIID);
    hr = point->Advise(sink, &cookie);
    sink->Release();
    check(hr);
  }

public:
  AddIn() { InterlockedIncrement(&objects); }
  virtual ~AddIn() {
    stop();
    InterlockedDecrement(&objects);
  }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **p) override {
    if (!p)
      return E_POINTER;
    *p = nullptr;
    if (id == IID_IUnknown || id == IID_IDispatch || id == IID_Ext)
      *p = (Ext *)this;
    else if (id == IID_Ribbon)
      *p = (Ribbon *)this;
    else
      return E_NOINTERFACE;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override {
    return InterlockedIncrement(&refs);
  }
  ULONG STDMETHODCALLTYPE Release() override {
    auto n = InterlockedDecrement(&refs);
    if (!n)
      delete this;
    return n;
  }
  HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT *n) override {
    *n = 0;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo **) override {
    return E_NOTIMPL;
  }
  HRESULT STDMETHODCALLTYPE GetIDsOfNames(REFIID, LPOLESTR *names, UINT count,
                                          LCID, DISPID *ids) override {
    const wchar_t *callbacks[] = {L"Toggle", L"Pressed",      L"SaveNow",
                                  L"Status", L"RibbonLoaded", L"Tick",
                                  L"Version"};
    for (UINT j = 0; j < count; j++) {
      ids[j] = DISPID_UNKNOWN;
      for (int i = 0; i < 7; i++)
        if (!_wcsicmp(names[j], callbacks[i]))
          ids[j] = 10 + i;
      if (ids[j] == DISPID_UNKNOWN)
        return DISP_E_UNKNOWNNAME;
    }
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE Invoke(DISPID id, REFIID, LCID, WORD,
                                   DISPPARAMS *args, VARIANT *result,
                                   EXCEPINFO *, UINT *) override {
    if (result)
      VariantInit(result);
    try {
      if (id == 13 || id == 16) {
        if (result) {
          result->vt = VT_BSTR;
          result->bstrVal =
              SysAllocString(wide(id == 16 ? "0.3.1" : status).c_str());
        }
        return S_OK;
      }
      if (id == 14) {
        if (args->cArgs)
          ribbon = D(V(args->rgvarg[0].pdispVal));
        return S_OK;
      }
      if (id == 15) {
        poll();
        return S_OK;
      }
      if (id == 11) {
        bool enabled = false;
        try {
          auto it = records.find(documentKey(active()));
          enabled = it != records.end() && !it->second.paused &&
                    !it->second.session.empty();
        } catch (...) {
        }
        if (result) {
          result->vt = VT_BOOL;
          result->boolVal = enabled ? VARIANT_TRUE : VARIANT_FALSE;
        }
        return S_OK;
      }
      if (busy)
        return S_OK;
      busy = true;
      try {
        auto doc = active();
        auto &rec = records[documentKey(doc)];
        if (id == 10) {
          bool pressed = args->cArgs && args->rgvarg[0].vt == VT_BOOL &&
                         args->rgvarg[0].boolVal;
          if (pressed) {
            rec.paused = false;
            process(doc, false, true);
          } else {
            try {
              if (!rec.session.empty())
                process(doc, true);
            } catch (const std::exception &e) {
              status = e.what();
            }
            close(rec);
            rec.paused = true;
          }
        } else if (id == 12)
          process(doc, true, true);
      } catch (const std::exception &e) {
        status = e.what();
      }
      busy = false;
      if (ribbon.p)
        try {
          ribbon.method(L"Invalidate");
        } catch (...) {
        }
    } catch (const std::exception &e) {
      status = e.what();
      busy = false;
    }
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetCustomUI(BSTR, BSTR *xml) override {
    if (!xml)
      return E_POINTER;
    *xml = SysAllocString(
        L"<customUI "
        L"xmlns='http://schemas.microsoft.com/office/2009/07/customui' "
        L"onLoad='RibbonLoaded'><ribbon><tabs><tab id='TwoDriveWine' "
        L"label='TwoDrive'><group id='TwoDriveSave' "
        L"label='自动保存'><toggleButton id='TwoDriveEnabled' label='自动保存' "
        L"getPressed='Pressed' onAction='Toggle'/><button id='TwoDriveNow' "
        L"label='立即保存' onAction='SaveNow'/><labelControl "
        L"id='TwoDriveStatus' "
        L"getLabel='Status'/></group></tab></tabs></ribbon></customUI>");
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE OnConnection(IDispatch *application, int,
                                         IDispatch *instance,
                                         SAFEARRAY **) override {
    try {
      if (instance)
        D(instance).put(L"Object", V((IDispatch *)(Ext *)this));
      app = D(application);
      auto name = app.str(L"Name");
      host = name.find(L"Word") != std::wstring::npos    ? L"Word"
             : name.find(L"Excel") != std::wstring::npos ? L"Excel"
                                                         : L"PowerPoint";
      wchar_t filename[32768];
      GetModuleFileNameW(moduleHandle, filename, 32768);
      directory = filename;
      directory = directory.substr(0, directory.find_last_of(L"\\"));
      auto ini = directory + L"\\bridge.ini";
      wchar_t token[512];
      GetPrivateProfileStringW(L"bridge", L"token", L"", token, 512,
                               ini.c_str());
      http.token = utf8(token);
      wchar_t bottle[32768];
      GetPrivateProfileStringW(L"bridge", L"prefix", L"", bottle, 32768,
                               ini.c_str());
      prefix = utf8(bottle);
      http.port = GetPrivateProfileIntW(L"bridge", L"port", 0, ini.c_str());
      if (http.token.size() < 32 || http.port < 1024 || http.port > 65535)
        throw std::runtime_error("请安装 TwoDrive Wine bridge 配置");
      auto settings = http.request(L"GET", "/settings");
      root = field(settings, "mount_root");
      while (root.size() > 1 && root.back() == '/')
        root.pop_back();
      auto pos = settings.find("\"sync_interval_seconds\"");
      if (pos != std::string::npos) {
        auto n = std::stod(settings.substr(settings.find(':', pos) + 1));
        if (n >= 1 && n <= 3600)
          interval = (ULONGLONG)(n * 1000);
      }
      connectEvents();
      WNDCLASSW wc = {};
      wc.lpfnWndProc = proc;
      wc.hInstance = moduleHandle;
      wc.lpszClassName = L"TwoDriveOfficeWineTimer";
      RegisterClassW(&wc);
      window = CreateWindowW(wc.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE,
                             nullptr, moduleHandle, this);
      if (!window)
        throw std::runtime_error("Cannot create STA timer");
      SetTimer(window, 1, 1000, nullptr);
      if (instance)
        D(instance).put(L"Object", V((IDispatch *)(Ext *)this));
    } catch (const std::exception &e) {
      status = e.what();
    }
    return S_OK;
  }
  void poll() {
    if (busy || !app.p || !window)
      return;
    busy = true;
    try {
      D docs = app.obj(host == L"Word"    ? L"Documents"
                       : host == L"Excel" ? L"Workbooks"
                                          : L"Presentations");
      long count = docs.num(L"Count");
      std::set<std::string> open;
      bool complete = true;
      for (long i = 1; i <= count; i++) {
        try {
          D doc(docs.call(L"Item", DISPATCH_PROPERTYGET | DISPATCH_METHOD,
                          {V(i)}));
          open.insert(documentKey(doc));
          try {
            process(doc);
          } catch (const std::exception &e) {
            status = e.what();
          }
        } catch (const std::exception &e) {
          status = e.what();
          complete = false;
        }
      }
      if (complete) {
        for (auto it = records.begin(); it != records.end();)
          if (!open.count(it->first)) {
            close(it->second);
            it = records.erase(it);
          } else
            ++it;
      }
      if (ribbon.p)
        ribbon.method(L"Invalidate");
    } catch (const std::exception &e) {
      status = e.what();
    }
    busy = false;
  }
  void event(DISPID id, DISPPARAMS *args) {
    if (busy || !eventType)
      return;
    BSTR name = nullptr;
    if (FAILED(
            eventType->GetDocumentation(id, &name, nullptr, nullptr, nullptr)))
      return;
    std::wstring n = name ? name : L"";
    SysFreeString(name);
    bool beforeClose = n == L"DocumentBeforeClose" ||
                       n == L"WorkbookBeforeClose" ||
                       n == L"PresentationBeforeClose";
    bool userSave = n == L"DocumentBeforeSave" || n == L"WorkbookBeforeSave" ||
                    n == L"PresentationSave";
    if (!beforeClose && !userSave)
      return;
    busy = true;
    try {
      for (UINT i = 0; i < args->cArgs; i++)
        if (args->rgvarg[i].vt == VT_DISPATCH) {
          D doc(args->rgvarg[i].pdispVal);
          if (beforeClose) {
            auto it = records.find(documentKey(doc));
            if (it != records.end() && !it->second.paused &&
                !it->second.session.empty()) {
              bool cancelled = false;
              for (UINT j = 0; j < args->cArgs; ++j)
                if (args->rgvarg[j].vt == (VT_BOOL | VT_BYREF) &&
                    args->rgvarg[j].pboolVal) {
                  *args->rgvarg[j].pboolVal = VARIANT_TRUE;
                  cancelled = true;
                }
              if (cancelled) {
                closing[documentKey(doc)] = doc;
                PostMessageW(window, WM_APP + 1, 0, 0);
              } else
                process(doc, true);
            }
          } else {
            auto it = records.find(documentKey(doc));
            if (it != records.end() && !it->second.session.empty() &&
                !it->second.paused)
              it->second.nativeObserved = true;
          }
          break;
        }
    } catch (const std::exception &e) {
      status = e.what();
    }
    busy = false;
  }
  void finishClose() {
    if (busy) {
      PostMessageW(window, WM_APP + 1, 0, 0);
      return;
    }
    busy = true;
    auto pending = std::move(closing);
    closing.clear();
    for (auto &pair : pending) {
      try {
        // Excel refuses exports/saves while BeforeClose is on the stack. Its
        // cancelled close has now returned; run on the same Office STA thread.
        process(pair.second, true);
        pair.second.method(
            L"Close"); // busy prevents re-entering our close hook
      } catch (const std::exception &e) {
        status = e.what();
      }
    }
    busy = false;
  }
  void stop() {
    if (window) {
      KillTimer(window, 1);
      DestroyWindow(window);
      window = nullptr;
    }
    if (point) {
      if (cookie)
        point->Unadvise(cookie);
      point->Release();
      point = nullptr;
      cookie = 0;
    }
    if (eventType) {
      eventType->Release();
      eventType = nullptr;
    }
    for (auto &pair : records)
      close(pair.second);
    records.clear();
    closing.clear();
    ribbon = D();
    app = D();
  }
  HRESULT STDMETHODCALLTYPE OnDisconnection(int, SAFEARRAY **) override {
    stop();
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE OnBeginShutdown(SAFEARRAY **) override {
    stop();
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE OnAddInsUpdate(SAFEARRAY **) override {
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE OnStartupComplete(SAFEARRAY **) override {
    return S_OK;
  }
};
HRESULT EventSink::Invoke(DISPID id, REFIID, LCID, WORD, DISPPARAMS *args,
                          VARIANT *, EXCEPINFO *, UINT *) {
  owner->event(id, args);
  return S_OK;
}
class Factory : public IClassFactory {
  LONG refs = 1;

public:
  Factory() { InterlockedIncrement(&objects); }
  virtual ~Factory() { InterlockedDecrement(&objects); }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **p) override {
    if (!p)
      return E_POINTER;
    *p = nullptr;
    if (id != IID_IUnknown && id != IID_IClassFactory)
      return E_NOINTERFACE;
    *p = this;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override {
    return InterlockedIncrement(&refs);
  }
  ULONG STDMETHODCALLTYPE Release() override {
    auto n = InterlockedDecrement(&refs);
    if (!n)
      delete this;
    return n;
  }
  HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown *outer, REFIID id,
                                           void **p) override {
    if (outer)
      return CLASS_E_NOAGGREGATION;
    auto a = new AddIn;
    auto hr = a->QueryInterface(id, p);
    a->Release();
    return hr;
  }
  HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override {
    if (lock)
      InterlockedIncrement(&objects);
    else
      InterlockedDecrement(&objects);
    return S_OK;
  }
};
extern "C" __declspec(dllexport) HRESULT WINAPI DllGetClassObject(REFCLSID cls,
                                                                  REFIID id,
                                                                  void **p) {
  if (cls != CLSID_AddIn)
    return CLASS_E_CLASSNOTAVAILABLE;
  auto f = new Factory;
  auto hr = f->QueryInterface(id, p);
  f->Release();
  return hr;
}
extern "C" __declspec(dllexport) HRESULT WINAPI DllCanUnloadNow() {
  return objects == 0 ? S_OK : S_FALSE;
}
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    moduleHandle = h;
    DisableThreadLibraryCalls(h);
  }
  return TRUE;
}
