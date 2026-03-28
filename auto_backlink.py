"""
Auto Backlink Analysis — run by GitHub Actions every working day at 9:30 AM IST.
Analyses the previous working day's backlinks for every team member and emails reports.
If a member has no data, sends a "no data / on leave" notification instead.

Usage: python auto_backlink.py [--date YYYY-MM-DD]
  --date   Override the analysis date (default: previous working day)
"""
import argparse
import io
import os
import sys
from datetime import date, timedelta

import pandas as pd
import requests as http_requests
from dotenv import load_dotenv

load_dotenv()

from team_store import load_team
from settings_store import load_settings
from backlink_analyzer import filter_backlinks, fetch_page_data, analyze_backlink, _extract_sheet_id


# ── Working-day logic ─────────────────────────────────────────────────────────

def is_working_day(d: date) -> bool:
    """Returns True for Mon–Fri and 1st/3rd Saturday. False for Sunday and 2nd/4th Saturday."""
    weekday = d.weekday()  # 0=Mon … 6=Sun
    if weekday == 6:  # Sunday
        return False
    if weekday == 5:  # Saturday — check which occurrence
        nth = (d.day - 1) // 7 + 1   # 1st, 2nd, 3rd, 4th, 5th
        if nth in (2, 4):
            return False
    return True


def previous_working_day(today: date) -> date:
    """Walk backwards until we find a working day."""
    d = today - timedelta(days=1)
    while not is_working_day(d):
        d -= timedelta(days=1)
    return d


# ── Sheet fetching (no Streamlit cache — this runs as a plain Python script) ──

def fetch_all_sheets_direct(sheet_url: str) -> dict:
    sheet_id = _extract_sheet_id(sheet_url)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    resp = http_requests.get(export_url, timeout=30)
    resp.raise_for_status()
    xl = pd.ExcelFile(io.BytesIO(resp.content))
    return {name: xl.parse(name) for name in xl.sheet_names}


# ── Email helpers ─────────────────────────────────────────────────────────────

def _brevo_send(subject: str, html_body: str, to_email: str, to_name: str):
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL", "sales@vyasharsh.com")
    cc_email = os.getenv("GMAIL_EMAIL", "")
    if not api_key:
        raise ValueError("BREVO_API_KEY not set")

    cc_list = [{"email": "tbmseoteam@gmail.com"}]
    if cc_email and cc_email.lower() != to_email.lower():
        cc_list.append({"email": cc_email})
    cc_list = [e for e in cc_list if e["email"].lower() != to_email.lower()]

    payload = {
        "sender": {"name": "Team Accountability Agent", "email": sender_email},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html_body,
    }
    if cc_list:
        payload["cc"] = cc_list

    resp = http_requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise ValueError(f"Brevo error {resp.status_code}: {resp.text}")


def send_backlink_report(member_name: str, member_email: str, results: list, analysis_date: date):
    rows_html = ""
    for r in results:
        a = r["analysis"]
        score = a.get("quality_score", 0)
        label = a.get("quality_label", "")
        color = "#28a745" if score >= 8 else ("#fd7e14" if score >= 6 else "#dc3545")
        suggestions = "<br>".join(f"• {s}" for s in a.get("suggestions", []))
        issues = "<br>".join(f"• {s}" for s in a.get("issues", []))
        rows_html += f"""
        <tr>
          <td style="padding:8px;border:1px solid #ddd;">{r['date']}</td>
          <td style="padding:8px;border:1px solid #ddd;">{r['type']}</td>
          <td style="padding:8px;border:1px solid #ddd;word-break:break-all;">
            <a href="{r['url']}">{r['url'][:60]}...</a>
          </td>
          <td style="padding:8px;border:1px solid #ddd;text-align:center;">
            <strong style="color:{color};">{score}/10 ({label})</strong>
          </td>
          <td style="padding:8px;border:1px solid #ddd;">{issues or '—'}</td>
          <td style="padding:8px;border:1px solid #ddd;">{suggestions or '—'}</td>
        </tr>"""

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <h2>📊 Auto Backlink Analysis Report</h2>
    <p><strong>Team Member:</strong> {member_name}</p>
    <p><strong>Date:</strong> {analysis_date}</p>
    <p><strong>Total Backlinks Reviewed:</strong> {len(results)}</p>
    <table style="border-collapse:collapse;width:100%;font-size:13px;">
      <thead style="background:#f2f2f2;">
        <tr>
          <th style="padding:8px;border:1px solid #ddd;">Date</th>
          <th style="padding:8px;border:1px solid #ddd;">Type</th>
          <th style="padding:8px;border:1px solid #ddd;">URL</th>
          <th style="padding:8px;border:1px solid #ddd;">Score</th>
          <th style="padding:8px;border:1px solid #ddd;">Issues</th>
          <th style="padding:8px;border:1px solid #ddd;">Suggestions</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <br><p style="color:#888;font-size:12px;">Sent automatically by Team Accountability Agent</p>
    </body></html>"""

    subject = f"Backlink Report — {member_name} ({analysis_date})"
    _brevo_send(subject, html_body, member_email, member_name)
    print(f"✓ Report sent to {member_name} <{member_email}> — {len(results)} backlinks")


def send_no_data_email(member_name: str, member_email: str, analysis_date: date):
    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <h2>⚠️ No Backlink Data Found — {member_name}</h2>
    <p>The automated backlink check for <strong>{analysis_date}</strong> found
    <strong>no backlinks</strong> in the Google Sheet for <strong>{member_name}</strong>.</p>
    <p>This could mean:</p>
    <ul>
      <li>The team member was on <strong>leave</strong> that day</li>
      <li>They forgot to <strong>update the sheet</strong> with their work</li>
    </ul>
    <p>Please check with {member_name} and ask them to update the sheet if applicable.</p>
    <br><p style="color:#888;font-size:12px;">Sent automatically by Team Accountability Agent</p>
    </body></html>"""

    subject = f"⚠️ No Backlink Data — {member_name} ({analysis_date})"
    _brevo_send(subject, html_body, member_email, member_name)
    print(f"⚠ No-data email sent for {member_name} <{member_email}>")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(analysis_date: date):
    today = date.today()
    print(f"Auto Backlink Analysis — today: {today}, analysing: {analysis_date}")

    # Safety check: don't run if today is a non-working day
    if not is_working_day(today):
        print(f"Today ({today}) is a non-working day — skipping.")
        return

    team = load_team()
    if not team:
        print("No team members found in R2. Nothing to do.")
        return

    settings = load_settings()
    sheet_url = settings.get("backlink_sheet_url", "")
    if not sheet_url:
        print("No Google Sheet URL saved in settings. Nothing to do.")
        sys.exit(1)

    print(f"Loading Google Sheet...")
    try:
        all_sheets = fetch_all_sheets_direct(sheet_url)
    except Exception as e:
        print(f"✗ Could not load Google Sheet: {e}")
        sys.exit(1)

    member_names = [m["name"] for m in team]

    for member in team:
        name = member["name"]
        email = member["email"]
        print(f"\n--- {name} ---")

        if name not in all_sheets:
            print(f"  No sheet named '{name}' in the workbook — skipping.")
            continue

        df = all_sheets[name]
        filtered = filter_backlinks(df, analysis_date, analysis_date)

        if filtered.empty:
            print(f"  No backlinks found for {analysis_date} — sending no-data email.")
            try:
                send_no_data_email(name, email, analysis_date)
            except Exception as e:
                print(f"  ✗ Failed to send no-data email: {e}")
            continue

        print(f"  {len(filtered)} backlink(s) found — analysing...")
        results = []

        for counter, (_, row) in enumerate(filtered.iterrows(), start=1):
            url = str(row.get("url", ""))
            bl_type = str(row.get("type", "Unknown"))
            project = str(row.get("project", ""))
            bl_date = str(row.get("date", ""))[:10]
            has_url = bool(row.get("has_url", False))

            print(f"  [{counter}/{len(filtered)}] {url[:70]}")

            if has_url:
                page_data = fetch_page_data(url)
                analysis = analyze_backlink(row.to_dict(), page_data)
            else:
                page_data = {"reachable": False, "error": "No URL provided"}
                analysis = {
                    "type_assessment": f"No URL entered for this {bl_type} entry.",
                    "quality_score": 0,
                    "quality_label": "No URL",
                    "html_structure": {},
                    "strengths": [],
                    "issues": ["No URL in the sheet for this entry"],
                    "suggestions": ["Ask the team member to add the published URL"],
                    "verdict": "Cannot analyse — no URL provided",
                }

            results.append({
                "url": url if has_url else "(no URL)",
                "type": bl_type,
                "project": project,
                "date": bl_date,
                "page_data": page_data,
                "analysis": analysis,
                "has_url": has_url,
            })

        try:
            send_backlink_report(name, email, results, analysis_date)
        except Exception as e:
            print(f"  ✗ Failed to send report email: {e}")

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        help="Date to analyse (YYYY-MM-DD). Defaults to previous working day.",
        default=None,
    )
    args = parser.parse_args()

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_date = previous_working_day(date.today())

    run(target_date)
