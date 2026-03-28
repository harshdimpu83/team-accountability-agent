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

LEARNINGS FROM PAST CONVERSATIONS:
{learnings}
(These are patterns observed over time — use them to give better, more personalised responses)

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
            for r in results[:5]:
                a = r.get("analysis", {})
                lines.append(f"    • [{r.get('type','?')}] {r.get('url','')[:60]} — {a.get('quality_score','?')}/10 ({a.get('quality_label','?')})")
    else:
        lines.append("BACKLINK ANALYSIS: No analysis has been run yet in this session")

    return "\n".join(lines)


def extract_learnings(history: list) -> list:
    """
    Review a day's chat history and extract reusable learnings about Harsh's
    preferences, corrections, and patterns. Returns a list of short strings.
    """
    if not history:
        return []

    # Format history as readable text
    conversation = "\n".join(
        f"{msg['role'].upper()}: {msg['content']}" for msg in history
    )

    prompt = f"""You are reviewing a chat conversation between an AI assistant and Harsh — a non-technical founder who manages an SEO team.

CONVERSATION:
{conversation}

Your task: Extract specific, reusable learnings that will help the AI give better responses to Harsh in future sessions.

Focus on:
- Communication preferences (how he likes info presented)
- Topics or team members he asks about most
- Things the AI got wrong or that Harsh corrected
- SEO topics and strategies he cares about
- Shortcuts or patterns (e.g. he always checks backlinks on Mondays)
- Any frustrations or praise about how the AI responded

Rules:
- Only extract learnings that are clearly supported by the conversation
- Each learning must be a short, actionable sentence (under 20 words)
- Skip generic observations — only specific, useful insights
- If the conversation has nothing useful, return an empty list

Return ONLY a valid JSON array of strings. Example:
["Harsh prefers bullet points over paragraphs", "Darshan is checked most frequently for backlinks"]

Return [] if nothing useful was found."""

    try:
        response = _model.generate_content(prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        result = json.loads(raw)
        return [l for l in result if isinstance(l, str) and l.strip()]
    except Exception:
        return []


def get_response(history: list, user_msg: str, context: str, learnings: list = None) -> tuple:
    """
    Send message to Gemini with full context, history, and learnings.
    Returns (assistant_text, action_dict_or_None).
    """
    if learnings:
        learnings_text = "\n".join(f"• {l}" for l in learnings)
    else:
        learnings_text = "No learnings yet — this improves automatically as you use the chat."

    system = (
        _SYSTEM_PROMPT
        .replace("{context}", context)
        .replace("{learnings}", learnings_text)
    )

    # Build Gemini chat history
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
        text = re.sub(r"\[ACTION:\s*\{.*?\}\]", "", text, flags=re.DOTALL).strip()

    return text, action
