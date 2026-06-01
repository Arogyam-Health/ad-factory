#!/usr/bin/env python3
"""Strict ChatGPT web image-generation automation for Playwright.

This is a ChatGPT adaptation of a Gemini-style prompt/image automation script.

Workflow:
  1. Use one real tab per prompt. The first prompt can reuse the initial blank tab.
  2. Navigate to a fresh ChatGPT chat. Never click old conversation rows or their menus.
  3. Optionally select the Instant model from the model picker.
  4. Optionally select "Create image" from the plus/tools menu using guarded selectors.
  5. Upload reference images if provided, wait for attachment chips/thumbnails to settle.
  6. Insert the full prompt, verify prompt integrity, and submit only after verification.
  7. Wait for a large generated image in the latest assistant response.
  8. Save the actual generated image resource where possible, not just a screenshot.
  9. Keep previous prompt tabs open, then move to the next prompt tab.

Important:
  - ChatGPT's web UI changes often. This script uses defensive selectors, but you may
    still need to adjust labels if the UI changes.
  - For high-volume production image generation, the official API is more reliable
    than browser automation.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image as PILImage

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)

CHATGPT_URL = "https://chatgpt.com/"
FORMAT_ORDER = ["BA", "FEAT", "HERO", "TEST", "UGC"]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
DOWNLOAD_TEMP_EXTS = {".crdownload", ".tmp", ".part"}

UNSAFE_CLICK_WORDS = (
    "share conversation",
    "share",
    "rename",
    "delete",
    "remove",
    "archive",
    "more options",
    "recent",
    "activity",
    "settings",
    "help",
    "temporary chat",
    "new chat",
    "history",
    "sidebar",
    "profile",
    "account",
)


@dataclass(frozen=True)
class PromptJob:
    prompt_path: Path
    format_id: str
    persona_id: str
    lang_id: str
    variant_id: str
    job_key: str
    output_stem: str
    concept_angle: str = ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ChatGPT web image generation automation")
    parser.add_argument("--prompt-dir", required=True, help="Directory containing prompt files")
    parser.add_argument(
        "--prompt-glob",
        default="*_P*_EN.txt",
        help="Prompt glob. Examples: '*_P*_EN.txt' for all English, "
             "'BA_P*_EN_pain_point.txt' for a single angle, "
             "'*_P*_*.txt' for every language.",
    )
    parser.add_argument(
        "--starting-prompt-file",
        default="input/startingprompt.txt",
        help="Starter prompt prepended to each prompt. Use an empty value to disable.",
    )
    parser.add_argument("--image-source-file", default="", help="Optional text file of image paths/URLs to upload")
    parser.add_argument(
        "--upload-dir",
        default="",
        help="Optional directory of reference images to upload. If empty, no directory upload is used.",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--timeout", type=int, default=420, help="Generation timeout per prompt")
    parser.add_argument("--download-timeout", type=int, default=90)
    parser.add_argument("--sleep-after-download", type=float, default=3.0)
    parser.add_argument("--min-image-bytes", type=int, default=20_000)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")

    parser.add_argument(
        "--expected-formats",
        default="",
        help="Comma-separated expected format IDs, e.g. BA,FEAT,HERO,TEST,UGC",
    )
    parser.add_argument("--strict-expected-formats", action="store_true")
    parser.add_argument("--allow-duplicate-prompt-keys", action="store_true")

    parser.add_argument("--user-data-dir", default="", help="Persistent Chrome profile directory")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--manual-login-timeout", type=int, default=180)
    parser.add_argument("--login-wait-mode", choices=["auto", "strict"], default="auto")
    parser.add_argument("--browser-download-dir", default="")
    parser.add_argument(
        "--first-tab-mode",
        choices=["reuse-blank", "new"],
        default="reuse-blank",
        help="reuse-blank avoids an extra empty ChatGPT tab at startup",
    )

    parser.add_argument("--skip-model-selection", action="store_true")
    parser.add_argument(
        "--require-instant-model",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail if Instant model cannot be confirmed",
    )
    parser.add_argument(
        "--skip-create-image-tool",
        action="store_true",
        help="Do not try to select Create image before sending.",
    )
    parser.add_argument(
        "--require-create-image-tool",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail if Create image tool cannot be confirmed",
    )

    parser.add_argument(
        "--prompt-paste-method",
        choices=["auto", "keyboard", "clipboard", "js"],
        default="auto",
    )
    parser.add_argument("--prompt-paste-timeout", type=int, default=35)
    parser.add_argument("--prompt-integrity-ratio", type=float, default=0.98)
    parser.add_argument("--prompt-settle-wait", type=float, default=4.0)
    parser.add_argument(
        "--send-submit-method",
        choices=["enter", "click", "auto"],
        default="enter",
    )
    parser.add_argument("--send-confirm-timeout", type=float, default=35.0)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--aspect-ratio",
        choices=["4:5", "9:16"],
        default="4:5",
        help="Target aspect ratio for output images (determines PIL crop target)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Prompt discovery
# ---------------------------------------------------------------------------


def _format_sort_key(fmt: str) -> tuple[int, str]:
    fmt_up = fmt.upper()
    if fmt_up in FORMAT_ORDER:
        return (FORMAT_ORDER.index(fmt_up), fmt_up)
    return (999, fmt_up)


def _parse_prompt_name(path: Path) -> tuple[str, str, str, str, str]:
    """Parse a prompt file name. Returns ``(fmt, persona, lang, variant, concept_angle)``.

    Canonical form:  ``<FMT>_P<NN>_<LANG>_<angle>[_A<NN>].txt``
    Legacy forms also accepted (with ``OUTPUT_``/``FINAL_`` prefix and/or missing
    angle component) for backward compatibility with older files.
    """
    stem = path.stem
    patterns = [
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)_(?P<angle>[a-z][a-z_]*?)(?:_(?P<variant>[AV]\d+))?$",
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)(?:_(?P<variant>[AV]\d+))?(?:_(?P<angle2>[a-z_]+))?$",
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)$",
    ]
    for pat in patterns:
        m = re.search(pat, stem, flags=re.IGNORECASE)
        if m:
            fmt = m.group("fmt").upper()
            persona = f"P{int(m.group('num')):02d}"
            lang = m.group("lang").upper() if "lang" in m.groupdict() and m.group("lang") else "XX"
            variant = m.group("variant").upper() if "variant" in m.groupdict() and m.group("variant") else ""
            angle = m.group("angle") if "angle" in m.groupdict() and m.group("angle") else (m.group("angle2") if "angle2" in m.groupdict() and m.group("angle2") else "")
            return fmt, persona, lang, variant, angle

    m_persona = re.search(r"(?:^|_)P(?P<num>\d+)(?:_|$)", stem, flags=re.IGNORECASE)
    persona_id = f"P{int(m_persona.group('num')):02d}" if m_persona else "P00"
    tokens = [t.upper() for t in re.split(r"[^A-Za-z0-9]+", stem) if t]
    for token in tokens:
        if token in FORMAT_ORDER:
            return token, persona_id, "XX", "", ""
    fmt = next((t for t in tokens if t not in {persona_id}), "PROMPT")
    return fmt, persona_id, "XX", "", ""


def discover_prompt_jobs(prompt_dir: Path, pattern: str, allow_duplicates: bool) -> tuple[list[PromptJob], list[PromptJob]]:
    raw_paths = [p for p in prompt_dir.glob(pattern) if p.is_file()]
    if not raw_paths:
        raise FileNotFoundError(f"No prompt files found in {prompt_dir} with pattern {pattern!r}")

    raw_jobs: list[PromptJob] = []
    for path in raw_paths:
        fmt, persona, lang, variant, concept_angle = _parse_prompt_name(path)
        variant_suffix = f"_{variant}" if variant else ""
        key = f"{fmt}_{persona}_{lang}{variant_suffix}"
        if concept_angle and concept_angle not in key:
            key += f"_{concept_angle}"

        # Reuse the prompt's stem verbatim so the generated image matches the
        # prompt file 1:1 (only the extension changes). E.g. BA_P01_EN_pain_point.
        if concept_angle:
            safe_stem = f"{fmt}_P{int(persona[1:]):02d}_{lang}_{concept_angle}{variant_suffix}"
        else:
            safe_stem = f"chatgpt-{fmt.lower()}-{persona.lower()}-{lang.lower()}{('-'+variant.lower()) if variant else ''}"

        # If the angle wasn't in the filename, fall back to reading it from
        # the prompt body (legacy behavior, kept for safety).
        if not concept_angle:
            try:
                body = path.read_text(encoding="utf-8")
                m = re.search(r"^- concept_angle\s*:\s*(.+)$", body, re.MULTILINE | re.IGNORECASE)
                if m:
                    concept_angle = m.group(1).strip()
                    if concept_angle:
                        safe_stem += f"-{concept_angle}"
                        if concept_angle not in key:
                            key += f"_{concept_angle}"
            except Exception:
                pass

        raw_jobs.append(
            PromptJob(
                prompt_path=path.resolve(),
                format_id=fmt,
                persona_id=persona,
                lang_id=lang,
                variant_id=variant,
                job_key=key,
                output_stem=safe_stem,
                concept_angle=concept_angle,
            )
        )

    raw_jobs.sort(
        key=lambda j: (
            int(re.search(r"\d+", j.persona_id).group(0)) if re.search(r"\d+", j.persona_id) else 0,
            _format_sort_key(j.format_id),
            j.prompt_path.name,
        )
    )

    if allow_duplicates:
        return raw_jobs, []

    seen: set[str] = set()
    jobs: list[PromptJob] = []
    duplicates: list[PromptJob] = []
    for job in raw_jobs:
        if job.job_key in seen:
            duplicates.append(job)
            continue
        seen.add(job.job_key)
        jobs.append(job)
    return jobs, duplicates


def print_job_manifest(jobs: list[PromptJob], duplicates: list[PromptJob]) -> None:
    print("\nResolved prompt jobs:")
    for idx, job in enumerate(jobs, start=1):
        print(f"  {idx:03d}. {job.job_key:<12} -> {job.prompt_path.name}")
    if duplicates:
        print("\nSkipped duplicate FORMAT/Pxx prompt files:")
        for job in duplicates:
            print(f"  duplicate {job.job_key:<12} -> {job.prompt_path.name}")
        print("Use --allow-duplicate-prompt-keys only if these duplicates are intentional.")


def validate_expected_formats(jobs: list[PromptJob], expected_csv: str, strict: bool) -> None:
    expected = [x.strip().upper() for x in expected_csv.split(",") if x.strip()]
    if not expected:
        return
    by_persona: dict[str, set[str]] = {}
    for job in jobs:
        by_persona.setdefault(job.persona_id, set()).add(job.format_id.upper())
    problems: list[str] = []
    for persona, present in sorted(by_persona.items()):
        missing = [fmt for fmt in expected if fmt not in present]
        if missing:
            problems.append(f"{persona}: missing {', '.join(missing)}")
    if problems:
        msg = "Expected-format check failed/warned:\n  " + "\n  ".join(problems)
        if strict:
            raise RuntimeError(msg)
        print("\nWARNING: " + msg)


def load_starting_prompt(path_value: str) -> str:
    if not path_value.strip():
        return ""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        print(f"\nWARNING: starting prompt file not found, continuing without it: {path}")
        return ""
    return path.read_text(encoding="utf-8").strip()


def prepend_starting_prompt(starting_prompt: str, prompt_text: str) -> str:
    body = prompt_text.strip()
    if not starting_prompt:
        return prompt_text
    if body.startswith(starting_prompt):
        return prompt_text
    return f"{starting_prompt}\n\n{body}\n"


def load_prompt_metadata(prompt_path: Path, prompt_text: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    sidecar = prompt_path.with_suffix(".json")
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Warning: could not read prompt metadata {sidecar.name}: {exc}")
    for label in (
        "Headline angle", "Awareness stage", "Concept angle", "Concept structure",
        "Proof style", "CTA voice", "Hook structure", "Concept angle", "Message structure",
    ):
        match = re.search(rf"^-\s*{re.escape(label)}\s*:\s*(.+)$", prompt_text, flags=re.MULTILINE | re.IGNORECASE)
        if match:
            meta[label.lower().replace(" ", "_")] = match.group(1).strip()
    for key in ("persona", "persona_number", "persona_name", "format", "language", "aspect_ratio",
                "visual_pattern", "visual_archetype", "background", "background_group_key",
                "background_decisions", "creative_index", "creative_total", "multiplier", "hypothesis"):
        if key not in meta:
            match = re.search(rf"^-\s*{re.escape(key)}\s*:\s*(.+)$", prompt_text, flags=re.MULTILINE | re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                try:
                    meta[key] = json.loads(val)
                except Exception:
                    meta[key] = val
    return meta


def build_test_variables(job: PromptJob, prompt_metadata: dict[str, Any], effective_hypothesis: dict[str, Any]) -> dict[str, Any]:
    visual_pattern = prompt_metadata.get("visual_pattern") if isinstance(prompt_metadata.get("visual_pattern"), dict) else {}
    visual_archetype = prompt_metadata.get("visual_archetype") if isinstance(prompt_metadata.get("visual_archetype"), dict) else {}
    hyp_type = ""
    hyp_variant = ""
    if isinstance(effective_hypothesis, dict):
        hyp_type = effective_hypothesis.get("type", "")
        hyp_variant = effective_hypothesis.get("variant", "")
    return {
        "format": job.format_id,
        "persona": job.persona_id,
        "language": job.lang_id,
        "variant": job.variant_id,
        "visual_pattern": visual_pattern,
        "visual_archetype": visual_archetype,
        "hypothesis": effective_hypothesis,
        "hypothesis_type": hyp_type,
        "hypothesis_variant": hyp_variant,
        "persona": prompt_metadata.get("persona", job.persona_id),
        "persona_number": prompt_metadata.get("persona_number", ""),
        "persona_name": prompt_metadata.get("persona_name", ""),
        "format": prompt_metadata.get("format", job.format_id),
        "language": prompt_metadata.get("language", job.lang_id),
        "aspect_ratio": prompt_metadata.get("aspect_ratio", ""),
        "headline_angle": prompt_metadata.get("headline_angle", ""),
        "awareness_stage": prompt_metadata.get("awareness_stage", ""),
        "concept_angle": prompt_metadata.get("concept_angle", ""),
        "background": prompt_metadata.get("background", {}),
        "background_group_key": prompt_metadata.get("background_group_key", ""),
        "background_decisions": prompt_metadata.get("background_decisions", {}),
        "creative_index": prompt_metadata.get("creative_index", 1),
        "creative_total": prompt_metadata.get("creative_total", 1),
        "multiplier": prompt_metadata.get("multiplier", prompt_metadata.get("creative_total", 1)),
    }


def build_image_metadata(
    status: str,
    job: PromptJob,
    prompt_metadata: dict[str, Any],
    effective_hypothesis: dict[str, Any],
    test_variables: dict[str, Any],
    saved_path: Path | None = None,
    error: str = "",
    diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "type": "chatgpt_ad_image",
        "status": status,
        "format": job.format_id,
        "persona": job.persona_id,
        "language": job.lang_id,
        "job_key": job.job_key,
        "prompt_file": job.prompt_path.name,
        "hypothesis_type": test_variables.get("hypothesis_type", ""),
        "hypothesis_variant": test_variables.get("hypothesis_variant", ""),
        "creative_index": test_variables.get("creative_index", 1),
        "creative_total": test_variables.get("creative_total", 1),
        "test_variables": test_variables,
        "timestamp": int(time.time()),
    }
    if saved_path is not None:
        metadata["saved_file"] = str(saved_path)
    if error:
        metadata["error"] = error
    if diag:
        metadata.update(diag)
    return metadata


# ---------------------------------------------------------------------------
# Optional image upload sources
# ---------------------------------------------------------------------------


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def download_to_temp(source: str, temp_dir: Path) -> Path:
    parsed = urllib.parse.urlparse(source)
    suffix = Path(parsed.path).suffix or ".png"
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(parsed.path).stem) or "image"
    out_path = temp_dir / f"{filename}{suffix}"
    req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out_path.write_bytes(resp.read())
    return out_path


def parse_image_source_file(path: Path) -> list[str]:
    sources: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _, value = line.split("=", 1)
            line = value.strip()
        if line:
            sources.append(line)
    return sources


def build_local_image_paths(sources: list[str], temp_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for src in sources:
        if is_url(src):
            paths.append(download_to_temp(src, temp_dir))
        else:
            p = Path(src).expanduser()
            if not p.is_absolute():
                p = Path.cwd() / p
            if not p.exists():
                raise FileNotFoundError(f"Image path not found: {p}")
            paths.append(p.resolve())
    return paths


def collect_upload_images(upload_dir_value: str, image_source_file_value: str, temp_dir: Path) -> list[Path]:
    images: list[Path] = []

    if image_source_file_value.strip():
        source_file = Path(image_source_file_value).expanduser()
        if not source_file.is_absolute():
            source_file = Path.cwd() / source_file
        sources = parse_image_source_file(source_file)
        images.extend(build_local_image_paths(sources, temp_dir))
        for p in images:
            if p.suffix.lower() not in IMAGE_EXTS:
                raise ValueError(f"Unsupported upload image extension: {p}")
        return images

    if upload_dir_value.strip():
        upload_dir = Path(upload_dir_value).expanduser()
        if not upload_dir.is_absolute():
            upload_dir = Path.cwd() / upload_dir
        if not upload_dir.exists():
            raise FileNotFoundError(f"Upload directory not found: {upload_dir}")
        if not upload_dir.is_dir():
            raise NotADirectoryError(f"Upload path is not a directory: {upload_dir}")
        images.extend(
            p.resolve()
            for p in sorted(upload_dir.iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )

    for p in images:
        if p.suffix.lower() not in IMAGE_EXTS:
            raise ValueError(f"Unsupported upload image extension: {p}")
    return images


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------


def resolve_browser_binary() -> str:
    for candidate in [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]:
        if Path(candidate).exists():
            return candidate
    return ""


def _debug_port_available(port: int = 9222) -> bool:
    try:
        sock = socket.socket()
        sock.settimeout(1)
        sock.connect(("127.0.0.1", port))
        sock.close()
        return True
    except Exception:
        return False


def grant_chatgpt_permissions(context: BrowserContext) -> None:
    try:
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://chatgpt.com")
    except Exception as exc:
        print(f"  [browser] Could not grant clipboard permissions; will use fallbacks: {exc}")


def build_browser_context(args: argparse.Namespace, download_dir: Path):
    p = sync_playwright().start()
    download_dir.mkdir(parents=True, exist_ok=True)

    if _debug_port_available(9222):
        print("  [connect] Connecting to existing Chrome via CDP on port 9222...")
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
        context.set_default_timeout(30000)
        grant_chatgpt_permissions(context)
        if not context.pages:
            context.new_page().goto("about:blank")
        return p, context

    print("  [launch] No Chrome debug port found, launching a persistent browser profile...")
    selected_binary = resolve_browser_binary()
    profile_dir = Path(args.user_data_dir).expanduser() if args.user_data_dir else download_dir / ".pw_chatgpt_profile"

    launch_opts: dict[str, Any] = {
        "headless": args.headless,
        "downloads_path": str(download_dir),
        "accept_downloads": True,
        "bypass_csp": True,
        "args": [
            "--disable-notifications",
            "--disable-popup-blocking",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--safebrowsing-enabled",
            "--start-maximized",
        ],
    }
    if selected_binary:
        launch_opts["executable_path"] = selected_binary

    context = p.chromium.launch_persistent_context(str(profile_dir), **launch_opts)
    context.set_default_timeout(30000)
    grant_chatgpt_permissions(context)
    if not context.pages:
        context.new_page().goto("about:blank")
    return p, context


def _configure_download_dir(context: BrowserContext, download_dir: Path) -> None:
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        pages = list(getattr(context, "pages", []) or [])
        if not pages:
            return
        session = context.new_cdp_session(pages[0])
        try:
            session.send(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir), "eventsEnabled": True},
            )
            return
        except Exception:
            pass
        try:
            session.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(download_dir)})
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ChatGPT app readiness and fresh chat
# ---------------------------------------------------------------------------


def is_blankish_url(url: str) -> bool:
    lower = (url or "").lower()
    return lower in ("", "about:blank") or lower.startswith("chrome://newtab") or lower.startswith("data:")


def goto_chatgpt(page: Page, timeout_ms: int = 60000) -> None:
    try:
        page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    except PWTimeoutError:
        current = ""
        try:
            current = page.url or ""
        except Exception:
            pass
        if "chatgpt.com" in current or chatgpt_app_ready(page):
            print("  [fresh] ChatGPT navigation timed out waiting for DOM load, but the app is present; continuing.")
            return
        raise


def chatgpt_app_ready(page: Page) -> bool:
    try:
        current = (page.url or "").lower()
    except Exception:
        return False
    if "chatgpt.com" not in current:
        return False
    selectors = [
        "#prompt-textarea",
        "textarea[data-testid='prompt-textarea']",
        "[data-testid='composer'] textarea",
        "[data-testid='composer'] [contenteditable='true']",
        "div.ProseMirror[contenteditable='true']",
        "main div[contenteditable='true']",
        "textarea",
        "button[aria-label*='New chat']",
    ]
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible():
                return True
        except Exception:
            continue
    return False


def wait_for_manual_login(page: Page, timeout: int, strict: bool) -> None:
    print("Waiting for ChatGPT login/readiness...")
    deadline = time.time() + timeout
    next_log = time.time() + 5
    while time.time() < deadline:
        if chatgpt_app_ready(page):
            print(f"ChatGPT UI looks ready at {page.url}. Continuing.")
            return
        if time.time() >= next_log:
            try:
                current = page.url
            except Exception:
                current = "<unavailable>"
            print(f"Still waiting for ChatGPT readiness... current URL: {current}")
            next_log = time.time() + 5
        time.sleep(1.0)
    msg = f"Timed out after {timeout}s waiting for ChatGPT readiness"
    if strict:
        raise PWTimeoutError(msg)
    print(f"{msg}; continuing in auto mode.")


def dismiss_open_overlays(page: Page) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    for selector in [
        "[data-radix-popper-content-wrapper] button[aria-label='Close']",
        "[role='dialog'] button[aria-label='Close']",
        "button[aria-label='Close']",
        ".modal-backdrop",
    ]:
        try:
            for el in page.locator(selector).all():
                if el.is_visible():
                    el.click(timeout=1000)
        except Exception:
            pass


def _path_is_conversation(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    return parsed.path.rstrip("/").startswith("/c/")


def count_chat_bubbles(page: Page) -> int:
    script = r"""
        () => {
            const selectors = [
                '[data-message-author-role]',
                '[data-testid*="conversation-turn"]',
                'article',
                '[class*="group/conversation-turn"]'
            ];
            const seen = new Set();
            let count = 0;
            for (const selector of selectors) {
                for (const el of document.querySelectorAll(selector)) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) count++;
                }
            }
            return count;
        }
    """.strip()
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


def click_new_chat_safely(page: Page) -> bool:
    script = r"""
        () => {
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            function textOf(el) {
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.innerText || '')).replace(/\s+/g, ' ').trim().toLowerCase();
            }
            const nodes = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            const candidates = [];
            for (const el of nodes) {
                if (!visible(el)) continue;
                const t = textOf(el);
                const href = el.getAttribute('href') || '';
                if (href.match(/^\/c\//)) continue;
                if (t === 'new chat' || t.includes('new chat') || href === '/' || href === '') {
                    const r = el.getBoundingClientRect();
                    // Prefer top/sidebar controls, but never old conversation rows.
                    if (el.closest('[data-testid*="history"], [aria-label*="chat history" i]') && href.match(/^\/c\//)) continue;
                    candidates.push({el, top: r.top, left: r.left, score: (t.includes('new chat') ? 100 : 0) + (r.top < 160 ? 20 : 0)});
                }
            }
            if (!candidates.length) return false;
            candidates.sort((a, b) => (b.score - a.score) || (a.top - b.top) || (a.left - b.left));
            candidates[0].el.click();
            return true;
        }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def open_prompt_tab(context: BrowserContext, page: Page, job_index: int, first_tab_mode: str) -> Page:
    use_current = False
    if job_index == 1 and first_tab_mode == "reuse-blank":
        try:
            use_current = is_blankish_url(page.url or "")
        except Exception:
            use_current = False

    if use_current:
        print("  [tab] Reusing initial blank tab for the first prompt.")
        return page

    print("  [tab] Opening a new tab for this prompt.")
    new_page = context.new_page()
    new_page.bring_to_front()
    return new_page


def navigate_to_fresh_chat(page: Page, manual_login_timeout: int, strict_login: bool) -> None:
    print("  [fresh] Navigating to ChatGPT...")

    page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
    time.sleep(0.4)
    goto_chatgpt(page)

    wait_for_manual_login(page, timeout=manual_login_timeout, strict=strict_login)
    dismiss_open_overlays(page)

    deadline = time.time() + 70
    while time.time() < deadline:
        current_url = page.url or ""
        if _path_is_conversation(current_url) or count_chat_bubbles(page) > 0:
            print("  [fresh] Existing conversation detected; opening New chat.")
            clicked = click_new_chat_safely(page)
            if not clicked:
                page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)
            dismiss_open_overlays(page)
            continue

        try:
            find_composer(page, timeout=4)
            time.sleep(1.5)
            if not _path_is_conversation(page.url or "") and count_chat_bubbles(page) == 0:
                print("  [fresh] SUCCESS: stable fresh ChatGPT chat verified.")
                return
        except Exception:
            pass

        time.sleep(1.0)

    raise RuntimeError("Failed to establish a fresh ChatGPT chat.")


# ---------------------------------------------------------------------------
# Safe clicks, composer, prompt integrity
# ---------------------------------------------------------------------------


def _safe_click_js(page: Page, labels: Iterable[str], exact: bool = False, timeout: float = 8.0) -> bool:
    labels_list = [str(x).lower().strip() for x in labels if str(x).strip()]
    bad_words_list = [x.lower().strip() for x in UNSAFE_CLICK_WORDS]
    deadline = time.time() + timeout
    while time.time() < deadline:
        js = """
        ({labels, exact, badWords}) => {
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            function inForbiddenArea(el) {
                const row = el.closest('nav [href^="/c/"], [data-testid*="history"], [aria-label*="chat history" i], [class*="sidebar"] [href^="/c/"]');
                return !!row;
            }
            function textOf(el) {
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.getAttribute('data-testid') || '') + ' ' +
                        (el.innerText || '')).replace(/\\s+/g, ' ').trim().toLowerCase();
            }
            const roots = Array.from(document.querySelectorAll('main, header, [role="dialog"], [data-radix-popper-content-wrapper], body'));
            const nodes = [];
            for (const root of roots) {
                for (const el of root.querySelectorAll('button,a,[role="button"],[role="menuitem"],[role="option"]')) {
                    if (!nodes.includes(el)) nodes.push(el);
                }
            }
            const candidates = [];
            for (const el of nodes) {
                if (!visible(el) || inForbiddenArea(el)) continue;
                const txt = textOf(el);
                if (!txt) continue;
                if (badWords.some(w => txt.includes(w)) && !labels.some(l => txt.includes(l))) continue;
                for (const label of labels) {
                    if ((exact && txt === label) || (!exact && txt.includes(label))) {
                        const r = el.getBoundingClientRect();
                        candidates.push({el, top: r.top, left: r.left, score: (r.top < 220 ? 20 : 0)});
                        break;
                    }
                }
            }
            if (!candidates.length) return false;
            candidates.sort((a, b) => (b.score - a.score) || (b.top - a.top) || (a.left - b.left));
            candidates[0].el.scrollIntoView({block:'center', inline:'center'});
            candidates[0].el.click();
            return true;
        }
        """.strip()
        try:
            clicked = page.evaluate(js, {"labels": labels_list, "exact": exact, "badWords": bad_words_list})
            if clicked:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def safe_click_labels(page: Page, labels: Iterable[str], timeout: float = 8.0, exact: bool = False) -> bool:
    try:
        return _safe_click_js(page, labels, exact=exact, timeout=timeout)
    except Exception:
        return False


def find_composer(page: Page, timeout: int = 30) -> Locator:
    selectors = [
        "#prompt-textarea",
        "textarea[data-testid='prompt-textarea']",
        "[data-testid='composer'] textarea",
        "[data-testid='composer'] [contenteditable='true']",
        "div.ProseMirror[contenteditable='true']",
        "main form textarea",
        "main form [contenteditable='true']",
        "main div[contenteditable='true']",
        "textarea",
    ]
    last_error: Exception | None = None
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="visible", timeout=timeout * 1000)
            return loc
        except Exception as exc:
            last_error = exc
    raise PWTimeoutError(f"Could not find ChatGPT composer. Last error: {last_error}")


def get_composer_text(page: Page, composer: Locator) -> str:
    script = """
        (root) => {
            function readText(el) {
                if (!el) return '';
                const tag = (el.tagName || '').toLowerCase();
                if (tag === 'textarea' || tag === 'input') return el.value || '';
                if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') {
                    return el.innerText || el.textContent || '';
                }
                if (el.querySelector) {
                    const child = el.querySelector('textarea, input, [contenteditable="true"], [role="textbox"]');
                    if (child) return readText(child);
                }
                return el.innerText || el.textContent || '';
            }
            return readText(root);
        }
    """.strip()
    try:
        return composer.evaluate(script) or ""
    except Exception:
        return ""


def _normalize_prompt_compare(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _compact_prompt_compare(text: str) -> str:
    return re.sub(r"\s+", " ", _normalize_prompt_compare(text)).strip()


def _sample_chunks(text: str, chunk_size: int = 96) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    positions = [0, int(len(text) * 0.25), int(len(text) * 0.50), int(len(text) * 0.75), max(0, len(text) - chunk_size)]
    chunks: list[str] = []
    seen: set[str] = set()
    for pos in positions:
        pos = min(max(0, pos), max(0, len(text) - chunk_size))
        chunk = text[pos : pos + chunk_size].strip()
        if len(chunk) < 24:
            continue
        if chunk not in seen:
            seen.add(chunk)
            chunks.append(chunk)
    return chunks


def prompt_integrity_report(expected: str, actual: str, min_ratio: float = 0.98) -> dict[str, Any]:
    expected_compact = _compact_prompt_compare(expected)
    actual_compact = _compact_prompt_compare(actual)
    expected_len = len(expected_compact)
    actual_len = len(actual_compact)
    ratio = (actual_len / expected_len) if expected_len else 1.0
    prefix_len = min(160, expected_len)
    suffix_len = min(160, expected_len)
    prefix_ok = bool(expected_len == 0 or actual_compact.startswith(expected_compact[:prefix_len]))
    suffix_ok = bool(expected_len == 0 or actual_compact.endswith(expected_compact[-suffix_len:]))
    chunks = _sample_chunks(expected_compact)
    found_chunks = sum(1 for chunk in chunks if chunk in actual_compact)
    chunks_ok = found_chunks == len(chunks)
    ok = bool(ratio >= min_ratio and prefix_ok and suffix_ok and chunks_ok)

    return {
        "ok": ok,
        "expected_len": expected_len,
        "actual_len": actual_len,
        "ratio": ratio,
        "prefix_ok": prefix_ok,
        "suffix_ok": suffix_ok,
        "chunks_found": found_chunks,
        "chunks_total": len(chunks),
        "actual_preview_start": actual_compact[:180],
        "actual_preview_end": actual_compact[-180:] if actual_compact else "",
    }


def format_prompt_integrity(report: dict[str, Any]) -> str:
    return (
        f"expected={report.get('expected_len')} compact chars, "
        f"actual={report.get('actual_len')} compact chars, "
        f"ratio={report.get('ratio', 0.0):.1%}, "
        f"prefix={report.get('prefix_ok')}, suffix={report.get('suffix_ok')}, "
        f"chunks={report.get('chunks_found')}/{report.get('chunks_total')}"
    )


def write_prompt_debug_file(path: Path | None, expected: str, actual: str, report: dict[str, Any], method: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [
            "PROMPT PASTE / SEND INTEGRITY FAILURE",
            f"method: {method}",
            format_prompt_integrity(report),
            "",
            "--- ACTUAL COMPOSER TEXT START ---",
            actual or "",
            "--- ACTUAL COMPOSER TEXT END ---",
            "",
            "--- EXPECTED PROMPT START ---",
            expected or "",
            "--- EXPECTED PROMPT END ---",
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        print(f"  [prompt] Wrote composer debug file: {path}")
    except Exception as exc:
        print(f"  [prompt] Could not write debug file {path}: {exc}")


def focus_composer(page: Page, composer: Locator) -> None:
    try:
        composer.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    dismiss_open_overlays(page)
    try:
        composer.click(timeout=5000)
    except Exception:
        pass
    time.sleep(0.15)


def clear_composer_keyboard(page: Page, composer: Locator) -> None:
    focus_composer(page, composer)
    try:
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
    except Exception:
        pass
    time.sleep(0.25)


def paste_prompt_via_keyboard(page: Page, text: str) -> bool:
    try:
        composer = find_composer(page, timeout=10)
        clear_composer_keyboard(page, composer)
        composer = find_composer(page, timeout=5)
        focus_composer(page, composer)
        page.keyboard.insert_text(text)
        return True
    except Exception as exc:
        print(f"  [prompt] keyboard insert_text failed: {exc}")
        return False


def paste_prompt_via_clipboard(page: Page, text: str) -> bool:
    try:
        ok = page.evaluate(
            """async (text) => {
                if (!navigator.clipboard || !navigator.clipboard.writeText || !navigator.clipboard.readText) {
                    throw new Error('clipboard read/write unavailable');
                }
                await navigator.clipboard.writeText(text);
                const current = await navigator.clipboard.readText();
                return current === text;
            }""",
            text,
        )
        if not ok:
            raise RuntimeError("clipboard verification failed")
        composer = find_composer(page, timeout=10)
        clear_composer_keyboard(page, composer)
        composer = find_composer(page, timeout=5)
        focus_composer(page, composer)
        page.keyboard.press("Control+v")
        return True
    except Exception as exc:
        print(f"  [prompt] clipboard paste failed: {exc}")
        return False


def paste_prompt_via_js_last_resort(page: Page, text: str) -> bool:
    script = """
        (el, text) => {
            el.scrollIntoView({block:'center', inline:'center'});
            el.focus();
            const tag = (el.tagName || '').toLowerCase();
            if (tag === 'textarea' || tag === 'input') {
                el.value = '';
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.value = text;
                el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertFromPaste', data:text}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                return true;
            }
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const pasteEvent = new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:dt});
            el.dispatchEvent(pasteEvent);
            if ((el.innerText || '').trim().length < 10) {
                el.textContent = '';
                document.execCommand('insertText', false, text);
            }
            el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertFromPaste', data:text}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
        }
    """.strip()
    try:
        composer = find_composer(page, timeout=10)
        clear_composer_keyboard(page, composer)
        composer = find_composer(page, timeout=5)
        focus_composer(page, composer)
        return bool(composer.evaluate(script, text))
    except Exception as exc:
        print(f"  [prompt] JS last-resort insert failed: {exc}")
        return False


def _prompt_methods_for(method: str) -> list[str]:
    if method == "auto":
        return ["keyboard", "js"]
    return [method]


def wait_for_prompt_integrity(page: Page, expected: str, timeout: float, min_ratio: float) -> tuple[Locator, str, dict[str, Any]]:
    deadline = time.time() + timeout
    last_composer: Locator | None = None
    last_actual = ""
    last_report = prompt_integrity_report(expected, "", min_ratio)
    while time.time() < deadline:
        try:
            last_composer = find_composer(page, timeout=3)
            last_actual = get_composer_text(page, last_composer)
            last_report = prompt_integrity_report(expected, last_actual, min_ratio)
            if last_report["ok"]:
                return last_composer, last_actual, last_report
        except Exception:
            pass
        time.sleep(0.5)
    if last_composer is None:
        last_composer = find_composer(page, timeout=5)
    return last_composer, last_actual, last_report


def set_prompt_text(
    page: Page,
    text: str,
    method: str = "auto",
    verify_timeout: float = 35,
    min_integrity_ratio: float = 0.98,
    debug_path: Path | None = None,
) -> Locator:
    print(f"  [prompt] Expected prompt: {len(text)} chars, {text.count(chr(10)) + 1} lines")
    last_actual = ""
    last_report = prompt_integrity_report(text, "", min_integrity_ratio)
    last_method = method

    for selected_method in _prompt_methods_for(method):
        last_method = selected_method
        print(f"  [prompt] Inserting with method: {selected_method}")
        if selected_method == "keyboard":
            inserted = paste_prompt_via_keyboard(page, text)
        elif selected_method == "clipboard":
            inserted = paste_prompt_via_clipboard(page, text)
        elif selected_method == "js":
            inserted = paste_prompt_via_js_last_resort(page, text)
        else:
            inserted = False

        if not inserted:
            continue

        composer, actual, report = wait_for_prompt_integrity(
            page, expected=text, timeout=verify_timeout, min_ratio=min_integrity_ratio
        )
        last_actual = actual
        last_report = report
        print(f"  [prompt] Verify after {selected_method}: {format_prompt_integrity(report)}")
        if report["ok"]:
            print("  [prompt] Full prompt integrity confirmed before Send.")
            return composer

    write_prompt_debug_file(debug_path, text, last_actual, last_report, last_method)
    raise PWTimeoutError(
        "Prompt was not inserted completely; refusing to send partial prompt. "
        + format_prompt_integrity(last_report)
    )


# ---------------------------------------------------------------------------
# Model picker and Create image tool
# ---------------------------------------------------------------------------


def instant_model_selected(page: Page) -> bool:
    script = """
        () => {
            const roots = Array.from(document.querySelectorAll('header, main'));
            for (const root of roots) {
                for (const el of root.querySelectorAll('button,[role="button"],[data-testid*="model"]')) {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const t = ((el.getAttribute('aria-label') || '') + ' ' +
                               (el.getAttribute('title') || '') + ' ' +
                               (el.innerText || '')).toLowerCase();
                    if (t.includes('instant')) return true;
                }
            }
            return false;
        }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def select_instant_model(page: Page) -> bool:
    if instant_model_selected(page):
        return True

    # Click the model picker. Labels vary: Auto, Instant, Thinking, GPT-5.5, ChatGPT.
    opened = safe_click_labels(page, ["model", "auto", "thinking", "instant", "gpt", "chatgpt"], timeout=5)
    if not opened:
        return instant_model_selected(page)
    time.sleep(0.8)

    # Prefer the exact "Instant" option; allow versioned names such as "GPT-5.5 Instant".
    clicked = safe_click_labels(page, ["gpt-5.5 instant", "instant"], timeout=6)
    time.sleep(1.0)
    dismiss_open_overlays(page)
    return clicked or instant_model_selected(page)


def create_image_tool_selected(page: Page) -> bool:
    script = """
        () => {
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            const roots = Array.from(document.querySelectorAll('main form, [data-testid="composer"], main'));
            for (const root of roots) {
                if (!visible(root)) continue;
                const txt = (root.innerText || '').toLowerCase();
                if (txt.includes('create image') || txt.includes('image generation') || txt.includes('images')) return true;
            }
            return false;
        }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _click_plus_or_tools_near_composer(page: Page) -> bool:
    script = """
    () => {
        const composer = document.querySelector(
            '#prompt-textarea, textarea[data-testid="prompt-textarea"], [data-testid="composer"] textarea, [data-testid="composer"] [contenteditable="true"], div.ProseMirror[contenteditable="true"], main form [contenteditable="true"], main form textarea'
        );
        const cr = composer ? composer.getBoundingClientRect() : null;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function textOf(el) {
            return ((el.getAttribute('aria-label') || '') + ' ' +
                    (el.getAttribute('title') || '') + ' ' +
                    (el.getAttribute('data-testid') || '') + ' ' +
                    (el.innerText || '')).replace(/\\s+/g, ' ').trim().toLowerCase();
        }
        const bad = ['send', 'voice', 'dictate', 'settings', 'profile', 'new chat', 'share', 'history'];
        const good = ['add photos', 'add files', 'add', 'attach', 'upload', 'tools', 'plus', '+'];
        const nodes = Array.from(document.querySelectorAll('main button, main [role="button"], form button, form [role="button"]'));
        const candidates = [];
        for (const el of nodes) {
            if (!visible(el)) continue;
            const t = textOf(el);
            if (bad.some(w => t.includes(w))) continue;
            const r = el.getBoundingClientRect();
            let near = true;
            if (cr) {
                near = r.bottom >= cr.top - 140 && r.top <= cr.bottom + 160 && r.right >= cr.left - 80 && r.left <= cr.right + 80;
            } else {
                near = r.top > window.innerHeight * 0.45;
            }
            if (!near) continue;
            const looks = good.some(w => t.includes(w)) || t === '+' || (r.width <= 64 && r.height <= 64 && r.left < window.innerWidth * 0.45);
            if (!looks) continue;
            let score = 0;
            if (t.includes('add photos') || t.includes('add files')) score += 120;
            if (t.includes('attach') || t.includes('upload')) score += 90;
            if (t.includes('tools')) score += 70;
            if (t === '+' || t.includes('plus')) score += 60;
            if (cr) score += Math.max(0, 80 - Math.abs((r.top + r.bottom) / 2 - (cr.top + cr.bottom) / 2));
            candidates.push({el, score, left: r.left, top: r.top});
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (b.score - a.score) || (a.left - b.left) || (b.top - a.top));
        candidates[0].el.click();
        return true;
    }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception as exc:
        print(f"  [tool] Plus/tools button click failed: {exc}")
        return False


def select_create_image_tool(page: Page) -> bool:
    if create_image_tool_selected(page):
        return True

    opened = _click_plus_or_tools_near_composer(page)
    if not opened:
        opened = safe_click_labels(page, ["tools", "add photos", "add files", "attach"], timeout=4)
    if not opened:
        return create_image_tool_selected(page)

    time.sleep(0.8)
    clicked = safe_click_labels(page, ["create image", "image generation", "images", "generate image"], timeout=6)
    time.sleep(0.8)
    dismiss_open_overlays(page)
    return clicked or create_image_tool_selected(page)


def select_model_and_tool_if_requested(page: Page, args: argparse.Namespace) -> None:
    if args.skip_model_selection:
        print("  [model] Skipping model selection by request.")
    else:
        instant_ok = select_instant_model(page)
        print(f"  [model] Instant selected/confirmed: {instant_ok}")
        if args.require_instant_model and not instant_ok:
            raise PWTimeoutError("Could not confirm Instant model selection. Use --no-require-instant-model to continue anyway.")

    if args.skip_create_image_tool:
        print("  [tool] Skipping Create image tool selection by request.")
        return

    tool_ok = select_create_image_tool(page)
    print(f"  [tool] Create image selected/confirmed: {tool_ok}")
    if args.require_create_image_tool and not tool_ok:
        raise PWTimeoutError("Could not confirm Create image tool. Use --no-require-create-image-tool to continue anyway.")


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------


def get_all_image_srcs(page: Page) -> set[str]:
    script = """
        () => {
            const out = new Set();
            for (const img of document.querySelectorAll('img')) {
                const src = img.currentSrc || img.src || '';
                if (src) out.add(src);
            }
            return Array.from(out);
        }
    """.strip()
    try:
        return set(page.evaluate(script) or [])
    except Exception:
        return set()


def _find_file_input_anywhere(page: Page) -> Locator | None:
    for frame in page.frames:
        for selector in ["input[type='file'][multiple]", "input[type='file'][accept*='image']", "input[type='file']"]:
            try:
                loc = frame.locator(selector).last
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
    return None


def _upload_with_playwright_input(page: Page, file_paths: list[str]) -> bool:
    try:
        loc = _find_file_input_anywhere(page)
        if loc is None:
            return False
        loc.set_input_files(file_paths, timeout=0)
        print(f"  [upload] set_input_files accepted {len(file_paths)} file(s).")
        return True
    except Exception as exc:
        print(f"  [upload] set_input_files failed: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        return False


def _upload_with_cdp_dom(page: Page, file_paths: list[str]) -> bool:
    try:
        session = page.context.new_cdp_session(page)
        doc = session.send("DOM.getDocument", {"depth": -1, "pierce": True})
        root_id = doc["root"]["nodeId"]
        node_ids = session.send("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type='file']"}).get("nodeIds", [])
        if not node_ids:
            return False
        for node_id in reversed(node_ids):
            try:
                session.send("DOM.setFileInputFiles", {"nodeId": int(node_id), "files": file_paths})
                try:
                    resolved = session.send("DOM.resolveNode", {"nodeId": int(node_id)})
                    object_id = resolved.get("object", {}).get("objectId")
                    if object_id:
                        session.send(
                            "Runtime.callFunctionOn",
                            {
                                "objectId": object_id,
                                "functionDeclaration": """
                                    function() {
                                        try { this.dispatchEvent(new Event('input', {bubbles:true})); } catch (e) {}
                                        try { this.dispatchEvent(new Event('change', {bubbles:true})); } catch (e) {}
                                        return true;
                                    }
                                """,
                            },
                        )
                except Exception:
                    pass
                print(f"  [upload] CDP setFileInputFiles accepted {len(file_paths)} file(s).")
                return True
            except Exception:
                continue
    except Exception as exc:
        print(f"  [upload] CDP direct upload failed: {exc}")
    return False


def _click_upload_menu_item(page: Page) -> bool:
    script = """
    () => {
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        function textOf(el) {
            return ((el.getAttribute('aria-label') || '') + ' ' +
                    (el.getAttribute('title') || '') + ' ' +
                    (el.innerText || '') + ' ' +
                    (el.textContent || '')).replace(/\\s+/g, ' ').trim().toLowerCase();
        }
        const nodes = Array.from(document.querySelectorAll('[role="menuitem"], [role="option"], button, a'));
        const candidates = [];
        for (const el of nodes) {
            if (!visible(el)) continue;
            const t = textOf(el);
            if (!t) continue;
            if (t.includes('upload') || t.includes('add photos') || t.includes('add files') || t.includes('attach files')) {
                if (t.includes('create image') || t.includes('image generation')) continue;
                const r = el.getBoundingClientRect();
                candidates.push({el, top: r.top, left: r.left});
            }
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (b.top - a.top) || (a.left - b.left));
        candidates[0].el.click();
        return true;
    }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def upload_activity_present(page: Page) -> bool:
    script = """
        () => {
            const main = document.querySelector('main') || document.body;
            const txt = (main.innerText || '').toLowerCase();
            if (txt.includes('uploading') || txt.includes('attaching') || txt.includes('processing')) return true;
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            for (const selector of ['[role="progressbar"]', '[class*="spinner" i]', '[class*="loading" i]', '[class*="progress" i]']) {
                for (const el of document.querySelectorAll(selector)) {
                    if (visible(el)) return true;
                }
            }
            return false;
        }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _visible_uploaded_image_count(page: Page, before_srcs: set[str]) -> int:
    script = """
    (before) => {
        const baseline = new Set(before || []);
        const main = document.querySelector('main') || document.body;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width >= 24 && r.height >= 24 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        function forbidden(el) {
            return !!el.closest('nav, header, [data-message-author-role="assistant"], [class*="assistant"]');
        }
        let count = 0;
        const seen = new Set();
        for (const img of Array.from(main.querySelectorAll('img'))) {
            const src = img.currentSrc || img.src || '';
            if (!src || seen.has(src) || baseline.has(src)) continue;
            seen.add(src);
            const low = src.toLowerCase();
            if (low.includes('avatar') || low.includes('profile') || low.includes('openai') || low.includes('logo')) continue;
            if (forbidden(img) || !visible(img)) continue;
            count++;
        }
        return count;
    }
    """.strip()
    try:
        return int(page.evaluate(script, list(before_srcs)) or 0)
    except Exception:
        return 0


def duplicate_upload_modal_present(page: Page) -> bool:
    try:
        return bool(page.evaluate("""
            () => ((document.body && document.body.innerText) || '')
                .toLowerCase()
                .includes("you've already uploaded this file")
        """))
    except Exception:
        return False


def dismiss_duplicate_upload_modal(page: Page) -> bool:
    script = """
    () => {
        const bodyText = ((document.body && document.body.innerText) || '').toLowerCase();
        if (!bodyText.includes("you've already uploaded this file")) return false;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
        const ok = buttons.find(el => ((el.innerText || el.textContent || '').trim().toLowerCase() === 'ok'));
        if (ok) {
            ok.click();
            return true;
        }
        return false;
    }
    """.strip()
    try:
        clicked = bool(page.evaluate(script))
        if clicked:
            time.sleep(0.5)
        return clicked
    except Exception:
        return False


def _composer_attachment_count(page: Page) -> int:
    script = """
    () => {
        const composer = document.querySelector('form') || document.querySelector('[contenteditable="true"]')?.closest('div') || document.body;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width >= 24 && r.height >= 24 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        const seen = new Set();
        let count = 0;
        for (const img of composer.querySelectorAll('img')) {
            if (!visible(img)) continue;
            const src = img.currentSrc || img.src || '';
            if (!src || seen.has(src)) continue;
            const low = src.toLowerCase();
            if (low.includes('avatar') || low.includes('profile') || low.includes('openai') || low.includes('logo')) continue;
            seen.add(src);
            count++;
        }
        return count;
    }
    """.strip()
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


def wait_for_uploads_to_settle(page: Page, before_srcs: set[str], expected_count: int, timeout: int = 180) -> None:
    if expected_count <= 0:
        return
    deadline = time.time() + timeout
    stable_since: float | None = None
    last_state: tuple[int, bool] | None = None
    last_log = 0.0
    while time.time() < deadline:
        count = _visible_uploaded_image_count(page, before_srcs)
        attachment_count = _composer_attachment_count(page)
        if duplicate_upload_modal_present(page):
            dismiss_duplicate_upload_modal(page)
            if attachment_count >= expected_count:
                print(f"  [upload] Duplicate upload modal dismissed; existing attachments={attachment_count}, expected={expected_count}")
                return
        active = upload_activity_present(page)
        state = (count, active)
        if state != last_state:
            stable_since = time.time()
            last_state = state
        if (count >= expected_count or attachment_count >= expected_count) and not active and stable_since is not None and time.time() - stable_since >= 4.0:
            print(f"  [upload] Upload settled. visible_uploaded_images={count}, attachments={attachment_count}, expected={expected_count}")
            return
        if time.time() - last_log > 6:
            print(f"  [upload] Waiting for upload settle... visible_uploaded_images={count}, expected={expected_count}, active={active}")
            last_log = time.time()
        time.sleep(1.0)
    count = _visible_uploaded_image_count(page, before_srcs)
    raise PWTimeoutError(f"Upload did not settle before timeout: visible_uploaded_images={count}, expected={expected_count}")


def upload_images(page: Page, image_paths: list[Path], timeout: int = 180) -> None:
    if not image_paths:
        return
    for p in image_paths:
        if not p.exists():
            raise FileNotFoundError(f"Upload image not found: {p}")
        if p.suffix.lower() not in IMAGE_EXTS:
            raise ValueError(f"Upload path is not a supported image: {p}")

    file_paths = [str(p) for p in image_paths]
    before_srcs = get_all_image_srcs(page)
    print(f"  [upload] Uploading {len(file_paths)} image(s). Existing page images={len(before_srcs)}")

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    time.sleep(0.2)

    for idx, file_path in enumerate(file_paths, start=1):
        if _composer_attachment_count(page) >= idx:
            print(f"  [upload] File {idx}/{len(file_paths)} already attached; skipping duplicate upload.")
            continue
        print(f"  [upload] Uploading file {idx}/{len(file_paths)}: {Path(file_path).name}")
        uploaded = _upload_with_cdp_dom(page, [file_path])
        if not uploaded:
            uploaded = _upload_with_playwright_input(page, [file_path])

        if uploaded:
            _wait_for_uploaded_count_at_least(page, before_srcs, target_count=idx, timeout=120)
            continue

        print("  [upload] No hidden input available; using upload menu for this file.")
        try:
            _upload_one_file_via_menu(page, file_path, before_srcs, target_count=idx)
        except Exception as exc:
            raise PWTimeoutError(str(exc))

    wait_for_uploads_to_settle(page, before_srcs, expected_count=len(file_paths), timeout=timeout)
    dismiss_open_overlays(page)


def _upload_one_by_one_via_menu(page: Page, file_paths: list[str], before_srcs: set[str]) -> bool:
    for idx, file_path in enumerate(file_paths, start=1):
        _upload_one_file_via_menu(page, file_path, before_srcs, target_count=idx)
    return True


def _upload_one_file_via_menu(page: Page, file_path: str, before_srcs: set[str], target_count: int) -> None:
    print(f"  [upload] Uploading file {target_count}: {Path(file_path).name}")
    _click_plus_or_tools_near_composer(page)
    time.sleep(0.5)
    try:
        with page.expect_file_chooser(timeout=8000) as chooser_info:
            if not _click_upload_menu_item(page):
                safe_click_labels(page, ["upload", "add photos", "add files"], timeout=3)
        chooser = chooser_info.value
        chooser.set_files(file_path, timeout=0)
        print("  [upload] File chooser accepted file.")
    except Exception as exc:
        print(f"  [upload] File chooser for single file failed: {exc}")
        raise PWTimeoutError(f"Could not upload {file_path}: {exc}")
    _wait_for_uploaded_count_at_least(page, before_srcs, target_count=target_count, timeout=120)


def _wait_for_uploaded_count_at_least(
    page: Page,
    before_srcs: set[str],
    target_count: int,
    timeout: int = 90,
    stable_seconds: float = 2.0,
) -> None:
    deadline = time.time() + timeout
    stable_since: float | None = None
    last_state: tuple[int, bool, int] | None = None
    last_log = 0.0
    while time.time() < deadline:
        images_added, active, spinners = _upload_counts(page, before_srcs)
        attachment_count = _composer_attachment_count(page)
        if duplicate_upload_modal_present(page):
            dismiss_duplicate_upload_modal(page)
            if attachment_count >= target_count:
                print(f"  [upload] Duplicate upload modal dismissed; existing attachments={attachment_count}, target={target_count}")
                return
        state = (images_added, active, spinners)
        if state != last_state:
            stable_since = time.time()
            last_state = state
        if (images_added >= target_count or attachment_count >= target_count) and not active and spinners == 0 and stable_since is not None and time.time() - stable_since >= stable_seconds:
            return
        if time.time() - last_log > 5:
            print(f"  [upload] Waiting for uploaded images... visible={images_added}, target={target_count}, active={active}, spinners={spinners}")
            last_log = time.time()
        time.sleep(0.6)
    images_added, active, spinners = _upload_counts(page, before_srcs)
    raise PWTimeoutError(f"Timed out waiting for uploaded image count {target_count}; visible={images_added}, active={active}, spinners={spinners}")


def _upload_counts(page: Page, before_srcs: set[str]) -> tuple[int, bool, int]:
    images_added = _visible_uploaded_image_count(page, before_srcs)
    active = upload_activity_present(page)
    spinners = _attachment_spinner_count(page)
    return images_added, active, spinners


def _attachment_spinner_count(page: Page) -> int:
    script = """
    () => {
        const main = document.querySelector('main') || document.body;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        let count = 0;
        for (const selector of ['[role="progressbar"]', '[class*="spinner" i]', '[class*="loading" i]', '[class*="progress" i]']) {
            for (const el of main.querySelectorAll(selector)) {
                if (visible(el)) count++;
            }
        }
        return count;
    }
    """.strip()
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Send and confirmation
# ---------------------------------------------------------------------------


def _send_button_action(page: Page, composer: Locator | None = None, loose: bool = False, click: bool = False) -> bool:
    cr = None
    if composer is not None:
        try:
            cr = composer.evaluate(
                """el => {
                    const r = el.getBoundingClientRect();
                    return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};
                }"""
            )
        except Exception:
            cr = None

    script = """
    ({cr, loose, click}) => {
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function disabled(el) {
            return !!el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('disabled');
        }
        function textOf(el) {
            const iconText = Array.from(el.querySelectorAll('svg title, [class*="icon"], .material-icons'))
                .map(x => x.textContent || '').join(' ');
            return ((el.getAttribute('aria-label') || '') + ' ' +
                    (el.getAttribute('title') || '') + ' ' +
                    (el.getAttribute('data-testid') || '') + ' ' +
                    (el.innerText || '') + ' ' + iconText).replace(/\\s+/g, ' ').trim().toLowerCase();
        }
        const badWords = ['add files', 'attach', 'upload', 'voice', 'dictate', 'model', 'tools', 'image', 'photo', 'settings', 'new chat', 'share'];
        const sendWords = ['send', 'submit', 'arrow up', 'paper plane', 'send-button'];
        const nodes = Array.from(document.querySelectorAll('main button, main [role="button"], form button, form [role="button"], button[aria-label*="Send"]'));
        const candidates = [];
        for (const el of nodes) {
            if (!visible(el) || disabled(el)) continue;
            const t = textOf(el);
            const r = el.getBoundingClientRect();
            const named = sendWords.some(w => t.includes(w)) || ((el.getAttribute('type') || '').toLowerCase() === 'submit');
            if (badWords.some(w => t.includes(w)) && !named) continue;

            let score = 0;
            if (t.includes('send prompt')) score += 280;
            if (t.includes('send message')) score += 260;
            if (t.includes('send')) score += 230;
            if ((el.getAttribute('type') || '').toLowerCase() === 'submit') score += 160;
            if (t.includes('send-button')) score += 180;

            let nearComposer = false;
            if (cr) {
                const vertical = r.top < cr.bottom + 180 && r.bottom > cr.top - 120;
                const rightSide = r.left > cr.left + cr.width * 0.48 || r.right > window.innerWidth * 0.55;
                nearComposer = vertical && rightSide;
                if (nearComposer) score += 120;
            } else if (r.top > window.innerHeight * 0.55 && r.left > window.innerWidth * 0.5) {
                nearComposer = true;
                score += 80;
            }

            if (!named && !(loose && nearComposer)) continue;
            if (r.width > 220 || r.height > 120) score -= 80;
            candidates.push({el, score, top:r.top, left:r.left});
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (b.score - a.score) || (b.top - a.top) || (b.left - a.left));
        if (click) {
            candidates[0].el.scrollIntoView({block:'center', inline:'center'});
            candidates[0].el.click();
        }
        return true;
    }
    """.strip()
    try:
        return bool(page.evaluate(script, {"cr": cr, "loose": bool(loose), "click": bool(click)}))
    except Exception as exc:
        print(f"  [send] Send button query failed: {exc}")
        return False


def wait_until_send_enabled(page: Page, composer: Locator | None = None, timeout: float = 20.0, loose: bool = False) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _send_button_action(page, composer=composer, loose=loose, click=False):
            return True
        time.sleep(0.35)
    return False


def wait_until_ready_to_send(
    page: Page,
    composer: Locator | None,
    *,
    expected_attachment_count: int = 0,
    timeout: float = 180.0,
) -> None:
    deadline = time.time() + timeout
    stable_since: float | None = None
    last_state: tuple[int, bool, int, bool] | None = None
    last_log = 0.0
    while time.time() < deadline:
        attachment_count = _composer_attachment_count(page)
        active = upload_activity_present(page)
        spinners = _attachment_spinner_count(page)
        send_enabled = _send_button_action(page, composer=composer, loose=True, click=False)
        state = (attachment_count, active, spinners, send_enabled)
        if state != last_state:
            stable_since = time.time()
            last_state = state

        attachments_ready = expected_attachment_count <= 0 or attachment_count >= expected_attachment_count
        if attachments_ready and not active and spinners == 0 and send_enabled and stable_since is not None and time.time() - stable_since >= 2.0:
            print(f"  [send] Ready: attachments={attachment_count}, expected={expected_attachment_count}, send_enabled={send_enabled}")
            return

        if time.time() - last_log > 6:
            print(
                "  [send] Waiting until uploads finish and Send is enabled... "
                f"attachments={attachment_count}, expected={expected_attachment_count}, active={active}, spinners={spinners}, send_enabled={send_enabled}"
            )
            last_log = time.time()
        time.sleep(0.75)

    attachment_count = _composer_attachment_count(page)
    active = upload_activity_present(page)
    spinners = _attachment_spinner_count(page)
    send_enabled = _send_button_action(page, composer=composer, loose=True, click=False)
    raise PWTimeoutError(
        "ChatGPT was not ready to send after waiting for uploads/button readiness. "
        f"attachments={attachment_count}, expected={expected_attachment_count}, active={active}, spinners={spinners}, send_enabled={send_enabled}"
    )


def click_send_button(page: Page, composer: Locator | None = None, loose: bool = False) -> bool:
    try:
        composer = composer or find_composer(page, timeout=3)
    except Exception:
        composer = None
    if not wait_until_send_enabled(page, composer=composer, timeout=12, loose=loose):
        return False
    return _send_button_action(page, composer=composer, loose=loose, click=True)


def generation_in_progress(page: Page) -> bool:
    script = """
        () => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            const main = document.querySelector('main') || document.body;
            const txt = (main.innerText || '').toLowerCase();
            if (txt.includes('stop generating') || txt.includes('stop streaming') || txt.includes('stop responding')) return true;
            if (txt.includes('creating image') || txt.includes('generating image')) return true;
            for (const el of document.querySelectorAll('[role="progressbar"], [class*="spinner" i], [class*="loading" i]')) {
                if (visible(el)) return true;
            }
            return false;
        }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def _submission_needles(expected_prompt: str | None) -> list[str]:
    compact = _compact_prompt_compare(expected_prompt or "")
    if not compact:
        return []
    return [c.lower() for c in _sample_chunks(compact, chunk_size=80)[:5] if len(c.strip()) >= 20]


def prompt_visible_outside_composer_count(page: Page, expected_prompt: str | None) -> int:
    needles = _submission_needles(expected_prompt)
    if not needles:
        return 0
    script = """
        (needles) => {
            needles = needles.map(x => (x || '').replace(/\\s+/g, ' ').trim().toLowerCase()).filter(Boolean);
            const main = document.querySelector('main') || document.body;
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            function skipParent(el) {
                if (!el) return true;
                if (el.closest('#prompt-textarea, [contenteditable="true"], textarea, input, nav, header, button, [role="button"]')) return true;
                return !visible(el);
            }
            let count = 0;
            const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const parent = node.parentElement;
                if (skipParent(parent)) continue;
                const txt = (node.nodeValue || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (!txt) continue;
                if (needles.some(n => txt.includes(n) || n.includes(txt))) count++;
            }
            return count;
        }
    """.strip()
    try:
        return int(page.evaluate(script, needles) or 0)
    except Exception:
        return 0


def wait_for_send_confirmation(
    page: Page,
    expected_prompt: str | None,
    before_marker_count: int,
    timeout: float,
    before_url: str | None = None,
) -> bool:
    deadline = time.time() + timeout
    before_path = ""
    try:
        before_path = urllib.parse.urlparse(before_url or "").path.rstrip("/")
    except Exception:
        pass

    while time.time() < deadline:
        try:
            current_composer = find_composer(page, timeout=1)
            current_text = get_composer_text(page, current_composer).strip()
        except Exception:
            current_text = ""

        try:
            current_path = urllib.parse.urlparse(page.url or "").path.rstrip("/")
        except Exception:
            current_path = ""
        url_changed_to_conversation = bool(current_path.startswith("/c/") and current_path != before_path)

        if generation_in_progress(page):
            print("  [send] Prompt accepted; ChatGPT shows generation/progress.")
            return True

        if not current_text and url_changed_to_conversation:
            print("  [send] Prompt accepted; composer cleared and conversation URL exists.")
            return True

        if expected_prompt:
            marker_count = prompt_visible_outside_composer_count(page, expected_prompt)
            if marker_count > before_marker_count:
                print("  [send] Prompt accepted; user prompt is visible in the conversation.")
                return True

        time.sleep(0.55)
    return False


def press_enter_to_send(page: Page, composer: Locator) -> None:
    composer = find_composer(page, timeout=5)
    focus_composer(page, composer)
    page.keyboard.press("Enter")


def click_send_and_confirm(
    page: Page,
    composer: Locator,
    expected_prompt: str | None,
    min_integrity_ratio: float,
    debug_path: Path | None,
    settle_wait: float,
    submit_method: str,
    confirm_timeout: float,
    expected_attachment_count: int = 0,
    ready_timeout: float = 180.0,
) -> None:
    if settle_wait > 0:
        print(f"  [send] Waiting {settle_wait:g}s for ChatGPT composer to settle before final prompt check...")
        time.sleep(settle_wait)

    composer = find_composer(page, timeout=5)
    before_text = get_composer_text(page, composer).strip()
    if expected_prompt is not None:
        report = prompt_integrity_report(expected_prompt, before_text, min_integrity_ratio)
        print(f"  [send] Final prompt check after settle wait: {format_prompt_integrity(report)}")
        if not report["ok"]:
            write_prompt_debug_file(debug_path, expected_prompt, before_text, report, "before-send-after-settle")
            raise PWTimeoutError(
                "Refusing to send because ChatGPT composer does not contain the complete prompt after settle wait. "
                + format_prompt_integrity(report)
            )
    elif not before_text:
        raise PWTimeoutError("Cannot send because ChatGPT composer is empty")

    before_marker_count = prompt_visible_outside_composer_count(page, expected_prompt)
    before_url = page.url or ""
    print(f"  [send] Existing submitted-prompt markers before Send: {before_marker_count}")

    wait_until_ready_to_send(
        page,
        composer,
        expected_attachment_count=expected_attachment_count,
        timeout=ready_timeout,
    )

    submit_method = (submit_method or "auto").lower().strip()
    if submit_method == "enter":
        attempts = ["enter", "button", "button_loose"]
    elif submit_method == "click":
        attempts = ["button", "button_loose", "enter"]
    else:
        attempts = ["button", "enter", "button_loose"]

    last_error = ""
    for method in attempts:
        print(f"  [send] Trying submit method: {method}")
        try:
            if method == "enter":
                press_enter_to_send(page, composer)
            elif method == "button":
                if not click_send_button(page, composer=composer, loose=False):
                    last_error = "enabled Send button was not found/clickable"
                    continue
            elif method == "button_loose":
                if not click_send_button(page, composer=composer, loose=True):
                    last_error = "loose bottom-right composer submit button was not found/clickable"
                    continue
        except Exception as exc:
            last_error = str(exc)
            continue

        if wait_for_send_confirmation(
            page,
            expected_prompt=expected_prompt,
            before_marker_count=before_marker_count,
            timeout=confirm_timeout,
            before_url=before_url,
        ):
            return

        try:
            composer = find_composer(page, timeout=3)
        except Exception:
            pass
        last_error = f"{method} did not produce a submit/progress marker within {confirm_timeout:g}s"

    raise PWTimeoutError("ChatGPT did not accept the prompt. " + last_error)


# ---------------------------------------------------------------------------
# Generated image detection and saving
# ---------------------------------------------------------------------------


def _image_candidates(page: Page, baseline_srcs: set[str]) -> list[dict[str, Any]]:
    script = """
    (baselineArg) => {
        const baseline = new Set(baselineArg || []);
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('#prompt-textarea, [contenteditable="true"], textarea, input, form, nav, header, [data-message-author-role="user"]');
        }
        function assistantArea(el) {
            return !!el.closest('[data-message-author-role="assistant"], article, [data-testid*="conversation-turn"], [class*="assistant"]');
        }
        function srcBad(src) {
            const s = (src || '').toLowerCase();
            if (!s) return true;
            if (s.includes('avatar') || s.includes('profile') || s.includes('logo')) return true;
            return false;
        }
        const imgs = Array.from(document.querySelectorAll('main img, img'));
        const out = [];
        imgs.forEach((img, idx) => {
            const src = img.currentSrc || img.src || '';
            if (baseline.has(src)) return;
            if (srcBad(src)) return;
            if (!visible(img) || forbidden(img)) return;
            const r = img.getBoundingClientRect();
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            const visibleArea = r.width * r.height;
            const naturalArea = nw * nh;
            const alt = (img.getAttribute('alt') || '').toLowerCase();
            const looksGenerated = alt.includes('generated') || alt.includes('image') || visibleArea >= 30000 || naturalArea >= 250000 || (r.width >= 160 && r.height >= 160);
            if (!looksGenerated) return;
            const inAssistant = assistantArea(img);
            const inViewport = r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
            out.push({
                src,
                top: r.top,
                left: r.left,
                width: r.width,
                height: r.height,
                naturalWidth: nw,
                naturalHeight: nh,
                alt,
                assistantArea: inAssistant,
                domIndex: idx,
                score: (inAssistant ? 10000000 : 0) + (inViewport ? 500000 : 0) + visibleArea + naturalArea / 50
            });
        });
        out.sort((a, b) => (b.score - a.score) || (b.top - a.top) || (b.left - a.left));
        return out;
    }
    """.strip()
    try:
        return list(page.evaluate(script, list(baseline_srcs)) or [])
    except Exception as exc:
        print(f"  [wait-img] image candidate JS failed: {exc}")
        return []


def mark_largest_generated_image(page: Page, src: str | None = None) -> str:
    script = """
    (requestedSrc) => {
        requestedSrc = requestedSrc || '';
        const marker = 'chatgpt-auto-generated-image-candidate';
        for (const old of document.querySelectorAll(`[data-${marker}]`)) old.removeAttribute(`data-${marker}`);
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 80 && r.height > 80 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('#prompt-textarea, [contenteditable="true"], textarea, input, form, nav, header, [data-message-author-role="user"]');
        }
        const imgs = Array.from(document.querySelectorAll('main img, img')).filter(img => {
            if (!visible(img) || forbidden(img)) return false;
            const s = (img.currentSrc || img.src || '').toLowerCase();
            if (requestedSrc && (img.currentSrc || img.src || '') !== requestedSrc) return false;
            if (!s || s.includes('avatar') || s.includes('profile') || s.includes('logo')) return false;
            const r = img.getBoundingClientRect();
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            return (r.width * r.height >= 30000) || (nw * nh >= 250000) || (r.width >= 160 && r.height >= 160);
        });
        imgs.sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            const aa = !!a.closest('[data-message-author-role="assistant"], article, [data-testid*="conversation-turn"]');
            const ba = !!b.closest('[data-message-author-role="assistant"], article, [data-testid*="conversation-turn"]');
            if (aa !== ba) return ba ? 1 : -1;
            return ((b.naturalWidth || br.width) * (b.naturalHeight || br.height)) - ((a.naturalWidth || ar.width) * (a.naturalHeight || ar.height));
        });
        const img = imgs[0];
        if (!img) return '';
        img.setAttribute(`data-${marker}`, '1');
        img.scrollIntoView({block:'center', inline:'center'});
        return img.currentSrc || img.src || '';
    }
    """.strip()
    try:
        return str(page.evaluate(script, src or "") or "")
    except Exception as exc:
        print(f"  [wait-img] mark image JS failed: {exc}")
        return ""


def wait_for_generated_image(page: Page, baseline_srcs: set[str], timeout: int) -> str:
    print("  [wait-img] Waiting for a new generated image in the assistant response...")
    deadline = time.time() + timeout
    next_log = time.time() + 10
    while time.time() < deadline:
        candidates = _image_candidates(page, baseline_srcs)
        if candidates:
            assistant_candidates = [c for c in candidates if c.get("assistantArea")]
            chosen = assistant_candidates[0] if assistant_candidates else candidates[0]
            if generation_in_progress(page):
                time.sleep(4.0)
                refreshed = _image_candidates(page, baseline_srcs)
                if refreshed:
                    assistant_refreshed = [c for c in refreshed if c.get("assistantArea")]
                    chosen = assistant_refreshed[0] if assistant_refreshed else refreshed[0]
            print(
                "  [wait-img] Found generated image: "
                f"{int(chosen.get('width', 0))}x{int(chosen.get('height', 0))}, "
                f"assistantArea={chosen.get('assistantArea')}."
            )
            return str(chosen.get("src") or "")

        if time.time() >= next_log:
            marked_src = mark_largest_generated_image(page)
            if marked_src and not generation_in_progress(page):
                print("  [wait-img] Found generated image using relaxed visible-image detection.")
                return marked_src
            print("  [wait-img] Still waiting for generated image...")
            next_log = time.time() + 10
        time.sleep(2.0)

    marked_src = mark_largest_generated_image(page)
    if marked_src:
        print("  [wait-img] Timeout reached, but visible image found; proceeding to save it.")
        return marked_src
    raise PWTimeoutError(f"No generated image appeared within {timeout}s")


def infer_ext_from_src(src: str, content_type: str = "") -> str:
    if src.startswith("data:image/"):
        m = re.match(r"data:image/([a-zA-Z0-9.+-]+);", src)
        if m:
            return mimetypes.guess_extension(f"image/{m.group(1)}") or ".png"
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if ext:
            return ext
    ext = Path(urllib.parse.urlparse(src).path).suffix
    return ext if ext.lower() in IMAGE_EXTS else ".png"


def _save_data_url(data_url: str, out_path_no_ext: Path, min_bytes: int) -> Path | None:
    if not (isinstance(data_url, str) and data_url.startswith("data:image/")):
        return None
    try:
        header, b64data = data_url.split(",", 1)
        ext = infer_ext_from_src(header)
        out_path = out_path_no_ext.with_suffix(ext)
        raw = base64.b64decode(b64data)
        if len(raw) < min_bytes:
            print(f"  [dl] data URL produced only {len(raw)} bytes; rejecting as too small.")
            return None
        out_path.write_bytes(raw)
        print(f"  [dl] Saved via data URL/fetch: {out_path} ({out_path.stat().st_size} bytes)")
        return out_path
    except Exception as exc:
        print(f"  [dl] Could not save data URL: {exc}")
        return None


def _save_visible_generated_image_via_dom(page: Page, out_path_no_ext: Path, min_bytes: int) -> Path | None:
    script = """
    () => new Promise(async (done) => {
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 80 && r.height > 80 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('#prompt-textarea, [contenteditable="true"], textarea, input, form, nav, header, [data-message-author-role="user"]');
        }
        const imgs = Array.from(document.querySelectorAll('main img, img')).filter(img => {
            if (!visible(img) || forbidden(img)) return false;
            const src = img.currentSrc || img.src || '';
            if (!src) return false;
            const s = src.toLowerCase();
            if (s.includes('avatar') || s.includes('profile') || s.includes('logo')) return false;
            const r = img.getBoundingClientRect();
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            return (r.width * r.height >= 30000) || (nw * nh >= 250000) || (r.width >= 160 && r.height >= 160);
        });
        imgs.sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            const aScore = (a.naturalWidth || ar.width) * (a.naturalHeight || ar.height) + ar.width * ar.height;
            const bScore = (b.naturalWidth || br.width) * (b.naturalHeight || br.height) + br.width * br.height;
            return bScore - aScore;
        });
        const img = imgs[0];
        if (!img) return done(null);

        const src = img.currentSrc || img.src || '';
        try {
            const resp = await fetch(src, {credentials: 'include'});
            const blob = await resp.blob();
            if (!blob || blob.size < 1000) throw new Error('blob too small');
            const reader = new FileReader();
            reader.onloadend = () => done({
                dataUrl: reader.result,
                src,
                blobSize: blob.size,
                naturalWidth: img.naturalWidth || 0,
                naturalHeight: img.naturalHeight || 0,
                method: 'fetch-currentSrc'
            });
            reader.onerror = () => done(null);
            reader.readAsDataURL(blob);
            return;
        } catch (e) {
            try {
                const w = img.naturalWidth || img.width || Math.round(img.getBoundingClientRect().width);
                const h = img.naturalHeight || img.height || Math.round(img.getBoundingClientRect().height);
                if (!w || !h) throw new Error('bad dimensions');
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, w, h);
                done({
                    dataUrl: canvas.toDataURL('image/png'),
                    src,
                    blobSize: 0,
                    naturalWidth: w,
                    naturalHeight: h,
                    method: 'canvas-from-image-element'
                });
            } catch (e2) {
                done(null);
            }
        }
    })
    """.strip()
    try:
        result = page.evaluate(script)
        if not result or not isinstance(result, dict):
            return None
        saved = _save_data_url(str(result.get("dataUrl") or ""), out_path_no_ext, min_bytes=min_bytes)
        if saved:
            print(
                "  [dl] Saved generated image from DOM resource "
                f"({result.get('method')}, {result.get('naturalWidth')}x{result.get('naturalHeight')})."
            )
            return saved
    except Exception as exc:
        print(f"  [dl] DOM image-resource save failed: {exc}")
    return None


def _download_control_available(page: Page) -> bool:
    try:
        return bool(page.evaluate("""
            () => {
                function visible(el) {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                }
                for (const el of document.querySelectorAll('button, [role="button"], a[href], a[download]')) {
                    if (!visible(el)) continue;
                    const t = ((el.getAttribute('aria-label') || '') + ' ' +
                               (el.getAttribute('title') || '') + ' ' +
                               (el.getAttribute('download') || '') + ' ' +
                               (el.innerText || '')).toLowerCase();
                    if (t.includes('download')) return true;
                }
                return false;
            }
        """))
    except Exception:
        return False


def _click_download_control_js(page: Page) -> str:
    try:
        return str(page.evaluate("""
            () => {
                function visible(el) {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                }
                function textOf(el) {
                    const iconText = Array.from(el.querySelectorAll('svg title, [data-icon], .material-icons, .material-symbols-outlined'))
                        .map(x => x.getAttribute('data-icon') || x.textContent || '').join(' ');
                    return ((el.getAttribute('aria-label') || '') + ' ' +
                            (el.getAttribute('title') || '') + ' ' +
                            (el.getAttribute('data-testid') || '') + ' ' +
                            (el.getAttribute('data-test-id') || '') + ' ' +
                            (el.getAttribute('download') || '') + ' ' +
                            (el.innerText || '') + ' ' + iconText).replace(/\\s+/g, ' ').trim().toLowerCase();
                }
                const candidates = [];
                for (const el of document.querySelectorAll('button, [role="button"], a[href], a[download]')) {
                    if (!visible(el)) continue;
                    const t = textOf(el);
                    let score = 0;
                    if (t === 'download') score += 400;
                    if (t.includes('download image')) score += 350;
                    if (t.includes('download')) score += 300;
                    if (t.includes('arrow_down') || t.includes('download_for_offline')) score += 250;
                    const r = el.getBoundingClientRect();
                    if (r.top < 220) score += 30;
                    if (score > 0) candidates.push({el, score, top:r.top, left:r.left});
                }
                if (!candidates.length) return '';
                candidates.sort((a, b) => (b.score - a.score) || (a.top - b.top) || (b.left - a.left));
                candidates[0].el.click();
                return 'labelled-download';
            }
        """) or "")
    except Exception as exc:
        print(f"  [dl] Download control click failed: {exc}")
        return ""


def _unique_download_target(download_dir: Path, suggested_filename: str, fallback_ext: str = ".png") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", suggested_filename or "")
    safe = safe.strip(" .") or f"chatgpt-download{fallback_ext}"
    base = download_dir / safe
    if not base.suffix:
        base = base.with_suffix(fallback_ext)
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    for i in range(2, 10000):
        candidate = base.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
    return base.with_name(f"{stem}-{int(time.time())}{suffix}")


def _save_playwright_download(download, download_dir: Path, src: str = "") -> Path:
    ext = infer_ext_from_src(src) if src else ".png"
    target = _unique_download_target(download_dir, getattr(download, "suggested_filename", "") or "", fallback_ext=ext)
    download.save_as(str(target))
    return target


def _capture_download_from_click(page: Page, download_dir: Path, src: str, min_bytes: int, click_timeout: int = 15) -> Path | None:
    if not _download_control_available(page):
        return None

    download_dirs = _default_download_dirs(download_dir)
    before_by_dir = snapshot_download_dirs(download_dirs)
    started_at = time.time()
    method = ""

    try:
        with page.expect_download(timeout=click_timeout * 1000) as dl_info:
            method = _click_download_control_js(page)
            if not method:
                raise RuntimeError("download control disappeared before click")
        download = dl_info.value
        saved = _save_playwright_download(download, download_dir, src=src)
        size = saved.stat().st_size if saved.exists() else 0
        print(f"  [dl] Browser download captured via Playwright ({method}): {saved} ({size} bytes)")
        if size >= min_bytes:
            return saved
        print("  [dl] Playwright download was too small; checking Chrome download folders.")
        try:
            saved.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as exc:
        if method:
            print(f"  [dl] Clicked download control ({method}) but Playwright did not capture download: {exc}")
        else:
            print(f"  [dl] Could not click/capture download control: {exc}")

    return wait_for_completed_download_any(
        download_dirs=download_dirs,
        before_by_dir=before_by_dir,
        started_at=started_at,
        timeout=min(30, click_timeout + 15),
        min_bytes=min_bytes,
    )


def _open_marked_image_viewer(page: Page, src: str | None = None) -> bool:
    marked_src = mark_largest_generated_image(page, src) or mark_largest_generated_image(page)
    if not marked_src:
        return False
    try:
        candidate = page.locator('[data-chatgpt-auto-generated-image-candidate="1"]').first
        candidate.scroll_into_view_if_needed(timeout=5000)
        box = candidate.bounding_box(timeout=5000)
        if box:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            candidate.click(timeout=5000)
        time.sleep(1.5)
        return True
    except Exception as exc:
        print(f"  [dl] Could not open image viewer: {exc}")
        return False


def _default_download_dirs(primary: Path) -> list[Path]:
    dirs: list[Path] = []
    for d in [primary, Path.home() / "Downloads", Path.home() / "downloads"]:
        try:
            d = d.expanduser().resolve()
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if d not in dirs:
            dirs.append(d)
    return dirs


def snapshot_download_dir(download_dir: Path) -> dict[Path, tuple[float, int]]:
    download_dir.mkdir(parents=True, exist_ok=True)
    out: dict[Path, tuple[float, int]] = {}
    for p in download_dir.iterdir():
        if p.is_file():
            try:
                st = p.stat()
                out[p] = (st.st_mtime, st.st_size)
            except Exception:
                pass
    return out


def snapshot_download_dirs(download_dirs: list[Path]) -> dict[Path, dict[Path, tuple[float, int]]]:
    return {d: snapshot_download_dir(d) for d in download_dirs}


def wait_for_completed_download_any(
    download_dirs: list[Path],
    before_by_dir: dict[Path, dict[Path, tuple[float, int]]],
    started_at: float,
    timeout: int,
    min_bytes: int = 5000,
) -> Path | None:
    deadline = time.time() + timeout
    last_candidate: Path | None = None
    stable_since: float | None = None
    last_size: int | None = None

    while time.time() < deadline:
        candidates: list[Path] = []
        active_temp = False

        for download_dir in download_dirs:
            try:
                entries = list(download_dir.iterdir())
            except Exception:
                continue
            before = before_by_dir.get(download_dir, {})
            for p in entries:
                if not p.is_file():
                    continue
                if p.suffix.lower() in DOWNLOAD_TEMP_EXTS:
                    active_temp = True
                    continue
                try:
                    st = p.stat()
                except Exception:
                    continue
                old = before.get(p)
                changed = old is None or st.st_mtime > old[0] + 0.5 or st.st_size != old[1]
                recent = st.st_mtime >= started_at - 2
                looks_image = p.suffix.lower() in IMAGE_EXTS or "generated" in p.name.lower() or "chatgpt" in p.name.lower()
                if changed and recent and looks_image and st.st_size >= min_bytes:
                    candidates.append(p)

        if candidates and not active_temp:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            size = newest.stat().st_size
            if newest == last_candidate and size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= 2.0:
                    return newest
            else:
                last_candidate = newest
                last_size = size
                stable_since = time.time()
        time.sleep(1.0)
    return None


def download_generated_image(
    page: Page,
    context: BrowserContext,
    src: str,
    out_path_no_ext: Path,
    download_dir: Path,
    min_bytes: int,
    download_timeout: int,
) -> Path:
    out_path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    _configure_download_dir(context, download_dir)

    src = mark_largest_generated_image(page, src) or src
    download_dirs = _default_download_dirs(download_dir)
    before_by_dir = snapshot_download_dirs(download_dirs)
    started_at = time.time()
    deadline = started_at + max(15, download_timeout)
    saved: Path | None = None

    print(f"  [dl] Racing direct fetch + download watch (timeout={max(15, download_timeout)}s)...")
    while time.time() < deadline and not saved:
        # Signal A: direct fetch current img src (fastest path)
        if not saved:
            try:
                current_src = mark_largest_generated_image(page) or src
                data_url = page.evaluate(
                    """s => new Promise((done) => {
                        fetch(s, {credentials: 'include'})
                            .then(r => r.blob())
                            .then(blob => {
                                const reader = new FileReader();
                                reader.onloadend = () => done(reader.result);
                                reader.onerror = () => done(null);
                                reader.readAsDataURL(blob);
                            })
                            .catch(() => done(null));
                    })""",
                    current_src,
                )
                saved = _save_data_url(data_url, out_path_no_ext, min_bytes=min_bytes)
                if saved:
                    print(f"  [dl] Saved via direct fetch: {saved}")
            except Exception:
                pass

        # Signal C: watch download directories for new files
        if not saved:
            try:
                found = wait_for_completed_download_any(
                    download_dirs=download_dirs,
                    before_by_dir=before_by_dir,
                    started_at=started_at,
                    timeout=2,
                    min_bytes=min_bytes,
                )
                if found:
                    ext = found.suffix if found.suffix else ".png"
                    out_path = out_path_no_ext.with_suffix(ext)
                    shutil.copy2(found, out_path)
                    saved = out_path
                    print(f"  [dl] Saved from download folder: {saved}")
            except Exception:
                pass

        if not saved:
            time.sleep(0.5)

    if saved:
        return saved
    raise RuntimeError("Image was detected, but no valid image file could be saved.")


def resize_to_ratio(input_path: Path, output_path: Path, output_size: tuple[int, int]) -> None:
    img = PILImage.open(input_path).convert("RGB")
    target_ratio = output_size[0] / output_size[1]
    w, h = img.size
    current_ratio = w / h
    crop_box = None

    if abs(current_ratio - target_ratio) < 0.001:
        final = img.resize(output_size, PILImage.Resampling.LANCZOS)
    elif current_ratio > target_ratio:
        new_w = h * target_ratio
        left = (w - new_w) / 2
        crop_box = (left, 0, left + new_w, h)
        final = img.crop(crop_box).resize(output_size, PILImage.Resampling.LANCZOS)
    else:
        new_h = w / target_ratio
        top = (h - new_h) / 2
        crop_box = (0, top, w, top + new_h)
        final = img.crop(crop_box).resize(output_size, PILImage.Resampling.LANCZOS)

    final.save(output_path, "PNG", optimize=True)
    print(f"  [crop] {img.size} -> {PILImage.open(output_path).size} (crop_box={crop_box})")


# ---------------------------------------------------------------------------
# Debug and metadata
# ---------------------------------------------------------------------------


def save_debug_snapshot(page: Page, base: Path, label: str) -> dict[str, str | None]:
    base.parent.mkdir(parents=True, exist_ok=True)
    png = base.with_name(base.name + f".{label}.png")
    html = base.with_name(base.name + f".{label}.html")
    current_url = None
    try:
        current_url = page.url
    except Exception:
        pass
    try:
        page.screenshot(path=str(png), full_page=True)
    except Exception:
        png = None
    try:
        html.write_text(page.content(), encoding="utf-8", errors="replace")
    except Exception:
        html = None
    return {
        "screenshot": str(png) if png else None,
        "html": str(html) if html else None,
        "current_url": current_url,
    }


def build_image_metadata(
    status: str,
    job: PromptJob,
    prompt_metadata: dict[str, Any],
    effective_hypothesis: dict[str, Any],
    test_variables: dict[str, Any],
    saved_path: Path | None = None,
    error: str = "",
    diag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "type": "chatgpt_ad_image",
        "status": status,
        "format": job.format_id,
        "persona": job.persona_id,
        "language": job.lang_id,
        "job_key": job.job_key,
        "prompt_file": job.prompt_path.name,
        "hypothesis_type": test_variables.get("hypothesis_type", ""),
        "hypothesis_variant": test_variables.get("hypothesis_variant", ""),
        "creative_index": test_variables.get("creative_index", 1),
        "creative_total": test_variables.get("creative_total", 1),
        "test_variables": test_variables,
        "timestamp": int(time.time()),
    }
    if saved_path is not None:
        metadata["saved_file"] = str(saved_path)
    if error:
        metadata["error"] = error
    if diag:
        metadata.update(diag)
    return metadata


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------


def run() -> None:
    args = parse_args()

    prompt_dir = Path(args.prompt_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_images_dir = out_dir / "generated images"
    generated_images_dir.mkdir(parents=True, exist_ok=True)

    download_dir = Path(args.browser_download_dir).expanduser().resolve() if args.browser_download_dir else out_dir / ".browser_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    jobs, duplicates = discover_prompt_jobs(prompt_dir, args.prompt_glob, args.allow_duplicate_prompt_keys)
    validate_expected_formats(jobs, args.expected_formats, args.strict_expected_formats)
    print_job_manifest(jobs, duplicates)

    if args.dry_run:
        return

    starting_prompt = load_starting_prompt(args.starting_prompt_file)

    with tempfile.TemporaryDirectory(prefix="chatgpt-auto-refs-") as tmp:
        temp_dir = Path(tmp)
        upload_images_for_all_jobs = collect_upload_images(args.upload_dir, args.image_source_file, temp_dir)
        if upload_images_for_all_jobs:
            print("\nReference images to upload for every prompt:")
            for pth in upload_images_for_all_jobs:
                print(f"  - {pth}")

        p = None
        context: BrowserContext | None = None
        try:
            p, context = build_browser_context(args, download_dir)
            _configure_download_dir(context, download_dir)

            page = context.pages[0] if context.pages else context.new_page()
            page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)

            for index, job in enumerate(jobs, start=1):
                print(f"\n=== [{index}/{len(jobs)}] {job.job_key} :: {job.prompt_path.name} ===")

                prompt_body = job.prompt_path.read_text(encoding="utf-8")
                prompt_metadata = load_prompt_metadata(job.prompt_path, prompt_body)
                prompt_hypothesis = prompt_metadata.get("hypothesis") if isinstance(prompt_metadata.get("hypothesis"), dict) else {}
                effective_hypothesis = prompt_hypothesis if prompt_hypothesis else {}
                test_variables = build_test_variables(job, prompt_metadata, effective_hypothesis)
                full_prompt = prepend_starting_prompt(starting_prompt, prompt_body)

                print(f"  Prompt file stats: {len(full_prompt)} chars, {full_prompt.count(chr(10)) + 1} lines")

                attempt = 1
                while attempt <= max(1, args.max_attempts):
                    page_for_job: Page | None = None
                    try:
                        page_for_job = open_prompt_tab(context, page, index if attempt == 1 else 999999, args.first_tab_mode)
                        page = page_for_job
                        navigate_to_fresh_chat(
                            page_for_job,
                            manual_login_timeout=args.manual_login_timeout,
                            strict_login=(args.login_wait_mode == "strict"),
                        )

                        select_model_and_tool_if_requested(page_for_job, args)

                        if upload_images_for_all_jobs:
                            upload_images(page_for_job, upload_images_for_all_jobs, timeout=180)

                        baseline_srcs = get_all_image_srcs(page_for_job)
                        debug_base = out_dir / "debug" / job.output_stem
                        prompt_debug_path = debug_base.with_suffix(".prompt-debug.txt")

                        composer = set_prompt_text(
                            page_for_job,
                            full_prompt,
                            method=args.prompt_paste_method,
                            verify_timeout=args.prompt_paste_timeout,
                            min_integrity_ratio=args.prompt_integrity_ratio,
                            debug_path=prompt_debug_path,
                        )

                        click_send_and_confirm(
                            page_for_job,
                            composer=composer,
                            expected_prompt=full_prompt,
                            min_integrity_ratio=args.prompt_integrity_ratio,
                            debug_path=prompt_debug_path,
                            settle_wait=args.prompt_settle_wait,
                            submit_method=args.send_submit_method,
                            confirm_timeout=args.send_confirm_timeout,
                            expected_attachment_count=len(upload_images_for_all_jobs),
                            ready_timeout=max(180.0, float(args.timeout)),
                        )

                        src = wait_for_generated_image(page_for_job, baseline_srcs, timeout=args.timeout)
                        saved_path = download_generated_image(
                            page_for_job,
                            context,
                            src,
                            out_path_no_ext=generated_images_dir / job.output_stem,
                            download_dir=download_dir,
                            min_bytes=args.min_image_bytes,
                            download_timeout=args.download_timeout,
                        )

                        metadata = build_image_metadata(
                            status="success",
                            job=job,
                            prompt_metadata=prompt_metadata,
                            effective_hypothesis=effective_hypothesis,
                            test_variables=test_variables,
                            saved_path=saved_path,
                        )
                        saved_path.with_suffix(".json").write_text(
                            json.dumps(metadata, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        print(f"  [done] Saved image: {saved_path}")

                        # ------------------------------------------------------------------
                        # Step: Ensure exact target dimensions via PIL crop
                        # ------------------------------------------------------------------
                        try:
                            target = (1080, 1350) if args.aspect_ratio == "4:5" else (1080, 1920)
                            resize_to_ratio(saved_path, saved_path, target)
                        except Exception as exc:
                            print(f"  [crop] Failed (keeping original): {exc}")

                        time.sleep(args.sleep_after_download)
                        break

                    except Exception as exc:
                        print(f"  [error] Attempt {attempt} failed for {job.job_key}: {exc}")
                        diag: dict[str, Any] = {}
                        if page_for_job is not None:
                            try:
                                diag = save_debug_snapshot(page_for_job, out_dir / "debug" / job.output_stem, f"attempt{attempt}-error")
                            except Exception:
                                diag = {}
                        metadata = build_image_metadata(
                            status="error",
                            job=job,
                            prompt_metadata=prompt_metadata,
                            effective_hypothesis=effective_hypothesis,
                            test_variables=test_variables,
                            error=str(exc),
                            diag=diag,
                        )
                        (out_dir / "debug").mkdir(parents=True, exist_ok=True)
                        (out_dir / "debug" / f"{job.output_stem}.error.json").write_text(
                            json.dumps(metadata, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        if attempt >= max(1, args.max_attempts):
                            if args.continue_on_error:
                                print("  [error] Continuing to next prompt because --continue-on-error is set.")
                                break
                            raise
                        attempt += 1

        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if p is not None:
                    p.stop()
            except Exception:
                pass


if __name__ == "__main__":
    run()
