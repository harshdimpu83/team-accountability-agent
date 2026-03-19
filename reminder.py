"""
Reminder script — run by Render Cron Jobs.
Usage: python reminder.py morning
       python reminder.py evening
"""
import os
import sys
import smtplib
from email.mime.text import MIMEText
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from team_store import load_team
from gmail_reader import fetch_emails_for_member, classify_emails

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

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
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
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
