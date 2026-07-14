# Job Copilot

A private, local-use Streamlit app that researches countries/companies fitting your profile,
tailors your CV per role, and tracks your applications — rebuilt clean from everything we
iterated on.

## Setup

1. Create `.streamlit/secrets.toml` next to `app.py`:
   ```toml
   GROQ_API_KEY = "your-key-here"
   ```
   Never put the key in `app.py` or commit it anywhere.

2. Install and run:
   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```

3. Go to **Profile → Preferences** and fill in your target role/countries/seniority, plus
   nationality/visa situation/languages (these drive the personalized visa-speed and
   "Your Edge" text on the Dashboard). Then **Profile → Master CV** to upload your CV
   (PDF and/or `.tex`) and click **Sync & re-parse** — this is what powers fit scoring
   everywhere else, so do it first.

## What's in it

**Dashboard**
- Hero stats row: roles scouted, liked, applied, and average match score of liked roles.
- Final Ranking Table: Priority (medals for top 3), Country, Est. Monthly Surplus, Visa
  Speed, QoL stars, Remote/Hybrid Culture stars, and a personalized "Your Edge" phrase.
  Ranking is a weighted composite (surplus + QoL + remote culture + visa favorability),
  not pure salary order.
- Companies sub-table filtered by country (or all), sorted by Match Score once your CV is
  synced — otherwise shown in scouted order rather than faking a sort with no data behind it.
- Region quick-add (Europe, North America, DACH, Nordics, Benelux) next to the country
  picker.

**Companies** — three tabs (🔗 LinkedIn Jobs, 🏢 Career Pages, 📋 Job Boards)
- Scouting searches all three sources in one click: real company career pages (Lever,
  Greenhouse, Ashby, Workday, SmartRecruiters, Workable, Personio, Teamtailor, JobVite,
  Breezy, Recruitee) via a three-tier query, plus dedicated LinkedIn and general job-board
  (Indeed, Glassdoor, Monster, etc.) searches. Connection-level failures are detected and
  fail fast instead of burning time retrying a dead network path.
- **Match Score** (★ rating + %): replaces the old flat "fit %" badge. Starts from CV-skill
  overlap, then adjusts for visa fit, salary-floor fit, and a lightweight "more like this"
  boost — postings with a similar tech stack to companies you've already liked score higher,
  a simple recommendation signal built entirely from your own activity (this is a private
  single-user tool, so there's no cross-user data to learn from). Hover the badge for the
  reasons behind the number.
- **Top pick** ribbon on the best 1-3 cards per page once sorted by match.
- **Freshness badges** ("🆕 Posted today", "📅 Posted 3 days ago") — only shown when the
  posting itself states an age; never guessed.
- Company favicons pulled in automatically next to each role title.
- Sort by Best match / Recently scouted / Company name; filters for country, minimum fit,
  level (with tooltip), visa-support; pagination via "Show more" instead of dumping the
  whole list at once.
- "Load more career pages (alt ATS hosts)" pulls from the alternate ATS set. "Recompute
  stack & fit" re-scores everything already scouted with current logic. "Clear all scouted
  entries" wipes the list for a clean restart (also clears your liked/applied history, so
  it's a deliberate reset, not routine cleanup).
- Salary line only shows a JD-stated figure as "stated in posting" — otherwise it falls
  back to a clearly-labeled AI country-average estimate, or says outright that nothing's
  available.
- Apply flow: opening the link immediately surfaces an "Applied / Skipped" prompt that
  logs a timestamp; already-decided roles get filtered out of future views.
- Optimize CV: LaTeX tailoring restricted to reordering/rewording existing content —
  never invents skills, titles, dates, or employers — with a "what changed and why" list
  next to the downloadable `.tex`.

**Profile**
- Preferences (role, seniority, countries, work mode, salary floor, nationality, visa
  situation, languages, lifestyle assumption, industries to avoid).
- Master CV upload + parsed skill list.
- Liked/disliked list with undo.
- Application tracker with basic stats and a per-country chart.

## Known limitations
- `ddgs` unofficially scrapes DuckDuckGo — it can still rate-limit or get blocked from
  some IPs/networks. Errors are surfaced in the UI rather than hidden.
- No LaTeX → PDF compile step bundled — download the `.tex` and compile locally or on
  Overleaf.
- Country salary/cost-of-living/visa figures are AI estimates from search snippets, not a
  licensed data feed — always shown with a timestamp, treat as directional not exact.
- Fetching full job pages (for better stack/fit/visa detection) adds latency and network
  calls per posting — toggle it off in the Companies sidebar if it's too slow.
