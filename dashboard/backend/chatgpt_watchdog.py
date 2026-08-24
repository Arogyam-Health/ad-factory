from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from dashboard.backend.pipeline.browser_env import dashboard_subprocess_env
from dashboard.backend.pipeline.paths import GENERATED_IMAGES_ROOT, INPUT_IMAGES_DIR, ROOT, RUNTIME_ROOT, STARTING_PROMPT_PATH


def run_chatgpt_generation_watchdog(
    *,
    batch: str,
    prompt_files: list[str],
    aspect_ratio: str,
    image_sources_file: str | None = None,
    headless: bool = False,
    run_dir: Path | None = None,
    prepend_starting_prompt: bool = True,
    first_tab_mode: str = "reuse-blank",
) -> subprocess.CompletedProcess[str]:
    """Run ChatGPT generation with terminal-error detection and a hard process limit."""
    del run_dir

    aspect_folder = "9_16" if aspect_ratio == "9:16" else "4_5"
    prompt_work_dir = (
        RUNTIME_ROOT
        / "chatgpt_selected_prompts"
        / f"{batch}_{aspect_folder}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    )
    prompt_work_dir.mkdir(parents=True, exist_ok=True)

    starting_prompt = ""
    if prepend_starting_prompt:
        if STARTING_PROMPT_PATH.exists():
            starting_prompt = STARTING_PROMPT_PATH.read_text(encoding="utf-8").strip()

    for prompt_file in prompt_files:
        source = Path(prompt_file)
        if not source.is_absolute():
            source = ROOT / prompt_file
        source = source.resolve()
        if not source.exists():
            raise RuntimeError(f"Prompt file not found: {source}")
        prompt_text = source.read_text(encoding="utf-8")
        combined = f"{starting_prompt}\n\n{prompt_text.strip()}\n" if starting_prompt else prompt_text
        (prompt_work_dir / source.name).write_text(combined, encoding="utf-8")
        sidecar = source.with_suffix(".json")
        if sidecar.exists():
            (prompt_work_dir / sidecar.name).write_text(
                sidecar.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    out_dir = GENERATED_IMAGES_ROOT / batch / aspect_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    generation_timeout = int(os.getenv("CHATGPT_GENERATION_TIMEOUT_SECONDS") or "420")
    download_timeout = int(os.getenv("CHATGPT_DOWNLOAD_TIMEOUT_SECONDS") or "90")
    login_timeout = int(os.getenv("CHATGPT_MANUAL_LOGIN_TIMEOUT_SECONDS") or "180")
    hard_timeout = int(
        os.getenv("CHATGPT_PROCESS_TIMEOUT_SECONDS")
        or str(generation_timeout + download_timeout + login_timeout + 180)
    )

    cmd = [
        sys.executable,
        "local_agent_runtime/chatgpt_web_watchdog.py",
        "--prompt-dir",
        str(prompt_work_dir),
        "--prompt-glob",
        "*.txt",
        "--out-dir",
        str(out_dir),
        "--timeout",
        str(generation_timeout),
        "--download-timeout",
        str(download_timeout),
        "--manual-login-timeout",
        str(login_timeout),
        "--upload-dir",
        str(INPUT_IMAGES_DIR),
    ]
    if headless:
        cmd.append("--headless")
    if first_tab_mode and first_tab_mode != "reuse-blank":
        cmd.extend(["--first-tab-mode", first_tab_mode])
    if image_sources_file:
        cmd.extend(["--image-source-file", image_sources_file])
    if not prepend_starting_prompt:
        cmd.extend(["--starting-prompt-file", ""])
    cmd.extend(["--aspect-ratio", aspect_ratio])

    cdp_url = os.getenv("CHATGPT_CDP_URL", "http://127.0.0.1:9222").strip()
    if cdp_url:
        cmd.extend(["--cdp-url", cdp_url])
    cdp_url_for_log = cdp_url or "auto-launch"

    log_dir = RUNTIME_ROOT / "generation_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gen_{batch}_{aspect_folder}_chatgpt.log"
    env = dashboard_subprocess_env()

    return_code = 0
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"[DEBUG] CDP URL: {cdp_url_for_log}\n")
        log_file.write(f"[DEBUG] Command: {' '.join(cmd)}\n")
        log_file.write(f"[DEBUG] Hard process timeout: {hard_timeout}s\n")
        log_file.flush()
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(ROOT),
                text=True,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
                timeout=hard_timeout,
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            return_code = 124
            log_file.write(
                "\n[WATCHDOG ERROR] ChatGPT automation exceeded the hard process timeout "
                f"of {hard_timeout}s and was terminated.\n"
            )
            log_file.flush()

    full_output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=return_code,
        stdout=full_output,
        stderr="",
    )
