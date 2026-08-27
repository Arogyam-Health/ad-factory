#!/usr/bin/env python3
"""Strict Gemini web image-generation automation for Playwright.

Per prompt:
  1. Use one real tab for that prompt. The first prompt reuses the initial
     blank tab when safe, so there is no extra empty Gemini tab at startup.
  2. Navigate to a fresh Gemini chat only. Never click old chat rows or their
     three-dot menus.
  3. Optionally select Pro/Create image using guarded selectors only.
  4. Upload references, wait for attachments to settle, paste the prompt with
     keyboard/clipboard input events, verify full prompt integrity, and confirm Send
     actually submitted.
  5. Wait for a large image in the latest assistant/model response.
  6. Download the exact generated image and wait until the file is complete.
  7. Move to the next prompt tab without closing previous prompt tabs.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import textwrap
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_agent_runtime.browser import (
    close_cdp_page,
    install_job_signal_handlers,
    mark_cdp_attached,
    open_cdp_page,
    release_browser,
    remember_job_page,
    resolve_browser_executable,
)
from playwright.sync_api import sync_playwright, Page, Locator, TimeoutError as PWTimeoutError
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading


GEMINI_URL = "https://gemini.google.com/app"
FORMAT_ORDER = ["BA", "FEAT", "HERO", "TEST", "UGC"]
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
)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
DOWNLOAD_TEMP_EXTS = {".crdownload", ".tmp", ".part"}


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
    parser = argparse.ArgumentParser(description="Gemini web image generation automation")
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
        default="dashboard/backend/defaults/starting_prompt.txt",
        help="Starter prompt prepended to each prompt before sending. Use an empty value to disable.",
    )
    parser.add_argument("--image-source-file", default="")
    parser.add_argument(
        "--upload-manifest",
        default="",
        help="Explicit ordered local JSON upload manifest. Takes precedence over legacy image sources.",
    )
    parser.add_argument(
        "--result-manifest",
        default="",
        help="Optional local JSON file receiving the exact generated output path.",
    )
    parser.add_argument(
        "--upload-dir",
        default="",
        help="Optional directory of reference images to upload. If empty, no directory upload is used.",
    )
    parser.add_argument("--logo-key", default="LIGHT_LOGO_URL")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--aspect-ratio", default="4:5", choices=["4:5", "9:16"],
                        help="Aspect ratio for output images; 4:5 (default) or 9:16")
    parser.add_argument("--timeout", type=int, default=1800, help="Generation timeout per prompt")
    parser.add_argument("--download-timeout", type=int, default=180)
    parser.add_argument("--sleep-after-download", type=float, default=3.0)
    parser.add_argument("--min-image-bytes", type=int, default=20_000)
    parser.add_argument("--max-attempts", type=int, default=1, help="Default 1 avoids accidental repeat tabs")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved prompt jobs and exit")
    parser.add_argument("--hypothesis-config", default="", help="Path to hypothesis config JSON (type, variant)")

    parser.add_argument(
        "--expected-formats",
        default="",
        help="Comma-separated expected format IDs, e.g. BA,FEAT,HERO,TEST,UGC",
    )
    parser.add_argument(
        "--strict-expected-formats",
        action="store_true",
        help="Fail if --expected-formats are missing for any persona",
    )
    parser.add_argument(
        "--allow-duplicate-prompt-keys",
        action="store_true",
        help="Do not skip duplicate FORMAT/Pxx prompt files",
    )

    parser.add_argument("--user-data-dir", default="")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--manual-login-timeout", type=int, default=180)
    parser.add_argument("--login-wait-mode", choices=["auto", "strict"], default="auto")
    parser.add_argument("--browser-download-dir", default="")
    parser.add_argument(
        "--first-tab-mode",
        choices=["reuse-blank", "new"],
        default="reuse-blank",
        help="reuse-blank prevents an extra empty Gemini tab at startup",
    )

    parser.add_argument(
        "--skip-model-selection",
        action="store_true",
        help="Do not touch Gemini model/tool controls",
    )
    parser.add_argument(
        "--require-pro-model",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use --no-require-pro-model if Gemini UI changed and selection cannot be confirmed",
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
        help="How to insert long prompts. auto tries clipboard paste, JS insertion, then safe keyboard insert_text.",
    )
    parser.add_argument(
        "--prompt-paste-timeout",
        type=int,
        default=35,
        help="Seconds to wait for the full prompt to appear in Gemini after paste",
    )
    parser.add_argument(
        "--prompt-integrity-ratio",
        type=float,
        default=0.98,
        help="Minimum compact-text ratio required before the script is allowed to click Send",
    )
    parser.add_argument(
        "--prompt-settle-wait",
        type=float,
        default=5.0,
        help="Seconds to wait after the prompt is pasted before the final completeness check and Send",
    )
    parser.add_argument(
        "--send-submit-method",
        choices=["enter", "click", "auto"],
        default="enter",
        help="How to submit after the prompt is complete. Default enter presses Enter first, then falls back to button clicks.",
    )
    parser.add_argument(
        "--send-confirm-timeout",
        type=float,
        default=35.0,
        help="Seconds to wait for proof that Gemini accepted the prompt after a submit attempt",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue to the next prompt after a job failure. Default is fail-fast so problems do not silently skip prompts.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Prompt discovery and dedupe
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
    stem = re.sub(r"^run_[a-f0-9]{12}__", "", path.stem, flags=re.IGNORECASE)
    patterns = [
        # canonical (new slug): <FMT>_<slug>_<LANG>_<angle>[_A<NN>]
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)_(?P<angle>[a-z][a-z_]*?)(?:_(?P<variant>[AV]\d+))?$",
        # legacy: <FMT>_P<NN>_<LANG>_<angle>[_A<NN>]
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)_(?P<angle>[a-z][a-z_]*?)(?:_(?P<variant>[AV]\d+))?$",
        # angle-less (new slug): <FMT>_<slug>_<LANG>[_A<NN>]
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)(?:_(?P<variant>[AV]\d+))?$",
        # legacy: <FMT>_P<NN>_<LANG>[_A<NN>] with optional trailing angle
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)(?:_(?P<variant>[AV]\d+))?(?:_(?P<angle2>[a-z_]+))?$",
        # legacy: <FMT>_P<NN>_<LANG>
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Za-z0-9]+)_P(?P<num>\d+)_(?P<lang>[A-Za-z0-9]+)$",
    ]
    for pat in patterns:
        m = re.search(pat, stem, flags=re.IGNORECASE)
        if m:
            fmt = m.group("fmt").upper()
            # New slug-based patterns carry the persona NAME (e.g. ``always_hungry``);
            # legacy P<NN> patterns carry the persona NUMBER. Preserve both forms
            # so downstream code can use the slug in image filenames.
            if "slug" in m.groupdict() and m.group("slug"):
                persona = m.group("slug").lower()
            else:
                persona = f"P{int(m.group('num')):02d}"
            lang = m.group("lang").upper() if "lang" in m.groupdict() and m.group("lang") else "XX"
            variant = m.group("variant").upper() if "variant" in m.groupdict() and m.group("variant") else ""
            angle = m.group("angle") if "angle" in m.groupdict() and m.group("angle") else (m.group("angle2") if "angle2" in m.groupdict() and m.group("angle2") else "")
            return fmt, persona, lang, variant, angle

    # Defensive fallback: find a known format token anywhere before Pxx.
    m_persona = re.search(r"(?:^|_)P(?P<num>\d+)(?:_|$)", stem, flags=re.IGNORECASE)
    persona_id = f"P{int(m_persona.group('num')):02d}" if m_persona else "P00"
    tokens = [t.upper() for t in re.split(r"[^A-Za-z0-9]+", stem) if t]
    known_formats = ["BA", "FEAT", "HERO", "TEST", "UGC"]
    for token in tokens:
        if token in known_formats:
            return token, persona_id, "XX", "", ""

    fmt = next((t for t in tokens if t not in {"FINAL", "OUTPUT", persona_id}), "PROMPT")
    return fmt, persona_id, "XX", "", ""


def discover_prompt_jobs(prompt_dir: Path, pattern: str, allow_duplicates: bool, aspect_ratio: str = "4:5") -> tuple[list[PromptJob], list[PromptJob]]:
    aspect_folder = "9_16" if aspect_ratio == "9:16" else "4_5"
    raw_paths = [p for p in prompt_dir.glob(pattern) if p.is_file()]
    if not raw_paths:
        raise FileNotFoundError(f"No prompt files found in {prompt_dir} with pattern {pattern!r}")

    raw_jobs: list[PromptJob] = []
    for path in raw_paths:
        fmt, persona, lang, variant, concept_angle = _parse_prompt_name(path)
        scope_match = re.match(r"^(run_[a-f0-9]{12}__)", path.stem, flags=re.IGNORECASE)
        scope_prefix = scope_match.group(1) if scope_match else ""
        variant_suffix = f"_{variant}" if variant else ""
        key = f"{scope_prefix}{fmt}_{persona}_{lang}{variant_suffix}"
        if concept_angle and concept_angle not in key:
            key += f"_{concept_angle}"
        # Reuse the prompt's stem verbatim so the generated image matches the
        # prompt file 1:1 (only the extension changes). E.g. BA_always_hungry_EN_pain_point
        # for the new slug format, or BA_P01_EN_pain_point for the legacy format.
        # Append the aspect ratio folder name so 4:5 and 9:16 images have distinct
        # filenames (e.g. ..._pain_point_4_5.png vs ..._pain_point_9_16.png).
        if concept_angle:
            safe_stem = f"{scope_prefix}{fmt}_{persona}_{lang}_{concept_angle}{variant_suffix}_{aspect_folder}"
        else:
            safe_stem = f"{scope_prefix}gemini-{fmt.lower()}-{persona.lower()}-{lang.lower()}{('-' + variant.lower()) if variant else ''}_{aspect_folder}"
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


def load_prompt_metadata(prompt_path: Path, prompt_text: str) -> dict[str, Any]:
    sidecar = prompt_path.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            print(f"Warning: could not read prompt metadata {sidecar.name}: {exc}")

    def field(label: str) -> str:
        match = re.search(rf"^-\s*{re.escape(label)}\s*:\s*(.+)$", prompt_text, flags=re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    return {
        "background": {
            "slot": field("Background slot").split(" - ", 1)[0].strip(),
            "seed": int(field("Background seed")) if field("Background seed").isdigit() else "",
            "seeded_direction": field("Seeded background direction (single sentence, exact)"),
        },
        "visual_archetype": {"id": field("Selected visual archetype").split(" - ", 1)[0].strip()},
    }


def build_test_variables(job: PromptJob, prompt_metadata: dict[str, Any], effective_hypothesis: dict[str, Any]) -> dict[str, Any]:
    visual_pattern = prompt_metadata.get("visual_pattern") if isinstance(prompt_metadata.get("visual_pattern"), dict) else {}
    visual_archetype = prompt_metadata.get("visual_archetype") if isinstance(prompt_metadata.get("visual_archetype"), dict) else {}
    if not visual_pattern and visual_archetype:
        visual_pattern = {
            "id": visual_archetype.get("id", ""),
            "label": visual_archetype.get("label", ""),
            "selected_by_user": bool(visual_archetype.get("forced")),
            "selection_mode": "manual" if visual_archetype.get("forced") else "auto_rotate",
        }

    return {
        "persona": prompt_metadata.get("persona", job.persona_id),
        "persona_number": prompt_metadata.get("persona_number", ""),
        "persona_name": prompt_metadata.get("persona_name", ""),
        "format": prompt_metadata.get("format", job.format_id),
        "language": prompt_metadata.get("language", job.lang_id),
        "aspect_ratio": prompt_metadata.get("aspect_ratio", ""),
        "hypothesis": effective_hypothesis,
        "hypothesis_type": effective_hypothesis.get("type", "") if effective_hypothesis else "",
        "hypothesis_variant": effective_hypothesis.get("variant", "") if effective_hypothesis else "",
        "headline_angle": prompt_metadata.get("headline_angle", ""),
        "awareness_stage": prompt_metadata.get("awareness_stage", ""),
        "concept_angle": prompt_metadata.get("concept_angle", ""),
        "visual_pattern": visual_pattern,
        "visual_archetype": visual_archetype,
        "background": prompt_metadata.get("background", {}),
        "background_group_key": prompt_metadata.get("background_group_key", ""),
        "background_decisions": prompt_metadata.get("background_decisions", {}),
        "creative_index": prompt_metadata.get("creative_index", 1),
        "creative_total": prompt_metadata.get("creative_total", 1),
        "multiplier": prompt_metadata.get("multiplier", prompt_metadata.get("creative_total", 1)),
    }


def build_image_metadata(
    *,
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
        "type": "ad_image",
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


# ---------------------------------------------------------------------------
# Image sources for upload
# ---------------------------------------------------------------------------


def parse_image_source_file(path: Path, logo_key: str) -> list[str]:
    logo_map: dict[str, str] = {}
    regular_sources: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                logo_map[key] = value
            continue
        regular_sources.append(line)
    selected_logo = logo_map.get(logo_key, "")
    return ([selected_logo] + regular_sources) if selected_logo else regular_sources


def parse_upload_manifest(path: Path) -> list[Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("Upload manifest requires ordered entries")
    resolved: list[Path] = []
    for expected_position, entry in enumerate(entries, start=1):
        if (
            not isinstance(entry, dict)
            or int(entry.get("position") or 0) != expected_position
            or entry.get("role") not in {"product", "logo", "reference", "source_creative", "replacement"}
        ):
            raise ValueError("Upload manifest entries are invalid or unordered")
        image = Path(str(entry.get("path") or "")).expanduser()
        if not image.is_absolute() or not image.is_file() or image.suffix.lower() not in IMAGE_EXTS:
            raise ValueError("Upload manifest contains an invalid local image")
        resolved.append(image.resolve())
    return resolved


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
    if not paths:
        raise ValueError("No images resolved for upload")
    return paths


def collect_upload_images_from_dir(upload_dir: Path) -> list[Path]:
    if not upload_dir.exists():
        raise FileNotFoundError(f"Upload directory not found: {upload_dir}")
    if not upload_dir.is_dir():
        raise NotADirectoryError(f"Upload path is not a directory: {upload_dir}")
    images = [p.resolve() for p in sorted(upload_dir.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not images:
        raise FileNotFoundError(f"No image files found in {upload_dir}")
    return images


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------


def resolve_browser_binary(args: argparse.Namespace) -> str:
    return resolve_browser_executable("chrome")


def grant_gemini_permissions(context) -> None:
    """Best-effort permissions needed for clipboard paste in CDP/visible Chrome."""
    try:
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://gemini.google.com")
    except Exception as exc:
        print(f"  [browser] Could not grant clipboard permissions; will use fallbacks: {exc}")


def build_browser_context(args: argparse.Namespace, download_dir: Path):
    p = sync_playwright().start()

    try:
        import socket
        sock = socket.socket()
        sock.settimeout(1)
        sock.connect(("127.0.0.1", 9222))
        sock.close()
        print("  [connect] Connecting to existing Chrome via CDP on port 9222...")
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        ctx.set_default_timeout(30000)
        grant_gemini_permissions(ctx)
        mark_cdp_attached(ctx)
        return p, ctx
    except Exception:
        pass

    print("  [launch] No Chrome debug port found, launching fresh browser...")
    selected_binary = resolve_browser_binary(args)

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
            "--disable-web-security",
            "--test-type",
            "--start-maximized",
        ],
    }
    if selected_binary:
        launch_opts["executable_path"] = selected_binary

    context = p.chromium.launch_persistent_context(
        str(download_dir / ".pw_profile"),
        **launch_opts,
    )
    context.set_default_timeout(30000)
    grant_gemini_permissions(context)
    context.pages[0].goto("about:blank")
    return p, context


def _configure_download_dir(context, download_dir: Path) -> None:
    """Best-effort configure Chrome's download directory.

    This matters when attached to an existing Chrome via CDP. Playwright's
    downloads_path is only guaranteed for browsers it launches itself; CDP
    sessions may otherwise save to the user's default Downloads folder.
    """
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    # Normal Playwright downloads are still handled by page.expect_download().
    # This CDP configuration is a fallback so directory polling also works.
    try:
        pages = list(getattr(context, "pages", []) or [])
        if not pages:
            return
        session = context.new_cdp_session(pages[0])
        try:
            session.send("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(download_dir),
                "eventsEnabled": True,
            })
            return
        except Exception:
            pass
        try:
            session.send("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(download_dir),
            })
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# App readiness and fresh chat navigation
# ---------------------------------------------------------------------------


def is_blankish_url(url: str) -> bool:
    lower = (url or "").lower()
    return lower in ("", "about:blank") or lower.startswith("chrome://newtab") or lower.startswith("data:")


def gemini_app_ready(page: Page) -> bool:
    try:
        current = (page.url or "").lower()
    except Exception:
        return False
    if "gemini.google.com" not in current:
        return False
    selectors = [
        "rich-textarea div[contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
        "div[contenteditable='true']",
        "textarea[aria-label*='message']",
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
    print("Waiting for Gemini login/readiness...")
    deadline = time.time() + timeout
    next_log = time.time() + 5
    while time.time() < deadline:
        if gemini_app_ready(page):
            print(f"Gemini UI looks ready at {page.url}. Continuing.")
            return
        if time.time() >= next_log:
            try:
                current = page.url
            except Exception:
                current = "<unavailable>"
            print(f"Still waiting for Gemini UI readiness... current URL: {current}")
            next_log = time.time() + 5
        time.sleep(1.0)
    msg = f"Timed out after {timeout}s waiting for Gemini readiness"
    if strict:
        raise PWTimeoutError(msg)
    print(f"{msg}; continuing in auto mode.")


def _url_is_base_app(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if "gemini.google.com" not in parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    return path == "/app"


def _url_looks_like_old_conversation(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if "gemini.google.com" not in parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    return path.startswith("/app/") and path != "/app"


def _url_looks_temporary(url: str) -> bool:
    lower = (url or "").lower()
    return "temporary" in lower or "temp_chat" in lower or "tempchat" in lower


def dismiss_open_overlays(page: Page) -> None:
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    selectors = [
        ".mat-drawer-backdrop",
        ".cdk-overlay-backdrop",
        "[role='dialog'] [aria-label='Close']",
        "button[aria-label*='Close']",
    ]
    for selector in selectors:
        for el in page.locator(selector).all():
            try:
                if el.is_visible():
                    el.click()
            except Exception:
                try:
                    el.evaluate("el => el.click()")
                except Exception:
                    pass


def page_heading_looks_temporary(page: Page) -> bool:
    script = r"""
        const headings = Array.from(document.querySelectorAll('main h1, main h2, header h1, header h2, [role="heading"]'));
        for (const h of headings) {
            const t = (h.innerText || h.textContent || '').trim().toLowerCase();
            const r = h.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && t === 'temporary chat') return true;
        }
        const title = (document.title || '').toLowerCase();
        return title.includes('temporary chat');
    """
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def assert_not_temporary_chat(page: Page) -> None:
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    if _url_looks_temporary(url) or page_heading_looks_temporary(page):
        raise RuntimeError(
            "Gemini appears to be in Temporary chat mode. The script will not continue "
            "because you asked not to use temporary chats. Turn Temporary chat off manually "
            "and re-run."
        )


def open_prompt_tab(context, page: Page, job_index: int, first_tab_mode: str, download_dir: Path) -> Page:
    """Open/switch to the tab that will own this prompt."""
    from local_agent_runtime.browser import open_cdp_page

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
    return open_cdp_page(context, new_window=False)




def goto_gemini_app(page: Page, timeout_ms: int = 60000) -> None:
    """Navigate to Gemini without failing only because the SPA never fires full load."""
    try:
        page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    except PWTimeoutError:
        current = ""
        try:
            current = page.url or ""
        except Exception:
            pass
        if "gemini.google.com" in current or gemini_app_ready(page):
            print("  [fresh] Gemini navigation timed out waiting for DOM load, but the app is present; continuing.")
            return
        raise


def navigate_to_fresh_chat(page: Page, manual_login_timeout: int, strict_login: bool) -> None:
    """Navigate to a fresh Gemini chat using URL Stability Locks to defeat SPA race conditions."""
    print("  [fresh] Navigating to Gemini...")

    page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
    time.sleep(0.5)
    goto_gemini_app(page)

    wait_for_manual_login(page, timeout=manual_login_timeout, strict=strict_login)
    dismiss_open_overlays(page)

    deadline = time.time() + 60

    while time.time() < deadline:
        time.sleep(2.0)

        current_url = page.url or ""
        assert_not_temporary_chat(page)

        if not _url_is_base_app(current_url):
            print(f"  [fresh] Dirty URL detected ({current_url}). Hunting for 'New Chat' button...")

            clicked = page.evaluate(r"""
                const clickables = Array.from(document.querySelectorAll('a, button, [role="button"], div[role="link"]'));
                for (const el of clickables) {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;

                    if (el.closest('history-list, .conversation-list, [aria-label*="Recent"], [data-test-id*="conversation"], .recent')) {
                        continue;
                    }

                    const href = el.getAttribute('href') || '';
                    if (href.match(/\/app\/[a-zA-Z0-9_-]{5,}/)) {
                        continue;
                    }

                    const txt = (el.innerText || '').toLowerCase().trim();
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase().trim();
                    const title = (el.getAttribute('title') || '').toLowerCase().trim();

                    if (txt === 'new chat' || aria === 'new chat' || title === 'new chat' || (href === '/app' && r.top < 150)) {
                        el.click();
                        return true;
                    }
                }
                return false;
            """.lstrip())

            if clicked:
                print("  [fresh] Clicked 'New Chat'. Waiting for UI to react...")
                time.sleep(2.5)
            else:
                print("  [fresh] Could not find the UI button. Doing a hard refresh instead...")
                goto_gemini_app(page)
            continue

        print("  [fresh] URL is currently base /app. Locking down for 3 seconds to test stability...")
        time.sleep(3.0)

        post_wait_url = page.url or ""
        if not _url_is_base_app(post_wait_url):
            print(f"  [fresh] HIJACKED! Gemini auto-redirected to {post_wait_url} at the last second. Retrying...")
            continue

        try:
            bubbles_count = int(page.evaluate(
                "return document.querySelectorAll('user-query, model-response, [data-author=\"user\"], .user-query, .model-response').length;"
            ))
        except Exception:
            bubbles_count = 0

        if bubbles_count > 0:
            print(f"  [fresh] Fakeout! URL is /app but found {bubbles_count} old chat bubbles. Forcing a refresh...")
            page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
            time.sleep(0.5)
            goto_gemini_app(page)
            continue

        try:
            find_composer(page, timeout=3)
            print("  [fresh] SUCCESS! Verified stable, truly fresh chat.")
            return
        except Exception:
            print("  [fresh] Clean chat, but composer isn't fully loaded yet. Waiting...")
            pass

    raise RuntimeError("Failed to establish a fresh Gemini chat. SPA state kept hijacking the session.")



# ---------------------------------------------------------------------------
# Safe UI clicking helpers
# ---------------------------------------------------------------------------


def _safe_click_js(page: Page, labels: Iterable[str], exact: bool = False, timeout: float = 8.0) -> bool:
    labels_list = [str(x).lower().strip().replace("'", "\\'") for x in labels if str(x).strip()]
    bad_words_list = [x.lower().strip().replace("'", "\\'") for x in UNSAFE_CLICK_WORDS]
    deadline = time.time() + timeout
    while time.time() < deadline:
        labels_copy = list(labels_list)
        js = f"""
        (function() {{
            const labels = {labels_copy};
            const exact = {str(exact).lower()};
            const badWords = {bad_words_list};
            function visible(el) {{
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }}
            function inForbiddenArea(el) {{
                return !!el.closest('nav, [role="navigation"], [aria-label*="Recent"], [aria-label*="Conversation history"], .conversation-list, .history');
            }}
            function bad(el) {{
                const txt = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '')).toLowerCase();
                if (badWords.some(function(w) {{ return txt.includes(w); }})) return true;
                return false;
            }}
            const roots = Array.from(document.querySelectorAll('main, header, [role="dialog"], .cdk-overlay-container, body'));
            const nodes = [];
            for (const root of roots) {{
                for (const el of root.querySelectorAll('button,a,[role="button"],[role="menuitem"],[role="option"],[role="menuitemradio"],mat-option')) {{
                    if (!nodes.includes(el)) nodes.push(el);
                }}
            }}
            const candidates = [];
            for (const el of nodes) {{
                if (!visible(el) || bad(el)) continue;
                if (inForbiddenArea(el)) continue;
                const txt = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '')).trim().toLowerCase();
                if (!txt) continue;
                for (const label of labels) {{
                    if ((exact && txt === label) || (!exact && txt.includes(label))) {{
                        candidates.push(el);
                        break;
                    }}
                }}
            }}
            if (!candidates.length) return false;
            candidates.sort(function(a, b) {{
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.top - ar.top) || (br.left - ar.left);
            }});
            candidates[0].scrollIntoView({{block:'center', inline:'center'}});
            candidates[0].click();
            return true;
        }})();
        """
        try:
            clicked = page.evaluate(js)
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


# ---------------------------------------------------------------------------
# Composer, model/tool, upload, and send
# ---------------------------------------------------------------------------


def find_composer(page: Page, timeout: int = 30) -> Locator:
    selectors = [
        "rich-textarea div[contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
        "main div[contenteditable='true']",
        "textarea[aria-label*='message']",
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
    raise PWTimeoutError(f"Could not find Gemini composer. Last error: {last_error}")


def get_composer_text(page: Page, composer: Locator) -> str:
    script = r"""
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
    # Gemini's rich textarea may collapse indentation but should not remove words.
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
        composer.scroll_into_view_if_needed()
    except Exception:
        pass
    for _ in range(3):
        try:
            composer.click()
            break
        except Exception:
            dismiss_open_overlays(page)
            time.sleep(0.3)
    time.sleep(0.15)


def clear_composer_keyboard(page: Page, composer: Locator) -> None:
    focus_composer(page, composer)
    try:
        composer.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
    except Exception:
        pass
    time.sleep(0.25)


def paste_prompt_via_keyboard(page: Page, text: str) -> bool:
    """Insert text without synthesizing Enter key presses.

    keyboard.type() is unsafe for long multiline Gemini prompts because every
    newline is delivered as an Enter key press and Gemini can submit early.
    insert_text() sends a text insertion command instead, so newlines stay in
    the composer.
    """
    try:
        composer = find_composer(page, timeout=10)
        clear_composer_keyboard(page, composer)
        composer = find_composer(page, timeout=5)
        focus_composer(page, composer)
        composer.click()
        page.keyboard.insert_text(text)
        return True
    except Exception as exc:
        print(f"  [prompt] keyboard insert_text failed: {exc}")
        return False


def paste_prompt_via_clipboard(page: Page, text: str) -> bool:
    try:
        # Only use clipboard when explicitly requested. Verify the browser
        # clipboard actually contains our prompt before pressing Control+V;
        # otherwise old user clipboard contents can be pasted into Gemini.
        probe = page.evaluate(
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
        if not probe:
            raise RuntimeError("clipboard verification failed; refusing to paste stale clipboard")
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
    script = r"""
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


def wait_for_prompt_integrity(
    page: Page,
    expected: str,
    timeout: float,
    min_ratio: float,
) -> tuple[Locator, str, dict[str, Any]]:
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


def _prompt_methods_for(method: str) -> list[str]:
    # Avoid using the OS/browser clipboard in auto mode. On a CDP-attached
    # desktop Chrome, navigator.clipboard.writeText can silently fail or be
    # denied; Control+V then pastes whatever the user last copied (for example
    # the shell command), which is exactly what we want to avoid.
    #
    # keyboard here uses Playwright keyboard.insert_text(), NOT keyboard.type(),
    # so multiline prompts are inserted as text and newlines do not submit.
    if method in {"auto", "cdp"}:
        return ["keyboard", "js"]
    return [method]


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
            page,
            expected=text,
            timeout=verify_timeout,
            min_ratio=min_integrity_ratio,
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
        + " | actual_start="
        + repr(last_report.get("actual_preview_start", "")[:120])
        + " | actual_end="
        + repr(last_report.get("actual_preview_end", "")[-120:])
    )



def _locator_rect(locator: Locator | None) -> dict[str, float] | None:
    if locator is None:
        return None
    try:
        return locator.evaluate(
            """el => {
                const r = el.getBoundingClientRect();
                return {top:r.top, bottom:r.bottom, left:r.left, right:r.right, width:r.width, height:r.height};
            }"""
        )
    except Exception:
        return None

def _send_button_diagnostics(page: Page, composer: Locator | None = None) -> list[str]:
    cr = _locator_rect(composer)
    script = r"""
        (cr) => {
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            const out = [];
            const nodes = Array.from(document.querySelectorAll('main button, main [role="button"]'));
            for (const b of nodes) {
                if (!visible(b)) continue;
                const r = b.getBoundingClientRect();
                if (cr) {
                    const nearY = r.top < cr.bottom + 220 && r.bottom > cr.top - 120;
                    if (!nearY) continue;
                } else if (r.top < window.innerHeight * 0.45) {
                    continue;
                }
                const txt = ((b.getAttribute('aria-label') || '') + ' | ' + (b.getAttribute('title') || '') + ' | ' + (b.innerText || '')).replace(/\s+/g, ' ').trim();
                out.push(`${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)} disabled=${!!b.disabled || b.getAttribute('aria-disabled') === 'true'} :: ${txt.slice(0, 140)}`);
                if (out.length >= 12) break;
            }
            return out;
        }
    """.strip()
    try:
        return list(page.evaluate(script, cr) or [])
    except Exception:
        return []


def _send_button_action(page: Page, composer: Locator | None = None, loose: bool = False, click: bool = False) -> bool:
    cr = _locator_rect(composer)
    script = r"""
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
                const iconText = Array.from(el.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, svg title'))
                    .map(x => x.textContent || '').join(' ');
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.getAttribute('data-testid') || '') + ' ' +
                        (el.getAttribute('data-test-id') || '') + ' ' +
                        (el.innerText || '') + ' ' + iconText).replace(/\s+/g, ' ').trim().toLowerCase();
            }
            function inForbiddenArea(el) {
                return !!el.closest('nav, [role="navigation"], [aria-label*="Recent"], [aria-label*="Conversation history"], .conversation-list, .history');
            }

            const badWords = [
                'add files', 'attach', 'attachment', 'upload', 'insert', 'image', 'photo',
                'microphone', 'mic', 'voice', 'tools', 'tool', 'model', 'flash', 'fast', 'pro',
                'close', 'cancel', 'menu', 'more options', 'share', 'new chat', 'temporary chat',
                'settings', 'apps', 'account', 'help', 'delete', 'rename', 'archive'
            ];
            const sendWords = ['send', 'submit', 'arrow_upward', 'arrow_forward', 'paper_plane', 'send_message'];
            const nodes = Array.from(document.querySelectorAll('main button, main [role="button"], button[aria-label*="Send"], button[aria-label*="Submit"]'));
            const candidates = [];
            const seen = new Set();

            for (const el of nodes) {
                if (!el || seen.has(el)) continue;
                seen.add(el);
                if (!visible(el) || disabled(el) || inForbiddenArea(el)) continue;
                const t = textOf(el);
                const r = el.getBoundingClientRect();
                const named = sendWords.some(w => t.includes(w)) || ((el.getAttribute('type') || '').toLowerCase() === 'submit');
                if (badWords.some(w => t.includes(w)) && !named) continue;

                let score = 0;
                if (t.includes('send message')) score += 260;
                if (t.includes('send')) score += 230;
                if (t.includes('submit')) score += 220;
                if (t.includes('arrow_upward') || t.includes('send_message') || t.includes('paper_plane')) score += 180;
                if ((el.getAttribute('type') || '').toLowerCase() === 'submit') score += 150;

                let nearComposer = false;
                if (cr) {
                    const midY = (r.top + r.bottom) / 2;
                    const compMidY = (cr.top + cr.bottom) / 2;
                    const vertical = r.top < cr.bottom + 220 && r.bottom > cr.top - 140;
                    const rightSide = r.left > cr.left + cr.width * 0.52 || r.right > window.innerWidth * 0.66;
                    nearComposer = vertical && rightSide;
                    if (nearComposer) score += 140;
                    score += Math.max(0, 45 - Math.abs(midY - compMidY) / 6);
                    score += Math.max(0, (r.left - cr.left) / 25);
                } else if (r.top > window.innerHeight * 0.55 && r.left > window.innerWidth * 0.55) {
                    score += 80;
                }

                if (!named && !(loose && nearComposer && score >= 120)) continue;
                if (r.width > 360 || r.height > 120) score -= 120;
                candidates.push({el, score, top: r.top, left: r.left});
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


def find_enabled_send_button(page: Page, composer: Locator | None = None, loose: bool = False) -> bool:
    return _send_button_action(page, composer=composer, loose=loose, click=False)


def wait_until_send_enabled(page: Page, composer: Locator | None = None, timeout: float = 20.0, loose: bool = False) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_enabled_send_button(page, composer=composer, loose=loose):
            return True
        time.sleep(0.35)
    return False


def click_send_button(page: Page, composer: Locator | None = None, loose: bool = False) -> bool:
    try:
        composer = composer or find_composer(page, timeout=3)
    except Exception:
        composer = None
    if not wait_until_send_enabled(page, composer=composer, timeout=12, loose=loose):
        diag = _send_button_diagnostics(page, composer)
        if diag:
            print("  [send] Visible composer-area buttons when Send was not found:")
            for row in diag:
                print(f"  [send]   {row}")
        return False
    return _send_button_action(page, composer=composer, loose=loose, click=True)

def _submission_activity_report(page: Page) -> dict[str, Any]:
    """Return visible signs that Gemini accepted the prompt.

    Gemini often clears the composer and shows a Stop response button / Show
    thinking panel before the generated image appears. The previous script only
    trusted a narrow progress detector, so it sometimes kept clicking Send even
    after the first Enter had already submitted the prompt.
    """
    script = r"""
        () => {
            const root = document.querySelector('main') || document.body;
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            function textOf(el) {
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.innerText || '') + ' ' +
                        (el.textContent || '')).replace(/\s+/g, ' ').trim().toLowerCase();
            }
            const bodyText = (root.innerText || document.body.innerText || '').replace(/\s+/g, ' ').toLowerCase();
            const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
            const buttonText = buttons.map(textOf).join(' | ');
            const stopVisible = buttonText.includes('stop response') || buttonText.includes('cancel generation') || buttonText.includes('stop generating');
            const thinkingVisible = bodyText.includes('show thinking') || bodyText.includes('thinking') || bodyText.includes('creating image') || bodyText.includes('generating');
            let progressVisible = false;
            for (const el of document.querySelectorAll('[role="progressbar"], mat-progress-spinner, mat-progress-bar, .spinner, .loading, .progress')) {
                if (visible(el)) { progressVisible = true; break; }
            }
            const assistantCount = Array.from(document.querySelectorAll('model-response, .model-response, .response-container, [data-response-index], [data-chunk-index], [data-testid*="response"], [class*="response"]')).filter(visible).length;
            const path = window.location.pathname || '';
            const conversationUrl = /^\/app\//.test(path);
            return {stopVisible, thinkingVisible, progressVisible, assistantCount, conversationUrl, path};
        }
    """.strip()
    try:
        report = page.evaluate(script) or {}
        if not isinstance(report, dict):
            return {}
        return report
    except Exception:
        return {}


def generation_in_progress(page: Page) -> bool:
    report = _submission_activity_report(page)
    return bool(
        report.get("stopVisible")
        or report.get("thinkingVisible")
        or report.get("progressVisible")
    )

def _submission_needles(expected_prompt: str | None) -> list[str]:
    """Return stable prompt snippets used to confirm the prompt left composer."""
    compact = _compact_prompt_compare(expected_prompt or "")
    if not compact:
        return []
    chunks = _sample_chunks(compact, chunk_size=80)
    # Avoid excessive DOM walking while still checking start/middle/end text.
    return [c.lower() for c in chunks[:5] if len(c.strip()) >= 20]

def prompt_visible_outside_composer_count(page: Page, expected_prompt: str | None) -> int:
    needles = _submission_needles(expected_prompt)
    if not needles:
        return 0
    script = r"""
        const needles = arguments[0].map(x => (x || '').replace(/\s+/g, ' ').trim().toLowerCase()).filter(Boolean);
        if (!needles.length) return 0;
        const main = document.querySelector('main') || document.body;
        function visible(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        function skipParent(el) {
            if (!el) return true;
            if (el.closest('rich-textarea, [contenteditable="true"], textarea, input, nav, [role="navigation"], header, button, [role="button"]')) return true;
            return !visible(el);
        }
        let count = 0;
        const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const parent = node.parentElement;
            if (skipParent(parent)) continue;
            const txt = (node.nodeValue || '').replace(/\s+/g, ' ').trim().toLowerCase();
            if (!txt) continue;
            if (needles.some(n => txt.includes(n) || n.includes(txt))) count++;
        }
        return count;
    """.lstrip()
    try:
        return int(page.evaluate(script, needles) or 0)
    except Exception:
        return 0


def wait_for_send_confirmation(
    page: Page,
    expected_prompt: str | None = None,
    before_marker_count: int = 0,
    timeout: float = 35.0,
    allow_progress_marker: bool = True,
    before_url: str | None = None,
) -> bool:
    deadline = time.time() + timeout
    saw_empty_composer = False
    before_path = ""
    try:
        before_path = urllib.parse.urlparse(before_url or "").path.rstrip("/")
    except Exception:
        before_path = ""

    while time.time() < deadline:
        try:
            current_composer = find_composer(page, timeout=1)
            current_text = get_composer_text(page, current_composer).strip()
        except Exception:
            current_text = ""

        report = _submission_activity_report(page)
        try:
            current_path = urllib.parse.urlparse(page.url or "").path.rstrip("/")
        except Exception:
            current_path = ""
        url_changed_to_conversation = bool(current_path.startswith("/app/") and current_path != before_path)

        if allow_progress_marker and generation_in_progress(page):
            print("  [send] Prompt accepted; Gemini shows thinking/progress/Stop response.")
            return True

        # In Gemini, a successful Enter often clears the composer immediately and
        # then shows Show thinking / Stop response. Treat that as success; do not
        # try to click Send again while Gemini is already working.
        if not current_text and (
            report.get("stopVisible")
            or report.get("thinkingVisible")
            or report.get("progressVisible")
            or report.get("assistantCount", 0) > 0
            or url_changed_to_conversation
        ):
            print(
                "  [send] Prompt accepted; composer cleared and Gemini activity is visible "
                f"(stop={report.get('stopVisible')}, thinking={report.get('thinkingVisible')}, "
                f"assistant={report.get('assistantCount')}, path={current_path})."
            )
            return True

        if expected_prompt:
            marker_count = prompt_visible_outside_composer_count(page, expected_prompt)
            if marker_count > before_marker_count:
                print("  [send] Prompt accepted; user prompt is visible in the conversation.")
                return True

        if not current_text and not saw_empty_composer:
            print("  [send] Composer is empty, but waiting for Gemini activity/URL/prompt marker...")
            saw_empty_composer = True
        time.sleep(0.55)
    return False

def press_enter_to_send(page: Page, composer: Locator) -> None:
    composer = find_composer(page, timeout=5)
    focus_composer(page, composer)
    try:
        composer.click()
        page.keyboard.press("Enter")
        return
    except Exception:
        pass


def click_send_and_confirm(
    page: Page,
    composer: Locator,
    expected_prompt: str | None = None,
    min_integrity_ratio: float = 0.98,
    debug_path: Path | None = None,
    settle_wait: float = 5.0,
    submit_method: str = "auto",
    confirm_timeout: float = 35.0,
) -> None:
    if settle_wait > 0:
        print(f"  [send] Waiting {settle_wait:g}s for Gemini composer to settle before final prompt check...")
        time.sleep(settle_wait)

    try:
        composer = find_composer(page, timeout=5)
    except Exception:
        pass

    before_text = get_composer_text(page, composer).strip()
    if expected_prompt is not None:
        report = prompt_integrity_report(expected_prompt, before_text, min_integrity_ratio)
        print(f"  [send] Final prompt check after settle wait: {format_prompt_integrity(report)}")
        if not report["ok"]:
            write_prompt_debug_file(debug_path, expected_prompt, before_text, report, "before-send-after-settle")
            raise PWTimeoutError(
                "Refusing to send because Gemini composer does not contain the complete prompt after the settle wait. "
                + format_prompt_integrity(report)
            )
    elif not before_text:
        raise PWTimeoutError("Cannot send because Gemini composer is empty")

    before_marker_count = prompt_visible_outside_composer_count(page, expected_prompt)
    before_progress_active = generation_in_progress(page)
    try:
        before_url = page.url or ""
    except Exception:
        before_url = ""
    print(f"  [send] Existing submitted-prompt markers before Send: {before_marker_count}")
    if before_progress_active:
        print("  [send] A progress indicator was already active before submit; progress alone will not be accepted as confirmation.")

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
                    last_error = "enabled Send/Submit button was not found/clickable"
                    continue
            elif method == "button_loose":
                if not click_send_button(page, composer=composer, loose=True):
                    last_error = "loose bottom-right composer submit button was not found/clickable"
                    continue
            else:
                continue
        except Exception as exc:
            last_error = str(exc)
            continue

        if wait_for_send_confirmation(
            page,
            expected_prompt=expected_prompt,
            before_marker_count=before_marker_count,
            timeout=confirm_timeout,
            allow_progress_marker=not before_progress_active,
            before_url=before_url,
        ):
            return

        try:
            composer = find_composer(page, timeout=3)
        except Exception:
            pass
        last_error = f"{method} did not produce a real submit/progress marker within {confirm_timeout:g}s"

    raise PWTimeoutError(
        "Gemini did not accept the prompt after button/Enter attempts. "
        + last_error
        + " | The script is stopping this prompt instead of moving on silently."
    )
def pro_model_selected(page: Page) -> bool:
    script = r"""
        const roots = Array.from(document.querySelectorAll('header, main'));
        for (const root of roots) {
            for (const el of root.querySelectorAll('button,[role="button"],.chip,.model-picker-container')) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).toLowerCase();
                if (t.includes('pro') && !t.includes('project')) return true;
            }
        }
        return false;
    """
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def select_pro_model(page: Page) -> bool:
    if pro_model_selected(page):
        return True
    opened = safe_click_labels(page, ["model", "fast", "flash", "gemini"], timeout=4)
    if not opened:
        return pro_model_selected(page)
    time.sleep(0.8)
    safe_click_labels(page, ["gemini 2.5 pro", "2.5 pro", "gemini pro", "pro"], timeout=5)
    time.sleep(1.0)
    dismiss_open_overlays(page)
    return pro_model_selected(page)


def create_image_tool_selected(page: Page) -> bool:
    script = r"""
        const roots = Array.from(document.querySelectorAll('header, main'));
        for (const root of roots) {
            const txt = (root.innerText || '').toLowerCase();
            if (txt.includes('create image') || txt.includes('image generation') || txt.includes('generate image')) return true;
        }
        return false;
    """
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def select_create_image_tool(page: Page) -> bool:
    if create_image_tool_selected(page):
        return True
    opened = safe_click_labels(page, ["tools"], timeout=4)
    if not opened:
        return create_image_tool_selected(page)
    time.sleep(0.8)
    safe_click_labels(page, ["create image", "image generation", "generate image"], timeout=5)
    time.sleep(0.8)
    dismiss_open_overlays(page)
    return create_image_tool_selected(page)


def select_model_and_tool_if_requested(page: Page, args: argparse.Namespace) -> None:
    if args.skip_model_selection:
        print("  [model] Skipping model/tool selection by request.")
        return
    pro_ok = select_pro_model(page)
    print(f"  [model] Pro selected/confirmed: {pro_ok}")
    if args.require_pro_model and not pro_ok:
        raise PWTimeoutError("Could not confirm Pro model selection. Use --no-require-pro-model to continue anyway.")
    tool_ok = select_create_image_tool(page)
    print(f"  [tool] Create image selected/confirmed: {tool_ok}")
    if args.require_create_image_tool and not tool_ok:
        raise PWTimeoutError("Could not confirm Create image tool. Use --no-require-create-image-tool to continue anyway.")


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------


def _find_file_input_anywhere(page: Page) -> Locator | None:
    """Find an existing file input. Hidden inputs are OK for set_input_files()."""
    selectors = [
        "input[type='file'][multiple]",
        "input[type='file'][accept*='image']",
        "input[type='file']",
    ]
    for frame in page.frames:
        for selector in selectors:
            try:
                loc = frame.locator(selector).last
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
    return None


def _find_file_input_across_frames(page: Page) -> Locator | None:
    return _find_file_input_anywhere(page)


def click_attach_button_near_composer(page: Page) -> bool:
    """Click the visible attachment/add-files control closest to the composer."""
    script = r"""
    (() => {
        const main = document.querySelector('main') || document.body;
        const composer = document.querySelector(
            'rich-textarea div[contenteditable="true"], div[contenteditable="true"][role="textbox"], main div[contenteditable="true"], textarea[aria-label*="message"], textarea'
        );
        const composerRect = composer ? composer.getBoundingClientRect() : null;
        const bad = ['share','settings','help','account','new chat','temporary','history','recent'];
        const good = ['add files','attach','upload','insert','add image','upload image','photo','image','+'];
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        const candidates = [];
        for (const el of Array.from(main.querySelectorAll('button,[role="button"],input[type="file"] + button'))) {
            if (!visible(el)) continue;
            if (el.closest('nav,[role="navigation"],[aria-label*="Recent"],header')) continue;
            const r = el.getBoundingClientRect();
            const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '') + ' ' + (el.textContent || '')).replace(/\s+/g, ' ').trim().toLowerCase();
            if (bad.some(w => label.includes(w))) continue;
            const looksLikePlus = label === '+' || label.includes('add') || label.includes('attach') || label.includes('upload') || label.includes('image') || label.includes('file');
            if (!looksLikePlus && !good.some(w => label.includes(w))) continue;
            let score = 0;
            if (label.includes('add files')) score += 80;
            if (label.includes('attach')) score += 70;
            if (label.includes('upload')) score += 60;
            if (label.includes('image')) score += 40;
            if (label === '+') score += 35;
            if (composerRect) {
                const cy = r.top + r.height / 2;
                const compY = composerRect.top + composerRect.height / 2;
                const dist = Math.abs(cy - compY);
                if (dist < 260) score += Math.max(0, 120 - dist / 2);
                if (r.left < composerRect.left + composerRect.width * 0.35) score += 20;
            } else if (r.top > window.innerHeight * 0.45) {
                score += 25;
            }
            candidates.push({el, score, top: r.top, left: r.left, label});
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (b.score - a.score) || (b.top - a.top) || (a.left - b.left));
        candidates[0].el.scrollIntoView({block: 'center', inline: 'center'});
        candidates[0].el.click();
        return true;
    })()
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception as exc:
        print(f"  [upload] Attach button JS click failed: {exc}")
        return False


def _click_upload_files_menu_item(page: Page) -> bool:
    """Click the Upload files item inside Gemini's opened attach menu."""
    script = r"""
    (() => {
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        const selectors = [
            '[role="menuitem"]', '[role="option"]', 'button', 'a',
            '.cdk-overlay-container *', '[class*="menu"] *', '[class*="overlay"] *'
        ];
        const seen = new Set();
        const candidates = [];
        for (const selector of selectors) {
            for (const el of Array.from(document.querySelectorAll(selector))) {
                if (seen.has(el) || !visible(el)) continue;
                seen.add(el);
                if (el.closest('nav,[role="navigation"],[aria-label*="Recent"],header')) continue;
                const t = ((el.getAttribute('aria-label') || '') + ' ' +
                           (el.getAttribute('title') || '') + ' ' +
                           (el.innerText || '') + ' ' +
                           (el.textContent || '')).replace(/\s+/g, ' ').trim().toLowerCase();
                if (!t) continue;
                if (t.includes('upload files') || t === 'upload' || (t.includes('upload') && t.includes('file'))) {
                    const r = el.getBoundingClientRect();
                    candidates.push({el, top: r.top, left: r.left, text: t});
                }
            }
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (b.top - a.top) || (a.left - b.left));
        candidates[0].el.scrollIntoView({block: 'center', inline: 'center'});
        candidates[0].el.click();
        return true;
    })()
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception as exc:
        print(f"  [upload] Upload-files menu click failed: {exc}")
        return False


def _click_attach_menu_button_only(page: Page) -> bool:
    """Click only the composer + / Add files button, never the Upload files menu item."""
    script = r"""
    (() => {
        const main = document.querySelector('main') || document.body;
        const composer = document.querySelector(
            'rich-textarea div[contenteditable="true"], div[contenteditable="true"][role="textbox"], main div[contenteditable="true"], textarea[aria-label*="message"], textarea'
        );
        if (!composer) return false;
        const cr = composer.getBoundingClientRect();
        const nodes = Array.from(main.querySelectorAll('button,[role="button"]'));
        const candidates = [];
        for (const el of nodes) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            if (r.width <= 0 || r.height <= 0 || s.display === 'none' || s.visibility === 'hidden') continue;
            const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '')).replace(/\s+/g, ' ').trim().toLowerCase();
            if (t.includes('upload files') || t.includes('add from drive') || t.includes('photos') || t.includes('import code') || t.includes('notebooklm')) continue;
            const looksLikePlus = t === '+' || t.includes('add files') || t.includes('attach file') || t.includes('attach files');
            if (!looksLikePlus) continue;
            const centerY = r.top + r.height / 2;
            const compCenterY = cr.top + cr.height / 2;
            const nearComposer = Math.abs(centerY - compCenterY) < 220;
            if (!nearComposer) continue;
            candidates.push({el, dist: Math.abs(centerY - compCenterY), left: r.left});
        }
        if (!candidates.length) return false;
        candidates.sort((a, b) => (a.dist - b.dist) || (a.left - b.left));
        candidates[0].el.click();
        return true;
    })()
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def open_attachment_ui(page: Page) -> None:
    """Open the + / attachment menu only.

    Important: do NOT click the "Upload files" menu item here. In an already
    running Chrome/CDP session that opens a native Linux file chooser, which
    Playwright cannot reliably drive. We only need Gemini's hidden
    input[type=file] to exist; the actual file assignment is done via CDP below.
    """
    if _find_file_input_across_frames(page) is not None:
        return
    try:
        _click_attach_menu_button_only(page)
    except Exception:
        pass
    time.sleep(0.5)


def _visible_uploaded_image_count(page: Page, before_srcs: set[str] | None = None) -> int:
    before_srcs = before_srcs or set()
    script = r"""
    (before) => {
        const baseline = new Set(before || []);
        const main = document.querySelector('main') || document.body;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width >= 32 && r.height >= 32 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        function forbidden(el) {
            return !!el.closest('nav,[role="navigation"],header,model-response,.model-response,[data-author="model"],[aria-label*="Recent" i]');
        }
        function srcBad(src) {
            const s = (src || '').toLowerCase();
            if (!s) return true;
            if (s.includes('googlelogo') || s.includes('gstatic.com')) return true;
            if (s.includes('/a-/') || s.includes('avatar') || s.includes('profile')) return true;
            return false;
        }
        let count = 0;
        const seen = new Set();
        for (const img of Array.from(main.querySelectorAll('img'))) {
            const src = img.currentSrc || img.src || '';
            if (seen.has(src)) continue;
            seen.add(src);
            if (baseline.has(src)) continue;
            if (srcBad(src) || forbidden(img) || !visible(img)) continue;
            count += 1;
        }
        return count;
    }
    """.strip()
    try:
        return int(page.evaluate(script, list(before_srcs)) or 0)
    except Exception:
        return 0


def _attachment_spinner_count(page: Page) -> int:
    script = r"""
    (() => {
        const main = document.querySelector('main') || document.body;
        function visible(el) {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        let count = 0;
        const selectors = [
            '[role="progressbar"]',
            'mat-progress-spinner',
            'mat-progress-bar',
            '.spinner',
            '.loading',
            '[class*="spinner" i]',
            '[class*="progress" i]'
        ];
        for (const selector of selectors) {
            for (const el of Array.from(main.querySelectorAll(selector))) {
                if (el.closest('model-response,.model-response,[data-author="model"]')) continue;
                if (visible(el)) count += 1;
            }
        }
        return count;
    })()
    """.strip()
    try:
        return int(page.evaluate(script) or 0)
    except Exception:
        return 0


def _dispatch_file_input_events_via_cdp(session, node_id: int) -> None:
    try:
        resolved = session.send("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return
        session.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        try { this.dispatchEvent(new Event('input', {bubbles: true})); } catch (e) {}
                        try { this.dispatchEvent(new Event('change', {bubbles: true})); } catch (e) {}
                        return true;
                    }
                """,
            },
        )
    except Exception:
        pass


def _describe_input_attrs(session, node_id: int) -> dict[str, str]:
    try:
        node = session.send("DOM.describeNode", {"nodeId": node_id}).get("node", {})
        attrs = node.get("attributes", []) or []
        return dict(zip(attrs[0::2], attrs[1::2]))
    except Exception:
        return {}


def _upload_with_cdp_dom(page: Page, file_paths: list[str]) -> bool:
    """Assign files to an existing Gemini file input through CDP.

    Gemini usually creates the real input only after the + menu/upload control
    is opened. This function never clicks the menu item that opens the native
    Linux file chooser; it only uses inputs that already exist.
    """
    try:
        session = page.context.new_cdp_session(page)
    except Exception as exc:
        print(f"  [upload] Could not create CDP session for direct file upload: {exc}")
        return False

    for attempt in range(1, 4):
        try:
            doc = session.send("DOM.getDocument", {"depth": -1, "pierce": True})
            root_id = doc["root"]["nodeId"]
            node_ids = session.send("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type='file']"}).get("nodeIds", [])
        except Exception as exc:
            print(f"  [upload] CDP input scan failed on attempt {attempt}: {exc}")
            node_ids = []

        if not node_ids:
            print(f"  [upload] No existing file input found for CDP direct upload (attempt {attempt}).")
            open_attachment_ui(page)
            time.sleep(0.6)
            continue

        print(f"  [upload] Found {len(node_ids)} file input(s); setting files directly via CDP.")
        for node_id in reversed(node_ids):
            attrs = _describe_input_attrs(session, int(node_id))
            if "disabled" in attrs:
                continue
            try:
                session.send("DOM.setFileInputFiles", {"nodeId": int(node_id), "files": file_paths})
                _dispatch_file_input_events_via_cdp(session, int(node_id))
                print(f"  [upload] CDP setFileInputFiles accepted {len(file_paths)} file(s).")
                return True
            except Exception as exc:
                print(f"  [upload] CDP setFileInputFiles failed for one input: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
                continue

        time.sleep(0.6)

    return False


def _upload_with_playwright_input(page: Page, file_paths: list[str]) -> bool:
    """Assign files to any existing file input using Playwright, without opening OS dialog."""
    selectors = ["input[type='file'][multiple]", "input[type='file']"]
    for selector in selectors:
        try:
            loc = page.locator(selector).last
            loc.wait_for(state="attached", timeout=1500)
            loc.set_input_files(file_paths, timeout=0)
            print(f"  [upload] Playwright set_input_files accepted {len(file_paths)} file(s).")
            return True
        except Exception as exc:
            print(f"  [upload] Playwright input fallback failed for {selector}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            continue
    return False


def _find_file_input_anywhere(page: Page) -> Locator | None:
    """Find a visible or hidden file input element anywhere in the page."""
    try:
        loc = page.locator("input[type='file']").first
        if loc and loc.count() > 0:
            return loc
    except Exception:
        pass
    return None


def _upload_with_playwright_input(page: Page, file_path: str) -> bool:
    """Upload a file via Playwright's set_input_files on the hidden file input.
    Works on all platforms — no xdotool needed."""
    try:
        loc = _find_file_input_anywhere(page)
        if loc is None:
            print("  [upload] No file input found for playwright input upload")
            return False
        loc.set_input_files(file_path, timeout=0)
        print("  [upload] set_input_files accepted the file.")
        return True
    except Exception as exc:
        print(f"  [upload] set_input_files failed: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        return False


def _upload_with_cdp_dom(page: Page, file_path: str) -> bool:
    """Upload a file via CDP DOM.setFileInputFiles. Works on all platforms."""
    try:
        session = page.context.new_cdp_session(page)
        doc = session.send("DOM.getDocument", {"depth": -1, "pierce": True})
        root_id = doc["root"]["nodeId"]
        node_ids = session.send("DOM.querySelectorAll", {"nodeId": root_id, "selector": "input[type='file']"}).get("nodeIds", [])
        if not node_ids:
            print("  [upload] No file input nodes found via CDP")
            return False
        for node_id in reversed(node_ids):
            try:
                session.send("DOM.setFileInputFiles", {"nodeId": int(node_id), "files": [file_path]})
                print("  [upload] CDP setFileInputFiles accepted the file.")
                return True
            except Exception:
                continue
    except Exception as exc:
        print(f"  [upload] CDP direct upload failed: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
    return False





def _open_upload_file_chooser(page: Page, timeout_ms: int = 7000):
    """Open Gemini's Upload files chooser.

    Returns a Playwright FileChooser when the event is captured, returns None
    when a native OS dialog likely opened, and raises only when no upload menu
    path could be clicked.
    """
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    time.sleep(0.15)

    opened_menu = _click_attach_menu_button_only(page) or click_attach_button_near_composer(page)
    if not opened_menu:
        raise PWTimeoutError("Could not open Gemini attachment menu")
    time.sleep(0.25)

    try:
        with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
            clicked = _click_upload_files_menu_item(page)
            if not clicked:
                # Fallback locator click for the same visible menu row.
                page.get_by_text("Upload files", exact=True).click(timeout=2500)
        return chooser_info.value
    except PWTimeoutError:
        # In CDP-attached Chrome, the native OS dialog can open while
        # Playwright misses the filechooser event. Return None so the caller
        # can fall back to set_input_files / CDP DOM file input.
        print("  [upload] Playwright did not catch the filechooser event; trying fallback upload.")
        return None


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
        state = (images_added, active, spinners)
        if state != last_state:
            stable_since = time.time()
            last_state = state
        if images_added >= target_count and not active and spinners == 0 and stable_since is not None and time.time() - stable_since >= stable_seconds:
            return
        if time.time() - last_log > 5:
            print(f"  [upload] Waiting for uploaded images... visible={images_added}, target={target_count}, active={active}, spinners={spinners}")
            last_log = time.time()
        time.sleep(0.6)
    images_added, active, spinners = _upload_counts(page, before_srcs)
    raise PWTimeoutError(f"Timed out waiting for uploaded image count {target_count}; visible={images_added}, active={active}, spinners={spinners}")


def _upload_one_file_via_menu(page: Page, file_path: str, before_srcs: set[str], target_count: int) -> None:
    print(f"  [upload] Uploading file {target_count}: {Path(file_path).name}")
    chooser = None
    try:
        chooser = _open_upload_file_chooser(page, timeout_ms=7000)
    except Exception as exc:
        print(f"  [upload] Could not open Upload files chooser: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")

    if chooser is not None:
        try:
            chooser.set_files(file_path, timeout=0)
            print("  [upload] Playwright filechooser accepted file.")
            _wait_for_uploaded_count_at_least(page, before_srcs, target_count=target_count, timeout=120)
            return
        except Exception as exc:
            print(f"  [upload] FileChooser.set_files reported: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")

    # Cross-platform fallbacks: try set_input_files then CDP DOM
    print("  [upload] Trying cross-platform upload fallbacks...")
    if _upload_with_playwright_input(page, file_path):
        _wait_for_uploaded_count_at_least(page, before_srcs, target_count=target_count, timeout=120)
        return
    if _upload_with_cdp_dom(page, file_path):
        _wait_for_uploaded_count_at_least(page, before_srcs, target_count=target_count, timeout=120)
        return
    raise PWTimeoutError(
        "Could not upload file. Playwright missed the native dialog and "
        "fallback upload via set_input_files / CDP DOM also failed. "
        "On Linux, try: sudo apt install xdotool. On macOS, ensure "
        "Chrome has Accessibility permissions in System Settings."
    )


def _upload_one_by_one_via_menu(page: Page, file_paths: list[str], before_srcs: set[str]) -> bool:
    """Upload files one at a time through Gemini's Upload files menu.

    This avoids repeated all-file fallbacks and prevents duplicate batches. It
    also works when Gemini only creates its file input after the menu item is
    selected.
    """
    for idx, file_path in enumerate(file_paths, start=1):
        _upload_one_file_via_menu(page, file_path, before_srcs, target_count=idx)
        if idx < len(file_paths):
            time.sleep(3.0)
    return True


def _upload_counts(page: Page, before_srcs: set[str]) -> tuple[int, bool, int]:
    images_added = _visible_uploaded_image_count(page, before_srcs)
    active = upload_activity_present(page)
    spinners = _attachment_spinner_count(page)
    return images_added, active, spinners


def wait_for_uploads_to_settle(
    page: Page,
    before_srcs: set[str],
    before_chip_count: int = 0,
    expected_count: int | None = None,
    timeout: int = 180,
) -> None:
    expected_count = int(expected_count or 0)
    deadline = time.time() + timeout
    stable_since: float | None = None
    last_state: tuple[int, bool, int] | None = None
    last_log = 0.0

    while time.time() < deadline:
        images_added, active, spinners = _upload_counts(page, before_srcs)
        enough_images = images_added >= expected_count if expected_count else images_added > 0
        state = (images_added, active, spinners)

        if state != last_state:
            stable_since = time.time()
            last_state = state

        # Require all expected thumbnails, no upload spinner/progress, and a short stable period.
        if enough_images and not active and spinners == 0 and stable_since is not None and time.time() - stable_since >= 5.0:
            print(f"  [upload] Upload settled. visible_uploaded_images={images_added}, expected={expected_count}")
            return

        if time.time() - last_log > 6:
            print(
                f"  [upload] Waiting for upload settle... "
                f"visible_uploaded_images={images_added}, expected={expected_count}, "
                f"active={active}, spinners={spinners}"
            )
            last_log = time.time()
        time.sleep(1.0)

    images_added, active, spinners = _upload_counts(page, before_srcs)
    raise PWTimeoutError(
        "Upload did not fully settle before timeout: "
        f"visible_uploaded_images={images_added}, expected={expected_count}, active={active}, spinners={spinners}"
    )


def upload_images(page: Page, image_paths: list[Path], timeout: int = 180) -> None:
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

    # Fast path: use any already-created hidden file input. This does not open
    # the native chooser and uploads the entire batch once.
    uploaded = _upload_with_cdp_dom(page, file_paths)
    if not uploaded:
        uploaded = _upload_with_playwright_input(page, file_paths)

    # Gemini often creates the input only after the visible "Upload files" menu
    # item is clicked. When that is the case, upload one file at a time through
    # the real menu/filechooser path. This prevents the duplicate batch behavior
    # seen in the previous version.
    if not uploaded:
        print("  [upload] No hidden input was available; using Upload files menu one file at a time.")
        try:
            uploaded = _upload_one_by_one_via_menu(page, file_paths, before_srcs)
        except Exception as exc:
            diag = _send_button_diagnostics(page, None)
            if diag:
                print("  [upload] Visible buttons near composer:")
                for row in diag:
                    print(f"  [upload]   {row}")
            raise PWTimeoutError(str(exc))

    if not uploaded:
        raise PWTimeoutError("Could not upload images through Gemini")

    wait_for_uploads_to_settle(
        page,
        before_srcs,
        before_chip_count=0,
        expected_count=len(file_paths),
        timeout=timeout,
    )
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

def get_all_image_srcs(page: Page) -> set[str]:
    script = r"""
        const out = new Set();
        for (const img of document.querySelectorAll('img')) {
            const src = img.currentSrc || img.src || '';
            if (src) out.add(src);
        }
        return Array.from(out);
    """
    try:
        return set(page.evaluate(script) or [])
    except Exception:
        return set()


def upload_activity_present(page: Page) -> bool:
    script = r"""
        (() => {
            const main = document.querySelector('main') || document.body;
            const txt = (main.innerText || '').toLowerCase();
            if (txt.includes('uploading') || txt.includes('attaching') || txt.includes('scanning')) return true;

            const composer = document.querySelector(
                'rich-textarea div[contenteditable="true"], div[contenteditable="true"][role="textbox"], main div[contenteditable="true"], textarea[aria-label*="message"], textarea'
            );
            const cr = composer ? composer.getBoundingClientRect() : null;
            function visible(el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
            }
            function nearComposer(el) {
                if (!cr) return true;
                const r = el.getBoundingClientRect();
                return r.bottom >= cr.top - 220 && r.top <= cr.bottom + 160 &&
                       r.right >= cr.left - 80 && r.left <= cr.right + 80;
            }
            const selectors = [
                '[role="progressbar"]', 'mat-progress-bar', 'mat-progress-spinner',
                '[class*="spinner" i]', '[class*="loading" i]', '[class*="progress" i]',
                'svg[aria-label*="loading" i]', 'svg[aria-label*="upload" i]'
            ];
            for (const selector of selectors) {
                for (const el of Array.from(main.querySelectorAll(selector))) {
                    if (visible(el) && nearComposer(el)) return true;
                }
            }
            for (const el of Array.from(main.querySelectorAll('*'))) {
                if (!visible(el) || !nearComposer(el)) continue;
                const s = getComputedStyle(el);
                const anim = `${s.animationName || ''} ${s.animationDuration || ''}`.toLowerCase();
                const label = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).toLowerCase();
                if ((label.includes('upload') || label.includes('loading')) && anim && !anim.includes('none') && !anim.includes('0s')) return true;
            }
            return false;
        })()
    """
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Generated image detection
# ---------------------------------------------------------------------------


def _image_candidates(page: Page, baseline_srcs: set[str]) -> list[dict[str, Any]]:
    """Return visible generated-image candidates.

    Use an arrow function for Playwright evaluation. Plain JS snippets with
    top-level `return` throw `Illegal return statement` in page.evaluate.
    """
    script = r"""
    (baselineArg) => {
        const baseline = new Set(baselineArg || []);
        function rect(el) { return el.getBoundingClientRect(); }
        function visible(el) {
            if (!el) return false;
            const r = rect(el);
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('rich-textarea, textarea, input, form, nav, [role="navigation"], header, user-query, .user-query, [data-author="user"], [class*="user-query"], [aria-label*="Recent"], [aria-label*="Conversation history"]');
        }
        function inAssistantArea(el) {
            return !!el.closest('model-response, .model-response, .response-container, [data-response-index], [data-chunk-index], [data-testid*="response"], [class*="response"], conversation-turn, [data-test-id*="conversation"], message-content, [id*="model-response"]');
        }
        function srcBadForLargeImage(src) {
            const s = (src || '').toLowerCase();
            if (!s) return true;
            if (s.includes('googlelogo')) return true;
            if (s.includes('/a-/') || s.includes('avatar') || s.includes('profile')) return true;
            return false;
        }
        function looksLikeOutputImage(img, r) {
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            const visibleArea = r.width * r.height;
            const naturalArea = nw * nh;
            if (r.width >= 160 && r.height >= 160) return true;
            if (nw >= 512 && nh >= 512 && r.width >= 90 && r.height >= 90) return true;
            if (visibleArea >= 30000 || naturalArea >= 250000) return true;
            return false;
        }
        const imgs = Array.from(document.querySelectorAll('main img, img'));
        const out = [];
        imgs.forEach((img, idx) => {
            const src = img.currentSrc || img.src || '';
            if (baseline.has(src)) return;
            if (srcBadForLargeImage(src)) return;
            if (!visible(img)) return;
            if (forbidden(img)) return;
            const r = rect(img);
            if (!looksLikeOutputImage(img, r)) return;
            const assistantArea = inAssistantArea(img);
            const inViewport = r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
            const viewportBonus = inViewport ? 500000 : 0;
            const area = r.width * r.height;
            out.push({
                src,
                top: r.top,
                left: r.left,
                width: r.width,
                height: r.height,
                naturalWidth: img.naturalWidth || 0,
                naturalHeight: img.naturalHeight || 0,
                assistantArea,
                domIndex: idx,
                score: (assistantArea ? 10000000 : 0) + viewportBonus + area + Math.max(img.naturalWidth || 0, img.naturalHeight || 0)
            });
        });
        out.sort((a,b) => (b.score - a.score) || (b.top - a.top) || (b.left - a.left));
        return out;
    }
    """.strip()
    try:
        rows = page.evaluate(script, list(baseline_srcs)) or []
        return list(rows)
    except Exception as exc:
        print(f"  [wait-img] image candidate JS failed: {exc}")
        return []


def mark_largest_generated_image(page: Page, src: str | None = None) -> str:
    """Mark the best visible output image with a data attribute and return its src."""
    script = r"""
    (requestedSrc) => {
        requestedSrc = requestedSrc || '';
        const marker = 'gemini-auto-generated-image-candidate';
        for (const old of document.querySelectorAll(`[data-${marker}]`)) old.removeAttribute(`data-${marker}`);
        function visible(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('rich-textarea, textarea, input, form, nav, [role="navigation"], header, user-query, .user-query, [data-author="user"], [class*="user-query"], [aria-label*="Recent"], [aria-label*="Conversation history"]');
        }
        const imgs = Array.from(document.querySelectorAll('main img, img')).filter(img => {
            if (!visible(img) || forbidden(img)) return false;
            const src = img.currentSrc || img.src || '';
            if (requestedSrc && src !== requestedSrc) return false;
            const s = src.toLowerCase();
            if (!s || s.includes('googlelogo') || s.includes('avatar') || s.includes('profile') || s.includes('/a-/')) return false;
            const r = img.getBoundingClientRect();
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            return (r.width >= 120 && r.height >= 120) || (nw >= 512 && nh >= 512);
        });
        if (!imgs.length && requestedSrc) return '';
        imgs.sort((a,b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            const aAssistant = !!a.closest('model-response, .model-response, .response-container, [data-response-index], [data-chunk-index], [data-testid*="response"], [class*="response"], conversation-turn, [data-test-id*="conversation"], message-content, [id*="model-response"]');
            const bAssistant = !!b.closest('model-response, .model-response, .response-container, [data-response-index], [data-chunk-index], [data-testid*="response"], [class*="response"], conversation-turn, [data-test-id*="conversation"], message-content, [id*="model-response"]');
            if (aAssistant !== bAssistant) return bAssistant ? 1 : -1;
            const aInViewport = ar.bottom > 0 && ar.top < window.innerHeight && ar.right > 0 && ar.left < window.innerWidth;
            const bInViewport = br.bottom > 0 && br.top < window.innerHeight && br.right > 0 && br.left < window.innerWidth;
            if (aInViewport !== bInViewport) return bInViewport ? 1 : -1;
            return (br.width * br.height) - (ar.width * ar.height);
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


def response_completed_with_media(page: Page) -> bool:
    script = r"""
    () => {
        function visible(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        const main = document.querySelector('main') || document.body;
        const hasStop = !!Array.from(main.querySelectorAll('button, [role="button"], span, div')).find(el => {
            if (!visible(el)) return false;
            const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')).toLowerCase();
            return t.includes('stop response');
        });
        const bigImgs = Array.from(document.querySelectorAll('main img, img')).filter(img => {
            if (!visible(img)) return false;
            if (img.closest('rich-textarea, textarea, input, form, nav, [role="navigation"], header, user-query, .user-query, [data-author="user"], [class*="user-query"]')) return false;
            const r = img.getBoundingClientRect();
            const src = (img.currentSrc || img.src || '').toLowerCase();
            if (!src || src.includes('googlelogo') || src.includes('avatar') || src.includes('profile') || src.includes('/a-/')) return false;
            return r.width >= 160 && r.height >= 160;
        });
        return !hasStop && bigImgs.length > 0;
    }
    """.strip()
    try:
        return bool(page.evaluate(script))
    except Exception:
        return False


def wait_for_generated_image(page: Page, baseline_srcs: set[str], timeout: int) -> str:
    print("  [wait-img] Waiting for a new generated image in the assistant response...")
    deadline = time.time() + timeout
    next_log = time.time() + 10
    last_seen_count = 0
    while time.time() < deadline:
        candidates = _image_candidates(page, baseline_srcs)
        last_seen_count = len(candidates)
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

        if response_completed_with_media(page):
            marked_src = mark_largest_generated_image(page)
            if marked_src:
                print("  [wait-img] Found generated image using emergency largest-visible-image detection.")
                return marked_src

        if time.time() >= next_log:
            marked_src = mark_largest_generated_image(page)
            if marked_src and not generation_in_progress(page):
                print("  [wait-img] Found generated image using relaxed visible-image detection.")
                return marked_src
            print(f"  [wait-img] Still waiting... candidate count={last_seen_count}")
            next_log = time.time() + 10
        time.sleep(0.5)
    marked_src = mark_largest_generated_image(page)
    if marked_src:
        print("  [wait-img] Timeout reached, but visible image found; proceeding to download it.")
        return marked_src
    raise PWTimeoutError(f"No generated image appeared within {timeout}s")


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


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
    return ext if ext else ".png"


def _save_data_url(data_url: str, out_path_no_ext: Path, min_bytes: int) -> Path | None:
    if not (isinstance(data_url, str) and data_url.startswith("data:image/")):
        return None
    try:
        header, b64data = data_url.split(",", 1)
        ext = infer_ext_from_src(header)
        out_path = out_path_no_ext.with_suffix(ext)
        raw = base64.b64decode(b64data)
        if len(raw) < min_bytes:
            print(f"  [dl] fetch produced only {len(raw)} bytes; rejecting as too small.")
            return None
        out_path.write_bytes(raw)
        print(f"  [dl] Saved via fetch/base64: {out_path} ({out_path.stat().st_size} bytes)")
        return out_path
    except Exception as exc:
        print(f"  [dl] Could not save data URL: {exc}")
        return None



def _save_visible_generated_image_via_dom(page: Page, out_path_no_ext: Path, min_bytes: int) -> Path | None:
    """Save the actual visible/generated image resource, not a screenshot.

    This is a recovery path for Gemini/Chrome cases where the toolbar Download
    button creates a zero-byte placeholder. It fetches the largest visible image
    element's currentSrc/blob from inside the page and writes that image data.
    """
    script = r"""
    () => new Promise(async (done) => {
        function visible(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 80 && r.height > 80 &&
                   s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
        }
        function forbidden(el) {
            return !!el.closest('rich-textarea, textarea, input, form, nav, [role="navigation"], header, user-query, .user-query, [data-author="user"], [class*="user-query"], [aria-label*="Recent"], [aria-label*="Conversation history"]');
        }
        const imgs = Array.from(document.querySelectorAll('main img, img')).filter(img => {
            if (!visible(img) || forbidden(img)) return false;
            const src = img.currentSrc || img.src || '';
            if (!src) return false;
            const s = src.toLowerCase();
            if (s.includes('googlelogo') || s.includes('avatar') || s.includes('profile') || s.includes('/a-/')) return false;
            const r = img.getBoundingClientRect();
            const nw = img.naturalWidth || 0;
            const nh = img.naturalHeight || 0;
            return (r.width * r.height >= 30000) || (nw * nh >= 250000);
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
            // Last non-screenshot recovery: draw the loaded image element at its
            // natural dimensions and export the image pixels. This is not a page
            // screenshot; it uses the actual generated image element.
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
        data_url = result.get("dataUrl") or ""
        saved = _save_data_url(data_url, out_path_no_ext, min_bytes=min_bytes)
        if saved:
            print(
                "  [dl] Saved generated image from DOM resource "
                f"({result.get('method')}, {result.get('naturalWidth')}x{result.get('naturalHeight')}): "
                f"{saved} ({saved.stat().st_size} bytes)"
            )
            return saved
    except Exception as exc:
        print(f"  [dl] DOM image-resource save failed: {exc}")
    return None


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



def _default_chrome_download_dirs(primary: Path) -> list[Path]:
    """Return likely places Chrome may save files during CDP-attached sessions."""
    dirs: list[Path] = []
    for d in [
        primary,
        Path.home() / "Downloads",
        Path.home() / "downloads",
    ]:
        try:
            d = d.expanduser().resolve()
        except Exception:
            continue
        if d not in dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            dirs.append(d)
    return dirs


def snapshot_download_dirs(download_dirs: list[Path]) -> dict[Path, dict[Path, tuple[float, int]]]:
    return {d: snapshot_download_dir(d) for d in download_dirs}


def wait_for_completed_download_any(
    download_dirs: list[Path],
    before_by_dir: dict[Path, dict[Path, tuple[float, int]]],
    started_at: float,
    timeout: int,
    min_bytes: int = 5000,
) -> Path | None:
    """Poll multiple download dirs.

    Chrome sometimes shows a valid 6MB file in its normal Downloads folder while
    Playwright/CDP reports a 0-byte placeholder in the configured folder. This
    searches both locations and returns the newest stable, non-empty image file.
    """
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
                looks_like_image_download = (
                    p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                    or "gemini_generated_image" in p.name.lower()
                    or "generated_image" in p.name.lower()
                )
                if changed and recent and looks_like_image_download and st.st_size >= min_bytes:
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


def wait_for_completed_download(
    download_dir: Path,
    before: dict[Path, tuple[float, int]],
    started_at: float,
    timeout: int,
) -> Path | None:
    deadline = time.time() + timeout
    last_candidate: Path | None = None
    stable_since: float | None = None
    last_size: int | None = None

    while time.time() < deadline:
        active_temp = [p for p in download_dir.iterdir() if p.is_file() and p.suffix.lower() in DOWNLOAD_TEMP_EXTS]
        candidates: list[Path] = []
        for p in download_dir.iterdir():
            if not p.is_file() or p.suffix.lower() in DOWNLOAD_TEMP_EXTS:
                continue
            try:
                st = p.stat()
            except Exception:
                continue
            old = before.get(p)
            changed = old is None or st.st_mtime > old[0] + 0.5 or st.st_size != old[1]
            recent = st.st_mtime >= started_at - 1
            if changed and recent and st.st_size > 0:
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


def _unique_download_target(download_dir: Path, suggested_filename: str, fallback_ext: str = ".png") -> Path:
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", suggested_filename or "")
    safe = safe.strip(" .") or f"gemini-download{fallback_ext}"
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


def _open_marked_image_viewer(page: Page, src: str | None = None) -> bool:
    marked_src = mark_largest_generated_image(page, src) or mark_largest_generated_image(page)
    if not marked_src:
        print("  [dl] Could not mark a generated image for opening.")
        return False

    try:
        candidate = page.locator('[data-gemini-auto-generated-image-candidate="1"]').first
        candidate.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        candidate = None

    # Click the actual center point. This is more reliable than DOM click for
    # Gemini image cards because overlay controls can intercept normal clicks.
    try:
        box = candidate.bounding_box(timeout=5000) if candidate is not None else None
    except Exception:
        box = None
    if box:
        try:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            pass
    else:
        try:
            page.evaluate("""() => {
                const el = document.querySelector('[data-gemini-auto-generated-image-candidate="1"]');
                if (!el) return false;
                el.scrollIntoView({block:'center', inline:'center'});
                el.click();
                return true;
            }""")
        except Exception:
            pass

    # Viewer is open if a Done button appears, or if top toolbar controls appear.
    for _ in range(12):
        try:
            opened = bool(page.evaluate(r"""() => {
                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                }
                const nodes = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
                for (const el of nodes) {
                    const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '') + ' ' + (el.innerText || '')).toLowerCase().trim();
                    if (t === 'done' || t.includes('done')) return true;
                }
                // Full-screen viewer usually has several top toolbar buttons.
                const topButtons = nodes.filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.top >= 0 && r.top < 170 && r.width >= 16 && r.height >= 16;
                });
                return topButtons.length >= 4;
            }"""))
            if opened:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return True  # Even if not detected, try hover/download path.


def _download_control_available(page: Page) -> bool:
    try:
        return bool(page.evaluate(r"""() => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            function textOf(el) {
                const iconText = Array.from(el.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, svg title'))
                    .map(x => x.textContent || '').join(' ');
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.getAttribute('data-tooltip') || '') + ' ' +
                        (el.getAttribute('data-testid') || '') + ' ' +
                        (el.getAttribute('data-test-id') || '') + ' ' +
                        (el.innerText || '') + ' ' + iconText).replace(/\s+/g, ' ').trim().toLowerCase();
            }
            const nodes = Array.from(document.querySelectorAll('button, [role="button"], a[href], a[download]')).filter(visible);
            if (nodes.some(el => {
                const t = textOf(el);
                return t === 'download' || t.includes('download image') || t.includes('download');
            })) return true;

            // Geometry fallback for Gemini image viewer: if Done exists, the
            // Download icon is typically the 3rd small top toolbar button left of Done.
            const done = nodes.find(el => {
                const t = textOf(el);
                return t === 'done' || t.includes('done');
            });
            if (done) {
                const dr = done.getBoundingClientRect();
                const leftControls = nodes.filter(el => {
                    if (el === done) return false;
                    const r = el.getBoundingClientRect();
                    return r.top >= dr.top - 45 && r.bottom <= dr.bottom + 45 &&
                           r.right < dr.left && r.width >= 16 && r.width <= 80 &&
                           r.height >= 16 && r.height <= 80;
                }).sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
                if (leftControls.length >= 3) return true;
            }
            return false;
        }"""))
    except Exception:
        return False


def _click_download_control_js(page: Page) -> str:
    try:
        return str(page.evaluate(r"""() => {
            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            }
            function textOf(el) {
                const iconText = Array.from(el.querySelectorAll('mat-icon, .material-icons, .material-symbols-outlined, svg title'))
                    .map(x => x.textContent || '').join(' ');
                return ((el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.getAttribute('data-tooltip') || '') + ' ' +
                        (el.getAttribute('data-testid') || '') + ' ' +
                        (el.getAttribute('data-test-id') || '') + ' ' +
                        (el.innerText || '') + ' ' + iconText).replace(/\s+/g, ' ').trim().toLowerCase();
            }
            const nodes = Array.from(document.querySelectorAll('button, [role="button"], a[href], a[download]')).filter(visible);

            // Label/icon-text path.
            const labelled = [];
            for (const el of nodes) {
                const t = textOf(el);
                let score = 0;
                if (t === 'download') score += 500;
                if (t.includes('download image')) score += 450;
                if (t.includes('download')) score += 400;
                if (t.includes('file_download') || t.includes('download_for_offline') || t.includes('arrow_downward')) score += 300;
                const r = el.getBoundingClientRect();
                if (r.top < 180) score += 50;
                if (score > 0) labelled.push({el, score});
            }
            labelled.sort((a,b) => b.score - a.score);
            if (labelled.length) {
                labelled[0].el.click();
                return 'labelled-download';
            }

            // Geometry fallback for Gemini full image viewer. In the viewer,
            // the toolbar is top-right: Share, Copy, Download, Undo, Redo, Done.
            // Therefore Download is usually the third small button to the left
            // of Done when scanning from right to left.
            const done = nodes.find(el => {
                const t = textOf(el);
                return t === 'done' || t.includes('done');
            });
            if (done) {
                const dr = done.getBoundingClientRect();
                const leftControls = nodes.filter(el => {
                    if (el === done) return false;
                    const r = el.getBoundingClientRect();
                    return r.top >= dr.top - 45 && r.bottom <= dr.bottom + 45 &&
                           r.right < dr.left && r.width >= 16 && r.width <= 80 &&
                           r.height >= 16 && r.height <= 80;
                }).sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
                if (leftControls.length >= 3) {
                    leftControls[2].click();
                    return 'viewer-third-left-of-done';
                }
            }

            return '';
        }""") or "")
    except Exception as exc:
        print(f"  [dl] Download button click JS failed: {exc}")
        return ""


def _capture_download_from_click(page: Page, download_dir: Path, src: str, click_timeout: int = 20) -> Path | None:
    if not _download_control_available(page):
        return None

    download_dirs = _default_chrome_download_dirs(download_dir)
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
        if size >= 5000:
            return saved

        # This is the Chrome/CDP mismatch the screenshots show: Chrome's toolbar
        # history may contain the real 6MB file, but Playwright exposes a 0-byte
        # placeholder. Do not fail yet; poll Chrome's real Downloads folder below.
        print("  [dl] Playwright file is zero/tiny; checking Chrome's real Downloads folder.")
        try:
            saved.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as exc:
        if method:
            print(f"  [dl] Clicked download control ({method}) but Playwright did not capture download: {exc}")
        else:
            print(f"  [dl] Could not click/capture download control: {exc}")

    downloaded = wait_for_completed_download_any(
        download_dirs=download_dirs,
        before_by_dir=before_by_dir,
        started_at=started_at,
        timeout=min(60, click_timeout + 30),
        min_bytes=5000,
    )
    if downloaded:
        print(f"  [dl] Valid Chrome download found: {downloaded} ({downloaded.stat().st_size} bytes)")
        return downloaded

    return None


def click_exact_image_and_download(page: Page, context, src: str, download_dir: Path, timeout: int) -> Path | None:
    _configure_download_dir(context, download_dir)

    # Primary path for this Chrome/CDP setup:
    #   1. open Gemini's full image viewer
    #   2. click Gemini's Download control
    #   3. poll the real download folders for a stable non-empty image file
    #
    # This avoids Playwright's flaky expect_download path, which often reports
    # a 0-byte placeholder before Chrome finishes or before the real file is
    # visible in .browser_downloads / Downloads.
    print("  [dl] Opening generated image viewer...")
    _open_marked_image_viewer(page, src)
    time.sleep(1.0)

    download_dirs = _default_chrome_download_dirs(download_dir)
    before_by_dir = snapshot_download_dirs(download_dirs)
    started_at = time.time()

    method = _click_download_control_js(page)
    if method:
        print(f"  [dl] Clicked Gemini download control: {method}")
        downloaded = wait_for_completed_download_any(
            download_dirs=download_dirs,
            before_by_dir=before_by_dir,
            started_at=started_at,
            timeout=min(75, max(30, timeout)),
            min_bytes=5000,
        )
        if downloaded:
            print(f"  [dl] Valid Chrome download found: {downloaded} ({downloaded.stat().st_size} bytes)")
            try:
                page.locator('button:has-text("Done"), [role="button"]:has-text("Done")').first.click(timeout=1500)
            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            return downloaded
        print("  [dl] Clicked Download, but no valid non-empty Chrome file appeared yet.")
    else:
        print("  [dl] Download control not found in viewer.")

    # Fallback A: close viewer, hover the generated image card, click any
    # hover/card Download control, and again poll real folders directly.
    try:
        page.locator('button:has-text("Done"), [role="button"]:has-text("Done")').first.click(timeout=1500)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    time.sleep(0.8)

    mark_largest_generated_image(page, src) or mark_largest_generated_image(page)
    try:
        candidate = page.locator('[data-gemini-auto-generated-image-candidate="1"]').first
        candidate.hover(timeout=5000)
        time.sleep(1.0)

        before_by_dir = snapshot_download_dirs(download_dirs)
        started_at = time.time()
        method = _click_download_control_js(page)
        if method:
            print(f"  [dl] Clicked hover/card download control: {method}")
            downloaded = wait_for_completed_download_any(
                download_dirs=download_dirs,
                before_by_dir=before_by_dir,
                started_at=started_at,
                timeout=min(75, max(30, timeout)),
                min_bytes=5000,
            )
            if downloaded:
                print(f"  [dl] Valid Chrome download found: {downloaded} ({downloaded.stat().st_size} bytes)")
                return downloaded
            print("  [dl] Hover/card Download clicked, but no valid non-empty Chrome file appeared.")
    except Exception as exc:
        print(f"  [dl] Hover download path failed: {exc}")

    # Fallback B: keep the old Playwright download-event path as a last resort.
    # It is slower/flakier in this environment, so it is no longer first.
    try:
        print("  [dl] Fallback: trying Playwright download-event capture.")
        _open_marked_image_viewer(page, src)
        time.sleep(1.0)
        downloaded = _capture_download_from_click(page, download_dir, src, click_timeout=min(20, max(8, timeout)))
        if downloaded:
            return downloaded
    except Exception as exc:
        print(f"  [dl] Playwright download-event fallback failed: {exc}")

    return None


def screenshot_exact_image(page: Page, src: str, out_path_no_ext: Path, min_bytes: int) -> Path | None:
    try:
        marked_src = mark_largest_generated_image(page, src) or mark_largest_generated_image(page)
        if not marked_src:
            return None
        locator = page.locator('[data-gemini-auto-generated-image-candidate="1"]').first
        out_path = out_path_no_ext.with_suffix(".png")
        locator.screenshot(path=str(out_path))
        if out_path.exists() and out_path.stat().st_size >= min_bytes:
            print(f"  [dl] Saved exact image screenshot: {out_path} ({out_path.stat().st_size} bytes)")
            return out_path
        if out_path.exists():
            print(f"  [dl] Screenshot was too small ({out_path.stat().st_size} bytes); rejecting.")
    except Exception as exc:
        print(f"  [dl] Screenshot fallback failed: {exc}")
    return None


def download_generated_image(
    page: Page,
    context,
    src: str,
    out_path_no_ext: Path,
    download_dir: Path,
    min_bytes: int,
    download_timeout: int,
) -> Path:
    out_path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    _configure_download_dir(context, download_dir)

    marked_src = mark_largest_generated_image(page, src) or src
    if marked_src and marked_src != src:
        print("  [dl] Updated generated image src from marked visible image.")
        src = marked_src

    print("  [dl] Strategy 1: click generated image, open viewer, then click Download.")
    try:
        downloaded = click_exact_image_and_download(page, context, src, download_dir, timeout=download_timeout)
        if downloaded and downloaded.exists() and downloaded.stat().st_size >= min_bytes:
            ext = downloaded.suffix if downloaded.suffix else ".png"
            out_path = out_path_no_ext.with_suffix(ext)
            shutil.copy2(downloaded, out_path)
            print(f"  [dl] Saved browser download: {out_path} ({out_path.stat().st_size} bytes)")
            return out_path
        if downloaded:
            print(f"  [dl] Browser download too small/corrupt: {downloaded} ({downloaded.stat().st_size} bytes)")
    except Exception as exc:
        print(f"  [dl] viewer/button strategy failed: {exc}")

    # Re-mark the actual viewer image after opening. Gemini sometimes gives the
    # prompt card a small blob src, while the viewer contains the real image src.
    src = mark_largest_generated_image(page) or src

    print(f"  [dl] Strategy 2: fetch exact current image src: {src[:100]}...")
    try:
        data_url = page.evaluate(
            """src => new Promise((done) => {
                fetch(src, {credentials: 'include'})
                    .then(r => r.blob())
                    .then(blob => {
                        const reader = new FileReader();
                        reader.onloadend = () => done(reader.result);
                        reader.onerror = () => done(null);
                        reader.readAsDataURL(blob);
                    })
                    .catch(() => done(null));
            })""",
            src,
        )
        saved = _save_data_url(data_url, out_path_no_ext, min_bytes=min_bytes)
        if saved:
            return saved
    except Exception as exc:
        print(f"  [dl] fetch strategy failed: {exc}")

    print("  [dl] Strategy 3: save actual visible generated image resource from DOM.")
    saved = _save_visible_generated_image_via_dom(page, out_path_no_ext, min_bytes=min_bytes)
    if saved:
        return saved

    raise RuntimeError("Full download failed: Gemini image was detected, but no valid image file could be found in Playwright or Chrome Downloads")


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
        page.screenshot(path=str(png))
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


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------


def run() -> None:
    install_job_signal_handlers()
    args = parse_args()
    hypothesis_config: dict[str, Any] = {}
    if args.hypothesis_config:
        hyp_path = Path(args.hypothesis_config).expanduser().resolve()
        if hyp_path.exists():
            try:
                hypothesis_config = json.loads(hyp_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Warning: could not read hypothesis config: {exc}")

    prompt_dir = Path(args.prompt_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    upload_dir = Path(args.upload_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_images_dir = out_dir / 'generated images'
    generated_images_dir.mkdir(parents=True, exist_ok=True)

    # Shared progress log for dashboard polling
    log_path = out_dir / "_headless_progress.json"
    def log_progress(step: str, msg: str) -> None:
        payload = {"step": step, "message": msg, "time": int(time.time())}
        log_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  [headless] {step}: {msg}", flush=True)

    log_progress("init", f"Starting headless={args.headless} headless mode, prompts in {prompt_dir}")

    jobs, duplicates = discover_prompt_jobs(
        prompt_dir=prompt_dir,
        pattern=args.prompt_glob,
        allow_duplicates=args.allow_duplicate_prompt_keys,
        aspect_ratio=args.aspect_ratio,
    )
    print_job_manifest(jobs, duplicates)
    validate_expected_formats(jobs, args.expected_formats, args.strict_expected_formats)
    starting_prompt = load_starting_prompt(args.starting_prompt_file)
    if starting_prompt:
        print(f"\nStarting prompt: {len(starting_prompt)} chars from {args.starting_prompt_file}")
    if args.dry_run:
        print("\nDry run complete. Browser was not started.")
        return

    browser_download_dir = Path(args.browser_download_dir).expanduser().resolve() if args.browser_download_dir else out_dir / ".browser_downloads"
    browser_download_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gemini_uploads_") as tmp:
        temp_dir = Path(tmp)
        if args.upload_manifest:
            upload_paths = parse_upload_manifest(
                Path(args.upload_manifest).expanduser().resolve()
            )
        elif args.image_source_file:
            source_file = Path(args.image_source_file).expanduser().resolve()
            image_sources = parse_image_source_file(source_file, args.logo_key)
            upload_paths = build_local_image_paths(image_sources, temp_dir)
        else:
            upload_paths = collect_upload_images_from_dir(upload_dir)

        print(f"\nUpload images: {len(upload_paths)}")
        for p in upload_paths[:10]:
            print(f"  - {p}")
        if len(upload_paths) > 10:
            print(f"  ... {len(upload_paths) - 10} more")
        if len(upload_paths) > 20:
            print("WARNING: Gemini may reject very large attachment batches.")

        log_progress("browser_launch", f"Launching browser, headless={args.headless}")
        pw, context = build_browser_context(args, download_dir=browser_download_dir)
        job_pages: list = []
        results: list[dict[str, Any]] = []
        try:
            from local_agent_runtime.browser import open_cdp_page
            page = remember_job_page(job_pages, open_cdp_page(context, new_window=True))

            log_progress("browser_ready", f"Browser launched, headless={args.headless}")
            print(f"\nBrowser launched in {'HEADLESS' if args.headless else 'VISIBLE'} mode.")

            strict_login = args.login_wait_mode == "strict"
            for idx, job in enumerate(jobs, start=1):
                print("\n" + "=" * 72)
                print(f"[{idx}/{len(jobs)}] {job.job_key}: {job.prompt_path.name}")
                print("=" * 72)
                log_progress("job_start", f"Starting job {idx}/{len(jobs)}: {job.job_key}")
                prompt_body = job.prompt_path.read_text(encoding="utf-8")
                prompt_metadata = load_prompt_metadata(job.prompt_path, prompt_body)
                prompt_hypothesis = prompt_metadata.get("hypothesis") if isinstance(prompt_metadata.get("hypothesis"), dict) else {}
                effective_hypothesis = prompt_hypothesis if prompt_hypothesis else hypothesis_config
                test_variables = build_test_variables(job, prompt_metadata, effective_hypothesis)
                prompt_text = prepend_starting_prompt(starting_prompt, prompt_body)
                out_base = generated_images_dir / job.output_stem
                if prompt_text == prompt_body:
                    print(f"  Prompt file stats: {len(prompt_text)} chars, {prompt_text.count(chr(10)) + 1} lines")
                else:
                    print(
                        "  Prompt file stats: "
                        f"{len(prompt_body)} body chars + {len(starting_prompt)} starter chars "
                        f"= {len(prompt_text)} sent chars"
                    )

                success = False
                last_exc: Exception | None = None
                attempts = max(1, int(args.max_attempts))
                for attempt in range(1, attempts + 1):
                    if attempts > 1:
                        print(f"  Attempt {attempt}/{attempts}")
                    try:
                        log_progress("opening_tab", f"Job {idx}: opening tab, attempt {attempt}")
                        page = remember_job_page(
                            job_pages,
                            open_prompt_tab(context, page, idx if attempt == 1 else 999999, args.first_tab_mode, browser_download_dir),
                        )
                        log_progress("navigating", f"Job {idx}: navigating to Gemini")
                        navigate_to_fresh_chat(
                            page,
                            manual_login_timeout=args.manual_login_timeout,
                            strict_login=strict_login,
                        )
                        _configure_download_dir(context, browser_download_dir)

                        print("  Uploading reference images...")
                        print("  [DEBUG] About to call upload_images")
                        log_progress("uploading", f"Job {idx}: uploading images")
                        upload_images(page, upload_paths)
                        dismiss_open_overlays(page)
                        time.sleep(1.0)

                        print("  Typing prompt with strict integrity check...")
                        log_progress("typing_prompt", f"Job {idx}: typing prompt ({len(prompt_text)} chars)")
                        composer = set_prompt_text(
                            page,
                            prompt_text,
                            method=args.prompt_paste_method,
                            verify_timeout=args.prompt_paste_timeout,
                            min_integrity_ratio=args.prompt_integrity_ratio,
                            debug_path=out_base.with_suffix(".prompt-paste-debug.txt"),
                        )
                        time.sleep(0.5)

                        select_model_and_tool_if_requested(page, args)

                        baseline_srcs = get_all_image_srcs(page)
                        print(f"  Baseline image src count before Send: {len(baseline_srcs)}")

                        print("  Sending prompt...")
                        log_progress("sending", f"Job {idx}: sending prompt")
                        click_send_and_confirm(
                            page,
                            composer,
                            expected_prompt=prompt_text,
                            min_integrity_ratio=args.prompt_integrity_ratio,
                            debug_path=out_base.with_suffix(".prompt-before-send-debug.txt"),
                            settle_wait=args.prompt_settle_wait,
                            submit_method=args.send_submit_method,
                            confirm_timeout=args.send_confirm_timeout,
                        )

                        log_progress("waiting_image", f"Job {idx}: waiting for generated image (timeout={args.timeout}s)")
                        image_src = wait_for_generated_image(page, baseline_srcs, timeout=args.timeout)

                        log_progress("downloading", f"Job {idx}: downloading image")
                        saved_path = download_generated_image(
                            page,
                            context,
                            image_src,
                            out_base,
                            download_dir=browser_download_dir,
                            min_bytes=args.min_image_bytes,
                            download_timeout=args.download_timeout,
                        )
                        if not saved_path.exists() or saved_path.stat().st_size < args.min_image_bytes:
                            raise RuntimeError(f"Saved file is missing or too small: {saved_path}")

                        metadata = build_image_metadata(
                            status="success",
                            job=job,
                            prompt_metadata=prompt_metadata,
                            effective_hypothesis=effective_hypothesis,
                            test_variables=test_variables,
                            saved_path=saved_path,
                        )
                        out_base.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                        log_progress("done", f"Job {idx} SUCCESS: {saved_path} ({saved_path.stat().st_size} bytes)")
                        print(f"  SUCCESS: saved {saved_path} ({saved_path.stat().st_size} bytes)")
                        results.append({"job": job.job_key, "status": "success", "file": str(saved_path)})
                        if args.result_manifest:
                            result_path = Path(args.result_manifest).expanduser().resolve()
                            result_path.parent.mkdir(parents=True, exist_ok=True)
                            result_path.write_text(
                                json.dumps({"output_path": str(saved_path.resolve())}) + "\n",
                                encoding="utf-8",
                            )
                        if args.sleep_after_download > 0:
                            print(f"  Waiting {args.sleep_after_download:g}s before next prompt tab...")
                            time.sleep(args.sleep_after_download)
                        success = True
                        print("  [loop] Finished this prompt. Returning to job loop now.")
                        break

                    except PWTimeoutError as exc:
                        last_exc = exc
                        print(f"  Timeout/pw error: {exc}")
                        log_progress("error", f"Job {idx} attempt {attempt}: {exc}")
                        if attempt < attempts:
                            try:
                                close_cdp_page(page)
                            except Exception:
                                pass
                            try:
                                page = remember_job_page(
                                    job_pages,
                                    open_cdp_page(context, new_window=True),
                                )
                            except Exception:
                                pass
                            continue
                        break
                    except Exception as exc:
                        last_exc = exc
                        print(f"  ERROR: {exc}")
                        log_progress("error", f"Job {idx} attempt {attempt}: {exc}")
                        if attempt < attempts:
                            continue
                        break

                if success:
                    if idx < len(jobs):
                        print("  [next] Moving to next prompt; a new tab will be opened and this tab will stay open.")
                    continue

                diag = save_debug_snapshot(page, out_base, "error")
                metadata = build_image_metadata(
                    status="error",
                    job=job,
                    prompt_metadata=prompt_metadata,
                    effective_hypothesis=effective_hypothesis,
                    test_variables=test_variables,
                    error=str(last_exc),
                    diag=diag,
                )
                out_base.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                results.append({"job": job.job_key, "status": "error", "error": str(last_exc)})
                log_progress("failed", f"Job {idx} FAILED: {last_exc}")
                if args.continue_on_error:
                    print(f"  FAILED: {job.job_key}. Continuing to next prompt because --continue-on-error is set.")
                    continue
                print(f"  FAILED: {job.job_key}. Stopping run so this prompt is not silently skipped. Use --continue-on-error to keep going.")
                break

            # Final summary
            log_progress("complete", f"All done. {sum(1 for r in results if r['status'] == 'success')}/{len(results)} succeeded.")
            print("\nAll prompt jobs finished.")
        finally:
            release_browser(pw, context, job_pages)
            print("Browser detached.")


if __name__ == "__main__":
    run()
