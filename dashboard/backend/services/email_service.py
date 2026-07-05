from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Any

from dashboard.backend.db.settings import settings


def _build_invite_html(
    org_name: str,
    inviter_display: str,
    role: str,
    invite_url: str,
) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:0 20px">
  <h2>You're invited to {org_name}</h2>
  <p><strong>{inviter_display}</strong> invited you to join <strong>{org_name}</strong> on Ad Factory as <strong>{role}</strong>.</p>
  <p style="margin:32px 0">
    <a href="{invite_url}" style="display:inline-block;padding:14px 36px;background:#F5821F;color:#fff;text-decoration:none;border-radius:8px;font-weight:600">
      Accept Invite
    </a>
  </p>
  <p style="color:#666;font-size:14px">This invite expires in 7 days.</p>
  <hr style="margin-top:32px;border:none;border-top:1px solid #eee" />
  <p style="color:#999;font-size:12px">Ad Factory — Creative pipeline for your team.</p>
</body>
</html>"""


def _build_invite_text(
    org_name: str,
    inviter_display: str,
    role: str,
    invite_url: str,
) -> str:
    return (
        f"{inviter_display} invited you to join {org_name} on Ad Factory as {role}.\n\n"
        f"Accept invite:\n{invite_url}\n\n"
        "This invite expires in 7 days."
    )


def try_send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> dict[str, Any]:
    """Send email via configured provider. Returns {sent, provider, error?}."""
    resend_key = os.environ.get("RESEND_API_KEY", "").strip()
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    email_from = os.environ.get("EMAIL_FROM", "").strip()

    if not email_from:
        email_from = "noreply@adfactory.app"

    if resend_key:
        return _send_via_resend(resend_key, email_from, to_email, subject, html_body, text_body)
    elif smtp_host:
        return _send_via_smtp(smtp_host, email_from, to_email, subject, html_body, text_body)
    else:
        return {"sent": False, "provider": "none", "error": None}


def _send_via_resend(
    api_key: str,
    email_from: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> dict[str, Any]:
    try:
        import httpx
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": email_from,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=15,
        )
        if resp.is_success:
            return {"sent": True, "provider": "resend", "error": None}
        else:
            return {"sent": False, "provider": "resend", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"sent": False, "provider": "resend", "error": str(e)}


def _send_via_smtp(
    smtp_host: str,
    email_from: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> dict[str, Any]:
    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "").strip()
        smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()

        msg = MIMEText(html_body, "html")
        msg["Subject"] = subject
        msg["From"] = email_from
        msg["To"] = to_email

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)

        return {"sent": True, "provider": "smtp", "error": None}
    except Exception as e:
        return {"sent": False, "provider": "smtp", "error": str(e)}


def send_invite_email(
    to_email: str,
    inviter_name: str,
    org_name: str,
    role: str,
    invite_url: str,
) -> dict[str, Any]:
    """Send an invite email. Returns {sent, provider, error?}."""
    inviter_display = inviter_name or to_email
    subject = f"{inviter_display} invited you to Ad Factory"
    html = _build_invite_html(org_name, inviter_display, role, invite_url)
    text = _build_invite_text(org_name, inviter_display, role, invite_url)
    return try_send_email(to_email, subject, html, text)
