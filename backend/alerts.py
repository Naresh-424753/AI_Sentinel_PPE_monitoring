"""Outgoing alerts: Telegram, e-mail, WhatsApp (Twilio) and a generic webhook, plus e-mail to one person.
Each channel is optional - it is used only when configured in .env."""
import os
import smtplib
import threading
import time
from email.message import EmailMessage

from backend import config
from backend.services.incident_service import SEVERITIES, pretty


def _rank(sev):
    return SEVERITIES.index(sev) if sev in SEVERITIES else 1


def _text(inc):
    if inc.get("_text"):
        return inc["_text"]
    return (f"AI Sentinel alert - {inc.get('severity', 'MEDIUM')}\n"
            f"{inc['incident_id']}: {pretty(inc['violation'])}\n"
            f"Camera: {inc.get('camera_name') or '-'}  Zone: {inc.get('zone') or '-'}\n"
            f"Time: {inc.get('timestamp')}  Confidence: {float(inc.get('confidence') or 0) * 100:.0f}%\n"
            f"Open: {config.FRONTEND_URL}/incidents")


def _shot(inc):
    if not inc.get("screenshot"):
        return None
    p = os.path.join(config.INCIDENTS_DIR, os.path.basename(inc["screenshot"]))
    return p if os.path.exists(p) else None


def channels_status():
    return {
        "telegram": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID),
        "email": bool(config.SMTP_EMAIL and config.SMTP_PASSWORD and config.ALERT_EMAILS),
        "whatsapp": bool(config.TWILIO_SID and config.TWILIO_TOKEN and config.TWILIO_WHATSAPP_FROM and config.WHATSAPP_TO),
        "webhook": bool(config.ALERT_WEBHOOK_URL),
        "min_severity": config.ALERT_MIN_SEVERITY,
    }


def _smtp():
    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        server.starttls()
    server.login(config.SMTP_EMAIL, config.SMTP_PASSWORD)
    return server


def _telegram(inc):
    import requests
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    shot = _shot(inc)
    if shot:
        with open(shot, "rb") as f:
            requests.post(f"{base}/sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": _text(inc)},
                          files={"photo": f}, timeout=20)
    else:
        requests.post(f"{base}/sendMessage", data={"chat_id": config.TELEGRAM_CHAT_ID, "text": _text(inc)}, timeout=20)


def _email(inc):
    msg = EmailMessage()
    msg["Subject"] = inc.get("_subject") or \
        f"[AI Sentinel] {inc.get('severity')} - {pretty(inc['violation'])} ({inc['incident_id']})"
    msg["From"] = config.SMTP_EMAIL
    msg["To"] = ", ".join(config.ALERT_EMAILS)
    msg.set_content(_text(inc))
    shot = _shot(inc)
    if shot:
        with open(shot, "rb") as f:
            msg.add_attachment(f.read(), maintype="image", subtype="jpeg", filename=os.path.basename(shot))
    with _smtp() as server:
        server.send_message(msg)


def _whatsapp(inc):
    import requests
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_SID}/Messages.json"
    for to in config.WHATSAPP_TO:
        data = {"From": config.TWILIO_WHATSAPP_FROM, "To": to, "Body": _text(inc)}
        if inc.get("screenshot"):
            data["MediaUrl"] = f"{config.PUBLIC_API_URL}/images/{os.path.basename(inc['screenshot'])}"
        requests.post(url, data=data, auth=(config.TWILIO_SID, config.TWILIO_TOKEN), timeout=20)


def _webhook(inc):
    import requests
    payload = {k: v for k, v in inc.items() if not k.startswith("_")}
    requests.post(config.ALERT_WEBHOOK_URL, json={"event": "incident", "incident": payload, "text": _text(inc)}, timeout=15)


def _send_all(inc, force=False):
    if not force and _rank(inc.get("severity")) < _rank(config.ALERT_MIN_SEVERITY):
        return
    try:
        from backend import push
        push.push_incident(inc)           # phones / browsers that turned on push alerts
    except Exception as e:
        print(f"alert via push failed: {e}")
    st = channels_status()
    for name, fn in (("telegram", _telegram), ("email", _email), ("whatsapp", _whatsapp), ("webhook", _webhook)):
        if st[name]:
            try:
                fn(inc)
            except Exception as e:
                print(f"alert via {name} failed: {e}")


def dispatch(inc, force=False):
    """Send an incident alert to every configured channel (in the background).
    Set inc["_text"] / inc["_subject"] to override the wording (used for escalations).
    During quiet hours (Admin > Quiet hours) the alert is dropped; the incident itself is still saved.
    force=True (the test button) always goes out."""
    if not force:
        try:
            from backend import mute
            if mute.is_muted(inc.get("severity")):
                print(f"alert for {inc.get('incident_id')} not sent: quiet hours")
                return
        except Exception as e:
            print("quiet-hour check failed, sending the alert:", e)
    threading.Thread(target=_send_all, args=(inc, force), daemon=True).start()


def _send_one(to_email, subject, body):
    """Send one e-mail. A temporary refusal (SMTP 4xx, e.g. Gmail 'Try again later') is retried twice."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_EMAIL
    msg["To"] = to_email
    msg.set_content(body)
    for attempt in range(3):
        try:
            with _smtp() as server:
                server.send_message(msg)
            return True
        except smtplib.SMTPResponseException as e:
            if 400 <= e.smtp_code < 500 and attempt < 2:
                time.sleep(30 * (attempt + 1))
                continue
            print(f"e-mail to {to_email} failed: {e}")
            return False
        except Exception as e:
            print(f"e-mail to {to_email} failed: {e}")
            return False
    return False


def email_user(to_email, subject, body):
    """E-mail one person (assignment, reply, escalation). Silently skipped when SMTP is not configured."""
    if not (config.SMTP_EMAIL and config.SMTP_PASSWORD and to_email):
        return
    threading.Thread(target=_send_one, args=(to_email, subject, body), daemon=True).start()


def send_email_with_attachment(to_list, subject, body, filename, data, mime="application/pdf"):
    """Synchronous (the caller wants to know if it failed). Used for scheduled reports."""
    if not (config.SMTP_EMAIL and config.SMTP_PASSWORD):
        raise RuntimeError("E-mail is not configured (set SMTP_EMAIL and SMTP_PASSWORD in .env)")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_EMAIL
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)
    maintype, subtype = mime.split("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    with _smtp() as server:
        server.send_message(msg)


def send_test():
    inc = {"incident_id": "TEST-0000", "violation": "no_helmet", "severity": "CRITICAL",
           "camera_name": "Test camera", "zone": "Test", "confidence": 0.99, "timestamp": "now"}
    dispatch(inc, force=True)