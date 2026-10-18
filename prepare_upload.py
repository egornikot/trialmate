#!/usr/bin/env python3
"""Make a clean copy of TrialMate for GitHub: no .env (your keys), no database, no caches.

    py prepare_upload.py      (or double-click prepare_upload.bat)

Creates ../trialmate-github next to this folder. Upload that folder, never this one.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT.parent / "trialmate-github"
SKIP_NAMES = {".env", "data", "__pycache__", ".DS_Store", "results", "check-result.txt"}


def ignore(directory: str, names: list) -> set:
    return {n for n in names if n in SKIP_NAMES or n.endswith(".pyc") or n.endswith(".db")}


def main() -> None:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(ROOT, TARGET, ignore=ignore)
    leaked = [p for p in TARGET.rglob(".env")]
    if leaked:
        raise SystemExit("Refusing to continue: a .env file was copied. Delete it from trialmate-github.")
    files = sum(1 for p in TARGET.rglob("*") if p.is_file())
    print(f"\n  Clean copy ready: {TARGET}")
    print(f"  {files} files, no .env, no database. Upload this folder to GitHub (see docs/GITHUB.md).\n")


if __name__ == "__main__":
    main()
