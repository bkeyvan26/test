# -*- coding: utf-8 -*-
"""
K1 VMS — پاک‌سازی credential از JSON/YAML/LOG
قبل از هر commit اجرا کن:  python scripts/scrub_secrets.py
"""
import re
import sys
from pathlib import Path

PATTERNS = [
    # rtsp://user:pass@host
    (re.compile(r'(rtsp[s]?://[^:/@\s]+:)[^@/\s]+(@)'), r'\1***\2'),
    # http(s)://user:pass@host
    (re.compile(r'(https?://[^:/@\s]+:)[^@/\s]+(@)'), r'\1***\2'),
    # "password": "..."
    (re.compile(
        r'("(?:password|passwd|pwd|secret|token|api_key|apikey|auth)"'
        r'\s*:\s*")[^"]+(")',
        re.IGNORECASE
    ), r'\1***\2'),
    # password=...
    (re.compile(
        r'((?:password|passwd|pwd|token|api_key)\s*=\s*)[^\s"\']+',
        re.IGNORECASE
    ), r'\1***'),
]

EXTS = {".json", ".yml", ".yaml", ".log", ".txt", ".ini", ".cfg"}


def scrub_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False
    except Exception as e:
        print(f"  skip {path.name}: {e}")
        return False

    original = text
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"  ✔ scrubbed: {path}")
        return True
    return False


def main():
    root = Path(__file__).resolve().parent.parent
    print(f"Root: {root}\n")

    targets = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS:
            if ".git" in p.parts:
                continue
            targets.append(p)

    print(f"Scanning {len(targets)} files…\n")
    changed = 0
    for t in targets:
        if scrub_file(t):
            changed += 1

    print(f"\nDone. {changed} file(s) modified.")
    if changed:
        print("\n⚠ این فایل‌ها تغییر کردند. حالا commit کن.")
    return 0


if __name__ == "__main__":
    sys.exit(main())