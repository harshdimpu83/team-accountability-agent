import json
import os
import re

import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.0-flash")

_SYSTEM_PROMPT = """You are an AI assistant embedded in the Team Accountability Agent — a tool used by Harsh, a non-technical founder, to manage his SEO team.

You have two roles:
1. **Team & App Assistant** — answer questions about the team, backlink results, daily reports, and app data provided in the context below
2. **Senior SEO Expert** — give expert advice on SEO strategy, backlink quality, content, rankings, and digital marketing

CONTEXT (current app state):
{context}

CAPABILITIES YOU CAN TRIGGER:
If the user asks you to perform an action, include this at the END of your response:
[ACTION: {{"type": "send_email", "member": "member name"}}] — to send a backlink report email
[ACTION: {{"type": "clear_chat"}}] — to clear the chat history

GUIDELINES:
- Be concise and direct — Harsh is non-technical, avoid jargon
- When answering about data, be specific (numbers, names, dates)
- When giving SEO advice, be practical and actionable
- If you don't have the data to answer, say so and suggest what to do (e.g. run the analysis first)
- Only trigger an action if the user explicitly asks for it"""


def build_context(team: list, bl_results: dict) -> str:
    """Assemble current app state into a readable context string for Gemini."""
    lines = []

    if team:
        lines.append(f"TEAM MEMBERS ({len(team)}):")
        for m in team:
            lines.append(f"  - {m['name']} ({m['email']})")
    else:
        lines.append("TEAM MEMBERS: None added yet")

    lines.append("")

    if bl_results:
        lines.append("LATEST BACKLINK ANALYSIS RESULTS:")
        for member, results in bl_results.items():
            if not results:
                continue
            scored = [r for r in results if r.get("analysis", {}).get("quality_score", 0) > 0]
            avg_score = round(sum(r["analysis"]["quality_score"] for r in scored) / len(scored), 1) if scored else 0
            poor = [r for r in results if r.get("analysis", {}).get("quality_score", 0) < 6]
            lines.append(f"  {member}: {len(results)} backlinks analysed, avg score {avg_score}/10, {len(poor)} below 6/10")
            for r in results[:5]:  # show first 5 per member
                a = r.get("analysis", {})
                lines.append(f"    • [{r.get('type','?')}] {r.get('url','')[:60]} — {a.get('quality_score','?')}/10 ({a.get('quality_label','?')})")
    else:
        lines.append("BACKLINK ANALYSIS: No analysis has been run yet in this session")

    return "\n".join(lines)


def get_response(history: list, user_msg: str, context: str) -> tuple:
    """
    Send message to Gemini with full context and history.
    Returns (assistant_text, action_dict_or_None).
    """
    system = _SYSTEM_PROMPT.replace("{context}", context)

    # Build Gemini chat history (list of Content objects)
    chat_history = []
    for msg in history[:-1]:  # exclude the latest user message (sent separately)
        role = "user" if msg["role"] == "user" else "model"
        chat_history.append({"role": role, "parts": [msg["content"]]})

    chat = _model.start_chat(history=chat_history)

    full_prompt = f"{system}\n\nUser: {user_msg}"
    response = chat.send_message(full_prompt)
    text = response.text.strip()

    # Parse action if present
    action = None
    action_match = re.search(r"\[ACTION:\s*(\{.*?\})\]", text, re.DOTALL)
    if action_match:
        try:
            action = json.loads(action_match.group(1))
        except json.JSONDecodeError:
            pass
        # Remove action tag from displayed text
        text = re.sub(r"\[ACTION:\s*\{.*?\}\]", "", text, flags=re.DOTALL).strip()

    return text, action
