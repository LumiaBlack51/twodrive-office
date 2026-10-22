# 0.2.0 验证记录（含 0.1.0 历史） · 2026-10-14

开发目录：`/home/lumia/workspace/Projects/codex/office-extension`。TwoDrive 源码只读回顾自 `/home/lumia/software/twodrive` 的 `dev` 分支；未修改、重启或重新配置真实 TwoDrive。

## 实际环境与测试

| 范围 | 实际结果 |
| --- | --- |
| 保存策略、桥接协议、回归用例 | 25 项 Python 测试通过：挂载断开、同名文档、符号链接、外部修改、原文件保护、加密/宏格式拒绝、缺失媒体拒绝、认证与原生 Origin、旧快照延迟返回时不能覆盖新版本、按需生命周期和继承监听 socket 等 |
| ONLYOFFICE 调度与保存状态 | 18 项 Node 测试通过：目录内自动开启且未修改不写入、目录外/未命名/拒绝挂载不开启、暂停后不重开、合并修改、最大延迟、失败重试、关闭/替换会话后不能确认、保存期间又有修改、避免重入和重复写入、保存点与恢复检查点、同一撤销点中的新修改、原生保存/嵌入编辑/单元格编辑保护、未知 SDK 保留提示 |
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
node --test tests/onlyoffice*.test.js
python3 scripts/build.py
/usr/bin/python3 tests/libreoffice_integration.py
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich --in-place
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format xlsx
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format pptx
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format pptx --in-place --race
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format xlsx --in-place
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --untitled
```

ONLYOFFICE 重现脚本另需 `websocket-client`、Flatpak、xauth 和 xdpyinfo。开发时 Xvfb 从系统软件源下载后解包至忽略的 `.research/`，没有安装或变更系统软件包。

## ONLYOFFICE 星号与关闭提示修复阶段 · 2026-10-14

此节记录先前的保存状态修复；之后的后台启动、自动开启和 0.2.0 验证见末节。

旧实现只让 bridge 将当前快照写入文件，没有更新 Desktop 的用户保存点。因此原位保存成功后，编辑器仍认为文档有修改，标题星号与关闭保存提示会残留。这是已确认的旧实现缺陷；此前 README 中关于原位保存可能保留星号的说明已被本轮修复取代。保存副本时，原文档没有被写回，保留其未保存提示是预期行为。

原位保存现在仅在收到当前会话、正确目标的 `saved_local` 响应后确认。关闭/重新开启开关会使旧请求失效；另存为其他源路径也不能确认旧文件。确认前同步重新读取 native 模型快照，与实际提交的快照比较；有新内容则保留/刷新未保存提示并安排下一次保存，不会将新编辑误标为已保存。失败保留待保存状态。每次导出提交使用独立递增版本，避免“服务已经写入但响应丢失”后重试同一版本而永远无法取得确认。

状态适配器只更新 `UserSavedIndex` 与 `ForceSave`，并通过编辑器的 `SetDocumentModified` 刷新编辑器和真实 Desktop 原生窗口。供本机恢复/增量流水线使用的 `SavedIndex` 不随外部写入推进；不调用整个 `Reset_SavedIndex`，也不伪造原生保存结束回调。Native 适配与公开 web 插件 API 仍然不同：编译后的私有字段按完整的保存点函数结构识别，并核实字段类型及原生保存、嵌入编辑、单元格编辑的状态保护；不依赖某一编辑器的压缩字段名。结构不匹配时继续保留星号，并在面板明确报告无法更新编辑器保存状态。参考 [Desktop 演示编辑器保存点与原生修改通知的 SDK 源码](https://github.com/ONLYOFFICE/sdkjs/blob/72b0421c0bbf9d01eed9cf14834ae47eb2df1b50/slide/Local/api.js)。

本轮实际版本仍为本机 Flatpak 9.4.0，安装提交 `5806f4728bd3e2bc5314c2c0dfbc59d10dcac33516b5536ac12ceae2e79f98c1`。未验证其他 SDK 构建/版本或 ODF 保存状态，不将这些结果推广至其他版本。

实际验证结果：

- Python 25 项、Node 13 项通过；安装包构建通过。
- 真实 PPTX、含 PNG 的 DOCX、XLSX 原位写入当前内容后，`isDocumentModified()` 与转发到真实 Desktop 的原生修改通知均为 false。关闭开关后不再写入；撤销离开保存点会变为未保存，重做回到保存点会恢复；通过私有 Xvfb 上的 `WM_DELETE_WINDOW` 关闭实际应用窗口，文档正常退出且没有保存确认阻塞。
- PPTX 延迟 bridge 写入成功响应，期间再次执行实际 SDK 编辑：旧确认不会发出清除新修改的通知，新版本随后自动写入并确认。另模拟成功写入后丢失 HTTP 响应：保留警告，未继续输入也能退避重试并恢复确认。
- PPTX 保存到指定副本目录时，源文件字节不变，编辑器与原生未保存状态仍为 true，原生关闭请求仍保护未保存的原文档。面板明确显示“已保存到副本”，不宣称原文件已保存或云端上传完成。
- `--format pptx --untitled` 的实际未命名演示文稿也能保存副本，仍保留编辑器与原生未保存状态及关闭保护。
- 修改隔离 profile 内的插件脚本后，只关闭、重开插件面板便加载了新脚本；文档和编辑器没有重启。

所有办公文件和挂载目录均为临时测试数据，未使用真实 TwoDrive 挂载或用户工作文件。编辑器运行在关闭 X11/Wayland socket 的独立 Flatpak 环境中，使用认证 TCP Xvfb；本轮没有操作前台文档。重现脚本同时修正了调试端口日志解析和完整进程组清理：不能只等待 Flatpak 启动器退出便认为编辑器子进程已经退出；仅回收本次测试的独立进程组，并验证无存活成员。

验证完成后，已将 `autosave.js`、`native-state.js`、`plugin.js`、`index.html` 四个文件更新到本机现有 Flatpak 插件目录，并与本轮构建包逐字节核对。原文件备份于开发目录的 `.test-state/onlyoffice-saved-state-backup-c05871a3`；连接设置 `settings.js` 字节保持不变。安装没有重启编辑器或桥接、没有修改办公软件偏好或操作当前文档。当前已打开的旧面板需由用户关闭并重新打开，再重新开启自动保存；不必重启文档或 ONLYOFFICE。

## 0.2.0：目录内文档自动开启 · 2026-10-14

ONLYOFFICE 配置改为公开配置支持的 `system` 类型，在打开文档时加载隐藏的保存控制器。控制器请求 bridge 创建会话后，只接受经规范化路径、挂载和可写性检查确认的 `in_place` 结果；目录外返回的副本会话立即关闭，不导出、不写入副本；未命名文档不创建自动会话。不能只按字符串前缀判定目录，符号链接和相邻目录仍由 bridge 的既有保存策略处理。参考 [插件类型](https://api.onlyoffice.com/docs/plugins/configuration/types)。

**TwoDrive → 自动保存** 工具栏入口通过公开 `PluginWindow` 接口打开独立控制面板，与每文档唯一的隐藏控制器同步状态和用户操作。关闭面板不会卸载控制器或关闭自动保存。9.4 的实际前端仍要求窗口参数包含 `isVisual: true`；本轮保留该兼容参数并实际验证面板显示及消息往返。手动暂停后，内容就绪事件和轮询不会重新开启；重新打开文档则按目录重新判断。

LibreOffice 在跟踪/加载文档时执行同样的目录检查，经 `enable()` 的挂载、格式、只读和加密检查后开启；原位文件已保存且未修改时清空初始待保存任务。手动关闭有当前文档级标记，正常手动保存事件不会覆盖该选择。目录外、未命名文档继续使用用户主动选择的副本流程。

两种适配器都避免仅因打开未修改文档而导出或覆盖。ONLYOFFICE 轮询与修改事件以编辑器的修改状态为依据；实际测试用 SDK Builder 命令修改内容，并调用界面状态刷新来提交对应的修改通知。完全绕过 SDK 修改状态的外部脚本赋值不属于本轮触发保证，可显式刷新修改状态或使用“立即保存”。

最终验证：

- Python 25 项、Node 18 项通过。新增覆盖：未修改文档启用但不导出/写入、bridge 判定为副本时拒绝自动开启、未命名/挂载拒绝时不开启、手动暂停后不被内容就绪事件和轮询重开。
- ONLYOFFICE Flatpak 9.4.0：PPTX（含延迟/丢失响应测试）、带 PNG 的 DOCX、XLSX 都在没有调用 `asc_pluginRun` 的情况下自动加载控制器、开启原位保存。修改前原文件字节不变；修改后实际写入、清除保存标记、撤销/重做及原生关闭通过。
- 目录外和未命名 PPTX 在打开时保持关闭且不生成任何副本；手动开启后仍可保存指定副本并保留原文档关闭保护。实际工具栏回调打开的面板显示了控制器开关状态；面板关闭后自动保存继续，面板暂停会停止后台保存，后续内容就绪事件不会重开。
- LibreOffice 26.2.5.2：本轮 `0.2.0.oxt` 实际注册于独立 profile，`--headless` 打开临时挂载内 ODT 后自动开启；未修改文件字节和 mtime 保持不变。真实修改自动原位保存，手动暂停在手动保存后仍保持；目录外/未命名文档未自动开启，Writer/Calc/Impress 副本回归通过。
- 测试脚本按运行时版本选择 OXT，修正了最初仍指向旧 `0.1.0.oxt` 的测试路径；上面的 LibreOffice 结论来自修正后实际执行的新包。所有启动的编辑器均采用隔离环境和临时文档；没有启动前台编辑器或使用真实 TwoDrive 文件。

构建脚本从运行时版本生成包名，并拒绝运行时、LibreOffice 和 ONLYOFFICE manifest 版本不一致的构建。0.2.0 继续是开发预览版；SDK、ODF、复杂对象和真实云端上传的边界沿用上述记录。

验证后已安装本机 0.2.0：`unopkg list org.twodrive.office.autosave` 确认 LibreOffice 扩展及组件注册为 0.2.0；ONLYOFFICE 的七个程序/界面/配置文件与构建包逐字节一致，已有连接设置保持原字节。ONLYOFFICE 旧程序文件备份于 `.test-state/onlyoffice-before-0.2.0-ed1b91a7`。安装未启动/重启办公软件或 bridge，未操作当前文档；新的后台启动配置在用户下次自行启动办公软件后生效。


## 0.2.1：重复快照写回与 Desktop 10 插件路径 · 2026-10-21

本次用户报告 TwoDrive 存在数十 GB 缓存，并说明同时用过网页 Microsoft Office 和本地 ONLYOFFICE。TwoDrive 调查在其仓库 TD-20261021-01 留档；本仓库只处理插件机制，不把主缓存回收缺口归因于办公软件本体。

确认的问题和证据：

- 0.2.0 的原生快照 JSON 含每次递增的 HTTP revision，使 AutoSave 整段字节比较不能识别实际内容相同的请求。桥接没有模型去重。新增相同模型/不同 revision 回归在旧代码失败：convert 被调用 2 次，预期 1 次。
- 修正模型比较后，真实 Desktop 10 的副本模式空闲测试仍失败。仅用合成 PPTX 对比两次包内容，唯一不同部分是 docProps/core.xml 的 dcterms:modified（导出推进约 5 秒），并非新的幻灯片内容。现增加第二层完整包内容摘要：保留全部文本/幻灯片、关系、媒体和其他属性，仅忽略 ZIP 容器时间及这一项导出时间。不能按文件长度、页数或历史游标去重；同一撤销点中的编辑仍受完整内容比较保护。
- 当前本机 Flatpak 已于 10 月 19 日由 9.4.0 升至 10.0.0（安装提交 0041502ece1ee4b3be92889b1503cb05b5c19be737413bd9a810440499754b83）。隔离进程 GetInstallPlugins 实际只枚举新的 data/sdkjs-plugins 用户路径；旧安装路径为空，插件没有加载。安装器覆盖 9.4 与 10.0 的两种目录布局，显式目录覆盖参数继续只使用指定路径。官方插件目录文档尚列旧 Linux 布局，故这里以实际原生注册结果为证，不宣称所有平台或未来版本均相同。

修复将请求排序与内容识别分开：模型/媒体摘要相同可省去转换，转换产物内容相同可省去原子写回；两条无操作路径仍重新验证挂载、目标、源文件及外部修改指纹，失败不确认保存。序号不会因去重失去顺序保护，丢失确认后的新序号仍可取得 saved_local。

当前已完成：新增合成回归旧实现失败，第一层修复后通过；实际 PPTX 原位保存（含延迟确认/丢失响应）、带图片 DOCX、保存点/撤销重做和原生关闭保护均在隔离 Desktop 10 通过。副本模式时间戳问题补修复后，真实 PPTX 副本与 XLSX 原位保存均通过 12 秒空闲检查，文件内容/mtime/inode 不变；实际保存、开关、撤销/重做与关闭保护继续通过。31 项 Python、18 项 Node 和 0.2.1 插件包构建通过。应用测试使用认证 TCP Xvfb、独立 XDG 配置/数据及实例端口中的测试资料及临时模拟挂载，没有编辑用户文档或执行真实 OneDrive 上传。

一次并行运行两个应用测试造成原生共用调试端口竞争，其中一个未找到调试目标，未通过文档身份门控、未执行保存。测试现用本机锁串行执行，清理仅针对各自独立进程组；没有关闭正常桌面上的编辑器。旧 9.4 验证保留为历史，不冒称本次重新运行了 9.4。

最终 31 项 Python、18 项 Node、0.2.1 包构建通过；最终实际 PPTX 原位/副本、带图片 DOCX 与 XLSX 的空闲稳定及保存保护均通过。修复源提交 `115ccdb3e115c903820a0dbd4b2f5c9fcfa1f0c7` 已提交 [PR #1](https://github.com/LumiaBlack51/twodrive-office/pull/1)，该源码的 [Office checks](https://github.com/LumiaBlack51/twodrive-office/actions/runs/39584311891) 已通过。Release 尚未发布；发布后补充交付记录。安装时保留连接凭据，不启动编辑器；用户自己的旧编辑会话须自行正常重开后加载新插件。


### 0.2.1 交付补记 · 2026-10-21

[PR #1](https://github.com/LumiaBlack51/twodrive-office/pull/1) 已合入 main，合并提交 `09a27bc34734b01bd393ffde01d951e8ba9cc0c8`；不可移动的注解标签 v0.2.1 指向已整合的 `a629d914ec9efdfb8b8d1afaa93512f37ab0b9f0`，两者 Git 树一致。最终源的 Office checks 成功。

[0.2.1 Preview Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.2.1) 已公开；发布前后统一门控校验了全部四项附件（OXT、plugin、SHA256SUMS、verification.json）的 state/size/远端 SHA-256 及注解标签解析目标。它保持预览版，不替换 TwoDrive 稳定渠道。构建来自已检查的干净源码；没有宣称 ZIP 时间相关的本机构建字节与 CI 重打包完全一致。

用户授权修复、发布和清理后的本机部署：桥接 MainPID=0 时从已核实发布包更新用户 runtime，并更新 9.4/10.0 两种 ONLYOFFICE 插件目录布局。配置及连接文件字节保持一致；未启动编辑器、没有更改当前文档或重启服务。后续正常打开文档才加载新插件；LibreOffice 的用户 profile 未由此轮修改。

真实 OneDrive 同步、网页 Microsoft Office 和本地编辑器的协作往返仍未执行；复杂真实 PPTX 与完整文档样本集合仍是验证边界。测试只使用合成临时文档。此前源快照中的“待发布”是当时状态，现由本补记更新，保留先前证据。

## 0.2.2：大课件保存后的错误待保存状态 · 2026-10-22

用户报告昨日升级后，PPTX 修改不能自动保存，同时打开 Office 文档期间 TwoDrive 持续显示同步。后续明确表现是“修改后星号和关闭提示一直在”，不将其记录成用户已执行手动保存。已逐字节核实本机两个插件目录及桥接 runtime 都与 0.2.1 一致；没有回退昨日的去重修复。

### 根因与旧代码证据

从现有本地缓存只读复制约 21 MB、163 页、293 个图片资源的 PPTX 到隔离测试目录。原始工作文件及真实挂载没有用于保存测试。0.2.1 在真实 Desktop 10 中开启并写入后，面板反复显示“上一版本已本地保存；新修改仍待保存”及“正在保存当前内容”，整轮保存断言失败。

在没有新编辑的情况下连续读取两个原生快照，解码后均为 2,944,592 字节，仅有一个字节不同，位于 Core 的修改时间，秒数随导出推进。SDK 的 [CCore.toStream](https://github.com/ONLYOFFICE/sdkjs/blob/9d2138866d929be32f1d107e8b5bfae36156e097/common/Drawings/Format/Format.js#L14341) 每次将当前时间写入 Core 属性 5；[PPTY 写入器](https://github.com/ONLYOFFICE/sdkjs/blob/9d2138866d929be32f1d107e8b5bfae36156e097/common/Shapes/SerializeWriter.js#L1076) 用主表项 2 定位 Core。这与本机快照的实际结构及差异位置相符。

昨日修复在桥接端比较完整转换产物并排除导出时间，已经阻止相同内容反复替换原文件；编辑器保存确认仍按原生快照字符串精确比较，遗漏了同一时间因素。小文档可能在同一秒完成确认，大文档的媒体导出与转换跨秒时则稳定暴露。此次为旧修复遗漏的相邻机制，没有证据表明昨日去重逻辑回归，也不能根据待保存提示认定用户先前编辑内容已经丢失。

### 修复与保护

保存确认在识别的 PPTY v1 内校验长度、30 项寻址表、主签名、唯一且有序的表地址、Core 记录边界和属性结构，只忽略 Core 属性 5 的 ISO 导出时间。幻灯片文本（包括文本中的日期）、媒体引用、标题/作者等其他属性及所有剩余字节继续比较；未知/损坏结构回退精确比较并保留未保存提示。没有修改编辑器时钟、模型属性、恢复检查点或实际写出的文件时间。

原生保存进行中、嵌入编辑、未提交单元格输入及保存期间的新编辑仍保留警告；失败继续重试。0.2.1 的模型/媒体与完整转换包两层去重、挂载与目标复验及请求顺序保护保持不变。此逻辑仍属于 Desktop 原生适配器，不是公开 web 插件 API 的保证。

### 实际验证

- Python 31 项、Node 21 项通过；新增跨秒保存确认、同一撤销点中的真实内容/媒体/属性/日期文本变化及未知/损坏结构回归。0.2.2 OXT/plugin 构建通过，所有 manifest 与 runtime 版本一致。
- Flatpak 10.0.0（原生 10.0.0.169）：上述大课件的临时副本原位自动保存通过，163 页与全部 293 个媒体资源的内容摘要/重数保持相同，实际新增编辑写入，编辑器与 Desktop 星号清除。首个成功确认额外延迟 1.2 秒，保证跨越导出时钟。
- 12 秒空闲检查中文件内容、mtime、inode 保持不变；保存中新增编辑不被旧确认清除，新修改随后保存；成功写入但 HTTP 确认丢失后，无继续输入也能重试并清除提示；关闭开关、撤销/重做和原生窗口关闭保护均通过。
- 应用运行在关闭 X11/Wayland socket、认证 TCP Xvfb、独立 XDG 数据/配置和原生实例端口中，交互前核实真实进程环境与测试文档身份。只清理专用进程组；没有启动用户桌面编辑器或保存真实 TwoDrive 文件。
- 未重新验证旧 9.4、所有 Office 格式或真实 OneDrive 上传。本次确认修复了大课件的保存确认循环，不将本地副本测试描述成云端端到端同步或用户原始编辑历史恢复。

复现命令（`--fixture` 始终先复制到临时目录，只读输入）：

```bash
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format pptx --in-place --slow-save --race --fixture /path/to/local-read-only-input
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --format pptx --slow-save
```

### TwoDrive 常亮同步的只读检查

排查时用户的编辑器已关闭，`activity.json` 的 active 为空，文件表没有 writing/dirty/uploading 项，目标课件为 cached；这只表明当时没有在途活动，不能复原打开期间每个活动项或证明云端字节一致。

现有 TwoDrive 的 `filesystem/fuse.rs::open` 将 O_RDWR/O_WRONLY 打开交给 `create_overwrite_upload`，`filesystem/writes.rs` 随即建立 kind=upload 的 ActivityGuard，持有句柄期间可以一直保留；托盘将任何这样的活动显示为 syncing。故即使尚未有新写入，Office 的可写文件或锁文件句柄也能造成常亮，图标本身不能证明持续上传或再次堆积副本。这是依据现有代码的机制解释；没有在真实挂载重新打开用户文件重现活动条目，也没有在本轮修改或重启 TwoDrive 本体。

最后的真实 PPTX 副本回归同样通过：额外延迟首次确认后保存成功，空闲 12 秒文件内容/mtime/inode 不变，源文件字节不变，暂停后停止写入，原生关闭继续保护未保存的原文档。最终 31 项 Python、21 项 Node、构建及 diff 检查通过。

本机已安装 0.2.2：从本轮构建包更新 9.4/10.0 两种插件目录，仅原生状态脚本与 manifest 有变化；runtime 仅更新版本标识，共替换 5 个文件。程序文件全部与包/源码逐字节核对，现有设置和两个连接文件摘要保持不变。仅保留这 5 个旧文件的小范围私有回退材料，没有复制用户缓存。安装时编辑器均已关闭、bridge MainPID=0，没有启动或重启编辑器、bridge、TwoDrive，也没有更改 Office 偏好。

安装后复查无残留测试进程或临时测试目录，用户课件的缓存路径、大小和修改时间与排查开始一致，TwoDrive 活动及 writing/dirty/uploading 数均为零。新插件在下次正常打开文档时加载。0.2.2 尚未公开发布。

### 用户编辑后的只读缓存核验与传输方案

用户随后确认实际修改多次均成功，并要求暂不修改 TwoDrive。2026-10-22 16:42（Asia/Shanghai）只读核验当前 SQLite、缓存和活动记录；没有运行 prune、删除缓存、修改配置、重启服务或另造挂载文件。检查当时的 81 个普通缓存文件共 131,555,061 字节，逐个计算 SHA-256。

- 16:35 以后的缓存只有当前课件一份，21,481,006 字节，状态 cached、无上传声明；修复前课件的旧缓存已不存在。未发现这轮编辑留下的重复 PPTX、Office 保存临时记录、转换临时目录或 ONLYOFFICE 恢复目录条目。retired_cache、活动列表及 writing/dirty/uploading/conflict 数均为零。
- 另有 11 份没有当前文件表/上传声明/待删除表引用的旧缓存，共 26,452,799 字节，mtime/ctime 为 10 月 18—21 日。其中文件内容完全重复的只有六份空文件及两份各 59,715 字节的小文件；旧 PPTX 为 20 页，并非本轮 163 页课件。来源及恢复价值未在本轮排除，全部保留；不能称全机没有历史残留或都可安全删除。
- 这里核验的是用户自行编辑后的本地结果，未进行真实 OneDrive 下载比对或抓包，不以 cached 状态断言云端字节一致。

微软的 [AutoSave 说明](https://support.microsoft.com/en-us/office/collab-files/what-is-autosave)说明它面向 OneDrive/SharePoint 上的文档定期保存；[Office 集成配置](https://learn.microsoft.com/en-us/sharepoint/ideal-state-configuration#office-integration)明确说明差量同步可以只传变化，而非每次传整个文件。这取决于受支持的应用/云端集成，不能推广到任何 Office 文件路径或任意第三方同步客户端。

现有插件导出完整办公文件并原子保存到本地目标；TwoDrive 的 `onedrive/upload.rs::upload_session_file` 从初始偏移顺序传完整文件，已确认片段可断点续传。[Graph createUploadSession](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0)要求完整上传后提交，分片与续传不是对上一文件版本的差量补丁；[普通内容上传](https://learn.microsoft.com/en-us/graph/api/driveitem-put-content?view=graph-rest-1.0)同样提交文件二进制内容。

本轮决定不增加传输机制修改。合并短时间修改、跳过未变化内容、避免重复写入仍有价值且已实现；若以后要降低真实上传字节，应另行评估云端协议与同步后端，并验证冲突、离线及恢复语义。在用户要求不修改 TwoDrive 的范围内，不能仅改插件就宣称实现微软式差量上传，也不以跳过部分 Graph 字节范围冒充差量保存。

后续逐项识别这 11 份旧缓存：一份 20,020,108 字节 PPTX 与 10 月 19 日保全的恢复材料逐字节一致，历史数据库和清理保护名单均有记录；两份各 59,715 字节的 PNG 是同一图片的上传/下载缓存，SHA-256 相同；两份约 3.16 MB 的 PDF 为同一 68 页网络编程课件的不同内容，文档 ID/首屏主题一致但全文提取摘要与文件摘要不同；六份零字节缓存的身份对应三个 PDF 目标和三个回收站信息文件，当前/历史记录的实际内容已使用其他缓存路径，不是六个空白用户文档。完整私人文件名与映射仅在本机，不加入公开源码。

保留原因可由昨日维护资料核实：上述 PPTX 是显式保护项，其余缓存当时均不足旧孤立缓存暂存流程要求的 24 小时，因此没有进入当次旧文件处理。创建上传缓存后切换代次的旧回收缺口会遗留这些无现表引用的路径；新维护只处理当前可释放记录及显式 retired_cache，不扫描追删任意来源不明的旧文件。具体 PNG/PDF 每次解除引用和空壳创建的应用调用流水未保留，不根据文件名前缀臆断是某一次 Office 操作造成。此次仍全部只读、未删除。

差量同步的可行性边界：微软的 [MS-FSSHTTP 概述](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-fsshttp/6d078cbe-2651-43a0-b460-685ac3f14c45)公开了由客户端/服务器配合上传变化、跟踪分区和处理锁/合并冲突的机制。要采用这条路线，必须先验证目标账号、文件和认证可使用兼容的端点，再实现版本基线、分块/文档模型、条件提交、冲突及离线恢复；本机 OneDrive 是否向第三方开放适用端点尚未实测。自建中转服务可减少客户端到中转的传输，但中转若仍调用现有 Graph 内容 API，最后一段仍是完整文件上传，不能称为原生 OneDrive 差量保存。此处是条件评估，没有引入新的同步实现。

### 0.2.2 GitHub 交付补记

修复提交 `260b0e9ebebfa8e0033b29a516a7f1ddc40d3ad6` 随 [PR #3](https://github.com/LumiaBlack51/twodrive-office/pull/3) 合入 main，合并提交 `428e1ab8e779a17f30dabab3759e5002409e7622`。[PR checks](https://github.com/LumiaBlack51/twodrive-office/actions/runs/39750106495) 和[合并源 checks](https://github.com/LumiaBlack51/twodrive-office/actions/runs/39750229712) 均成功；不可移动的注解标签 v0.2.2 指向此合并源。

[0.2.2 Preview Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.2.2) 已于 2026-10-22 16:50（Asia/Shanghai）公开。OXT/plugin 来自上述合并源的 Actions 制品，先验证制品摘要和原始包校验值，再逐项与源码文件字节核对。新增的 verification.json 说明实际应用测试、CI 来源及未验证范围；SHA256SUMS 覆盖两包和验证摘要。草稿公开前及公开后均核实完整四项附件的 uploaded 状态、大小、远端 SHA-256、注解标签对象与解析提交；保持预发布状态，不冒称稳定渠道。

本机先前安装的程序文件已与此次 CI 包的源码内容一致；ZIP 容器时间不同不等于程序不一致。没有再次安装或启动编辑器，也没有修改 TwoDrive。此前段落“尚未公开发布”保留为当时状态，由本补记更新。
