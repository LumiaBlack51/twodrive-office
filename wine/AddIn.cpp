#include "com.hpp"
#include <cstdio>
#include <algorithm>
#include <exception>
#include <map>
#include <memory>
#include <set>
#include <winhttp.h>
#include <commctrl.h>

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
struct SessionClosed : std::runtime_error {
  SessionClosed() : std::runtime_error("session_closed") {}
};
struct WordSnapshotError : std::runtime_error {
  explicit WordSnapshotError(const ComError &error)
      : std::runtime_error("Word 导出失败，自动保存已暂停；请用 Ctrl+S 保存。" +
                           std::string(error.what())) {}
};
struct Http {
  int port = 0;
  std::string token;
  std::wstring wakeDirectory;
  ULONGLONG nextWake = 0;
  void wake() {
    if (!wakeDirectory.empty() && GetTickCount64() >= nextWake) {
      nextWake = GetTickCount64() + 5000;
      // A directory-only trigger cannot truncate an existing user file or
      // follow a file symlink. The host path unit consumes this empty marker.
      CreateDirectoryW(wakeDirectory.c_str(), nullptr);
    }
  }
  std::string request(const wchar_t *verb, const std::string &path,
                      const std::string &body = {},
                      const std::string &headers = {}, HANDLE upload = INVALID_HANDLE_VALUE) {
    Internet session(WinHttpOpen(L"TwoDrive-Wine/0.3.2",
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
    DWORD total = upload == INVALID_HANDLE_VALUE ? static_cast<DWORD>(body.size()) : snapshot_size(upload);
    BOOL sent = WinHttpSendRequest(req.h, hs.c_str(), (DWORD)-1L,
                                   body.empty() ? nullptr : (void *)body.data(),
                                   static_cast<DWORD>(body.size()), total, 0);
    if (sent && upload != INVALID_HANDLE_VALUE) {
      if (!SetFilePointerEx(upload, {}, nullptr, FILE_BEGIN))
        throw std::runtime_error("Cannot rewind private export");
      read_blocks(upload, total, [&](const BYTE *data, DWORD size) {
        DWORD written = 0;
        if (!WinHttpWriteData(req.h, data, size, &written) || written != size)
          throw std::runtime_error("Incomplete private export transfer");
      });
    }
    if (!sent || !WinHttpReceiveResponse(req.h, nullptr)) {
      auto error = GetLastError();
      wake();
      throw std::runtime_error("保存服务连接失败（WinHTTP " +
                               std::to_string(error) +
                               "）；请检查容器保存端口连接");
    }
    DWORD status = 0, n = sizeof(status);
    if (!WinHttpQueryHeaders(
            req.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            nullptr, &status, &n, nullptr))
      throw std::runtime_error("Invalid HTTP reply");
    std::string result;
    char buf[4096];
    DWORD got = 0;
    while (true) {
      if (!WinHttpReadData(req.h, buf, sizeof(buf), &got))
        throw std::runtime_error("保存服务响应中断；尚未确认保存成功");
      if (!got)
        break;
      result.append(buf, got);
      if (result.size() > 65536)
        throw std::runtime_error("Bridge response too large");
    }
    if (status != 200) {
      if (status == 404 && field(result, "error") == "session_closed")
        throw SessionClosed();
      try {
        throw std::runtime_error(field(result, "error"));
      } catch (const std::runtime_error &e) {
        throw std::runtime_error(e.what());
      }
    }
    return result;
  }
};
// A worker owns exactly one immutable export. It never holds an Office object,
// Record reference or pointer to the add-in. The STA consumes its result before
// publishing, replacing the session, closing the document or unloading the DLL.
struct SnapshotTransfer {
  Http bridge;
  std::string path, headers, checksum, previous, nativeToken;
  bool native = false, unchanged = false;
  std::wstring wordXml;
  HANDLE file = INVALID_HANDLE_VALUE;
  BY_HANDLE_FILE_INFORMATION savedStamp{};
  std::exception_ptr error;
  HANDLE thread = nullptr;

  static DWORD WINAPI send(void *argument) noexcept {
    auto &job = *static_cast<SnapshotTransfer *>(argument);
    try {
      if (job.native) {
        auto content = hex(digest_file(job.file));
        BY_HANDLE_FILE_INFORMATION current{};
        if (!GetFileInformationByHandle(job.file, &current) ||
            CompareFileTime(&current.ftLastWriteTime, &job.savedStamp.ftLastWriteTime) != 0 ||
            current.nFileSizeHigh != job.savedStamp.nFileSizeHigh ||
            current.nFileSizeLow != job.savedStamp.nFileSizeLow)
          throw std::runtime_error("确认期间原生文件发生变化，恢复版本已保留");
        if (job.nativeToken.empty())
          job.nativeToken = field(job.bridge.request(L"POST", job.path + "/native-explicit"), "native_token");
        job.bridge.request(L"POST", job.path + "/native-digest", content,
                           "X-TwoDrive-Native-Token: " + job.nativeToken + "\r\n");
      } else {
        auto content = job.file == INVALID_HANDLE_VALUE ? utf8(job.wordXml) : std::string();
        std::wstring().swap(job.wordXml);
        job.checksum = job.file == INVALID_HANDLE_VALUE ? digest(content) : digest_file(job.file);
        job.unchanged = job.checksum == job.previous;
        if (!job.unchanged) {
          auto reply = job.bridge.request(L"POST", job.path, content, job.headers, job.file);
          job.unchanged = bool_field(reply, "unchanged");
        }
      }
    } catch (...) {
      job.error = std::current_exception();
    }

    return 0;
  }
  void start() {
    thread = CreateThread(nullptr, 0, send, this, 0, nullptr);
    if (!thread) throw std::runtime_error("Cannot start snapshot transfer");
  }
  ~SnapshotTransfer() {
    if (thread) {
      WaitForSingleObject(thread, INFINITE);
      CloseHandle(thread);
    }
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
  }
};

struct Record {
  D identity;
  std::wstring autoName, sessionName;
  std::string session, source;
  bool paused = false, inPlace = false, nativeObserved = false;
  unsigned long long revision = 0;
  ULONGLONG due = 0, lastStage = 0, lastCaptureAttempt = 0,
            lastHeartbeat = 0, lastAutoCheck = 0;
  std::string lastSnapshot;
  std::unique_ptr<SnapshotTransfer> transfer;
  std::unique_ptr<SnapshotTransfer> savedFile;
  ULONGLONG dirtySince = 0, captureGap = 5000;

  ULONGLONG retryAfter = 0, lastFailure = 0, retryDelay = 0;
  void deferRetry(ULONGLONG now) {
    retryDelay = !retryDelay || now - lastFailure > 120000 ? 5000 : (std::min<ULONGLONG>)(60000, 2 * retryDelay);
    lastFailure = now;
    retryAfter = now + retryDelay;
  }

  DWORD captureInput = 0;
  bool quietSnapshot = false;

  bool timerCaptureDue(ULONGLONG now, bool recentInput, DWORD inputStamp = 0) const {
    if (lastCaptureAttempt && now - lastCaptureAttempt < 5000) return false;
    if (now >= due) return true;
    // PowerPoint keeps Saved false after SaveCopyAs. Once two captures agree,
    // new input wakes normal sampling. Periodically check silent COM edits too.
    if (quietSnapshot && inputStamp == captureInput)
      return now - lastCaptureAttempt >= 60000;
    if (now - dirtySince >= 30000) return true;
    return !recentInput && (!lastCaptureAttempt || now - lastCaptureAttempt >= captureGap);
  }
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
#ifdef TWODRIVE_TEST_ACCESS
  friend struct AddInSmoke;
#endif
  LONG refs = 1;
  D app, ribbon;
  HWND window = nullptr;
  bool busy = false, settingsReady = false;
  ULONGLONG nextConnect = 0;
  std::wstring host;
  std::string root, prefix, status = "等待打开 TwoDrive 文档";
  Http http;
  std::map<std::string, Record> records;
  std::map<std::string, D> closing;
  std::map<std::string, HWND> closingFrames;
  std::set<HWND> frames;
  HWND closingFrame = nullptr;
  std::set<std::string> savedCloseRequests;
  std::wstring directory;
  ULONGLONG interval = 180000;
  IConnectionPoint *point = nullptr;
  DWORD cookie = 0;
  ITypeInfo *eventType = nullptr;
  static LRESULT CALLBACK frameProc(HWND h, UINT msg, WPARAM wp, LPARAM lp,
                                    UINT_PTR id, DWORD_PTR data) {
    auto self = (AddIn *)data;
    self->AddRef();
    HWND previous = self->closingFrame;
    if (msg == WM_CLOSE || (msg == WM_SYSCOMMAND && (wp & 0xfff0) == SC_CLOSE))
      self->closingFrame = h;
    if (msg == WM_NCDESTROY) {
      RemoveWindowSubclass(h, frameProc, id);
      self->frames.erase(h);
    }
    LRESULT result = DefSubclassProc(h, msg, wp, lp);
    self->closingFrame = previous;
    self->Release();
    return result;
  }
  static BOOL CALLBACK registerFrame(HWND h, LPARAM data) {
    auto self = (AddIn *)data;
    wchar_t name[64];
    GetClassNameW(h, name, 64);
    if (wcscmp(name, L"OpusApp") && wcscmp(name, L"XLMAIN") && wcscmp(name, L"PPTFrameClass"))
      return TRUE;
    if (SetWindowSubclass(h, frameProc, 1, (DWORD_PTR)self)) self->frames.insert(h);
    return TRUE;
  }
  static LRESULT CALLBACK proc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    auto self = (AddIn *)GetWindowLongPtrW(h, GWLP_USERDATA);
    if (msg == WM_NCCREATE) {
      self = (AddIn *)((CREATESTRUCTW *)lp)->lpCreateParams;
      SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)self);
    }
    if (msg == WM_TIMER && self) {
      if (!self->closing.empty())
        self->finishClose();
      else
        self->poll();
    }
    if (msg == WM_APP + 2 && self)
      self->observeNativeSaves();
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
    if (root.empty())
      return false;
    if (root == "/")
      return !s.empty() && s[0] == '/';
    return s == root ||
           (s.size() > root.size() && s.compare(0, root.size(), root) == 0 &&
            s[root.size()] == '/');
  }
  void loadSettings() {
    auto settings = http.request(L"GET", "/settings");
    auto mount = field(settings, "mount_root");
    if (mount.empty() || mount[0] != '/')
      throw std::runtime_error("保存服务挂载配置无效");
    while (mount.size() > 1 && mount.back() == '/')
      mount.pop_back();
    auto pos = settings.find("\"sync_interval_seconds\"");
    if (pos != std::string::npos) {
      auto n = std::stod(settings.substr(settings.find(':', pos) + 1));
      if (n >= 1 && n <= 3600)
        interval = (ULONGLONG)(n * 1000);
    }
    root = mount;
    settingsReady = true;
    status = "等待打开 TwoDrive 文档";
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
  void snapshot(const D &doc, SnapshotTransfer &job) {
    Alerts alerts(app, host);
    if (host == L"Word") {
      try {
        job.wordXml = doc.str(L"WordOpenXML");
        return;
      } catch (const ComError &error) {
        if (error.hresult == DISP_E_EXCEPTION)
          throw WordSnapshotError(error);
        throw;
      }
    }
    wchar_t temp[MAX_PATH];
    if (!GetTempFileNameW(directory.c_str(), L"tdo", 0, temp))
      throw std::runtime_error("Cannot allocate private export");
    DeleteFileW(temp);
    std::wstring path = std::wstring(temp) + L"." + wide(extension());
    try {
      if (host == L"Excel")
        doc.method(L"SaveCopyAs", {V(path)});
      else
        doc.method(L"SaveCopyAs", {V(path), V(24L)});
      job.file = CreateFileW(path.c_str(), GENERIC_READ | DELETE,
                             FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                             FILE_FLAG_DELETE_ON_CLOSE, nullptr);
      if (job.file == INVALID_HANDLE_VALUE)
        throw std::runtime_error("Cannot open private Office export");
    } catch (...) {
      DeleteFileW(path.c_str());
      throw;
    }
  }
  bool finishTransfer(Record &record, bool wait) {
    if (!record.transfer) return true;
    auto result = WaitForSingleObject(record.transfer->thread, wait ? INFINITE : 0);
    if (result == WAIT_TIMEOUT) return false;
    if (result != WAIT_OBJECT_0) throw std::runtime_error("Cannot check snapshot transfer");
    auto job = std::move(record.transfer);
    if (job->error) std::rethrow_exception(job->error);
    if (job->native) {
      record.lastSnapshot.clear();
      record.quietSnapshot = false;
      record.lastStage = 0;
      record.due = GetTickCount64() + interval;
      status = "Office 已保存至 TwoDrive，上传由 TwoDrive 管理";
      return true;
    }
    record.quietSnapshot = host == L"PowerPoint" && job->unchanged;
    if (job->checksum != job->previous) {
      record.lastSnapshot = std::move(job->checksum);
      record.lastStage = GetTickCount64();
    }
    status = "已本地保存，待提交 TwoDrive";
    return true;
  }
  void close(Record &record) {
    try { finishTransfer(record, true); }
    catch (const std::exception &e) { status = e.what(); }
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
    record.lastCaptureAttempt = GetTickCount64();
    auto job = std::make_unique<SnapshotTransfer>();
    try {
      snapshot(doc, *job);
    } catch (const WordSnapshotError &) {
      // Stop retrying the same failed serializer on the Office UI thread.
      // Keep the native dirty flag and every earlier durable revision intact.
      record.paused = true;
      throw;
    }
    // Budget expensive Office exports according to measured STA time,
    // independently for each document. Cap the interval for active editing.
    auto elapsed = GetTickCount64() - record.lastCaptureAttempt;
    record.captureGap = (std::clamp<ULONGLONG>)(elapsed * 10, 5000, 30000);
    job->bridge = http;
    job->path = "/sessions/" + record.session + "/office";
    job->headers =
        "X-TwoDrive-Source: " + hex(current) +
        "\r\nX-TwoDrive-Revision: " + std::to_string(++record.revision) +
        "\r\nX-TwoDrive-Encoding: " + (host == L"Word" ? "flatopc" : "ooxml") +
        "\r\n";
    if (GetTickCount64() - record.lastStage < 10000)
      job->previous = record.lastSnapshot;
    job->start();
    record.transfer = std::move(job);
    record.dirtySince = 0;
    status = "正在写入本地恢复版本";
  }
  std::unique_ptr<SnapshotTransfer> savedFile(const D &doc, const Record &record,
                                               std::string approval = {}) {
    auto name = doc.str(L"FullName");
    if (name != record.sessionName)
      throw std::runtime_error("另存为后请重新开启自动保存");
    auto job = std::make_unique<SnapshotTransfer>();
    job->native = true;
    job->nativeToken = std::move(approval);
    job->bridge = http;
    job->path = "/sessions/" + record.session;
    // Pin the file that Office actually saved. If it is replaced while the
    // next edit is underway, the bridge still compares against these bytes.
    job->file = CreateFileW(name.c_str(), GENERIC_READ,
                            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                            nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (job->file == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(job->file, &job->savedStamp))
      throw std::runtime_error("Cannot open the saved Office file for confirmation");
    return job;
  }
  void confirm(Record &record) {
    record.savedFile->start();
    record.transfer = std::move(record.savedFile);
    status = "Office 已写入文件，正在确认";
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
      record.savedFile = savedFile(doc, record, field(approval, "native_token"));
      record.nativeObserved = false;
      confirm(record);
      return;
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
    if (!settingsReady)
      loadSettings();
    auto key = documentKey(doc);
    auto &rec = records[key];
    rec.identity = doc;
    if (!manual && (rec.paused || (!force && GetTickCount64() < rec.retryAfter)))
      return;
    if (rec.session.empty()) {
      auto name = doc.str(L"FullName");
      if (!manual && name == rec.autoName && rec.lastAutoCheck &&
          GetTickCount64() - rec.lastAutoCheck < 10000)
        return;
      rec.autoName = name;
      rec.source = source(doc);
      rec.lastAutoCheck = GetTickCount64();
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
      rec.sessionName = name;
      rec.savedFile.reset();
      rec.nativeObserved = false;
      rec.lastSnapshot.clear();
      rec.lastStage = 0;
      rec.lastCaptureAttempt = 0;
      rec.quietSnapshot = false;
      rec.dirtySince = 0;
      rec.captureGap = 5000;
      rec.inPlace = bool_field(result, "in_place");
      status = "自动保存已开启，等待修改";
      rec.due = GetTickCount64() + interval;
      rec.paused = false;
    }
    const bool hadTransfer = bool(rec.transfer);
    if (!finishTransfer(rec, force)) return;
    if (GetTickCount64() - rec.lastHeartbeat >= 10000) {
      auto response = http.request(L"GET", "/sessions/" + rec.session);
      rec.lastHeartbeat = GetTickCount64();
      auto error = field(response, "sync_error");
      if (!error.empty())
        status = error;
    }
    // After-save events can arrive between polls. A pinned file can be
    // acknowledged even after the user starts the next edit. A before-save
    // notification alone still requires Office to report a clean document.
    if (rec.savedFile || (rec.nativeObserved && rec.inPlace && doc.num(L"Saved") != 0)) {
      if (source(doc) != rec.source)
        throw std::runtime_error("另存为后请重新开启自动保存");
      if (!rec.savedFile) rec.savedFile = savedFile(doc, rec);
      rec.nativeObserved = false;
      confirm(rec);
      if (!finishTransfer(rec, force)) return;
    }
    const auto now = GetTickCount64();
    if (doc.num(L"Saved") == 0) {
      if (!rec.dirtySince) rec.dirtySince = now;
      LASTINPUTINFO input{sizeof(input), 0};
      bool typing = GetLastInputInfo(&input) && DWORD(now - input.dwTime) < 1500;
      // A completed timer export can be published now. Forced actions must
      // capture again so edits made during the older transfer reach the file.
      if (force || (!hadTransfer && rec.timerCaptureDue(now, typing, input.dwTime))) {
        rec.captureInput = input.dwTime;
        stage(doc, rec);
        if (!finishTransfer(rec, force)) return;
      } else if (!hadTransfer && now >= rec.due) {
        // Preserve the most recent edits in copy mode before publishing.
        return;
      }
    } else {
      rec.dirtySince = 0;
    }
    if (force || GetTickCount64() >= rec.due) {
      if (!rec.lastSnapshot.empty()) {
        publish(doc, rec);
        finishTransfer(rec, force);
      } else
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
              SysAllocString(wide(id == 16 ? "0.3.2" : status).c_str());
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
      if (GetPrivateProfileIntW(L"bridge", L"sandbox_wakeup", 0, ini.c_str()))
        http.wakeDirectory = directory + L"\\relay.request";
      if (http.token.size() < 32 || http.port < 1024 || http.port > 65535)
        throw std::runtime_error("请安装 TwoDrive Wine bridge 配置");
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
      EnumThreadWindows(GetCurrentThreadId(), registerFrame, (LPARAM)this);
      // Keep the STA timer alive when a sandbox relay/service is not ready at
      // connection time. No document is processed before settings are loaded.
      loadSettings();
      if (instance)
        D(instance).put(L"Object", V((IDispatch *)(Ext *)this));
    } catch (const std::exception &e) {
      status = e.what();
    }
    return S_OK;
  }
  void observeNativeSaves() {
    // A before-save notification can also precede a cancelled save. Only pin
    // a clean document here. Running a full timer tick from this message
    // could start an export inside Office's own save dialog/message pump.
    if (busy) return;
    busy = true;
    for (auto &entry : records) {
      auto &record = entry.second;
      try {
        if (!record.paused && record.nativeObserved && record.inPlace &&
            record.identity.num(L"Saved") != 0) {
          record.savedFile = savedFile(record.identity, record);
          record.nativeObserved = false;
        }
      } catch (const std::exception &e) { status = e.what(); }
    }
    busy = false;
  }
  void poll() {
    if (busy || !app.p || !window)
      return;
    busy = true;
    EnumThreadWindows(GetCurrentThreadId(), registerFrame, (LPARAM)this);
    try {
      if (!settingsReady) {
        if (GetTickCount64() < nextConnect) {
          busy = false;
          return;
        }
        nextConnect = GetTickCount64() + 5000;
        loadSettings();
      }
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
          } catch (const SessionClosed &) {
            // Idle shutdown is independent of stale Office processes. Reopen
            // through bridge validation; retained revisions block unsafe reuse.
            auto &record = records[documentKey(doc)];
            record.session.clear();
            record.lastAutoCheck = 0;
            status = "保存会话已空闲退出，正在重新连接";
          } catch (const std::exception &e) {
            records[documentKey(doc)].deferRetry(GetTickCount64());
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
                    n == L"PresentationSave" || n == L"WorkbookAfterSave";
    if (!beforeClose && !userSave)
      return;
    busy = true;
    try {
      for (UINT i = 0; i < args->cArgs; i++)
        if (args->rgvarg[i].vt == VT_DISPATCH) {
          D doc(args->rgvarg[i].pdispVal);
          if (beforeClose) {
            if (savedCloseRequests.erase(documentKey(doc))) break;
            auto it = records.find(documentKey(doc));
            if (it != records.end() && !it->second.paused &&
                !it->second.session.empty()) {
              // Word and PowerPoint can save within BeforeClose. Excel also
              // needs no deferral after a successful native save. Preserve
              // the original application/window-close request in these cases.
              if (host != L"Excel" || doc.num(L"Saved")) {
                try { process(doc, true); }
                catch (const WordSnapshotError &error) {
                  // A user-initiated close must retain Word's normal native
                  // save/discard/cancel choice when our serializer is unusable.
                  // No native Save ran, no dirty flag or close argument changed.
                  status = error.what();
                } catch (...) {
                  for (UINT j = 0; j < args->cArgs; ++j)
                    if (args->rgvarg[j].vt == (VT_BOOL | VT_BYREF) && args->rgvarg[j].pboolVal)
                      *args->rgvarg[j].pboolVal = VARIANT_TRUE;
                  throw;
                }
                break;
              }
              bool cancelled = false;
              for (UINT j = 0; j < args->cArgs; ++j)
                if (args->rgvarg[j].vt == (VT_BOOL | VT_BYREF) &&
                    args->rgvarg[j].pboolVal) {
                  *args->rgvarg[j].pboolVal = VARIANT_TRUE;
                  cancelled = true;
                }
              if (cancelled) {
                closing[documentKey(doc)] = doc;
                closingFrames[documentKey(doc)] = closingFrame;
                PostMessageW(window, WM_APP + 1, 0, 0);
              } else
                process(doc, true);
            }
          } else {
            auto it = records.find(documentKey(doc));
            if (it != records.end() && !it->second.session.empty() &&
                !it->second.paused && it->second.inPlace) {
              auto &record = it->second;
              if (n == L"PresentationSave" || n == L"WorkbookAfterSave") {
                bool success = n == L"PresentationSave";
                for (UINT j = 0; j < args->cArgs; ++j)
                  if (args->rgvarg[j].vt == VT_BOOL)
                    success = args->rgvarg[j].boolVal != VARIANT_FALSE;
                record.nativeObserved = false;
                if (success) record.savedFile = savedFile(doc, record);
              } else {
                record.nativeObserved = true;
              }
              if (record.nativeObserved) PostMessageW(window, WM_APP + 2, 0, 0);
            }
          }
          break;
        }
    } catch (const std::exception &e) {
      status = e.what();
    }
    busy = false;
  }
  void finishClose() {
    if (busy) return; // The existing STA timer drains the retained close request.
    busy = true;
    auto pending = std::move(closing);
    closing.clear();
    auto requests = std::move(closingFrames);
    closingFrames.clear();
    for (auto &pair : pending) {
      try {
        // Excel refuses exports/saves while BeforeClose is on the stack. Its
        // cancelled close has now returned; run on the same Office STA thread.
        process(pair.second, true);
        auto originalFrame = requests[pair.first];
        if (originalFrame && IsWindow(originalFrame)) {
          // Retry the same frame close after saving. This preserves both SDI
          // document closes and application exit; no implicit Quit is added.
          savedCloseRequests.insert(pair.first);
          if (!PostMessageW(originalFrame, WM_CLOSE, 0, 0)) savedCloseRequests.erase(pair.first);
        } else pair.second.method(L"Close");
      } catch (const std::exception &e) {
        status = e.what();
      }
    }
    busy = false;
  }
  void stop() {
    settingsReady = false;
    root.clear();
    for (auto frame : frames) RemoveWindowSubclass(frame, frameProc, 1);
    frames.clear();
    closingFrames.clear();
    savedCloseRequests.clear();
    if (window) {
      KillTimer(window, 1);
      DestroyWindow(window);
      window = nullptr;
      UnregisterClassW(L"TwoDriveOfficeWineTimer", moduleHandle);
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
