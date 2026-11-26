// Exercise the actual DLL transport code without creating an Office application.
#include "../../wine/AddIn.cpp"
#include <cstdlib>

int wmain(int argc, wchar_t **argv) {
  try {
    if (argc != 2)
      throw std::runtime_error("Usage: HttpSmoke test-port (private namespace only)");
    wchar_t token[512];
    if (!GetEnvironmentVariableW(L"TWODRIVE_TEST_TOKEN", token, 512))
      throw std::runtime_error("Missing private test credential");
    Http client;
    client.token = utf8(token);
    client.port = 1; // no listener in the private test network
    bool failed = false;
    try {
      client.request(L"GET", "/settings");
    } catch (const std::runtime_error &e) {
      failed = std::string(e.what()).find("WinHTTP") != std::string::npos;
    }
    if (!failed)
      throw std::runtime_error("Disconnected request was not diagnosed");
    client.port = _wtoi(argv[1]);
    auto reply = client.request(L"GET", "/settings");
    if (field(reply, "mount_root").empty())
      throw std::runtime_error("Reconnected settings missing");
    bool expired = false;
    try {
      client.request(L"GET", "/sessions/expired-test-session");
    } catch (const SessionClosed &) {
      expired = true;
    }
    if (!expired)
      throw std::runtime_error("Expired session was not distinguished");
    if (!bool_field("{\"in_place\": true}", "in_place") ||
        bool_field("{\"in_place\": false}", "in_place"))
      throw std::runtime_error("Invalid native/copy classification");
    for (auto bad : {"{}", "{\"in_place\": \"true\"}", "{\"in_place\": truex}",
                     "{\"in_place\":", "{\"in_place\"}"}) {
      bool rejected = false;
      try {
        bool_field(bad, "in_place");
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      if (!rejected)
        throw std::runtime_error("Malformed classification accepted");
    }
    puts("WinHTTP failure/reconnect, authenticated settings and classification passed");
    return 0;
  } catch (const std::exception &e) {
    fprintf(stderr, "FAIL %s\n", e.what());
    return 1;
  }
}
