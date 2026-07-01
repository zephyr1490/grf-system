# GRF SYSTEM — Project Briefing
*Paste this at the start of each new chat session so Claude has full context.*

---

## 🚦 STATUS — last updated 2026-07-01 (later in the day, after a long session)

**Right now, literally in progress:** a local `grf_sync.py --full` run on the owner's
own machine (NOT Railway) is either still running or just finished — check with the
owner before assuming either way. **Railway's cron is currently PAUSED** (Cron Schedule
field cleared under Settings → Deploy, then "Apply 1 change → Deploy" clicked — this
is a config-only change, does NOT touch the deployed code, which is still the OLD
pre-session grf_sync.py). This was done deliberately to avoid a real risk (see
"⚠️ Shared RaceNet token" below).

**⚠️ CRITICAL — do not let the owner get confused about which fix is in which file:**
- The `--full` run happening right now on the owner's machine uses the **OLD**
  `grf_sync.py` (the one that was already on their machine before this session's
  fixes). It does NOT include the DNF/total-time fix, the all-clubs-cron fix, or
  the real `--full` championship-loop fix from this session.
- Wait, actually — re-check this with the owner directly if unsure: they downloaded
  the fixed file multiple times during this session but explicitly said each time
  "lass ihn liegen" (leave it in Downloads, don't use it yet) and kept running the
  original script for the actual `--full` historical backfill. **The fixed file has
  never been run against real data. Confirm this assumption with the owner before
  concluding anything about what's "verified".**

**⚠️ Shared RaceNet token — real collision risk, this is WHY the cron got paused:**
The RaceNet refresh token lives centrally in Supabase (`system_config` table, not a
per-process file). Both a local `grf_sync.py` run AND the Railway cron read AND
(when expired) rewrite this same token. If both happen to trigger a refresh at
overlapping moments, one process can invalidate the token out from under the other
mid-run, causing an auth failure. This is why running a long local `--full` sync
and leaving the Railway cron active simultaneously is genuinely risky, not just
messy — pausing the cron during any local run is the right call going forward too,
not a one-off.

**Deploy plan, agreed this session, not yet executed (in order):**
1. Wait for the current local `--full` run to finish.
2. ⚠️ Do NOT try to "verify the DNF fix" against this run's results — it's running
   old code. The DNF fix can only be verified AFTER deploy (step 3) and AFTER at
   least one real sync of the specific event used as the test case (EKO Acropolis
   Rally Greece, Rd.2, driver "ZephyR1490" — should show total time `58:03.265`
   once correctly fixed and re-synced, was showing `4:59.941` before the fix).
3. Deploy the session's `grf_sync.py` (contains: real `--full` championship-loop
   fix, all-clubs-cron fix, DNF/total-time calculation fix — see sections below for
   each) to GitHub. Deliberately NOT bundling in this deploy: the performance fixes,
   the driver-ID/name fix, or the Admin redesign — all still just proposals/findings,
   not code, kept separate on purpose so a bad outcome is traceable to one change at
   a time (owner's explicit concern this session: too many untested changes stacking
   up at once makes debugging impossible).
4. Re-enable Railway cron (Cron Schedule back to `*/10 * * * *`, Apply → Deploy).
5. After 1-2 real cron cycles: check the Acropolis Rd.2 event on the live site to
   confirm the DNF fix actually landed correctly on real data, not just in tests.
6. Admin panel: ELO Force-Reset, all clubs checked — full rebuild with the now-live
   surface/drivetrain/era tracks AND the corrected times/points from the DNF fix.
7. Update this briefing: move the DNF fix from "fixed, untested" to "fixed, verified
   on real data" (or document what broke, if anything did).

**After that, tackle ONE AT A TIME (owner's explicit preference — no bundling),
in priority order:**
1. **🔒 Security — RLS check still open (code side already fixed this session).**
   `grf_sync.py` now correctly uses `SUPABASE_SERVICE_KEY` (env var already existed
   on Railway, just wasn't being read). Still needed: check Supabase RLS policies
   actually restrict the public `anon` key to SELECT-only — see dedicated "SECURITY"
   section below for full detail. Site not shared publicly yet, so not urgent-urgent,
   but must be done before the link is ever shared anywhere.
2. Performance fixes (43s ELO call + 21s stats-update gap — see "Known issues"
   below, both fully diagnosed with exact code locations, neither fixed yet)
3. Driver identity fix (RaceNet ssid vs display name — see dedicated section below)
4. Package 3 Admin Championship Setup redesign ("Admin Create Championship Full New")
5. Visual/design pass on `index.html` — owner flagged issues but hasn't detailed
   them yet. **Now includes a concrete first item, agreed this session (see
   dedicated section below): a public Changelog widget.**

**Confirmed earlier this session, still true, no action needed:**
- Supabase `drivers` table already has `elo_mu`/`elo_sigma`/`elo_events`/
  `elo_provisional`/`elo_inactive` columns — no ALTER TABLE needed.
- `elo_state` table already exists with correct structure (`id`, `state_json`, `updated_at`).
- Supabase free-tier usage was nowhere near limits (0.03GB/500MB storage,
  0.1GB/5GB egress) BEFORE the `--full` run — worth re-checking current numbers
  once the local run is done, but very unlikely to be a real constraint yet.

**Explicitly decided against / deferred, don't re-litigate unless owner brings it up:**
- Per-club ELO ratings (separate from the global cross-club ELO) — discussed,
  owner decided the added complexity isn't worth it, dropped for good.
- Combinable ELO filters (e.g. "Gravel + AWD" simultaneously) — discussed, deferred,
  not dropped — could revisit later.
- The duplicate-event-id bug in Admin's "Create from RaceNet" import
  (`_import_racenet_events()` doesn't preserve real RaceNet event IDs) — found,
  not fixed, tied to the Admin redesign anyway (see "Known issues" section).

---

## 🔒 SECURITY — found this session, HIGH PRIORITY, still 1 step open

**Ranks above the performance fixes in priority.** A slow sync is an inconvenience;
an exploitable write hole on a public site is a different category of problem.
Owner explicitly asked for this to be looked at ("eine ganz billige Sicherheitslücke
wäre extrem peinlich für uns beide").

**Mitigating context from the owner (reduces urgency, doesn't remove the need to
fix it):** the website has NOT been shared with anyone yet — not in the GRF Discord,
not anywhere public. Real-world exploit risk right now is low (someone would have
to guess/find the Vercel/Railway URL). **This must still be closed before the first
time the link is ever shared** — treat "share the site" as blocked on this.

### The finding, now precisely scoped (was broader in an earlier pass this session)
`index.html` has this comment, correctly identifying the requirement:
```js
// Sicherheit: SB_KEY ist der anon-key — öffentlich ok,
//   SOLANGE RLS auf allen Tabellen aktiv ist (nur SELECT für anon).
```
Checked both server-side scripts directly:
- **`admin_api.py` was already doing this correctly all along** — it loads
  `SUPABASE_SVC_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")` and uses it for
  all its Supabase calls (`SB` headers dict). Not a bug here, my first pass at this
  was too broad in blaming both scripts equally — corrected now.
- **`grf_sync.py` was the actual gap** — it had the public `anon` key (same one
  `index.html` uses, confirmed character-for-character identical) hardcoded directly
  in the source, ignoring Supabase entirely bypassing any service_role option.
- Owner checked Railway directly (screenshots, both services): `SUPABASE_SERVICE_KEY`
  already exists as an env var on BOTH the `grf-system` (sync/cron) service AND the
  `practical-beauty` (admin_api.py) service. The infrastructure was already fully in
  place — `grf_sync.py` just never read it.

### ✅ Fixed this session (code side)
`grf_sync.py` now reads `SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")`
instead of the hardcoded anon key. Added a clear startup error if the env var is
missing/empty (previously it would have silently "worked" with the wrong key — now
it fails loudly with instructions instead of a cryptic 401 buried in output).
**Consequence: `grf_sync.py` can no longer run locally without
`SUPABASE_SERVICE_KEY` set in the local shell environment first** — this is new,
wasn't required before this fix. Not yet tested against a real run (same "fixed,
untested" caveat as the DNF fix — verify after deploy, not before).

### ⚠️ Still open — the actual RLS check, can't be done from here
Switching `grf_sync.py` to the service_role key means IT no longer depends on
what `anon` can do. But the public `anon` key in `index.html` is still exactly as
exposed as before — whether that's actually safe depends entirely on the Supabase
RLS policies, which is a dashboard action, not a code change:
1. Supabase Dashboard → Authentication → Policies — check EVERY table for policies
   granting `INSERT`/`UPDATE`/`DELETE` to the `anon` role. If any exist, that's
   the hole (a site visitor could still write directly via the public anon key,
   completely independent of what `grf_sync.py`/`admin_api.py` now do correctly).
2. Tighten so `anon` gets SELECT-only everywhere, no write access at all.
3. **Extra-sensitive candidate, check first:** `system_config` (holds the RaceNet
   refresh token) — if its RLS is as unrestricted as the others might be, a visitor
   could read/overwrite the RaceNet auth token remotely, worse than result-tampering.
4. `index.html` itself needs no changes — using the `anon` key in the frontend is
   correct and expected, it's the RLS enforcement on the Supabase side that's unverified.

### Two smaller, already-confirmed-OK items while investigating this
- `ADMIN_PASSWORD` is correctly read from an env var
  (`os.environ.get("ADMIN_PASSWORD", "")`) in the current `admin_api.py` — the
  briefing's old note about a hardcoded default (`grf2024admin`) is **stale**, no
  longer accurate, should be considered resolved unless the owner confirms otherwise.
- The password check itself (`pw != ADMIN_PASSWORD`) is a plain string comparison,
  not constant-time (`hmac.compare_digest`) — theoretical timing-attack surface,
  low priority, cosmetic fix if anyone ever gets to it.

---

## What this project is

A web dashboard for **Global Rally Fans (GRF)** — a rally simulation community playing EA WRC.
The site shows championship results, ELO rankings, car ratings, event narratives and more.
Design language: **Win98 retro aesthetic, red/black/green color scheme.**

---

## Tech Stack

| Component | What it is |
|---|---|
| **Supabase** | PostgreSQL database (cloud, free tier) |
| **grf_sync.py** | Python script — fetches RaceNet API → writes to Supabase |
| **racenet_client.py** | RaceNet API client (tokens, auth, leaderboards) |
| **HTML file** | Main website (all pages, Win98 style) |
| **Railway.app** | Hosts the sync script (free, cron every 10 min) |
| **Vercel** | Hosts the website (free, auto-deploys from GitHub) |
| **GitHub** | Code storage (repo: grf-system) |

**Supabase credentials:**
- URL: `https://ixuhhzdijvtlfdjtrnyi.supabase.co`
- Anon key: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml4dWhoemRpanZ0bGZkanRybnlpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODIzNDkxOTIsImV4cCI6MjA5NzkyNTE5Mn0.rJi8w8ayIiB-m5v1Kn-f5Re-K2Z_Ke1v1IaJo1qGmSA`

**RaceNet Clubs:**
- `23799` — GRF Themed (main, multiclass events possible)
- `23834` — GRF Themed secondary / GRF Teamed (future)
- Other club IDs also appear in the DB (e.g. `62`, `14317`, `104`, `3630`) from
  `get_active_clubs()` / Package 9 — full list not documented here, check
  `client.get_active_clubs()` output for the current set.

**One-off utility scripts (built this session, not part of the regular pipeline):**
- `grf_sync_check.py` — read-only Supabase coverage diagnostic (missing dates,
  missing event_results/stage_results per championship). Run before any big sync
  to know what work is actually needed.
- `extract_lookup_vocab.py` — read-only RaceNet crawl, dumps distinct location
  names + vehicle names (exact RaceNet spelling) to CSV. Used once to build the
  surface/vehicle lookup source lists.
- `classify_vehicles_by_class.py` — read-only, matches a vehicle-name CSV against
  RaceNet's own 20 game classes (via public class leaderboards) to help assign
  Era. Used once, not needed again unless the vehicle list needs rebuilding.

**Railway:**
- Cron: `*/10 * * * *` (every 10 min)
- Env var: `RACENET_REFRESH_TOKEN` — RaceNet cookie, set in Railway Variables
- Token auto-refresh: racenet_client.py bootstraps from env var on every run,
  auto-refreshes access token when expired (3-6h lifetime), saves new tokens to file

---

## Database Schema (Supabase — current clean state)

```
championships     id, club_id, name, narrative, vehicle_class, season_number, start_date, end_date
events            id, championship_id, club_id, name, location, round_number, start_date, end_date, status, narrative
stages            id, event_id, championship_id, club_id, name, stage_number, leaderboard_id, status
stage_results     id, stage_id, event_id, championship_id, driver_name, driver_id, vehicle, time_ms, time_str, stage_position, is_dnf, platform
event_results     id, event_id, championship_id, driver_name, driver_id, vehicle, platform, position, total_time_ms, time, stages_completed, is_dnf, base_points, cr_multiplier, cr_points, bonus_points, total_points
drivers           id, name, elo, elo_mu, elo_sigma, elo_events, elo_provisional, elo_inactive, wins, starts, country, is_established, events_total (all-time), events_live (post-baseline), updated_at
car_ratings       id, championship_id, vehicle, cr_value, surface, era
cr_sets           id, name, vehicle_classes, exponent, created_at   -- (was missing from this doc — used by /cr/save, /cr/sets, /cr/assign)
championship_rules id, championship_id, rule_type, is_active, points, params, description
manual_bonuses    id, championship_id, event_id, driver_name, bonus_type, bonus_name, points, note
teams             id, championship_id, name, color
team_members      id, team_id, driver_name
elo_state         id, state_json, updated_at   -- (was missing from this doc — single-row global ELO calculation state, see ELO System section)
stage_info        location, stage_name, length_km, surface, conditions, elevation_profile (JSON)  -- not yet built, see Package 8
elo_history       id, driver_name, elo_before, elo_after, delta, event_id, recorded_at
```

**⚠️ `vehicles` lookup table — decided AGAINST, do not build.** Original plan was a
Supabase table for Surface/Drivetrain/Era lookups. Since the game (EA WRC) receives
no further updates, locations/vehicles will never change again — so this lives as two
static JSON files in the repo instead (no DB round-trip, no migration risk):
- `category_lookups_surface.json` — 20 locations → surface (gravel/tarmac/snow)
- `category_lookups_vehicles.json` — 99 vehicles → {drivetrain, era}
Loaded by `admin_api.py`'s `elo_update()` via `elo_categories.build_lookups(...)`.
Era values are `"modern"` / `"historic"` (NOT `"classic"` — renamed deliberately,
see Notes). Cutoff rule used to classify vehicles: **2012+ = modern, everything
before = historic** (this also resolves Super2000 → historic, WRC-Car 1997-2011 →
historic, all three "World Rally Car" generations 2012+ → modern).

**Key logic:**
- `stage_results` = raw times per driver per stage (milliseconds)
- `event_results` = calculated from stage_results (sum of all stage times = total event time)
- **DNF/Finisher rule (clarified + fixed this session — was previously vague AND
  buggy, see "🔧 Fixed this session" below for the full story):**
  - EVERY stage entry a driver has — real time OR RaceNet's own max/penalty time
    (a round-minute value, ≥4 min, exact multiple of 60s) — counts fully toward the
    driver's total event time. Never computed/guessed ourselves, always exactly
    what RaceNet reports.
  - Finisher vs. DNF is decided **exclusively** by the driver's entry on the
    **last** stage with data — never by anything on earlier stages:
    - real time on last stage → finisher, regardless of penalties earlier in the rally
    - max/round-number time on last stage → DNF
    - missing from the last stage entirely (quit and never came back) → DNF
- `position = 0` means DNF
- `total_points = cr_points + bonus_points`
- `cr_points = base_points × cr_multiplier`
- Loyalty bonus: same vehicle all season → +50 pts (checked by website)
- Other bonuses only active if configured in `championship_rules`

**Points table (base_points by position):**
`[50,44,40,38,36,34,32,30,28,26,25,24,23,22,21,20,19,18,17,16,15,14,13,12,11,10,9,8,7,6]`
DNF = 2 pts

---

## Sync Script (grf_sync.py)

- Loads every stage leaderboard individually via RaceNet API
- Stores times in milliseconds
- Calculates cumulative event result (sum of all stages per driver)
- DNF = round-minute penalty time (≥4 min, exact multiple of 60s)
- Smart skip: completed events with existing stage_results are skipped
- Active events (status=1) always re-synced
- Event/championship **date backfill runs unconditionally**, even for skipped events
  (only the expensive stage-leaderboard re-fetch is skipped, not the metadata write)
- `--test` = no writes, `--full` = visit ALL championships per club (via
  `get_all_championship_ids()`), `--force-stages` = ALSO re-fetch stage data for
  events that already have it (separate flag now, see below)
- **`--full` bug actually fixed this session** (previous claim of "fixed" in this
  doc was wrong — `main()` still only ever synced `club.currentChampionship`
  regardless of `--full`; the docstring said otherwise but the code didn't match).
  Root cause + fix confirmed via code read + `--test --full` dry run against real data.
- **Club scope**: both the normal 10-min cron AND `--full` now sync **all** clubs via
  `client.get_active_clubs()` (previously the cron only touched `GRF_CLUBS` = the 2
  main clubs; other clubs were only ever synced during a manual `--full` run).
  `GRF_CLUBS` constant remains only as a fallback if the RaceNet call fails.
  Cron stays fast because it's still "all clubs × current championship only" —
  the historical-championship crawl (`get_all_championship_ids`) is still gated
  behind `--full` alone, per-club scope and per-championship scope are independent knobs.
- ELO auto-trigger at the end of `main()`: also switched from hardcoded `GRF_CLUBS`
  to the same dynamic all-clubs list (reuses the club_ids already fetched above,
  no duplicate RaceNet call), `force_reset: false` (delta update only — the one-time
  full rebuild stays a manual Admin-panel action).
- ✅🧪 **DNF/total-time calculation bug fixed (`sync_event()`) — FIXED, NOT YET TESTED
  against real data.** Found by the owner from a live event screenshot: a driver
  with a mid-rally penalty stage (round-minute time) who then kept driving with real
  times got a wildly wrong total (their total showed only their SS1 time — nothing
  after the penalty stage was ever summed). Root cause was two separate bugs stacked:
  1. The stage-summing loop re-checked the driver's *overall* DNF flag instead of
     just the current stage's own flag, so once ANY earlier stage looked like a
     DNF/penalty, every stage after it got silently dropped from the sum — even
     real, valid times.
  2. Separately, a penalty/max-time stage's time itself was never added to the sum
     at all (excluded entirely) — this turned out to be the wrong rule, not a bug
     in isolation, but combined with #1 it compounded.
  Rewritten per the owner's clarified rule (see "Key logic" above): every stage
  entry (real or max-time) always sums into the total; finisher/DNF status is
  decided exclusively by the driver's entry on the LAST stage with data, nothing
  earlier matters for that decision. Verified against the exact screenshot data
  (corrected total 58:03.265, was showing 4:59.941) plus 3 additional synthetic
  edge cases (normal finisher, max-time on last stage, quits after stage 1 and
  never returns) — all matched the expected rule exactly.
  **⚠️ NOT tested against a real full sync run yet** — only unit-level logic
  tests with hand-built data so far. Watch the first real `--full` run after this
  is deployed for any DNF/position/points weirdness, especially events with
  mid-rally penalties.

---

## Website Structure

**Pages:**
- **Home** — Hero, 4 stat tiles (Active Clubs / GRF Members / Most Events / Top ELO verified), Top 10 ELO, Most Improved last 7 days, Next Event teaser
- **GRF Themed** — Championship hero + narrative, sub-tabs: Live Results / Championship Standings / Car Ratings / Challenges
- **GRF Teamed** — Team-based championship (same pipeline as Themed, results grouped by team)
- **ELO Rankings** — Full driver list, filters (all/established/provisional/per club/surface/drivetrain/era)
- **Admin** — Password protected, full management

**Design rules:**
- Win98 window chrome (red gradient title bar, raised/sunken borders)
- Colors: `--red: #CC0000`, `--black: #080808`, `--green: #00FF41`, `--gray: #C0C0C0`
- Fonts: VT323 (display), Oswald (headers), Share Tech Mono (body)
- Scanline overlay, no X-buttons on windows, single app window feel
- Min font size 13px, status bar always black background

---

## GRF Themed Page — JS State & Functions (implemented)

Key state variables:
- `_themedChampId` — current championship_id for club 23799 (loaded on page open)
- `_themedActiveEventId` — active event in selector
- `_themedEvents` — all events of championship, sorted by round_number (cached)

Key functions:
- `loadThemedResults()` — entry point, loads championship + events, builds selector
- `selectThemedEvent(id, el)` — click handler for event selector buttons
- `_fetchAndRenderResults(eventId)` — loads event_results + stage_results, renders table + event info panel
- `toggleStageRow(rowId)` — show/hide stage times row on driver row click
- `loadStandings()` — loads all event_results for championship, aggregates by driver+round
- `loadCarRatings()` — loads car_ratings for current championship

Results table columns: # / DRIVER / VEHICLE / TIME / BASE / ×CR / CR PTS / TOTAL
Stage times: expandable on row click, SS1/SS2... columns, green = stage win

---

## Championship Rules System

**Always active:** Loyalty Bonus (same vehicle all season → +50 pts)

**Optional per championship (Admin configured):**
- Stage Improvement — faster 2nd attempt → +X pts (AUTO)
- Penalty Challenge — finish despite ≥X sec penalty → +X pts (AUTO)
- Livery Challenge — historically accurate livery → +X pts (MANUAL)
- Survivor Bonus — last non-DNF finisher → +X pts (AUTO)
- Custom — any name, any points (MANUAL)

---

## ELO System — Important Design Decisions

**No static baseline.** ELO is calculated chronologically through ALL historical events,
sorted by `championships.start_date` + `events.start_date` + `events.round_number`.
Every past event counts in order, just like future events will.
This makes the ELO immediately meaningful and eliminates the need for a separate baseline import.

✅ **This is now actually enforced in code, not just design intent.** Previously
`elo_pipeline.py` still had dead legacy code (`process_historical_batch` — Bradley-Terry
batch fit, order-independent; `process_csv_baseline` — sequential CSV import path) left
over from before `start_date`/`end_date` were reliably available from RaceNet. Neither
was ever called from `admin_api.py`, but they existed as a source of confusion. Removed
this session, along with the matching dead fields in `elo_state.py`
(`historical_batch_*`, `csv_baseline_*`). `process_racenet_events()` — the chronological,
sequential, delta-aware path — is now the **only** entry point.

**Two event counters in `drivers` table:**
- `events_total` — all events ever counted toward this driver's ELO (historical + live)
- `events_live` — events since the system went live (post-deployment counter)
Both shown separately on ELO page so players understand the history behind the number.
⚠️ **Not actually wired up yet** — `admin_api.py`'s `elo_update()` writes `elo`,
`elo_mu`, `elo_sigma`, `elo_events`, `elo_provisional`, `elo_inactive`, but never
touches `events_total`/`events_live`. Confirmed these `drivers` columns already exist
in Supabase (checked this session), just nothing writes to them yet. Still open, low
priority — not blocking the current ELO baseline work.

**ELO filters — surface/drivetrain/era are LIVE as of this session** (previously the
`tracks_for_event()` mechanism existed in `elo_categories.py` but ran with empty
lookups, so only the `"overall"` track was ever populated). Now wired via the two
static JSON lookup files (see Database Schema section above). Confirmed working with
a real end-to-end test: `surface:gravel/tarmac/snow`, `drivetrain:AWD/RWD/FWD`,
`era:modern/historic` tracks all populate correctly.
- Surface: Gravel / Tarmac / Snow (no soft/hard gravel split — GRF's simplified 3-value scheme)
- Drivetrain: AWD / RWD / FWD
- Era: **Modern / Historic** (NOT "Classic" — renamed this session, "historic" is the
  team's preferred term; verified zero references to "classic" remain anywhere in the
  codebase or frontend after the rename)
- Combinable filters (e.g. "Gravel + AWD") are NOT yet supported — `tracks_for_event()`
  currently only builds independent single-axis tracks. Combos would need either extra
  cross-product tracks (track count grows fast) or live filtering at query time —
  discussed, deliberately deferred, not in scope yet.
All filters work on historical data too (via event_results.vehicle + events.location →
static lookups).

**Most Improved:** Calculated from `elo_history` — compare current ELO vs ELO 7 days ago.
Not yet verified working end-to-end this session (not the focus — focus was the
sync bug + baseline correctness first).

---

## Admin credentials
- Username context: `zephyr-club`
- Admin password (website): `grf2024admin` — change before going live

---

## Notes for Claude
- Owner cannot program — explain all steps clearly
- Everything in **English** (UI, comments, all text) — ⚠️ **not actually true in
  practice**: the existing codebase (elo_state.py, elo_categories.py) already had
  German comments/docstrings before this session, and this session's edits followed
  that existing convention (German comments in grf_sync.py, elo_pipeline.py,
  elo_state.py, elo_categories.py) rather than fighting it. Flagging so this rule
  gets either updated to match reality or actively enforced going forward — your call.
- Always `--test` before live sync run
- RaceNet `SortCumulative: false` = individual stage time — correct, we sum manually
- Deployment: GitHub → Vercel (website), Railway.app (sync script)
- Teams are scoped to a championship (not a club) — clean cascade deletes
- `.gitignore` includes `racenet_tokens.json` — never commit tokens to GitHub

---

## 🔧 Known issues found this session (not fixed yet — for later)

1. **Duplicate event rows via Admin's "Create from RaceNet" flow.**
   `admin_api.py`'s `_import_racenet_events()` inserts `events` rows WITHOUT setting
   `id` to the real RaceNet event ID (Supabase auto-generates one instead; the real
   ID is only kept in a side field `racenet_event_id`). `grf_sync.py`'s own sync
   uses the real RaceNet event ID as `id` (`on_conflict="id"` upsert). Any
   championship set up via Admin's RaceNet-import path before this was noticed likely
   has duplicate event rows — one from the Admin import, one from grf_sync's own
   upsert of the same real-world event. Needs a Supabase cleanup pass + a fix to
   `_import_racenet_events()` to use the real event ID. Not urgent, but real.
2. **"Create without RaceNet" championships can never be connected to a RaceNet ID
   later.** `championship_alter()` only allows patching `name/vehicle_class/
   season_number/start_date/end_date/narrative` — never `id`. A manually-created
   championship is a permanent dead end if you later want to link it to a live
   RaceNet championship. Likely moot once the Admin redesign below happens (see
   "Admin Create Championship Full New").
3. **CR-assignment UI in Championship Setup is completely missing, not just hidden.**
   `index.html` has working JS (`_crPopulateSetDropdowns()`) and a working backend
   endpoint (`/cr/assign`) for assigning a saved CR-set to a championship — but the
   actual `<select id="cs-cr-set-select">` element was never added to the HTML, so
   the JS silently no-ops (`if (!sel) return;`) and `/cr/assign` is never called from
   anywhere. Manual CR entry (`/cr/manual-save`) does work, but only from the
   separate "Car Rating Calc" tab, not from Championship Setup.
4. **RaceNet's championship-list endpoint doesn't surface vehicle class**, making it
   hard for the Admin to tell which "Championship" entry (they're all unnamed) is
   which without checking start/end dates. Only fixable in Package 3 admin redesign.
5. **Supabase's default 1000-row response cap is silently truncating data in at
   least two places — this is a CLASS of bug, expect more instances.** Any
   unpaginated Supabase fetch (no `.range()`/`Range` header) silently returns only
   the first 1000 rows, no error, no warning. Confirmed hit in:
   - `index.html`'s `loadHome()` — the "GRF Members" stat tile counts
     `new Set(allER.map(r => r.driver_name))` from an unpaginated `event_results`
     fetch (`sb()` helper has zero pagination). This is almost certainly why the
     owner saw only 341 unique drivers on the site vs ~1200 rows in the `drivers`
     table — `event_results` grew past 1000 rows during this session's `--full` run,
     so the frontend only ever sees a fraction of it now, ordered by
     `created_at.desc` (most recently inserted first).
   - `grf_sync.py`'s `sync_event()` Step 6 "Update driver stats" (~line 512-534):
     `db.select("event_results", "select=driver_name,position,is_dnf")` — same
     unpaginated pattern, meaning `drivers.wins`/`drivers.starts` are likely wrong
     too, not just slow (see performance section below for the speed side of this
     same bug).
   - **Not yet audited:** every other unpaginated Supabase fetch in `admin_api.py`
     and `index.html`. Given the table sizes just multiplied from the `--full` sync,
     this is worth a systematic grep for `sb(` / `sb_get(` calls without a
     `limit=`/`.range()` before assuming any aggregate count/stat on the site is correct.
6. **Driver identity is based on raw display-name string everywhere it matters,
   even though RaceNet's real stable player ID is already captured and sitting
   unused right next to it.** Full detail:
   - `entry.get("ssid", "")` from RaceNet IS the real, persistent RaceNet player ID
     (confirmed via `racenet_client.py`'s own comments — used for fetching ghost/
     replay data specifically because it's the stable player_id; also matches the
     large platform-style numeric values seen in real `stage_results.driver_id`
     data, e.g. `1014274977381` — not small sequential IDs).
   - `grf_sync.py` correctly captures it into `stage_results.driver_id` AND
     `event_results.driver_id` on every row (same source, same value, confirmed
     identical for the same driver/event).
   - **But nothing downstream uses it.** The `drivers` table has no column for it
     at all (only has its own unrelated Supabase-internal `drivers.id` primary key —
     do not confuse the two, they are completely different things). `grf_sync.py`'s
     driver-insert logic keys new drivers purely on `name`
     (~line 500-509: `existing = {r["name"] for r...}`, `on_conflict="name"`).
     `admin_api.py`'s `elo_update()` SQL query only selects `driver_name`, never
     `driver_id`, despite the column existing and being populated
     (~line 821: `select=driver_name,position,vehicle,is_dnf`).
   - **Consequence:** every time a player renames their RaceNet display name, they
     get a brand-new row in `drivers` AND are treated as a brand-new person by the
     ELO chronological pipeline — splitting their rating history in two. Owner
     specifically worried about this for the ~50-100 actually-active/Discord
     players, who are exactly the ones most likely to rename over time. This is a
     real, not hypothetical, correctness problem for the ELO numbers.
   - **Fix sketch (discussed, not built):** add a `racenet_id` column to `drivers`
     (separate from the existing internal `id` PK), switch dedup/matching in both
     `grf_sync.py` and `admin_api.py` to key on this instead of `name`, run a
     one-time merge migration using the already-correct `event_results.driver_id`
     values to figure out which of the ~1200 name-rows are actually duplicates of
     the same real person.
   - **Display-name-overwrite safety (resolved in discussion, not built):**
     `elo_state.py`'s existing `driver_labels: Dict[driver_id, display_name]` +
     `update_label()` pattern already solves "which name to show" correctly — one
     name per ID, always overwritten by the latest, never a "two names at once"
     conflict. Two catches when porting this pattern to the `drivers` table: (a) it
     must run unconditionally, even for events skipped by smart-skip stage-reload
     (same pattern as the date-backfill fix earlier this session), or a stale name
     could persist indefinitely for a driver whose events never get re-visited;
     (b) "latest" must be judged by the event's actual `start_date`, NOT by
     processing/insertion order — `get_all_championship_ids()` does NOT return
     championships in chronological order (confirmed from real `--full` log output
     jumping between 2026-04, 2026-02, 2026-05 non-sequentially), so naive
     "last write wins" during a `--full` run could let an old name overwrite a
     newer one depending on random API ordering.
7. **Performance: two real, diagnosed-but-unfixed cost centers, found by timestamp-
   analyzing a real sync log + real admin log side by side.** Total observed
   runtime ~90s for a normal (non-`--full`) cron cycle:
   - **`/elo/update` call itself: ~43s — the single biggest cost, and NOT RaceNet-
     related at all.** Purely Supabase I/O on the `admin_api.py` side. Root cause
     not fully confirmed yet but strongly suspected to be the same anti-pattern as
     below: the final driver-write loop
     (`sb_patch("drivers", f"name=eq.{...}", {...})` inside a Python `for summary in
     summaries:` loop) fires one sequential HTTP PATCH per driver — 96 drivers were
     written in the log example, which alone could plausibly account for a large
     chunk of the 43s at typical Supabase round-trip latency. Likely also an N+1
     read pattern (per-championship, per-event sequential `sb_get()` calls while
     building `RawEvent`s) — not fully traced line-by-line yet, worth doing before
     attempting a fix.
   - **`grf_sync.py`'s Step 6 "Update driver stats" (~line 512-534): ~21s gap,
     confirmed via matching log timestamps exactly.** Root cause fully traced:
     unpaginated full `event_results` table read (same 1000-row cap bug as above,
     so likely wrong AND slow) + a Python loop computing wins/starts per driver
     name + **one sequential, unbatched `requests.patch()` HTTP call per unique
     driver name** (not bulk-upserted). Made worse by running once per newly-synced
     event within a single run, not once per whole run — if multiple events sync
     in the same pass (e.g. a busy event weekend), this cost multiplies linearly,
     not just adds once.
   - Fix direction for both (discussed, not built): batch/bulk-upsert instead of
     N sequential PATCH calls, add pagination to the `event_results` reads, and for
     the stats step specifically, move it to run once per whole sync run instead of
     once per synced event.

---

## 💡 "Admin Create Championship Full New" — agreed redesign, not built yet

Discussed and agreed as the direction for the Package 3 rebuild (Championship Setup
specifically). Current 3-tab structure ("Create without RaceNet" / "Create from
RaceNet" / "Alter running championship") is confusing and has the dead-end/missing-UI
problems listed above. Owner's own summary of what's actually wanted, confirmed correct
against the backend:

> Championships already appear automatically in Supabase (grf_sync.py upserts them
> the moment RaceNet has one running, incl. dates + vehicle_class already read from
> RaceNet). Admin shouldn't need to "create" a championship at all in the common case —
> just **see the auto-synced championships, pick one, and fill in the individual data**:
> name, CR (from a saved list OR manual fallback), bonus rules, narrative, teams.

This is mostly a **frontend consolidation**, not new backend work — `championship_alter`,
`/cr/assign`, `/cr/manual-save`, `/bonus/add`, `/narrative/championship`, `/teams/save`
already operate on any `championship_id` regardless of how the row was created. What's
actually missing:
- A listing endpoint reading **existing Supabase championships** (not RaceNet-live) for
  the picker — similar shape to `/championship/racenet-list` but backed by Supabase.
- The CR dropdown (`cs-cr-set-select`) actually added to the Championship Setup HTML.
- Manual CR entry duplicated/moved into Championship Setup (currently only in CR Calc tab).
- The old "Create without RaceNet" mode demoted to a rare special case, not the default path.

Not started. Revisit once the current sync/baseline work is fully deployed and stable.

---

## 💡 Changelog widget — agreed this session, not built yet

Belongs thematically to the deferred **visual/design pass on `index.html`** (see
STATUS block) — came up as its own concrete request, worth tracking separately
since it's now fully scoped, not just "something feels off visually" like the
rest of that item.

**What the owner wants:**
- A small widget on the **Home page**, positioned near the other home-page
  widgets (Top 10 ELO, Next Event, etc.) — NOT a dedicated page/tab, NOT a
  popup/modal. Explicitly chosen over those alternatives when asked.
- Shows recent changes + version number, visible to all visitors (not admin-only).
- **Versioning scheme, owner's own call, makes sense — don't second-guess this:**
  no retroactive version numbers for all the internal work leading up to launch.
  **v1.0 = the moment the site link is first shared publicly** (e.g. posted in the
  GRF Discord). Everything before that is prep work, not a versioned release.
- Content should be player-relevant, in plain user-facing language — NOT a raw
  commit log. E.g. "Neue Filter: Untergrund, Antriebsart, Ära" (yes, a real
  feature, belongs in) vs. "elif-Bug in sync_event() gefixt" (too technical,
  would be rephrased as something like "Verbesserte Genauigkeit bei Gesamtzeiten
  nach Stage-Strafen" if included at all — judgment call each time on whether a
  backend fix is even worth mentioning to players).
- **Workflow for writing entries:** Claude and owner draft the changelog text
  together in chat, owner copy-pastes the finished text into a new Admin panel
  field alongside the version number — no code change needed to publish a new
  changelog entry once this is built.

**Implementation direction (agreed, not built):**
- New Supabase table, something like `changelog(id, version, date, entries)` —
  `entries` could be a text blob or a JSON array of bullet strings, decide when
  actually building this (either works for a simple copy-paste workflow).
  **Needs correct RLS from the start, unlike some existing tables** (see SECURITY
  section) — `anon` gets SELECT-only, only the service_role key (via `admin_api.py`,
  password-protected) can INSERT/UPDATE. Good opportunity to set the right pattern
  since it's a brand-new table, not a retrofit.
- `admin_api.py`: new endpoint(s) to write a changelog entry (behind `@auth`, same
  password protection as everything else in Admin).
- `index.html`: a new small home-page widget reading from this table (same
  `sb()`-style read as the other home widgets), styled to match the existing
  Win98 widget look — and a new small form in the Admin tab to add entries.

Not started — this is real code (new table + backend endpoint + two frontend
pieces), not just a text edit. Tackle together with the rest of the visual pass,
whenever that starts.

---
---

# WORK PACKAGES

## ✅ COMPLETED

### Package 1 — Server Hosting & Auto-Update ✅
- GitHub repo `grf-system` live
- Railway.app deployed, cron every 10 min
- `RACENET_REFRESH_TOKEN` stored as Railway environment variable
- `racenet_client.py` bootstraps token from env var on every run
- Auto-refresh confirmed working (access token renewed automatically when expired)
- ~~`grf_sync.py --full` fixed: now loads ALL championships via `get_all_championship_ids()`~~
  **⚠️ This line was WRONG when originally written — the code didn't actually match
  it.** Genuinely fixed this session (see Sync Script section above for the real
  root cause + fix). Verified via code read + `--test --full` dry run + live
  `--full` run in progress at time of writing this update.
- ✅ Cron now covers ALL clubs (was hardcoded to `GRF_CLUBS` = 2 main clubs before)

### Package 2 — Themed Base ✅ (core complete)
- GRF Themed page loads real data from Supabase
- Championship hero: name, season, rounds, status, current round — all dynamic
- Event selector: all rounds sorted by round_number, labelled "RD.1 Portugal" etc.
- Live Results table: 8 columns (pos/driver/vehicle/time/base/×CR/cr pts/total)
- Stage times: expandable per driver row (click to toggle), SS1/SS2... green = stage win
- Event info panel: location, status, start/end dates — dynamic
- Championship Standings: per-round columns, loyalty bonus indicator, driver count
- Car Ratings tab: from car_ratings table (shows "not set" if empty)
- **Still to do in Pkg 2:** Challenges tab (needs Admin first), Team view (Pkg 5)

---

## 📦 PACKAGE 3 — Admin Panel (full)
**Priority: HIGH — next up**
**Files needed:** current HTML, briefing

### Goal
Admin can manage everything from the website. No manual Supabase editing needed.

**⚠️ See "Admin Create Championship Full New" section above (after Notes for Claude)
for the agreed redesign of Championship Setup specifically — discussed and agreed
this session, not built yet. Read that before starting this package's Championship
Setup tasks below, it changes the approach.**

### Tasks
**Championship Setup:**
- [ ] Form writes to `championships` (name, club, class, season, narrative)
- [ ] Bonus rules configurator → writes to `championship_rules`
- [ ] Upcoming championship display: calendar, class, club, narrative visible before season

**Car Ratings (CR) entry:**
- [ ] Per championship: input vehicle name + CR value → saves to `car_ratings`
- [ ] List existing CR entries, allow edit/delete

**Running Championship:**
- [ ] Add/edit manual bonus points per driver per event → `manual_bonuses`
- [ ] Auto-applies to `event_results.bonus_points` + `total_points` on save
- [ ] Edit event narrative
- [ ] Edit championship narrative

**Challenges / Rules:**
- [ ] Configure championship_rules per championship (type, points, active toggle)
- [ ] This unlocks the Challenges tab on the Themed page

---

## 📦 PACKAGE 4 — ELO System
**Priority: HIGH**
**Files needed:** `elo_pipeline.py`, `elo_state.py`, `elo_excel.py`, current HTML, briefing

### Goal
Chronological ELO from ALL historical events. No static baseline — every past event
counted in order by date. Filters by surface/drivetrain/era. Most Improved widget live.

### Tasks
**Supabase:**
- [ ] Add `elo_history` table: driver_name, elo_before, elo_after, delta, event_id, recorded_at
  *(already in schema doc — not specifically re-verified this session whether it's
  actually written to by elo_update(), see ELO System section note above)*
- [x] ~~Add `vehicles` lookup table~~ **Done differently:** two static JSON files
  instead of a Supabase table (game gets no more updates, values are permanent —
  see Database Schema section). `category_lookups_surface.json` +
  `category_lookups_vehicles.json`, wired into `elo_update()` via `build_lookups()`.
- [ ] Update `drivers` table: rename/add `events_total`, `events_live` columns
  *(NOT verified this session — the 5 columns confirmed present were `elo_mu`,
  `elo_sigma`, `elo_events`, `elo_provisional`, `elo_inactive`, which is a
  different set. `events_total`/`events_live` status unknown, check before Package 4 work.)*
- [x] ~~Populate `vehicles` table (one-time, from known EA WRC car list)~~ **Done
  differently, via JSON files:** 20 locations → surface, 99 vehicles → drivetrain+era.
  Sourced from RaceNet itself (own `/api/wrc2023Stats/values` class data + public
  leaderboards), NOT external/real-world car data — deliberate choice since the
  game's own class taxonomy doesn't always match real-world car specs. Era cutoff:
  2012+ = modern, everything before = historic (owner's rule, see ELO System section).

**Script:**
- [x] Sort ALL events by `start_date` + `round_number` globally across all championships
  *(already implemented in `admin_api.py` before this session; confirmed correct via
  functional test with out-of-order input this session)*
- [x] Run ELO calculation chronologically through every event (historical + future)
  *(same — pre-existing, confirmed via test; also the dead Bradley-Terry/CSV-baseline
  code paths that could have bypassed this were removed this session, see ELO System section)*
- [~] Write to `drivers.elo` + `elo_history` after each event
  *(drivers.elo/.elo_mu/.elo_sigma/.elo_events/.elo_provisional/.elo_inactive write
  confirmed in code — elo_history write NOT specifically checked this session)*
- [ ] Increment `events_total` for every event, `events_live` only post-deployment
  *(confirmed NOT wired up anywhere in current code — flagged as open gap this session)*
- [x] Admin can trigger full ELO recalculation from website
  *(confirmed working: club checkboxes + force_reset toggle in Admin panel,
  calls `/elo/update` correctly)*

**HTML:**
- [ ] Home: Most Improved last 7 days from `elo_history` (real data) *(not reviewed this session)*
- [ ] ELO page: full table, filters (established/provisional, per club, surface, drivetrain, era)
  *(backend data for surface/drivetrain/era filters is now live and ready to query —
  frontend filter UI itself not reviewed/built this session)*
- [ ] ELO page: sort by ELO / most events / most wins *(not reviewed this session)*
- [ ] ELO page: show `events_total` and `events_live` as separate columns
  *(blocked on the events_total/events_live wiring gap above)*

---

## 📦 PACKAGE 5 — GRF Teamed (full)
**Priority: MEDIUM**
**Files needed:** current HTML, briefing

### Goal
Full team-based championship page. Same data pipeline as Themed, results grouped by team.
Team Builder in Admin for future player-created teams.

### Tasks
**Admin:**
- [ ] Create Teamed championship (same form + team size + ELO budget per team)
- [ ] Team Builder: assign drivers from ELO pool to teams, lock before season
- [ ] Future: team leaders build own teams (low priority)

**HTML — Teamed page:**
- [ ] Same sub-tabs as Themed (Live Results / Standings / Car Ratings / Challenges)
- [ ] Additional: Team Standings (combined points per team per event)
- [ ] Individual driver results visible within team context
- [ ] Both individual and team points shown

---

## 📦 PACKAGE 6 — Archive
**Priority: MEDIUM**
**Files needed:** current HTML, briefing

### Goal
All past championships browsable with full results.

### Tasks
- [ ] Archive page: list past championships (season, dates, winner, rounds)
- [ ] Click → full standings + event-by-event results
- [ ] Each event expandable with stage results

---

## 📦 PACKAGE 7 — Statistics
**Priority: LOW-MEDIUM**
**Files needed:** current HTML, briefing

### Goal
Community stats page — records and fun facts, all from existing tables.

### Tasks
- [ ] Most championship wins (driver)
- [ ] Most stage wins (driver)
- [ ] Most championship entries (driver)
- [ ] Favorite car per driver (most used vehicle)
- [ ] Favorite location per driver
- [ ] Underdog driver (most non-meta car usage — lowest average CR)
- [ ] Total rallies / stages driven GRF all-time
- [ ] Longest DNF-free streak

---

## 📦 PACKAGE 8 — Stage Info Cards
**Priority: LOW — nice to have**
**Files needed:** Stage info Excel file, current HTML

### Goal
Show stage details (length, surface condition, elevation profile) alongside event results.
Data already exists in an Excel file — just needs importing.

### Tasks
- [ ] Add `stage_info` table to Supabase: location, stage_name, length_km, surface, conditions (dry/wet/ice/snow), elevation_profile (JSON array)
- [ ] One-time import script: reads Excel → writes to Supabase
- [ ] Match by location + stage_name (note: Chile location names may differ from RaceNet — verify)
- [ ] Website: elevation profile shown as small SVG chart in stage row
- [ ] Website: surface condition badge (dry/wet/ice/snow) next to stage name
- [ ] Hidden by default, visible on stage expand

---

## 📦 PACKAGE 9 — Other GRF Clubs
**Priority: LOW**
**Files needed:** current HTML, briefing, list of other GRF club IDs

### Goal
All GRF Discord clubs visible on website. Standard clubs use RaceNet points directly.
Any club can optionally use the full custom points pipeline — zero extra effort for creators.

### Tasks
- [ ] Add `clubs` table: club_id, name, description, creator, uses_custom_points (bool)
- [ ] Standard clubs: sync uses RaceNet points as-is (no CR, no custom rules)
- [ ] Custom clubs: full pipeline available on request
- [ ] Home: "GRF Clubs" section listing all clubs
- [ ] Each club links to its results page (auto-adapts to standard or custom)
- [ ] Fallback: name + link to RaceNet page if not fully integrated

---

## 📦 PACKAGE 10 — Discord Bot
**Priority: LOW**
**Files needed:** `grf_sync.py`, briefing

### Goal
Own GRF Discord bot replacing FourLeft dependency. Runs on same Railway server as sync.
Understands custom points, posts results automatically.

### Tasks
- [ ] Create bot via Discord Developer Portal
- [ ] Host on Railway (same server as sync, no extra cost)
- [ ] Commands: `!nextevent`, `!standings`, `!elo [@player]`, `!results [round]`, `!cr`
- [ ] Auto-post results to Discord after each completed event sync
- [ ] Auto-post ELO movers after ELO update
- [ ] Admin-only commands (role restricted)

---

## File checklist per session

| Package | Files to bring |
|---|---|
| 3 — Admin | current `index.html`, briefing |
| 4 — ELO | `elo_pipeline.py`, `elo_state.py`, current `index.html`, briefing |
| 5 — Teamed | current `index.html`, briefing |
| 6 — Archive | current `index.html`, briefing |
| 7 — Stats | current `index.html`, briefing |
| 8 — Stage Info | stage info Excel, current `index.html`, briefing |
| 9 — Other Clubs | current `index.html`, briefing, GRF club IDs list |
| 10 — Discord Bot | `grf_sync.py`, briefing |
