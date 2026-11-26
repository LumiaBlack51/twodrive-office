# Linux 使用与恢复

[返回首页](../README.md) · [Wine / Soda](wine.md) · [验证记录](verification.md)

本页适用于 LibreOffice、ONLYOFFICE Desktop Editors 和 Wine / Soda 的本地保存队列。Windows 原生加载项使用 CFAPI 同步根，Craft 预览使用独立的修订目录。两者各有自己的操作指南。[Windows 原生 Office](msoffice.md) · [Craft](artcraft.md)

## 文档保存到哪里

| 打开的文档 | 默认行为 | 需要操作时 |
| --- | --- | --- |
| TwoDrive 挂载目录内已有文件 | 自动开启原位保存。只打开、没有修改的文档会跳过导出。 | 在 TwoDrive 菜单或面板暂停这份文档，或点 **立即保存** 提前提交。 |
| 挂载目录外的文件 | 默认关闭。手动开启后，修改写入 TwoDrive 内的独立副本。 | LibreOffice 或 ONLYOFFICE 可以在开启时选择副本目录。Wine 使用配置好的默认目录。 |
| 尚未命名的文档 | 手动开启后按会话生成唯一副本名。关闭原文档时，编辑器仍可能询问是否保存。 | 要启用原位保存，先用编辑器将原文档另存到挂载目录内，再重新开启。 |

有路径的源文件会在副本名中带上路径摘要，避免不同目录下的同名文档相互覆盖。另存为改变了源路径后，应检查新目标，再开启保存。

## 在编辑器里查看状态

**LibreOffice**　在 TwoDrive 菜单或工具栏使用 **自动保存 开/关**。同一菜单可选择副本目录、立即保存或查看状态。Writer、Calc 和 Impress 在编辑器内部暂存修改。连续输入时会合并多次修改，最长约五秒安排一次本地保存。

**ONLYOFFICE Desktop Editors**　打开 **TwoDrive → 自动保存**。关闭面板后，后台仍会保存。收到持久化确认且当前内容仍与快照一致时，支持的 Desktop SDK 会更新原位文档的保存点并清除星号。保存期间又有编辑、写入失败或 SDK 无法识别时，星号会保留。副本模式保留原文档的保存状态。文字与光标事件会触发检查，其他修改由最长约十秒的周期检查补齐。

**Microsoft Office on Wine / Soda**　在 TwoDrive 功能区查看每份文档的本地状态。原位模式下，本地恢复版本写好后仍可能看到星号，Office 将文件保存到挂载目录后才清除。长文稿导出和 Office 忙碌都会影响保存时机。[Wine 保存行为与兼容性](wine.md)

## 调整目录与提交间隔

以下命令从源码目录运行。默认挂载位置是 `~/TwoDrive/OneDrive`，副本目录是其中的 `Office AutoSave`。如需调整，可指定已有的挂载和其中的副本目录。

```bash
python3 -m twodrive_office.cli status
python3 -m twodrive_office.cli configure --mount-root "$HOME/TwoDrive/OneDrive"
python3 -m twodrive_office.cli configure --copy-directory "$HOME/TwoDrive/OneDrive/我的文档"
python3 -m twodrive_office.cli configure --sync-interval 180
```

提交间隔范围为 1–3600 秒，下次开启保存时生效。关闭文档或点 **立即保存** 会尝试提前提交，Ctrl+S 继续执行编辑器自己的保存。设置保存在 `~/.config/twodrive-office/settings.json`，遵循 `XDG_CONFIG_HOME`。连接凭据文件权限为 `0600`。默认挂载还可以通过 `TWODRIVE_MOUNT_DIR` 指定。

## 恢复待同步版本

1. **查看已有修订。** 在源码目录运行 `python3 -m twodrive_office.cli pending`，只读列出目标和本地内容路径。完整修订默认位于 `~/.local/state/twodrive-office/pending`，遵循 `XDG_STATE_HOME`。清理缓存时请保留这个目录。
2. **确认挂载并正常关闭办公软件。** 运行 `python3 -m twodrive_office.cli status` 检查挂载。存在崩溃遗留修订时，对应目标会拒绝开启新会话，以免旧内容覆盖恢复结果。
3. **重试提交。** 运行 `python3 -m twodrive_office.cli recover`，再用 `pending` 查看剩余项目。若目标已被其他程序修改，保留队列中的原件，将完整的 `.document` 复制到挂载目录外，以对应扩展名另存并比较，再决定保留哪一版。

提交前会重新核对挂载、目标位置和外部修改。失败的提交会保留已暂存的版本。成功写入挂载后，TwoDrive 继续处理云端上传。[查看 TwoDrive 传输状态](https://github.com/LumiaBlack51/twodrive/blob/main/doc/usage.zh-CN.md)

## ONLYOFFICE 安装与兼容性

源码安装脚本可检测 Flatpak，也可用 `python3 scripts/install.py --onlyoffice --oo-flatpak --bridge` 指定。升级时请同时更新桌面插件和桥接服务，正常重启编辑器。脚本支持 Desktop 9.4 与 10.0 的两种用户插件目录布局。

桥接由 systemd 用户 socket 按需启动，只监听本机回环地址，并校验连接凭据、Origin 和 Host。没有进行中的请求且最后一次认证请求或心跳过去约 30 秒后，Python 进程退出。下次连接可再次启动桥接。快照上限为 128 MiB。

本地文档通过 Desktop 原生模型和随附的 x2t 转换器导出。公开的网页插件接口不足以完成此流程，升级 Desktop SDK 后需要重新验证。相同模型和媒体可复用已有保存。对已识别的 PPTY v1，比较时会忽略导出器生成的修改时间，同时检查幻灯片、媒体引用和其他属性。未知结构仍逐字节比较。[ONLYOFFICE 验证记录](verification.md) · [公开插件 API](https://api.onlyoffice.com/docs/plugins/interacting-with-editors/overview) · [Desktop SDK](https://github.com/ONLYOFFICE/desktop-sdk)
