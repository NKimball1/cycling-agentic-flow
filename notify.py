"""Phase 3 delivery: send the finished analysis to you.

Delivery is deterministic plumbing — the output step, not the agent. sync.py
(and analyze.py) call notify() after producing an analysis. Channels are
toggled via NOTIFY_CHANNELS in .env, so this stays modular: to add a channel,
write a sender and add its name to the list.

Currently supported: "email" (your own Gmail via SMTP + App Password).
"""

from __future__ import annotations

import html
import smtplib
import socket
from email.message import EmailMessage

import config
import net

# Transient SMTP/connection failures worth a retry. Auth/recipient errors are
# NOT here — they're permanent config problems that should surface immediately.
_TRANSIENT_SMTP = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    socket.timeout,
    OSError,
)


def _markdown_to_html(md_text: str) -> str:
    """Render the analysis Markdown to HTML. Falls back to <pre> if the
    markdown package isn't installed."""
    try:
        import markdown

        return markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    except ImportError:
        return f"<pre style='white-space:pre-wrap'>{html.escape(md_text)}</pre>"


def _wrap_html(inner: str) -> str:
    """Wrap the rendered analysis in a simple, email-client-friendly shell."""
    return (
        '<div style="max-width:680px;margin:0 auto;padding:20px;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'color:#1a1a1a;line-height:1.55;font-size:15px;">'
        f"{inner}"
        '<hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0;">'
        '<p style="color:#888;font-size:12px;">Sent by your cycling-agentic-flow coach.</p>'
        "</div>"
    )


def send_email(subject: str, markdown_body: str) -> None:
    """Send the analysis as a Markdown-and-HTML email via Gmail SMTP."""
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
        raise RuntimeError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set in .env. Create an App "
            "Password at Google Account -> Security -> 2-Step Verification -> App passwords."
        )
    if not config.NOTIFY_TO:
        raise RuntimeError("No recipient: set NOTIFY_TO in .env (or GMAIL_ADDRESS).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = config.NOTIFY_TO
    msg.set_content(markdown_body)  # plain-text part (the raw Markdown, still readable)
    msg.add_alternative(_wrap_html(_markdown_to_html(markdown_body)), subtype="html")

    def _send() -> None:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)

    net.call_with_retries(_send, attempts=3, base_delay=2.0, retry_on=_TRANSIENT_SMTP, label="email send")


def notify(subject: str, markdown_body: str, verbose: bool = True) -> None:
    """Deliver `markdown_body` over every channel enabled in NOTIFY_CHANNELS.

    A no-op if NOTIFY_CHANNELS is empty, so nothing is ever sent until you opt in.
    """
    if not config.NOTIFY_CHANNELS:
        if verbose:
            print("  (delivery off — set NOTIFY_CHANNELS in .env to enable)")
        return

    for channel in config.NOTIFY_CHANNELS:
        if channel == "email":
            send_email(subject, markdown_body)
            if verbose:
                print(f"  Emailed analysis to {config.NOTIFY_TO}")
        else:
            if verbose:
                print(f"  (unknown notify channel '{channel}' — skipped)")


if __name__ == "__main__":
    # Send yourself a test email to verify your Gmail setup.
    #   python notify.py
    notify(
        "Test — cycling agentic flow",
        "# Test email\n\nIf you're reading this, **email delivery works**.\n\n"
        "- Gmail SMTP is connected\n- Markdown renders to HTML\n",
    )
    print("Test notification sent (if email is enabled in .env).")
