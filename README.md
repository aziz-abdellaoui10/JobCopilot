# Job Copilot — setup

## 1. Rotate your Groq key
You pasted a real key into chat, so it's exposed. Go to console.groq.com,
delete that key, create a new one.

## 2. Store the key properly
Create `.streamlit/secrets.toml` next to `app.py`:

```toml
GROQ_API_KEY = "your-new-key-here"
```

Never put the key inside `app.py` or commit it anywhere.

## 3. Install and run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## What was actually broken, and what changed
- **Scouting returned nothing**: `duckduckgo_search` has been renamed to `ddgs`;
  the old import path throws under many environments, and a bare `except: jobs = []`
  was hiding the real error. Fixed by importing `ddgs` and surfacing search errors
  in the UI instead of silently swallowing them.
- **Dashboard numbers were fake**: the Teleport city-score call was failing for
  every city (same bare-except problem), so you always saw the identical
  fallback row. Replaced with country-level, AI-synthesized estimates built
  from live web-search snippets, always shown with a visible "last updated"
  timestamp and a manual refresh button — never presented as live/paid data
  it isn't.
- **Fit % was decorative**: now it's a transparent keyword-overlap score
  between your parsed CV skills and each job posting, with the matched
  skills shown as pills on the card — no black box.
- **No real profile**: added target countries, seniority, salary floor,
  work mode, lifestyle assumption (for cost-of-living), industries to avoid.
- **No apply/skip tracking**: every card now prompts "Applied / Skipped"
  right under the career-page link, logs a timestamp, and hides
  already-decided roles from future scouting so nothing resurfaces.
- **CV tailoring had no guardrails or diff**: the system prompt now explicitly
  forbids inventing skills/titles/dates, and the output includes a bullet-point
  "what changed and why" alongside the downloadable `.tex`.
- **UI**: dark canvas, warm orange accent, rounded cards — a "Welcome to the
  Jungle"-style look without copying their actual branding.

## Known limitations to know about
- `ddgs` scrapes DuckDuckGo unofficially — it can still rate-limit or get
  blocked from some IPs. If a search comes back empty, wait a few seconds
  and retry; errors are now shown to you instead of hidden.
- There's no compile step for LaTeX → PDF in this script (no LaTeX engine
  bundled). Download the `.tex` and compile it locally or on Overleaf.
- Country salary/cost-of-living figures are AI estimates from search
  snippets, not a licensed data feed — treat them as directional, not exact.
