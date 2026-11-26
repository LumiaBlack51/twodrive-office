# TwoDrive Office for Wine / Soda

This ZIP contains the native x64 Word, Excel and PowerPoint add-in and its Linux bridge. Exit the target bottle normally, then run the installer from the extracted directory. Replace the example prefix with your bottle's absolute path.

```bash
python3 -m twodrive_office.cli configure
python3 scripts/install_wine.py --prefix /absolute/path/to/bottle --bridge
```

For cpak networking, updates, uninstallation and recovery, see the [Wine guide](docs/wine.md). [Tested versions and limits](docs/verification.md) · [Other editors](https://github.com/LumiaBlack51/twodrive-office)
