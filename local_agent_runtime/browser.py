"""Find Chrome/Brave on the current machine without hardcoding a user home path."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path


def _first_env(environ: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = str(environ.get(key) or "").strip()
        if value:
            return value
    return ""


def browser_candidates(
    browser: str = "chrome",
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    env = os.environ if environ is None else environ
    home_dir = Path(home or Path.home()).expanduser()
    browser = (browser or "chrome").lower().strip()
    ordered: list[str] = []

    override = _first_env(env, "CHROME_PATH", "BROWSER_PATH", "AD_FACTORY_CHROME")
    if override:
        ordered.append(override)

    local_appdata = Path(
        env.get("LOCALAPPDATA") or str(home_dir / "AppData" / "Local")
    )
    program_files = Path(env.get("PROGRAMFILES") or r"C:\Program Files")
    program_files_x86 = Path(env.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)")

    if browser == "chrome":
        ordered.extend(
            [
                "google-chrome",
                "google-chrome-stable",
                "chrome",
                "chromium",
                "chromium-browser",
                "chrome.exe",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/snap/bin/chromium",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(home_dir / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                str(local_appdata / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(local_appdata / "Google" / "Chrome SxS" / "Application" / "chrome.exe"),
                str(program_files / "Google" / "Chrome" / "Application" / "chrome.exe"),
                str(program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ]
        )
    else:
        ordered.extend(
            [
                "brave-browser",
                "brave",
                "brave-browser-stable",
                "brave.exe",
                "/usr/bin/brave-browser",
                "/usr/bin/brave",
                "/snap/bin/brave",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(home_dir / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                str(local_appdata / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
                str(program_files / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
                str(program_files_x86 / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"),
            ]
        )

    unique: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def resolve_browser_executable(
    browser: str = "chrome",
    *,
    candidates: list[str] | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    items = candidates if candidates is not None else browser_candidates(
        browser, home=home, environ=environ
    )
    seen: set[str] = set()
    for candidate in items:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        found = which(candidate)
        if found:
            return found
        path = Path(candidate).expanduser()
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return ""


def mark_cdp_attached(context):
    """Mark a Playwright context as attached to the shared CDP Chrome."""
    if context is None:
        return context
    try:
        setattr(context, "_ad_factory_cdp", True)
    except Exception:
        pass
    return context


def is_cdp_attached(context) -> bool:
    return getattr(context, "_ad_factory_cdp", False) is True


def remember_job_page(job_pages: list, page):
    """Track a page this job opened so cleanup can close only those targets."""
    if page is not None and page not in job_pages:
        job_pages.append(page)
    return page


def _job_target_ids(context) -> list[str]:
    if context is None:
        return []
    ids = getattr(context, "_ad_factory_job_target_ids", None)
    if not isinstance(ids, list):
        ids = []
        try:
            setattr(context, "_ad_factory_job_target_ids", ids)
        except Exception:
            return []
    return ids


def _remember_job_target(context, target_id: str) -> None:
    if not target_id or context is None:
        return
    ids = _job_target_ids(context)
    if target_id not in ids:
        ids.append(target_id)


def _forget_job_target(context, target_id: str) -> None:
    if not target_id or context is None:
        return
    ids = _job_target_ids(context)
    try:
        while target_id in ids:
            ids.remove(target_id)
    except Exception:
        pass


def _target_id_from_info(info) -> str:
    if not isinstance(info, dict):
        return ""
    nested = info.get("targetInfo")
    if isinstance(nested, dict) and nested.get("targetId"):
        return str(nested["targetId"])
    return str(info.get("targetId") or "")


def _page_target_id(page) -> str:
    stored = getattr(page, "_ad_factory_target_id", "")
    if isinstance(stored, str) and stored:
        return stored
    context = getattr(page, "context", None)
    if context is None:
        return ""
    try:
        session = context.new_cdp_session(page)
        info = session.send("Target.getTargetInfo")
    except Exception:
        return ""
    return _target_id_from_info(info)


def _page_list(context) -> list:
    if context is None:
        return []
    pages = getattr(context, "pages", None)
    if isinstance(pages, list):
        return pages
    if isinstance(pages, tuple):
        return list(pages)
    return []


def _existing_page(context):
    pages = _page_list(context)
    return pages[0] if pages else None


def _cdp_session(context, page=None):
    """CDP session for this context. Never opens a new Chrome window to get one."""
    if context is None:
        return None
    seed = page if page is not None else _existing_page(context)
    if seed is not None:
        try:
            return context.new_cdp_session(seed)
        except Exception:
            pass
    browser = getattr(context, "browser", None)
    if browser is None:
        return None
    try:
        return browser.new_browser_cdp_session()
    except Exception:
        return None


def _close_target_id(context, target_id: str) -> None:
    if not target_id or context is None:
        return
    try:
        session = _cdp_session(context)
        if session is not None:
            session.send("Target.closeTarget", {"targetId": target_id})
    except Exception:
        pass
    _forget_job_target(context, target_id)


def _wait_for_adopted_page(context, target_id: str, timeout: float):
    import time

    if not target_id or context is None:
        return None
    deadline = time.time() + max(0.0, timeout)
    while True:
        for page in _page_list(context):
            if _page_target_id(page) == target_id:
                return page
        if time.time() >= deadline:
            return None
        time.sleep(0.1)


def close_cdp_page(page) -> None:
    """Close one job-opened tab/window. Never closes the shared Chrome profile."""
    if page is None:
        return
    context = getattr(page, "context", None)
    target_id = _page_target_id(page)
    try:
        page.close()
    except Exception:
        pass
    if target_id:
        _close_target_id(context, target_id)


def close_job_pages(job_pages: list | None) -> None:
    for page in list(job_pages or []):
        close_cdp_page(page)


def close_job_targets(context) -> None:
    """Close Chrome targets this job created that Playwright never adopted."""
    for target_id in list(_job_target_ids(context)):
        _close_target_id(context, target_id)


def release_browser(playwright, context, job_pages: list | None = None) -> None:
    """Detach Playwright. On CDP, close only job pages; never kill Chrome."""
    close_job_pages(job_pages)
    close_job_targets(context)
    if context is not None and not is_cdp_attached(context):
        try:
            context.close()
        except Exception:
            pass
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            pass


def install_job_signal_handlers() -> None:
    """Turn cancel SIGTERM/SIGINT into a clean exit so finally still runs."""
    import signal

    def _stop(signum, _frame) -> None:
        raise SystemExit(128 + int(signum))

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            continue


def open_cdp_page(context, *, new_window: bool = False, timeout: float = 8.0):
    """Open one tab or window on the already-logged-in CDP Chrome profile.

    Creates exactly one Chrome target. If Playwright never attaches to it,
    that target is closed and no second window is opened.
    """
    session = _cdp_session(context)
    if session is None:
        raise RuntimeError("Chrome CDP is not available to open a job window.")
    created = session.send(
        "Target.createTarget",
        {"url": "about:blank", "newWindow": bool(new_window)},
    )
    created_id = _target_id_from_info(created)
    if not created_id:
        raise RuntimeError("Chrome did not return a target for the job window.")
    _remember_job_target(context, created_id)
    page = _wait_for_adopted_page(context, created_id, timeout)
    if page is None:
        _close_target_id(context, created_id)
        raise RuntimeError("Chrome opened a job window but Playwright did not attach to it.")
    try:
        page._ad_factory_target_id = created_id
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    return page
