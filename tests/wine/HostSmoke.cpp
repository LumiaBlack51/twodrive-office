#include "com.hpp"
#include <cstdio>
#include <fstream>

void require(bool b, const char *message) {
  if (!b)
    throw std::runtime_error(message);
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
int wmain(int argc, wchar_t **argv) {
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  D app, doc;
  bool owned = false;
  try {
    require(argc == 3, "Usage: HostSmoke host test-directory");
    std::wstring host = argv[1], directory = argv[2];
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
    std::ofstream(utf8(directory + L"\\host.ready")) << "ready";
    ULONGLONG allowedBy = GetTickCount64() + 15000;
    while (GetFileAttributesW((directory + L"\\allow-host").c_str()) ==
               INVALID_FILE_ATTRIBUTES &&
           GetTickCount64() < allowedBy)
      pump(50);
    require(GetFileAttributesW((directory + L"\\allow-host").c_str()) !=
                INVALID_FILE_ATTRIBUTES,
            "Outer harness did not verify private process environment");
    D addins = app.obj(L"COMAddIns");
    addins.method(L"Update");
    printf("ADDINS %ld\n", addins.num(L"Count"));
    fflush(stdout);
    D addin(addins.call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET,
                        {V(std::wstring(L"TwoDrive.Office.WineAutoSave"))}));
    printf("CONNECTED %ld\n", addin.num(L"Connect"));
    fflush(stdout);
    if (!addin.num(L"Connect"))
      addin.put(L"Connect", V(true));
    D api = addin.obj(L"Object");
    printf("ADAPTER %s STATUS %s\n", utf8(api.str(L"Version")).c_str(),
           utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    if (host == L"Word")
      doc = D(docs.call(L"Add", DISPATCH_METHOD));
    else if (host == L"Excel")
      doc = D(docs.call(L"Add", DISPATCH_METHOD));
    else
      doc = D(docs.call(L"Add", DISPATCH_METHOD, {V(0L)}));
    std::wstring ext = host == L"Word"    ? L"docx"
                       : host == L"Excel" ? L"xlsx"
                                          : L"pptx";
    auto target = directory + L"\\source." + ext;
    if (host == L"Word") {
      doc.obj(L"Content").put(L"Text", V(std::wstring(L"Wine original")));
      doc.method(L"SaveAs2", {V(target), V(12L)});
    } else if (host == L"Excel") {
      D sheet(
          doc.obj(L"Worksheets")
              .call(L"Item", DISPATCH_METHOD | DISPATCH_PROPERTYGET, {V(1L)}));
      D cell(
          sheet.call(L"Range", DISPATCH_PROPERTYGET, {V(std::wstring(L"A1"))}));
      cell.put(L"Value2", V(std::wstring(L"Wine original")));
      doc.method(L"SaveAs", {V(target), V(51L)});
    } else {
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
    pump(2200);
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
    edit(L"Wine actual unsaved edits 汉字");
    require(doc.num(L"Saved") == 0, "Edit did not mark document dirty");
    pump(2200);
    printf("STAGED STATUS %s\n", utf8(api.str(L"Status")).c_str());
    fflush(stdout);
    require(read_file(target) == baseline,
            "Staging changed mounted source before interval");
    require(doc.num(L"Saved") == 0,
            "Local staging falsely cleared native dirty marker");
    // The outer harness inspects the real Linux durable revision at this
    // barrier.
    std::ofstream(utf8(directory + L"\\staged.ready")) << "ready";
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
    pump(1500);
    require(doc.num(L"Saved") != 0, "Explicit native Save failed");
    edit(L"Timer latest edits 汉字");
    pump(11000);
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
    // Closing invokes the registered application event and must save the final
    // edit.
    edit(L"Wine final close edits 汉字");
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
    app.method(L"Quit");
    app = D();
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
