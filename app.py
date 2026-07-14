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
import plotly.graph_objects as go
import pypdf
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
                     "Switzerland", "Ireland", "Spain", "Portugal", "Canada"]

DEFAULT_DB = {
    "profile": {
        "role": "Software Engineer",
        "seniority": "Mid-level",
        "countries": ["Germany", "Netherlands", "United Kingdom"],
        "salary_floor": 55000,
        "work_mode": "Hybrid",
        "avoid_industries": "",
        "lifestyle": "Single, mid-tier city",
    },
    "cv_text": "",
    "tex_source": "",
    "cv_skills": [],
    "cv_meta_updated": None,
    "companies": {},        # id -> role/company record incl. liked/status/date
    "country_cache": {},    # country -> {fit, salary_eur, col_eur, surplus_eur, note, updated_at}
}


def load_db():
    if not os.path.exists(DB_PATH):
        return json.loads(json.dumps(DEFAULT_DB))
    try:
        with open(DB_PATH, "r") as f:
            data = json.load(f)
        # backfill any missing keys if the schema grows
        for k, v in DEFAULT_DB.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_DB))


def save_db(data):
    with open(DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


if "db" not in st.session_state:
    st.session_state.db = load_db()
db = st.session_state.db


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
    if not cv_skills:
        return 0, []
    text_low = job_text.lower()
    matched = [s for s in cv_skills if s.lower() in text_low]
    score = round(100 * len(matched) / max(len(cv_skills), 1))
    # cap so a huge CV doesn't need to match everything to hit 100
    score = min(100, round(score * 2.2))
    return score, matched


# --------------------------------------------------------------------------------------
# SCOUTING ENGINE — searches company career pages, prefers first-party links over boards
# --------------------------------------------------------------------------------------
CAREER_SITE_FILTER = "(site:lever.co OR site:greenhouse.io OR site:ashbyhq.com OR site:workday.com OR site:jobs.smartrecruiters.com)"


def search_roles(role, countries, max_results_per_query=8):
    results, errors = [], []
    try:
        with DDGS() as ddgs:
            for country in countries:
                query = f'{CAREER_SITE_FILTER} "{role}" {country}'
                try:
                    hits = list(ddgs.text(query, max_results=max_results_per_query))
                except Exception as e:
                    errors.append(f"{country}: {e}")
                    hits = []
                for h in hits:
                    h["_country"] = country
                results.extend(hits)
                time.sleep(0.4)  # be polite to the backend, reduce rate-limit risk
    except Exception as e:
        errors.append(str(e))

    # dedupe by href
    seen, deduped = set(), []
    for r in results:
        href = r.get("href") or r.get("url")
        if not href or href in seen:
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
    with DDGS() as ddgs:
        for country in countries:
            try:
                snippets = list(ddgs.text(
                    f"average {seniority} {role} salary {country} 2026 cost of living", max_results=5
                ))
            except Exception:
                snippets = []
            context = "\n".join(f"- {s.get('title','')}: {s.get('body','')}" for s in snippets)[:3500]

            prompt = (
                f"Based on these web search snippets about {role} ({seniority}) salaries and cost of "
                f"living in {country}, estimate: average gross annual salary in EUR for this role/level, "
                f"and average annual cost of living in EUR for a '{st.session_state.db['profile']['lifestyle']}' "
                f"lifestyle. If the snippets are insufficient, use your best general knowledge and say so. "
                'Return ONLY JSON: {"salary_eur": int, "col_eur": int, "note": "1 sentence on data basis"}\n\n'
                f"SNIPPETS:\n{context if context else '(no search results found)'}"
            )
            try:
                res = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                raw = re.sub(r"^```json|```$", "", res.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
                parsed = json.loads(raw)
                salary = int(parsed.get("salary_eur", 0))
                col = int(parsed.get("col_eur", 0))
                note = parsed.get("note", "")
            except Exception as e:
                salary, col, note = 0, 0, f"estimation failed: {e}"

            # fit % = share of currently-cached matched roles in this country vs total cached roles
            country_roles = [c for c in db["companies"].values() if c.get("country") == country]
            fit_scores = [c["fit"] for c in country_roles if "fit" in c]
            fit_pct = round(sum(fit_scores) / len(fit_scores)) if fit_scores else None

            db["country_cache"][country] = {
                "salary_eur": salary,
                "col_eur": col,
                "surplus_eur": (salary - col) if salary and col else None,
                "fit_pct": fit_pct,
                "fit_basis": f"{len(country_roles)} scouted roles" if country_roles else "no roles scouted yet",
                "note": note,
                "updated_at": datetime.now().isoformat(timespec="minutes"),
            }
    save_db(db)


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


# --------------------------------------------------------------------------------------
# VIEW 1 — DASHBOARD
# --------------------------------------------------------------------------------------
def view_dashboard():
    st.title("🌍 Country Fit & Salary Surplus")
    prof = db["profile"]

    countries = st.multiselect("Countries to evaluate", DEFAULT_COUNTRIES + prof["countries"],
                                default=prof["countries"])
    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🔄 Refresh market data", use_container_width=True):
            with st.spinner("Searching current salary & cost-of-living data..."):
                refresh_country_data(countries, prof["role"], prof["seniority"])
            st.rerun()

    if not db["country_cache"]:
        st.info("No market data yet — click **Refresh market data** to pull current estimates.")
        return

    rows = []
    for c in countries:
        d = db["country_cache"].get(c)
        if not d:
            continue
        rows.append({
            "Country": c,
            "Fit %": f"{d['fit_pct']}%" if d.get("fit_pct") is not None else "—",
            "Avg Salary (EUR)": f"€{d['salary_eur']:,}" if d.get("salary_eur") else "—",
            "Avg Surplus (EUR)": f"€{d['surplus_eur']:,}" if d.get("surplus_eur") else "—",
            "Last updated": d["updated_at"],
            "_surplus_val": d.get("surplus_eur") or 0,
        })

    if not rows:
        st.info("Selected countries have no cached data yet — hit refresh.")
        return

    df = pd.DataFrame(rows)
    sort_col = st.selectbox("Sort by", ["Fit %", "Avg Salary (EUR)", "Avg Surplus (EUR)"], index=2)
    df_display = df.sort_values("_surplus_val", ascending=False) if sort_col == "Avg Surplus (EUR)" else df

    c1, c2 = st.columns([1.3, 1])
    with c1:
        st.dataframe(df_display[["Country", "Fit %", "Avg Salary (EUR)", "Avg Surplus (EUR)"]],
                     use_container_width=True, hide_index=True)
        oldest = min(db["country_cache"][c]["updated_at"] for c in countries if c in db["country_cache"])
        st.markdown(f'<span class="last-updated">Oldest data point: {oldest} — AI-estimated from live web search, not a paid data feed.</span>',
                    unsafe_allow_html=True)
    with c2:
        fig = go.Figure(data=[go.Bar(
            x=df["Country"], y=df["_surplus_val"], marker_color=ACCENT,
            text=[f"€{v:,}" for v in df["_surplus_val"]], textposition="auto",
        )])
        fig.update_layout(template="plotly_dark", plot_bgcolor=CARD, paper_bgcolor=CARD,
                           margin=dict(l=0, r=0, t=20, b=0), height=350, title="Annual surplus by country")
        st.plotly_chart(fig, use_container_width=True)

    st.caption("Click a country below to jump into Companies filtered to it.")
    cols = st.columns(min(len(countries), 5) or 1)
    for i, c in enumerate(countries):
        if cols[i % len(cols)].button(f"→ {c}", key=f"jump_{c}"):
            st.session_state["companies_country_filter"] = c
            st.session_state["nav"] = "Companies"
            st.rerun()


# --------------------------------------------------------------------------------------
# VIEW 2 — COMPANIES
# --------------------------------------------------------------------------------------
def view_companies():
    st.title("🏢 Companies & Roles")
    prof = db["profile"]

    with st.sidebar:
        st.subheader("Search")
        role = st.text_input("Target role", prof["role"])
        countries = st.multiselect("Countries", DEFAULT_COUNTRIES + prof["countries"],
                                    default=prof["countries"])
        if st.button("🔭 Scout new roles", use_container_width=True):
            if not db["cv_skills"]:
                st.warning("Upload & sync your CV in Profile first for real fit scoring.")
            with st.spinner(f"Searching company career pages for {role}..."):
                hits, errors = search_roles(role, countries)
                for h in hits:
                    jid = job_id(h["href"], h.get("title", ""))
                    if jid in db["companies"]:
                        continue  # never re-show something already tracked
                    fit, matched = score_job_fit(h.get("title", "") + " " + h.get("body", ""), db["cv_skills"])
                    db["companies"][jid] = {
                        "title": h.get("title", "Untitled role"),
                        "body": h.get("body", ""),
                        "href": h["href"],
                        "country": h.get("_country", "Unknown"),
                        "fit": fit,
                        "matched_skills": matched,
                        "liked": None,
                        "status": None,
                        "logged_at": None,
                    }
                save_db(db)
                if errors:
                    st.warning(f"{len(errors)} search querie(s) failed (rate-limited or blocked): "
                               + "; ".join(errors[:3]))
                st.success(f"Found {len(hits)} candidate roles ({sum(1 for h in hits if job_id(h['href'], h.get('title','')) in db['companies'])} new).")

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

    entries = [(jid, c) for jid, c in db["companies"].items()]
    if filter_country != "All":
        entries = [(j, c) for j, c in entries if c["country"] == filter_country]
    if liked_only:
        entries = [(j, c) for j, c in entries if c.get("liked")]
    if hide_decided:
        entries = [(j, c) for j, c in entries if not c.get("status")]
    entries = [(j, c) for j, c in entries if c.get("fit", 0) >= min_fit]
    entries.sort(key=lambda jc: jc[1].get("fit", 0), reverse=True)

    if not entries:
        st.info("No roles match these filters yet. Use **Scout new roles** in the sidebar.")
        return

    for jid, c in entries:
        with st.container():
            st.markdown(f"""
                <div class="card">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h3 style="margin:0;">{c['title']}</h3>
                        <span class="fit-badge">{c.get('fit', 0)}% fit</span>
                    </div>
                    <p class="small-muted">📍 {c['country']}</p>
                    <p style="color:#c9cdd6; margin:10px 0;">{c['body'][:280]}{'...' if len(c['body'])>280 else ''}</p>
                    <div>{''.join(f'<span class="pill">{s}</span>' for s in c.get('matched_skills', [])[:8])}</div>
                </div>
            """, unsafe_allow_html=True)

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
        c1, c2 = st.columns(2)
        with c1:
            prof["role"] = st.text_input("Target role", prof["role"])
            prof["seniority"] = st.selectbox(
                "Seniority", ["Junior", "Mid-level", "Senior", "Staff/Lead"],
                index=["Junior", "Mid-level", "Senior", "Staff/Lead"].index(prof["seniority"])
                if prof["seniority"] in ["Junior", "Mid-level", "Senior", "Staff/Lead"] else 1,
            )
            prof["countries"] = st.multiselect("Target countries", DEFAULT_COUNTRIES, default=prof["countries"])
            prof["work_mode"] = st.selectbox("Work mode", ["Remote", "Hybrid", "Onsite"],
                                              index=["Remote", "Hybrid", "Onsite"].index(prof["work_mode"]))
        with c2:
            prof["salary_floor"] = st.number_input("Minimum salary (EUR/yr)", value=prof["salary_floor"], step=1000)
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
