import streamlit as st
import os
from datetime import date, timedelta
from dotenv import load_dotenv

import requests as http_requests

from team_store import load_team, add_member, update_member, delete_member
from gmail_reader import fetch_emails_for_member, classify_emails
from ai_analyzer import analyze_member_tasks
from backlink_analyzer import fetch_all_sheets, filter_backlinks, fetch_page_data, analyze_backlink, score_color
from settings_store import load_settings, save_settings
from chat_store import load_chat, save_chat, clear_chat
from chat_assistant import build_context, get_response

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

report_tab, backlink_tab, chat_tab, team_tab = st.tabs(["📊 Daily Report", "🔗 Backlink Analysis", "💬 Chat", "👥 Team"])


# ── Email helper (defined before tabs so it's available when buttons are clicked) ──

def _send_backlink_report(member_name, member_email, results, from_date, to_date):
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL", "sales@vyasharsh.com")
    cc_email = os.getenv("GMAIL_EMAIL")
    if not api_key:
        raise ValueError("BREVO_API_KEY not set in environment.")

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

    to_list = [{"email": member_email}]
    cc_emails = ["tbmseoteam@gmail.com"]
    if cc_email and cc_email.lower() != member_email.lower():
        cc_emails.append(cc_email)
    cc_list = [{"email": e} for e in cc_emails if e.lower() != member_email.lower()]

    payload = {
        "sender": {"name": "Team Accountability Agent", "email": sender_email},
        "to": to_list,
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

    col_member, col_from, col_to, col_run, col_refresh = st.columns([2, 1.5, 1.5, 1, 1])
    with col_member:
        selected_member = st.selectbox("Team Member", ["All members"] + member_names)
    with col_from:
        from_date = st.date_input("From", value=date.today() - timedelta(days=1), key="bl_from")
    with col_to:
        to_date = st.date_input("To", value=date.today(), key="bl_to")
    with col_run:
        st.write("")  # vertical alignment
        run_analysis = st.button("🔍 Analyse", type="primary", use_container_width=True)
    with col_refresh:
        st.write("")  # vertical alignment
        if st.button("🔄 Refresh", use_container_width=True, help="Force reload the Google Sheet"):
            fetch_all_sheets.clear()
            st.toast("Sheet cache cleared — next analysis will reload from Google.")

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

        # ── Process each member and save to session_state ──
        st.session_state["bl_results"] = {}
        st.session_state["bl_meta"] = {
            "from_date": from_date,
            "to_date": to_date,
            "member_email_map": member_email_map,
        }

        for member_name, df in sheets_to_check.items():
            st.divider()
            st.markdown(f"### 👤 {member_name}")

            filtered = filter_backlinks(df, from_date, to_date)

            if filtered.empty:
                total_rows = len(df.dropna(how="all"))
                st.info(
                    f"No backlinks found for **{member_name}** between {from_date} and {to_date}. "
                    f"(Sheet has {total_rows} total rows — check the date range matches your data.)"
                )
                continue

            st.caption(f"{len(filtered)} backlink(s) found — analysing...")

            results = []
            progress = st.progress(0)
            total = len(filtered)

            for counter, (_, row) in enumerate(filtered.iterrows(), start=1):
                url = str(row.get("url", ""))
                bl_type = str(row.get("type", "Unknown"))
                project = str(row.get("project", ""))
                bl_date = str(row.get("date", ""))[:10]
                has_url = bool(row.get("has_url", False))

                if has_url:
                    with st.spinner(f"[{counter}/{total}] Checking {url[:60]}..."):
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
                progress.progress(counter / total)

            progress.empty()
            st.session_state["bl_results"][member_name] = results

    # ── Display results from session_state (persists when email button is clicked) ──
    if st.session_state.get("bl_results"):
        meta = st.session_state.get("bl_meta", {})
        saved_from = meta.get("from_date", "")
        saved_to = meta.get("to_date", "")
        saved_email_map = meta.get("member_email_map", {})

        for member_name, results in st.session_state["bl_results"].items():
            st.divider()
            st.markdown(f"### 👤 {member_name}")

            for r in results:
                a = r["analysis"]
                score = a.get("quality_score", 0)
                label = a.get("quality_label", "Unknown")

                detected = a.get("detected_type", r["type"])
                with st.expander(
                    f"**{detected}** — {r['url'][:65]}  |  Score: {score}/10 ({label})",
                    expanded=False,
                ):
                    col_score, col_verdict = st.columns([1, 4])
                    with col_score:
                        st.metric("Quality Score", f"{score}/10")
                        st.markdown(f"**{label}**")
                    with col_verdict:
                        st.markdown(f"**Verdict:** {a.get('verdict', '')}")
                        st.caption(a.get("type_assessment", ""))

                    # ── Type-specific checklist ──
                    checklist = a.get("checklist", {})
                    if checklist:
                        st.markdown("**📋 Type Checklist**")
                        cl_cols = st.columns(2)
                        for i, (item, status) in enumerate(checklist.items()):
                            cl_cols[i % 2].markdown(f"**{item}:** {status}")

                    # ── SEO signals ──
                    seo_sig = a.get("seo_signals", {})
                    if seo_sig:
                        st.markdown("**🔍 SEO Signals**")
                        sig_cols = st.columns(4)
                        signal_items = list(seo_sig.items())
                        for i, (key, val) in enumerate(signal_items):
                            sig_cols[i % 4].markdown(f"**{key.replace('_', ' ').title()}:** {val}")

                    # ── HTML structure ──
                    html_s = a.get("html_structure", {})
                    if html_s:
                        st.markdown("**🏗️ HTML Structure**")
                        h_cols = st.columns(3)
                        for i, (key, val) in enumerate(html_s.items()):
                            h_cols[i % 3].markdown(f"**{key.replace('_', ' ').title()}:** {val}")

                    st.divider()
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

            # ── Email button — outside if run_analysis so it works after rerun ──
            member_email = saved_email_map.get(member_name)
            if member_email and results:
                if st.button(f"📧 Email Report to {member_name}", key=f"email_{member_name}"):
                    with st.spinner("Sending email..."):
                        try:
                            _send_backlink_report(member_name, member_email, results, saved_from, saved_to)
                            st.success(f"✅ Report sent to {member_email} (CC: {os.getenv('GMAIL_EMAIL')})")
                        except Exception as e:
                            st.error(f"Failed to send email: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# CHAT TAB
# ══════════════════════════════════════════════════════════════════════════════
with chat_tab:
    st.subheader("💬 AI Assistant")
    st.caption("Ask questions about your team, backlink results, or get SEO advice.")

    # Load today's chat history from R2 into session_state (once per session)
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = load_chat(date.today())

    # ── Clear chat button ──
    col_title, col_clear = st.columns([5, 1])
    with col_clear:
        if st.button("🗑️ Clear", key="clear_chat_btn", use_container_width=True):
            st.session_state["chat_history"] = []
            clear_chat(date.today())
            st.rerun()

    # ── Render existing messages ──
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Chat input ──
    user_input = st.chat_input("Ask me anything — team, backlinks, SEO advice...")

    if user_input:
        # Add user message
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Build context from current app state
        context = build_context(
            load_team(),
            st.session_state.get("bl_results", {}),
        )

        # Get AI response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    reply, action = get_response(
                        st.session_state["chat_history"], user_input, context
                    )
                except Exception as e:
                    reply = f"Sorry, I ran into an error: {e}"
                    action = None

            st.markdown(reply)

        st.session_state["chat_history"].append({"role": "assistant", "content": reply})

        # ── Execute action if triggered ──
        if action:
            action_type = action.get("type")

            if action_type == "send_email":
                member_name = action.get("member", "")
                team = load_team()
                email_map = {m["name"].lower(): m["email"] for m in team}
                member_email = email_map.get(member_name.lower())
                bl_results = st.session_state.get("bl_results", {})

                # Find closest matching member name
                if not member_email:
                    for name, email in email_map.items():
                        if member_name.lower() in name or name in member_name.lower():
                            member_email = email
                            member_name = next(m["name"] for m in team if m["email"] == email)
                            break

                if member_email and member_name in bl_results:
                    try:
                        meta = st.session_state.get("bl_meta", {})
                        _send_backlink_report(
                            member_name, member_email,
                            bl_results[member_name],
                            meta.get("from_date", ""), meta.get("to_date", ""),
                        )
                        st.success(f"✅ Report sent to {member_email}")
                    except Exception as e:
                        st.error(f"Failed to send email: {e}")
                else:
                    st.warning(f"Could not send — no backlink results found for '{member_name}'. Run the analysis first.")

            elif action_type == "clear_chat":
                st.session_state["chat_history"] = []
                clear_chat(date.today())
                st.rerun()

        # Save updated history to R2
        save_chat(date.today(), st.session_state["chat_history"])


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
