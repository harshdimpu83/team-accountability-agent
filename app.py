import streamlit as st
import os
from datetime import date
from dotenv import load_dotenv

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from team_store import load_team, add_member, update_member, delete_member
from gmail_reader import fetch_emails_for_member, classify_emails
from ai_analyzer import analyze_member_tasks
from backlink_analyzer import fetch_all_sheets, filter_backlinks, fetch_page_data, analyze_backlink, score_color
from settings_store import load_settings, save_settings

load_dotenv()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _all_done(result: dict) -> bool:
    return bool(result["comparison"]) and all(
        i.get("status") == "done" for i in result["comparison"]
    )


def _any_done(result: dict) -> bool:
    return any(i.get("status") == "done" for i in result["comparison"])


# ── Auth ──────────────────────────────────────────────────────────────────────
APP_PASSWORD = os.getenv("APP_PASSWORD", "")


def check_auth():
    if not APP_PASSWORD:
        return
    if st.session_state.get("authenticated"):
        return
    pwd = st.text_input("Enter password", type="password")
    if pwd == APP_PASSWORD:
        st.session_state["authenticated"] = True
        st.rerun()
    elif pwd:
        st.error("Wrong password")
    st.stop()


st.set_page_config(page_title="Team Accountability", page_icon="📋", layout="wide")
check_auth()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📋 Team Accountability Agent")
st.caption("Check if your team completed what they planned.")

report_tab, backlink_tab, team_tab = st.tabs(["📊 Daily Report", "🔗 Backlink Analysis", "👥 Team"])

# ══════════════════════════════════════════════════════════════════════════════
# REPORT TAB
# ══════════════════════════════════════════════════════════════════════════════
with report_tab:
    team = load_team()

    col_date, col_btn = st.columns([3, 1])
    with col_date:
        selected_date = st.date_input("Select date", value=date.today(), label_visibility="collapsed")
    with col_btn:
        run_report = st.button("🔍 Run Report", type="primary", use_container_width=True)

    if run_report:
        if not team:
            st.warning("No team members yet. Add them in the **Team** tab first.")
            st.stop()

        st.divider()

        for member in team:
            with st.spinner(f"Checking {member['name']}..."):
                try:
                    emails = fetch_emails_for_member(member["email"], selected_date)
                    morning_emails, evening_emails = classify_emails(emails)
                    result = analyze_member_tasks(member["name"], morning_emails, evening_emails)
                except Exception as e:
                    st.error(f"**{member['name']}** — Error: {e}")
                    continue

            # ── Member result card ──
            status_icon = "✅" if _all_done(result) else ("⚠️" if _any_done(result) else "❌")
            with st.expander(f"{status_icon} **{member['name']}** — {result['summary']}", expanded=True):

                col_plan, col_done = st.columns(2)
                with col_plan:
                    st.markdown("**Morning Plan**")
                    if result["planned_tasks"]:
                        for task in result["planned_tasks"]:
                            st.markdown(f"- {task}")
                    else:
                        st.caption("No morning email found")

                with col_done:
                    st.markdown("**Evening Report**")
                    if result["completed_tasks"]:
                        for task in result["completed_tasks"]:
                            st.markdown(f"- {task}")
                    else:
                        st.caption("No evening email found")

                if result["comparison"]:
                    st.divider()
                    st.markdown("**Task Breakdown**")
                    for item in result["comparison"]:
                        status = item.get("status", "")
                        task = item.get("task", "")
                        note = item.get("note", "")
                        if status == "done":
                            st.success(f"✓ {task}")
                        elif status == "missed":
                            st.error(f"✗ {task}")
                        else:
                            st.warning(f"⚠ {task}" + (f" — {note}" if note else ""))

            st.write("")  # spacing between members


# ══════════════════════════════════════════════════════════════════════════════
# BACKLINK ANALYSIS TAB
# ══════════════════════════════════════════════════════════════════════════════
with backlink_tab:
    st.subheader("🔗 Backlink Analysis")

    # ── Google Sheet URL setting ──
    settings = load_settings()
    saved_url = settings.get("backlink_sheet_url", "")

    with st.expander("⚙️ Google Sheet Settings", expanded=(not saved_url)):
        sheet_input = st.text_input(
            "Google Sheet URL",
            value=saved_url,
            placeholder="https://docs.google.com/spreadsheets/d/...",
        )
        if st.button("Save Sheet URL", key="save_sheet_url"):
            if sheet_input.strip():
                save_settings({**settings, "backlink_sheet_url": sheet_input.strip()})
                st.success("Sheet URL saved.")
                st.rerun()
            else:
                st.error("Please enter a valid Google Sheet URL.")

    sheet_url = sheet_input.strip() or saved_url
    if not sheet_url:
        st.info("Add your Google Sheet URL above to get started.")
        st.stop()

    # ── Filters ──
    team = load_team()
    member_names = [m["name"] for m in team]
    member_email_map = {m["name"]: m["email"] for m in team}

    col_member, col_from, col_to, col_run = st.columns([2, 1.5, 1.5, 1])
    with col_member:
        selected_member = st.selectbox("Team Member", ["All members"] + member_names)
    with col_from:
        from_date = st.date_input("From", value=date.today().replace(day=1), key="bl_from")
    with col_to:
        to_date = st.date_input("To", value=date.today(), key="bl_to")
    with col_run:
        st.write("")  # vertical alignment
        run_analysis = st.button("🔍 Analyse", type="primary", use_container_width=True)

    if run_analysis:
        if from_date > to_date:
            st.error("'From' date must be before 'To' date.")
            st.stop()

        # ── Load sheet ──
        with st.spinner("Loading Google Sheet..."):
            try:
                all_sheets = fetch_all_sheets(sheet_url)
            except Exception as e:
                st.error(f"Could not load Google Sheet: {e}")
                st.stop()

        # Determine which sheets to process
        if selected_member == "All members":
            sheets_to_check = {
                name: df for name, df in all_sheets.items()
                if name in member_names
            }
        else:
            if selected_member in all_sheets:
                sheets_to_check = {selected_member: all_sheets[selected_member]}
            else:
                st.warning(
                    f"No sheet named **{selected_member}** found in the Google Sheet. "
                    "Make sure the tab name matches exactly."
                )
                st.stop()

        if not sheets_to_check:
            st.warning("No matching sheets found. Check that sheet tab names match team member names.")
            st.stop()

        # ── Process each member ──
        for member_name, df in sheets_to_check.items():
            st.divider()
            st.markdown(f"### 👤 {member_name}")

            filtered = filter_backlinks(df, from_date, to_date)

            if filtered.empty:
                st.info(f"No backlinks found for **{member_name}** in the selected date range.")
                continue

            st.caption(f"{len(filtered)} backlink(s) found — analysing...")

            results = []
            progress = st.progress(0)

            for idx, row in filtered.iterrows():
                url = str(row.get("url", ""))
                bl_type = str(row.get("type", "Unknown"))
                project = str(row.get("project", ""))
                bl_date = str(row.get("date", ""))[:10]

                with st.spinner(f"Checking {url[:60]}..."):
                    page_data = fetch_page_data(url)
                    analysis = analyze_backlink(row.to_dict(), page_data)

                results.append({
                    "url": url,
                    "type": bl_type,
                    "project": project,
                    "date": bl_date,
                    "page_data": page_data,
                    "analysis": analysis,
                })
                progress.progress((idx + 1) / len(filtered))

            progress.empty()

            # ── Display results ──
            for r in results:
                a = r["analysis"]
                score = a.get("quality_score", 0)
                label = a.get("quality_label", "Unknown")
                color = score_color(score)

                with st.expander(
                    f"**{r['type']}** — {r['url'][:70]}  |  Score: {score}/10 ({label})",
                    expanded=False,
                ):
                    col_score, col_verdict = st.columns([1, 4])
                    with col_score:
                        st.metric("Quality Score", f"{score}/10")
                        st.markdown(f"**{label}**")
                    with col_verdict:
                        st.markdown(f"**Verdict:** {a.get('verdict', '')}")
                        st.markdown(f"*{a.get('type_assessment', '')}*")

                    st.markdown("**HTML Structure**")
                    html_s = a.get("html_structure", {})
                    cols = st.columns(3)
                    for i, (key, val) in enumerate(html_s.items()):
                        cols[i % 3].markdown(f"**{key.replace('_', ' ').title()}:** {val}")

                    col_str, col_iss, col_sug = st.columns(3)
                    with col_str:
                        st.markdown("**✅ Strengths**")
                        for s in a.get("strengths", []):
                            st.markdown(f"- {s}")
                    with col_iss:
                        st.markdown("**❌ Issues**")
                        for s in a.get("issues", []):
                            st.markdown(f"- {s}")
                    with col_sug:
                        st.markdown("**💡 Suggestions**")
                        for s in a.get("suggestions", []):
                            st.markdown(f"- {s}")

            # ── Email report button ──
            member_email = member_email_map.get(member_name)
            if member_email and results:
                if st.button(f"📧 Email Report to {member_name}", key=f"email_{member_name}"):
                    with st.spinner("Sending email..."):
                        try:
                            _send_backlink_report(member_name, member_email, results, from_date, to_date)
                            st.success(f"Report sent to {member_email}")
                        except Exception as e:
                            st.error(f"Failed to send email: {e}")


def _send_backlink_report(member_name, member_email, results, from_date, to_date):
    gmail_user = os.getenv("GMAIL_EMAIL")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")

    subject = f"Backlink Analysis Report — {member_name} ({from_date} to {to_date})"

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
    <h2>Backlink Analysis Report</h2>
    <p><strong>Team Member:</strong> {member_name}</p>
    <p><strong>Period:</strong> {from_date} to {to_date}</p>
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
    <br><p style="color:#888;font-size:12px;">Sent by Team Accountability Agent</p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = member_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, member_email, msg.as_string())


# ══════════════════════════════════════════════════════════════════════════════
# TEAM TAB
# ══════════════════════════════════════════════════════════════════════════════
with team_tab:
    st.subheader("Team Members")

    team = load_team()

    # ── Add member ──
    with st.expander("➕ Add New Member", expanded=(len(team) == 0)):
        with st.form("add_member_form", clear_on_submit=True):
            col_n, col_e = st.columns(2)
            with col_n:
                new_name = st.text_input("Name", placeholder="e.g. Priya Sharma")
            with col_e:
                new_email = st.text_input("Email", placeholder="e.g. priya@company.com")
            if st.form_submit_button("Add Member", type="primary"):
                if new_name.strip() and new_email.strip():
                    add_member(new_name.strip(), new_email.strip().lower())
                    st.success(f"Added {new_name.strip()}")
                    st.rerun()
                else:
                    st.error("Both name and email are required.")

    st.divider()

    # ── Member list ──
    if not team:
        st.info("No team members yet. Use the form above to add your first member.")
    else:
        for i, member in enumerate(team):
            col_info, col_edit, col_del = st.columns([5, 1, 1])

            with col_info:
                st.markdown(f"**{member['name']}**  \n`{member['email']}`")

            with col_edit:
                if st.button("✏️ Edit", key=f"edit_btn_{i}", use_container_width=True):
                    st.session_state[f"editing_{i}"] = True

            with col_del:
                if st.button("🗑️", key=f"del_btn_{i}", use_container_width=True):
                    delete_member(i)
                    st.rerun()

            # Inline edit form
            if st.session_state.get(f"editing_{i}"):
                with st.form(f"edit_form_{i}"):
                    col_en, col_ee = st.columns(2)
                    with col_en:
                        edit_name = st.text_input("Name", value=member["name"])
                    with col_ee:
                        edit_email = st.text_input("Email", value=member["email"])
                    col_save, col_cancel = st.columns(2)
                    with col_save:
                        if st.form_submit_button("Save", type="primary"):
                            update_member(i, edit_name.strip(), edit_email.strip().lower())
                            del st.session_state[f"editing_{i}"]
                            st.rerun()
                    with col_cancel:
                        if st.form_submit_button("Cancel"):
                            del st.session_state[f"editing_{i}"]
                            st.rerun()

            st.divider()
