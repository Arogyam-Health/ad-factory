#!/usr/bin/env python3
"""Execute one Reference Flow browser job in an isolated process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    result_path = Path(args.result).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))

    from dashboard.backend import reference_flow as flow
    from dashboard.backend.chatgpt_runtime_patch import install_chatgpt_watchdog

    install_chatgpt_watchdog()
    result = flow._run_image_engine(
        engine=str(config["engine"]),
        batch=str(config["batch"]),
        prompt_path=Path(config["prompt_path"]),
        source_file=Path(config["source_file"]),
        aspect_ratio=str(config.get("aspect_ratio") or "4:5"),
        headless=bool(config.get("headless", False)),
        run_dir=Path(config["run_dir"]),
    )
    _write_result(
        result_path,
        {
            "returncode": int(result.returncode),
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        },
    )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
