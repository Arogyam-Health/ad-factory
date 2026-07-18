#!/usr/bin/env python3
"""Run the existing ChatGPT web automation with terminal-failure detection.

The upstream automation is intentionally left untouched. This wrapper imports it,
replaces only its generated-image wait routine, and then executes the normal CLI.
A visible ChatGPT image-generation failure is treated as a completed failed job
instead of waiting for the full image timeout.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_base_module() -> ModuleType:
    source = Path(__file__).with_name("chatgpt_web_sutomation.py")
    spec = importlib.util.spec_from_file_location("ad_factory_chatgpt_web_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load ChatGPT automation module: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base_module()

_FAILURE_PATTERNS = (
    "image-generation tool encountered an error",
    "image generation tool encountered an error",
    "unable to generate the image",
    "couldn't generate the image",
    "could not generate the image",
    "failed to generate the image",
    "there was an error generating the image",
    "error while generating the image",
    "generation failed",
)


def _latest_assistant_text(page: Any) -> str:
    script = r"""
    () => {
        function visible(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        }
        const exact = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'))
            .filter(visible);
        if (exact.length) {
            const el = exact[exact.length - 1];
            return (el.innerText || el.textContent || '').trim();
        }
        const turns = Array.from(document.querySelectorAll('article, [data-testid*="conversation-turn"]'))
            .filter(visible)
            .filter(el => {
                const role = (el.getAttribute('data-message-author-role') || '').toLowerCase();
                if (role === 'assistant') return true;
                return !!el.querySelector('[data-message-author-role="assistant"]');
            });
        if (!turns.length) return '';
        const el = turns[turns.length - 1];
        return (el.innerText || el.textContent || '').trim();
    }
    """.strip()
    try:
        return str(page.evaluate(script) or "").strip()
    except Exception:
        return ""


def _terminal_failure(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    low = compact.lower()
    for phrase in _FAILURE_PATTERNS:
        if phrase in low:
            return compact[:800]
    if "something went wrong" in low and ("image" in low or "generat" in low):
        return compact[:800]
    return ""


def wait_for_generated_image(page: Any, baseline_srcs: set[str], timeout: int) -> str:
    print("  [wait-img] Waiting for a new generated image in the assistant response...")
    deadline = time.time() + timeout
    next_log = time.time() + 10
    stable_text = ""
    stable_since: float | None = None
    grace_seconds = max(
        10,
        int(os.getenv("CHATGPT_TERMINAL_RESPONSE_GRACE_SECONDS") or "30"),
    )

    while time.time() < deadline:
        candidates = BASE._image_candidates(page, baseline_srcs)
        if candidates:
            assistant_candidates = [candidate for candidate in candidates if candidate.get("assistantArea")]
            chosen = assistant_candidates[0] if assistant_candidates else candidates[0]
            if BASE.generation_in_progress(page):
                time.sleep(4.0)
                refreshed = BASE._image_candidates(page, baseline_srcs)
                if refreshed:
                    assistant_refreshed = [candidate for candidate in refreshed if candidate.get("assistantArea")]
                    chosen = assistant_refreshed[0] if assistant_refreshed else refreshed[0]
            print(
                "  [wait-img] Found generated image: "
                f"{int(chosen.get('width', 0))}x{int(chosen.get('height', 0))}, "
                f"assistantArea={chosen.get('assistantArea')}."
            )
            return str(chosen.get("src") or "")

        assistant_text = _latest_assistant_text(page)
        failure = _terminal_failure(assistant_text)
        if failure:
            print(f"  [wait-img] ChatGPT reported terminal image-generation failure: {failure}")
            raise RuntimeError(f"ChatGPT image generation failed: {failure}")

        in_progress = BASE.generation_in_progress(page)
        normalized = re.sub(r"\s+", " ", assistant_text).strip()
        if normalized and not in_progress:
            if normalized != stable_text:
                stable_text = normalized
                stable_since = time.time()
            elif stable_since is not None and time.time() - stable_since >= grace_seconds:
                preview = normalized[:800]
                print(
                    "  [wait-img] Assistant response completed without an image; "
                    f"stopping after {grace_seconds}s stable response."
                )
                raise RuntimeError(
                    "ChatGPT completed the response without producing an image: " + preview
                )
        else:
            stable_text = ""
            stable_since = None

        if time.time() >= next_log:
            marked_src = BASE.mark_largest_generated_image(page)
            if marked_src and not in_progress:
                print("  [wait-img] Found generated image using relaxed visible-image detection.")
                return marked_src
            state_label = "generating" if in_progress else "waiting for image"
            print(f"  [wait-img] Still {state_label}...")
            next_log = time.time() + 10
        time.sleep(2.0)

    marked_src = BASE.mark_largest_generated_image(page)
    if marked_src:
        print("  [wait-img] Timeout reached, but visible image found; proceeding to save it.")
        return marked_src
    raise BASE.PWTimeoutError(f"No generated image appeared within {timeout}s")


BASE.wait_for_generated_image = wait_for_generated_image


if __name__ == "__main__":
    BASE.run()
