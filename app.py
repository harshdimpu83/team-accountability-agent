import streamlit as st
import os
from datetime import date
from dotenv import load_dotenv

from team_store import load_team, add_member, update_member, delete_member
from gmail_reader import fetch_emails_for_member, classify_emails
from ai_analyzer import analyze_member_tasks

load_dotenv()

# ── Auth ──────────────────────────────────────────────────────────────────────
APP_PASSWORD = os.getenv("APP_PASSWORD", "")


def check_auth():
    if not APP_PASSWORD:
        return
    if st.query_params.get("auth") == APP_PASSWORD:
        return
    pwd = st.text_input("Enter password", type="password")
    if pwd == APP_PASSWORD:
        st.query_params["auth"] = APP_PASSWORD
        st.rerun()
    elif pwd:
        st.error("Wrong password")
    st.stop()


st.set_page_config(page_title="Team Accountability", page_icon="📋", layout="wide")
check_auth()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📋 Team Accountability Agent")
st.caption("Check if your team completed what they planned.")

report_tab, team_tab = st.tabs(["📊 Daily Report", "👥 Team"])

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


def _all_done(result: dict) -> bool:
    return bool(result["comparison"]) and all(
        i.get("status") == "done" for i in result["comparison"]
    )


def _any_done(result: dict) -> bool:
    return any(i.get("status") == "done" for i in result["comparison"])


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
