# Artcraft TwoDrive AutoSave companion 0.1.1

适用于 Linux 的 WordCraft 0.18.1、GridCraft 0.19.1、DeckCraft 0.18.1。三款应用没有可加载的通用 Office add-in API，本扩展使用独立启动入口、私有工作副本和原生控制接口，不修改应用二进制或日常 Office profile。其他版本需要重新验证。

## 安装与打开

需要 Python 3.10+、bubblewrap (`bwrap`)、对应 Artcraft 应用和 TwoDrive 挂载。可以先写代码、构建；办公软件安装就绪后再运行安装器。

```sh
python3 scripts/build_artcraft.py
python3 scripts/install_artcraft.py
```

安装器不启动编辑器。它新增三个“WordCraft / GridCraft / DeckCraft — TwoDrive AutoSave”入口和 `~/.local/bin/twodrive-{wordcraft,gridcraft,deckcraft}`，保留原有应用入口和文件默认关联。在文件管理器的“打开方式”中选择对应 TwoDrive 入口，或执行：

```sh
twodrive-wordcraft "$HOME/TwoDrive/OneDrive/report.docx"
twodrive-gridcraft "$HOME/TwoDrive/OneDrive/budget.xlsx"
twodrive-deckcraft "$HOME/TwoDrive/OneDrive/slides.pptx"
```

每次打开一个已有的 DOCX、XLSX 或 PPTX。目录外文档需要明确选择副本目的地：

```sh
twodrive-wordcraft /path/report.docx --copy-directory "$HOME/TwoDrive/OneDrive/Office AutoSave"
```

新建文档先正常保存为相应格式，再通过 TwoDrive 入口打开。本版没有接管普通 Artcraft 窗口、所有“新建”文档、其他格式或同一窗口后来打开的文档。

## AutoSave 与同步

原生编辑器打开挂载外的私有本地工作副本。WordCraft 在该副本上执行一次本地 Save，满足它“本次会话明确保存后才允许 AutoSave”的要求，然后开启原生开关；这次初始化不会覆盖挂载源文件。GridCraft 通过独立配置开启原生 AutoSave。两者均可在原生开关暂停；扩展只收集编辑器确实写入本地的文件，不另外强制保存被暂停的编辑内容。

DeckCraft 当前的 AutoRecover 不等于文档 AutoSave。扩展每约 3 秒检查一次；仅在唯一文档的路径等于本会话工作副本、文档已修改且没有对话框时调用原生 `file.save`，使用明确的本地 PPTX 路径，避免 Save As 对话框。存在多个文档时暂停这种补充保存。它不声称与不存在的原生 AutoSave 开关集成。`twodrive-artcraft pause SESSION_ID` / `resume SESSION_ID` 控制 DeckCraft 的补充保存。

原生 Save 或 Ctrl+S 保存到私有工作副本，并清除编辑器的本地未保存标记。扩展每约 0.5 秒收集已完成的文件保存，复用 TwoDrive Office 的持久待同步队列，默认约 180 秒提交挂载；编辑器正常退出时提交最新已保存版本，也可用 `twodrive-artcraft flush SESSION_ID` 提前提交。原生保存点表示本地文件已保存，不表示 TwoDrive 挂载提交或 OneDrive 上传完成。发布频率使用现有 `~/.config/twodrive-office/settings.json` 的 `sync_interval_seconds`。

每次收集和发布均校验挂载、目标、符号链接和外部冲突。打开未改、关闭未改不会覆盖原文件。挂载断开时，尚不能进入公共待同步队列的新保存仍保留在本地工作文件中；已有待同步修订也保留。错误写入 `session.json`，自动保存不弹对话框。

```sh
twodrive-artcraft sessions
twodrive-artcraft recover SESSION_ID
```

`sessions` 显示目标、工作文件、待同步状态及错误。恢复前请正常关闭所有 Artcraft 编辑器；恢复会校验目标未被外部修改，把保留的完整本地文件提交到挂载。有保留会话的目标会拒绝重新打开，包括断挂载后尚未进入 `pending/` 的本地保存。已有待同步修订时，只有它的内容和原始目标 fingerprint 都与所选会话一致才直接发布；存在不同版本时拒绝自动替换，保留两份数据供人工比较选择。请保留 `~/.local/state/twodrive-office/artcraft/` 中未完成会话和现有 `pending/`；这些属于恢复数据。干净退出、成功提交后的会话目录及复制的配置、日志会删除。意外退出的私有 profile 可能含额外 AutoRecover 数据，恢复成功前一并保留。

## 隔离与兼容边界

启动入口通过 bubblewrap 为伴随进程和编辑器创建专用网络命名空间，所有控制端口仅在其中可达。WordCraft 的 key 与 GridCraft 的 token 仍参与认证；DeckCraft 此版本的控制接口没有认证，因此本扩展拒绝省略网络隔离。会话中的编辑器没有外网访问；TwoDrive 的宿主 FUSE 服务仍负责网络上传。所有 XDG/profile 路径均独立，不改变正常 Office 配置；用户自行通过此入口打开文档时才显示编辑器。

基于应用原生格式导入/保存的保真能力，复杂图形、宏、加密、外链对象和未验证版本不在本版承诺范围内；本版拒绝宏格式、加密源文件和非 DOCX/XLSX/PPTX 源格式。不要通过另存为覆盖真实挂载文件来绕过扩展的校验。在工作窗口里“另存为”到其他位置后，扩展只继续观察原私有工作文件；新目标需要重新通过 TwoDrive 入口打开。

验证脚本：`python3 -B -m unittest discover -s tests -p 'test_*.py'`；真实应用测试：`python3 -B tests/artcraft_integration.py --xvfb /absolute/path/to/Xvfb`。实测版本、命令、结果及局限见 [verification.md](verification.md)。测试只使用隔离显示、临时配置和模拟挂载，没有验证真实 OneDrive 上传。

上游接口参考：[WordCraft control protocol](https://github.com/storytold/wordcraft/blob/v0.18.1/docs/control-protocol.md)、[GridCraft](https://github.com/storytold/gridcraft)、[DeckCraft](https://github.com/storytold/deckcraft)。
