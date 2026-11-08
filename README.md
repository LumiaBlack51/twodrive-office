# TwoDrive Office AutoSave

ONLYOFFICE Desktop Editors、LibreOffice 和 Wine/Soda Microsoft Office 的 TwoDrive 自动保存插件，当前源码为 0.3.1 开发预览版（本地保存与定时同步）。

源代码：[LumiaBlack51/twodrive-office](https://github.com/LumiaBlack51/twodrive-office)。安装包、校验值与验证摘要见 [v0.3.1 Preview Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.1)。

打开位于 TwoDrive 挂载目录内的文档后，会自动开启本地保存。修改先持久保存到挂载外的私有目录，默认每 180 秒将最新版本提交到 TwoDrive，关闭文档或点“立即保存”时立即提交。目录外或未命名文档默认关闭，用户手动开启后保存当前编辑内容的副本。默认副本目录是 `~/TwoDrive/OneDrive/Office AutoSave`，用户可以在开启/保存时选择 TwoDrive 内的其他目录。

插件将频繁编辑合并为本地待同步版本，定时发布到挂载；TwoDrive 的 FUSE/后台服务负责上传。插件包只包含 Office 插件与桥接程序。本插件不会因空开文档而导出/提交；配套 [TwoDrive 0.2.22](https://github.com/LumiaBlack51/twodrive/releases/tag/v0.2.22-1) 另行修复未写入的可写句柄在关闭时排队上传。界面中的“已本地保存”不代表 OneDrive 上传完成。此项目没有修改 TwoDrive 本体、登录方式或后台同步服务。

## 安装

需要 Python 3.10+、正在挂载的 TwoDrive，以及对应办公软件。已测试的实际版本和边界见 [验证记录](docs/verification.md)。

在项目目录运行：

```bash
python3 scripts/build.py
python3 -m twodrive_office.cli configure
python3 scripts/install.py --libreoffice
python3 scripts/install.py --onlyoffice --bridge
```

安装脚本不会启动编辑器、打开文档或改变当前会话的开关。升级后请在自己方便时重新启动办公软件，让新版本的后台启动配置生效。ONLYOFFICE 安装脚本会自动检测本机的 Flatpak 安装；也可以显式传入 `--oo-flatpak`。

可安装包位于 `dist/twodrive-autosave-0.3.1.oxt` 和 `dist/twodrive-autosave-0.3.1.plugin`。通过 ONLYOFFICE 插件管理器安装通用包时，还需要本地 bridge；项目安装脚本会自动写入连接配置。

Wine/Soda 下的 Microsoft 365 x64：解压 `twodrive-autosave-0.3.1-wine-x64.zip`，正常退出 bottle 后运行 `python3 scripts/install_wine.py --prefix /绝对/bottle/路径 --bridge`。使用 Linux 保存服务加原生 COM 加载项，支持 Word、Excel、PowerPoint；操作和兼容边界见 [Wine 安装与说明](docs/wine.md)。原位模式的星号由 Office 原生保存清除，本地暂存成功仍保留星号，默认 180 秒、关闭或“立即保存”时提交。

自定义默认副本目录：

```bash
python3 -m twodrive_office.cli configure --copy-directory "$HOME/TwoDrive/OneDrive/我的文档"
python3 -m twodrive_office.cli status
```

设置文件是 `~/.config/twodrive-office/settings.json`（遵循 XDG_CONFIG_HOME），连接凭据以 0600 权限保存。自定义 TwoDrive 挂载根目录可以通过 `TWODRIVE_MOUNT_DIR` 或 `configure --mount-root` 配置。

## 使用

LibreOffice：使用 TwoDrive 菜单或工具栏的“自动保存 开/关”。目录内文档先保存私有本地版本；目录外/未命名文档首次开启会显示副本目录选择器。菜单还有“选择副本目录”“立即保存”“查看保存状态”。目录内文档自动开启；每个文档可独立暂停，手动关闭后本次打开期间不会自行重开。

ONLYOFFICE：打开文档即在后台检查 TwoDrive 保存位置，目录内文档自动开启，无需打开面板。上方 **TwoDrive → 自动保存** 可查看状态、暂停或手动选择副本目录；关闭面板不停止后台保存。未修改的文档不会仅因打开而重新导出、覆盖；手动暂停后，本次打开期间保持暂停。本地版本持久保存收到成功确认、且编辑器内容仍与保存快照一致后，会更新编辑器的保存点并清除星号；保存期间又有修改或保存失败时保留待保存提示。

面板会明确区分“已提交 TwoDrive”和“已保存本地待同步版本”。副本保存不会改变原文档的未保存状态，关闭原文档仍可能询问是否保存。如果希望自动保存当前文档并清除星号，先手动将它另存到 TwoDrive 挂载目录内，再重新开启插件。无法识别的 Desktop SDK 会保留星号，并在面板说明保存状态无法更新。

相同原生模型和媒体的重复请求会确认已有保存，不重新转换或替换文件；每次仍核对挂载、目标及外部修改。升级时需同时更新桥接服务和插件。安装脚本覆盖 Desktop 9.4 与 10.0 使用的两种用户插件目录布局。

PPTX 原生导出会生成新的修改时间。保存确认只在已识别的 PPTY v1 结构内忽略这一项导出时间，继续完整比较幻灯片、媒体引用和其他属性，避免大课件保存耗时跨秒后永远显示待保存。未知结构继续使用精确比较并保留未保存提示；0.2.1 防止相同内容重复写入的两层检查保持生效。

桥接按需运行：登录时只由现有 systemd 用户管理器保留一个本地监听 socket，不启动 Python 进程或额外监控进程。ONLYOFFICE 打开文档时，后台插件的首次连接才唤醒桥接；ONLYOFFICE 关闭且没有请求或保存任务时，桥接通常在 30 秒内退出，下一次连接可再次唤醒。编辑器仍开着时保留会话，避免最小化或后台标签页导致会话丢失。LibreOffice 在编辑器内部保存，不使用此桥接。

本地待同步版本位于 `~/.local/state/twodrive-office/pending`（遵循 XDG_STATE_HOME），权限为私有；这属于未同步用户数据，清理缓存时不能删除。每个目标只保留当前待同步内容；成功提交后移除其内容和清单。连续编辑按约 1 秒的延迟合并，LibreOffice 连续输入最长 5 秒保存一次。ONLYOFFICE 的文字/光标事件触发保存，其他编辑由最长约 10 秒的周期检查补齐。本地保存失败保留编辑器待保存状态；挂载提交失败保留本地版本并退避重试。原位模式的保存标记表示本地版本已持久保存，目标原文件可滞后最多一个同步周期。Ctrl+S 使用编辑器自身保存行为，可提前触发上传；用户也可以点“立即保存”。不会在自动保存计时器中弹出选择器、密码框或错误对话框。

同步间隔可通过 `python3 -m twodrive_office.cli configure --sync-interval 180` 设置（1–3600 秒，下次开启生效）。`python3 -m twodrive_office.cli pending` 可只读列出待同步版本和目标。意外退出后，先正常关闭办公软件，再运行 `python3 -m twodrive_office.cli recover`；ONLYOFFICE 的按需桥接也会在确认所有办公软件退出后重试恢复。为避免用旧的内存文档覆盖恢复内容，存在崩溃遗留版本的目标会拒绝开启新会话，直至安全恢复。冲突不会自动覆盖；保留的 `.document` 是对应格式的完整文件，可复制为相应扩展名后另行检查。

副本名带源文件路径的短摘要，避免不同目录的同名文档互相覆盖。未命名文档使用会话唯一标识。源文档另存为新文件、目标被其他程序修改，或挂载断开时，保存会停止/报告待处理状态；检查后重新开启。

## 当前范围

- 支持 DOCX、XLSX、PPTX 和 ODT、ODS、ODP。LibreOffice 覆盖 Writer、Calc、Impress。
- ONLYOFFICE 使用 Desktop 原生模型快照和随软件安装的 x2t 转换器；公开的 `GetFileToDownload` 在本机本地文档上无法完成导出。此适配器依赖桌面版本暴露的接口和本地 frame 权限，升级后需要重新验证。浏览器中的 ONLYOFFICE Docs 不在此包的范围内。
- 桥接服务只监听 127.0.0.1，校验凭据、Origin 和 Host，不下载任意 URL，也不接受目标目录之外的写入。快照上限为 128 MiB。
- 当前拒绝加密文档、含宏文件和旧版二进制 Office 源格式。复杂嵌入对象、音视频、完整大型文档样本集合还需要额外兼容性验证。不要把当前验证结果推及所有文档类型或所有软件版本。
- 尚未验证真实 OneDrive 端到端上传。真实办公软件测试使用临时目录模拟挂载，并保留真实云端及用户文档不变。

## 开发与验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/onlyoffice*.test.js
python3 scripts/build.py
/usr/bin/python3 tests/libreoffice_integration.py
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich
```

LibreOffice 测试使用系统 Python 的 UNO 模块和独立后台 profile。ONLYOFFICE 测试使用经过认证的独立 Xvfb 显示；禁止连接开发者正在工作的显示。相关结果与实际发现见 [验证记录](docs/verification.md)。

代码结构：`twodrive_office/` 是保存策略、调度、本地 bridge 和转换器；`libreoffice/` 是 UNO 扩展；`onlyoffice/` 是面板及桌面适配器；`wine/` 是 Microsoft Office 的原生 x64 COM 入口；`scripts/` 负责构建和安装。

## 许可

本项目原创代码使用 [MIT](LICENSE) 许可。随包附带的 ONLYOFFICE `plugins.js` 保留其原始 AGPL-3.0 及附加条款声明，许可证与来源说明位于 `onlyoffice/vendor/`；根目录 MIT 许可不覆盖第三方代码。安装的 ONLYOFFICE/LibreOffice 软件保持各自许可。

参考：[TwoDrive](https://github.com/LumiaBlack51/twodrive)、[LibreOffice XStorable](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1frame_1_1XStorable.html)、[ONLYOFFICE 插件接口](https://api.onlyoffice.com/docs/plugins/interacting-with-editors/overview)、[Desktop SDK](https://github.com/ONLYOFFICE/desktop-sdk)。
