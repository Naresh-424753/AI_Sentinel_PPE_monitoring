"""One place that sends e-mail.

If BREVO_API_KEY is set, mail goes through Brevo's HTTPS API (port 443). That works on hosts such as the
Render free plan, which block SMTP ports. Otherwise it uses SMTP exactly as before (SMTP_EMAIL / SMTP_PASSWORD).

.env / Render variables:
  BREVO_API_KEY   the v3 API key from Brevo (starts with xkeysib-)
  MAIL_FROM       a sender e-mail you verified in Brevo (falls back to SMTP_EMAIL)
  MAIL_FROM_NAME  optional, default "AI Sentinel"
"""
import base64
import os
import smtplib
from email.message import EmailMessage

from backend import config

BREVO_URL = os.getenv("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")


def _key():
    return os.getenv("BREVO_API_KEY", "").strip()


def sender():
    return os.getenv("MAIL_FROM", "").strip() or getattr(config, "SMTP_EMAIL", "")


def configured():
    """True when some way of sending e-mail is set up."""
    if _key():
        return bool(sender())
    return bool(getattr(config, "SMTP_EMAIL", "") and getattr(config, "SMTP_PASSWORD", ""))


def _brevo(to_list, subject, body, attachments):
    import requests
    payload = {
        "sender": {"name": os.getenv("MAIL_FROM_NAME", "AI Sentinel"), "email": sender()},
        "to": [{"email": t} for t in to_list],
        "subject": subject,
        "textContent": body,
    }
    if attachments:
        payload["attachment"] = [{"name": n, "content": base64.b64encode(d).decode()} for n, d, _m in attachments]
    r = requests.post(BREVO_URL, json=payload, timeout=20,
                      headers={"api-key": _key(), "accept": "application/json", "content-type": "application/json"})
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"Brevo refused the mail ({r.status_code}): {r.text[:300]}")


def _smtp(to_list, subject, body, attachments):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_EMAIL
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)
    for name, data, mime in attachments or []:
        maintype, subtype = mime.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        server.starttls()
    server.login(config.SMTP_EMAIL, config.SMTP_PASSWORD)
    with server:
        server.send_message(msg)


def send(to, subject, body, attachments=None):
    """Send one message. `to` is an address or a list. `attachments` is a list of (filename, bytes, mime).
    Raises when it fails, so the caller can decide what to do."""
    to_list = [to] if isinstance(to, str) else [t for t in to if t]
    if not to_list:
        raise ValueError("no recipient")
    if not configured():
        raise RuntimeError("E-mail is not configured (set BREVO_API_KEY + MAIL_FROM, or SMTP_EMAIL + SMTP_PASSWORD)")
    if _key():
        _brevo(to_list, subject, body, attachments)
    else:
        _smtp(to_list, subject, body, attachments)
