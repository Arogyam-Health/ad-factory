#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# FIX: Force UTF-8 on stdout to prevent Windows cp1252 encoding crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")


def extract_section(content: str, header: str) -> str:
    pattern = re.compile(rf"^###\s+{re.escape(header)}\s*$", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        raise RuntimeError(f"Section '{header}' not found")
    start = match.end()
    next_header = re.search(r"^##+\s+", content[start:], flags=re.MULTILINE)
    end = start + next_header.start() if next_header else len(content)
    return content[start:end].strip()


def bullets(section: str) -> list[str]:
    out: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- "):
            out.append(line[2:].strip())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract format rule block from playbook")
    parser.add_argument("--playbook", default="AD_CREATIVE_SYSTEM_PLAYBOOK.md")
    parser.add_argument("--format", required=True, choices=["HERO", "BA", "TEST", "FEAT", "UGC"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    content = Path(args.playbook).read_text(encoding="utf-8")
    section = extract_section(content, args.format)
    payload = {
        "format": args.format,
        "rules": bullets(section),
        "raw": section,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
