"""Real transactional email sending -- currently just used for password
reset links, but written generically so any future feature (billing
receipts, task-complete notifications, etc.) can reuse `send_email`.

Uses Python's built-in `smtplib` against whatever SMTP provider is
configured via environment variables -- no paid email API or extra
package required. Works with any real SMTP provider: a Gmail account
with an App Password, or a transactional provider's free tier (Brevo,
SendGrid, etc. all give you SMTP credentials).

Design rules (same honesty pattern as the rest of this project):
  - The SMTP credentials are NEVER hardcoded -- read from environment
    variables only (see config.INTEGRATIONS["email"]). If they aren't
    set, is_configured("email") already tells the caller that, so it can
    show an honest "not set up yet" message instead of pretending to send.
  - We never fabricate success. If the SMTP connection or send fails for
    any reason, send_email returns a clear, honest error.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

import config

SMTP_TIMEOUT_SECONDS = 20


def send_email(to_email, subject, body_text):
    """Send a plain-text email. Returns (ok, error_message_or_None).

    Never raises -- any failure (missing config, can't connect, bad
    credentials, provider rejects the send) comes back as an honest
    error string instead of an unhandled exception reaching the caller.
    """
    if not config.is_configured("email"):
        return False, "Email sending isn't configured for this app yet (no SMTP credentials set)."

    host = os.environ.get("SMTP_HOST")
    port_raw = os.environ.get("SMTP_PORT", "587")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ.get("SMTP_FROM_EMAIL")

    try:
        port = int(port_raw)
    except ValueError:
        return False, f"SMTP_PORT is set to '{port_raw}', which isn't a valid port number."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Creator Hub AI <{from_email}>"
    msg["To"] = to_email
    msg.set_content(body_text)

    try:
        if port == 465:
            # Implicit TLS from the start of the connection -- the
            # convention most providers use on port 465.
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context) as server:
                server.login(username, password)
                server.send_message(msg)
        else:
            # STARTTLS -- the convention most providers use on port 587
            # (and the default here, since it's the most common setup).
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(username, password)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, "The email provider rejected the SMTP username/password (authentication failed)."
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        return False, f"Couldn't send the email: {exc}"

    return True, None
