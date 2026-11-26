#!/usr/bin/env python3
"""Ordinary Craft add-in entry point; the host owns process startup and lifetime."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE if (HERE / "twodrive_office").is_dir() else HERE.parent))
from twodrive_office.artcraft_plugin import main

if __name__ == "__main__":
    main()
