"""
Personal Job Copilot — private, local-use Streamlit app.

Views:
  1. Dashboard  — personalized country ranking (surplus, visa speed, QoL, remote culture,
                  "your edge") + a companies table sorted by fit once your CV is synced
  2. Companies  — scout roles from LinkedIn, company career pages, and general job boards
                  (3 tabs), filter by level/visa/fit, like/dislike, log applied/skipped,
                  get a starred Match Score with "why", and generate a strictly-truthful
                  tailored CV variant per role
  3. Profile    — preferences (incl. visa situation, languages), master CV files,
                  liked/disliked list, application tracker with stats

Run locally with:
    streamlit run app.py

Requires a `.streamlit/secrets.toml` file next to this script:
    GROQ_API_KEY = "your-key-here"
Never paste a real key into a chat or commit it — rotate it if you ever do.
"""

import streamlit as st
import json
import os
import re
import hashlib
import time
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import pypdf
import requests
from groq import Groq

try:
    from ddgs import DDGS  # current package name (formerly duckduckgo_search)
except ImportError:
    from duckduckgo_search import DDGS  # noqa: F401 — fallback for older envs

# ============================================================================================
# CONFIG & THEME  ("Welcome to the Jungle"-inspired: dark canvas, warm accent, bold type)
# ============================================================================================
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
    padding:2px 10px; border-radius:12px; margin-right:6px; margin-bottom:4px; border:1px solid {BORDER};
}}
.small-muted {{ color:#8b909c; font-size:13px; }}
.stButton>button {{ border-radius:10px; border:1px solid {BORDER}; }}
.stButton>button:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
.last-updated {{ color:#8b909c; font-size:12px; font-style:italic; }}
</style>
""", unsafe_allow_html=True)

# ============================================================================================
# CONSTANTS
# ============================================================================================
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
    "companies": {},        # id -> role/company record (see _ingest for schema)
    "country_cache": {},    # country -> ranking/market data (see refresh_country_data)
}

VISA_STATUS_OPTIONS = [
    "EU/EEA citizen (no visa needed)",
    "Non-EU — needs work visa/sponsorship",
    "Already hold a valid work permit",
]

LEVEL_DEFINITIONS = {
    "Internship": "Student/intern position, usually part of a degree program.",
    "Junior": "Roughly 0-2 years experience; entry-level or graduate roles.",
    "Mid": "Roughly 2-5 years experience; works independently on well-scoped work.",
    "Senior": "Roughly 5+ years experience; owns projects end-to-end, mentors others.",
    "Staff/Lead": "Senior IC or team-lead scope; sets technical direction across a team.",
    "Not specified": "Seniority wasn't detectable from the posting title.",
}
LEVEL_TOOLTIP = "\n".join(f"{k}: {v}" for k, v in LEVEL_DEFINITIONS.items())
LEVEL_OPTIONS = list(LEVEL_DEFINITIONS.keys())

TECH_VOCAB = [
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Golang", "Rust", "Ruby", "PHP", "Scala",
    "Kotlin", "Swift", "C++", "C#", ".NET", "Node.js", "React", "Vue", "Angular", "Next.js",
    "Django", "Flask", "FastAPI", "Spring", "Rails", "AWS", "GCP", "Azure", "Docker", "Kubernetes",
    "Terraform", "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "GraphQL", "REST",
    "Machine Learning", "TensorFlow", "PyTorch",
]

# ATS/first-party hosts we treat as genuine company career pages
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com",
             "workday.com", "workable.com", "personio.de", "teamtailor.com", "jobvite.com",
             "breezy.hr", "recruitee.com")

CAREER_SITE_FILTER = "(site:lever.co OR site:greenhouse.io OR site:ashbyhq.com OR site:workday.com OR site:jobs.smartrecruiters.com)"
ALT_CAREER_SITE_FILTER = "(site:workable.com OR site:personio.de OR site:teamtailor.com OR site:jobvite.com OR site:breezy.hr OR site:recruitee.com)"

LINKEDIN_DOMAIN = "linkedin.com"
LINKEDIN_FILTER = "site:linkedin.com/jobs"

# General job-board aggregators — now a first-class scouted category of their own (its own
# tab), not something to filter out. Kept separate from LinkedIn since that gets its own tab.
JOB_BOARD_DOMAINS = [
    "indeed.com", "glassdoor.com", "monster.com", "ziprecruiter.com",
    "careerbuilder.com", "simplyhired.com", "jooble.org", "talent.com", "stepstone.",
    "totaljobs.com", "reed.co.uk", "cwjobs.co.uk", "adzuna.", "jobrapido.com",
    "jobisjob.", "careerjet.", "neuvoo.", "jobted.", "welcometothejungle.com",
]
JOB_BOARD_FILTER = "(" + " OR ".join(f"site:{d.rstrip('.')}" for d in JOB_BOARD_DOMAINS[:9]) + ")"



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

SALARY_MENTION_REGEX = re.compile(
    r'(?:€|\$|£)\s?\d{1,3}(?:[.,]\d{3})*\s?[kK]?(?:\s?(?:-|–|to)\s?(?:€|\$|£)?\s?\d{1,3}(?:[.,]\d{3})*\s?[kK]?)?'
)

GROQ_MODEL = "llama-3.3-70b-versatile"


# ============================================================================================
# PERSISTENCE
# ============================================================================================
def load_db():
    if not os.path.exists(DB_PATH):
        return json.loads(json.dumps(DEFAULT_DB))
    try:
        with open(DB_PATH, "r") as f:
            data = json.load(f)
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


# ============================================================================================
# SHARED UI HELPERS
# ============================================================================================
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


def _stars(n):
    n = max(0, min(5, int(n)))
    return "★" * n + "☆" * (5 - n)


def _json_from_response(raw):
    """Strip a ```json fence if the model added one, then parse."""
    cleaned = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


# ============================================================================================
# CV PARSING — canonical skills profile, grounds all later scoring/generation
# ============================================================================================
def extract_cv_skills(cv_text, tex_source):
    client = get_client()
    source = (cv_text or "") + "\n" + (tex_source or "")
    prompt = (
        "Extract a flat JSON list of concrete skills, tools, languages, and role keywords "
        "that literally appear in this CV. Do not infer or add anything not present. "
        "Return ONLY a JSON array of strings, nothing else.\n\nCV CONTENT:\n" + source[:6000]
    )
    try:
        res = client.chat.completions.create(
            model=GROQ_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0,
        )
        skills = _json_from_response(res.choices[0].message.content)
        return [s.strip() for s in skills if isinstance(s, str) and s.strip()]
    except Exception as e:
        st.warning(f"Skill extraction failed ({e}); falling back to naive keyword scan.")
        words = re.findall(r"[A-Za-z][A-Za-z0-9\+\#\.]{2,}", source)
        return list(dict.fromkeys(words))[:80]


# ============================================================================================
# EXPLAINABLE FIT SCORING — no black box: score = keyword overlap, matches shown to user
# ============================================================================================
def score_job_fit(job_text, cv_skills):
    """Scaled against a fixed 'strong match' threshold rather than the size of the whole CV
    skill list — dividing by total skill count punishes anyone with a long CV, since a job
    posting will never mention most of a 60-skill list even for a perfect-fit role."""
    if not cv_skills:
        return 0, []
    text_low = job_text.lower()
    matched = [s for s in cv_skills if s.lower() in text_low]
    STRONG_MATCH_COUNT = 5  # 5+ matched skills = a full/100% fit
    score = min(100, round(100 * len(matched) / STRONG_MATCH_COUNT))
    return score, matched


# ============================================================================================
# POSTING ENRICHMENT — company name, tech stack, level, visa support, stated salary
# ============================================================================================
def extract_company_name(href):
    """Prefer the real company name over the ATS/aggregator domain when the link is hosted
    on a third-party applicant-tracking system."""
    try:
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


def extract_level(title):
    """Level is read from the title only — body text often name-drops unrelated seniority
    words ('work with senior engineers') that would misclassify a posting if scanned too
    broadly."""
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


def extract_visa_support(text):
    t = (text or "").lower()
    if any(p in t for p in VISA_NEGATIVE_PHRASES):
        return "No"
    if any(p in t for p in VISA_POSITIVE_PHRASES):
        return "Yes"
    return "Not specified"


def extract_salary_mention(text):
    """Only returns a value if the posting itself states a figure — never presented as if
    the employer said it when it's actually our own estimate."""
    if not text:
        return None
    match = SALARY_MENTION_REGEX.search(text)
    return match.group(0).strip() if match else None


FRESHNESS_REGEX = re.compile(
    r'\b(today|just posted|new|(\d+)\s*(hour|hours|hr|hrs)\s*ago|(\d+)\s*(day|days)\s*ago|'
    r'(\d+)\s*(week|weeks)\s*ago|(\d+)\s*(month|months)\s*ago|yesterday)\b',
    re.IGNORECASE,
)


def extract_freshness(text):
    """Only claims a posting age if the page/snippet actually states one — never guessed.
    Returns a short display string, or None if nothing was found."""
    if not text:
        return None
    m = FRESHNESS_REGEX.search(text)
    if not m:
        return None
    phrase = m.group(0).lower()
    if phrase in ("today", "just posted", "new"):
        return "🆕 Posted today"
    if "yesterday" in phrase:
        return "📅 Posted yesterday"
    return f"📅 Posted {phrase}"


def fetch_page_text(url, timeout=6, max_chars=4000, max_download_bytes=300_000):
    """Best-effort fetch of a job posting's real page text. Search snippets are often just
    a restated title with no real content, so scoring against the snippet alone badly
    under-detects stack/level/visa/fit. Fails silently on network errors, non-http links,
    or JS-only pages. Downloads are capped so one huge/slow page can't stall the run or
    blow up memory on a small hosting instance."""
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
        html = raw.decode(resp.encoding or "utf-8", errors="ignore")[:max_download_bytes]
        html = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ",
                      html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def liked_stack_signature(companies):
    """A simple 'people who liked X also liked Y' signal, built from your own likes rather
    than other users' data (this is a private single-user tool, so there's no cross-user
    pool to learn from) — collects tech-stack tokens across everything you've liked so
    similar postings can get a small recommendation boost."""
    from collections import Counter
    counter = Counter()
    for c in companies.values():
        if c.get("liked") is True:
            stack = c.get("stack", "")
            if stack and stack != "—":
                for token in stack.split(","):
                    counter[token.strip().lower()] += 1
    return counter


def compute_match_score(c, profile, country_cache, liked_stack_counter=None):
    """The overall recommendation score shown as stars — starts from fit, then adjusts for
    things fit alone doesn't capture: visa fit, salary-floor fit, and similarity to the
    stack of companies you've already liked (a lightweight 'more like this' signal)."""
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

    if liked_stack_counter:
        stack = c.get("stack", "")
        if stack and stack != "—":
            tokens = [t.strip().lower() for t in stack.split(",")]
            overlap = sum(1 for t in tokens if t in liked_stack_counter)
            if overlap:
                boost = min(15, overlap * 5)
                score += boost
                reasons.append("similar stack to companies you liked")

    if c.get("liked") is False:
        score -= 30
        reasons.append("you disliked this one")

    score = max(0, min(100, round(score)))
    stars = max(1, min(5, round(score / 20))) if score > 0 else 0
    return score, reasons, stars


# ============================================================================================
# SCOUTING ENGINE
# ============================================================================================
def determine_source(href):
    """Classifies a posting into one of the three Companies sub-views. Checked from the URL
    so it works even for legacy entries scouted before 'source' was stored explicitly."""
    href_low = (href or "").lower()
    if LINKEDIN_DOMAIN in href_low:
        return "linkedin"
    if any(h in href_low for h in ATS_HOSTS):
        return "career_page"
    return "job_board"


def _make_ddgs(timeout=8):
    """A few older ddgs/duckduckgo_search versions don't accept a timeout kwarg — fall back
    to the no-arg constructor rather than crashing the whole scouting run over it."""
    try:
        return DDGS(timeout=timeout)
    except TypeError:
        return DDGS()


def _is_network_error(err):
    """Distinguishes 'DuckDuckGo couldn't be reached at all' from 'query genuinely returned
    nothing'. The former means retrying a different backend or a different query phrasing
    won't help — the connection itself is failing (common on cloud/shared IPs with
    restrictive egress) — so we should fail fast instead of burning time on repeats."""
    if not err:
        return False
    e = err.lower()
    return any(k in e for k in [
        "timeout", "timed out", "connect", "connection", "max retries",
        "failed to establish", "name resolution", "network is unreachable",
    ])


def _try_query(ddgs, query, max_results, backend=None):
    try:
        kwargs = {"max_results": max_results}
        if backend:
            kwargs["backend"] = backend
        hits = list(ddgs.text(query, **kwargs))
        return hits, None
    except TypeError:
        # installed ddgs version doesn't support the backend kwarg — retry without it
        try:
            hits = list(ddgs.text(query, max_results=max_results))
            return hits, None
        except Exception as e:
            return [], str(e)
    except Exception as e:
        return [], str(e)


def _try_query_multi_backend(ddgs, query, max_results, backends=(None, "lite", "html")):
    """Try the same query across a few backends before giving up — but only when it might
    plausibly help. If the first attempt fails with a connection-level error, the network
    path itself is broken and trying a different backend endpoint over the same broken
    connection won't fix it, so we stop immediately instead of tripling the wait for
    nothing."""
    last_err = None
    for backend in backends:
        hits, err = _try_query(ddgs, query, max_results, backend=backend)
        if hits:
            return hits, None
        if err:
            last_err = err
            if _is_network_error(err):
                break
            time.sleep(0.4)
    return [], last_err


def _run_country_loop(countries, query_builder, tiers_after_narrow=None, max_results=10):
    """Shared per-country search loop: tries `query_builder(country)` for the narrow query,
    then optionally each function in `tiers_after_narrow` (each taking country and returning
    a query string) if the narrow query comes back empty for non-network reasons, then one
    backoff retry of the narrow query. Aborts the whole run early after two consecutive
    connection-level failures, since retrying more countries the same way is pointless."""
    results, errors = [], []
    consecutive_network_failures = 0

    with _make_ddgs(8) as ddgs:
        for i, country in enumerate(countries):
            time.sleep(0.3)

            narrow_query = query_builder(country)
            hits, err = _try_query_multi_backend(ddgs, narrow_query, max_results)
            network_issue = _is_network_error(err)

            if not hits and not network_issue and tiers_after_narrow:
                for build_fallback in tiers_after_narrow:
                    fallback_query = build_fallback(country)
                    hits, err_f = _try_query_multi_backend(ddgs, fallback_query, max_results)
                    if hits:
                        break
                    if err_f:
                        err = err_f
                        network_issue = _is_network_error(err_f)
                    if network_issue:
                        break

            if not hits and not network_issue:
                time.sleep(1.5)
                hits, err_retry = _try_query_multi_backend(ddgs, narrow_query, max_results)
                if not hits and err_retry:
                    err = err_retry

            if not hits and err:
                errors.append(f"{country}: {err}")

            if network_issue:
                consecutive_network_failures += 1
                if consecutive_network_failures >= 2:
                    errors.append("Stopped early — connections are timing out from this network; "
                                  "the remaining countries would just repeat the same failure.")
                    break
            else:
                consecutive_network_failures = 0

            for h in hits:
                h["_country"] = country
            results.extend(hits)

    seen, deduped = set(), []
    for r in results:
        href = r.get("href") or r.get("url")
        if not href or href in seen:
            continue
        seen.add(href)
        r["href"] = href
        deduped.append(r)
    return deduped, errors


def search_career_pages(role, countries, max_results_per_query=10, site_filter=CAREER_SITE_FILTER, query_extra=""):
    """Career-pages tab: three-tier search, strictest to loosest —
      1. Narrow  — only the requested ATS host set (e.g. lever/greenhouse).
      2. Medium  — BOTH known ATS host sets combined, still fully site-restricted.
      3. Open (last resort) — no site restriction, with explicit '-site:' exclusions for
         LinkedIn and known job boards, so it doesn't just duplicate the other two tabs."""
    exclude_terms = " ".join(f"-site:{d.rstrip('.')}" for d in ([LINKEDIN_DOMAIN] + JOB_BOARD_DOMAINS))

    def narrow(country):
        return f'{site_filter} "{role}" {country} {query_extra}'.strip()

    def medium(country):
        combined_filter = f"({CAREER_SITE_FILTER.strip('()')} OR {ALT_CAREER_SITE_FILTER.strip('()')})"
        return f'{combined_filter} "{role}" {country} {query_extra}'.strip()

    def open_tier(country):
        return f"{role} jobs {country} careers {exclude_terms} {query_extra}".strip()

    hits, errors = _run_country_loop(countries, narrow, [medium, open_tier], max_results=max_results_per_query)
    hits = [h for h in hits if determine_source(h["href"]) == "career_page"]
    return hits, errors


def search_single_filter(role, countries, site_filter, max_results_per_query=10, query_extra=""):
    """Used for the LinkedIn and Job Boards tabs — a single site-restricted query per
    country (backend rotation + one backoff retry), no further fallback tiers, since the
    whole point is staying within that specific source."""
    def narrow(country):
        return f'{site_filter} "{role}" {country} {query_extra}'.strip()
    return _run_country_loop(countries, narrow, max_results=max_results_per_query)


def search_linkedin(role, countries, max_results_per_query=10):
    return search_single_filter(role, countries, LINKEDIN_FILTER, max_results_per_query)


def search_job_boards(role, countries, max_results_per_query=10):
    return search_single_filter(role, countries, JOB_BOARD_FILTER, max_results_per_query)


def build_company_record(hit, cv_skills, fetch_full_page):
    """Turn a raw search hit into a full company/role record: enrich with real page text
    when possible, then score fit and detect stack/level/visa/salary from that richer text."""
    href = hit.get("href")
    title = hit.get("title", "Untitled role")
    body = hit.get("body", "")
    text_for_scoring = f"{title} {body}"
    if fetch_full_page:
        page_text = fetch_page_text(href)
        if page_text:
            text_for_scoring += " " + page_text

    fit, matched = score_job_fit(text_for_scoring, cv_skills)
    return {
        "title": title,
        "body": body,
        "href": href,
        "country": hit.get("_country", "Unknown"),
        "source": determine_source(href),
        "company_name": extract_company_name(href),
        "stack": extract_tech_stack(text_for_scoring, ""),
        "level": extract_level(title),
        "visa_support": extract_visa_support(text_for_scoring),
        "salary_mentioned": extract_salary_mention(text_for_scoring),
        "posted_freshness": extract_freshness(text_for_scoring),
        "scouted_at": datetime.now().isoformat(timespec="minutes"),
        "fit": fit,
        "matched_skills": matched,
        "liked": None,
        "status": None,
        "logged_at": None,
    }


# ============================================================================================
# MARKET INTELLIGENCE — AI-synthesized, personalized, always timestamped
# ============================================================================================
def refresh_country_data(countries, role, seniority):
    client = get_client()
    prof = db["profile"]
    with _make_ddgs(8) as ddgs:
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
                f"- monthly_surplus_low / monthly_surplus_high: a realistic MONTHLY surplus range in EUR\n"
                f"- visa_speed_weeks: a short string like '2-4 weeks' reflecting how fast THIS candidate could "
                f"realistically get authorized to work there given their nationality/visa situation. If they're "
                f"already an EU/EEA citizen and the country is in the EU/EEA, say no visa is needed at all.\n"
                f"- visa_favorability: 'green' (fast/no visa needed), 'yellow' (moderate friction), or "
                f"'red' (slow/restrictive) — from THIS candidate's perspective\n"
                f"- qol_stars: overall quality of life, integer 1-5\n"
                f"- remote_culture_stars: how strong remote/hybrid culture is in this country's tech sector, integer 1-5\n"
                f"- edge: ONE short phrase (under 10 words) on why this country specifically suits or doesn't suit "
                f"THIS candidate — reference their visa situation or languages if relevant\n"
                f"- note: one sentence on what the estimate is based on\n\n"
                'Return ONLY JSON with exactly these keys: {"salary_eur": int, "col_eur": int, '
                '"monthly_surplus_low": int, "monthly_surplus_high": int, "visa_speed_weeks": str, '
                '"visa_favorability": "green"|"yellow"|"red", "qol_stars": int, "remote_culture_stars": int, '
                '"edge": str, "note": str}\n\n'
                f"SNIPPETS:\n{context if context else '(no search results found)'}"
            )
            try:
                res = client.chat.completions.create(
                    model=GROQ_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.3,
                )
                parsed = _json_from_response(res.choices[0].message.content)
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
    rank below a lower-salary/fast-visa one."""
    visa_bonus = {"green": 20, "yellow": 5, "red": -15}.get(entry.get("visa_favorability"), 0)
    surplus_mid = (entry.get("monthly_surplus_low", 0) + entry.get("monthly_surplus_high", 0)) / 2
    return surplus_mid * 0.5 + entry.get("qol_stars", 0) * 20 + entry.get("remote_culture_stars", 0) * 15 + visa_bonus


# ============================================================================================
# CV OPTIMIZATION — strictly grounded LaTeX edits + diff summary, no fabrication
# ============================================================================================
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
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=4000,
    )
    try:
        parsed = _json_from_response(res.choices[0].message.content)
        return parsed.get("tex", ""), parsed.get("changes", [])
    except Exception:
        return res.choices[0].message.content, ["(model did not return structured JSON — showing raw output)"]


# ============================================================================================
# VIEW 1 — DASHBOARD
# ============================================================================================
def view_dashboard():
    st.title("🌍 Country Ranking")
    prof = db["profile"]

    # --- Hero stats ------------------------------------------------------------
    companies = db["companies"]
    total_scouted = len(companies)
    total_liked = sum(1 for c in companies.values() if c.get("liked") is True)
    total_applied = sum(1 for c in companies.values() if c.get("status") == "Applied")
    countries_with_data = len(db["country_cache"])
    liked_fits = [c.get("fit", 0) for c in companies.values() if c.get("liked") is True]
    avg_liked_fit = round(sum(liked_fits) / len(liked_fits)) if liked_fits else None

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Roles scouted", total_scouted)
    h2.metric("Liked", total_liked)
    h3.metric("Applied", total_applied)
    h4.metric("Avg fit of liked roles", f"{avg_liked_fit}%" if avg_liked_fit is not None else "—")
    st.divider()

    countries = country_picker("Countries to evaluate", "dash_countries", prof["countries"])
    if st.button("🔄 Refresh market & visa data"):
        with st.spinner("Searching current salary, cost-of-living and visa data..."):
            refresh_country_data(countries, prof["role"], prof["seniority"])
        st.rerun()

    cached = [c for c in countries if c in db["country_cache"] and "qol_stars" in db["country_cache"][c]]
    stale = [c for c in countries if c in db["country_cache"] and "qol_stars" not in db["country_cache"][c]]
    if stale:
        st.warning(f"{len(stale)} countr(y/ies) have data cached from an older schema "
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
                border=BORDER, priority=medals.get(i, str(i + 1)),
                flag=COUNTRY_FLAGS.get(country, "🏳️"), country=country, surplus=surplus_range,
                visa_icon=VISA_ICON.get(d.get("visa_favorability"), "⚠️"),
                visa_weeks=d.get("visa_speed_weeks", "—"),
                qol=_stars(d.get("qol_stars", 0)), remote=_stars(d.get("remote_culture_stars", 0)),
                edge=d.get("edge", ""),
            )
        header = (
            '<tr style="color:#8b909c; text-align:left;">'
            '<th style="padding:6px 8px;">Priority</th><th style="padding:6px 8px;">Country</th>'
            '<th style="padding:6px 8px;">Est. Monthly Surplus</th><th style="padding:6px 8px;">Visa Speed</th>'
            '<th style="padding:6px 8px;">QoL</th><th style="padding:6px 8px;">Remote/Hybrid Culture</th>'
            '<th style="padding:6px 8px;">Your Edge</th></tr>'
        )
        table_html = f'<table style="width:100%; border-collapse:collapse; font-size:14px;">{header}{rows_html}</table>'
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
    liked_stack_counter = liked_stack_signature(db["companies"])
    if has_cv:
        entries.sort(key=lambda c: compute_match_score(c, prof, db["country_cache"], liked_stack_counter)[0],
                     reverse=True)
    label = f"{COUNTRY_FLAGS.get(picked, '🌍')} {picked} — Companies" if picked != "All countries" \
        else "🌍 All Countries — Companies"
    st.markdown(f"#### {label}" + (" (sorted by match score)" if has_cv else ""))
    if not has_cv:
        st.caption("Upload your CV in Profile to rank these by match — showing them in scouted order for now.")

    if not entries:
        st.info("No scouted companies yet — go to **Companies** and click **Scout all three sources**.")
        return

    source_short = {"linkedin": "🔗 LinkedIn", "career_page": "🏢 Career page", "job_board": "📋 Job board"}
    table_rows = []
    for c in entries:
        match_pct, _, stars = compute_match_score(c, prof, db["country_cache"], liked_stack_counter)
        table_rows.append({
            "Company": c.get("company_name") or extract_company_name(c["href"]),
            "Country": c.get("country", "Unknown"),
            "Source": source_short.get(c.get("source") or determine_source(c.get("href", "")), "—"),
            "Stack": c.get("stack") or extract_tech_stack(c.get("title", ""), c.get("body", "")),
            "Role": c.get("title", ""),
            "Match": ("★" * stars + "☆" * (5 - stars)) if has_cv else "—",
            "Match %": f"{match_pct}%" if has_cv else "—",
            "Link": c["href"],
        })

    st.dataframe(
        pd.DataFrame(table_rows), use_container_width=True, hide_index=True,
        column_config={"Link": st.column_config.LinkColumn("Link", display_text=None)},
    )


# ============================================================================================
# VIEW 2 — COMPANIES
# ============================================================================================
SOURCE_LABELS = {
    "linkedin": "🔗 LinkedIn Jobs",
    "career_page": "🏢 Career Pages",
    "job_board": "📋 Job Boards",
}


def _favicon_url(href):
    try:
        netloc = urlparse(href).netloc
        return f"https://www.google.com/s2/favicons?sz=64&domain={netloc}"
    except Exception:
        return ""


def _render_role_card(jid, c, prof, liked_stack_counter=None, rank=None):
    pills = ''.join(f'<span class="pill">{s}</span>' for s in c.get('matched_skills', [])[:8])
    body_preview = c['body'][:280] + ('...' if len(c['body']) > 280 else '')

    level = c.get("level", "Not specified")
    visa = c.get("visa_support", "Not specified")
    visa_icon = {"Yes": "✅", "No": "❌", "Not specified": "❔"}.get(visa, "❔")
    freshness = c.get("posted_freshness")

    salary_mentioned = c.get("salary_mentioned")
    country_est = db["country_cache"].get(c.get("country", ""), {}).get("salary_eur")
    if salary_mentioned:
        salary_line = f"💰 {salary_mentioned} <span class='small-muted'>(stated in posting)</span>"
    elif country_est:
        salary_line = (f"💰 ~€{country_est:,}/yr <span class='small-muted'>"
                       f"(AI estimate — {c.get('country','this country')} average, not stated by employer)</span>")
    else:
        salary_line = "💰 <span class='small-muted'>Not stated — refresh market data for this country to get an estimate</span>"

    match_pct, match_reasons, stars = compute_match_score(c, prof, db["country_cache"], liked_stack_counter)
    match_color = ACCENT if match_pct >= 60 else ("#ffce54" if match_pct >= 35 else "#8b909c")
    match_tooltip = "; ".join(match_reasons) if match_reasons else "based on fit alone"
    star_html = "★" * stars + "☆" * (5 - stars)

    ribbon = ('<span class="pill" style="background:#ffce54; color:#111; border-color:#ffce54; '
              'font-weight:800;">🌟 Top pick</span>') if rank is not None and rank < 3 and match_pct >= 50 else ""
    freshness_pill = f'<span class="pill">{freshness}</span>' if freshness else ""
    favicon = _favicon_url(c["href"])
    logo_html = (f'<img src="{favicon}" width="20" height="20" style="border-radius:4px; '
                 f'vertical-align:middle; margin-right:6px;" onerror="this.style.display=\'none\'">') if favicon else ""

    card_html = (
        '<div class="card">'
        '<div style="display:flex; justify-content:space-between; align-items:center;">'
        f'<h3 style="margin:0;">{logo_html}{c["title"]}</h3>'
        f'<span class="fit-badge" title="{match_tooltip}">{star_html} · {match_pct}%</span>'
        '</div>'
        f'<p class="small-muted">📍 {c["country"]} · 🏢 {c.get("company_name","")}</p>'
        f'<div style="margin:8px 0;">'
        f'{ribbon}'
        f'<span class="pill">🎚 {level}</span>'
        f'<span class="pill">{visa_icon} Visa: {visa}</span>'
        f'{freshness_pill}'
        '</div>'
        f'<p style="margin:6px 0;">{salary_line}</p>'
        f'<p style="color:#c9cdd6; margin:10px 0;">{body_preview}</p>'
        f'<div>{pills}</div>'
        '</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    b1, b2, b3, b4 = st.columns([1, 1, 1, 1])
    if b1.button("👍 Like", key=f"like_{jid}"):
        db["companies"][jid]["liked"] = True
        save_db(db)
        st.rerun()
    if b2.button("👎 Dislike", key=f"dislike_{jid}"):
        db["companies"][jid]["liked"] = False
        save_db(db)
        st.rerun()
    b3.link_button("🚀 Open link", c["href"], use_container_width=True)

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


def view_companies():
    prof = db["profile"]
    subview = st.session_state.get("companies_subview", "career_page")
    st.title(SOURCE_LABELS.get(subview, "🏢 Companies & Roles"))

    with st.expander("🔭 Search & scout settings", expanded=not db["companies"]):
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
                    db["companies"][jid] = build_company_record(h, db["cv_skills"], fetch_full_page)
                    new_count += 1
                except Exception:
                    continue
            return new_count

        b1, b2 = st.columns(2)
        if b1.button("🔭 Scout all three sources", use_container_width=True):
            if not db["cv_skills"]:
                st.warning("Upload & sync your CV in Profile first for real fit scoring.")
            total_new, total_found = 0, 0
            for label, fn in [
                ("career pages", lambda: search_career_pages(role, countries, max_results_per_query=results_per_country)),
                ("LinkedIn", lambda: search_linkedin(role, countries, max_results_per_query=results_per_country)),
                ("job boards", lambda: search_job_boards(role, countries, max_results_per_query=results_per_country)),
            ]:
                with st.spinner(f"Searching {label} for {role}..."):
                    hits, errors = fn()
                    new_count = _ingest(hits)
                    total_new += new_count
                    total_found += len(hits)
                    if errors:
                        st.warning(f"{label}: {len(errors)} search querie(s) failed — " + "; ".join(errors[:3]))
            save_db(db)
            st.success(f"Found {total_found} candidate roles across all sources ({total_new} new).")

        if b2.button("🔁 Load more career pages (alt ATS hosts)", use_container_width=True):
            with st.spinner("Searching additional ATS platforms..."):
                hits, errors = search_career_pages(role, countries, max_results_per_query=results_per_country,
                                                    site_filter=ALT_CAREER_SITE_FILTER)
                new_count = _ingest(hits)
                save_db(db)
                if errors:
                    st.warning(f"{len(errors)} search querie(s) failed: " + "; ".join(errors[:3]))
                st.success(f"Found {len(hits)} candidate roles ({new_count} new).")

        b3, b4 = st.columns(2)
        if b3.button("♻️ Recompute stack & fit for existing entries", use_container_width=True):
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
                        c["source"] = c.get("source") or determine_source(c.get("href", ""))
                        c["posted_freshness"] = extract_freshness(text_for_scoring)
                        c.setdefault("scouted_at", None)
                        updated += 1
                    except Exception:
                        failed += 1
                        continue
                save_db(db)
                msg = f"Recomputed {updated} entries."
                if failed:
                    msg += f" Skipped {failed} that errored out."
                st.success(msg)

        if b4.button("🧹 Clear all scouted entries", use_container_width=True):
            db["companies"] = {}
            save_db(db)
            st.success("Cleared. Your liked/disliked history and application log are gone with it — "
                       "scout again to rebuild the list.")

    # --- Filters, front and center at the top of the page -------------------------
    country_options = ["All"] + sorted({c["country"] for c in db["companies"].values()})
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        filter_country = st.selectbox(
            "Country", country_options,
            index=country_options.index(st.session_state.get("companies_country_filter", "All"))
            if st.session_state.get("companies_country_filter") in country_options else 0,
        )
    with f2:
        min_fit = st.slider("Minimum fit %", 0, 100, 0)
    with f3:
        level_filter = st.multiselect("Level", LEVEL_OPTIONS, default=[], help=LEVEL_TOOLTIP)
    with f4:
        visa_filter = st.selectbox("Visa support mentioned", ["Any", "Yes", "No"])
    f5, f6, f7 = st.columns(3)
    with f5:
        liked_only = st.checkbox("Liked only")
    with f6:
        hide_decided = st.checkbox("Hide applied/skipped", value=True)
    with f7:
        sort_by = st.selectbox("Sort by", ["🌟 Best match", "🆕 Recently scouted", "Company name (A-Z)"])
    st.divider()

    liked_stack_counter = liked_stack_signature(db["companies"])

    entries = [(jid, c) for jid, c in db["companies"].items()
               if (c.get("source") or determine_source(c.get("href", ""))) == subview]
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

    if sort_by == "🌟 Best match":
        entries.sort(key=lambda jc: compute_match_score(jc[1], prof, db["country_cache"], liked_stack_counter)[0],
                     reverse=True)
    elif sort_by == "🆕 Recently scouted":
        entries.sort(key=lambda jc: jc[1].get("scouted_at") or "", reverse=True)
    else:
        entries.sort(key=lambda jc: (jc[1].get("company_name") or "").lower())

    if not entries:
        st.info("No roles here yet matching these filters. Use **Search & scout settings** above.")
        return

    st.caption(f"{len(entries)} role(s) match your filters.")

    page_key = f"show_count_{subview}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 10
    show_count = st.session_state[page_key]

    for rank, (jid, c) in enumerate(entries[:show_count]):
        with st.container():
            _render_role_card(jid, c, prof, liked_stack_counter=liked_stack_counter, rank=rank)

    if show_count < len(entries):
        if st.button(f"⬇️ Show more ({len(entries) - show_count} remaining)", use_container_width=True):
            st.session_state[page_key] += 10
            st.rerun()


# ============================================================================================
# VIEW 3 — PROFILE
# ============================================================================================
def view_profile():
    prof = db["profile"]
    subview = st.session_state.get("profile_subview", "Preferences")
    st.title(f"👤 {subview}")

    if subview == "Preferences":
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
                "Visa situation", VISA_STATUS_OPTIONS,
                index=VISA_STATUS_OPTIONS.index(prof["visa_status"]) if prof["visa_status"] in VISA_STATUS_OPTIONS else 0,
            )
            prof["languages"] = st.text_input("Languages you speak (comma separated)", prof["languages"])
            prof["lifestyle"] = st.text_input("Lifestyle assumption (for cost-of-living calc)", prof["lifestyle"])
            prof["avoid_industries"] = st.text_input("Industries to avoid (comma separated)", prof["avoid_industries"])
        if st.button("💾 Save preferences"):
            save_db(db)
            st.success("Saved.")

    elif subview == "Master CV":
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

    elif subview == "Liked / Disliked":
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

    elif subview == "Application Tracker":
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


# ============================================================================================
# NAVIGATION — collapsible tree in the sidebar (Dashboard is a direct link; Companies and
# Profile expand to reveal their sub-views, each of which becomes the whole main page)
# ============================================================================================
PROFILE_SUBVIEWS = ["Preferences", "Master CV", "Liked / Disliked", "Application Tracker"]
COMPANIES_SUBVIEWS = ["linkedin", "career_page", "job_board"]

if "nav" not in st.session_state:
    st.session_state["nav"] = "Dashboard"
if "companies_subview" not in st.session_state:
    st.session_state["companies_subview"] = "career_page"
if "profile_subview" not in st.session_state:
    st.session_state["profile_subview"] = "Preferences"

with st.sidebar:
    st.markdown("### 🐆 Job Copilot")
    st.caption("Your private job-search command center")

    if st.button("🌍 Dashboard", key="nav_dashboard", use_container_width=True,
                 type="primary" if st.session_state["nav"] == "Dashboard" else "secondary"):
        st.session_state["nav"] = "Dashboard"
        st.rerun()

    with st.expander("🏢 Companies", expanded=(st.session_state["nav"] == "Companies")):
        for key in COMPANIES_SUBVIEWS:
            is_active = st.session_state["nav"] == "Companies" and st.session_state["companies_subview"] == key
            if st.button(SOURCE_LABELS[key], key=f"nav_companies_{key}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state["nav"] = "Companies"
                st.session_state["companies_subview"] = key
                st.rerun()

    with st.expander("👤 Profile", expanded=(st.session_state["nav"] == "Profile")):
        for label in PROFILE_SUBVIEWS:
            is_active = st.session_state["nav"] == "Profile" and st.session_state["profile_subview"] == label
            if st.button(label, key=f"nav_profile_{label}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state["nav"] = "Profile"
                st.session_state["profile_subview"] = label
                st.rerun()

nav = st.session_state["nav"]
if nav == "Dashboard":
    view_dashboard()
elif nav == "Companies":
    view_companies()
elif nav == "Profile":
    view_profile()
