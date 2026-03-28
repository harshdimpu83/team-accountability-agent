import io
import json
import os
import re
import time

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
    """Rename first 4 columns, parse dates, forward-fill merged cells, filter by date range."""
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

    # Drop repeated header rows pasted mid-sheet
    df = df[~df["date"].astype(str).str.strip().str.lower().isin(["date", "dates"])]

    # Parse dates — handles Excel datetime objects AND text like "21-Mar-2026" / "11 Aug 2023"
    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)

    # Forward-fill merged date cells
    df["date"] = df["date"].ffill()
    df = df.dropna(subset=["date"])

    if "url" not in df.columns:
        return pd.DataFrame()

    df["has_url"] = df["url"].astype(str).str.startswith("http")

    mask = (df["date"].dt.date >= start_date) & (df["date"].dt.date <= end_date)
    return df[mask].reset_index(drop=True)


# ── Type detection ─────────────────────────────────────────────────────────────

def detect_type(raw_type: str) -> str:
    """Map raw sheet type string to a canonical backlink type key."""
    t = raw_type.lower().strip()
    if any(k in t for k in ("sbm", "social bookmarking", "social bookmark", "bookmarking")):
        return "social_bookmarking"
    if "classified" in t:
        return "classified"
    if any(k in t for k in ("profile creation", "profile")):
        return "profile"
    if any(k in t for k in ("business listing", "local listing", "listing")):
        return "business_listing"
    if any(k in t for k in ("blog", "article", "guest post", "ai blog", "ai article", "blog publish")):
        return "blog_article"
    if any(k in t for k in ("forum", "q&a", "quora", "question", "answer")):
        return "forum_qa"
    if "pdf" in t:
        return "pdf"
    if any(k in t for k in ("directory", "citation", "local citation")):
        return "directory"
    return "generic"


_TYPE_LABELS = {
    "social_bookmarking": "Social Bookmarking",
    "classified": "Classified",
    "profile": "Profile Creation",
    "business_listing": "Business Listing",
    "blog_article": "Blog / Article",
    "forum_qa": "Forum / Q&A",
    "pdf": "PDF Submission",
    "directory": "Directory / Citation",
    "generic": "General",
}


# ── Page fetching ─────────────────────────────────────────────────────────────

def fetch_page_data(url: str) -> dict:
    """Fetch a URL and extract SEO-relevant HTML structure and quality signals."""
    result = {
        "url": url,
        "reachable": False,
        "status_code": None,
        "response_time_ms": None,
        "title": None,
        "meta_description": None,
        "meta_robots": None,
        "x_robots_tag": None,
        "is_indexable": None,
        "h1": [],
        "h2": [],
        "h3": [],
        "canonical": None,
        "has_schema": False,
        "word_count": 0,
        "images_total": 0,
        "images_with_alt": 0,
        "outbound_links": [],   # list of {"href": "...", "rel": "..."}
        "has_nofollow": False,  # any nofollow links on page
        "error": None,
    }

    try:
        t0 = time.time()
        resp = requests.get(
            url, headers=_HEADERS, timeout=_FETCH_TIMEOUT, allow_redirects=True
        )
        result["response_time_ms"] = int((time.time() - t0) * 1000)
        result["status_code"] = resp.status_code
        result["x_robots_tag"] = resp.headers.get("X-Robots-Tag", "")

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        result["reachable"] = True
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("title")
        result["title"] = title_tag.get_text(strip=True) if title_tag else None

        meta_desc = soup.find("meta", attrs={"name": "description"})
        result["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else None

        meta_robots = soup.find("meta", attrs={"name": re.compile("robots", re.I)})
        result["meta_robots"] = meta_robots.get("content", "").lower() if meta_robots else ""

        # Indexability — noindex in meta robots or X-Robots-Tag = not indexable
        noindex = (
            "noindex" in (result["meta_robots"] or "")
            or "noindex" in (result["x_robots_tag"] or "").lower()
        )
        result["is_indexable"] = not noindex

        result["h1"] = [h.get_text(strip=True) for h in soup.find_all("h1")]
        result["h2"] = [h.get_text(strip=True) for h in soup.find_all("h2")]
        result["h3"] = [h.get_text(strip=True) for h in soup.find_all("h3")]

        canonical = soup.find("link", rel="canonical")
        result["canonical"] = canonical.get("href") if canonical else None

        result["has_schema"] = bool(soup.find("script", attrs={"type": "application/ld+json"}))
        result["word_count"] = len(soup.get_text(separator=" ", strip=True).split())

        imgs = soup.find_all("img")
        result["images_total"] = len(imgs)
        result["images_with_alt"] = sum(1 for i in imgs if i.get("alt", "").strip())

        links = []
        for a in soup.find_all("a", href=True)[:50]:
            href = a.get("href", "")
            rel = " ".join(a.get("rel", [])) if isinstance(a.get("rel"), list) else str(a.get("rel", ""))
            links.append({"href": href, "rel": rel})
        result["outbound_links"] = links
        result["has_nofollow"] = any("nofollow" in lnk["rel"] for lnk in links)

    except requests.exceptions.Timeout:
        result["error"] = "Timeout — page too slow to respond"
        result["response_time_ms"] = _FETCH_TIMEOUT * 1000
    except requests.exceptions.ConnectionError:
        result["error"] = "Connection error — URL may be down or invalid"
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Type-specific prompts ─────────────────────────────────────────────────────

def _build_page_summary(page_data: dict) -> str:
    if not page_data["reachable"]:
        return f"Page NOT reachable — {page_data.get('error', 'Unknown error')}"

    speed = page_data.get("response_time_ms")
    speed_str = f"{speed}ms ({'Fast' if speed < 1000 else 'Slow' if speed > 3000 else 'OK'})" if speed else "Unknown"

    indexable = page_data.get("is_indexable")
    index_str = "✅ Indexable" if indexable else ("❌ Noindex (no SEO value)" if indexable is False else "Unknown")

    nofollow = page_data.get("has_nofollow", False)

    return f"""HTTP Status: {page_data['status_code']}
Response Time: {speed_str}
Indexable: {index_str}
Title: {page_data['title'] or 'MISSING'}
Meta Description: {page_data['meta_description'] or 'MISSING'} ({len(page_data.get('meta_description') or '')} chars)
H1 ({len(page_data['h1'])} found): {', '.join(page_data['h1'][:2]) or 'None'}
H2 ({len(page_data['h2'])} found): {', '.join(page_data['h2'][:3]) or 'None'}
Word Count: ~{page_data['word_count']}
Images: {page_data['images_total']} total, {page_data['images_with_alt']} with alt text
Schema Markup: {'Present' if page_data['has_schema'] else 'Not found'}
Canonical: {page_data['canonical'] or 'Not set'}
Nofollow links on page: {'Yes' if nofollow else 'No'}"""


def _get_type_prompt(canonical_type: str, type_label: str, page_summary: str, row: dict) -> str:
    project = row.get("project", "Unknown")
    url = row.get("url", "")
    date_created = str(row.get("date", ""))[:10]

    checklists = {
        "social_bookmarking": """You are auditing a SOCIAL BOOKMARKING backlink. Check these specific elements:

CHECKLIST:
- Title: Is it present? Is it keyword-rich and descriptive (min 50 chars)?
- Description: Is it present? Is it detailed enough (min 100 chars)? Does it read naturally?
- Target Link: Is the link to the client site present and clickable?
- Image: Is an image/thumbnail attached to the post?
- Hashtags: Are relevant hashtags present? Are they topic-relevant?
- Platform Quality: Is this a legitimate, high-authority bookmarking site?
- Link Type: Is the link dofollow or nofollow?

SEO CONSIDERATIONS:
- SBM links on spam sites have zero value — assess platform quality
- Posts without images get less engagement and visibility
- Short descriptions look spammy and get filtered
- Nofollow is common but dofollow is more valuable""",

        "classified": """You are auditing a CLASSIFIED AD backlink. Check these specific elements:

CHECKLIST:
- Title: Present and keyword-rich? Does it match what the business offers?
- Description: Present and detailed (min 150 chars)? Reads naturally without keyword stuffing?
- Target Link: Is the website/URL of the client present in the listing?
- Image: Is a business/product image uploaded?
- Contact Details: Is address present? Is phone number present?
- Target Keyword: Does the target keyword appear naturally in title or description?
- Category: Is the listing in the correct relevant category?

SEO CONSIDERATIONS:
- Classified sites with NAP data help local SEO significantly
- Missing contact details make the listing look incomplete and less trustworthy
- Images dramatically increase engagement and credibility""",

        "profile": """You are auditing a PROFILE CREATION backlink. Check these specific elements:

CHECKLIST:
- Profile/Logo Image: Is a profile picture or logo uploaded?
- Business Name: Is the correct business name present?
- Address: Is the full business address present?
- Contact Details: Phone number and/or email present?
- Banner Image: Is a cover/banner image uploaded?
- Business Description: Present and informative (min 100 chars)?
- Social Media Links: Are social profiles linked (Facebook, Instagram, LinkedIn etc)?
- Website Link: Is the target client website linked?
- Profile Completeness: What percentage of fields appear filled?

SEO CONSIDERATIONS:
- Incomplete profiles have low trust signals and minimal SEO value
- Profiles with images get 3x more engagement
- Consistent NAP across profiles is critical for local SEO""",

        "business_listing": """You are auditing a BUSINESS LISTING backlink. Check these specific elements:

CHECKLIST:
- Logo: Is a business logo/image present?
- Business Name: Correctly listed? Consistent with the official name?
- Address: Full address present? Consistent with other listings (NAP)?
- Contact Details: Phone and/or email present?
- Business Description: Present and detailed (min 150 chars)?
- Social Links: Are social media profiles linked?
- Category: Is the business in the most relevant and specific category?
- Website Link: Is the target site linked (dofollow preferred)?

SEO CONSIDERATIONS:
- NAP consistency across listings is critical — inconsistencies hurt local rankings
- Wrong categories reduce relevance signals
- Business listings on high-DA directories pass significant link equity""",

        "blog_article": """You are auditing a BLOG or ARTICLE backlink. Check these specific elements:

CHECKLIST:
- H1 Tag: Exactly 1 H1 present? Is it keyword-optimised?
- H2 Tags: At least 2 H2s for proper content structure?
- Meta Title: Present and keyword-rich (50-60 chars ideal)?
- Meta Description: Present and compelling (150-160 chars ideal)?
- Word Count: Minimum 500 words for SEO value? Longer is better.
- Target Keyword: Does it appear naturally in the H1 and first paragraph?
- Backlink Placement: Is the link in the body content (not footer/sidebar)?
- Images: Are relevant images present? Do they have alt text?
- Content Quality: Is the content original and informative, or thin/spun?

SEO CONSIDERATIONS:
- Guest posts and articles on relevant, high-authority sites are among the most valuable backlinks
- Thin content (under 500 words) on low-quality sites is a spam signal
- Link placement in body text passes more value than sidebar/footer links""",

        "forum_qa": """You are auditing a FORUM or Q&A backlink. Check these specific elements:

CHECKLIST:
- Answer Quality: Is the answer genuinely helpful and informative?
- Link Context: Is the link placed naturally within a relevant answer?
- Thread Relevance: Is the forum thread/question related to the client's niche?
- Spam Signals: Does the post look spammy (only a link, no real content)?
- Profile Quality: Is the profile posting the answer complete and credible?
- Platform Authority: Is this a legitimate, active forum or Q&A platform?

SEO CONSIDERATIONS:
- Forum links are almost always nofollow — but brand visibility still matters
- Helpful, genuine answers build authority; spammy link drops get removed
- Quora, Reddit, niche forums have high traffic — referral value matters too""",

        "pdf": """You are auditing a PDF SUBMISSION backlink. Check these specific elements:

CHECKLIST:
- PDF Accessibility: Is the PDF loading correctly (not 404 or blocked)?
- Platform Quality: Is this a legitimate PDF hosting platform (Issuu, SlideShare, Scribd etc)?
- PDF Title/Metadata: Is the title descriptive and keyword-relevant?
- Target Link: Is the link to the client site embedded in the PDF or description?
- Description: Is there a description/summary accompanying the PDF?
- Content Quality: Does the PDF look professional (not just a spammy link page)?

SEO CONSIDERATIONS:
- PDFs on high-authority platforms (SlideShare, Issuu) can rank in Google themselves
- The embedded link in the PDF description is usually the backlink that matters
- Low-quality PDFs on obscure platforms have negligible SEO value""",

        "directory": """You are auditing a DIRECTORY or CITATION backlink. Check these specific elements:

CHECKLIST:
- Business Name: Correctly and consistently listed (NAP consistency)?
- Address: Full address present? Matches other citations?
- Phone Number: Present and consistent with other listings?
- Category: Is the business in the most relevant category?
- Description: Present and informative?
- Website Link: Is the target URL present? Dofollow or nofollow?
- Directory Quality: Is this a legitimate, indexed directory?

SEO CONSIDERATIONS:
- NAP consistency is the #1 factor — any variation hurts local rankings
- Industry-specific directories are more valuable than generic ones
- Paid directories with dofollow links from high-DA sites pass significant equity""",

        "generic": """You are auditing a backlink of type: {type_label}. Apply general SEO best practices:

CHECKLIST:
- Page reachability and HTTP status
- Page indexability (noindex signals)
- Content quality and relevance to linked site
- Link placement and context
- Platform/domain quality and authority signals
- Technical SEO (title, meta description, heading structure)

SEO CONSIDERATIONS:
- Assess whether this backlink type is likely to pass SEO value
- Flag any spam signals or low-quality indicators
- Consider the relevance between this page and the client site"""
    }

    checklist = checklists.get(canonical_type, checklists["generic"]).replace("{type_label}", type_label)

    return f"""You are a Senior SEO Expert auditing a backlink built by an SEO team.

Project: {project}
Backlink Type: {type_label}
Date Created: {date_created}
URL: {url}

--- PAGE DATA (scraped from live URL) ---
{page_summary}

--- YOUR TASK ---
{checklist}

Return ONLY valid JSON (no markdown, no extra text):
{{
  "detected_type": "{type_label}",
  "type_assessment": "2-3 sentences assessing this specific backlink type and how well this one was built",
  "quality_score": 7,
  "quality_label": "Good",
  "checklist": {{
    "Element Name": "✅ Present" or "❌ Missing" or "⚠️ Needs improvement — reason"
  }},
  "seo_signals": {{
    "link_type": "Dofollow" or "Nofollow" or "Unknown",
    "indexable": "✅ Indexable" or "❌ Not indexable — no SEO value" or "Unknown",
    "load_speed": "{speed_label}",
    "spam_signals": "None detected" or "⚠️ specific signal found"
  }},
  "html_structure": {{
    "h1": "✅ 1 found" or "❌ Missing" or "⚠️ Multiple found",
    "h2": "✅ N found" or "❌ Missing",
    "meta_title": "✅ Present" or "❌ Missing",
    "meta_description": "✅ Present" or "❌ Missing",
    "schema": "✅ Present" or "❌ Not found"
  }},
  "strengths": ["strength 1", "strength 2"],
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["specific actionable fix 1", "specific actionable fix 2"],
  "verdict": "One sentence overall verdict on the SEO value of this backlink"
}}

quality_score: integer 1-10
quality_label: one of "Excellent", "Good", "Average", "Poor", "Very Poor", "Not Reachable"
checklist keys: use the exact element names from the checklist above
Keep suggestions specific and actionable."""


# ── Gemini SEO analysis ───────────────────────────────────────────────────────

def analyze_backlink(row: dict, page_data: dict) -> dict:
    """Ask Gemini to review this backlink as a Senior SEO Expert using type-specific prompts."""
    raw_type = str(row.get("type", "Unknown")).strip()
    canonical_type = detect_type(raw_type)
    type_label = _TYPE_LABELS.get(canonical_type, raw_type)

    page_summary = _build_page_summary(page_data)

    # Replace speed label placeholder
    speed = page_data.get("response_time_ms")
    if speed:
        speed_label = f"{speed}ms — {'Fast ✅' if speed < 1000 else 'Slow ⚠️' if speed > 3000 else 'OK'}"
    else:
        speed_label = "Unknown"

    prompt = _get_type_prompt(canonical_type, type_label, page_summary, row)
    prompt = prompt.replace("{speed_label}", speed_label)

    try:
        response = _model.generate_content(prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        return json.loads(raw)
    except Exception:
        return {
            "detected_type": type_label,
            "type_assessment": "Analysis failed — could not parse AI response",
            "quality_score": 0,
            "quality_label": "Error",
            "checklist": {},
            "seo_signals": {},
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
    if score >= 8:
        return "green"
    if score >= 6:
        return "orange"
    return "red"
