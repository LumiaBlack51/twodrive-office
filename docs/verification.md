# 0.1.0 验证记录 · 2026-10-14

开发目录：`/home/lumia/workspace/Projects/codex/office-extension`。TwoDrive 源码只读回顾自 `/home/lumia/software/twodrive` 的 `dev` 分支；未修改、重启或重新配置真实 TwoDrive。

## 实际环境与测试

| 范围 | 实际结果 |
| --- | --- |
| 保存策略、桥接协议、回归用例 | 25 项 Python 测试通过：挂载断开、同名文档、符号链接、外部修改、原文件保护、加密/宏格式拒绝、缺失媒体拒绝、认证与原生 Origin、旧快照延迟返回时不能覆盖新版本、按需生命周期和继承监听 socket 等 |
| ONLYOFFICE 调度 | 5 项 Node 测试通过：合并连续修改、最大延迟、失败重试、导出期间关闭、导出期间又有修改、避免重入和重复写入 |
| LibreOffice 26.2.5.2 | `.oxt` 实际注册并加载于独立 profile。通过工具栏 dispatch → 真实修改事件 → UI 线程回调 → Writer 原位保存；关闭开关后停止；外部/未命名文档保存副本；检测副本外部冲突 |
| LibreOffice Writer / Calc / Impress | 真实当前编辑内容分别导出至 ODT、XLSX、PPTX 副本，保留原文件字节、原 URL 和未保存标记；具体命令见下方 |
| ONLYOFFICE Flatpak 9.4.0（内核 9.4.0.129） | 插件实际加载到编辑器。DOCX 带 PNG 图片的文档通过指定目录副本与原位保存；图片原始字节保持一致；关闭开关停止写入；外部原文件未被修改 |
| ONLYOFFICE 电子表格 / 演示文稿 | XLSX 单元格修改和 PPTX 新增文本形状保存为指定目录副本，原文件保持不变；关闭开关停止写入 |
| ONLYOFFICE 未命名文档 | `--new:word` 创建的真实未命名文档被识别为无原位置，修改保存为带独立会话标识的副本；关闭开关停止写入 |
| 真实 OneDrive 上传 | 未执行。办公软件测试均使用临时目录模拟挂载；不能由这些测试推断云端上传已完成 |

LibreOffice 的 Writer 测试使用 ODT；Calc/Impress 测试使用实际 XLSX/PPTX。ONLYOFFICE 的 DOCX/XLSX/PPTX 已经过上述实际应用测试；ODF 转换、复杂嵌入对象、音视频、完整大型文档样本集合以及其他应用版本仍需进一步验证。拒绝加密/宏文件的策略已测试，未用用户的加密文档运行真实应用测试。

## 发现并修正的机制

1. `XStorable2.storeSelf` 不接受 `Silent` 参数。真实 LibreOffice 返回 `IllegalArgumentException: Unexpected MediaDescriptor parameter: Silent`。改用可接受的 `InteractionHandler` 并在后台拒绝交互请求；真实原位保存测试通过。
2. 早期 Impress 测试通过 UNO 给 TextShape 赋值，但该后台赋值没有将文档标为已修改，测试对修改标记的假设错误。测试现在显式模拟用户编辑的修改状态，导出适配器也明确保留导出前的标记；不将早期断言失败误称为 `storeToURL` 清除标记的已确认故障。
3. ONLYOFFICE 公开 `GetFileToDownload` 在本机本地文档上走向不存在的 Docs 服务，不能完成导出。插件改用经过能力检查的 Desktop 原生模型快照及本机 x2t；通过真实编辑内容验证。
4. `callCommand` 的命令上下文隔离编辑器全局数据，且 9.4 对象返回值会变成 `undefined`。原生适配器明确使用本地 Desktop frame 的接口，不把 web 插件 API 当作文件系统接口。配置要求 9.4+，实际只核实了 9.4.0。
5. x2t 的临时目录必须先创建，否则返回 88。已在私有转换目录中创建，转换器不会启动 Qt/CEF 编辑器。
6. Native SDK 符号经过压缩改名，不能按原始变量名读取媒体表。适配器识别当前文档的媒体根，读取快照中引用的缓存媒体；发布前校验包内关系。缺少关联内容时拒绝写入，带图片的实际应用测试通过。
7. 网络超时可能让早先的快照比后来的快照更晚完成。桥接服务记录提交版本，拒绝旧版本覆盖较新版本；用受控并发转换顺序验证。

## 前台隔离问题及修正

最初仅按常规安装路径判断 ONLYOFFICE 未安装，结论错误；随后确认其通过 Flatpak 安装。

Flatpak 强制覆盖 `flatpak run --env` 传入的 DISPLAY/XDG 路径。第一次测试设置实例端口时写入了正常配置的 `instAppPort`，已撤回本次设置的端口键；后续将 XDG 变量放在沙箱内部 shell 设置，并检查独立配置中的实例端口。在显示隔离彻底修正前，测试窗口曾出现在用户桌面，已停止并关闭；不能把早期测试说成从未干扰前台。

后续测试关闭 X11/Wayland socket 权限，使用带随机认证 cookie 的 TCP Xvfb 显示。在交互前核实实际 DesktopEditors 进程的 DISPLAY、XDG_CONFIG_HOME、XDG_DATA_HOME、私有实例端口；去除正常会话管理/DBus 环境。清理只针对本次启动的进程组。重现脚本不会启动正常显示上的编辑器。

## 本机安装检查 · 2026-10-14

经用户明确要求，插件现已安装到日常用户配置。LibreOffice 的 `org.twodrive.office.autosave` 0.1.0 及其 Python 组件、菜单配置均显示已注册；ONLYOFFICE 插件安装到 Flatpak 用户插件目录，文件及连接配置已核对。

首次安装发现 systemd 不接受带字面双引号的 `WorkingDirectory`。安装脚本改为 `%h/.local/lib/twodrive-office`，在启动前验证 unit，并在报告成功前验证认证 HTTP 响应及实际活动状态，避免误报安装成功。脚本也为本地运行时和直接安装的插件保留 MIT 许可文件。

初次安装时 `twodrive-office-bridge.service` 曾设置为登录自启动且处于 `active/running`，Nice=10、I/O 优先级=7，仅监听 `127.0.0.1:19836`。原生插件 Origin 的认证请求通过，无凭据请求被拒绝；设置文件和连接文件权限为 0600。安装结束时没有 ONLYOFFICE 或 LibreOffice 编辑器进程；没有打开文档、开启自动保存或执行真实文件保存。

随后按用户要求取消了服务的登录自启动，改为 systemd socket activation。现在服务为 `static/inactive`、MainPID=0，只有现有 systemd 用户管理器保留启用的 loopback socket，不新增常驻 Python 或监控进程。ONLYOFFICE 插件的首次请求唤醒桥接；没有进行中的请求、最近认证请求超过 30 秒且当前用户的 ONLYOFFICE 进程已退出时，桥接正常退出。正在转换/保存的请求阻止退出；编辑器仍开着时保留会话，避免最小化或后台标签页的定时器节流导致会话丢失。LibreOffice 不需要桥接。

已在本机真实服务单元上连续验证两轮“认证请求 → socket 唤醒新 Python 进程 → 无编辑器时自行退出 → 再次唤醒”；两轮均通过，最终 MainPID=0。新增回归覆盖长期请求不能被空闲退出打断、未认证流量不能重置空闲时间、只保留当前用户 ONLYOFFICE、继承监听 socket 和原生 CORS。此轮没有启动任何编辑器、没有打开用户文档，也没有写入 TwoDrive 文件。

## 重现命令

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/onlyoffice.test.js
python3 scripts/build.py
/usr/bin/python3 tests/libreoffice_integration.py
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich --in-place
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format xlsx
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format pptx
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --untitled
```

ONLYOFFICE 重现脚本另需 `websocket-client`、Flatpak、xauth 和 xdpyinfo。开发时 Xvfb 从系统软件源下载后解包至忽略的 `.research/`，没有安装或变更系统软件包。
