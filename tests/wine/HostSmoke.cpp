#include "com.hpp"
#include <cstdio>

void require(bool b, const char *message) {
  if (!b)
    throw std::runtime_error(message);
}
void ready(const std::wstring &path) {
  HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  require(file != INVALID_HANDLE_VALUE, "Cannot create private Unicode test barrier");
  DWORD written = 0;
  bool complete = WriteFile(file, "ready", 5, &written, nullptr) && written == 5;
  CloseHandle(file);
  require(complete, "Cannot write private test barrier");
}
void pump(int ms) {
  ULONGLONG end = GetTickCount64() + ms;
  while (GetTickCount64() < end) {
    MSG m;
    while (PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) {
      TranslateMessage(&m);
      DispatchMessageW(&m);
    }
    Sleep(20);
  }
}
struct WindowSearch {
  std::wstring executable;
  std::vector<HWND> matches;
};
BOOL CALLBACK findWindow(HWND window, LPARAM argument) {
  auto &search = *(WindowSearch *)argument;
  if (!IsWindowVisible(window) || GetWindow(window, GW_OWNER) ||
      (GetWindowLongPtrW(window, GWL_EXSTYLE) & WS_EX_TOOLWINDOW))
    return TRUE;
  DWORD pid = 0;
  GetWindowThreadProcessId(window, &pid);
  HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (process) {
    wchar_t name[32768]; DWORD size = 32768;
    if (QueryFullProcessImageNameW(process, 0, name, &size)) {
      std::wstring path(name, size);
      auto pos = path.find_last_of(L"\\/");
      if (!_wcsicmp(path.substr(pos == std::wstring::npos ? 0 : pos + 1).c_str(),
                    search.executable.c_str()))
        search.matches.push_back(window);
    }
    CloseHandle(process);
  }
  return TRUE;
}
int wmain(int argc, wchar_t **argv) {
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  D app, doc;
  bool owned = false;
  try {
    require(argc == 3, "Usage: HostSmoke host test-directory");
    std::wstring host = argv[1], directory = argv[2];
    bool measureInput = host == L"PowerPoint" && GetEnvironmentVariableW(L"TWODRIVE_TEST_PAINT", nullptr, 0);
    bool largeDeck = measureInput || (host == L"PowerPoint" &&
        GetEnvironmentVariableW(L"TWODRIVE_TEST_LARGE_DECK", nullptr, 0));
    CLSID cls;
    check(CLSIDFromProgID((host + L".Application").c_str(), &cls));
    IDispatch *p = nullptr;
    check(CoCreateInstance(cls, nullptr, CLSCTX_LOCAL_SERVER, IID_IDispatch,
                           (void **)&p));
    app = D(p);
    p->Release();
    D docs = app.obj(host == L"Word"    ? L"Documents"
                     : host == L"Excel" ? L"Workbooks"
                                        : L"Presentations");
    require(docs.num(L"Count") == 0,
            "Unexpected existing documents in isolated prefix");
    owned = true;
    if (host != L"PowerPoint")
      app.put(L"Visible", V(false));
    printf("HOST %s version=%s\n", utf8(host).c_str(),
           utf8(app.str(L"Version")).c_str());
    fflush(stdout);
    ready(directory + L"\\host.ready");
    ULONGLONG allowedBy = GetTickCount64() + 15000;
    while (GetFileAttributesW((directory + L"\\allow-host").c_str()) ==
               INVALID_FILE_ATTRIBUTES &&
           GetTickCount64() < allowedBy)
      pump(50);
    require(GetFileAttributesW((directory + L"\\allow-host").c_str()) !=
                INVALID_FILE_ATTRIBUTES,
            "Outer harness did not verify private process environment");
    D addins = app.obj(L"COMAddIns");
    printf("ADDINS %ld\n", addins.num(L"Count"));
    fflush(stdout);
    D addin(addins.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                        {V(std::wstring(L"TwoDrive.Office.WineAutoSave"))}));
    printf("CONNECTED %ld\n", addin.num(L"Connect"));
    fflush(stdout);
    require(addin.num(L"Connect") != 0,
            "Add-in did not connect automatically; forced connection is forbidden in startup verification");
    D api = addin.obj(L"Object");
    printf("ADAPTER %s STATUS %s\n", utf8(api.str(L"Version")).c_str(),
           utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    if (GetEnvironmentVariableW(L"TWODRIVE_TEST_IDLE_REOPEN", nullptr, 0)) {
      if (host == L"Excel")
        app.put(L"Visible", V(true));
      // No documents/heartbeats: the relay must exit despite the real Office
      // process remaining alive, then wake when this same instance opens one.
      pump(35000);
      ready(directory + L"\\empty-idle.ready");
      auto reopenBy = GetTickCount64() + 15000;
      while (GetFileAttributesW((directory + L"\\allow-reopen").c_str()) ==
                 INVALID_FILE_ATTRIBUTES && GetTickCount64() < reopenBy)
        pump(50);
      require(GetFileAttributesW((directory + L"\\allow-reopen").c_str()) !=
                  INVALID_FILE_ATTRIBUTES,
              "Outer harness did not verify empty-editor relay exit");
    }
    std::wstring ext = host == L"Word"    ? L"docx"
                       : host == L"Excel" ? L"xlsx"
                                          : L"pptx";
    wchar_t inputName[MAX_PATH] = {};
    DWORD inputLength = GetEnvironmentVariableW(L"TWODRIVE_TEST_WORD_DOCUMENT", inputName, MAX_PATH);
    require(inputLength < MAX_PATH, "Private document name is too long");
    bool existingWord = host == L"Word" && inputLength;
    auto target = directory + L"\\" + (existingWord ? std::wstring(inputName) : L"source." + ext);
    printf("CREATE DOCUMENT\n"); fflush(stdout);
    if (existingWord) {
      app.put(L"AutomationSecurity", V(3L));
      auto oldAlerts = app.call(L"DisplayAlerts");
      app.put(L"DisplayAlerts", V(0L));
      app.obj(L"Options").put(L"UpdateLinksAtOpen", V(false));
      doc = D(docs.call(L"Open", DISPATCH_METHOD,
                        {V(target), V(false), V(false), V(false)}));
      app.put(L"DisplayAlerts", oldAlerts);
      if (GetEnvironmentVariableW(L"TWODRIVE_TEST_WORD_VISIBLE", nullptr, 0))
        app.put(L"Visible", V(true)); // verified private display only
      printf("AUTHORIZED COPY OPENED compatibility=%ld\n", doc.num(L"CompatibilityMode")); fflush(stdout);
    } else if (host == L"Word")
      doc = D(docs.call(L"Add", DISPATCH_METHOD));
    else if (host == L"Excel")
      doc = D(docs.call(L"Add", DISPATCH_METHOD));
    else
      doc = D(docs.call(L"Add", DISPATCH_METHOD, {V(measureInput ? -1L : 0L)}));
    printf("DOCUMENT CREATED\n"); fflush(stdout);
    if (host == L"Word" && !existingWord) {
      doc.obj(L"Content").put(L"Text", V(std::wstring(L"Wine original")));
      wchar_t mode[16];
      if (GetEnvironmentVariableW(L"TWODRIVE_TEST_WORD_COMPATIBILITY", mode, 16)) {
        auto compatibility = _wtol(mode);
        doc.method(L"SetCompatibilityMode", {V(compatibility)});
        std::wstring body;
        for (int page = 0; page < 17; ++page) {
          for (int line = 0; line < 20; ++line)
            body += L"Synthetic compatibility document 汉字 body text.\r";
          if (page != 16) body += L"\f";
        }
        doc.obj(L"Content").put(L"Text", V(body));
        D section(doc.obj(L"Sections").call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
        for (auto collection : {L"Headers", L"Footers"}) {
          D part(section.obj(collection).call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
          part.obj(L"Range").put(L"Text", V(std::wstring(collection) + L" synthetic preserved marker"));
        }
        D shape(doc.obj(L"Shapes").call(L"AddTextbox", DISPATCH_METHOD,
                                        {V(1L), V(20L), V(20L), V(300L), V(80L)}));
        shape.obj(L"TextFrame").obj(L"TextRange").put(L"Text", V(std::wstring(L"Floating textbox preserved marker")));
        require(doc.num(L"CompatibilityMode") == compatibility, "Synthetic compatibility mode was not applied");
        printf("WORD COMPATIBILITY %ld\n", compatibility); fflush(stdout);
      }
      doc.method(L"SaveAs2", {V(target), V(12L)});
    } else if (host == L"Excel") {
      D sheet(
          doc.obj(L"Worksheets")
              .call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
      D cell(
          sheet.call(L"Range", DISPATCH_PROPERTYGET, {V(std::wstring(L"A1"))}));
      cell.put(L"Value2", V(std::wstring(L"Wine original")));
      printf("EXCEL SAVEAS\n"); fflush(stdout);
      doc.method(L"SaveAs", {V(target), V(51L)});
    } else if (host == L"PowerPoint") {
      D slide(
          doc.obj(L"Slides").call(L"Add", DISPATCH_METHOD, {V(1L), V(12L)}));
      D shape(
          slide.obj(L"Shapes").call(L"AddTextbox", DISPATCH_METHOD,
                                    {V(1L), V(20L), V(20L), V(400L), V(100L)}));
      shape.obj(L"TextFrame")
          .obj(L"TextRange")
          .put(L"Text", V(std::wstring(L"Wine original")));
      doc.method(L"SaveAs", {V(target), V(24L)});
    }
    printf("PATH %s FULL %s\n", utf8(doc.str(L"Path")).c_str(),
           utf8(doc.str(L"FullName")).c_str());
    fflush(stdout);
    auto baseline = read_file(target);
    if (GetEnvironmentVariableW(L"TWODRIVE_TEST_RIBBON", nullptr, 0)) {
      app.put(L"Visible", V(true));
      pump(1200);
      ready(directory + L"\\ribbon.ready");
      auto capturedBy = GetTickCount64() + 15000;
      while (GetFileAttributesW((directory + L"\\ribbon.captured").c_str()) ==
                 INVALID_FILE_ATTRIBUTES && GetTickCount64() < capturedBy)
        pump(50);
      require(GetFileAttributesW((directory + L"\\ribbon.captured").c_str()) !=
                  INVALID_FILE_ATTRIBUTES, "Private ribbon capture did not finish");
    }
    auto connectedBy = GetTickCount64() + 12000;
    do {
      pump(100);
    } while (api.call(L"Pressed").num() == 0 && GetTickCount64() < connectedBy);
    printf("AUTOOPEN STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(api.call(L"Pressed").num() != 0, "Automatic clean-document session did not connect");
    require(read_file(target) == baseline, "Clean open rewrote source");
    auto edit = [&](const std::wstring &text) {
      if (host == L"Word")
        doc.obj(L"Content").method(L"InsertAfter", {V(text)});
      else if (host == L"Excel") {
        D sheet(doc.obj(L"Worksheets")
                    .call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                          {V(1L)}));
        D cell(sheet.call(L"Range", DISPATCH_PROPERTYGET,
                          {V(std::wstring(L"A1"))}));
        cell.put(L"Value2", V(text));
      } else {
        D slide(doc.obj(L"Slides").call(
            L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
        D shape(slide.obj(L"Shapes").call(
            L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
        shape.obj(L"TextFrame").obj(L"TextRange").put(L"Text", V(text));
      }
    };
    bool mixed = GetEnvironmentVariableW(L"TWODRIVE_TEST_MIXED_EDITS", nullptr, 0);
    D component;
    if (mixed) {
      api.method(L"Toggle", {V((IDispatch *)nullptr), V(false)});
      if (host == L"Word") {
        D anchor(doc.call(L"Range", DISPATCH_METHOD, {V(0L), V(0L)}));
        D table(doc.obj(L"Tables").call(L"Add", DISPATCH_METHOD, {V(anchor.p), V(3L), V(2L)}));
        D cell(table.call(L"Cell", DISPATCH_METHOD, {V(2L), V(1L)}));
        component = cell.obj(L"Range");
      } else if (host == L"Excel") {
        D sheet(doc.obj(L"Worksheets").call(L"Add", DISPATCH_METHOD));
        D formulas(sheet.call(L"Range", DISPATCH_PROPERTYGET, {V(std::wstring(L"B2:B2049"))}));
        formulas.put(L"Formula", V(std::wstring(L"=SUM(A1:A2)")));
        component = D(sheet.call(L"Range", DISPATCH_PROPERTYGET, {V(std::wstring(L"C2"))}));
      } else {
        D slide(doc.obj(L"Slides").call(L"Add", DISPATCH_METHOD, {V(2L), V(12L)}));
        D shape(slide.obj(L"Shapes").call(L"AddTable", DISPATCH_METHOD, {V(3L), V(2L)}));
        D cell(shape.obj(L"Table").call(L"Cell", DISPATCH_METHOD, {V(2L), V(1L)}));
        component = cell.obj(L"Shape").obj(L"TextFrame").obj(L"TextRange");
      }
      component.put(host == L"Excel" ? L"Value2" : L"Text", V(std::wstring(L"Mixed component first")));
    }
    edit(L"Wine actual unsaved edits 汉字");
    require(doc.num(L"Saved") == 0, "Edit did not mark document dirty");
    if (mixed) {
      api.method(L"Toggle", {V((IDispatch *)nullptr), V(true)});
      // Handler holds the first snapshot reply until a second real Office
      // edit completes. A synchronous STA transfer cannot pass this barrier.
      auto until = GetTickCount64() + 12000;
      while (GetFileAttributesW((directory + L"\\reply-held").c_str()) == INVALID_FILE_ATTRIBUTES &&
             GetTickCount64() < until) pump(10);
      require(GetFileAttributesW((directory + L"\\reply-held").c_str()) != INVALID_FILE_ATTRIBUTES,
              "No staged snapshot reached the bridge");
      auto start = GetTickCount64();
      component.put(host == L"Excel" ? L"Value2" : L"Text", V(std::wstring(L"Mixed component final")));
      auto elapsed = GetTickCount64() - start;
      ready(directory + L"\\edited-during-transfer");
      printf("EDIT DURING SNAPSHOT REPLY %llu ms\n", elapsed); fflush(stdout);
      component = D();
    } else {
      pump(2200);
    }
    printf("STAGED STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(read_file(target) == baseline,
            "Staging changed mounted source before interval");
    require(doc.num(L"Saved") == 0,
            "Local staging falsely cleared native dirty marker");
    // The outer harness inspects the real Linux durable revision at this
    // barrier.
    ready(directory + L"\\staged.ready");
    ULONGLONG deadline = GetTickCount64() + 15000;
    while (GetFileAttributesW((directory + L"\\continue").c_str()) ==
               INVALID_FILE_ATTRIBUTES &&
           GetTickCount64() < deadline)
      pump(100);
    auto alerts = app.call(L"DisplayAlerts");
    api.method(L"SaveNow");
    printf("SAVE STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(doc.num(L"Saved") != 0,
            "Native Save now did not acknowledge saved state");
    require(app.call(L"DisplayAlerts").num() == alerts.num(),
            "Alerts were not restored");
    require(read_file(target) != baseline, "Native Save did not persist edits");
    auto saved = read_file(target);
    edit(L"Native Ctrl S edits");
    doc.method(L"Save");
    require(doc.num(L"Saved") != 0, "Explicit native Save failed");
    if (!mixed) pump(1500);
    edit(L"Timer latest edits 汉字");
    // The publication deadline is checked by a one-second STA timer, and a
    // durable capture/confirmation can require a subsequent timer tick.
    auto timerStart = GetTickCount64();
    do { pump(150); } while (doc.num(L"Saved") == 0 && GetTickCount64() - timerStart < 20000);
    printf("TIMER COMMIT %llu ms\n", GetTickCount64() - timerStart);
    printf("TIMER STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(doc.num(L"Saved") != 0, "STA interval did not save document");
    require(read_file(target) != saved,
            "STA interval did not persist latest edits");
    saved = read_file(target);
    pump(2200);
    require(read_file(target) == saved, "Idle rewrote document");
    api.method(L"Toggle", {V((IDispatch *)nullptr), V(false)});
    edit(L"Disabled edits");
    pump(2200);
    require(read_file(target) == saved, "Paused adapter wrote source");
    require(api.call(L"Pressed").num() == 0, "Pause did not persist");
    api.method(L"Toggle", {V((IDispatch *)nullptr), V(true)});
    pump(1500);
    printf("REENABLE %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    if (largeDeck) {
      api.method(L"Toggle", {V((IDispatch *)nullptr), V(false)});
      D slides = doc.obj(L"Slides");
      for (int index = 1; index <= 9; ++index) {
        D slide(slides.call(L"Add", DISPATCH_METHOD, {V(slides.num(L"Count") + 1), V(12L)}));
        slide.obj(L"Shapes").method(L"AddPicture", {V(directory + L"\\media-" + std::to_wstring(index) + L".png"),
                                      V(0L), V(-1L), V(40L), V(40L), V(250L), V(250L)});
      }
      doc.method(L"Save");
      api.method(L"Toggle", {V((IDispatch *)nullptr), V(true)});
      D last(slides.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(slides.num(L"Count"))}));
      D marker(last.obj(L"Shapes").call(L"AddTextbox", DISPATCH_METHOD,
                                        {V(1L), V(20L), V(300L), V(400L), V(50L)}));
      D markerText = marker.obj(L"TextFrame").obj(L"TextRange");
      D text;
      if (measureInput) {
        D win = app.obj(L"ActiveWindow");
        win.method(L"Activate");
        win.obj(L"View").method(L"GotoSlide", {V(1L)});
        D slide(slides.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
        D shape(slide.obj(L"Shapes").call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
        text = shape.obj(L"TextFrame").obj(L"TextRange");
        text.put(L"Text", V(std::wstring(L"Edit")));
        text.method(L"Select");
        auto frame = (HWND)(INT_PTR)win.num(L"HWND");
        require(GetAncestor(GetForegroundWindow(), GA_ROOT) == GetAncestor(frame, GA_ROOT) &&
                IsWindowEnabled(frame), "PowerPoint document window is not available for keyboard input");
        long left = win.call(L"PointsToScreenPixelsX", DISPATCH_METHOD, {V(text.num(L"BoundLeft"))}).num();
        long top = win.call(L"PointsToScreenPixelsY", DISPATCH_METHOD, {V(text.num(L"BoundTop"))}).num();
        long right = win.call(L"PointsToScreenPixelsX", DISPATCH_METHOD,
                              {V(text.num(L"BoundLeft") + text.num(L"BoundWidth"))}).num();
        long bottom = win.call(L"PointsToScreenPixelsY", DISPATCH_METHOD,
                               {V(text.num(L"BoundTop") + text.num(L"BoundHeight"))}).num();
        FILE *bounds = _wfopen((directory + L"\\paint-bounds").c_str(), L"w");
        require(bounds != nullptr, "Cannot record slide text bounds");
        fprintf(bounds, "%ld %ld %ld %ld", left, top, right, bottom); fclose(bounds);
        printf("PAINT BOUNDS %ld %ld %ld %ld\n", left, top, right, bottom); fflush(stdout);
        ready(directory + L"\\paint.ready");
      }
      auto until = GetTickCount64() + 90000;
      D extraTable;
      int trial = 0;
      while ((measureInput ? GetFileAttributesW((directory + L"\\paint.done").c_str()) == INVALID_FILE_ATTRIBUTES
                           : trial < 7) && GetTickCount64() < until) {
        auto suffix = std::to_wstring(trial);
        if (!measureInput || GetFileAttributesW((directory + L"\\prepare-" + suffix).c_str()) != INVALID_FILE_ATTRIBUTES) {
          if (trial == 2 || trial == 4) {
            if (!extraTable.p)
              extraTable = D(last.obj(L"Shapes").call(L"AddTable", DISPATCH_METHOD, {V(5L), V(4L)})).obj(L"Table");
            D cell(extraTable.call(L"Cell", DISPATCH_METHOD, {V(2L), V(3L)}));
            cell.obj(L"Shape").obj(L"TextFrame").obj(L"TextRange").put(L"Text", V(L"Table edit " + suffix));
          }
          if (trial == 3 || trial == 5) {
            D images = last.obj(L"Shapes");
            D previous(images.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                                   {trial == 3 ? V(1L) : V(std::wstring(L"Replacement 3"))}));
            previous.method(L"Delete");
            auto source = directory + L"\\media-" + std::to_wstring(trial == 3 ? 10 : 11) + L".png";
            D picture(images.call(L"AddPicture", DISPATCH_METHOD,
                                  {V(source), V(0L), V(-1L), V(40L), V(40L), V(250L), V(250L)}));
            picture.put(L"Name", V(L"Replacement " + suffix));
          }
          markerText.put(L"Text", V(L"Paint trial " + suffix));
          require(doc.num(L"Saved") == 0, "Timed save did not start with a dirty presentation");
          if (measureInput) {
            ready(directory + L"\\dirty-" + suffix);
          } else {
            auto start = GetTickCount64();
            doc.method(L"Save");
            edit(L"Immediate follow-up " + suffix);
            printf("NATIVE SAVE AND NEXT COM EDIT %d %llu ms\n", trial, GetTickCount64() - start);
            fflush(stdout);
          }
          ++trial;
        }
        pump(20);
      }
      require(trial == 7, "Incomplete large-document trials");
      if (measureInput) require(text.str(L"Text") == L"Editw", "Visible edit did not reach the intended slide textbox");
    }
    // Closing invokes the registered application event and must save the final
    // edit.
    edit(L"Wine final close edits 汉字");
    if (GetEnvironmentVariableW(L"TWODRIVE_TEST_FIRST_QUIT", nullptr, 0)) {
      require(host == L"Word", "First-Quit regression currently targets Word");
      try { app.method(L"Quit", {V(0L)}); }
      catch (const std::exception &) { /* The harness still requires natural exit. */ }
      api = D(); addin = D(); addins = D(); docs = D(); doc = D(); app = D();
      pump(2000);
      require(read_file(target) != saved, "Initial Quit did not save final edits");
      printf("PASS first Word Quit\n"); fflush(stdout);
      CoUninitialize(); return 0;
    }
    try {
      if (host == L"Word")
        doc.method(L"Close", {V(0L)});
      else if (host == L"Excel")
        doc.method(L"Close", {V(false)});
      else {
        doc.method(L"Close");
      }
    } catch (const std::exception &) {
      // Word reports its cancelled BeforeClose to automation. The posted STA
      // close must still finish; count/content assertions below verify that.
      printf("CLOSE deferred by add-in\n");
      fflush(stdout);
    }
    doc = D();
    pump(1500);
    printf("CLOSE STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(read_file(target) != saved, "Close did not publish final edit");
    require(docs.num(L"Count") == 0, "Generated document did not close");
    if (GetEnvironmentVariableW(L"TWODRIVE_TEST_WINDOW_CLOSE", nullptr, 0)) {
      app.put(L"Visible", V(true)); // authenticated private Xvfb only
      WindowSearch search{host == L"Word" ? L"WINWORD.EXE" : host == L"Excel" ? L"EXCEL.EXE" : L"POWERPNT.EXE", {}};
      EnumWindows(findWindow, (LPARAM)&search);
      require(search.matches.size() == 1, "Private Office main window is missing or ambiguous");
      auto mainWindow = search.matches[0];
      // Release the driver's COM references before simulating the window close;
      // otherwise the test itself could create an automation-only residue.
      api = D(); addin = D(); addins = D(); docs = D(); app = D();
      require(PostMessageW(mainWindow, WM_CLOSE, 0, 0), "Private WM_CLOSE failed");
      pump(1500);
      printf("WINDOW CLOSE requested after zero documents\n");
    } else {
      app.method(L"Quit");
      app = D();
    }
    printf("PASS %s\n", utf8(host).c_str());
    fflush(stdout);
  } catch (const std::exception &e) {
    fprintf(stderr, "FAIL %s\n", e.what());
    if (doc.p)
      try {
        doc.put(L"Saved", V(true));
        doc.method(L"Close");
      } catch (...) {
      }
    if (owned && app.p)
      try {
        app.method(L"Quit");
      } catch (...) {
      }
    return 1;
  }
  CoUninitialize();
  return 0;
}
