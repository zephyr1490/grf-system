"""
GRF Sync Script v3
════════════════════════════════════════════════════════════════════════════════
Correct data model:

  Championship
    └── Events
          └── Stages  (each with own leaderboardID)
                └── Stage Results  (individual stage times per driver)
          └── Event Results  (calculated: sum of all stage times per driver)

Key fixes vs v2:
  - Every stage is loaded individually via its own leaderboardID
  - Times stored in milliseconds (integer, no float rounding issues)
  - Event result = sum of all stage times (not last stage only)
  - DNF = driver missing from any stage OR has round-minute penalty time
  - status==0 is NOT used to skip events (unreliable in RaceNet for past champs)
  - Completed events skipped only if stage_results already exist in Supabase

Points:
  base_points  = POINTS_TABLE[event_position]   (DNF = 2)
  cr_points    = base_points × CR               (from car_ratings, default 1.0)
  bonus_points = 0 at sync time                 (added via Admin)
  total_points = cr_points

Loyalty bonus checked by website at championship level — not here.

Usage:
  python grf_sync.py                    — smart sync (current championship only)
  python grf_sync.py --full             — load ALL championships (current + historical)
                                           per club; still smart-skips stage data for
                                           events that already have stage_results
                                           (event/championship dates are always backfilled)
  python grf_sync.py --full --force-stages
                                         — same as --full, but ALSO re-fetches stage
                                           data for events that already have it
                                           (slow — use only if stage data is suspect)
  python grf_sync.py --test             — test connections, no writes
════════════════════════════════════════════════════════════════════════════════
"""

import sys
import os
import time
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ixuhhzdijvtlfdjtrnyi.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")   # service_role key — bypasses RLS, correct for a trusted server-side script. Was hardcoded to the public anon key before — fixed this session.

GRF_CLUBS = ["23799", "23834"]

POINTS_TABLE = [
    50, 44, 40, 38, 36, 34, 32, 30, 28, 26,
    25, 24, 23, 22, 21, 20, 19, 18, 17, 16,
    15, 14, 13, 12, 11, 10,  9,  8,  7,  6
]
DNF_POINTS = 2

# RaceNet encodes DNF as a round-minute penalty >= 4 minutes
DNF_MIN_MS      = 4 * 60 * 1000   # 4 minutes in ms
DNF_MODULUS_MS  = 60 * 1000       # must be exact multiple of 1 minute


# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ─────────────────────────────────────────────────────────────────────────────
#  TIME HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def time_str_to_ms(t: str) -> int | None:
    """
    Convert RaceNet time string to milliseconds.
    Handles: "1:23:45.678"  "12:34.567"  "45.678"  "+0:47.210"
    Returns None if unparseable.
    """
    if not t:
        return None
    try:
        t = t.strip().lstrip("+")
        # Split milliseconds
        if "." in t:
            main, ms_str = t.rsplit(".", 1)
            ms = int(ms_str.ljust(3, "0")[:3])
        else:
            main, ms = t, 0

        parts = main.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            h, m, s = 0, 0, int(parts[0])

        return h * 3_600_000 + m * 60_000 + s * 1_000 + ms
    except Exception:
        return None


def ms_to_display(ms: int | None) -> str:
    """Convert milliseconds to H:MM:SS.mmm display string."""
    if ms is None:
        return "—"
    h  = ms // 3_600_000;  ms %= 3_600_000
    m  = ms // 60_000;     ms %= 60_000
    s  = ms // 1_000;      ms %= 1_000
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"


def is_dnf_ms(ms: int | None) -> bool:
    """
    RaceNet marks DNF with a round-minute penalty time (e.g. 4:00, 6:00, 10:00).
    Condition: >= 4 minutes AND exact multiple of 1 minute (no milliseconds).
    """
    if ms is None:
        return False
    return ms >= DNF_MIN_MS and (ms % DNF_MODULUS_MS) == 0


def get_base_points(position: int, is_dnf: bool) -> int:
    if is_dnf or position <= 0:
        return DNF_POINTS
    if position <= len(POINTS_TABLE):
        return POINTS_TABLE[position - 1]
    return POINTS_TABLE[-1]


def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def extract_dates(obj: dict) -> tuple[str | None, str | None]:
    """Extract start/end dates from any RaceNet object (event, championship, etc.)
    RaceNet stores dates in two ways:
      - Nested in eventSettings/settings/championshipSettings as startDate/endDate
      - Directly on the object as absoluteOpenDate/absoluteCloseDate (events)
    We check both, preferring the direct fields as they are more reliable.
    """
    # Direct fields on the object (events use these)
    direct_start = parse_date(obj.get("absoluteOpenDate"))
    direct_end   = parse_date(obj.get("absoluteCloseDate"))
    if direct_start or direct_end:
        return direct_start, direct_end

    # Nested in settings sub-object (championships may use these)
    s = (obj.get("eventSettings")
         or obj.get("settings")
         or obj.get("championshipSettings")
         or {})
    return (
        parse_date(s.get("startDate") or s.get("start_date")),
        parse_date(s.get("endDate")   or s.get("end_date")),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  SUPABASE CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.url     = url.rstrip("/")
        self.headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
        }

    def select(self, table: str, filters: str = "") -> list:
        url = f"{self.url}/rest/v1/{table}"
        if filters:
            url += f"?{filters}"
        r = requests.get(url, headers=self.headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def select_all(self, table: str, filters: str = "", page_size: int = 1000) -> list:
        """
        Wie select(), aber holt ALLE Zeilen via limit/offset-Pagination.
        Notwendig weil Supabase/PostgREST unpaginierte Reads still auf 1000
        Zeilen kappt — kein Fehler, keine Warnung, einfach weniger Daten.
        (Bug-Klasse aus dem Briefing, Known Issue #5 — dies ist einer der
        bestätigten Fälle: event_results ist mit 25k+ Zeilen weit über dem Cap.)

        Erzwingt eine stabile Sortierung (order=id.asc), falls der Aufrufer
        keine eigene angibt — ohne deterministische Order sind aufeinander-
        folgende limit/offset-Seiten nicht garantiert überlappungsfrei.
        """
        if "order=" not in filters:
            sep = "&" if filters else ""
            filters = f"{filters}{sep}order=id.asc"

        all_rows: list = []
        offset = 0
        while True:
            sep = "&" if filters else ""
            page_filters = f"{filters}{sep}limit={page_size}&offset={offset}"
            page = self.select(table, page_filters)
            if not page:
                break
            all_rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return all_rows

    def upsert(self, table: str, data: dict | list, on_conflict: str = "id") -> list:
        if isinstance(data, dict):
            data = [data]
        h = {**self.headers,
             "Prefer": "resolution=merge-duplicates,return=representation"}
        r = requests.post(
            f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}",
            headers=h, json=data, timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def insert_ignore(self, table: str, data: list, on_conflict: str = "id") -> None:
        """Insert, silently skip duplicates."""
        if not data:
            return
        h = {**self.headers,
             "Prefer": "resolution=ignore-duplicates,return=representation"}
        requests.post(
            f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}",
            headers=h, json=data, timeout=15,
        )

    def upsert_all(self, table: str, rows: list, on_conflict: str, chunk_size: int = 500) -> None:
        """
        Bulk-Upsert in Chunks statt N sequenzieller PATCH-Calls — ein POST pro
        Chunk (Prefer: resolution=merge-duplicates → nur mitgeschickte Spalten
        werden überschrieben, alle anderen bleiben unangetastet).

        Gleiches Muster/gleicher Grund wie admin_api.py's sb_upsert_all():
        seit dem Pagination-Fix (select_all) werden korrekt ALLE Fahrer aus
        event_results für starts/wins verarbeitet statt nur den ersten ~1000 —
        das macht die alte, sequenzielle requests.patch()-Schleife (ein Call
        pro Fahrer) zum dominanten Kostenfaktor in Step 6. Chunked Bulk-Upsert
        macht daraus wenige Calls statt hunderte/tausende.
        """
        if not rows:
            return
        h = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            r = requests.post(
                f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}",
                headers=h, json=chunk, timeout=30,
            )
            if not r.ok:
                print(f"[upsert_all ERROR] {table} chunk {i}-{i+len(chunk)} → "
                      f"HTTP {r.status_code}: {r.text}")
            r.raise_for_status()

    def delete(self, table: str, filters: str) -> None:
        r = requests.delete(
            f"{self.url}/rest/v1/{table}?{filters}",
            headers=self.headers, timeout=15,
        )
        r.raise_for_status()

    def exists(self, table: str, filters: str) -> bool:
        rows = self.select(table, filters + "&limit=1")
        return len(rows) > 0

    def get_car_ratings(self, championship_id: str) -> dict:
        """Returns {vehicle_name: cr_value}. Empty dict if none set yet."""
        try:
            rows = self.select("car_ratings",
                               f"championship_id=eq.{championship_id}")
            return {r["vehicle"]: float(r["cr_value"])
                    for r in rows if r.get("vehicle")}
        except Exception:
            return {}


# ─────────────────────────────────────────────────────────────────────────────
#  LOAD SINGLE STAGE LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

def load_stage_leaderboard(client, club_id: str, lb_id: str) -> list[dict]:
    """
    Load all entries for one stage leaderboard from RaceNet.
    Returns list of raw entry dicts.
    """
    try:
        entries = client.get_event_leaderboard(club_id, lb_id, max_results=500)
        return entries or []
    except Exception as ex:
        log(f"        ⚠ Leaderboard {lb_id} failed: {ex}")
        return []


def load_stage_leaderboards_adaptive(
    client, club_id: str, stage_specs: list[tuple],
    start_concurrency: int = 3, min_concurrency: int = 1,
) -> dict:
    """
    Lädt mehrere Stage-Leaderboards eines Events parallel — mit schrumpfender
    (nie wieder wachsender) Parallelität innerhalb dieses Laufs. "Variante A":
    einfacher, sicherer Startpunkt, kein Hochregeln — jeder neue Sync-Lauf
    startet wieder frisch bei start_concurrency.

    RaceNet hat keine dokumentierte/bekannte Rate-Limit-Angabe (inoffizielle,
    interne API — nachrecherchiert, nichts gefunden), deshalb reagieren statt
    raten: die Parallelität wird nach jedem Batch verkleinert, wenn eines von
    zwei Signalen auftritt:
      - RaceNet hat während des Batches mit 429/5xx geantwortet
        (client.throttle_events ist gestiegen, siehe racenet_client.py._get())
        → Parallelität halbieren.
      - Die durchschnittliche Antwortzeit des Batches ist > 2x so hoch wie die
        des allerersten Batches dieses Events (RaceNet wird spürbar langsamer,
        auch OHNE Fehlercode — deckt z.B. tageszeit-/wochentagsbedingt hohe
        Gesamtlast auf RaceNet ab, nicht nur unsere eigene Anfragerate)
        → Parallelität um 1 verringern.
    Sinkt nie unter min_concurrency, steigt nie über start_concurrency zurück.

    stage_specs: Liste von (i, stage_dict, lb_id) in der Original-Reihenfolge.
    Rückgabe: {lb_id: [entries]} — Verarbeitung/Logging der Ergebnisse bleibt
    beim Aufrufer in Original-Reihenfolge, nur der Netzwerk-Teil läuft parallel.
    """
    results: dict = {}
    concurrency = start_concurrency
    baseline_avg = None
    remaining = list(stage_specs)

    def _fetch(spec):
        _, _, lb_id = spec
        t0 = time.time()
        entries = load_stage_leaderboard(client, club_id, lb_id)
        return lb_id, entries, time.time() - t0

    while remaining:
        batch, remaining = remaining[:concurrency], remaining[concurrency:]
        throttle_before = client.throttle_events
        batch_times = []

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_fetch, spec) for spec in batch]
            for fut in as_completed(futures):
                lb_id, entries, elapsed = fut.result()
                results[lb_id] = entries
                batch_times.append(elapsed)

        avg_time = sum(batch_times) / len(batch_times) if batch_times else 0
        if baseline_avg is None:
            baseline_avg = avg_time

        throttled = client.throttle_events > throttle_before
        new_concurrency = concurrency
        if throttled:
            new_concurrency = max(min_concurrency, concurrency // 2)
            if new_concurrency != concurrency:
                log(f"        ⚠ RaceNet-Throttling erkannt (429/5xx) — "
                    f"parallele Anfragen {concurrency} → {new_concurrency}")
        elif baseline_avg > 0 and avg_time > baseline_avg * 2:
            new_concurrency = max(min_concurrency, concurrency - 1)
            if new_concurrency != concurrency:
                log(f"        ⚠ RaceNet-Antworten werden langsamer "
                    f"({avg_time:.1f}s vs {baseline_avg:.1f}s Baseline) — "
                    f"parallele Anfragen {concurrency} → {new_concurrency}")
        concurrency = new_concurrency

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  SYNC: single event (all stages)
# ─────────────────────────────────────────────────────────────────────────────

def sync_event(db: SupabaseClient, client,
               club_id: str, event: dict,
               championship_id: str, car_ratings: dict,
               round_number: int = 0,
               test: bool = False) -> bool:

    ev_settings = event.get("eventSettings") or {}
    ev_id       = event.get("id")
    ev_name     = ev_settings.get("name") or f"Event {ev_id}"
    ev_location = ev_settings.get("location") or ev_settings.get("locationName") or ""
    ev_status   = event.get("status", 0)
    ev_start, ev_end = extract_dates(event)

    log(f"    🗓  {ev_name} | {ev_location} | status={ev_status}")

    stages = event.get("stages", [])
    if not stages:
        log("      ℹ No stages in this event.")
        # Still write event metadata
        if not test:
            db.upsert("events", {
                "id": ev_id, "championship_id": championship_id,
                "club_id": club_id, "name": ev_name, "location": ev_location,
                "round_number": round_number,
                "start_date": ev_start, "end_date": ev_end, "status": ev_status,
            }, on_conflict="id")
        return False

    log(f"      Loading {len(stages)} stage(s)...")

    # ── 1. Write event metadata ───────────────────────────────────────────────
    if not test:
        db.upsert("events", {
            "id": ev_id, "championship_id": championship_id,
            "club_id": club_id, "name": ev_name, "location": ev_location,
            "round_number": round_number,
            "start_date": ev_start, "end_date": ev_end, "status": ev_status,
        }, on_conflict="id")

    # ── 2. Load every stage individually ─────────────────────────────────────
    # stage_data[stage_index] = list of raw entries from RaceNet
    stage_data: list[tuple[dict, list[dict]]] = []

    # Phase A: Stage-Metadaten schreiben (schnell, reine DB-Writes, bleibt
    # sequenziell — keine RaceNet-Calls hier) + Liste der zu holenden
    # Leaderboards für Phase B sammeln.
    stage_specs = []  # (i, stage, lb_id) — nur Stages MIT leaderboardID
    for i, stage in enumerate(stages, start=1):
        lb_id      = stage.get("leaderboardID")
        stage_id   = stage.get("id") or lb_id or f"{ev_id}_s{i}"
        stage_name = stage.get("name") or f"Stage {i}"

        if not lb_id:
            log(f"        ⚠ Stage {i} ({stage_name}): no leaderboardID — skipping")
            stage_data.append((stage, []))
            continue

        if not test:
            db.upsert("stages", {
                "id":              stage_id,
                "event_id":        ev_id,
                "championship_id": championship_id,
                "club_id":         club_id,
                "name":            stage_name,
                "stage_number":    i,
                "leaderboard_id":  lb_id,
                "status":          stage.get("status", 0),
            }, on_conflict="id")

        stage_specs.append((i, stage, lb_id))

    # Phase B: RaceNet-Leaderboards parallel laden (schrumpfende Parallelität,
    # startet bei 3, reagiert auf 429/5xx oder spürbare Verlangsamung — siehe
    # load_stage_leaderboards_adaptive). Ersetzt das alte strikt-sequenzielle
    # Laden + festes time.sleep(0.3) pro Stage; die Drosselung passiert jetzt
    # dynamisch statt über eine blind feste Pause.
    leaderboards = (
        load_stage_leaderboards_adaptive(client, club_id, stage_specs)
        if stage_specs else {}
    )

    # Phase C: Ergebnisse in Original-Reihenfolge verarbeiten (Logging + DB-
    # Writes) — unabhängig davon, in welcher Reihenfolge die parallelen
    # RaceNet-Calls in Phase B tatsächlich fertig wurden.
    for i, stage, lb_id in stage_specs:
        stage_id   = stage.get("id") or lb_id or f"{ev_id}_s{i}"
        stage_name = stage.get("name") or f"Stage {i}"
        entries    = leaderboards.get(lb_id, [])
        stage_data.append((stage, entries))

        if entries:
            log(f"        Stage {i} ({stage_name}): {len(entries)} entries")
        else:
            log(f"        Stage {i} ({stage_name}): no entries yet")

        # Write stage results
        if not test and entries:
            stage_rows = []
            for rank, entry in enumerate(entries, start=1):
                t_str = entry.get("time", "")
                t_ms  = time_str_to_ms(t_str)
                dnf   = is_dnf_ms(t_ms)
                stage_rows.append({
                    "stage_id":        stage_id,
                    "event_id":        ev_id,
                    "championship_id": championship_id,
                    "driver_name":     entry.get("displayName", ""),
                    "driver_id":       entry.get("ssid", ""),
                    "vehicle":         entry.get("vehicle", ""),
                    "time_ms":         t_ms,
                    "time_str":        t_str,
                    "stage_position":  rank,
                    "is_dnf":          dnf,
                    "platform":        str(entry.get("platform", "")),
                })

            try:
                db.delete("stage_results", f"stage_id=eq.{stage_id}")
            except Exception:
                pass
            for batch_start in range(0, len(stage_rows), 50):
                db.upsert("stage_results",
                          stage_rows[batch_start:batch_start+50],
                          on_conflict="id")
                time.sleep(0.05)

    # ── 3. Calculate event results from stage data ────────────────────────────
    # Collect all drivers and their info (vehicle, driver_id, platform)
    # Use stage 1 as primary source for vehicle/platform
    driver_info: dict[str, dict] = {}
    for _stage, entries in stage_data:
        for entry in entries:
            name = entry.get("displayName", "")
            if name and name not in driver_info:
                driver_info[name] = {
                    "driver_id": entry.get("ssid", ""),
                    "vehicle":   entry.get("vehicle", ""),
                    "platform":  str(entry.get("platform", "")),
                }

    if not driver_info:
        log("      ℹ No drivers found across all stages.")
        return False

    # Sum times per driver; DNF/Finisher decided ONLY by the driver's status
    # on the LAST stage with data — never by what happened on earlier stages.
    #
    # Rules (confirmed with owner):
    #   1. Every stage entry a driver has — real time OR RaceNet's own max/penalty
    #      time — counts fully toward the total. We never compute/guess a max time
    #      ourselves; if RaceNet gives one, we sum it like any other stage time.
    #   2. Finisher vs. DNF is decided EXCLUSIVELY by the LAST stage with data:
    #        - real time there              -> finisher (regardless of earlier stages)
    #        - max/round-number time there  -> DNF (regardless of earlier stages)
    #        - missing entirely there       -> DNF (quit and never came back)
    driver_total_ms:    dict[str, int]  = {n: 0     for n in driver_info}
    driver_is_dnf:      dict[str, bool] = {n: True  for n in driver_info}  # DNF until proven otherwise below
    driver_stages_done: dict[str, int]  = {n: 0     for n in driver_info}

    stages_with_data = [(s, e) for s, e in stage_data if e]

    # 1) Sum every stage entry a driver has, real or max-time alike.
    for _stage, entries in stages_with_data:
        for entry in entries:
            name = entry.get("displayName", "")
            if not name:
                continue
            t_ms = time_str_to_ms(entry.get("time", ""))
            if t_ms is not None:
                driver_total_ms[name]    += t_ms
                driver_stages_done[name] += 1

    # 2) Finisher/DNF decided exclusively by the LAST stage with data.
    #    Anyone not explicitly cleared here (incl. drivers absent from the last
    #    stage entirely) stays DNF from the default above.
    if stages_with_data:
        last_entries = stages_with_data[-1][1]
        for entry in last_entries:
            name = entry.get("displayName", "")
            if not name:
                continue
            t_ms = time_str_to_ms(entry.get("time", ""))
            if t_ms is not None and not is_dnf_ms(t_ms):
                driver_is_dnf[name] = False

    # Split finishers / DNFs and rank
    finishers = sorted(
        [n for n in driver_info if not driver_is_dnf[n]],
        key=lambda n: driver_total_ms[n]
    )
    dnfs = [n for n in driver_info if driver_is_dnf[n]]

    event_rows = []

    for pos, name in enumerate(finishers, start=1):
        info  = driver_info[name]
        base  = get_base_points(pos, False)
        cr    = car_ratings.get(info["vehicle"], 1.0)
        crpts = round(base * cr, 2)
        event_rows.append({
            "event_id":         ev_id,
            "championship_id":  championship_id,
            "driver_name":      name,
            "driver_id":        info["driver_id"],
            "position":         pos,
            "vehicle":          info["vehicle"],
            "platform":         info["platform"],
            "total_time_ms":    driver_total_ms[name],
            "time":             ms_to_display(driver_total_ms[name]),
            "stages_completed": driver_stages_done[name],
            "is_dnf":           False,
            "base_points":      base,
            "cr_multiplier":    cr,
            "cr_points":        crpts,
            "bonus_points":     0,
            "total_points":     crpts,
        })

    for name in dnfs:
        info  = driver_info[name]
        base  = DNF_POINTS
        cr    = car_ratings.get(info["vehicle"], 1.0)
        crpts = round(base * cr, 2)
        event_rows.append({
            "event_id":         ev_id,
            "championship_id":  championship_id,
            "driver_name":      name,
            "driver_id":        info["driver_id"],
            "position":         0,
            "vehicle":          info["vehicle"],
            "platform":         info["platform"],
            "total_time_ms":    None,
            "time":             "DNF",
            "stages_completed": driver_stages_done[name],
            "is_dnf":           True,
            "base_points":      base,
            "cr_multiplier":    cr,
            "cr_points":        crpts,
            "bonus_points":     0,
            "total_points":     crpts,
        })

    n_fin = len(finishers)
    n_dnf = len(dnfs)
    log(f"      ✅ {n_fin} finisher(s) | {n_dnf} DNF | CR: {bool(car_ratings)}")

    # ── 4. Write event results ────────────────────────────────────────────────
    if not test and event_rows:
        try:
            db.delete("event_results", f"event_id=eq.{ev_id}")
        except Exception:
            pass
        for batch_start in range(0, len(event_rows), 50):
            db.upsert("event_results",
                      event_rows[batch_start:batch_start+50],
                      on_conflict="id")
            time.sleep(0.1)

    # ── 5. Ensure drivers exist in drivers table ──────────────────────────────
    existing_drivers: set = set()
    if not test:
        # Paginiert (select_all) statt select — bei 2219+ Fahrern schnitt der
        # alte unpaginierte Read hier still bei ~1000 ab (gleiche Cap-Bug-Klasse
        # wie Step 6 vor dem Fix), was neue Fahrer fälschlich als "neu" markiert
        # hätte (harmlos dank insert_ignore) und v.a. dem Upsert unten in Step 6
        # eine unvollständige Referenzliste gegeben hätte.
        existing_drivers = {r["name"] for r in db.select_all("drivers", "select=name")}
        if driver_info:
            new_drivers = [
                {"name": name, "elo": 1000, "wins": 0, "starts": 0, "country": ""}
                for name in driver_info
                if name and name not in existing_drivers
            ]
            if new_drivers:
                db.insert_ignore("drivers", new_drivers, on_conflict="name")
                log(f"      👤 {len(new_drivers)} new driver(s) added")
                existing_drivers |= {d["name"] for d in new_drivers}

    # ── 6. Update driver stats (starts, wins) ──────────────────────────────────
    if not test:
        try:
            all_results = db.select_all("event_results", "select=driver_name,position,is_dnf")
            stats: dict = {}
            for r in all_results:
                name = r.get("driver_name", "")
                if not name:
                    continue
                s = stats.setdefault(name, {"starts": 0, "wins": 0})
                if not r.get("is_dnf", False):
                    s["starts"] += 1
                    if r.get("position") == 1:
                        s["wins"] += 1
            upsert_rows = [
                {"name": name, "starts": s["starts"], "wins": s["wins"]}
                for name, s in stats.items()
                if name in existing_drivers
            ]
            skipped = len(stats) - len(upsert_rows)
            db.upsert_all("drivers", upsert_rows, on_conflict="name")
            if skipped:
                log(f"      ⚠ Stats: {skipped} driver name(s) not in drivers table, skipped")
        except Exception as e:
            log(f"      ⚠ Stats update failed: {e}")

    return True


# ─────────────────────────────────────────────────────────────────────────────
#  SYNC: championship
# ─────────────────────────────────────────────────────────────────────────────

def sync_championship(db: SupabaseClient, client,
                      club_id: str, champ: dict,
                      test: bool = False, force_stage_reload: bool = False):

    champ_id = champ.get("id")
    if not champ_id:
        log("  ⚠ No championship ID.")
        return

    settings   = champ.get("settings") or champ.get("championshipSettings") or {}
    champ_name = settings.get("name") or champ.get("name") or champ_id
    veh_class  = settings.get("vehicleClass") or ""
    start, end = extract_dates(champ)

    # If championship itself has no dates, derive from its events:
    # start = absoluteOpenDate of first event, end = absoluteCloseDate of last event
    events = champ.get("events", [])
    if not start and events:
        start, _ = extract_dates(events[0])
    if not end and events:
        _, end = extract_dates(events[-1])
    if start:
        log(f"    📅 Championship dates: {start} → {end or '?'}")

    log(f"  📋 {champ_name} ({champ_id})")

    if not test:
        db.upsert("championships", {
            "id":            champ_id,
            "club_id":       club_id,
            "name":          champ_name,
            "start_date":    start,
            "end_date":      end,
            "vehicle_class": veh_class,
        }, on_conflict="id")

    car_ratings = db.get_car_ratings(champ_id)
    if car_ratings:
        log(f"    🚗 CR loaded: {len(car_ratings)} vehicles")
    else:
        log(f"    🚗 No CR set yet — using 1.0 (configure via Admin before season)")

    events  = champ.get("events", [])
    synced  = 0
    skipped = 0
    log(f"    {len(events)} event(s) total.")

    for round_num, event in enumerate(events, start=1):
        ev_id   = event.get("id")
        ev_name = (event.get("eventSettings") or {}).get("name") or ev_id

        # ── Always backfill event metadata, regardless of skip decision below.
        # This is a single cheap Supabase upsert — NOT a RaceNet stage call —
        # so it's safe to run even for events whose stage data we're skipping.
        # This is what fills in start_date/end_date for events that were
        # created earlier (e.g. via Admin's RaceNet import) without dates.
        #
        # Date extraction + log happen OUTSIDE the `if not test` guard (same
        # pattern as the championship-level date log above) so --test shows
        # you exactly what dates WOULD be written, per event, without writing.
        ev_settings_bf = event.get("eventSettings") or {}
        ev_location_bf = ev_settings_bf.get("location") or ev_settings_bf.get("locationName") or ""
        ev_start_bf, ev_end_bf = extract_dates(event)
        log(f"      📅 Rd.{round_num} dates: {ev_start_bf or '?'} → {ev_end_bf or '?'}")

        if not test:
            db.upsert("events", {
                "id": ev_id, "championship_id": champ_id,
                "club_id": club_id, "name": ev_name, "location": ev_location_bf,
                "round_number": round_num,
                "start_date": ev_start_bf, "end_date": ev_end_bf,
                "status": event.get("status", 0),
            }, on_conflict="id")

        # Smart skip: only skip completed events that already have stage_results
        # Do NOT skip based on status==0 alone (unreliable per RaceNet client notes)
        if not force_stage_reload:
            has_stage_results = db.exists("stage_results", f"event_id=eq.{ev_id}")
            ev_status = event.get("status", 0)

            # Active event (status==1): always re-sync (live updates)
            if ev_status == 1:
                log(f"    🟢 Rd.{round_num} {ev_name} — active, syncing...")

            # Completed with data: skip stage-level reload (dates already backfilled above)
            elif ev_status == 2 and has_stage_results:
                log(f"    ✓  Rd.{round_num} {ev_name} — completed & synced, skipping stage reload")
                skipped += 1
                continue
            # Status==0 with data already: skip (past championship events)
            elif ev_status == 0 and has_stage_results:
                log(f"    ✓  Rd.{round_num} {ev_name} — already synced, skipping stage reload")
                skipped += 1
                continue
            # No data yet: always try to sync
            else:
                log(f"    ↻  Rd.{round_num} {ev_name} — loading...")

        ok = sync_event(db, client, club_id, event,
                        champ_id, car_ratings, round_number=round_num, test=test)
        if ok:
            synced += 1
        time.sleep(0.5)

    log(f"    📊 Synced: {synced} | Skipped: {skipped}")
    return synced


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    test_mode    = "--test" in sys.argv
    force_full   = "--full" in sys.argv           # visit ALL championships (not just current)
    force_stages = "--force-stages" in sys.argv    # ALSO re-fetch stage data for already-synced events

    print("=" * 60)
    print("  GRF Sync Script v3")
    if test_mode:
        print("  Mode: TEST — no writes to Supabase")
    elif force_full and force_stages:
        print("  Mode: FULL RE-SYNC — all championships, all stages re-fetched")
    elif force_full:
        print("  Mode: FULL — all championships, smart-skip on stage data")
    else:
        print("  Mode: SMART — all clubs, current championship only, skips completed & synced events")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # RaceNet
    log("Connecting to RaceNet...")
    try:
        from racenet_client import RacenetClient
        client   = RacenetClient()
        identity = client.test_auth()
        log(f"✅ RaceNet: {identity.get('displayName', '?')}")
    except Exception as ex:
        log(f"❌ RaceNet failed: {ex}")
        sys.exit(1)

    # Supabase
    if not SUPABASE_KEY:
        log("❌ SUPABASE_SERVICE_KEY environment variable is not set (or empty).")
        log("   This script now requires the service_role key from Supabase")
        log("   (Dashboard → Settings → API → service_role), set as an environment")
        log("   variable — it no longer has the key hardcoded. If running locally,")
        log("   set it in your shell before running, e.g.:")
        log("   export SUPABASE_SERVICE_KEY='eyJ...'  (Mac/Linux)")
        log("   $env:SUPABASE_SERVICE_KEY='eyJ...'     (Windows PowerShell)")
        sys.exit(1)

    log("Connecting to Supabase...")
    try:
        db = SupabaseClient(SUPABASE_URL, SUPABASE_KEY)
        db.select("championships", "limit=1")
        log("✅ Supabase: connected")
    except Exception as ex:
        log(f"❌ Supabase failed: {ex}")
        sys.exit(1)

    t0 = time.time()
    total_synced = 0

    # Club-Liste: IMMER alle Clubs vom RaceNet-Account (nicht nur GRF_CLUBS) —
    # sowohl im normalen 10-Min-Cron als auch unter --full. Der Unterschied
    # zwischen den beiden Modi ist NICHT "wie viele Clubs", sondern "wie viele
    # Championships pro Club" (siehe force_full-Verzweigung weiter unten:
    # ALLE Championships vs. nur currentChampionship). GRF_CLUBS bleibt nur
    # als Fallback, falls der RaceNet-Call fehlschlägt.
    try:
        all_clubs = client.get_active_clubs()
        club_ids  = [str(c.get("clubID") or c.get("id","")) for c in all_clubs]
        club_ids  = [cid for cid in club_ids if cid]
        if not club_ids:
            club_ids = GRF_CLUBS
        log(f"Syncing {len(club_ids)} club(s) from RaceNet account")
    except Exception as ex:
        log(f"  ⚠ Could not load club list ({ex}), falling back to GRF_CLUBS")
        club_ids = GRF_CLUBS

    for club_id in club_ids:
        log(f"\n🏁 Club {club_id}...")
        try:
            club = client.get_club(club_id)
        except Exception as ex:
            log(f"  ❌ Could not load club: {ex}")
            continue

        log(f"  {club.get('clubName', club_id)}")

        if force_full:
            # Load every championship this club has ever run (current + historical),
            # not just currentChampionship. This is the actual fix for --full.
            try:
                champ_ids = client.get_all_championship_ids(club_id)
            except Exception as ex:
                log(f"  ❌ Could not load championship list: {ex}")
                continue

            if not champ_ids:
                log("  ℹ No championships found for this club.")
                continue

            log(f"  Found {len(champ_ids)} championship(s) for this club (full history).")

            for champ_id in champ_ids:
                try:
                    champ = client.get_championship(club_id, champ_id)
                except Exception as ex:
                    log(f"  ❌ Could not load championship {champ_id}: {ex}")
                    continue

                n = sync_championship(db, client, club_id, champ,
                                       test=test_mode, force_stage_reload=force_stages)
                total_synced += (n or 0)
                time.sleep(0.5)
        else:
            current = club.get("currentChampionship", {})
            if not current or not current.get("id"):
                log("  ℹ No active championship.")
                continue

            n = sync_championship(db, client, club_id, current,
                              test=test_mode, force_stage_reload=force_stages)
            total_synced += (n or 0)

    print()
    print("=" * 60)
    print(f"  ✅ Done in {time.time() - t0:.1f}s")
    if test_mode:
        print("  ℹ TEST MODE — nothing written to Supabase")
    print("=" * 60)

    # ── ELO automatisch aktualisieren ──────────────────────────────────────
    # WICHTIG: läuft bei JEDEM Sync-Durchlauf, nicht nur wenn total_synced > 0.
    # Grund: die Inaktivitäts-Decay-Berechnung (4-Wochen-Frist) hängt vom
    # aktuellen Datum ab, nicht von neuen Ergebnissen. Wäre dieser Trigger an
    # total_synced > 0 gekoppelt, würde die Inaktivitäts-Neuberechnung in
    # Phasen ohne frische Resultate (zwischen Events/Saisons) komplett
    # einfrieren — Fahrer blieben dann unbegrenzt lange fälschlich "aktiv".
    #
    # club_ids ist bewusst NICHT mehr hardcoded auf GRF_CLUBS — die ELO läuft
    # automatisch für ALLE Clubs, die gerade am RaceNet-Account hängen (auch
    # neu dazugekommene, ohne Code-Änderung). force_reset bleibt False (Delta-
    # Update); der einmalige volle Rebuild läuft weiterhin manuell über Admin
    # (Checkbox "alle Clubs" + Force-Reset-Toggle, siehe elo/update im Admin-Tab).
    # Wiederverwendet dieselbe club_ids-Liste vom Sync-Loop oben (Zeile ~700) —
    # kein zweiter get_active_clubs()-Call nötig, RaceNet-Aufrufe sparen.
    if not test_mode:
        elo_club_ids = club_ids

        log(f"\n🔢 Triggering ELO/inactivity update for {len(elo_club_ids)} club(s) "
            f"({total_synced} new event(s) this run)...")
        try:
            admin_api_url = os.environ.get("ADMIN_API_URL", "").rstrip("/")
            admin_api_pw  = os.environ.get("ADMIN_API_PASSWORD", "")
            if not admin_api_url:
                log("  ⚠ ADMIN_API_URL not set — skipping ELO auto-update")
            else:
                resp = requests.post(
                    f"{admin_api_url}/elo/update",
                    headers={"X-Admin-Password": admin_api_pw, "Content-Type": "application/json"},
                    json={"club_ids": elo_club_ids, "force_reset": False},
                    timeout=120,
                )
                if resp.ok:
                    data = resp.json()
                    log(f"  ✅ ELO updated: {data.get('drivers', '?')} drivers")
                else:
                    log(f"  ❌ ELO update failed: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as ex:
            log(f"  ❌ ELO update request failed: {ex}")


if __name__ == "__main__":
    main()
