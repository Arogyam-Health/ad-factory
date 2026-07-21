from __future__ import annotations

import dashboard.backend.app as app_module
import dashboard.backend.reference_flow as reference_flow_module

from dashboard.backend.chatgpt_watchdog import run_chatgpt_generation_watchdog

_INSTALLED = False


def install_chatgpt_watchdog() -> None:
    """Install the watchdog for reference generation, conversions, and revisions."""
    global _INSTALLED
    if _INSTALLED:
        return
    app_module.run_chatgpt_generation = run_chatgpt_generation_watchdog
    reference_flow_module.run_chatgpt_generation = run_chatgpt_generation_watchdog
    _INSTALLED = True
