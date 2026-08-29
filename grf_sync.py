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
  python grf_sync.py --standings-only   — NUR championship_standings für JEDE
                                           Championship jedes Clubs nachladen (RaceNets
                                           fertigen Punktestand über den
                                           /championship/points/-Endpoint). Kein Event-/
                                           Stage-/Result-Sync, deutlich leichtgewichtiger
                                           als --full. Gedacht für den Fall, dass
                                           historische Championships nie ihre Standings
                                           bekommen haben (z.B. weil
                                           sync_championship_standings() erst nachträglich
                                           eingebaut wurde, s. Session 10).
  python grf_sync.py --test             — test connections, no writes
════════════════════════════════════════════════════════════════════════════════
"""

import sys
import os
import time
import requests
from datetime import datetime, timezone
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


def pg_in_list(values: list[str]) -> str:
    """
    Baut die Werteliste für einen PostgREST `in.(...)`-Filter aus Freitext-
    Werten (z.B. Fahrernamen, die roh/ungefiltert von RaceNet kommen und
    Kommas oder Anführungszeichen enthalten könnten). Jeder Wert wird in
    doppelte Anführungszeichen gesetzt, enthaltene " werden auf "" verdoppelt
    (PostgREST-CSV-Quoting-Regel) — sonst würde z.B. ein Name mit Komma den
    Filter in mehrere falsche Werte zerreißen.
    """
    return ",".join('"' + v.replace('"', '""') + '"' for v in values)


def get_base_points(position: int, is_dnf: bool) -> int:
    if is_dnf or position <= 0:
        return DNF_POINTS
    if position <= len(POINTS_TABLE):
        return POINTS_TABLE[position - 1]
    return POINTS_TABLE[-1]


# ─────────────────────────────────────────────────────────────────────────────
#  CUSTOM SCORING — pro-Championship-Overrides für Clubs mit eigenem
#  Punktesystem (abweichend vom GRF-Standard oben). Bewusst nach
#  championship_id geschlüsselt, nicht nach club_id — gilt NUR für die
#  explizit eingetragene Championship, nicht automatisch auch für eine
#  spätere Saison desselben Clubs (die müsste bei Bedarf separat
#  eingetragen werden). Kein Admin-UI dafür (Owner-Entscheidung,
#  Session 10) — wird direkt hier im Code gepflegt.
#
#  Frontend hat dieselben Zahlen zur Anzeige (index.html, CLUB_SCORING_INFO)
#  — beide Stellen müssen bei Änderungen synchron gehalten werden.
# ─────────────────────────────────────────────────────────────────────────────

CUSTOM_SCORING = {
    # Club 14317, upcoming championship (Session 10)
    "2pBfWYxRdRTDb1tCP": {
        "position_points":    [20, 16, 14, 12, 10, 8, 6, 5, 4, 3, 2, 1, 0],
        "dnf_points":         0,
        "stage_bonus_points": 1,  # pro gewonnener Stage, AUCH bei DNF (falls vor dem Ausfall gewonnen)
    },
}


def get_base_points_for(position: int, is_dnf: bool, championship_id: str) -> int:
    """Wie get_base_points(), respektiert aber einen CUSTOM_SCORING-Override
    für die übergebene championship_id, falls vorhanden."""
    cfg = CUSTOM_SCORING.get(championship_id)
    if not cfg:
        return get_base_points(position, is_dnf)
    if is_dnf or position <= 0:
        return cfg["dnf_points"]
    table = cfg["position_points"]
    if position <= len(table):
        return table[position - 1]
    return table[-1]


def get_stage_bonus_points_for(championship_id: str) -> int:
    """Punkte pro Stage-Sieg für die übergebene championship_id — 0 für alle
    Clubs ohne CUSTOM_SCORING-Eintrag (GRF-Standard kennt keinen Stage-Bonus)."""
    cfg = CUSTOM_SCORING.get(championship_id)
    return cfg["stage_bonus_points"] if cfg else 0


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
        # Egress-Diagnose (Owner-Meldung, ~140MB/Tag trotz vorheriger Fixes):
        # zeichnet jeden Lesezugriff auf (Tabelle, Bytes, Zeilen), damit
        # main() am Ende eine echte Aufschlüsselung ausgeben kann — Messwerte
        # statt Schätzung.
        self.read_log: list = []

    def select(self, table: str, filters: str = "") -> list:
        url = f"{self.url}/rest/v1/{table}"
        if filters:
            url += f"?{filters}"
        r = requests.get(url, headers=self.headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        self.read_log.append((table, len(r.content), len(data) if isinstance(data, list) else 1))
        return data

    def count(self, table: str, filters: str = "") -> int:
        """
        Nur die ANZAHL passender Zeilen, ohne die Zeilen selbst zu laden —
        HEAD-Request mit "Prefer: count=exact", die Zahl steht im
        Content-Range-Response-Header ("0-0/42"), der Body bleibt leer.
        Praktisch egress-frei im Vergleich zu select_all() für denselben
        Filter, wenn nur die Anzahl gebraucht wird.
        """
        url = f"{self.url}/rest/v1/{table}"
        if filters:
            url += f"?{filters}"
        h = {**self.headers, "Prefer": "count=exact"}
        r = requests.head(url, headers=h, timeout=15)
        r.raise_for_status()
        content_range = r.headers.get("content-range", "")  # z.B. "0-0/42" oder "*/42"
        try:
            return int(content_range.split("/")[-1])
        except (ValueError, IndexError):
            return 0

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

    def upsert(self, table: str, data: dict | list, on_conflict: str = "id") -> None:
        # Egress-Fix (Session 10): return=minimal statt return=representation —
        # kein Aufrufer dieser Methode hat je den Rückgabewert genutzt (geprüft,
        # alle 8 Aufrufstellen), Supabase hat also bei jedem einzelnen Schreib-
        # vorgang unnötig die komplette geschriebene Zeile zurückgeschickt.
        if isinstance(data, dict):
            data = [data]
        h = {**self.headers,
             "Prefer": "resolution=merge-duplicates,return=minimal"}
        r = requests.post(
            f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}",
            headers=h, json=data, timeout=15,
        )
        r.raise_for_status()

    def insert_ignore(self, table: str, data: list, on_conflict: str = "id") -> None:
        """Insert, silently skip duplicates."""
        if not data:
            return
        h = {**self.headers,
             "Prefer": "resolution=ignore-duplicates,return=minimal"}
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

    # Stage-Siege pro Fahrer zählen (für CUSTOM_SCORING-Stage-Bonus, s.o.).
    # entries ist bereits nach Zeit sortiert (Index 0 = schnellste Zeit,
    # s. stage_rows-Aufbau weiter oben, das denselben Index direkt als
    # 1-basierten Rang verwendet) — Sieger einer Stage ist also entries[0],
    # sofern die Zeit nicht als DNF zählt. Für Clubs OHNE CUSTOM_SCORING-
    # Eintrag ist stage_bonus_pts weiter unten ohnehin 0, diese Zählung
    # kostet dort also nur ein paar überflüssige Dict-Updates, kein
    # zusätzlicher Netzwerk-/DB-Aufwand.
    stage_wins_by_driver: dict[str, int] = {}
    for _stage, entries in stage_data:
        if not entries:
            continue
        first = entries[0]
        t_ms  = time_str_to_ms(first.get("time", ""))
        if t_ms is not None and not is_dnf_ms(t_ms):
            name = first.get("displayName", "")
            if name:
                stage_wins_by_driver[name] = stage_wins_by_driver.get(name, 0) + 1

    stage_bonus_pts = get_stage_bonus_points_for(championship_id)

    event_rows = []

    for pos, name in enumerate(finishers, start=1):
        info  = driver_info[name]
        base  = get_base_points_for(pos, False, championship_id)
        cr    = car_ratings.get(info["vehicle"], 1.0)
        crpts = round(base * cr, 2)
        bonus = stage_wins_by_driver.get(name, 0) * stage_bonus_pts
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
            "bonus_points":     bonus,
            "total_points":     round(crpts + bonus, 2),
        })

    for name in dnfs:
        info  = driver_info[name]
        base  = get_base_points_for(0, True, championship_id)
        cr    = car_ratings.get(info["vehicle"], 1.0)
        crpts = round(base * cr, 2)
        # Stage-Bonus bleibt bei DNF erhalten (für Stages VOR dem Ausfall
        # gewonnen) — nur die Positions-Punkte fallen weg. Spec-Entscheidung
        # Session 10, gilt nur für Clubs mit CUSTOM_SCORING (stage_bonus_pts
        # ist sonst 0, hat also für den GRF-Standard keine Auswirkung).
        bonus = stage_wins_by_driver.get(name, 0) * stage_bonus_pts
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
            "bonus_points":     bonus,
            "total_points":     round(crpts + bonus, 2),
        })

    n_fin = len(finishers)
    n_dnf = len(dnfs)
    log(f"      ✅ {n_fin} finisher(s) | {n_dnf} DNF | CR: {bool(car_ratings)}")

    # Nur im --test-Modus: volle Ergebnisliste loggen (Position, Fahrer, Zeit,
    # Stages) — für gezielte Kontrolle einzelner Events (z.B. reload_single_
    # events.py --test) ohne die Live-Logs bei normalen Cron-Läufen
    # aufzublähen (die haben test=False, dieser Block feuert dort nie).
    if test and event_rows:
        for r in sorted(event_rows, key=lambda x: x["position"]):
            log(f"         P{r['position']:>2}  {r['driver_name']:<20} "
                f"{r['time']:>14}  stages={r['stages_completed']}"
                f"{'  DNF' if r['is_dnf'] else ''}")

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
    # Egress-Fix (Session 10): statt der KOMPLETTEN drivers-Tabelle (2200+
    # Namen) bei jedem Event-Sync nur die Namen abfragen, die in DIESEM Event
    # tatsächlich mitgefahren sind (driver_info, typischerweise 20-40 Fahrer).
    # Kein club_id/championship_id-Filter — bewusst weiterhin fahrer-, nicht
    # event-gescoped (siehe Schritt 6).
    existing_drivers: set = set()
    if not test and driver_info:
        names = list(driver_info.keys())
        name_list = pg_in_list(names)
        existing_drivers = {
            r["name"] for r in db.select_all("drivers", f"name=in.({name_list})&select=name")
        }
        new_drivers = [
            {"name": name, "elo": 1000, "wins": 0, "starts": 0, "country": ""}
            for name in names
            if name and name not in existing_drivers
        ]
        if new_drivers:
            db.insert_ignore("drivers", new_drivers, on_conflict="name")
            log(f"      👤 {len(new_drivers)} new driver(s) added")
            existing_drivers |= {d["name"] for d in new_drivers}

    # ── 6. Update driver stats (starts, wins) ───────────────────────────────
    # Egress-Fix (Owner-Meldung, ~140MB/Tag): DIES war der eigentliche
    # Hauptverursacher, bestätigt durch die Log-Diagnose (968.5 KB von
    # 1087.4 KB grf_sync.py-Egress in einem einzigen Lauf, ~89%). Die
    # frühere "Session 10"-Optimierung (nur die Fahrer DIESES Events statt
    # der ganzen Liga) hat das Problem verkleinert, aber nicht behoben: für
    # jeden Fahrer wird weiterhin seine KOMPLETTE Ergebnis-Historie (alle
    # Events, alle Clubs, seit jeher) neu geladen, nur um starts/wins neu
    # zu zählen — UND das passierte bei einem noch laufenden (status=1)
    # Event bei JEDEM der 144 täglichen 10-Minuten-Läufe erneut, nicht nur
    # einmal.
    #
    # Fix: läuft jetzt NUR NOCH, wenn das Event tatsächlich FERTIG ist
    # (status==2) — nicht bei jedem Zwischenstand eines laufenden Events.
    # Das ist sicher, weil sync_event() für ein Event mit status==2 UND
    # bereits vorhandenen stage_results beim nächsten Lauf ohnehin
    # komplett übersprungen wird (s. Skip-Logik in sync_championship()) —
    # der teure Vollrecompute läuft dadurch effektiv genau EINMAL pro
    # Event-Lebenszyklus (wenn es fertig wird), nicht mehr wiederholt
    # während der gesamten ~1 Woche, die ein Event typischerweise läuft.
    # Nebeneffekt (bewusst in Kauf genommen): starts/wins eines Fahrers
    # zählen ein laufendes Event erst mit, sobald es fertig ist, nicht
    # schon während es noch läuft — bei ~1 Woche Eventdauer ein kleiner,
    # sinnvoller Kompromiss für die massive Egress-Einsparung.
    if not test and driver_info and ev_status == 2:
        try:
            names = list(driver_info.keys())
            name_list = pg_in_list(names)
            their_results = db.select_all(
                "event_results", f"driver_name=in.({name_list})&select=driver_name,position,is_dnf"
            )
            stats: dict = {}
            for r in their_results:
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

def sync_championship_standings(db: SupabaseClient, client, club_id: str, champ_id: str, test: bool = False):
    """
    Lädt RaceNets eigenen (dynamischen, fahreranzahl-abhängigen) Championship-
    Punktestand über den bislang ungenutzten /championship/points/-Endpoint
    (racenet_client.get_championship_standings, existierte im Client schon,
    wurde bisher nirgends aufgerufen) und speichert ihn in
    championship_standings. Wir bauen RaceNets Punkteformel NICHT selbst
    nach — RaceNet liefert das fertig berechnete Ergebnis direkt mit.
    Bewusst delete-then-insert (nicht nur upsert): RaceNets Antwort ist ein
    kompletter Snapshot, kein Delta — Fahrer, die z.B. aus der Championship
    entfernt wurden, sollen dadurch auch bei uns verschwinden.
    """
    try:
        # max_results=100 ist der DEFAULT in racenet_client.py — bricht die
        # cursor-Pagination künstlich nach der ersten Seite ab, obwohl die
        # Funktion selbst problemlos beliebig viele Seiten laden kann.
        # Championships mit mehr als 100 Fahrern wurden dadurch stumm
        # abgeschnitten (sichtbar z.B. als exakt "100 driver(s)" in vielen
        # aufeinanderfolgenden Log-Zeilen — kein Zufall, sondern genau die
        # Kappung). 10.000 ist komfortabel über jeder realistischen
        # Championship-Größe; die Schleife bricht ohnehin von selbst ab,
        # sobald RaceNet kein cursorNext mehr liefert.
        entries = client.get_championship_standings(club_id, champ_id, max_results=10000)
    except Exception as ex:
        log(f"    ⚠ Could not load championship standings: {ex}")
        return

    if not entries:
        return

    rows = []
    for e in entries:
        name = e.get("displayName", "")
        if not name:
            continue
        rows.append({
            "championship_id":    champ_id,
            "driver_name":        name,
            "rank":                e.get("rank"),
            "points_accumulated": e.get("pointsAccumulated"),
        })

    if not test and rows:
        db.delete("championship_standings", f"championship_id=eq.{champ_id}")
        db.upsert_all("championship_standings", rows, on_conflict="championship_id,driver_name")
        log(f"    🏆 Standings synced: {len(rows)} driver(s)")


def _fill_season_numbers(db: SupabaseClient, club_id: str, log):
    """
    Setzt season_number automatisch für Championships eines Clubs, wo noch
    keine manuell gesetzt wurde: älteste Championship = Season 1,
    aufsteigend nach start_date. Bereits gesetzte Werte (egal ob vom Admin
    manuell oder aus einem früheren Lauf dieser Funktion) werden NIE
    überschrieben — die Funktion befüllt ausschließlich echte NULL-Werte.

    Bewusst pro Club einzeln aufgerufen (nicht global über alle Clubs
    hinweg) — die Nummerierung ist club-relativ, nicht club-übergreifend.
    """
    # Egress-Fix (Owner-Meldung, ~140MB/Tag trotz vorheriger Fixes): diese
    # Funktion lief bisher bei JEDEM 10-Minuten-Sync mit `select_all()` über
    # die KOMPLETTE Championship-Historie eines Clubs (bei mittlerweile
    # 140+ Championships pro Club × 11 Clubs × 144 Läufe/Tag ein
    # signifikanter, fast immer unnötiger Dauer-Verbrauch — season_number
    # ist so gut wie nie noch NULL, nachdem der erste Durchlauf alles
    # aufgefüllt hat). Jetzt: nur Championships MIT season_number IS NULL
    # laden — nach dem ersten Auffüllen ist das praktisch immer eine leere
    # oder sehr kurze Liste statt der ganzen Historie.
    #
    # select=* (komplette Zeile, nicht nur einzelne Spalten): zwei Runden
    # zuvor scheiterte der Upsert erst an "name", dann an "club_id" NOT
    # NULL — beide standen nicht im (veralteten) supabase_schema.sql als
    # NOT NULL. Statt eine dritte fehlende Spalte zu raten: komplette
    # bestehende Zeile laden und unverändert mit der neuen season_number
    # zurückschicken — unabhängig davon, welche Spalten tatsächlich NOT
    # NULL sind. Bleibt trotzdem klein, da nur die (üblicherweise 0 oder
    # sehr wenigen) noch unnummerierten Zeilen betroffen sind, nicht die
    # ganze Historie.
    champs = db.select_all(
        "championships",
        f"club_id=eq.{club_id}&season_number=is.null&select=*"
    )
    if not champs:
        return

    # Für die korrekte Season-Nummer (Rang unter ALLEN Championships des
    # Clubs, nicht nur den noch unnummerierten) brauchen wir trotzdem die
    # Gesamtzahl der bereits nummerierten — aber nur EIN Zähl-Wert, nicht
    # jede einzelne Zeile.
    #
    # Bekannte, bewusst in Kauf genommene Einschränkung: diese Annahme geht
    # davon aus, dass neu entdeckte (season_number IS NULL) Championships
    # chronologisch NEUER sind als alle bereits nummerierten — im normalen
    # 10-Minuten-Sync praktisch immer der Fall, da RaceNet Championships
    # fortlaufend anlegt, keine rückwirkend eingefügten alten Saisons.
    # Einziger Fall, wo das kippen könnte: ein `--full`-Lauf entdeckt eine
    # ÄLTERE, bisher nie synchronisierte historische Championship NACHDEM
    # neuere bereits nummeriert wurden — dann bekäme sie fälschlich eine zu
    # hohe statt einer niedrigeren Nummer. Sehr seltener Fall (season_number
    # ist ohnehin nur Anzeige, keine funktionskritische Zahl); der volle,
    # korrekte Rebuild über die gesamte Historie ist bewusst nicht mehr Teil
    # des routinemäßigen 10-Minuten-Laufs, aus Egress-Gründen.
    already_numbered = db.count("championships", f"club_id=eq.{club_id}&season_number=not.is.null")

    champs.sort(key=lambda c: c.get("start_date") or "")
    updates = []
    for i, c in enumerate(champs, start=1):
        row = dict(c)  # komplette bestehende Zeile unverändert übernehmen
        row["season_number"] = already_numbered + i
        updates.append(row)

    if updates:
        db.upsert_all("championships", updates, on_conflict="id")
        log(f"  🔢 Season numbers filled: {len(updates)} championship(s)")


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
        ev_settings_bf = event.get("eventSettings") or {}
        ev_location_bf = ev_settings_bf.get("location") or ev_settings_bf.get("locationName") or ""
        ev_start_bf, ev_end_bf = extract_dates(event)

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
        #
        # Kein log() mehr pro geskipptem Event (vorher hier UND bei den Skip-
        # Meldungen unten) — bei hunderten bereits synchronisierten Events in
        # einem Lauf feuerte das ungebremst (kein time.sleep() im Skip-Pfad,
        # der `continue` unten überspringt den Sleep am Ende der Schleife) und
        # hat Railways 500-Logs/Sekunde-Limit gerissen. Skips zählen weiterhin
        # still mit (`skipped`-Zähler), sichtbar über die vorhandene
        # "📊 Synced: X | Skipped: Y"-Zusammenfassung am Ende. Der Date-Log
        # bleibt nur für tatsächlich verarbeitete Events (aktiv/neu/force-reload).
        if not force_stage_reload:
            has_stage_results = db.exists("stage_results", f"event_id=eq.{ev_id}")
            ev_status = event.get("status", 0)

            # Completed with data: skip stage-level reload (dates already backfilled above)
            if ev_status == 2 and has_stage_results:
                skipped += 1
                continue
            # Status==0 with data already: skip (past championship events)
            elif ev_status == 0 and has_stage_results:
                skipped += 1
                continue

            log(f"      📅 Rd.{round_num} dates: {ev_start_bf or '?'} → {ev_end_bf or '?'}")
            # Active event (status==1): always re-sync (live updates)
            if ev_status == 1:
                log(f"    🟢 Rd.{round_num} {ev_name} — active, syncing...")
            # No data yet: always try to sync
            else:
                log(f"    ↻  Rd.{round_num} {ev_name} — loading...")
        else:
            log(f"      📅 Rd.{round_num} dates: {ev_start_bf or '?'} → {ev_end_bf or '?'}")

        ok = sync_event(db, client, club_id, event,
                        champ_id, car_ratings, round_number=round_num, test=test)
        if ok:
            synced += 1
        time.sleep(0.5)

    log(f"    📊 Synced: {synced} | Skipped: {skipped}")

    sync_championship_standings(db, client, club_id, champ_id, test=test)

    return synced


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    test_mode      = "--test" in sys.argv
    force_full     = "--full" in sys.argv           # visit ALL championships (not just current)
    force_stages   = "--force-stages" in sys.argv    # ALSO re-fetch stage data for already-synced events
    standings_only = "--standings-only" in sys.argv  # NUR championship_standings nachladen (kein Event-/Stage-Sync)

    print("=" * 60)
    print("  GRF Sync Script v3")
    if test_mode:
        print("  Mode: TEST — no writes to Supabase")
    elif standings_only:
        print("  Mode: STANDINGS-ONLY — refresh championship_standings for every championship, no event/stage/result sync")
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

    if standings_only:
        # Leichtgewichtiger Modus (Session 10): NUR championship_standings
        # für JEDE Championship jedes Clubs nachladen — kein
        # get_championship()-Call (der lädt teuer die komplette
        # Championship inkl. aller Events/Stages mit, den brauchen wir
        # hier gar nicht), kein Event-/Stage-/Result-Sync. Spart gegenüber
        # einem vollen --full-Lauf den kompletten, schweren Teil — für den
        # Fall, dass historische Championships nie ihre Standings bekommen
        # haben (z.B. weil sync_championship_standings() erst nachträglich
        # eingebaut wurde).
        for club_id in club_ids:
            log(f"\n🏆 Club {club_id} (standings only)...")
            try:
                champ_ids = client.get_all_championship_ids(club_id)
            except Exception as ex:
                log(f"  ❌ Could not load championship list: {ex}")
                continue

            if not champ_ids:
                log("  ℹ No championships found for this club.")
                continue

            log(f"  Found {len(champ_ids)} championship(s) — refreshing standings for each.")
            for champ_id in champ_ids:
                sync_championship_standings(db, client, club_id, champ_id, test=test_mode)
                time.sleep(0.3)

        elapsed = time.time() - t0
        log(f"\n✅ Standings-only sync complete in {elapsed:.1f}s")
        return

    for club_id in club_ids:
        log(f"\n🏁 Club {club_id}...")
        try:
            club = client.get_club(club_id)
        except Exception as ex:
            log(f"  ❌ Could not load club: {ex}")
            continue

        log(f"  {club.get('clubName', club_id)}")

        # Club-Namen in die clubs-Tabelle schreiben (für den Club-Filter im
        # ELO-Tab auf der Website — vorher gab es nirgends eine Zuordnung
        # club_id -> lesbarer Name, nur die rohe ID).
        club_name = club.get("clubName") or f"Club {club_id}"
        if not test_mode:
            try:
                db.upsert("clubs", {"club_id": club_id, "name": club_name}, on_conflict="club_id")
            except Exception as ex:
                log(f"  ⚠ Could not write club name: {ex}")

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

        # Season-Nummern automatisch auffüllen (Session 10, Owner-Wunsch):
        # älteste Championship eines Clubs = Season 1, aufsteigend nach
        # start_date. Überschreibt NIE einen bereits gesetzten Wert (auch
        # nicht bei --full) — nur dort befüllt, wo season_number aktuell
        # NULL ist, damit ein manuell im Admin-Panel gesetzter Wert
        # garantiert erhalten bleibt.
        if not test_mode:
            try:
                _fill_season_numbers(db, club_id, log)
            except Exception as ex:
                log(f"  ⚠ Could not fill season numbers: {ex}")

    print()
    print("=" * 60)
    print(f"  ✅ Done in {time.time() - t0:.1f}s")
    if test_mode:
        print("  ℹ TEST MODE — nothing written to Supabase")
    print("=" * 60)

    # Egress-Diagnose (Owner-Meldung, ~140MB/Tag trotz vorheriger Fixes):
    # Aufschlüsselung nach Tabelle für ALLE Lesezugriffe dieses grf_sync.py-
    # Laufs selbst (die separate Aufschlüsselung für /elo/update kommt weiter
    # unten mit dessen Response). Absteigend nach Bytes sortiert — die
    # Tabelle ganz oben ist der größte Verdächtige dieses Laufs.
    if db.read_log:
        by_table: dict = {}
        for table, nbytes, nrows in db.read_log:
            e = by_table.setdefault(table, {"bytes": 0, "rows": 0, "calls": 0})
            e["bytes"] += nbytes
            e["rows"]  += nrows
            e["calls"] += 1
        total_bytes = sum(e["bytes"] for e in by_table.values())
        print(f"  📊 grf_sync.py egress this run: {total_bytes/1024:.1f} KB total")
        for table, e in sorted(by_table.items(), key=lambda x: -x[1]["bytes"]):
            print(f"     {table}: {e['bytes']/1024:.1f} KB ({e['rows']} rows, {e['calls']} calls)")
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
    if not test_mode:
        # "Last synced"-Kachel im Frontend-Footer (Session 10) — reiner
        # Zeitstempel, wann der Sync zuletzt tatsächlich durchgelaufen ist.
        # Bewusst HIER (Datensync selbst fertig, bevor der ELO-Update-
        # Trigger unten läuft) statt danach — soll widerspiegeln, wann GRFs
        # eigene Daten zuletzt von RaceNet geholt wurden, nicht wann die
        # ELO-Neuberechnung fertig war.
        try:
            db.upsert("system_config",
                      {"key": "last_sync_at", "value": datetime.now(timezone.utc).isoformat()},
                      on_conflict="key")
        except Exception as ex:
            log(f"  ⚠ Could not write last_sync_at: {ex}")

    # Wiederverwendet dieselbe club_ids-Liste vom Sync-Loop oben (Zeile ~700) —
    # kein zweiter get_active_clubs()-Call nötig, RaceNet-Aufrufe sparen.
    if not test_mode:
        elo_club_ids = club_ids

        # Egress-Fix (Owner-Vorschlag): ELO-Update lief bisher bei JEDEM
        # 10-Minuten-Sync (144x/Tag) — für ein Hobby-Projekt unnötig oft,
        # zumal ELO ohnehin die am wenigsten "live-kritische" Funktion ist
        # (niemand erwartet, dass sich das eigene Rating innerhalb von
        # Minuten nach einem Rennen ändert). Jetzt: automatischer Trigger
        # nur noch 1x/Tag, geprüft über denselben system_config-Zeitstempel-
        # Ansatz wie schon bei last_sync_at/driver_stats_summary. Ergebnisse/
        # Zeiten selbst bleiben davon unberührt — die laufen weiterhin über
        # den normalen Sync alle 10 Minuten, nur die ELO-NEUBERECHNUNG
        # verzögert sich um bis zu 24h. Der manuelle "ELO Update"-Knopf im
        # Admin-Panel nutzt einen ANDEREN Code-Pfad (direkter Browser-Aufruf
        # von /elo/update) und ist von diesem Gate nicht betroffen — jederzeit
        # sofort auslösbar.
        already_today = False
        try:
            existing = db.select("system_config", "key=eq.last_elo_update_at&select=value")
            today_str = datetime.now(timezone.utc).date().isoformat()
            already_today = bool(existing) and str(existing[0].get("value", ""))[:10] == today_str
        except Exception as ex:
            log(f"  ⚠ Could not check last_elo_update_at: {ex}")

        if already_today:
            log("\n🔢 ELO/inactivity update already ran today — skipping automatic trigger.")
        else:
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
                        try:
                            db.upsert("system_config",
                                      {"key": "last_elo_update_at", "value": datetime.now(timezone.utc).isoformat()},
                                      on_conflict="key")
                        except Exception as ex:
                            log(f"  ⚠ Could not write last_elo_update_at: {ex}")
                        # Egress-Diagnose (Owner-Meldung, ~140MB/Tag): Aufschlüsselung
                        # aus der elo_update-Response mit ins Sync-Log übernehmen,
                        # damit alles an einer Stelle in den Railway-Logs steht.
                        by_table = data.get("egress_by_table") or {}
                        if by_table:
                            log(f"     📊 Egress this run: {data.get('egress_kb', '?')} KB total")
                            for table, e in sorted(by_table.items(), key=lambda x: -x[1]["kb"]):
                                log(f"        {table}: {e['kb']} KB ({e['rows']} rows, {e['calls']} calls)")
                    else:
                        log(f"  ❌ ELO update failed: HTTP {resp.status_code} — {resp.text[:200]}")
            except Exception as ex:
                log(f"  ❌ ELO update request failed: {ex}")


if __name__ == "__main__":
    main()
