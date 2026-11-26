# TwoDrive Office for Wine / Soda

This ZIP contains the native x64 Word, Excel and PowerPoint add-in and its Linux bridge. Exit the target bottle normally. In the extracted directory, configure the mount, then choose **one** installer command for your bottle. Replace the example prefix with your bottle's absolute path.

```bash
python3 -m twodrive_office.cli configure
```

**Standard Wine / Soda bottle**

```bash
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge
```

**cpak bottle with isolated networking** — enable on-demand relay activation so Office can reach the bridge.

```bash
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge --sandbox-wakeup
```

For updates, uninstallation and recovery, see the [Wine guide](docs/wine.md). [Tested versions and limits](docs/verification.md) · [Other editors](https://github.com/LumiaBlack51/twodrive-office)
