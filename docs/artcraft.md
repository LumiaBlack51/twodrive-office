# TwoDrive Craft 普通插件 0.2.0-preview.2

**已实现协议预览，暂不安装。** 2026-11-15 核对的官方版本（WordCraft 0.18.1、GridCraft 0.19.1、DeckCraft 0.18.1）没有所需的公共插件宿主。插件面向尚未合并的共同插件协议 2.2；需等官方发布包含相应接口的版本，再核对协议并进行应用内验证。预览包不表示官方已经支持。

插件为 Python 3.10+ 程序，由 Office 的插件宿主启动和停止，通过标准输入/输出传输 JSON-RPC。更新插件只需更新插件文件，不需要重新编译 Office；未来官方升级若改变协议，需要重新检查兼容性。插件不修改 Office 二进制、原生 AutoSave 开关、恢复功能、保存点或原文档路径，也不移除 Office 内置功能。

## 应用内操作

官方宿主支持并安装后，正常双击 DOCX、XLSX 或 PPTX，在原应用的 **TwoDrive** 标签内使用以下入口，无需专用启动器：

- **自动同步**：原生表单中勾选“自动同步已保存版本”，填写 TwoDrive 副本目录和提交间隔，点“应用”。每个打开的文档单独启用，默认关闭；关闭后下次打开需重新启用。
- **立即同步**：请求下一次检查立即提交磁盘上的已保存版本。
- **暂停同步**：停止跟踪此文档，并尝试提交最后已保存版本；失败时保留修订。
- **保存状态**：显示待提交、已提交挂载目录或需要处理的错误。
- **保留修订**：显示本地恢复清单的位置。定时器自身不弹对话框。

**这份协议没有三款编辑器通用的完整未保存文档导出接口。** 因此插件只能自动同步编辑器已写入磁盘的版本，不能替用户保存内存中的修改。WordCraft/GridCraft 原生 AutoSave 如果确实写入源文件，插件会读取该版本；DeckCraft 仍需使用原生保存或 Ctrl+S。原生 AutoRecover 不等于源文件已保存。界面和协议结果均明确标注这个边界，不能将“已提交”理解为未保存修改也已同步。

即使源文件位于 TwoDrive 内，插件也只写独立副本，名称带源文件路径摘要；它不会把原文档重定向到私有工作副本。源文件本身的原生保存仍由编辑器负责。支持 DOCX/XLSX/PPTX 与对应 ODT/ODS/ODP；拒绝链接源文件、加密或含宏文档、旧二进制格式及超过 128 MiB 的文件。另存为改变路径时暂停旧会话，需要在新路径重新启用。

## 构建与后续安装

```sh
python3 -B scripts/build_artcraft.py
python3 -B scripts/install_artcraft.py
```

第二条命令现在只是只读兼容性检查：使用临时 XDG 配置运行 `*-cli commands`，不启动图形编辑器、不安装、不创建额外应用入口。发现 `plugins.load/list` 也仅表示存在候选宿主，还需核对官方协议 2.2、稳定文档 id、事件及原生对话框能力。

输出包为 `dist/twodrive-artcraft-plugin-0.2.0-preview.2-linux.zip` 和 `.sha256`。ZIP 根目录包含 `plugin.json`、`main.py` 和配套 Python 模块。当前只交付预览包，**不要复制进日常 Office 插件目录，也不要编译非官方 Office 替换正常版本**。

待官方支持后，先核对本说明及官方发布记录，再校验 ZIP、解压到插件专用目录，通过官方插件管理器加载这个完整目录。仓库中的 `artcraft/` 清单用于开发；源代码安装器若只复制该子目录会缺失共享模块，故应使用完整发布 ZIP。不要使用本仓库其他 Office 的安装包。尚未验证未来官方插件管理器的具体安装步骤。

默认配置只读取 `~/.config/twodrive-office/settings.json`，遵循 XDG_CONFIG_HOME；未配置时使用 `TWODRIVE_MOUNT_DIR` 或 `~/TwoDrive/OneDrive`，默认副本目录 `Office AutoSave`、提交间隔 180 秒。表单设置仅限当前打开会话，不改原生 Office 配置。每次提交均重新验证挂载、目标范围、链接和外部修改；检查失败保留本地修订，不自动接受新的目标基线。约每秒检查源文件是否变化；文件正在原生保存时稍后重试。

“已提交挂载目录”只表示本地文件提交成功，OneDrive 上传由 TwoDrive 负责。本次未测试真实挂载或云端上传。

## 失败数据与恢复

恢复数据位于 `~/.local/state/twodrive-office/artcraft-plugin/`，遵循 XDG_STATE_HOME：

- 会话目录含 `session.json`、`revision.json` 和完整的 `<SHA256>.document`；断挂载后尚未进入提交队列的版本也会保留在这里。
- `pending/` 是本插件专用的持久提交队列，与其他 Office 集成的队列分开；目标锁阻止多个插件进程同时写同一副本。
- 插件退出后不保留独立守护进程。重新启动不会自动发布崩溃遗留版本；有遗留内容的目标拒绝新会话。目标被其他程序修改时不会覆盖。

恢复只提供**导出本地新文件**，不会重新发布旧队列、覆盖现有文件或删除原修订。在解压包目录运行：

```sh
python3 -B main.py --list-revisions
python3 -B main.py --export-revision 会话目录的32位标识 --output /本地目录/recovered.pptx
```

输出必须位于 TwoDrive 挂载目录外，扩展名必须匹配，目标必须不存在。导出会校验摘要及文档容器，文件权限为 0600。请正常关闭编辑器后比较各版本，确认所需内容再手动保存/同步；保留数据不会自动清除。若写入在清单完成前中断，`session.json` 与同目录的完整 `.document` 也会阻止新会话，应逐份复制成对应扩展名检查。不要把恢复目录当缓存删除。

旧版 0.1.1 companion 已撤回安装流程；本包不包含它，也不生成其桌面入口。旧会话位于 `artcraft/` 和公共 `pending/`，应继续保留其恢复管理程序和唯一数据，不能用本插件的恢复命令处理。旧版测试记录仅说明历史 companion，不证明本插件已在官方应用内运行。

## 协议来源与验证

共同协议来源是以下未合并提案，不是官方已发布 API：

- [WordCraft #2586](https://github.com/storytold/wordcraft/pull/2586)，固定提交 `74eaa3184f2408b1fe76f210075370b913916551`。
- [GridCraft #1123](https://github.com/storytold/gridcraft/pull/1123)，固定提交 `3e325c1c2c2e35cf7acc6c01bda8b326739ec0a3`。
- [DeckCraft #919](https://github.com/storytold/deckcraft/pull/919)，固定提交 `4763e9497687ac260d6cb5d83f3f86c3ea3206d1`。

`protocol-sources.json` 记录具体来源与通信/对话框模块 SHA256。清单要求 `host.protocol >=2.2, <3` 和 `dialogs/document/events`；JSON-RPC 握手的 `protocolVersion` 仍为整数 `1`。文档事件只通知明确的打开、保存和关闭，不含 AutoSave 写盘，因此还需轮询磁盘。插件不发送编辑器操作、`host/sync` 或原生 `file.save`。

```sh
python3 -B -m unittest discover -s tests -p 'test_artcraft_plugin.py'
# 可选：需要 Rust 和已缓存的 serde/serde_json/log，临时下载固定的上游模块
python3 -B tests/artcraft_host_conformance.py --cargo-home /临时/CARGO_HOME
```

上游一致性脚本只编译固定提案的 `process.rs`、`push.rs`、`dialog.rs`，使用最小 Manifest/锁辅助代码，与真实插件进程往返；不编译/运行 Office 引擎、窗口或插件管理器。所有保存测试都使用临时模拟挂载及文档。实际结果及兼容限制见 [验证记录](verification.md)。
