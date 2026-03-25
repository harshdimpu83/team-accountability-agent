"""
Reminder script — run by Render Cron Jobs.
Usage: python reminder.py morning
       python reminder.py evening
"""
import os
import sys
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from team_store import load_team
from gmail_reader import fetch_emails_for_member, classify_emails

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "sales@vyasharsh.com")

MORNING_SUBJECT = "Reminder: Please send your daily plan"
MORNING_BODY = (
    "Hi {name},\n\n"
    "This is a friendly reminder to send your daily plan email for today.\n\n"
    "Please reply with what tasks you plan to complete today.\n\n"
    "Thank you!"
)

EVENING_SUBJECT = "Reminder: Please send your end-of-day report"
EVENING_BODY = (
    "Hi {name},\n\n"
    "This is a friendly reminder to send your end-of-day report.\n\n"
    "Please reply with what tasks you completed today.\n\n"
    "Thank you!"
)


def send_email(to_email: str, to_name: str, subject: str, body: str):
    payload = {
        "sender": {"name": "Team Accountability Agent", "email": BREVO_SENDER_EMAIL},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "textContent": body,
    }
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise ValueError(f"Brevo error {resp.status_code}: {resp.text}")
    print(f"✓ Reminder sent to {to_name} <{to_email}>")


def run(reminder_type: str):
    team = load_team()
    if not team:
        print("No team members found. Nothing to do.")
        return

    today = date.today()
    print(f"Running {reminder_type} reminder check for {today} — {len(team)} members")

    for member in team:
        name = member["name"]
        email = member["email"]

        try:
            emails = fetch_emails_for_member(email, today)
            morning_emails, evening_emails = classify_emails(emails)

            if reminder_type == "morning" and not morning_emails:
                send_email(email, name, MORNING_SUBJECT, MORNING_BODY.format(name=name))
            elif reminder_type == "evening" and not evening_emails:
                send_email(email, name, EVENING_SUBJECT, EVENING_BODY.format(name=name))
            else:
                print(f"✓ {name} already sent their {reminder_type} email — no reminder needed")

        except Exception as e:
            print(f"✗ Error processing {name}: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("morning", "evening"):
        print("Usage: python reminder.py morning|evening")
        sys.exit(1)
    run(sys.argv[1])
