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


## 0.3.0：本地持久保存与定时挂载提交 · 2026-11-01

用户再次报告空开关闭也上传、编辑时多次全量上传。0.2.1/0.2.2 的内容去重仍有效，但只覆盖插件写回；TwoDrive 的可写 open/release 仍可把未写入句柄排入上传。另一个独立原因是插件每秒直接保存挂载，真实变更每次仍会触发全文件传输。

### 行为与恢复边界

修改先以 0600 文件与原子清单、文件/目录 fsync 保存在挂载外的 XDG_STATE_HOME/twodrive-office/pending。目标每 180 秒提交最新本地版本，连续输入不会无限推迟；关闭/暂停或“立即保存”提前提交。定时器仍复验挂载、路径/符号链接和目标指纹，失败保留内容并退避。只在本地持久保存收到确认且原生快照仍相同时清除原位模式保存标记。副本模式保留原文档提示。目标原文件可落后本地版本一个周期；Ctrl+S 可绕过插件间隔。

关闭时提交失败，bridge 保留队列并继续重试。崩溃后的清单会保留并阻止同目标会话覆盖；只有所有 Office 进程退出后，按需 bridge 或显式 CLI recover 才允许恢复。LibreOffice 退出后的未完成内容可用 pending/recover 查看/重试。不会在已载入旧模型时悄悄恢复文件，然后允许该旧模型覆盖恢复内容。冲突需人工选择版本，原始内容继续保留。

LibreOffice 实测发现：直接原子替换原文件会使后续 Ctrl+S 误判外部修改，headless store 返回 0x11b。现仅把中间版本导出到私有本地目录；定时原位提交在 UNO UI 线程通过 native storeSelf 更新真实 medium，使用 AbortInteraction 拒绝后台对话框。Writer 原位保存及其后 Ctrl+S 回归通过。副本及崩溃后的离线恢复继续使用受保护的原子写入。挂载临时文件的 fd 保持打开到 rename 完成，避免 close 提前把中间文件名送入上传。

### 本轮实际验证

- Python 40 项、Node 21 项与 0.3.0 OXT/plugin 构建通过。旧 0.2.2 源的快照提交烟测实际失败于“写到挂载而非本地暂存”，本轮实际应用测试确认先有本地完整文件、挂载原文件不变。新增真实文件系统回归覆盖 180 次连续本地修改只在周期末发布最新版本、空开关闭不写入、失败关闭/重启恢复、旧模型恢复门控、断挂载/外部冲突、跨编辑器互斥、显式原生保存、清单写失败和 rename 之前保持上传句柄打开。
- ONLYOFFICE Flatpak 10.0.0：合成 PPTX 原位保存（延迟/丢失确认、并发新编辑）、带 PNG 的 DOCX 原位保存、XLSX 原位保存均通过。真实编辑先写到挂载外完整办公文件，默认间隔内原文件字节保持不变；之后强制发布/加速间隔验证真实内容、保存标记、12 秒空闲稳定、撤销/重做、暂停及原生关闭保护。180 秒边界使用可控单调时钟回归验证，没有冒称每个 GUI 样本实等三分钟。
- LibreOffice 26.2.5.2：0.3.0.oxt 注册/加载在独立 headless profile；Writer 原位、随后原生 Ctrl+S、暂停、目录外/未命名副本、Calc XLSX、Impress PPTX 当前内容导出与源保护通过。实际周期测试配置为 1 秒；副本测试额外确认首次本地导出尚未写入挂载。
- 所有应用测试使用独立 profile、临时文档和模拟挂载；ONLYOFFICE 使用禁用 X11/Wayland socket 的认证 TCP Xvfb，并核实进程 DISPLAY、XDG 与私有 native instance port。仅清理测试进程组，没有启动用户前台编辑器或使用真实云端文件保存测试。
- 仍依赖已验证的 Desktop native adapter，而非公开 web 插件 API；未重新验证 9.4、复杂文档全集及真实 OneDrive 端到端。全量上传协议未改变；降低的是提交频率。

### TwoDrive 机制调查（不属于本轮插件交付）

未合并的 TwoDrive 候选将非截断可写 open 与真实写入分开：不标 Writing、不建立 upload activity、不在未写入 release 时排队；首次 write/ftruncate 才启动原有写入流程，并验证缓存/远端基线。新建/截断和真实修改继续提交。旧 0.2.21 源的新增回归实际失败于“open is not an upload”；修复候选通过。源码在独立 worktree 完成，未修改另一个开发目录的未提交文件。

本机尝试专用临时 FUSE 挂载测试时 fusermount3 返回 Operation not permitted，不能列为本机实测成功。独立候选的 Linux CI 成功挂载后，真实写入完成断言超时，尚未通过完整集成门控。用户指出当前是插件项目后，已停止并关闭 TwoDrive PR #55，取消余下候选构建；没有合并、发布或安装 TwoDrive，也没有操作其运行服务。用户随后明确授权在证据确认 TwoDrive 缺陷时独立修复，已恢复 PR #55 的工作。CI 失败定位为 MockBackend 的 ETag 断言不适用；更正为检查模拟远端内容并等待完整卸载后，独立 Ubuntu 容器的真实 FUSE 挂载连续三轮通过。旧 0.2.21 源在同一实际挂载回归中失败，确认空开可引起状态/上传副作用。两库分别交付，结果待补记；插件本身仍无法拦截编辑器原生可写 open/release 的底层上传。真实 OneDrive 无写入测试。


### 0.3.0 与配套 TwoDrive 0.2.22 交付补记

[PR #5](https://github.com/LumiaBlack51/twodrive-office/pull/5) 已合入 main，源提交 `ccc46b26860123c3382feed2dc7dde681e95ec07` 的 [Office checks](https://github.com/LumiaBlack51/twodrive-office/actions/runs/41297853360) 通过。不可移动注解标签 v0.3.0 指向此合并源。[0.3.0 Preview Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.0) 已于 2026-11-01 18:32:57（Asia/Shanghai）公开；OXT 的 15 项、plugin 的 14 项内容逐字节与源码核对，四项附件的集合、状态、大小、远端 SHA-256 与注解标签解析目标在公开前后通过统一保护检查。

本机现有 ONLYOFFICE 两种插件目录布局及 bridge runtime 从该 CI 包更新，实际替换 10 个程序文件；设置和连接凭据保持原字节，仅保留这些旧程序的小范围私有回退材料。认证 /settings 请求验证 socket activation 后，bridge 在无编辑器/任务时按需退出。没有创建真实文档会话、没有启动用户前台编辑器；LibreOffice 日常 profile 未变更，其新 OXT 已提供下载。再次运行最终 headless LibreOffice Writer/Calc/Impress 和真实 DOCX 原生关闭发布通过。关闭时浏览器不读取 HTTP 回复可能产生 BrokenPipe，桥接现将这种断连作为正常结束，实际保存/重试语义保持不变。

合并前已检查本 PR 的 comments 和 review threads：当时只有 Copilot 配额提示，尚无 Dot 回复，不能声称 Dot 已独立批准插件。TwoDrive 的独立 [PR #55](https://github.com/LumiaBlack51/twodrive/pull/55) 最新提交则已收到 [Dot 无剩余阻断意见](https://github.com/LumiaBlack51/twodrive/pull/55#issuecomment-6556722306)，所有必需检查、真实 mock FUSE 与标准 Debian 制品门控通过后合入 main；[0.2.22-1 stable](https://github.com/LumiaBlack51/twodrive/releases/tag/v0.2.22-1) 已于 18:47:30 发布并从正式包更新本机 CLI/daemon。运行中 daemon 摘要与包一致，配置/凭据和自定义 service 保留，挂载正常，SQLite quick_check 为 ok。没有活动传输、待同步内容或打开的常规文件句柄时才执行后台升级；未重放真实 OneDrive 空开/保存，也不据模拟结果声称真实云端字节完全一致。

收尾只清理本轮 Cargo target、独立工作树、CI 包/源码解包及测试容器、临时脚本和日志；保留必要配置/SQLite/队列、一套旧程序及已有独有恢复资料，不扫描删除用户缓存。已移除本轮 Cargo target、CI/源码解包、测试容器、旧码复现 worktree、本轮测试日志和字节码；被统计的已删 payload 路径分配占用为 1,982,590,976 字节，不冒称整盘净空闲变化。仅保留等待文档 PR 门控的小型源工作树，合入后移除；历史私人审计/独有恢复材料、用户缓存和必要回退状态保持不变。

## 0.3.1：Wine/Soda Microsoft Office · 2026-11-08

新增 Wine 原生 x64 COM 入口，Linux bridge 继续负责路径核验、私有完整文档快照、队列和恢复；没有合入 PR #7 的 Windows CFAPI 候选或改动 TwoDrive。实际源安装是 Bottles Microsoft365-Trio，Soda `11.0-37-office-v7`，Microsoft 365 x64 `16.0.20326.20232`（Word 二进制 FileVersion），三种应用对象模型均为 16.0。

- Python 54 项、Node 22 项通过。新增回归覆盖 Flat OPC 命名空间/图片字节、非法包/宏/签名、Linux 盘符解析/大小写/重映射、仅本地暂存、原生提交许可与旧确认拒绝、断挂载/符号链接/外部冲突、关闭失败后的恢复门控、显式原生 Save、离线安装/卸载和忙碌 prefix 拒绝。
- 真实 Word / Excel / PowerPoint 的 COM 注册加载和自动连接通过；完整修改内容先成为挂载外的 DOCX/XLSX/PPTX，挂载原文件保持不变，本地保存未假清原生 Saved 标记。真实 Save now、Ctrl+S 后继续编辑、8 秒实际 STA 定时提交、空闲字节稳定、暂停/重新开启、最终关闭文本及原生保存标记验证通过。未实等每个样本 180 秒；默认 180 秒策略另由可控时钟的现有队列回归覆盖。按钮通过已注册 COM 对象的同名回调调用，不能称实际鼠标点击 ribbon 已验证。
- 测试使用停止源 prefix 的完整独立副本、临时模拟挂载和设置、带随机认证的独立 Xvfb（无 TCP listener，无 Wayland/会话 DBus）。编辑前核实实际 Office 进程的 DISPLAY、WINEPREFIX、XDG；原始三份 Wine 注册表摘要保持不变。只终止验证为该测试 prefix 所属的专用进程组；临时 prefix、生成文档和测试显示全部移除。
- LibreOffice 26.2.5.2 headless 独立 profile 回归通过：注册加载、Writer 原位和暂停、手动保存、目录外/未命名副本、外部冲突、Calc XLSX / Impress PPTX 内容与源保护。

### 实际发现和修正

1. Wine 的 HKCR 未识别本次 HKCU Classes，Office 也未枚举 HKCU Addins。安装器离线修改**指定 bottle 内**的 HKLM Classes/Addins（system.reg），不运行 Wine、不改宏信任或其他 bottle；真实枚举、连接与组件加载通过。权限和凭据写在私有插件目录。
2. `wine_get_unix_file_name` 在独立探针成功、在 Office/AppV 进程内失败；改由 Linux 按该 prefix 的实际 dosdevices 解析，并在导出/提交时重新解析。没有固定假设 Z: 总是根目录。
3. 普通文档的 Permission/MSIPC getter 在此 Wine 安装不可用。已知 IRM 状态会拒绝，仍核验普通无宏/无签名 OOXML 与密码/文档保护；动态 IRM、复杂保护状态不在支持/验证声明内。不能把 getter 异常当成“已验证无权限限制”。
4. Excel 在 BeforeClose 内不可靠地产生 SaveCopyAs，且 Save 后仍显示未保存。改为取消这次关闭、PostMessage 到同一 Office STA，完成当前内容暂存及原生保存后再关闭；失败保留文档打开，避免最后编辑丢失。Word 的自动化 Close 会报告这次取消；测试继续核验延后的真实关闭及最终包文本，不将初次取消 HRESULT 误称保存失败。
5. 此 Wine PowerPoint 的 dispatch identity 会变化。保留文档 COM 引用，并以该引用的实时路径/名称对齐包装器；另存为仍关联原会话并由源变化保护停止，不生成重复目标会话。无窗口的 PowerPoint 无 ActivePresentation 时，只在恰有一个文档时选择该对象；不在多个文档间猜测。
6. 有窗口的私有 X11 PowerPoint 样本曾在 SaveAs 阶段发生 RPC 断开；未确认根因，不能把这次运行列为成功。最终 PowerPoint 检查使用无窗口的真实应用，仍通过注册加载、当前内容、原生保存、定时器和关闭。用户日常 Wayland ribbon 交互未在这轮自动化中实测。

原位模式不由 bridge 在打开期间原子替换文件：每次先核验 mount/目标指纹，再由 Office 原生 Save 更新自己的文件状态；确认携带一次性许可并绑定快照版本。源改名、挂载失效或外部修改保留本地版本；强制退出后的未采样内容仍依赖 Office 自身恢复。普通本地保存与 OneDrive 上传完成分开陈述；这轮没有真实云端保存测试。

### ONLYOFFICE 关闭回归补充

本轮 0.3.1 初期连续三次在“关闭后 6 秒内发布”处失败，诊断为仍有一个普通（非 Wine native_publish）会话、无活动请求和无提交错误，待同步内容仍完整保留；对照 0.3.0 源的一次运行通过，因此不能声称旧版必现或是 Wine 提交方式导致。卸载时的跨源 DELETE/Authorization 请求需要预检，存在退出阶段送达不稳定的边界；现优先使用正文携带凭据的简单 text/plain beacon，限定于 /close 的小请求，继续校验 Host、Origin、令牌，且不在 URL 或日志写凭据；拒绝入队时保留原 keepalive 后备。新增认证/恶意 Origin/Host/错误凭据及队列保全回归通过。修改后的真实 ONLYOFFICE 10.0.0 PNG DOCX 原位完整回归（含关闭前待同步内容立即提交）通过。强制杀进程不保证网页退出通知送达，仍依靠持久队列及按需服务退出/恢复保护。

### 0.3.1 发布、安装与清理补记

[PR #8](https://github.com/LumiaBlack51/twodrive-office/pull/8) 已合入 main；合并源 `e70568897c975d8cf8a17b433f3816d607e47356` 的 [CI](https://github.com/LumiaBlack51/twodrive-office/actions/runs/42470739575) 通过。Actions 原始制品 SHA-256 `0a4206f11250942ecd7218fa8de231a98eba8523ebbfc091a8ba7825a292596a` 与服务端声明一致；OXT、plugin 和 Wine ZIP 的源码文件逐字节核对。**此次 CI 包中的原样 Wine DLL** 又完成 Word/Excel/PowerPoint 全套真实应用验证，原始三份 Wine 注册表摘要保持不变、私有进程组和 prefix 已移除。修正后的 ONLYOFFICE PNG DOCX 关闭/待同步发布连续两次完整通过。

不可移动注解标签 v0.3.1 指向上述合并源。[0.3.1 Preview Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.1) 于 2026-11-08 18:22:00（Asia/Shanghai）公开；五项附件（OXT、plugin、Wine x64 ZIP、verification.json、SHA256SUMS）的集合、uploaded 状态、大小和远端 SHA-256 在公开前后均通过核验，注解标签对象及解析提交一致。Copilot 仅返回配额不足提示，未获得独立代码审阅，不把该提示或 CI 成功称为审阅批准。

公开后从同一 CI 包安装到本机停止的 Microsoft365 bottle，并更新独立 Linux runtime 和已有 ONLYOFFICE 两种插件目录，runtime/ONLYOFFICE 共替换或新增 10 个程序文件，另写入 Wine 加载项及其连接配置。Wine DLL 摘要为 `d55f1c6fc5f73a79a7b02265aa998e143dfe3ce9e2635e61e718ef5bdc337e8b`；其八项 COM 注册之外的 system.reg 内容、完整 user.reg、连接凭据和 systemd unit 均保持原字节。仅保留一套少量旧程序回退材料。安装没有启动日常编辑器、创建真实文档会话或对真实挂载保存；日常 LibreOffice profile 未修改。认证 /settings 验证 socket 唤醒更新后的 bridge，空闲后 MainPID=0、服务 inactive/dead，只有现有用户管理器的 socket 继续监听。

清理移除本轮临时 MinGW/clang 工具、9.8 GiB 测试 prefix、0.3.0 对照源码、探针/临时脚本/日志、Actions 原始解包及重复程序文件。被统计的已删除 payload 路径分配占用合计 11,020,439,552 字节；这不是整盘净空闲增加的声明。保留正式发布包、必要审计摘要、一套小范围程序回退和所有真实 pending/用户数据；没有扫描删除用户缓存或恢复材料，也没有修改 TwoDrive 本体/服务。本补记是文档收尾，不改变 v0.3.1 的不可移动发布源。

## 未发布修复：cpak Wine 连接失败 · 2026-11-08

截图的 `Bridge request failed; pending revision retained` 来自 Wine DLL 的 HTTP 传输。只读排查确认：连接文件与主机设置的端口、令牌和 prefix 相符，主机认证 `/settings` 返回 200；真实 PowerPoint 由 cpak 启动，网络命名空间与主机不同，slirp4netns 带 `--disable-host-loopback`。容器内 `127.0.0.1:19836` 连接被拒绝，经 `10.0.2.2` 亦不可达。授权文件在容器中使用 `/run/cpak/grants/...` 路径，原来按主机 dosdevices 解析的实现也不能正确定位该路径。只读核对了授权目录与主机目录的 inode 对应，没有对该真实目录执行保存测试。

本地源码修复增加单端口 namespace relay：通过 `nsenter` 在指定同用户容器绑定回环 listener，将 FD 传回主机，仅转接既有 bridge 的端口；原 bridge 继续校验认证/Host/Origin 并持有唯一保存队列。namespace PID/启动时间改变后退出，同一 namespace/端口重复连接复用已就绪的 relay，不开启主机网络共享。cpak 启动器需要在文件授权/空闲容器刷新完成后、启动 Office 前调用新模块，安装方法见 [Wine 说明](wine.md#cpak-isolated-networking-unreleased-fix)。

容器路径由同 prefix 的真实 Office 进程挂载表与主机挂载表转换，要求唯一的主机 inode 匹配并拒绝符号链接；每次采样/提交重新解析，陈旧 grant、歧义或源变化拒绝继续。DLL 在初次 `/settings` 失败时仍保留 STA 定时器，每 5 秒重试；配置有效前不处理文档，原位/副本模式使用 bridge 的返回值。传输失败提示不再声称尚未暂存的版本已经被保留；读取响应中断不作为保存成功。

本轮验证：

- Python 57 项、Node 22 项通过。新增真实 Linux user/network namespace 回归先复现隔离回环连接失败，再经 relay 验证无凭据 401、认证设置读取、模拟目录 DOCX 保存、会话清理、重复连接及 namespace 退出后的 relay/锁文件清理。路径回归覆盖挂载表转义、唯一 inode、歧义、陈旧 inode 和符号链接拒绝。
- MinGW GCC 13.2 x64 编译 DLL 通过。Soda `11.0-37-office-v7` 在独立临时 Wine prefix、无 DISPLAY/Wayland/会话 DBus、禁用图形驱动的环境中运行 `tests/wine/HttpSmoke.cpp`：直接复用 DLL 的实际 Http 实现，连接失败诊断、同一客户端随后成功读取认证设置、原位/副本布尔字段及畸形响应拒绝通过。仅运行传输探针，没有创建 Office COM 应用或打开文档。
- **没有对本轮补丁重跑真实 Word/Excel/PowerPoint 保存、STA 初始失败恢复或日常 ribbon 操作；此前 0.3.1 的真实应用结果不代表这个补丁已完成相同验证。没有真实 TwoDrive 保存或云端同步测试。**

用户最终选择暂不停止当前 bottle、先保留修复，因此没有替换已安装 DLL、Linux runtime、启动器或注册表，没有启动/停止日常编辑器。源码和一个标为 unreleased 的候选补丁包保留待后续应用；正式 v0.3.1 发布包及标签不变。临时编译工具、下载、测试 prefix/文档/日志、探针二进制和项目 bytecode 在收尾移除，专用测试进程已退出；真实 pending/恢复数据保留。

### 续检：真实挂载命名空间

用户要求继续后，再次检测到 Microsoft365-Trio 的 PowerPoint 与 Wine 后台进程仍在运行，因此没有绕过停止检查、复制活动 profile 或替换已安装程序。新增独立 Linux user/mount namespace 测试：仅在私有 namespace 的 `/run` 上挂载 tmpfs，把临时模拟目录 bind 到 `/run/cpak/grants/...`，通过公共 `resolve_wine_path` 验证实际 mountinfo、大小写解析及主机 inode 对应；符号链接和 namespace 退出后路径均被拒绝。进程名称由 Python fixture 模拟，不是 Office 应用，不将此项记为真实 Office 保存测试。Python 总计 58 项通过；先前的 Node 22 项与原生 WinHTTP 结果不变。新增临时挂载、文件和专用测试进程已清理，候选包更新，安装及真实 Office 回归仍待源 bottle 正常停止。

## 未发布修复：残留进程与后台生命周期 · 2026-11-08

用户确认 PowerPoint 窗口已经关闭，但进程意外残留，另由 Office 团队处理。只读检查时，已安装的 bridge（PID 1581322）仍在运行约 1.5 小时，RSS 约 14 MiB、累计平均 CPU 约 0.2%，`NRestarts=0`。旧逻辑把任何当前用户的 Office 进程视为保留理由；提交失败或无法识别的 pending 也会触发无限重试。这两项都可能使 bridge 长期驻留，不能再用“关闭窗口后 30 秒退出”概括旧版行为。本轮没有终止任何日常 Office 进程，也未替换已安装 runtime、插件、单元或注册表；当前安装仍受旧逻辑影响。

源码改为只由进行中的请求和认证请求/会话心跳续期，默认 30 秒空闲退出，不依赖 Office PID 消失。退出时安全尝试关闭/恢复；失败版本和损坏/未识别清单留在磁盘，不以继续驻留作为保留数据的条件。Office 进程检测仅用于阻止在旧内存模型仍可能存在时恢复文件，不能阻止 bridge 本身退出。CLI 默认不再永久常驻，拒绝 0、负数、NaN 和无限退出延迟。安装单元使用 `Restart=no`，保留按连接触发的 socket activation，不进行独立失败重启循环。

ONLYOFFICE 对尚未修改的已开启会话也发送心跳，不导出、不写入文件；过期会话按正常安全校验重新建立。Wine 的既有 10 秒心跳保持，HTTP 明确识别 `session_closed`，下一轮重新建立会话。所有重建都受 pending 冲突保护，不能用旧编辑器内容覆盖恢复数据。此前关于最小化时通过进程存在永久保留会话的描述已被此源码修复取代；安装升级必须同步更新 runtime、Wine DLL 和已安装的 ONLYOFFICE 插件。

待安装的 namespace relay 增加首连接前 60 秒启动宽限、连接完成后 30 秒无新连接退出；容器、残留 Office PID 都不能单独保留 relay。namespace 消失时通知转发线程停止，空闲连接不再阻碍线程池退出；锁文件通过 finally 清理。cpak 内没有独立的 socket activation：relay 空闲退出后，从仍开着的空白 Office 内部文件菜单打开文档不会自行重建 relay，需要再次运行主机启动器/hook。这个取舍和启动器集成尚未做真实 Office 验证，不能据此宣称可直接发布。

本轮实际验证：

- Python 65 项、Node 24 项通过。覆盖残留进程不能阻止退出、进行中的请求先完成、原生 pending/外部冲突保留、过期会话安全重建及恢复冲突阻止覆盖。真实 Linux user/network namespace 验证无首连接/连接结束后的 relay 退出，目标容器继续存活且锁文件清理；另覆盖 namespace 退出与无响应转发连接的收尾。
- 独立 CLI 进程使用真实默认 30 秒配置，在系统仍有 Office 进程时于最后一次认证请求后 **30.05 秒**正常退出，监听端口关闭；临时模拟目录中的原文件字节不变、待恢复内容及清单完整保留。测试未接触真实 TwoDrive 保存。
- 临时解包的 MinGW GCC 13.2 编译更新后的 DLL 和实际 WinHTTP 探针通过；Soda `11.0-37-office-v7` 在独立临时 prefix、隔离网络、禁用 DISPLAY/Wayland/图形驱动的环境中运行探针，确认断连、认证重连和过期会话的专门错误类型。没有创建 Office COM 应用或打开用户文档；真实 Office 的睡眠/节流恢复、完整保存和 cpak 启动器流程仍未验证。

系统另有独立的 `twodrive-daemon.service`（挂载与同步）及 `twodrive-nautilus-watch.service`（Nautilus 响应诊断）正在常驻，它们不属于 Office bridge 生命周期，本轮未修改或停用。正式 0.3.1 包保持原样，只更新同一个未发布候选包。临时工具链、下载、探针、测试文档/prefix 和本轮专用进程在收尾清理；所有真实 pending/恢复材料保留。

## 安装与原生退出回归 · 2026-11-08

用户说明此前是退出后的进程残留。之前仅凭 POWERPNT.EXE 存在就称程序仍正常打开，是不准确的判断；现已在 AGENTS.md 中明确禁止把 PID 等同于活动窗口/文档，安装检查忽略僵尸进程，并将其他存活 PID 表述为尚未释放、可能是退出残留。不能在这些进程仍可能回写注册表时强行替换程序，也不能把测试强制清理记作自然退出。原残留进程已结束，本轮没有现场 dump，无法追溯其确切原因。

已补齐 cpak relay 空闲后的事件唤醒：DLL 在连接失败时创建自身私有目录中的空 `relay.request` 目录，每 5 秒最多一次；新 `--sandbox-wakeup` 安装选项为指定 prefix 写入 systemd 用户 path/service 单元。host helper 消费目录，核对既有 token/port/prefix，按实际目录去重 `steamuser` 等别名，确定同 prefix 的唯一网络 namespace 后转接单一端口。服务与 relay 一起按需退出，仅由用户管理器保留路径监听；不依赖启动器常驻监视，也不需要修改主机启动器。listener 使用 SO_REUSEADDR，允许空闲退出后重新绑定；连接重建成功后清除旧错误状态。有效凭据已存在时安装器不再重写设置文件，避免改变用户格式或额外字段。单元生成、目录别名、符号链接拒绝、设置保全及僵尸检测均有回归。

实际结果：

- Python **68 项**、Node **24 项**通过。MinGW GCC 13.2 编译最终 DLL，SHA-256 `28f55e55e4b6c426636122e0b94d192e639b8eb348e0c452b1c58f9ac79616ff`。
- 最终 DLL 的真实 Word：在完整独立 prefix、认证私有 Xvfb、独立 user/net/mount namespace 和临时模拟挂载中运行；初次无端口连接失败触发真实 systemd path activation，STA 自动恢复、干净文档不改写、本地完整暂存、Save now、原生 Save 后继续编辑、8 秒定时提交、暂停/恢复、最终关闭内容均通过。零文档后调用 Quit，确认 Word 进程自行退出，再清理 Wine 后台服务。
- 最终 DLL 的真实 PowerPoint：同样使用真实 path activation 和 `/run/cpak/grants/...` bind 路径；空白应用实等 35 秒，核验 relay MainPID=0、没有文档会话，再由同一应用创建文档，连接无需重跑启动器即可恢复。完整暂存/保存/定时/暂停/最终内容通过。最后确认零文档，释放驱动程序自己的 COM 引用，在私有 Wine 桌面中按进程映像枚举唯一主窗口并投递 WM_CLOSE（该版本未暴露 Application.HWND）；没有调用 Quit，PowerPoint 进程在强制测试清理前自行退出。不能把它等同于已验证日常 Wayland 鼠标关窗的全部路径。
- Excel 在当前复制 profile 中发生 COM Add/早期编辑断连（800706ba/800706be）；使用正式发布的未改动 0.3.1 DLL 和原始测试驱动做对照，也在 SaveAs 后、本地暂存确认前断连。此项没有通过，不归因为本补丁，也不声称已修复。此前版本的三应用成功记录不能替代本轮 Excel 的实际失败。

各次测试仅操作私有模拟文档，原始 Wine 三份注册表摘要在成功的最终 Word/PowerPoint 测试前后保持一致；专用 prefix、Xvfb、namespace、临时 path/service 单元和进程组均清理。没有真实 TwoDrive 保存或云端同步测试。

已在停止的 Microsoft365-Trio 中安装上述最终 DLL，并同步更新 Linux runtime、已安装 ONLYOFFICE 两种布局中的 plugin.js 和后台按需单元；主机 Office 启动器未改动。配置凭据保持，settings.json 恢复并核验为安装前的原始字节，user.reg/userdef.reg 字节不变；system.reg 仅经安装器的八个自有 COM 段补丁流程更新，未保留完整旧 system.reg 做外部内容逐字节对照，不作该项额外声明。待恢复目录中原有 **2 个文件逐字节摘要不变**。保留一个按内容去重的小范围程序回退目录，旧 DLL 仅保存在 prefix 的 `.previous` 文件。

安装后未启动日常编辑器，没有为验收访问真实保存接口或触发 pending 恢复；HTTP bridge 与 namespace relay 均为 inactive、MainPID=0，只有原 socket 和新 path 处于监听/等待。本轮补丁仍未发布，正式 v0.3.1 制品和标签不变。临时工具链/下载、旧版对照程序、探针、测试日志/profile/文档和 bytecode 已清理，专用测试进程无残留；真实待同步与恢复数据保留。

## 自动加载缺失补正 · 2026-11-08

用户再次截图显示 TwoDrive 选项卡缺失。只读排查发现 DLL 仍为已安装的最终版本，但普通 HKLM PowerPoint Addins 的 LoadBehavior=3 之外，Click-to-Run 虚拟 Machine 注册表中同一加载项另有 LoadBehavior=2。此前真实驱动打印过 CONNECTED 0，随后通过 COMAddIn.Connect=true 强制连接后才验证保存；这证明保存功能，不证明自动加载或正常启动时选项卡出现。前次测试把这一缺口掩盖，现已取消集合 Update 和强制连接，启动即未连接就必须失败。[Microsoft 的 LoadBehavior 值说明](https://learn.microsoft.com/en-us/visualstudio/vsto/registry-entries-for-vsto-add-ins?view=visualstudio#loadbehavior-values)可供对照；本项目仍为原生 COM DLL，不依赖 VSTO/CLR。

安装器现在同时维护自身三个 Click-to-Run 虚拟 Machine Addins 段，总共 11 个自有注册段；匹配 Windows 注册表段时不区分大小写，避免同一键不同大小写留下旧覆盖值。新增回归以已有的 C2R=2 覆盖验证修复为 3，并确认其他加载项的 2 保持原样。Python 共 69 项通过。

最终 DLL 未改动。独立 prefix、私有认证 Xvfb 和隔离 net/mount namespace 中的真实 PowerPoint 启动直接返回 CONNECTED -1；没有调用 Connect=true，也没有刷新集合。自动会话、暂存、Save now、定时、暂停/恢复、最后内容和原生 WM_CLOSE 自然退出均通过；Wine 后台退出写回注册表后，C2R 的启动标志仍为 3。原始三份 Wine 注册表摘要在测试前后不变。

另一次尝试在私有 X11 显示中提前展示文档以抓取 ribbon 时出现既有 COM 800706ba，画面为空黑；该截图已删除，不作为可视选项卡验证通过的证据。正常 Wayland 界面的选项卡仍需用户重新打开应用确认，不能用无窗口 COM 成功替代可视验证。

确认日常 bottle 已无 Wine/Office 进程后，只修补日常 system.reg 中这三个自有 C2R 段；其余注册段内容（忽略末尾分隔换行）核对一致。user.reg、userdef.reg、设置、bridge 凭据、当前 DLL/previous DLL 及所有 pending 文件摘要保持不变；没有重装 DLL、改其他加载项、信任设置或启动日常编辑器。原有必要程序回退中另保留这三个段的旧值。候选包和验证摘要同步更新；临时工具链、测试副本、日志、空黑截图及专用进程在收尾清理。

## WinHTTP 12029：嵌套 user namespace 补正 · 2026-11-09

用户截图中的 TwoDrive 选项卡已经出现，但显示保存服务连接失败。只读查看真实 systemd 日志，按需 relay 多次退出于 `nsenter: reassociate to namespace 'ns/net' failed: Operation not permitted`；主机 bridge socket 仍正常监听。真实 Word 的 user namespace 为 `user:[4026533196]`，继承的 net namespace 为 `net:[4026532687]`，通过 `NS_GET_USERNS` 查询到该网络实际归属 `user:[4026532679]`。先进入 Word 的嵌套 user namespace 后没有权限进入上层所有的网络，解释了端口未创建及 WinHTTP 12029。此前独立 namespace 与真实 Office 测试只覆盖 user/net 同层结构，未覆盖实际 cpak exec 的嵌套关系；前次成功不能证明这条日常连接路径通过。

修复只改 Linux `namespace_listener`：从已验证归属的目标进程打开 net namespace，使用 `NS_GET_USERNS` 获得真正所有者的描述符，通过继承的 `/proc/self/fd/...` 参数交给 nsenter。两项描述符在使用期间保持打开，使用前后继续验证目标身份；不扫描猜测上层进程、不需要 root、不更改 cpak 隔离策略，也不改 DLL、Office 注册表或保存规则。内核行为依据 [NS_GET_USERNS](https://man7.org/linux/man-pages/man2/NS_GET_USERNS.2const.html) 和 [setns 权限要求](https://man7.org/linux/man-pages/man2/setns.2.html)。

独立回归改为先建立外层 user/net namespace，再由内层 user namespace 模拟 Office：核对两层 user 不同、net 的所有者确为外层，确认旧 nsenter 命令实际失败；修复后的 relay 完成认证拒绝/接受、模拟 DOCX 副本保存、重复 relay 复用和进程退出清理。7 项 namespace 测试及全套 69 项 Python 测试通过。真实 Office 集成脚本也已补充同样的嵌套启动及所有权断言，本轮未运行该脚本。另以现有 cpak `application-runtime` 进程为目标，只执行 `/bin/true` 比较：旧方式退出码 1，实际网络所有者描述符方式退出码 0；未建立任何 socket 或访问 Office/文档。

所有保存写入仅使用临时模拟文档，没有真实 TwoDrive 保存或云端上传验证。本轮没有启动任何 Office 编辑器。

等待另一个“修复悬停 Reflect 样式时崩溃”Codex 任务完成后，原子更新本机 Linux runtime 中的 `wine_namespace.py`，摘要为 `5e3d951a8b159037173267b6c29187415de4777b3aaab25ca076ceaa94b9bea5`；保留该单文件的必要回退。安装前后三份 Office 注册表、设置、bridge.ini、DLL/previous DLL 以及现有 2 个 pending 文件逐字节摘要一致。仅重置自有 relay 的失败状态，没有重启主机 bridge 或修改 Office 启动器。

用**已安装模块**在现有 cpak 容器中创建独立随机临时端口，将随机 nonce 转发至独立主机测试 socket 并原样返回；未使用真实 bridge 端口或访问保存接口。该 relay 在测试后自行空闲退出，容器继续运行，临时锁和目录删除。收尾时正式 bridge 和 relay 均为 inactive/MainPID=0，原 socket 为 listening、path 为 waiting。当前未发布候选包同步更新，正式 v0.3.1 制品保持原样；专用测试进程、缓存、bytecode、临时脚本/日志均无保留。不能据此声称已验证日常 Word 保存或真实云端同步。


## 2026-11-09：保留 Wine Office 的原始关闭请求

用户报告 Word 编辑后无法退出；私有 Wayland 样本在旧 DLL 下复现：Word、Excel、PowerPoint 关闭文档后均留下零文档、零文档窗口的空白应用框架。第二次关框架才退出。旧版统一取消 BeforeClose，延后调用 Document/Workbook/Presentation.Close，丢失了原先退出应用的请求。

Word、PowerPoint 可在 BeforeClose 中完成快照和原生保存，现保留原始请求；失败仍取消关闭并保留文档。Excel 仍需延后保存，通过同线程窗口 subclass 记录 WM_CLOSE/SC_CLOSE，保存成功后重试同一框架；COM 文档关闭保留文档关闭路径，不隐式 Quit。卸载会移除 subclass；回调期间保留对象生命期。

新增 `tests/wine_integration.py --host Word --first-quit`：首次 Quit 前产生最后修改，释放测试引用后要求自然退出，不补发第二次关闭；检查最终文件内容及无残留待同步版本。旧 DLL 对照未退出，最终 DLL 通过。69 项 Python 回归通过。

另在私有 scale-2 Wayland/cpak、mock mount 上验证三款原生右上角 X：最终文字确实保存、窗口与进程正常退出、mock pending 清空。记录在 `/home/lumia/software/windows/verification/office-v10`；字体/绘图修复属于该目录的 Wine 运行层，保持本插件现有桥接协议。未对真实 TwoDrive 或用户工作文件执行保存测试。

附加 X11 Excel 自动化检查在旧 v9/旧 DLL 和候选环境都于关闭前的编辑/快照阶段断开 RPC，未用其宣称 Excel X11 通过；正常部署和本次实际验收使用原生 Wayland。Excel 纯 COM Application.Quit 且未产生原生框架关闭消息的情形未扩展为自动退出策略。

## WordOpenXML 80020009：失败保护与诊断 · 2026-11-11

用户截图显示 `COM WordOpenXML error 80020009`，随后报告无响应、无法关闭，后来说明已能正常关闭。截图中的错误发生在 Word 导出完整 Flat OPC 阶段，尚未将该次快照发往 bridge。`80020009` 是外层 `DISP_E_EXCEPTION`，旧包装丢弃了 Office 的 EXCEPINFO 描述/内部错误码，不能单凭它判定文档内容、兼容模式或 Wine 的具体缺陷。源码另确认两项失败处理问题：失败不更新时间戳，定时器会每秒重试导出；BeforeClose 中同一导出再次失败会取消关闭。它们可以阻碍关闭，但没有完整的 Windows 主线程回溯证明本次无响应的确切原因。只读调试后进程未保留 tracer；未终止日常 Office，也未复制活动 profile。

修订保留 EXCEPINFO 的 Unicode 描述、Office scode/wCode，并支持 deferred fill-in；返回明确 Word 序列化异常时暂停该文档的自动保存，保留原生 dirty 标记、已有本地版本及其版本号。关闭请求中的同类失败保留 Word 自身的保存/放弃/取消选择，不再强制取消；其他保存失败继续保守拦截。Word 导出期间禁止自动警告并恢复原值；普通 busy/RPC 失败仍可重试，失败采样也遵守至少五秒间隔。没有改用只导出正文的降级路径，也没有通过切换真实文档路径来另存为。此保护只处理已返回的异常，无法中止一个永不返回的同步 COM 调用，不能据此宣称所有卡死已经修复。

本轮验证：

- Python **69 项**、Node **24 项**通过；MinGW GCC 13.2 x64 构建通过。新增原生 `tests/wine/AddInSmoke.cpp` 在独立临时 Wine prefix、无 DISPLAY/Wayland/会话 DBus、禁用图形驱动的环境中运行，无 Office 应用。实际复用 COM 包装及插件 process/event 代码，验证 deferred Unicode 异常和内部码、导出失败暂停、旧暂存版本不变、未调用原生 Save/未清 dirty、暂停后不再导出、首次 BeforeClose 失败不改取消标记、已有取消保持、瞬时拒绝不永久暂停及失败采样限频。
- 最终 DLL 在 Soda `11.0-37-office-v12`、Microsoft 365 Word 16.0 的完整独立 prefix、认证私有 Xvfb、临时模拟挂载中分别验证兼容模式 **12（Word 2007）**与 **14（Word 2010）**。17 页合成文档包含中文、页眉、页脚及浮动文本框；本地完整快照保留非正文内容，挂载源在暂存期间不变，原生 Save now、随后手动 Save、定时提交、暂停/恢复及带最终修改的首次 Quit 均通过。Word 在测试强制清理前自然退出，原始三份注册表摘要不变，专用 profile/文档/进程组已清理。
- 合成兼容文档没有复现原始 `WordOpenXML` 错误，因此不能将“兼容模式”定为根因，也不能宣称原始文档导出已修复。没有真实 TwoDrive 保存或云端同步测试；其他 Office 应用没有因本轮 Word 修订重新实测。

确认日常 Microsoft365-Trio 已无 Wine/Office 进程后，已原子更新其自有 DLL；SHA-256 为 `40b9bc43d8daa2530a577e413bb11c84b5323da0a26493d92fd0f488245c51aa`。仅替换 DLL 和其 `.previous` 回退文件，回退为安装前 `4e70571c5a2c0f8e04c725d6f095f7a4e7a41106ca4c845f1ff95f113fee9c76`。三份注册表、bridge.ini、settings.json 与 **4 个 pending 文件**合计 9 个受保护文件摘要在安装前后完全一致，没有改 Wine runner、Office 配置、桥接协议或其他任务的测试环境，没有启动日常编辑器。未读取原始工作文档；本轮只交付失败保护和诊断，原始文档的具体导出故障仍待获准使用临时副本后定位。更新同一个未发布候选包，正式 v0.3.1 制品和标签保持不变。

收尾已删除本轮工具链/下载、原生探针二进制、临时脚本/日志、全部测试 prefix/模拟文档及本轮 bytecode；核验没有本轮专用 Wine/Office 进程残留。保留源代码、当前候选包、正式发布集及必要的单份 DLL 回退，所有真实 pending/恢复数据保持原样。

### 获准使用文档副本后的续检 · 2026-11-11

用户随后明确允许只读复制出错的 DOCX。用原授权路径的 inode 确认实际文件位置，读取稳定快照至 0700 私有临时目录；另对同名较早版本作只读对照。当前文件的 mtime 为 14:15:40（Asia/Shanghai），晚于截图，无法从它还原故障当时未保存的内存状态。测试从未在真实 TwoDrive 或原件上打开编辑器/执行 Save，原件和较早版本的内容摘要在收尾复核。

为复用此流程，集成驱动新增 `--word-document`：只把输入复制至私有模拟挂载，要求 `--host Word --sandbox`，禁用宏及打开时链接更新，保留输入文件名；可选 `--word-visible` 仅用于已验证的私有显示。测试标记文件改用 CreateFileW/WriteFile，支持中文目录；新增严格的已有正文/脚注/尾注文字及图片原始字节比较，比较失败时只记录位置和长度，不记录正文。

实际结果与边界：

- 当前保存版本和同名较早版本在私有 Xvfb、独立 user/net/mount namespace 中均完成 WordOpenXML、修改暂存、Save now、定时保存、暂停/恢复、首次 Quit 和自然退出，没有复现 `80020009`。这两次早期测试只核对新增标记和保存流程，**不能据此声称整篇原有文字逐字一致**。
- 使用修订前 DLL，在私有原生 Wayland、scale 2、与原路径相同长度/中文名称的模拟 grant 路径下，WordOpenXML 和本地暂存同样成功，8 张图片原始字节保持；但新增的严格文字比较发现一处中文字发生变化，因此该文档的完整保真检查失败，没有把它记为整套通过。截图中的 WordOpenXML 异常仍未复现。
- 进一步在打开副本前断开插件，直接读取 Word 的 Content.Text 并调用 WordOpenXML、原生 Save，发现差异在第一次 WordOpenXML 调用前的内存模型中已存在。该控制实际完成零文档后 Quit 和自然退出；外层普通自动加载断言因控制有意断开插件而不适用，未将整套 harness 记为通过。
- 最后在真正的 Bottles cpak 容器中重新建立完全独立的 prefix 和私有 GPU Wayland compositor，**打开前从该私有注册表移除本插件，并验证 COMAddIns 中不存在本插件**。核验实际 Office 的私有 prefix、DISPLAY 缺失、私有 Wayland socket、XDG 配置目录及独立网络 namespace。无插件时，同一读取差异仍在首次导出前出现；WordOpenXML、临时副本上的原生 Save、文档关闭及零文档后 Quit 均完成，确认编辑器自行退出，再停止自有容器。该结果将此字符差异定位到原生读取路径，不能归因于插件的 Flat OPC 转换；**它与原始 80020009 的关系尚未建立，不能作为原报错已定位或已修复的证据**。
- 中文长路径首次探针因驱动的窄字符 ready 文件路径中止，修正为宽字符 API 后继续；另一个 X11 可视对照在 Visible 阶段返回既有的 RPC `800706be`，不计为 WordOpenXML 复现或成功。后续使用隔离的原生 Wayland。没有通过放宽文字比较掩盖原生读取差异。

本轮没有进一步修改或替换生产 DLL、Wine runner 或 Office 配置，只扩充复现工具和验证记录。原始保存异常仍需故障发生时的详细 EXCEPINFO/交互状态才能继续归因，既有失败保护继续生效。Python 69 项、Node 24 项及更新后的 x64 测试驱动编译通过；真实用户文件的副本不随候选包交付。

收尾核验两份原件内容摘要均未变化，三份原始注册表、DLL/previous DLL、bridge.ini、settings.json 和现有 pending 共 **11 个受保护文件**逐字节摘要保持。所有本轮专用 Office/Wine、私有 compositor 和临时容器均已结束，没有残留测试进程；原件、已有恢复内容和其他任务的环境不作清理。本轮输入副本、导出的 XML/文字/办公文件、copied profile、测试日志、一次性探针/适配脚本、工具链和下载均删除，仅更新当前候选包中的测试工具及简要审计记录。

### 字符读取问题的定位、修复与部署 · 2026-11-11

用户要求继续修复，仍明确只测试临时副本、不改原件。纯 IXmlReader 的公开接口已独立复现，不需要 Word 或插件：Wine 的 UTF-8 尾部扫描没有正确识别 continuation byte，保留未完整字符时又从错误的原始缓冲区位置拷贝数据，导致分块边界处的多字节字符变成其他 ASCII 字符及 U+FFFD。与 [Wine MR12399 的公开提案](https://list.winehq.org/hyperkitty/list/wine-gitlab%40list.winehq.org/thread/C5AMKGAILZI2QG62LTLYZX4OWP7ORWDZ/)吻合；本地回移 Andrew Manchinsky 提案的 reader.c 修订，包含 UTF-16 转换后的原始缓冲区消费。没有声称该提案已被上游接受。

维护源及归属明确的补丁位于 `/home/lumia/software/windows/tools/office-input/patches/0017-xmllite-fragmented-unicode.patch`，已核验从 Wine 基线可逐字节重建修改后的 reader.c。独立新运行器 `soda-11.0-37-office-v14` 相对 v13 **仅改变 x64 xmllite.dll**，SHA-256 `107d5f2a341665714cc41603607ff1ddf88b35abe7c04fd928f95adb7806f8ca`；v13 的旧模块为 `9c5da9e5b996adb32ff172fdcd3e72aaf9a790d30708723d9f750c3cb137f162`，保留用于针对性回退。其他既有显示/字体/关闭修复的运行层模块没有变化。

实际验证：

- 公开 API 分块不变性测试共 **140 组**：覆盖 2/3/4 字节 UTF-8、不同文本长度和流片段大小、UTF-8 BOM/无 BOM 及 UTF-16LE。旧 v13 **10 组失败**，v14 **全部通过**。用独立完整 Unicode 转换生成预期值，不以解析器内部缓冲区布局充当预期结果。
- Wine 原有 reader **2145**、writer **2746** 项检查均无失败；原有 **15/44 个 todo** 仍如实保留，不宣称这些未实现能力已通过。
- 真正 Bottles cpak 内，对 v13/v14 使用同一份授权 DOCX 快照和合成中/拉丁文、扩展区汉字及 emoji 样本做成对验证。都在独立 profile、私有 scale-2 GPU Wayland、独立网络 namespace 中运行；打开前移除私有插件注册，核验插件不存在，并核对真实 Office 进程环境及实际加载的 xmllite 路径。
- v13 在文本接口、完整 Flat OPC 导出和**真实追加修改后的原生 Save** 均出现字符变化。v14 的两份样本在读取前后、导出和保存后，所有既有正文/脚注/尾注文字均与预期完全一致；授权样本的 **8 张图片原始字节**完整保持。追加的公开 Unicode 标记确实进入原生文件，Saved 标记由 dirty 恢复，不能以未修改文件上的空 Save 冒充保存回归。
- Word.Content.Text 会把内嵌图片表示为占位内容，直接与 OOXML 的 w:t 拼接结果比较会产生额外差异。测试按 InlineShape.Range 的真实位置及内容精确排除占位范围，再作文本比较；独立的 XML 原有文字及图片字节比较未放宽。最终两份样本的各项检查全部通过。每份文档关闭后 Documents.Count=0，随后 Quit，确认 Word 自行退出，才停止测试容器和后台服务。

确认日常 bottle 无未释放进程后，已只把 `bottle.yml` 的 Runner 和两份 `office-display.json` 的 runner 字段从 v13 切到 v14。没有启动日常编辑器，没有改原文档、三份注册表、插件 DLL/previous、bridge.ini、settings.json 或 pending；11 个受保护文件摘要在部署前后保持，原件内容摘要同样未变。简要运行时/测试/部署审计在 `/home/lumia/software/windows/config/office-v14-xml-runtime.json`。需要回退时，在 Office 退出后仅把这三个字段改回 v13，不恢复整个 profile 或文档。

该修复解决的是已复现的 Unicode 读取/保存问题。原始 `80020009` 异常与它是否同因仍未建立证据；未对 x86、其他 Office 应用、真实 TwoDrive 写入或云端同步作本轮验证，不扩大兼容性声明。


## Soda Office 保存期间继续编辑 · 2026-11-19

本地候选使用 Microsoft 365 Office 16.0 和 Soda 11.0-37-office-v15。在复制的 bottle、私有显示和本地模拟挂载目录中运行。程序包的 DLL SHA-256 为 `0c5682c88bcf5500d88db4eb2b78efcbbbe60516772781a298b1c61dbefd7cb9`。

- Word 使用 17 页兼容模式 14 的合成 DOCX，包含中文正文、页眉页脚、浮动文本框和表格。保存服务成功暂存第一版后扣住回执，修改表格的 COM 调用在 10 毫秒内返回。第一版快照保留旧表格，最终 DOCX 保留新表格和关闭前的正文修改。手动 Save 后立即再编辑、定时保存及首次 Quit 的内容断言通过。退出后整体测试因原 bottle 注册表同期变化而报错；检查时原 bottle 有活动进程。因此这次不计为完整环境校验通过。
- Excel 使用两张以上工作表、2,048 个公式、单元格编辑、原生保存和关闭。Wayland 独立启动的候选完成恢复快照、定时和最终文件检查，实际公式与最后的两个修改均存在，扣住快照回执时单元格编辑为 12 毫秒。此轮早于最终 DLL 增加文件句柄修改时间和大小复核。最终 DLL 的连续多宿主测试中，第二个应用 Excel 在创建 COM 应用时报告 80004002；随后原 bottle 已有活动进程，独立重跑未启动。未将此前候选的结果计入最终 DLL 全套通过。
- PowerPoint 使用约 108 MiB 的合成 PPTX，含 9 张独立生成的图片，多页文本及表格。七次编辑包括修改文本、添加并修改另一张表格及两次替换大图。每次原生 Save 后立刻继续修改标题。七次从调用原生 Save 到下一次 COM 编辑返回为 475、525、505、461、669、552、460 毫秒，中位数 505 毫秒。另一次小文稿测试在扣住快照回执时，表格编辑在 14 毫秒完成。最终文件通过 ZIP CRC、超过 100 MiB、最后的图片对象名、表格内容和关闭前正文校验，恢复队列已清空，宿主自行退出。上述七次测量含同步自动化调用耗时。它们不能用来证明键盘输入的实际绘制延迟。
- 私有 Wayland GPU 显示上的按键到字形测试遇到 PowerPoint 的登录窗口，文本框未收到标定输入，未产生有效的上屏延迟样本。候选没有截获用户的 Ctrl+S 或更改登录状态。宏、加密、超过既有 128 MiB 传输上限的快照、真实云端和每日 GNOME 前台时延不在本轮覆盖范围内。
- Python 单元测试 113 项通过。独立 Wine COM 故障注入检查涵盖失败快照保留、关闭、持续输入的采样上限、慢导出的间隔和重试退避。数据传输、摘要和确认在工作线程完成；Office 的原生导出、Save 和必须确认最后一版的关闭仍可能阻塞。

复验入口为 `tests/wine_integration.py --mixed-edits`。使用 `--large-deck --host PowerPoint` 复验大包，用 `--host Word --word-compatibility 14 --first-quit` 复验复杂 Word 文档。Soda 的原生 Wayland 环境可指定 `--wayland-tools` 指向包含 `scripts/run-headless-wayland.sh` 的测试工具目录。已授权且能接受键盘输入的独立 PowerPoint 窗口可额外使用 `--measure-input`；该测试检查像素、每次保存的不同标记以及最后文档中的文字。硬件、测试环境和文件内容会影响结果。

## Soda PowerPoint 实际 147 页课件的后台保存与分片上传 · 2026-11-19

从现有本地缓存复制 83,109,780 字节的 PPTX，包含 147 页和 201 个媒体条目。原件有独立的生产上传任务。以下编辑仅发生在副本上。使用上一节所列摘要的预览包 DLL 及随包 bridge，运行 Microsoft 365 PowerPoint 16.0、Soda v15、复制的 bottle、独立 Weston 显示、挂载检查开启。测试使用低优先级，与桌面共享主机。两轮均通过插件自动连接、正常宿主退出及源 bottle 注册表校验。

| PowerPoint 与同一份课件 | 默认 180 秒发布周期 | 加速复测，15 秒发布周期 |
| --- | ---: | ---: |
| 从首批对象修改到观察到已持久化的第一版恢复快照 | 1.995 秒 | 2.245 秒 |
| 扣住快照回执后，再改首页文字并在末页建表、写单元格 | 35 毫秒 | 39 毫秒 |
| 从首批修改到观察到原生定时保存完成 | 187.503 秒 | 21.733 秒 |
| 等待定时保存期间，约每 40 毫秒查询 Office 状态的最长间隔 | 1.252 秒 | 1.073 秒 |
| 三次原生保存至下一次表格编辑完成（单次） | 1.658、1.647、1.586 秒 | 1.578、1.781、1.707 秒 |
| 最后一次改字后关闭，直到文稿数归零 | 3.786 秒 | 3.978 秒 |
| 立即在同一 PowerPoint 进程重开至读回最后标记 | 0.967 秒 | 0.904 秒 |

第一份快照通过 ZIP CRC，含首版标记，当时挂载文件仍与输入摘要一致。定时保存后的挂载文件含第二次编辑的首页文字和末页表格。扣住旧上传第一片的回执，接着手动保存三次，其间插入、替换图片并立即修改表格。最后再次改字并正常关闭。两轮重开都通过 147 页、最终文本、表格和图片对象断言。最终文件 ZIP CRC 通过，原有 201 个媒体条目的内容摘要全部保留。计时来自 Office COM 和文件屏障；没有键盘到像素的延迟样本或物理断电测试。

### 挂载和接收端

- 安装的正式 0.2.29 daemon 在独立 FUSE 挂载和内存模拟后端上，另做两轮连续同目录临时文件替换。每轮将缓存释放至 online_only，再从模拟后端读回，整份 SHA-256 均匹配第二版。一条并发临时上传报本地条目消失，加做的一轮在 67 秒后的刷新中只剩正文，待删除和元数据队列均为零。
- 两轮 Office 联测使用 v0.2.29-1 的 release 优化构建，仅在临时源码树将 Graph 服务地址指向环回 HTTP，使用虚构凭据和私有数据目录。接收端核对每片起止偏移、长度和顺序，从分片重组独立文件。首轮接收器对短临时文件 PUT 返回 404，遗留 5 条待删除记录；该轮仅用于计时、分片正文和摘要校验。第二轮补全小文件写入、改名、删除及增量元数据响应，两类持久队列最终均为空，按目标名称查询的接收文件、挂载和合并后的平坦缓存摘要一致。释放隔离缓存后，从该 HTTP 接收端重新下载也得到同一摘要。本地接收器只覆盖本实验涉及的 Graph 行为。
- 第二轮收齐 10 个完整分片会话、80 个被接受的分片，共 794.6 MiB。8 个会话确认时使用 Office 临时文件名，合计 635.8 MiB。旧上传被暂停时继续修改，旧版整份接收摘要与定时保存时独立记录的摘要相符。最后一次正文会话在第二片首次请求时收到测试注入的 503，查询偏移并重试后补齐，最终正文与重开时的最新版一致。恢复旧上传回执后约 35.2 秒取得这次最终正文回执。这里的传输时长包含人为控制的回执和后台扫描等待，不能预测公网耗时。

### 资源和重复恢复写入

约每 120 毫秒从 Linux 采样，RSS 为单进程驻留内存；缓存大小按实际分配块统计、硬链接只计一次。写入量来自进程 write_bytes，与存储设备的物理写入存在差异。CPU 百分比按单核 100% 计，峰值使用约 1 秒的窗口。

| 从第一笔编辑至重开验证完成附近 | 180 秒轮 | 15 秒轮 |
| --- | ---: | ---: |
| PowerPoint / bridge / TwoDrive RSS 峰值（MiB） | 1095 / 196 / 53 | 1074 / 157 / 73 |
| PowerPoint / bridge / TwoDrive CPU 峰值 | 99% / 27% / 52% | 100% / 29% / 57% |
| TwoDrive 缓存较已预热课件的额外峰值 | 238.5 MiB | 159.3 MiB |
| 插件恢复目录峰值 | 158.5 MiB | 158.5 MiB |

默认周期中，完成第二批编辑后只查询 Saved 状态。连续 150 秒内，PowerPoint 写入约 1111 MiB，bridge 写入约 1110 MiB，TwoDrive 的写入增量为零。连续六份恢复快照有 586 个 ZIP 条目，幻灯片和媒体摘要相同，变化的条目集中在 docProps/app.xml 与 docProps/core.xml；抽查一对属性观察到 modified 和 revision 值变化。较晚的连续快照间隔约 10 秒。插件在原生 Saved 仍为假时再次调用 SaveCopyAs；bridge 的内容摘要保留 revision 和扩展属性差异，可以为同一可见内容再次完整写盘。新增 test_powerpoint_idle_export_keeps_the_existing_recovery_blob 使用独立缓存和合成 PPTX，将同一页面仅改变导出修订号后再次暂存，已确认断言在当前实现失败。113 项 Python 测试通过，此用例作为 1 项预期失败记录。代码尚未修复。

第二轮最终确认后约 60 秒，后台范围映射清理完毕，平坦缓存摘要正确。在确认到清理之间持续轮换打开、读取 7 个固定偏移的 4 KiB 并关闭，521 个样本内容一致，耗时中位数 13.6 毫秒，P95 为 21.1 毫秒，最长 65.6 毫秒。该窗口内挂载进程约用 2.94 CPU 秒、写入 73.1 MiB，包含并行读取和后台维护。仓库上传会话回归 9 项通过，范围写入、冻结与合并回归 6 项通过，另有 1 项计时基准默认忽略。测试包与默认安装未变更，未发布新版本，未对真实 OneDrive 写入。

## PowerPoint 0.3.2 候选的合并上传与空闲消耗 · 2026-11-19

在隔离 Soda PowerPoint、独立 FUSE 和环回 Graph 协议接收端，使用相同 147 页缓存副本及默认 180 秒。第一次恢复快照耗时 2.74 秒，扣住回执时二次编辑耗时 35 毫秒。定时原生保存 186.09 秒。三次手动保存加下次编辑 1.54–1.61 秒，关闭 4.31 秒，立即重开并核对文字、表格及图片 0.89 秒。

连续 150 秒中，PowerPoint 和桥接的写入从 1111、1110 MiB 降为 317.8、79.3 MiB，包括第二批有效编辑的恢复版本。两者消耗 7.04 和 1.34 CPU 秒。全程一秒窗口内 PowerPoint 峰值 101% 单核；其 RSS 峰值 1009 MiB，桥接 130 MiB，挂载进程 33 MiB。存在单次原生序列化停顿，无持续单核忙等的证据。模拟接收端收到两份正文，临时文件内容上传为零。首次候选同时暴露了旧版本快速轮换时远端正文名短暂消失的问题，由 TwoDrive 端继续处理。

这些数值来自 Windows 加载项、宿主和 Linux 进程采样。真实 OneDrive 网络超时、SSD 物理写放大和用户前台按键显示延迟未在此测试中测量。
