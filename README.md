<p align="center">
  <img src="onlyoffice/icon.svg" width="72" height="72" alt="TwoDrive 云朵图标">
</p>
<h1 align="center">TwoDrive Office AutoSave</h1>
<p align="center"><strong>在熟悉的编辑器里保存文档，由 TwoDrive 同步到 OneDrive。</strong></p>
<p align="center">简体中文 · <a href="README.en.md">English</a></p>
<p align="center">
  <a href="#安装"><strong>开始使用</strong></a> ·
  <a href="https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.2">下载 Linux 预览版</a> ·
  <a href="docs/usage.md">使用与恢复</a> ·
  <a href="docs/verification.md">验证记录</a>
</p>

在 Linux 上打开 TwoDrive 挂载目录里的文档，插件会自动为这份文档开启保存。编辑时先在本地保留可恢复的版本，默认每 **180 秒**把最新修改写入挂载目录。需要马上提交时，点编辑器里的 **立即保存**。云端上传进度可在 TwoDrive 中查看。

## 选择编辑器

| 环境 | 保存方式 | 从哪里开始 |
| --- | --- | --- |
| **LibreOffice** · Writer、Calc、Impress | 自动保存原位文档。目录外或未命名的文档可手动开启副本保存。 | [安装 OXT](#安装) |
| **ONLYOFFICE Desktop Editors** | 后台保存，在 **TwoDrive → 自动保存** 查看状态或暂停。需要本地桥接服务。 | [安装桌面插件和桥接服务](#安装) |
| **Microsoft 365 x64 · Wine / Soda** | Word、Excel、PowerPoint 先暂存本地版本，原位提交时调用 Office 原生保存。 | [Wine 安装指南](docs/wine.md#installation) |
| **Microsoft Office · 原生 Windows** | 源码预览。绑定账户的 TwoDrive Development 同步根内，已命名的 DOCX、XLSX、PPTX 默认按 **30 秒**间隔保存。 | [Windows 构建与安装](docs/msoffice.md#build-and-install) |
| **WordCraft、GridCraft、DeckCraft** | 协议预览。只同步编辑器已写入磁盘的版本，安装须等官方宿主支持并完成验证。 | [Craft 兼容性与恢复](docs/artcraft.md) |

## 安装

Linux 需要 **Python 3.10+**、对应编辑器，以及已登录并挂载的 TwoDrive。默认挂载目录是 `~/TwoDrive/OneDrive`。还没有挂载时，先完成 [TwoDrive 安装与登录](https://github.com/LumiaBlack51/twodrive/blob/main/doc/getting-started.zh-CN.md)。

**LibreOffice 或 ONLYOFFICE**　在源码目录配置挂载，然后只运行对应编辑器的安装命令。安装脚本会构建插件，下次启动编辑器时生效。需要安装脚本时，可以下载并解压 [v0.3.2 源码 ZIP](https://github.com/LumiaBlack51/twodrive-office/archive/refs/tags/v0.3.2.zip)。

```bash
python3 -m twodrive_office.cli configure

# LibreOffice
python3 scripts/install.py --libreoffice

# ONLYOFFICE Desktop Editors
python3 scripts/install.py --onlyoffice --bridge
```

**Wine / Soda**　下载并解压 `twodrive-autosave-0.3.2-wine-x64.zip`，正常退出目标 bottle，然后在解压目录按指南安装加载项和桥接服务。v0.3.2 发布页同时提供 `.oxt`、`.plugin` 和 `SHA256SUMS`。手动安装 ONLYOFFICE 通用包仍需配置桥接服务。[下载与校验文件](https://github.com/LumiaBlack51/twodrive-office/releases/tag/v0.3.2) · [Wine 安装步骤](docs/wine.md#installation)

## 使用

| 在 Linux 上看到的状态 | 文件在哪里 |
| --- | --- |
| **已保存本地待同步版本** | 挂载目录外的私有队列已有完整副本。此时挂载目录里的文件可能仍是上一版。 |
| **已提交 TwoDrive** | 最新版本已写入挂载目录。后续 OneDrive 上传由 TwoDrive 后台处理。 |
| **挂载断开或文件冲突** | 已暂存的版本会保留。重新连接挂载，或比较本地版本与目标文件后，再处理恢复。 |

当前文档处于副本模式时，原文件保留自己的未保存标记。默认副本目录是 `~/TwoDrive/OneDrive/Office AutoSave`。切换目录、调整间隔、查看待同步版本和意外退出后的恢复步骤见 [Linux 使用与恢复指南](docs/usage.md)。

## 当前范围

LibreOffice 和 ONLYOFFICE 的目标格式为 DOCX、XLSX、PPTX、ODT、ODS、ODP，实际验证过的编辑器和格式组合见记录。ONLYOFFICE 的本地导出依赖 Desktop 原生模型和随附的 x2t 转换器。加密、含宏和旧二进制文档不在支持范围。[兼容性与历次验证](docs/verification.md)

## 开发与验证

在仓库根目录运行单元测试和插件构建。真实编辑器测试的隔离要求、版本和结果见 [开发约定](AGENTS.md) 与 [验证记录](docs/verification.md)。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/onlyoffice*.test.js
python3 scripts/build.py
```

## 许可

原创代码使用 MIT 许可。随包附带的 ONLYOFFICE `plugins.js` 使用 AGPL-3.0 并保留上游附加条款。[MIT 许可](LICENSE) · [第三方声明](onlyoffice/vendor/NOTICE.txt)
