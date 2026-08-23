from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import requests


MAX_PROVIDER_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROVIDER_RESPONSE_BYTES = 4 * 1024 * 1024
_GOOGLE_PATH = re.compile(
    r"^/v1beta/models/[A-Za-z0-9._-]{1,256}:generateContent$"
)


def _validate_endpoint(provider: str, endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ValueError("Provider relay endpoint is not allowed")
    if provider == "opencode":
        valid = (
            parsed.hostname == "opencode.ai"
            and parsed.path == "/zen/v1/chat/completions"
        )
    elif provider == "google_gemini":
        valid = (
            parsed.hostname == "generativelanguage.googleapis.com"
            and bool(_GOOGLE_PATH.fullmatch(parsed.path))
        )
    else:
        valid = False
    if not valid:
        raise ValueError("Provider relay endpoint is not allowed")


def execute_provider_call(
    payload: dict[str, Any],
    *,
    post: Callable[..., Any] = requests.post,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Provider relay call is invalid")
    provider = str(payload.get("provider") or "")
    endpoint = str(payload.get("endpoint") or "")
    api_key = str(payload.get("api_key") or "")
    request_body = payload.get("request_body")
    _validate_endpoint(provider, endpoint)
    if (
        not api_key
        or len(api_key) > 4096
        or not isinstance(request_body, dict)
    ):
        raise ValueError("Provider relay call is invalid")
    serialized = json.dumps(
        request_body,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_PROVIDER_REQUEST_BYTES:
        raise ValueError("Provider relay request is too large")

    headers = {"Content-Type": "application/json"}
    if provider == "opencode":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["x-goog-api-key"] = api_key
    try:
        response = post(
            endpoint,
            headers=headers,
            json=request_body,
            timeout=None,
            allow_redirects=False,
            stream=True,
        )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_PROVIDER_RESPONSE_BYTES:
                raise ValueError("Provider relay response is too large")
            chunks.append(bytes(chunk))
        body = b"".join(chunks).decode("utf-8", errors="replace")
        return {
            "http_status": int(response.status_code),
            "content_type": str(
                response.headers.get("content-type") or ""
            )[:200],
            "body": body,
            "transport_error": "",
        }
    except requests.RequestException as exc:
        return {
            "http_status": 0,
            "content_type": "",
            "body": "",
            "transport_error": type(exc).__name__,
        }
