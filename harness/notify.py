"""Cycle 20: reviewer notifications -- a Slack incoming webhook and a
plain-text email via SMTP, both entirely optional per tenant.yaml's
`notifications` key and both fail closed. Missing credentials, or the
channel turned off, never raises and never blocks a run or a command -- it
just logs "notification skipped: no channel configured" and moves on.

No secret (a webhook URL, an SMTP password, an API key) is ever put into a
message body or printed. An ad claim flagged as an overclaim is truncated to
120 characters before it can appear in any message, so a notification can
never repeat a full flagged claim verbatim.
"""
import json
import os
import smtplib
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

MAX_CLAIM_CHARS = 120


def _truncate_claim(text):
    text = text or ""
    return text if len(text) <= MAX_CLAIM_CHARS else text[: MAX_CLAIM_CHARS - 1] + "…"


def send_slack(webhook_url, text, *, log=None):
    if not webhook_url:
        if log:
            log.event("notify", "notification skipped: no channel configured (slack)")
        return False
    body = json.dumps({"text": text}).encode("utf-8")
    # Cycle 22 finding R19: this used to catch urllib.error.URLError only, but
    # Request() itself raises ValueError on a webhook url with no scheme
    # ("unknown url type"), and urlopen can surface an http.client error --
    # either of which killed the review_notify stage of an otherwise finished
    # run, contradicting this module's own promise that a notification never
    # blocks anything. A notification is best-effort by design; the run's own
    # output is not.
    try:
        req = urllib.request.Request(
            webhook_url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
    except Exception as e:
        if log:
            log.event("notify", f"slack send failed: {type(e).__name__}: {e}")
        return False
    if log:
        log.event("notify", f"slack sent: {ok}")
    return ok


def send_email(*, host, port, user, password, from_addr, to_addrs, subject, body, log=None):
    if not (host and port and user and password and from_addr and to_addrs):
        if log:
            log.event("notify", "notification skipped: no channel configured (email)")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    try:
        with smtplib.SMTP(host, int(port), timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
    except Exception as e:
        # Same rule as send_slack above: a non-numeric SMTP_PORT raises
        # ValueError out of int(), which the old (smtplib.SMTPException, OSError)
        # pair did not cover.
        if log:
            log.event("notify", f"email send failed: {type(e).__name__}: {e}")
        return False
    if log:
        log.event("notify", f"email sent to {len(to_addrs)} address(es)")
    return True


def _channels_from_env():
    """Reads SLACK_WEBHOOK_URL / SMTP_* from the process environment (loaded
    from the tenant's .env by Tenant.load_env, same as every other
    credential in this harness). Never logged, never printed."""
    return {
        "slack_webhook_url": os.environ.get("SLACK_WEBHOOK_URL"),
        "smtp_host": os.environ.get("SMTP_HOST"),
        "smtp_port": os.environ.get("SMTP_PORT"),
        "smtp_user": os.environ.get("SMTP_USER"),
        "smtp_pass": os.environ.get("SMTP_PASS"),
        "notify_from": os.environ.get("NOTIFY_FROM"),
    }


def _send(tenant, subject, text, *, log=None):
    """Sends `text` to every channel tenant.yaml's `notifications` turns on.
    Each channel fails closed independently. Returns {"slack": bool, "email": bool}."""
    notifications = tenant.get("notifications") or {}
    env = _channels_from_env()
    sent = {"slack": False, "email": False}

    if notifications.get("slack"):
        sent["slack"] = send_slack(env["slack_webhook_url"], text, log=log)
    elif log:
        log.event("notify", "notification skipped: no channel configured (slack disabled for tenant)")

    to_addrs = notifications.get("email") or []
    if to_addrs:
        sent["email"] = send_email(
            host=env["smtp_host"], port=env["smtp_port"], user=env["smtp_user"],
            password=env["smtp_pass"], from_addr=env["notify_from"], to_addrs=to_addrs,
            subject=subject, body=text, log=log,
        )
    elif log:
        log.event("notify", "notification skipped: no channel configured (no email recipients for tenant)")

    return sent


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_needs_review_message(*, tenant, run_id, input_name, pages, gate_log,
                                  ad_not_repeated, review_paths, approve_command):
    lines = [
        f"[{tenant.display_name}] Run {run_id} needs review",
        f"Input: {input_name}",
        f"Pages: {', '.join(pages)}",
        "",
        "Gate history:",
    ]
    for name in pages:
        entry = (gate_log or {}).get(name, {})
        attempts = entry.get("attempts", [[]])
        lines.append(f"  - {name}: {len(attempts)} attempt(s)")
    lines.append("")
    lines.append(f"AD CLAIMS NOT REPEATED ON PAGE: {len(ad_not_repeated or [])}")
    for item in (ad_not_repeated or [])[:5]:
        lines.append(f"  - {_truncate_claim(item.get('message', ''))}")
    lines.append("")
    lines.append("Review files:")
    for path in review_paths:
        lines.append(f"  - {path}")
    lines.append("")
    lines.append(f"Approve: {approve_command}")
    return "\n".join(lines)


def notify_needs_review(tenant, *, run_id, input_name, pages, gate_log, ad_not_repeated,
                          run_dir, log=None):
    run_dir = Path(run_dir)
    review_paths = [str(run_dir / "REVIEW.md")] + [
        str(run_dir / f"{name}-review.html") for name in pages
    ]
    approve_command = f"harness approve {run_dir} --by <email> --pages {','.join(pages)}"
    text = format_needs_review_message(
        tenant=tenant, run_id=run_id, input_name=input_name, pages=pages, gate_log=gate_log,
        ad_not_repeated=ad_not_repeated, review_paths=review_paths, approve_command=approve_command,
    )
    return _send(tenant, f"[{tenant.display_name}] run {run_id} needs review", text, log=log)


def notify_approved(tenant, *, run_id, by, pages, run_dir, log=None):
    text = (
        f"[{tenant.display_name}] Run {run_id} approved by {by}\n"
        f"Pages: {', '.join(pages)}\n"
        f"Run dir: {run_dir}"
    )
    return _send(tenant, f"[{tenant.display_name}] run {run_id} approved", text, log=log)


def notify_published(tenant, *, run_id, page, url, log=None):
    text = f"[{tenant.display_name}] Run {run_id} page {page!r} published\nURL: {url}"
    return _send(tenant, f"[{tenant.display_name}] run {run_id} published", text, log=log)
