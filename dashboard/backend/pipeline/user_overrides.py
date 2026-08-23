from __future__ import annotations

from typing import Any

_current_user_config: dict[str, Any] = {}


def set_user_config_overrides(config: dict[str, Any]) -> None:
    global _current_user_config
    _current_user_config = config or {}


def clear_user_config_overrides() -> None:
    global _current_user_config
    _current_user_config = {}


def resolve_user_config(key: str, default: Any = None) -> Any:
    if _current_user_config and key in _current_user_config:
        return _current_user_config[key]
    return default
