# TwoDrive Office AutoSave

ONLYOFFICE Desktop Editors 和 LibreOffice 的 TwoDrive 自动保存插件，当前为 0.1.0 开发预览版。

源代码：[LumiaBlack51/twodrive-office](https://github.com/LumiaBlack51/twodrive-office)。安装包与校验值见 [v0.1.0 Release](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.1.0)。

开启当前文档的开关后：文档在 TwoDrive 挂载目录中就保存到原位置；在目录外或尚未命名，就保存当前编辑内容的副本。默认副本目录是 `~/TwoDrive/OneDrive/Office AutoSave`，用户可以在开启/保存时选择 TwoDrive 内的其他目录。

插件负责本地保存，TwoDrive 的 FUSE/后台服务负责上传。界面中的“已本地保存”不代表 OneDrive 上传完成。此项目没有修改 TwoDrive 本体、登录方式或后台同步服务。

## 安装

需要 Python 3.10+、正在挂载的 TwoDrive，以及对应办公软件。已测试的实际版本和边界见 [验证记录](docs/verification.md)。

在项目目录运行：

```bash
python3 scripts/build.py
python3 -m twodrive_office.cli configure
python3 scripts/install.py --libreoffice
python3 scripts/install.py --onlyoffice --bridge
```

安装脚本不会启动编辑器，也不会打开任何文档或替用户开启自动保存。若编辑器正在运行，请在自己方便时关闭并重新打开，再使用插件。ONLYOFFICE 安装脚本会自动检测本机的 Flatpak 安装；也可以显式传入 `--oo-flatpak`。

可安装包位于 `dist/twodrive-autosave-0.1.0.oxt` 和 `dist/twodrive-autosave-0.1.0.plugin`。通过 ONLYOFFICE 插件管理器安装通用包时，还需要本地 bridge；项目安装脚本会自动写入连接配置。

自定义默认副本目录：

```bash
python3 -m twodrive_office.cli configure --copy-directory "$HOME/TwoDrive/OneDrive/我的文档"
python3 -m twodrive_office.cli status
```

设置文件是 `~/.config/twodrive-office/settings.json`（遵循 XDG_CONFIG_HOME），连接凭据以 0600 权限保存。自定义 TwoDrive 挂载根目录可以通过 `TWODRIVE_MOUNT_DIR` 或 `configure --mount-root` 配置。

## 使用

LibreOffice：使用 TwoDrive 菜单或工具栏的“自动保存 开/关”。目录内文档直接保存；目录外/未命名文档首次开启会显示副本目录选择器。菜单还有“选择副本目录”“立即保存”“查看保存状态”。每个文档独立开关，默认关闭。

ONLYOFFICE：在插件中打开 TwoDrive AutoSave 面板。插件自动识别当前文件，选择副本目录并开启开关。请保持面板打开；关闭面板即结束该文档的自动保存。原位保存由本地桥接服务写入，因此 ONLYOFFICE 自身的未保存标记可能仍然显示。

连续编辑按约 1 秒的延迟合并，LibreOffice 连续输入最长 5 秒保存一次。ONLYOFFICE 的文字/光标事件触发保存，其他编辑由最长约 10 秒的周期检查补齐。保存失败保留待保存状态并退避重试；用户也可以点“立即保存”。不会在自动保存计时器中弹出选择器、密码框或错误对话框。

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
node --test tests/onlyoffice.test.js
python3 scripts/build.py
/usr/bin/python3 tests/libreoffice_integration.py
python3 tests/onlyoffice_integration.py --xvfb /absolute/path/to/Xvfb --rich
```

LibreOffice 测试使用系统 Python 的 UNO 模块和独立后台 profile。ONLYOFFICE 测试使用经过认证的独立 Xvfb 显示；禁止连接开发者正在工作的显示。相关结果与实际发现见 [验证记录](docs/verification.md)。

代码结构：`twodrive_office/` 是保存策略、调度、本地 bridge 和转换器；`libreoffice/` 是 UNO 扩展；`onlyoffice/` 是面板及桌面适配器；`scripts/` 负责构建和安装。

## 许可

本项目原创代码使用 [MIT](LICENSE) 许可。随包附带的 ONLYOFFICE `plugins.js` 保留其原始 AGPL-3.0 及附加条款声明，许可证与来源说明位于 `onlyoffice/vendor/`；根目录 MIT 许可不覆盖第三方代码。安装的 ONLYOFFICE/LibreOffice 软件保持各自许可。

参考：[TwoDrive](https://github.com/LumiaBlack51/twodrive)、[LibreOffice XStorable](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1frame_1_1XStorable.html)、[ONLYOFFICE 插件接口](https://api.onlyoffice.com/docs/plugins/interacting-with-editors/overview)、[Desktop SDK](https://github.com/ONLYOFFICE/desktop-sdk)。
