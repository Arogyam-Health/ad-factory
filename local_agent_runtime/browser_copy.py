from __future__ import annotations

import os
import threading
import time
from typing import Any

from playwright.sync_api import sync_playwright


_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_SESSION_PREFIX = "bcs_"
_TURN_TIMEOUT = int(os.getenv("BROWSER_COPY_TURN_TIMEOUT", "1200"))
_STABLE_SECONDS = float(os.getenv("BROWSER_COPY_STABLE_SECONDS", "4"))
_CHATGPT_EXTRACT_JS = r"""
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
    const turns = Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"]'))
        .filter(visible)
        .filter((el) => !!el.querySelector('[data-message-author-role="assistant"]'));
    if (!turns.length) return '';
    const el = turns[turns.length - 1];
    return (el.innerText || el.textContent || '').trim();
}
"""
_GEMINI_EXTRACT_JS = r"""
() => {
    function visible(el) {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    }
    const nodes = Array.from(document.querySelectorAll(
        'structured-content-container .markdown-main-panel, [id^="model-response-message-content"], .markdown.markdown-main-panel'
    )).filter(visible);
    if (nodes.length) {
        const el = nodes[nodes.length - 1];
        return (el.innerText || el.textContent || '').trim();
    }
    return '';
}
"""


def _engine_module(engine: str) -> Any:
    if engine == "chatgpt":
        from local_agent_runtime import chatgpt_web_sutomation as module
        return module
    from local_agent_runtime import gemini_web_automation as module
    return module


def _cdp_url() -> str:
    return os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")


def _attach_cdp(engine: str) -> dict[str, Any]:
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(_cdp_url())
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        origin = (
            "https://chatgpt.com"
            if engine == "chatgpt"
            else "https://gemini.google.com"
        )
        try:
            context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=origin,
            )
        except Exception:
            pass
        page = context.new_page()
        return {
            "engine": engine,
            "playwright": playwright,
            "context": context,
            "page": page,
        }
    except Exception:
        try:
            playwright.stop()
        except Exception:
            pass
        raise RuntimeError(
            "Chrome CDP is not available. Start Chrome with remote debugging."
        )


def _close_session(session_id: str) -> None:
    with _LOCK:
        session = _SESSIONS.pop(session_id, None)
    if session is None:
        return
    page = session.get("page")
    playwright = session.get("playwright")
    try:
        if page is not None:
            page.close()
    except Exception:
        pass
    try:
        if playwright is not None:
            playwright.stop()
    except Exception:
        pass


def _get_session(session_id: str) -> dict[str, Any] | None:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        return session if isinstance(session, dict) else None


def extract_assistant_text(page: Any, engine: str) -> str:
    script = _CHATGPT_EXTRACT_JS if engine == "chatgpt" else _GEMINI_EXTRACT_JS
    try:
        text = str(page.evaluate(script) or "").strip()
    except Exception:
        return ""
    for prefix in ("ChatGPT said:", "Gemini said:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    return text


def _wait_for_assistant_text(session: dict[str, Any], *, expect_json: bool) -> str:
    engine = str(session["engine"])
    page = session["page"]
    module = _engine_module(engine)
    deadline = time.time() + max(30, _TURN_TIMEOUT)
    last_text = ""
    stable_since: float | None = None
    while time.time() < deadline:
        in_progress = False
        try:
            in_progress = bool(module.generation_in_progress(page))
        except Exception:
            in_progress = False
        if in_progress:
            stable_since = None
            time.sleep(2.0)
            continue
        text = extract_assistant_text(page, engine)
        if text and text == last_text:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= _STABLE_SECONDS:
                if expect_json and "{" not in text:
                    time.sleep(1.0)
                    continue
                return text
        else:
            last_text = text
            stable_since = time.time() if text else None
        time.sleep(1.0)
    raise TimeoutError("Browser copy turn timed out waiting for a reply")


def _send_prompt(session: dict[str, Any], prompt: str) -> None:
    engine = str(session["engine"])
    page = session["page"]
    module = _engine_module(engine)
    composer = module.set_prompt_text(page, prompt)
    if engine == "chatgpt":
        module.click_send_and_confirm(
            page,
            composer,
            prompt,
            0.98,
            None,
            2.0,
            "auto",
            35.0,
            0,
            180.0,
        )
        return
    module.click_send_and_confirm(
        page,
        composer,
        expected_prompt=prompt,
        min_integrity_ratio=0.98,
        debug_path=None,
        settle_wait=2.0,
        submit_method="auto",
        confirm_timeout=35.0,
    )


def _open_session(session_id: str, engine: str) -> dict[str, Any]:
    _close_session(session_id)
    session = _attach_cdp(engine)
    module = _engine_module(engine)
    module.navigate_to_fresh_chat(session["page"], 120, False)
    with _LOCK:
        _SESSIONS[session_id] = session
    return session


def execute_browser_copy(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Browser copy payload is invalid")
    engine = str(payload.get("engine") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    session_id = str(payload.get("session_id") or "").strip()
    prompt = str(payload.get("prompt") or "")
    expect_json = bool(payload.get("expect_json"))
    if engine not in {"chatgpt", "gemini"}:
        raise ValueError("Browser copy engine is invalid")
    if action not in {"new", "continue", "repair", "close"}:
        raise ValueError("Browser copy action is invalid")
    if not session_id.startswith(_SESSION_PREFIX) or len(session_id) > 80:
        raise ValueError("Browser copy session is invalid")
    if action == "close":
        _close_session(session_id)
        return {
            "http_status": 200,
            "content_type": "text/plain",
            "body": "",
            "transport_error": "",
        }
    if action == "new":
        session = _open_session(session_id, engine)
    else:
        session = _get_session(session_id)
        if session is None:
            raise ValueError("Browser copy session is not open")
        if str(session.get("engine") or "") != engine:
            raise ValueError("Browser copy session engine mismatch")
    if not prompt.strip():
        raise ValueError("Browser copy prompt is empty")
    _send_prompt(session, prompt)
    text = _wait_for_assistant_text(session, expect_json=expect_json)
    return {
        "http_status": 200,
        "content_type": "application/json" if expect_json else "text/plain",
        "body": text,
        "transport_error": "",
    }


def handle_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from local_agent_runtime.provider_relay import execute_provider_call

    if str((payload or {}).get("provider") or "") == "browser":
        return execute_browser_copy(payload)
    return execute_provider_call(payload)
