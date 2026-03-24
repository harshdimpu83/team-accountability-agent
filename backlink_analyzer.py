import io
import json
import os
import re

import google.generativeai as genai
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from datetime import date

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.0-flash")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_FETCH_TIMEOUT = 15


# ── Sheet reading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_sheets(sheet_url: str) -> dict:
    """Download Google Sheet as XLSX → dict of {sheet_name: DataFrame}.
    Cached for 5 minutes — use fetch_all_sheets.clear() to force a reload."""
    sheet_id = _extract_sheet_id(sheet_url)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    resp = requests.get(export_url, timeout=30)
    resp.raise_for_status()
    xl = pd.ExcelFile(io.BytesIO(resp.content))
    return {name: xl.parse(name) for name in xl.sheet_names}


def filter_backlinks(df: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    """Rename first 4 columns, parse dates, drop rows without a URL, filter by date range."""
    df = df.copy()
    cols = list(df.columns)
    rename = {}
    if len(cols) >= 1:
        rename[cols[0]] = "date"
    if len(cols) >= 2:
        rename[cols[1]] = "project"
    if len(cols) >= 3:
        rename[cols[2]] = "type"
    if len(cols) >= 4:
        rename[cols[3]] = "url"
    df = df.rename(columns=rename)

    # Drop repeated header rows pasted mid-sheet (e.g. a row with "Date" as the date value)
    df = df[~df["date"].astype(str).str.strip().str.lower().isin(["date", "dates"])]

    # Parse dates — handles Excel datetime objects directly AND text like "21-Mar-2026" / "11 Aug 2023"
    # Do NOT convert to string first — that breaks Excel datetime objects
    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)

    # Forward-fill to handle merged date cells in Google Sheets —
    # when a date cell is merged across multiple rows, only the first row gets the value;
    # all rows below it appear as NaN but belong to the same date
    df["date"] = df["date"].ffill()

    df = df.dropna(subset=["date"])

    if "url" not in df.columns:
        return pd.DataFrame()

    # Mark whether each row has a real URL (starts with http)
    df["has_url"] = df["url"].astype(str).str.startswith("http")

    mask = (df["date"].dt.date >= start_date) & (df["date"].dt.date <= end_date)
    return df[mask].reset_index(drop=True)


# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_page_data(url: str) -> dict:
    """Fetch a URL and extract SEO-relevant HTML structure."""
    result = {
        "url": url,
        "reachable": False,
        "status_code": None,
        "title": None,
        "meta_description": None,
        "h1": [],
        "h2": [],
        "h3": [],
        "canonical": None,
        "has_schema": False,
        "word_count": 0,
        "error": None,
    }

    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=_FETCH_TIMEOUT, allow_redirects=True
        )
        result["status_code"] = resp.status_code

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        result["reachable"] = True
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("title")
        result["title"] = title_tag.get_text(strip=True) if title_tag else None

        meta = soup.find("meta", attrs={"name": "description"})
        result["meta_description"] = meta.get("content", "").strip() if meta else None

        result["h1"] = [h.get_text(strip=True) for h in soup.find_all("h1")]
        result["h2"] = [h.get_text(strip=True) for h in soup.find_all("h2")]
        result["h3"] = [h.get_text(strip=True) for h in soup.find_all("h3")]

        canonical = soup.find("link", rel="canonical")
        result["canonical"] = canonical.get("href") if canonical else None

        result["has_schema"] = bool(
            soup.find("script", attrs={"type": "application/ld+json"})
        )
        result["word_count"] = len(soup.get_text(separator=" ", strip=True).split())

    except requests.exceptions.Timeout:
        result["error"] = "Timeout — page too slow"
    except requests.exceptions.ConnectionError:
        result["error"] = "Connection error — URL may be down or invalid"
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Gemini SEO analysis ───────────────────────────────────────────────────────

def analyze_backlink(row: dict, page_data: dict) -> dict:
    """Ask Gemini to review this backlink as a Senior SEO Expert."""
    backlink_type = str(row.get("type", "Unknown")).strip()
    url = row.get("url", "")
    project = row.get("project", "Unknown")
    created_date = str(row.get("date", ""))[:10]

    if page_data["reachable"]:
        h1_list = ", ".join(page_data["h1"][:3]) or "None"
        h2_list = ", ".join(page_data["h2"][:5]) or "None"
        page_context = f"""Status: Reachable (HTTP {page_data['status_code']})
Title: {page_data['title'] or 'MISSING'}
Meta Description: {page_data['meta_description'] or 'MISSING'}
H1 ({len(page_data['h1'])} found): {h1_list}
H2 ({len(page_data['h2'])} found): {h2_list}
H3 count: {len(page_data['h3'])}
Canonical: {page_data['canonical'] or 'Not set'}
Schema Markup: {'Present' if page_data['has_schema'] else 'Not found'}
Word Count: ~{page_data['word_count']}"""
    else:
        page_context = f"Status: NOT REACHABLE — {page_data.get('error', 'Unknown error')}"

    prompt = f"""You are a Senior SEO Expert auditing a backlink built by an SEO team.

Project: {project}
Backlink Type: {backlink_type}
Date Created: {created_date}
URL: {url}

Page Analysis:
{page_context}

Instructions:
1. First assess this backlink based on its TYPE. Quality standards differ by type:
   - Blog / Article: content quality, H1/H2 structure, word count (should be 500+), meta tags, link placement context
   - Social Bookmarking (SBM): description quality, platform authority, dofollow vs nofollow, profile completeness
   - PDF Submission: platform authority (Issuu, SlideShare etc), PDF indexability, embedded link quality
   - Forum / Q&A: answer helpfulness, contextual relevance, spam signals
   - Directory / Citation: category relevance, NAP data, listing completeness
   - Profile Creation: profile completeness, bio quality, link placement
   - Classified Listing: category match, description quality, contact info
   - For any other type: apply appropriate SEO best practices

2. Assess the HTML structure and on-page SEO signals
3. Check if the backlink actually delivers SEO value
4. Give specific, actionable improvement suggestions

Return ONLY valid JSON (no markdown, no extra text):
{{
  "type_assessment": "2-3 sentences: what this backlink type should deliver and how this one measures up",
  "quality_score": 7,
  "quality_label": "Good",
  "html_structure": {{
    "h1": "✅ 1 found" or "❌ Missing" or "⚠️ Multiple ({{n}} found)",
    "h2": "✅ {{n}} found" or "❌ Missing",
    "meta_title": "✅ Present" or "❌ Missing",
    "meta_description": "✅ Present" or "❌ Missing",
    "canonical": "✅ Set" or "⚠️ Not set",
    "schema": "✅ Present" or "❌ Not found"
  }},
  "strengths": ["strength 1", "strength 2"],
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["specific actionable fix 1", "specific actionable fix 2"],
  "verdict": "One sentence overall verdict on SEO value of this backlink"
}}

quality_score: integer 1–10
quality_label: one of "Excellent", "Good", "Average", "Poor", "Very Poor", "Not Reachable"
Keep suggestions specific and actionable, not generic."""

    try:
        response = _model.generate_content(prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        return json.loads(raw)
    except Exception:
        return {
            "type_assessment": "Analysis failed",
            "quality_score": 0,
            "quality_label": "Error",
            "html_structure": {},
            "strengths": [],
            "issues": ["Could not analyze — check the URL manually"],
            "suggestions": [],
            "verdict": "Analysis failed",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_sheet_id(url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Invalid Google Sheet URL. Make sure it contains /spreadsheets/d/...")
    return match.group(1)


def score_color(score: int) -> str:
    """Return a color string based on quality score."""
    if score >= 8:
        return "green"
    if score >= 6:
        return "orange"
    return "red"
