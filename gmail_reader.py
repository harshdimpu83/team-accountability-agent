import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import date
import os

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Emails sent before this hour (24h) = morning plan
MORNING_CUTOFF_HOUR = 12
# Emails sent at or after this hour (24h) = evening report
EVENING_START_HOUR = 15


def fetch_emails_for_member(sender_email: str, target_date: date) -> list:
    """Fetch all emails from a specific sender on a specific date."""
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        raise ValueError(
            "Gmail credentials not set. Add GMAIL_EMAIL and GMAIL_APP_PASSWORD to your .env file."
        )

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
    mail.select("inbox")

    # IMAP date format: 01-Jan-2025
    date_str = target_date.strftime("%d-%b-%Y")
    search_criteria = f'(FROM "{sender_email}" ON {date_str})'

    _, message_ids = mail.search(None, search_criteria)

    emails = []
    if message_ids[0]:
        for msg_id in message_ids[0].split():
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            # Parse sent time
            date_header = msg.get("Date")
            sent_time = None
            sent_hour = None
            if date_header:
                try:
                    sent_time = parsedate_to_datetime(date_header)
                    sent_hour = sent_time.hour
                except Exception:
                    pass

            subject = _decode_header_str(msg.get("Subject", "(no subject)"))
            body = _extract_body(msg)

            emails.append({
                "subject": subject,
                "body": body,
                "sent_time": sent_time,
                "hour": sent_hour,
            })

    mail.logout()
    return emails


def classify_emails(emails: list) -> tuple:
    """Split emails into (morning_plan_emails, evening_report_emails)."""
    morning = []
    evening = []

    for e in emails:
        hour = e.get("hour")
        if hour is None:
            continue
        if hour < MORNING_CUTOFF_HOUR:
            morning.append(e)
        elif hour >= EVENING_START_HOUR:
            evening.append(e)
        # emails between noon and 3pm are skipped (unlikely plan or EOD)

    return morning, evening


def _decode_header_str(raw: str) -> str:
    parts = decode_header(raw)
    result = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _extract_body(msg) -> str:
    """Extract plain text body, falling back to HTML stripped of tags."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
    return body.strip()
