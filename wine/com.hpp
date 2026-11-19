#pragma once
#define WIN32_LEAN_AND_MEAN
#include <algorithm>
#include <ocidl.h>
#include <ole2.h>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <windows.h>

inline void check(HRESULT hr) {
  if (FAILED(hr)) {
    char s[40];
    sprintf(s, "COM error %08lx", (unsigned long)hr);
    throw std::runtime_error(s);
  }
}
struct V : VARIANT {
  V() { VariantInit(this); }
  V(const V &v) {
    VariantInit(this);
    check(VariantCopy(this, const_cast<V *>(&v)));
  }
  V(V &&v) noexcept {
    *(VARIANT *)this = v;
    VariantInit(&v);
  }
  V &operator=(V v) {
    std::swap(*(VARIANT *)this, *(VARIANT *)&v);
    return *this;
  }
  explicit V(const std::wstring &s) : V() {
    vt = VT_BSTR;
    bstrVal = SysAllocStringLen(s.data(), (UINT)s.size());
  }
  explicit V(long n) : V() {
    vt = VT_I4;
    lVal = n;
  }
  explicit V(bool b) : V() {
    vt = VT_BOOL;
    boolVal = b ? VARIANT_TRUE : VARIANT_FALSE;
  }
  explicit V(IDispatch *p) : V() {
    vt = VT_DISPATCH;
    pdispVal = p;
    if (p)
      p->AddRef();
  }
  ~V() { VariantClear(this); }
  std::wstring str() const {
    if (vt != VT_BSTR)
      throw std::runtime_error("Expected string");
    return std::wstring(bstrVal, SysStringLen(bstrVal));
  }
  long num() const {
    V r;
    check(VariantChangeType(&r, const_cast<V *>(this), 0, VT_I4));
    return r.lVal;
  }
};
inline std::string utf8(const std::wstring &s);
struct DispatchException : EXCEPINFO {
  DispatchException() : EXCEPINFO{} {}
  ~DispatchException() {
    SysFreeString(bstrSource);
    SysFreeString(bstrDescription);
    SysFreeString(bstrHelpFile);
  }
};
struct ComError : std::runtime_error {
  HRESULT hresult;
  SCODE scode;
  WORD code;
  static std::string message(const wchar_t *name, HRESULT hr,
                             const EXCEPINFO &info) {
    char number[100];
    sprintf(number, " error %08lx", (unsigned long)hr);
    auto result = "COM " + utf8(name) + number;
    if (info.scode) {
      sprintf(number, " (Office %08lx)", (unsigned long)info.scode);
      result += number;
    }
    if (info.wCode)
      result += " (Office " + std::to_string(info.wCode) + ")";
    if (info.bstrDescription) {
      try {
        result += ": " + utf8(std::wstring(info.bstrDescription,
                                           SysStringLen(info.bstrDescription)));
      } catch (const std::runtime_error &) {
        // Keep the HRESULT even when the server's description is malformed.
      }
    }
    return result;
  }
  ComError(const wchar_t *name, HRESULT hr, const EXCEPINFO &info)
      : std::runtime_error(message(name, hr, info)), hresult(hr),
        scode(info.scode), code(info.wCode) {}
};
struct D {
  IDispatch *p = nullptr;
  D() = default;
  explicit D(IDispatch *value) : p(value) {
    if (p)
      p->AddRef();
  }
  explicit D(const V &v) {
    if (v.vt == VT_DISPATCH)
      p = v.pdispVal;
    else if (v.vt == VT_UNKNOWN && v.punkVal)
      check(v.punkVal->QueryInterface(IID_IDispatch, (void **)&p));
    else
      throw std::runtime_error("Expected dispatch");
    if (v.vt == VT_DISPATCH && p)
      p->AddRef();
  }
  D(const D &d) : D(d.p) {}
  D(D &&d) noexcept : p(d.p) { d.p = nullptr; }
  D &operator=(D d) {
    std::swap(p, d.p);
    return *this;
  }
  ~D() {
    if (p)
      p->Release();
  }
  V call(const wchar_t *name, WORD flags = DISPATCH_PROPERTYGET,
         std::vector<V> args = {}) const {
    if (!p)
      throw std::runtime_error("No dispatch");
    DISPID id;
    LPOLESTR n = const_cast<wchar_t *>(name);
    check(p->GetIDsOfNames(IID_NULL, &n, 1, LOCALE_USER_DEFAULT, &id));
    std::reverse(args.begin(), args.end());
    DISPPARAMS params = {};
    params.rgvarg = args.data();
    params.cArgs = (UINT)args.size();
    DISPID put = DISPID_PROPERTYPUT;
    if (flags & DISPATCH_PROPERTYPUT) {
      params.rgdispidNamedArgs = &put;
      params.cNamedArgs = 1;
    }
    V result;
    DispatchException info;
    UINT error = 0;
    HRESULT hr = p->Invoke(id, IID_NULL, LOCALE_USER_DEFAULT, flags, &params,
                           &result, &info, &error);
    if (FAILED(hr)) {
      if (hr == DISP_E_EXCEPTION && info.pfnDeferredFillIn)
        info.pfnDeferredFillIn(&info);
      throw ComError(name, hr, info);
    }
    return result;
  }
  D obj(const wchar_t *name) const { return D(call(name)); }
  std::wstring str(const wchar_t *name) const { return call(name).str(); }
  long num(const wchar_t *name) const { return call(name).num(); }
  void put(const wchar_t *name, V value) const {
    call(name, DISPATCH_PROPERTYPUT, {value});
  }
  void method(const wchar_t *name, std::vector<V> args = {}) const {
    call(name, DISPATCH_METHOD, args);
  }
  std::string key() const {
    IUnknown *u = nullptr;
    check(p->QueryInterface(IID_IUnknown, (void **)&u));
    auto result = std::to_string((uintptr_t)u);
    u->Release();
    return result;
  }
};
inline std::string utf8(const std::wstring &s) {
  if (s.empty())
    return {};
  int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s.data(),
                              (int)s.size(), nullptr, 0, nullptr, nullptr);
  if (!n)
    throw std::runtime_error("Invalid Unicode");
  std::string r(n, 0);
  WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, s.data(), (int)s.size(),
                      r.data(), n, nullptr, nullptr);
  return r;
}
inline std::wstring wide(const std::string &s) {
  if (s.empty())
    return {};
  int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(),
                              (int)s.size(), nullptr, 0);
  if (!n)
    throw std::runtime_error("Invalid UTF-8");
  std::wstring r(n, 0);
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), (int)s.size(),
                      r.data(), n);
  return r;
}
inline std::string quote(const std::string &s) {
  std::string r = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      r += '\\';
      r += c;
    } else if (c < 32) {
      char h[7];
      sprintf(h, "\\u%04x", c);
      r += h;
    } else
      r += c;
  }
  return r + '"';
}
inline std::string field(const std::string &json, const std::string &key) {
  size_t p = json.find('"' + key + '"');
  if (p == std::string::npos)
    throw std::runtime_error("Missing response field");
  p = json.find(':', p);
  p = json.find_first_not_of(" \t\r\n", p + 1);
  if (p == std::string::npos || json[p] != '"')
    throw std::runtime_error("Invalid response field");
  std::string r;
  for (++p; p < json.size(); ++p) {
    char c = json[p];
    if (c == '"')
      return r;
    if (c != '\\') {
      r += c;
      continue;
    }
    if (++p == json.size())
      break;
    c = json[p];
    if (c == '"' || c == '\\' || c == '/')
      r += c;
    else if (c == 'n')
      r += '\n';
    else if (c == 'r')
      r += '\r';
    else if (c == 't')
      r += '\t';
    else
      throw std::runtime_error("Unsupported response escape");
  }
  throw std::runtime_error("Truncated response");
}
inline bool bool_field(const std::string &json, const std::string &key) {
  auto p = json.find('"' + key + '"');
  if (p == std::string::npos)
    throw std::runtime_error("Missing response field");
  p = json.find(':', p);
  if (p == std::string::npos)
    throw std::runtime_error("Invalid boolean response field");
  p = json.find_first_not_of(" \t\r\n", p + 1);
  for (auto value : {std::string("true"), std::string("false")}) {
    if (p != std::string::npos && json.compare(p, value.size(), value) == 0) {
      auto end = json.find_first_not_of(" \t\r\n", p + value.size());
      if (end != std::string::npos && (json[end] == ',' || json[end] == '}'))
        return value == "true";
    }
  }
  throw std::runtime_error("Invalid boolean response field");
}
inline std::string hex(const std::string &s) {
  const char *h = "0123456789abcdef";
  std::string r;
  for (unsigned char c : s) {
    r += h[c >> 4];
    r += h[c & 15];
  }
  return r;
}
inline std::string read_file(HANDLE h) {
  LARGE_INTEGER size;
  if (!GetFileSizeEx(h, &size) || size.QuadPart <= 0 ||
      size.QuadPart > 128 * 1024 * 1024) {
    throw std::runtime_error("Snapshot size rejected");
  }
  std::string r((size_t)size.QuadPart, 0);
  DWORD n = 0;
  BOOL ok = ReadFile(h, r.data(), (DWORD)r.size(), &n, nullptr);
  if (!ok || n != r.size())
    throw std::runtime_error("Incomplete snapshot");
  return r;
}

inline std::string read_file(const std::wstring &path) {
  HANDLE h = CreateFileW(path.c_str(), GENERIC_READ,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         nullptr, OPEN_EXISTING, 0, nullptr);
  if (h == INVALID_HANDLE_VALUE)
    throw std::runtime_error("Cannot read snapshot, Win32 " +
                             std::to_string(GetLastError()));
  try {
    auto bytes = read_file(h);
    CloseHandle(h);
    return bytes;
  } catch (...) {
    CloseHandle(h);
    throw;
  }
}

// Keep only a 32-byte fingerprint in Office, rather than another full document.
#include <wincrypt.h>
class SnapshotHash {
  HCRYPTPROV provider = 0;
  HCRYPTHASH hash = 0;
public:
  SnapshotHash() {
    if (!CryptAcquireContextW(&provider, nullptr, nullptr, PROV_RSA_AES, CRYPT_VERIFYCONTEXT))
      throw std::runtime_error("Snapshot hash unavailable");
    if (!CryptCreateHash(provider, CALG_SHA_256, 0, 0, &hash)) {
      CryptReleaseContext(provider, 0);
      throw std::runtime_error("Snapshot hashing failed");
    }
  }
  SnapshotHash(const SnapshotHash &) = delete;
  ~SnapshotHash() {
    CryptDestroyHash(hash);
    CryptReleaseContext(provider, 0);
  }
  void add(const void *data, DWORD size) {
    if (!CryptHashData(hash, static_cast<const BYTE *>(data), size, 0))
      throw std::runtime_error("Snapshot hashing failed");
  }
  std::string finish() const {
    BYTE bytes[32];
    DWORD size = sizeof(bytes);
    if (!CryptGetHashParam(hash, HP_HASHVAL, bytes, &size, 0) || size != sizeof(bytes))
      throw std::runtime_error("Snapshot hashing failed");
    return std::string(reinterpret_cast<char *>(bytes), size);
  }
};
inline std::string digest(const std::string &data) {
  SnapshotHash hash;
  hash.add(data.data(), static_cast<DWORD>(data.size()));
  return hash.finish();
}
inline DWORD snapshot_size(HANDLE file) {
  LARGE_INTEGER size;
  if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 128*1024*1024)
    throw std::runtime_error("Snapshot size rejected");
  return static_cast<DWORD>(size.QuadPart);
}
template <typename Consume>
void read_blocks(HANDLE file, DWORD remaining, Consume consume) {
  std::vector<BYTE> block(256 * 1024);
  while (remaining) {
    DWORD count = 0;
    DWORD wanted = (std::min)(remaining, static_cast<DWORD>(block.size()));
    if (!ReadFile(file, block.data(), wanted, &count, nullptr) || count == 0)
      throw std::runtime_error("Incomplete snapshot");
    consume(block.data(), count);
    remaining -= count;
  }
}
inline std::string digest_file(HANDLE file) {
  SnapshotHash hash;
  read_blocks(file, snapshot_size(file), [&](const BYTE *data, DWORD count) { hash.add(data, count); });
  return hash.finish();
}
