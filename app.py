"""
Personal Job Copilot — private, local-use Streamlit app.

Views:
  1. Dashboard  — country fit / salary / cost-of-living surplus overview
  2. Companies  — browse & filter matched roles, like/dislike, apply+track, tailor CV
  3. Profile    — preferences, master CV files, liked/disliked list, application tracker

Run locally with:
    streamlit run app.py

Requires a `.streamlit/secrets.toml` file with:
    GROQ_API_KEY = "your-key-here"
(Never paste your real key into chat or commit it to a repo — rotate it if you ever do.)
"""

import streamlit as st
import json
import os
import re
import hashlib
import time
from datetime import datetime, timedelta

import pandas as pd
import pypdf
import requests
from groq import Groq

try:
    from ddgs import DDGS  # current package name
except ImportError:
    # fallback for older environments that still only have the deprecated package
    from duckduckgo_search import DDGS  # noqa: F401

# --------------------------------------------------------------------------------------
# CONFIG & THEME  ("Welcome to the Jungle"-inspired: dark canvas, warm accent, bold type)
# --------------------------------------------------------------------------------------
st.set_page_config(page_title="Job Copilot", layout="wide", page_icon="🐆")

ACCENT = "#FF6B4A"
ACCENT_SOFT = "#FFB199"
BG = "#0F1115"
CARD = "#181B21"
BORDER = "#2A2E37"

st.markdown(f"""
<style>
.stApp {{ background-color: {BG}; }}
h1, h2, h3 {{ font-weight: 800 !important; letter-spacing: -0.02em; }}
.stMetric {{ background:{CARD}; padding:16px; border-radius:14px; border:1px solid {BORDER}; }}
.card {{
    background:{CARD}; border:1px solid {BORDER}; border-radius:16px;
    padding:22px 24px; margin-bottom:18px; transition:0.2s;
}}
.card:hover {{ border-color:{ACCENT}; }}
.fit-badge {{
    display:inline-block; background:{ACCENT}; color:#111; font-weight:800;
    padding:4px 12px; border-radius:20px; font-size:13px;
}}
.pill {{
    display:inline-block; background:#232730; color:{ACCENT_SOFT}; font-size:12px;
    padding:2px 10px; border-radius:12px; margin-right:6px; border:1px solid {BORDER};
}}
.small-muted {{ color:#8b909c; font-size:13px; }}
.stButton>button {{ border-radius:10px; border:1px solid {BORDER}; }}
.stButton>button:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
.last-updated {{ color:#8b909c; font-size:12px; font-style:italic; }}
</style>
""", unsafe_allow_html=True)

DB_PATH = "copilot_db.json"
DEFAULT_COUNTRIES = ["Germany", "Netherlands", "United Kingdom", "Belgium", "France",
                     "Switzerland", "Ireland", "Spain", "Portugal", "Poland", "Czech Republic",
                     "Sweden", "Denmark", "Austria", "Italy", "Canada", "United States"]

COUNTRY_FLAGS = {
    "Germany": "🇩🇪", "Netherlands": "🇳🇱", "United Kingdom": "🇬🇧", "Belgium": "🇧🇪",
    "France": "🇫🇷", "Switzerland": "🇨🇭", "Ireland": "🇮🇪", "Spain": "🇪🇸",
    "Portugal": "🇵🇹", "Canada": "🇨🇦", "Poland": "🇵🇱", "Czech Republic": "🇨🇿",
    "Sweden": "🇸🇪", "Denmark": "🇩🇰", "Austria": "🇦🇹", "Italy": "🇮🇹", "United States": "🇺🇸",
}

REGIONS = {
    "Europe": ["Germany", "Netherlands", "United Kingdom", "Belgium", "France", "Switzerland",
               "Ireland", "Spain", "Portugal", "Poland", "Czech Republic", "Sweden", "Denmark",
               "Austria", "Italy"],
    "North America": ["Canada", "United States"],
    "DACH (Germany/Austria/Switzerland)": ["Germany", "Austria", "Switzerland"],
    "Nordics": ["Sweden", "Denmark"],
    "Benelux": ["Belgium", "Netherlands"],
}

VISA_ICON = {"green": "✅", "yellow": "⚠️", "red": "❌"}

DEFAULT_DB = {
    "profile": {
        "role": "Software Engineer",
        "seniority": "Mid-level",
        "countries": ["Germany", "Netherlands", "United Kingdom"],
        "salary_floor": 55000,
        "work_mode": "Hybrid",
        "avoid_industries": "",
        "lifestyle": "Single, mid-tier city",
        "nationality": "",
        "visa_status": "EU/EEA citizen (no visa needed)",
        "languages": "English",
    },
    "cv_text": "",
    "tex_source": "",
    "cv_skills": [],
    "cv_meta_updated": None,
    "companies": {},        # id -> role/company record incl. liked/status/date
    "country_cache": {},    # country -> ranking/market data, see refresh_country_data
}


def load_db():
    if not os.path.exists(DB_PATH):
        return json.loads(json.dumps(DEFAULT_DB))
    try:
        with open(DB_PATH, "r") as f:
            data = json.load(f)
        # backfill any missing top-level and nested profile keys as the schema grows
        for k, v in DEFAULT_DB.items():
            data.setdefault(k, v)
        for k, v in DEFAULT_DB["profile"].items():
            data["profile"].setdefault(k, v)
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_DB))


def save_db(data):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


if "db" not in st.session_state:
    st.session_state.db = load_db()
db = st.session_state.db


def country_picker(label, key, default):
    """Country multiselect with a 'quick add region' control next to it, so picking
    'Europe' or 'North America' adds every country in that region in one click instead
    of selecting each one individually. The value lives in session_state[key] so the
    region-add button can update it and have the multiselect reflect it on rerun."""
    if key not in st.session_state:
        st.session_state[key] = [c for c in default if c in DEFAULT_COUNTRIES]
    else:
        st.session_state[key] = [c for c in st.session_state[key] if c in DEFAULT_COUNTRIES]

    col1, col2 = st.columns([2, 1])
    with col2:
        region_pick = st.selectbox("Quick add region", ["—"] + list(REGIONS.keys()), key=f"{key}_region_pick")
        if st.button("➕ Add region", key=f"{key}_region_btn", use_container_width=True):
            if region_pick != "—":
                merged = list(dict.fromkeys(st.session_state[key] + REGIONS[region_pick]))
                st.session_state[key] = merged
                st.rerun()
    with col1:
        selected = st.multiselect(label, DEFAULT_COUNTRIES, key=key)
    return selected


def get_client():
    key = st.secrets.get("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    if not key:
        st.error("No GROQ_API_KEY found in .streamlit/secrets.toml — add it there (never paste it into chat).")
        st.stop()
    return Groq(api_key=key)


def job_id(href, title):
    return hashlib.sha1(f"{href}|{title}".encode()).hexdigest()[:12]


# --------------------------------------------------------------------------------------
# CV PARSING — turn PDF/LaTeX into a canonical skills profile (grounds later generation)
# --------------------------------------------------------------------------------------
def extract_cv_skills(cv_text, tex_source):
    """Ask the model to extract a flat skills/keyword list strictly from the CV content.
    This list is later used for transparent, explainable fit scoring — no invented skills."""
    client = get_client()
    source = (cv_text or "") + "\n" + (tex_source or "")
    prompt = (
        "Extract a flat JSON list of concrete skills, tools, languages, and role keywords "
        "that literally appear in this CV. Do not infer or add anything not present. "
        "Return ONLY a JSON array of strings, nothing else.\n\nCV CONTENT:\n" + source[:6000]
    )
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = res.choices[0].message.content.strip()
        raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
        skills = json.loads(raw)
        return [s.strip() for s in skills if isinstance(s, str) and s.strip()]
    except Exception as e:
        st.warning(f"Skill extraction failed ({e}); falling back to naive keyword scan.")
        words = re.findall(r"[A-Za-z][A-Za-z0-9\+\#\.]{2,}", source)
        return list(dict.fromkeys(words))[:80]


# --------------------------------------------------------------------------------------
# EXPLAINABLE FIT SCORING — no black box: score = keyword overlap, matches shown to user
# --------------------------------------------------------------------------------------
def score_job_fit(job_text, cv_skills):
    """Transparent fit score: percentage based on how many of the candidate's known skills show
    up in the posting, scaled against a fixed 'strong match' threshold rather than the size of
    the candidate's whole skill list — dividing by the full CV skill count (as before) punished
    anyone with a long CV, since a short job snippet will never mention most of a 60-skill list."""
    if not cv_skills:
        return 0, []
    text_low = job_text.lower()
    matched = [s for s in cv_skills if s.lower() in text_low]
    STRONG_MATCH_COUNT = 5  # 5+ matched skills counts as a full/100% fit
    score = min(100, round(100 * len(matched) / STRONG_MATCH_COUNT))
    return score, matched


ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com",
             "workday.com", "workable.com", "personio.de", "teamtailor.com", "jobvite.com",
             "breezy.hr", "recruitee.com")

TECH_VOCAB = [
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Golang", "Rust", "Ruby", "PHP", "Scala",
    "Kotlin", "Swift", "C++", "C#", ".NET", "Node.js", "React", "Vue", "Angular", "Next.js",
    "Django", "Flask", "FastAPI", "Spring", "Rails", "AWS", "GCP", "Azure", "Docker", "Kubernetes",
    "Terraform", "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "GraphQL", "REST",
    "Machine Learning", "TensorFlow", "PyTorch",
]


def extract_company_name(href):
    """Prefer the real company name over the ATS/aggregator domain when the link is hosted
    on a third-party applicant-tracking system."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(href)
        netloc = parsed.netloc.replace("www.", "")
        if any(h in netloc for h in ATS_HOSTS):
            parts = [p for p in parsed.path.split("/") if p]
            if parts:
                return parts[0].replace("-", " ").replace("_", " ").title()
        return netloc.split(".")[0].replace("-", " ").title()
    except Exception:
        return "Unknown company"


def extract_tech_stack(title, body):
    text = f"{title} {body}"
    found = [t for t in TECH_VOCAB if re.search(rf"\b{re.escape(t)}\b", text, re.IGNORECASE)]
    return ", ".join(found[:5]) if found else "—"


LEVEL_DEFINITIONS = {
    "Internship": "Student/intern position, usually part of a degree program.",
    "Junior": "Roughly 0-2 years experience; entry-level or graduate roles.",
    "Mid": "Roughly 2-5 years experience; works independently on well-scoped work.",
    "Senior": "Roughly 5+ years experience; owns projects end-to-end, mentors others.",
    "Staff/Lead": "Senior IC or team-lead scope; sets technical direction across a team.",
    "Not specified": "Seniority wasn't detectable from the posting title.",
}
LEVEL_TOOLTIP = "\n".join(f"{k}: {v}" for k, v in LEVEL_DEFINITIONS.items())


def extract_level(title):
    """Level is read from the title only (not the body) — body text often name-drops
    unrelated seniority words ('work with senior engineers') that would misclassify a
    posting if scanned too broadly."""
    t = (title or "").lower()
    if any(k in t for k in ["intern", "internship"]):
        return "Internship"
    if any(k in t for k in ["staff", "principal", "head of", "lead "]) or t.strip().endswith("lead"):
        return "Staff/Lead"
    if any(k in t for k in ["senior", "sr.", "sr "]):
        return "Senior"
    if any(k in t for k in ["junior", "jr.", "jr ", "entry level", "entry-level", "graduate"]):
        return "Junior"
    if any(k in t for k in ["mid-level", "mid level", "intermediate"]):
        return "Mid"
    return "Not specified"


VISA_POSITIVE_PHRASES = [
    "visa sponsorship", "sponsor visa", "sponsors visas", "we sponsor", "relocation support",
    "relocation assistance", "work permit assistance", "visa support", "sponsorship available",
    "will sponsor",
]
VISA_NEGATIVE_PHRASES = [
    "no visa sponsorship", "not able to sponsor", "unable to sponsor", "cannot sponsor",
    "no sponsorship", "must have the right to work", "must already have the right to work",
    "without sponsorship",
]


def extract_visa_support(text):
    t = (text or "").lower()
    if any(p in t for p in VISA_NEGATIVE_PHRASES):
        return "No"
    if any(p in t for p in VISA_POSITIVE_PHRASES):
        return "Yes"
    return "Not specified"


SALARY_MENTION_REGEX = re.compile(
    r'(?:€|\$|£)\s?\d{1,3}(?:[.,]\d{3})*\s?[kK]?(?:\s?(?:-|–|to)\s?(?:€|\$|£)?\s?\d{1,3}(?:[.,]\d{3})*\s?[kK]?)?'
)


def extract_salary_mention(text):
    """Only returns a value if the job posting/page itself states a figure — we never want
    to present an AI estimate as if the employer said it. Returns None if nothing found."""
    if not text:
        return None
    match = SALARY_MENTION_REGEX.search(text)
    return match.group(0).strip() if match else None


def compute_apply_advice(c, profile, country_cache):
    """A separate 'should you apply' score from raw fit — factors in things fit alone
    doesn't capture: whether you'd actually need visa sponsorship here and whether this
    role offers it, and whether the salary (if known) clears your floor."""
    score = c.get("fit", 0)
    reasons = []

    needs_visa = profile.get("visa_status") != "EU/EEA citizen (no visa needed)"
    if needs_visa:
        if c.get("visa_support") == "Yes":
            score += 15
            reasons.append("mentions visa sponsorship")
        elif c.get("visa_support") == "No":
            score -= 25
            reasons.append("says no visa sponsorship")

    salary_mentioned = extract_salary_mention(c.get("title", "") + " " + c.get("body", ""))
    salary_floor = profile.get("salary_floor")
    country_est = country_cache.get(c.get("country", ""), {}).get("salary_eur")
    if not salary_mentioned and country_est and salary_floor:
        if country_est >= salary_floor:
            score += 5
            reasons.append("country avg salary clears your floor")
        else:
            score -= 10
            reasons.append("country avg salary is below your floor")

    if c.get("liked") is False:
        score -= 30
        reasons.append("you disliked this one")

    return max(0, min(100, round(score))), reasons


def fetch_page_text(url, timeout=6, max_chars=4000, max_download_bytes=300_000):
    """Best-effort fetch of a job posting's page text, for stack detection and fit scoring.
    Search snippets are often just a title restated ('Jobs at JetBrains') with no real content,
    so scoring against the snippet alone badly under-detects both tech stack and fit. Fails
    silently (returns '') on network errors, blocks, non-http links, or JS-only pages — callers
    fall back to the snippet in that case. Downloads are capped so one huge or slow page can't
    stall or blow up memory on a small hosting instance."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        resp = requests.get(
            url, timeout=timeout, stream=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobCopilot/1.0; personal use)"},
        )
        if resp.status_code != 200:
            return ""
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_download_bytes:
                break
        raw = b"".join(chunks)
        html = raw.decode(resp.encoding or "utf-8", errors="ignore")
        html = html[:max_download_bytes]  # extra safety before regex work
        html = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ",
                      html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


# --------------------------------------------------------------------------------------
# SCOUTING ENGINE — searches company career pages, prefers first-party links over boards
# --------------------------------------------------------------------------------------
CAREER_SITE_FILTER = "(site:lever.co OR site:greenhouse.io OR site:ashbyhq.com OR site:workday.com OR site:jobs.smartrecruiters.com)"
ALT_CAREER_SITE_FILTER = "(site:workable.com OR site:personio.de OR site:teamtailor.com OR site:jobvite.com OR site:breezy.hr OR site:recruitee.com)"

# Aggregators/job boards to actively keep out — these kept slipping through the old
# unrestricted fallback query, which is what caused most results to be board listings
# instead of a company's own career page.
AGGREGATOR_DOMAINS = [
    "indeed.com", "linkedin.com", "glassdoor.com", "monster.com", "ziprecruiter.com",
    "careerbuilder.com", "simplyhired.com", "jooble.org", "talent.com", "stepstone.",
    "totaljobs.com", "reed.co.uk", "cwjobs.co.uk", "adzuna.", "jobrapido.com",
    "jobisjob.", "careerjet.", "neuvoo.", "jobted.", "welcometothejungle.com",
]


def _is_aggregator(href):
    href_low = (href or "").lower()
    return any(dom in href_low for dom in AGGREGATOR_DOMAINS)


def _try_query(ddgs, query, max_results):
    """Run one query, returning (hits, error_message_or_None)."""
    try:
        hits = list(ddgs.text(query, max_results=max_results))
        return hits, None
    except Exception as e:
        return [], str(e)


def search_roles(role, countries, max_results_per_query=8, site_filter=CAREER_SITE_FILTER, query_extra=""):
    """Three-tier search per country, from strictest to loosest:
      1. Narrow: only the requested ATS host set (e.g. lever/greenhouse).
      2. Medium: BOTH known ATS host sets combined — recovers a lot of the empty-result
         cases without giving up the site restriction entirely.
      3. Open, as a last resort: no site restriction, but explicitly excludes the known
         job-board aggregators via '-site:' terms, and results are still hard-filtered
         against AGGREGATOR_DOMAINS afterward as a safety net.
    This replaced a version whose fallback query had no site restriction at all, which is
    why most results were coming from Indeed/LinkedIn/Glassdoor instead of a company's own
    career page."""
    results, errors = [], []
    exclude_terms = " ".join(f"-site:{d.rstrip('.')}" for d in AGGREGATOR_DOMAINS)

    with DDGS() as ddgs:
        for i, country in enumerate(countries):
            time.sleep(0.5 + 0.2 * (i % 3))

            narrow_query = f'{site_filter} "{role}" {country} {query_extra}'.strip()
            hits, err = _try_query(ddgs, narrow_query, max_results_per_query)

            if not hits:
                time.sleep(0.6)
                combined_filter = f"({CAREER_SITE_FILTER.strip('()')} OR {ALT_CAREER_SITE_FILTER.strip('()')})"
                medium_query = f'{combined_filter} "{role}" {country} {query_extra}'.strip()
                hits, err2 = _try_query(ddgs, medium_query, max_results_per_query)
                if not hits and err2:
                    err = err2

            if not hits:
                time.sleep(0.6)
                open_query = f"{role} jobs {country} careers {exclude_terms} {query_extra}".strip()
                hits, err3 = _try_query(ddgs, open_query, max_results_per_query)
                if not hits and err3:
                    err = err3

            if not hits and err:
                errors.append(f"{country}: {err}")
            for h in hits:
                h["_country"] = country
            results.extend(hits)

    # dedupe by href, and hard-filter out any aggregator that slipped through
    seen, deduped = set(), []
    for r in results:
        href = r.get("href") or r.get("url")
        if not href or href in seen or _is_aggregator(href):
            continue
        seen.add(href)
        r["href"] = href
        deduped.append(r)
    return deduped, errors


# --------------------------------------------------------------------------------------
# MARKET INTELLIGENCE — AI-synthesized from live search snippets, always timestamped
# --------------------------------------------------------------------------------------
def refresh_country_data(countries, role, seniority):
    client = get_client()
    prof = db["profile"]
    with DDGS() as ddgs:
        for country in countries:
            try:
                snippets = list(ddgs.text(
                    f"average {seniority} {role} salary {country} 2026 cost of living work visa", max_results=5
                ))
            except Exception:
                snippets = []
            context = "\n".join(f"- {s.get('title','')}: {s.get('body','')}" for s in snippets)[:3500]

            prompt = (
                f"You are estimating how well {country} fits THIS specific candidate for a "
                f"{seniority} {role} role. Candidate facts — nationality: {prof.get('nationality') or 'not specified'}; "
                f"visa situation: {prof['visa_status']}; languages spoken: {prof.get('languages', 'English')}; "
                f"lifestyle assumption for cost of living: '{prof['lifestyle']}'.\n\n"
                f"Using the web search snippets below (and general knowledge if snippets are thin), estimate:\n"
                f"- salary_eur: average gross ANNUAL salary in EUR for this role/level in {country}\n"
                f"- col_eur: average ANNUAL cost of living in EUR for the stated lifestyle\n"
                f"- monthly_surplus_low / monthly_surplus_high: a realistic MONTHLY surplus range in EUR "
                f"(salary minus cost of living, divided by 12, with some realistic spread)\n"
                f"- visa_speed_weeks: a short string like '2-4 weeks' reflecting how fast THIS candidate "
                f"(given their stated nationality/visa situation) could realistically get authorized to work there. "
                f"If they're already an EU/EEA citizen and the country is in the EU/EEA, speed should reflect that "
                f"no visa is needed at all — say so explicitly.\n"
                f"- visa_favorability: 'green' (fast/no visa needed), 'yellow' (moderate/some friction), or "
                f"'red' (slow or restrictive) — from THIS candidate's perspective specifically\n"
                f"- qol_stars: overall quality of life, integer 1-5\n"
                f"- remote_culture_stars: how strong remote/hybrid work culture is in this country's tech sector, integer 1-5\n"
                f"- edge: ONE short phrase (under 10 words) on why this country specifically suits or doesn't "
                f"suit THIS candidate — reference their visa situation or languages if relevant (e.g. a language "
                f"they speak unlocking a market, or their citizenship giving them fast/no-visa access)\n"
                f"- note: one sentence on what the estimate is based on\n\n"
                'Return ONLY JSON with exactly these keys: {"salary_eur": int, "col_eur": int, '
                '"monthly_surplus_low": int, "monthly_surplus_high": int, "visa_speed_weeks": str, '
                '"visa_favorability": "green"|"yellow"|"red", "qol_stars": int, "remote_culture_stars": int, '
                '"edge": str, "note": str}\n\n'
                f"SNIPPETS:\n{context if context else '(no search results found)'}"
            )
            try:
                res = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                raw = re.sub(r"^```json|```$", "", res.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
                parsed = json.loads(raw)
                salary = int(parsed.get("salary_eur", 0))
                col = int(parsed.get("col_eur", 0))
                entry = {
                    "salary_eur": salary,
                    "col_eur": col,
                    "surplus_eur": (salary - col) if salary and col else None,
                    "monthly_surplus_low": int(parsed.get("monthly_surplus_low", 0)),
                    "monthly_surplus_high": int(parsed.get("monthly_surplus_high", 0)),
                    "visa_speed_weeks": parsed.get("visa_speed_weeks", "—"),
                    "visa_favorability": parsed.get("visa_favorability", "yellow"),
                    "qol_stars": max(1, min(5, int(parsed.get("qol_stars", 3)))),
                    "remote_culture_stars": max(1, min(5, int(parsed.get("remote_culture_stars", 3)))),
                    "edge": parsed.get("edge", ""),
                    "note": parsed.get("note", ""),
                    "updated_at": datetime.now().isoformat(timespec="minutes"),
                }
            except Exception as e:
                entry = {
                    "salary_eur": 0, "col_eur": 0, "surplus_eur": None,
                    "monthly_surplus_low": 0, "monthly_surplus_high": 0,
                    "visa_speed_weeks": "—", "visa_favorability": "yellow",
                    "qol_stars": 3, "remote_culture_stars": 3,
                    "edge": "estimation failed", "note": str(e),
                    "updated_at": datetime.now().isoformat(timespec="minutes"),
                }
            db["country_cache"][country] = entry
    save_db(db)


def composite_priority_score(entry):
    """Weighted score used only to order the ranking table — surplus, QoL, remote culture,
    and visa favorability all contribute, which is why a high-salary/slow-visa country can
    rank below a lower-salary/fast-visa one, matching how people actually prioritize moves."""
    visa_bonus = {"green": 20, "yellow": 5, "red": -15}.get(entry.get("visa_favorability"), 0)
    surplus_mid = (entry.get("monthly_surplus_low", 0) + entry.get("monthly_surplus_high", 0)) / 2
    return surplus_mid * 0.5 + entry.get("qol_stars", 0) * 20 + entry.get("remote_culture_stars", 0) * 15 + visa_bonus





# --------------------------------------------------------------------------------------
# CV OPTIMIZATION — strictly grounded LaTeX edits + diff summary, no fabrication
# --------------------------------------------------------------------------------------
def optimize_cv_for_role(tex_source, cv_text, job_title, job_body):
    client = get_client()
    system = (
        "You edit LaTeX CVs. You may ONLY reorder sections/bullets, re-emphasize, and reword "
        "existing achievements/keywords to match a target role. You must NEVER invent skills, "
        "titles, employers, dates, degrees, or achievements that are not already present in the "
        "source. If the source lacks something the job wants, do not add it — omission is fine, "
        "fabrication is not."
    )
    prompt = (
        f"TARGET ROLE: {job_title}\nJOB DESCRIPTION EXCERPT:\n{job_body[:1500]}\n\n"
        f"ORIGINAL LATEX SOURCE:\n{tex_source[:6000] if tex_source else '(none provided — use CV text below)'}\n\n"
        f"ORIGINAL CV TEXT (for reference/reconciliation):\n{cv_text[:2000]}\n\n"
        'Return ONLY JSON: {"tex": "<full modified latex document>", "changes": ["short bullet describing each change made and why", "..."]}'
    )
    res = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000,
    )
    raw = res.choices[0].message.content.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
        return parsed.get("tex", ""), parsed.get("changes", [])
    except Exception:
        return raw, ["(model did not return structured JSON — showing raw output)"]


def compute_country_fit(country):
    """Live fit % for a country — always reflects whatever has been scouted so far,
    not a snapshot frozen at the last time salary/col data was refreshed."""
    country_roles = [c for c in db["companies"].values() if c.get("country") == country]
    scores = [c["fit"] for c in country_roles if "fit" in c]
    if not scores:
        return None, "no roles scouted yet"
    return round(sum(scores) / len(scores)), f"{len(scores)} scouted role(s)"


# --------------------------------------------------------------------------------------
# VIEW 1 — DASHBOARD
# --------------------------------------------------------------------------------------
def _stars(n):
    n = max(0, min(5, int(n)))
    return "★" * n + "☆" * (5 - n)


def view_dashboard():
    st.title("🌍 Country Ranking")
    prof = db["profile"]

    countries = country_picker("Countries to evaluate", "dash_countries", prof["countries"])
    if st.button("🔄 Refresh market & visa data", use_container_width=False):
        with st.spinner("Searching current salary, cost-of-living and visa data..."):
            refresh_country_data(countries, prof["role"], prof["seniority"])
        st.rerun()

    cached = [c for c in countries
              if c in db["country_cache"] and "qol_stars" in db["country_cache"][c]]
    stale = [c for c in countries
             if c in db["country_cache"] and "qol_stars" not in db["country_cache"][c]]
    if stale:
        st.warning(f"{len(stale)} countr(y/ies) have data cached from before the ranking table was added "
                   f"({', '.join(stale)}) — click **Refresh market & visa data** to re-estimate them.")
    if not cached:
        st.info("No ranking data yet — click **Refresh market & visa data** above to generate it "
                "(estimates are personalized to your visa situation and languages in Profile).")
    else:
        ranked = sorted(cached, key=lambda c: composite_priority_score(db["country_cache"][c]), reverse=True)
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}

        st.subheader("Final Ranking Table — Your Profile Specific")
        row_tpl = (
            '<tr style="border-top:1px solid {border};">'
            '<td style="padding:10px 8px;">{priority}</td>'
            '<td style="padding:10px 8px;">{flag} {country}</td>'
            '<td style="padding:10px 8px;">{surplus}</td>'
            '<td style="padding:10px 8px;">{visa_icon} {visa_weeks}</td>'
            '<td style="padding:10px 8px; color:#ffce54;">{qol}</td>'
            '<td style="padding:10px 8px; color:#ffce54;">{remote}</td>'
            '<td style="padding:10px 8px; color:#c9cdd6;">{edge}</td>'
            '</tr>'
        )
        rows_html = ""
        for i, country in enumerate(ranked):
            d = db["country_cache"][country]
            surplus_range = f"€{d['monthly_surplus_low']:,}-{d['monthly_surplus_high']:,}" \
                if d.get("monthly_surplus_high") else "—"
            rows_html += row_tpl.format(
                border=BORDER,
                priority=medals.get(i, str(i + 1)),
                flag=COUNTRY_FLAGS.get(country, "🏳️"),
                country=country,
                surplus=surplus_range,
                visa_icon=VISA_ICON.get(d.get("visa_favorability"), "⚠️"),
                visa_weeks=d.get("visa_speed_weeks", "—"),
                qol=_stars(d.get("qol_stars", 0)),
                remote=_stars(d.get("remote_culture_stars", 0)),
                edge=d.get("edge", ""),
            )

        header = (
            '<tr style="color:#8b909c; text-align:left;">'
            '<th style="padding:6px 8px;">Priority</th>'
            '<th style="padding:6px 8px;">Country</th>'
            '<th style="padding:6px 8px;">Est. Monthly Surplus</th>'
            '<th style="padding:6px 8px;">Visa Speed</th>'
            '<th style="padding:6px 8px;">QoL</th>'
            '<th style="padding:6px 8px;">Remote/Hybrid Culture</th>'
            '<th style="padding:6px 8px;">Your Edge</th>'
            '</tr>'
        )
        table_html = (
            '<table style="width:100%; border-collapse:collapse; font-size:14px;">'
            + header + rows_html + '</table>'
        )
        st.markdown(table_html, unsafe_allow_html=True)

        oldest = min(db["country_cache"][c]["updated_at"] for c in cached)
        st.markdown(f'<span class="last-updated">Data as of {oldest} — AI-estimated from live web search '
                    f'and your Profile (visa situation, languages), not a licensed data feed.</span>',
                    unsafe_allow_html=True)

    # --- Companies sub-table -------------------------------------------------------
    st.divider()
    filter_options = ["All countries"] + sorted({c["country"] for c in db["companies"].values()})
    picked = st.selectbox("Companies for", filter_options,
                           index=filter_options.index(st.session_state.get("dash_country_filter", "All countries"))
                           if st.session_state.get("dash_country_filter") in filter_options else 0)
    st.session_state["dash_country_filter"] = picked

    entries = list(db["companies"].values())
    if picked != "All countries":
        entries = [c for c in entries if c["country"] == picked]

    has_cv = bool(db["cv_skills"])
    if has_cv:
        entries.sort(key=lambda c: c.get("fit", 0), reverse=True)
    label = f"{COUNTRY_FLAGS.get(picked, '🌍')} {picked} — Companies" if picked != "All countries" \
        else "🌍 All Countries — Companies"
    st.markdown(f"#### {label}" + (" (sorted by fit to your CV)" if has_cv else ""))

    if not has_cv:
        st.caption("Upload your CV in Profile to rank these by fit — showing them in scouted order for now.")

    if not entries:
        st.info("No scouted companies yet — go to **Companies** and click **Scout new roles**.")
        return

    table_rows = [{
        "Company": c.get("company_name") or extract_company_name(c["href"]),
        "Country": c.get("country", "Unknown"),
        "Stack": c.get("stack") or extract_tech_stack(c.get("title", "") + " " + c.get("body", ""), ""),
        "Role": c.get("title", ""),
        "Fit %": f"{c.get('fit', 0)}%" if has_cv else "—",
        "Career Page": c["href"],
    } for c in entries]

    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
        column_config={"Career Page": st.column_config.LinkColumn("Career Page", display_text=None)},
    )


# --------------------------------------------------------------------------------------
# VIEW 2 — COMPANIES
# --------------------------------------------------------------------------------------
def view_companies():
    st.title("🏢 Companies & Roles")
    prof = db["profile"]

    with st.sidebar:
        st.subheader("Search")
        role = st.text_input("Target role", prof["role"])
        countries = country_picker("Countries", "companies_countries", prof["countries"])
        results_per_country = st.slider("Results per country", 5, 25, 10)
        fetch_full_page = st.checkbox("Fetch full page for better stack/fit detection (slower)", value=True)

        def _ingest(hits):
            new_count = 0
            for h in hits:
                try:
                    href = h.get("href")
                    if not href:
                        continue
                    jid = job_id(href, h.get("title", ""))
                    if jid in db["companies"]:
                        continue  # never re-show something already tracked
                    text_for_scoring = h.get("title", "") + " " + h.get("body", "")
                    if fetch_full_page:
                        page_text = fetch_page_text(href)
                        if page_text:
                            text_for_scoring += " " + page_text
                    fit, matched = score_job_fit(text_for_scoring, db["cv_skills"])
                    db["companies"][jid] = {
                        "title": h.get("title", "Untitled role"),
                        "body": h.get("body", ""),
                        "href": href,
                        "country": h.get("_country", "Unknown"),
                        "company_name": extract_company_name(href),
                        "stack": extract_tech_stack(text_for_scoring, ""),
                        "level": extract_level(h.get("title", "")),
                        "visa_support": extract_visa_support(text_for_scoring),
                        "salary_mentioned": extract_salary_mention(text_for_scoring),
                        "fit": fit,
                        "matched_skills": matched,
                        "liked": None,
                        "status": None,
                        "logged_at": None,
                    }
                    new_count += 1
                except Exception:
                    continue
            return new_count

        if st.button("🔭 Scout new roles", use_container_width=True):
            if not db["cv_skills"]:
                st.warning("Upload & sync your CV in Profile first for real fit scoring.")
            with st.spinner(f"Searching company career pages for {role}..."):
                hits, errors = search_roles(role, countries, max_results_per_query=results_per_country)
                new_count = _ingest(hits)
                save_db(db)
                if errors:
                    st.warning(f"{len(errors)} search querie(s) failed (rate-limited or blocked): "
                               + "; ".join(errors[:3]))
                st.success(f"Found {len(hits)} candidate roles ({new_count} new).")

        if st.button("🔁 Load more (different job boards)", use_container_width=True):
            with st.spinner("Searching additional ATS platforms for more roles..."):
                hits, errors = search_roles(role, countries, max_results_per_query=results_per_country,
                                             site_filter=ALT_CAREER_SITE_FILTER)
                new_count = _ingest(hits)
                save_db(db)
                if errors:
                    st.warning(f"{len(errors)} search querie(s) failed: " + "; ".join(errors[:3]))
                st.success(f"Found {len(hits)} candidate roles ({new_count} new).")

        if st.button("♻️ Recompute stack & fit for existing entries", use_container_width=True):
            with st.spinner("Re-scoring already-scouted roles with the improved detection..."):
                updated, failed = 0, 0
                for jid, c in list(db["companies"].items()):
                    try:
                        text_for_scoring = c.get("title", "") + " " + c.get("body", "")
                        if fetch_full_page:
                            page_text = fetch_page_text(c.get("href", ""))
                            if page_text:
                                text_for_scoring += " " + page_text
                        fit, matched = score_job_fit(text_for_scoring, db["cv_skills"])
                        c["fit"] = fit
                        c["matched_skills"] = matched
                        c["stack"] = extract_tech_stack(text_for_scoring, "")
                        c["level"] = extract_level(c.get("title", ""))
                        c["visa_support"] = extract_visa_support(text_for_scoring)
                        c["salary_mentioned"] = extract_salary_mention(text_for_scoring)
                        c["company_name"] = c.get("company_name") or extract_company_name(c.get("href", ""))
                        updated += 1
                    except Exception:
                        failed += 1
                        continue
                save_db(db)
                msg = f"Recomputed {updated} entries."
                if failed:
                    msg += f" Skipped {failed} that errored out."
                st.success(msg)

        st.divider()
        filter_country = st.selectbox(
            "Filter by country", ["All"] + sorted({c["country"] for c in db["companies"].values()}),
            index=(["All"] + sorted({c["country"] for c in db["companies"].values()})).index(
                st.session_state.get("companies_country_filter", "All")
            ) if st.session_state.get("companies_country_filter") in
                 (["All"] + sorted({c["country"] for c in db["companies"].values()})) else 0,
        )
        min_fit = st.slider("Minimum fit %", 0, 100, 0)
        liked_only = st.checkbox("Liked only")
        hide_decided = st.checkbox("Hide applied/skipped", value=True)
        level_filter = st.multiselect(
            "Level", ["Internship", "Junior", "Mid", "Senior", "Staff/Lead", "Not specified"],
            default=[], help=LEVEL_TOOLTIP,
        )
        visa_filter = st.selectbox("Visa support mentioned", ["Any", "Yes", "No"])

    entries = [(jid, c) for jid, c in db["companies"].items()]
    if filter_country != "All":
        entries = [(j, c) for j, c in entries if c["country"] == filter_country]
    if liked_only:
        entries = [(j, c) for j, c in entries if c.get("liked")]
    if hide_decided:
        entries = [(j, c) for j, c in entries if not c.get("status")]
    if level_filter:
        entries = [(j, c) for j, c in entries if c.get("level", "Not specified") in level_filter]
    if visa_filter != "Any":
        entries = [(j, c) for j, c in entries if c.get("visa_support") == visa_filter]
    entries = [(j, c) for j, c in entries if c.get("fit", 0) >= min_fit]
    entries.sort(key=lambda jc: jc[1].get("fit", 0), reverse=True)

    if not entries:
        st.info("No roles match these filters yet. Use **Scout new roles** in the sidebar.")
        return

    for jid, c in entries:
        with st.container():
            pills = ''.join(f'<span class="pill">{s}</span>' for s in c.get('matched_skills', [])[:8])
            body_preview = c['body'][:280] + ('...' if len(c['body']) > 280 else '')

            level = c.get("level", "Not specified")
            visa = c.get("visa_support", "Not specified")
            visa_icon = {"Yes": "✅", "No": "❌", "Not specified": "❔"}.get(visa, "❔")

            salary_mentioned = c.get("salary_mentioned")
            country_est = db["country_cache"].get(c.get("country", ""), {}).get("salary_eur")
            if salary_mentioned:
                salary_line = f"💰 {salary_mentioned} <span class='small-muted'>(stated in posting)</span>"
            elif country_est:
                salary_line = (f"💰 ~€{country_est:,}/yr <span class='small-muted'>"
                               f"(AI estimate — {c.get('country','this country')} average, not stated by employer)</span>")
            else:
                salary_line = "💰 <span class='small-muted'>Not stated — refresh market data for this country to get an estimate</span>"

            advice_pct, advice_reasons = compute_apply_advice(c, prof, db["country_cache"])
            advice_color = ACCENT if advice_pct >= 60 else ("#ffce54" if advice_pct >= 35 else "#8b909c")
            advice_tooltip = "; ".join(advice_reasons) if advice_reasons else "based on fit alone"

            card_html = (
                '<div class="card">'
                '<div style="display:flex; justify-content:space-between; align-items:center;">'
                f'<h3 style="margin:0;">{c["title"]}</h3>'
                f'<span class="fit-badge">{c.get("fit", 0)}% fit</span>'
                '</div>'
                f'<p class="small-muted">📍 {c["country"]}</p>'
                f'<div style="margin:8px 0;">'
                f'<span class="pill">🎚 {level}</span>'
                f'<span class="pill">{visa_icon} Visa: {visa}</span>'
                f'<span class="pill" title="{advice_tooltip}" '
                f'style="color:{advice_color}; border-color:{advice_color};">👉 Apply advice: {advice_pct}%</span>'
                '</div>'
                f'<p style="margin:6px 0;">{salary_line}</p>'
                f'<p style="color:#c9cdd6; margin:10px 0;">{body_preview}</p>'
                f'<div>{pills}</div>'
                '</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

            b1, b2, b3, b4, b5 = st.columns([1, 1, 1, 1, 1.4])
            if b1.button("👍 Like", key=f"like_{jid}"):
                db["companies"][jid]["liked"] = True
                save_db(db)
                st.rerun()
            if b2.button("👎 Dislike", key=f"dislike_{jid}"):
                db["companies"][jid]["liked"] = False
                save_db(db)
                st.rerun()
            b3.link_button("🚀 Open career page", c["href"], use_container_width=True)

            if not c.get("status"):
                st.markdown('<span class="small-muted">After visiting the page, tell us what happened:</span>',
                            unsafe_allow_html=True)
                cc1, cc2 = st.columns(2)
                if cc1.button("✅ I applied", key=f"applied_{jid}"):
                    db["companies"][jid]["status"] = "Applied"
                    db["companies"][jid]["logged_at"] = str(datetime.now())
                    save_db(db)
                    st.toast("Logged as Applied")
                    st.rerun()
                if cc2.button("⏭️ Skipped", key=f"skipped_{jid}"):
                    db["companies"][jid]["status"] = "Skipped"
                    db["companies"][jid]["logged_at"] = str(datetime.now())
                    save_db(db)
                    st.toast("Logged as Skipped")
                    st.rerun()
            else:
                st.caption(f"Status: **{c['status']}** on {c['logged_at'][:16]}")

            if b4.button("✨ Optimize CV", key=f"tailor_{jid}"):
                if not db["tex_source"] and not db["cv_text"]:
                    st.error("Upload your CV in Profile first.")
                else:
                    with st.spinner("Tailoring CV — grounded strictly in your source document..."):
                        tex, changes = optimize_cv_for_role(db["tex_source"], db["cv_text"], c["title"], c["body"])
                    st.session_state[f"tailored_{jid}"] = (tex, changes)

            if f"tailored_{jid}" in st.session_state:
                tex, changes = st.session_state[f"tailored_{jid}"]
                with st.expander(f"Tailored CV for {c['title']}", expanded=True):
                    st.markdown("**What changed and why:**")
                    for ch in changes:
                        st.markdown(f"- {ch}")
                    st.download_button("⬇️ Download .tex", tex, file_name=f"cv_{jid}.tex", key=f"dl_{jid}")
                    st.code(tex[:3000] + ("..." if len(tex) > 3000 else ""), language="latex")
                    st.caption("Compile locally with pdflatex/Overleaf to get the PDF.")


# --------------------------------------------------------------------------------------
# VIEW 3 — PROFILE
# --------------------------------------------------------------------------------------
def view_profile():
    st.title("👤 Profile & Preferences")
    prof = db["profile"]

    tabs = st.tabs(["Preferences", "Master CV", "Liked / Disliked", "Application Tracker"])

    with tabs[0]:
        prof["countries"] = country_picker("Target countries", "profile_countries", prof["countries"])
        c1, c2 = st.columns(2)
        with c1:
            prof["role"] = st.text_input("Target role", prof["role"])
            prof["seniority"] = st.selectbox(
                "Seniority", ["Junior", "Mid-level", "Senior", "Staff/Lead"],
                index=["Junior", "Mid-level", "Senior", "Staff/Lead"].index(prof["seniority"])
                if prof["seniority"] in ["Junior", "Mid-level", "Senior", "Staff/Lead"] else 1,
            )
            prof["work_mode"] = st.selectbox("Work mode", ["Remote", "Hybrid", "Onsite"],
                                              index=["Remote", "Hybrid", "Onsite"].index(prof["work_mode"]))
            prof["salary_floor"] = st.number_input("Minimum salary (EUR/yr)", value=prof["salary_floor"], step=1000)
        with c2:
            prof["nationality"] = st.text_input("Nationality (for visa estimates)", prof["nationality"])
            prof["visa_status"] = st.selectbox(
                "Visa situation", [
                    "EU/EEA citizen (no visa needed)",
                    "Non-EU — needs work visa/sponsorship",
                    "Already hold a valid work permit",
                ],
                index=["EU/EEA citizen (no visa needed)", "Non-EU — needs work visa/sponsorship",
                       "Already hold a valid work permit"].index(prof["visa_status"])
                if prof["visa_status"] in ["EU/EEA citizen (no visa needed)", "Non-EU — needs work visa/sponsorship",
                                            "Already hold a valid work permit"] else 0,
            )
            prof["languages"] = st.text_input("Languages you speak (comma separated)", prof["languages"])
            prof["lifestyle"] = st.text_input("Lifestyle assumption (for cost-of-living calc)", prof["lifestyle"])
            prof["avoid_industries"] = st.text_input("Industries to avoid (comma separated)", prof["avoid_industries"])
        if st.button("💾 Save preferences"):
            save_db(db)
            st.success("Saved.")

    with tabs[1]:
        c1, c2 = st.columns(2)
        with c1:
            pdf = st.file_uploader("Master CV (PDF)", type="pdf")
            tex = st.file_uploader("Master Source (.tex)", type="tex")
            if st.button("💾 Sync & re-parse"):
                if pdf:
                    reader = pypdf.PdfReader(pdf)
                    db["cv_text"] = "".join(p.extract_text() or "" for p in reader.pages)
                if tex:
                    db["tex_source"] = tex.getvalue().decode("utf-8")
                if db["cv_text"] or db["tex_source"]:
                    with st.spinner("Extracting canonical skills profile..."):
                        db["cv_skills"] = extract_cv_skills(db["cv_text"], db["tex_source"])
                    db["cv_meta_updated"] = str(datetime.now())
                save_db(db)
                st.success("Synced and re-parsed.")
        with c2:
            if db["cv_skills"]:
                st.markdown("**Extracted skills/keywords (used for fit scoring):**")
                st.markdown("".join(f'<span class="pill">{s}</span>' for s in db["cv_skills"]), unsafe_allow_html=True)
                st.caption(f"Last parsed: {db.get('cv_meta_updated', 'never')}")
            else:
                st.info("No CV synced yet.")

    with tabs[2]:
        liked = [(j, c) for j, c in db["companies"].items() if c.get("liked") is True]
        disliked = [(j, c) for j, c in db["companies"].items() if c.get("liked") is False]
        lc, dc = st.columns(2)
        with lc:
            st.subheader(f"👍 Liked ({len(liked)})")
            for jid, c in liked:
                st.write(f"**{c['title']}** — {c['country']}")
                if st.button("Undo", key=f"undo_like_{jid}"):
                    db["companies"][jid]["liked"] = None
                    save_db(db)
                    st.rerun()
        with dc:
            st.subheader(f"👎 Disliked ({len(disliked)})")
            for jid, c in disliked:
                st.write(f"**{c['title']}** — {c['country']}")
                if st.button("Undo", key=f"undo_dislike_{jid}"):
                    db["companies"][jid]["liked"] = None
                    save_db(db)
                    st.rerun()

    with tabs[3]:
        logged = [c for c in db["companies"].values() if c.get("status")]
        if not logged:
            st.info("Nothing logged yet — apply or skip roles in Companies.")
        else:
            df = pd.DataFrame([{
                "Role": c["title"], "Country": c["country"], "Status": c["status"],
                "Date": c["logged_at"][:16] if c.get("logged_at") else "", "Link": c["href"],
            } for c in logged])
            m1, m2, m3 = st.columns(3)
            m1.metric("Total applied", sum(1 for c in logged if c["status"] == "Applied"))
            m2.metric("Total skipped", sum(1 for c in logged if c["status"] == "Skipped"))
            m3.metric("Response rate", "— (update manually, not tracked automatically yet)")
            st.dataframe(df, use_container_width=True, hide_index=True)
            per_country = df[df.Status == "Applied"].groupby("Country").size()
            if not per_country.empty:
                st.bar_chart(per_country)


# --------------------------------------------------------------------------------------
# NAVIGATION
# --------------------------------------------------------------------------------------
if "nav" not in st.session_state:
    st.session_state["nav"] = "Dashboard"

nav = st.sidebar.radio("Navigate", ["Dashboard", "Companies", "Profile"],
                        index=["Dashboard", "Companies", "Profile"].index(st.session_state["nav"]))
st.session_state["nav"] = nav

if nav == "Dashboard":
    view_dashboard()
elif nav == "Companies":
    view_companies()
elif nav == "Profile":
    view_profile()
