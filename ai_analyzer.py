import os
import json
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.0-flash")


def analyze_member_tasks(member_name: str, morning_emails: list, evening_emails: list) -> dict:
    """
    Use Gemini to extract planned tasks from morning emails,
    completed tasks from evening emails, and compare them.
    Returns a dict with planned_tasks, completed_tasks, comparison, and summary.
    """
    morning_text = _emails_to_text(morning_emails) if morning_emails else "No morning email found."
    evening_text = _emails_to_text(evening_emails) if evening_emails else "No evening email found."

    prompt = f"""You are reviewing daily work emails for a team member named {member_name}.

MORNING EMAIL(S) — what they planned to do today:
{morning_text}

EVENING EMAIL(S) — what they reported completing today:
{evening_text}

Instructions:
1. Extract the specific tasks they PLANNED in the morning email(s).
2. Extract the specific tasks they COMPLETED in the evening email(s).
3. For each planned task, determine its status:
   - "done" → clearly completed
   - "missed" → no mention of it in the evening email
   - "partial" → started or partially done, with a short note

Return ONLY a valid JSON object with this exact structure (no extra text, no markdown):
{{
  "planned_tasks": ["task 1", "task 2"],
  "completed_tasks": ["task 1", "task 2"],
  "comparison": [
    {{"task": "task description", "status": "done"}},
    {{"task": "task description", "status": "missed"}},
    {{"task": "task description", "status": "partial", "note": "brief explanation"}}
  ],
  "summary": "Short one-line summary, e.g. '4 of 5 tasks completed'"
}}

If no morning email: set planned_tasks to [] and mention it in summary.
If no evening email: set completed_tasks to [] and mention it in summary."""

    response = _model.generate_content(prompt)
    raw = response.text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "planned_tasks": [],
            "completed_tasks": [],
            "comparison": [],
            "summary": "Could not parse AI response — check the email content.",
        }


def _emails_to_text(emails: list) -> str:
    parts = []
    for e in emails:
        time_label = f" (sent at {e['sent_time'].strftime('%H:%M')})" if e.get("sent_time") else ""
        parts.append(f"Subject: {e['subject']}{time_label}\n\n{e['body']}")
    return "\n\n---\n\n".join(parts)
