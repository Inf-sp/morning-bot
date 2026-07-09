#!/usr/bin/env python3
"""Синхронизирует VERSION с последним заголовком в RELEASE_NOTES.md."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_NOTES = ROOT / "RELEASE_NOTES.md"
VERSION_FILE = ROOT / "VERSION"

VERSION_RE = re.compile(r"^##\s*v(\d+\.\d+\.\d+)")


def latest_version() -> str | None:
    for line in RELEASE_NOTES.read_text(encoding="utf-8").splitlines():
        m = VERSION_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


def main() -> int:
    version = latest_version()
    if not version:
        print("sync_version: не нашёл заголовок вида '## vX.Y.Z' в RELEASE_NOTES.md", file=sys.stderr)
        return 1
    current = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else ""
    if current == version:
        return 0
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"sync_version: VERSION обновлён {current or '(нет)'} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
