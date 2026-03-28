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
from chat_store import load_chat, save_chat, clear_chat, load_learnings, save_learnings
from chat_assistant import build_context, get_response, extract_learnings

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


# ── Session state defaults ────────────────────────────────────────────────────
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "Dashboard"
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = load_chat(date.today())
if "chat_learnings" not in st.session_state:
    st.session_state["chat_learnings"] = load_learnings()


# ── Email helper ──────────────────────────────────────────────────────────────
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


# ── Chat helper (shared by sidebar and full chat page) ───────────────────────
def _process_chat_message(user_input: str):
    """Send a message, get AI response, handle actions, save to R2."""
    # Daily learning extraction (runs once per day on first message)
    if not st.session_state.get("learnings_updated_today"):
        yesterday = date.today() - timedelta(days=1)
        yesterday_history = load_chat(yesterday)
        if yesterday_history:
            new_learnings = extract_learnings(yesterday_history)
            if new_learnings:
                updated = st.session_state["chat_learnings"] + new_learnings
                save_learnings(updated)
                st.session_state["chat_learnings"] = updated[-100:]
        st.session_state["learnings_updated_today"] = True

    st.session_state["chat_history"].append({"role": "user", "content": user_input})

    context = build_context(load_team(), st.session_state.get("bl_results", {}))

    try:
        reply, action = get_response(
            st.session_state["chat_history"],
            user_input,
            context,
            st.session_state.get("chat_learnings"),
        )
    except Exception as e:
        reply = f"Sorry, I ran into an error: {e}"
        action = None

    st.session_state["chat_history"].append({"role": "assistant", "content": reply})
    save_chat(date.today(), st.session_state["chat_history"])

    # Handle actions
    if action:
        action_type = action.get("type")
        if action_type == "send_email":
            member_name = action.get("member", "")
            team = load_team()
            email_map = {m["name"].lower(): m["email"] for m in team}
            member_email = email_map.get(member_name.lower())
            bl_results = st.session_state.get("bl_results", {})
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
                    st.toast(f"✅ Report sent to {member_email}")
                except Exception as e:
                    st.toast(f"❌ Failed to send email: {e}")
            else:
                st.toast(f"No backlink results for '{member_name}'. Run analysis first.")
        elif action_type == "clear_chat":
            st.session_state["chat_history"] = []
            clear_chat(date.today())

    return reply


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📋 Team Agent")
    st.divider()

    # ── Navigation ──
    nav_items = [
        ("📈", "Dashboard"),
        ("📊", "Daily Report"),
        ("🔗", "Backlink Analysis"),
        ("👥", "Team"),
        ("💬", "Chat"),
    ]
    for icon, name in nav_items:
        is_active = st.session_state["current_page"] == name
        btn_type = "primary" if is_active else "secondary"
        if st.button(f"{icon} {name}", use_container_width=True, key=f"nav_{name}", type=btn_type):
            st.session_state["current_page"] = name
            st.rerun()

    st.divider()

    # ── Mini Chat ──
    st.markdown("**💬 Quick Chat**")

    history = st.session_state.get("chat_history", [])
    if history:
        for msg in history[-4:]:
            prefix = "**You:**" if msg["role"] == "user" else "**AI:**"
            content = msg["content"]
            short = content[:100] + "..." if len(content) > 100 else content
            st.markdown(
                f"<div style='font-size:12px;margin-bottom:4px;'>{prefix} {short}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No messages yet.")

    with st.form("sidebar_chat_form", clear_on_submit=True):
        mini_input = st.text_input(
            "message",
            placeholder="Ask something...",
            label_visibility="collapsed",
        )
        col_send, col_open = st.columns(2)
        with col_send:
            send_clicked = st.form_submit_button("Send", use_container_width=True, type="primary")
        with col_open:
            open_chat = st.form_submit_button("Full Chat", use_container_width=True)

    if send_clicked and mini_input.strip():
        with st.spinner("Thinking..."):
            _process_chat_message(mini_input.strip())
        st.rerun()

    if open_chat:
        st.session_state["current_page"] = "Chat"
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def render_dashboard():
    st.title("📈 Dashboard")
    st.caption("Overview of your team and backlink performance.")

    team = load_team()

    # ── Section 1: Team Daily Status ──────────────────────────────────────────
    st.subheader("👥 Team Daily Status")

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        refresh_status = st.button("🔄 Refresh", key="dash_refresh_status")

    # Fetch email status (lazy — only when requested or not yet fetched today)
    cache_key = f"dash_status_{date.today().isoformat()}"
    if refresh_status or cache_key not in st.session_state:
        if team:
            with st.spinner("Checking team emails..."):
                status_data = []
                for member in team:
                    try:
                        emails = fetch_emails_for_member(member["email"], date.today())
                        morning_emails, evening_emails = classify_emails(emails)
                        status_data.append({
                            "name": member["name"],
                            "morning": bool(morning_emails),
                            "evening": bool(evening_emails),
                        })
                    except Exception:
                        status_data.append({
                            "name": member["name"],
                            "morning": None,
                            "evening": None,
                        })
            st.session_state[cache_key] = status_data
        else:
            st.session_state[cache_key] = []

    status_data = st.session_state.get(cache_key, [])

    # Metric cards
    total_members = len(team)
    morning_count = sum(1 for s in status_data if s["morning"])
    evening_count = sum(1 for s in status_data if s["evening"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Team Members", total_members)
    c2.metric("Morning Emails Today", f"{morning_count}/{total_members}")
    c3.metric("Evening Emails Today", f"{evening_count}/{total_members}")

    if status_data:
        st.divider()
        for s in status_data:
            morning_icon = "✅" if s["morning"] else ("❌" if s["morning"] is False else "⚠️")
            evening_icon = "✅" if s["evening"] else ("❌" if s["evening"] is False else "⚠️")
            col_name, col_m, col_e = st.columns([3, 2, 2])
            col_name.markdown(f"**{s['name']}**")
            col_m.markdown(f"{morning_icon} Morning")
            col_e.markdown(f"{evening_icon} Evening")
    elif team:
        st.info("Click **Refresh** to load today's email status.")
    else:
        st.info("No team members yet. Add them in the **Team** page.")

    st.divider()

    # ── Section 2: Backlink Quality Summary ───────────────────────────────────
    st.subheader("🔗 Backlink Quality Summary")

    bl_results = st.session_state.get("bl_results", {})

    if not bl_results:
        st.info("Run a **Backlink Analysis** to see quality metrics here.")
    else:
        all_results = [r for results in bl_results.values() for r in results]
        scored = [r for r in all_results if r.get("analysis", {}).get("quality_score", 0) > 0]
        avg_score = round(sum(r["analysis"]["quality_score"] for r in scored) / len(scored), 1) if scored else 0
        reachable = sum(1 for r in all_results if r.get("page_data", {}).get("reachable", False))
        indexable = sum(
            1 for r in all_results
            if not r.get("page_data", {}).get("noindex", True) and r.get("page_data", {}).get("reachable", False)
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Backlinks", len(all_results))
        m2.metric("Avg Quality Score", f"{avg_score}/10")
        m3.metric("Reachable", f"{reachable}/{len(all_results)}")
        m4.metric("Indexable", f"{indexable}/{len(all_results)}")

        # Quality distribution
        st.markdown("**Quality Distribution**")
        buckets = {"Excellent (9-10)": 0, "Good (7-8)": 0, "Average (5-6)": 0, "Poor (3-4)": 0, "Very Poor (0-2)": 0}
        for r in scored:
            s = r["analysis"]["quality_score"]
            if s >= 9:
                buckets["Excellent (9-10)"] += 1
            elif s >= 7:
                buckets["Good (7-8)"] += 1
            elif s >= 5:
                buckets["Average (5-6)"] += 1
            elif s >= 3:
                buckets["Poor (3-4)"] += 1
            else:
                buckets["Very Poor (0-2)"] += 1

        cols = st.columns(5)
        colors = ["🟢", "🔵", "🟡", "🟠", "🔴"]
        for i, (label, count) in enumerate(buckets.items()):
            cols[i].metric(f"{colors[i]} {label}", count)

    st.divider()

    # ── Section 3: Team Leaderboard ───────────────────────────────────────────
    st.subheader("🏆 Team Leaderboard")

    if not bl_results:
        st.info("Run a **Backlink Analysis** to see the leaderboard.")
    else:
        leaderboard = []
        for member_name, results in bl_results.items():
            scored = [r for r in results if r.get("analysis", {}).get("quality_score", 0) > 0]
            avg = round(sum(r["analysis"]["quality_score"] for r in scored) / len(scored), 1) if scored else 0
            best = max((r["analysis"]["quality_score"] for r in scored), default=0)
            poor = sum(1 for r in results if r.get("analysis", {}).get("quality_score", 0) < 6)
            leaderboard.append({
                "Member": member_name,
                "Backlinks": len(results),
                "Avg Score": avg,
                "Best Score": best,
                "Poor (<6)": poor,
            })
        leaderboard.sort(key=lambda x: x["Avg Score"], reverse=True)
        st.dataframe(leaderboard, use_container_width=True, hide_index=True)

    st.divider()

    # ── Section 4: Issues & Alerts ────────────────────────────────────────────
    st.subheader("⚠️ Issues & Alerts")

    if not bl_results:
        st.info("No analysis data yet.")
    else:
        not_reachable = [
            r for results in bl_results.values()
            for r in results
            if not r.get("page_data", {}).get("reachable", True) and r.get("has_url", False)
        ]
        noindex_urls = [
            r for results in bl_results.values()
            for r in results
            if r.get("page_data", {}).get("noindex", False) and r.get("page_data", {}).get("reachable", False)
        ]

        if not_reachable:
            st.markdown(f"**🔴 Not Reachable ({len(not_reachable)})**")
            for r in not_reachable[:10]:
                st.warning(f"{r['url'][:80]} — {r.get('page_data', {}).get('error', 'unreachable')}")

        if noindex_urls:
            st.markdown(f"**🟠 Noindex — No SEO Value ({len(noindex_urls)})**")
            for r in noindex_urls[:10]:
                st.warning(f"{r['url'][:80]}")

        if not not_reachable and not noindex_urls:
            st.success("No major issues found.")


# ── Daily Report ──────────────────────────────────────────────────────────────
def render_daily_report():
    st.title("📊 Daily Report")
    st.caption("Check if your team completed what they planned.")

    team = load_team()

    col_date, col_btn = st.columns([3, 1])
    with col_date:
        selected_date = st.date_input("Select date", value=date.today(), label_visibility="collapsed")
    with col_btn:
        run_report = st.button("🔍 Run Report", type="primary", use_container_width=True)

    if run_report:
        if not team:
            st.warning("No team members yet. Add them in the **Team** page first.")
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

            st.write("")


# ── Backlink Analysis ─────────────────────────────────────────────────────────
def render_backlink_analysis():
    st.title("🔗 Backlink Analysis")

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
        return

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
        st.write("")
        run_analysis = st.button("🔍 Analyse", type="primary", use_container_width=True)
    with col_refresh:
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True, help="Force reload the Google Sheet"):
            fetch_all_sheets.clear()
            st.toast("Sheet cache cleared — next analysis will reload from Google.")

    if run_analysis:
        if from_date > to_date:
            st.error("'From' date must be before 'To' date.")
            return

        with st.spinner("Loading Google Sheet..."):
            try:
                all_sheets = fetch_all_sheets(sheet_url)
            except Exception as e:
                st.error(f"Could not load Google Sheet: {e}")
                return

        if selected_member == "All members":
            sheets_to_check = {name: df for name, df in all_sheets.items() if name in member_names}
        else:
            if selected_member in all_sheets:
                sheets_to_check = {selected_member: all_sheets[selected_member]}
            else:
                st.warning(
                    f"No sheet named **{selected_member}** found. "
                    "Make sure the tab name matches exactly."
                )
                return

        if not sheets_to_check:
            st.warning("No matching sheets found. Check that sheet tab names match team member names.")
            return

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

    # ── Display results ──
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

                    checklist = a.get("checklist", {})
                    if checklist:
                        st.markdown("**📋 Type Checklist**")
                        cl_cols = st.columns(2)
                        for i, (item, status) in enumerate(checklist.items()):
                            cl_cols[i % 2].markdown(f"**{item}:** {status}")

                    seo_sig = a.get("seo_signals", {})
                    if seo_sig:
                        st.markdown("**🔍 SEO Signals**")
                        sig_cols = st.columns(4)
                        for i, (key, val) in enumerate(seo_sig.items()):
                            sig_cols[i % 4].markdown(f"**{key.replace('_', ' ').title()}:** {val}")

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

            member_email = saved_email_map.get(member_name)
            if member_email and results:
                if st.button(f"📧 Email Report to {member_name}", key=f"email_{member_name}"):
                    with st.spinner("Sending email..."):
                        try:
                            _send_backlink_report(member_name, member_email, results, saved_from, saved_to)
                            st.success(f"✅ Report sent to {member_email} (CC: {os.getenv('GMAIL_EMAIL')})")
                        except Exception as e:
                            st.error(f"Failed to send email: {e}")


# ── Team ──────────────────────────────────────────────────────────────────────
def render_team():
    st.title("👥 Team Members")

    team = load_team()

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


# ── Full Chat ─────────────────────────────────────────────────────────────────
def render_full_chat():
    st.title("💬 AI Assistant")
    st.caption("Ask questions about your team, backlink results, or get SEO advice.")

    col_title, col_clear = st.columns([5, 1])
    with col_clear:
        if st.button("🗑️ Clear", key="clear_chat_btn", use_container_width=True):
            st.session_state["chat_history"] = []
            clear_chat(date.today())
            st.rerun()

    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Ask me anything — team, backlinks, SEO advice...")

    if user_input:
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply = _process_chat_message(user_input)
            st.markdown(reply)

        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE ROUTER
# ══════════════════════════════════════════════════════════════════════════════
page = st.session_state["current_page"]

if page == "Dashboard":
    render_dashboard()
elif page == "Daily Report":
    render_daily_report()
elif page == "Backlink Analysis":
    render_backlink_analysis()
elif page == "Team":
    render_team()
elif page == "Chat":
    render_full_chat()
