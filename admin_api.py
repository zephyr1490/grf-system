"""
GRF Admin API — Railway Web Service
════════════════════════════════════════════════════════════════════════════════
Environment Variables (Railway):
  ADMIN_PASSWORD        → Admin-Passwort
  SUPABASE_URL          → https://ixuhhzdijvtlfdjtrnyi.supabase.co
  SUPABASE_SERVICE_KEY  → service_role key
  RACENET_REFRESH_TOKEN → RaceNet Refresh Token
  ALLOWED_ORIGIN        → https://deine-domain.vercel.app
════════════════════════════════════════════════════════════════════════════════
"""

import os, re, hmac, statistics, requests
from flask import Flask, request, jsonify
from functools import wraps
from racenet_client import RacenetClient
from vehicle_classes_data import VEHICLE_CLASSES

app = Flask(__name__)


def _err_detail(e):
    """
    requests' raise_for_status() nur "400 Client Error: Bad Request for url: ..."
    zurueckgibt — die eigentliche PostgREST-Fehlermeldung (z.B. welche Spalte
    fehlt oder welcher Constraint verletzt wurde) steht im Response-Body, den
    str(e) nicht zeigt. Dieser Helper holt den echten Body raus, falls vorhanden.
    Gibt IMMER einen String zurueck (nicht ein dict) — sonst zeigt das Frontend
    nur "[object Object]" an, da jsonify(error=dict) dort als Objekt ankommt.
    """
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.json()
            # PostgREST-Fehler haben fast immer ein "message"-Feld — das ist
            # der eigentlich lesbare Teil, details/hint/code nur als Zusatz.
            if isinstance(body, dict) and body.get("message"):
                extra = f" ({body['details']})" if body.get("details") else ""
                return f"{body['message']}{extra}"
            return str(body)
        except Exception:
            return resp.text
    return str(e)


ADMIN_PASSWORD   = os.environ.get("ADMIN_PASSWORD", "")
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_SVC_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALLOWED_ORIGIN   = os.environ.get("ALLOWED_ORIGIN", "*")

SB = {
    "apikey":        SUPABASE_SVC_KEY,
    "Authorization": f"Bearer {SUPABASE_SVC_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ── CORS ─────────────────────────────────────────────────────────────────────

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"]  = ALLOWED_ORIGIN
    r.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,PATCH,OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type,X-Admin-Password"
    return r

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>",             methods=["OPTIONS"])
def preflight(path): return jsonify({})

# ── AUTH ─────────────────────────────────────────────────────────────────────

def auth(f):
    @wraps(f)
    def inner(*a, **kw):
        if not ADMIN_PASSWORD:
            return jsonify({"error": "ADMIN_PASSWORD not set"}), 500
        pw = request.headers.get("X-Admin-Password") or (request.json or {}).get("password","")
        # hmac.compare_digest() statt != — konstante Vergleichszeit, verhindert
        # Timing-Angriffe auf das Admin-Passwort (Zeichen-für-Zeichen-Ableitung
        # über Antwortzeit-Unterschiede bei frühem Abbruch von !=).
        if not hmac.compare_digest(pw, ADMIN_PASSWORD):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*a, **kw)
    return inner

# ── SUPABASE HELPERS ──────────────────────────────────────────────────────────

# IDs, die vor dem Einbau in einen PostgREST-Filter-String (f"id=eq.{value}")
# geprüft werden müssen — verhindert Filter-Injection über Sonderzeichen wie
# &, =, ,, (), Leerzeichen, Anführungszeichen, die PostgREST's Query-Syntax
# manipulieren könnten (z.B. zusätzliche Filter-Parameter einschleusen und so
# den Scope eines DELETE/PATCH unbeabsichtigt über die eine gemeinte Zeile
# hinaus ausweiten). Deckt die realen ID-Formate im Schema ab: kurze
# alphanumerische Strings wie "3uQ7Za9R6C4NL9oGk", UUIDs, numerische IDs.
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def sb_get(table, qs=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{qs}", headers=SB, timeout=10)
    r.raise_for_status(); return r.json()

def sb_get_all(table, qs="", page_size=1000):
    """
    Wie sb_get(), aber holt ALLE Zeilen via limit/offset-Pagination.
    Supabase/PostgREST kappt unpaginierte Reads still auf 1000 Zeilen — kein
    Fehler, keine Warnung, einfach weniger Daten. (Bug-Klasse aus dem Briefing,
    Known Issue #5 — dies ist der Fall, der den drivers-Namens-Match in
    elo_update() bei 2219 Fahrern kaputt gemacht hat: alles jenseits der ersten
    ~1000 gelesenen Namen wurde als "unmatched" verworfen und nie geschrieben.)

    Erzwingt eine stabile Sortierung (order=id.asc), falls der Aufrufer keine
    eigene angibt — ohne deterministische Order sind aufeinanderfolgende
    limit/offset-Seiten nicht garantiert überlappungsfrei.
    """
    if "order=" not in qs:
        sep = "&" if qs else ""
        qs = f"{qs}{sep}order=id.asc"

    all_rows = []
    offset = 0
    while True:
        sep = "&" if qs else ""
        page_qs = f"{qs}{sep}limit={page_size}&offset={offset}"
        page = sb_get(table, page_qs)
        if not page:
            break
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return all_rows

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB, json=data, timeout=10)
    if not r.ok:
        print(f"[sb_post ERROR] {table} → HTTP {r.status_code}: {r.text}")
    r.raise_for_status(); return r.json()

def sb_patch(table, qs, data):
    h = {**SB, "Prefer": "return=minimal"}
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{qs}", headers=h, json=data, timeout=10)
    if not r.ok:
        print(f"[sb_patch ERROR] {table}?{qs} → HTTP {r.status_code}: {r.text}")
    r.raise_for_status()

def sb_upsert_all(table, rows, on_conflict, chunk_size=500):
    """
    Bulk-Upsert statt N sequenzieller sb_patch()-Calls — EIN POST pro Chunk
    (Prefer: resolution=merge-duplicates → nur die mitgeschickten Spalten
    werden überschrieben, alle anderen bleiben unangetastet, exakt wie beim
    bisherigen sb_patch() pro Zeile).

    Grund: die alte for-Schleife mit einem sb_patch() pro Fahrer war bei
    ~1000 durch den Pagination-Cap "unmatched" Fahrern unbemerkt schnell
    genug — seit dem Pagination-Fix werden korrekt alle ~2219 Fahrer
    geschrieben, was die 43s auf mehrere hundert Sekunden (Cron: 14min statt
    ~90s) hochgetrieben hat. Das ist der dazugehörige Performance-Fix
    (im Briefing als "Fix direction: batch/bulk-upsert" vorgemerkt).

    rows: Liste von dicts, JEDES muss die on_conflict-Spalte enthalten
          (hier: "name"), sonst kann Supabase nicht matchen.
    """
    if not rows:
        return
    h = {**SB, "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}",
            headers=h, json=chunk, timeout=30,
        )
        if not r.ok:
            print(f"[sb_upsert_all ERROR] {table} chunk {i}-{i+len(chunk)} → "
                  f"HTTP {r.status_code}: {r.text}")
        r.raise_for_status()

def sb_delete(table, qs):
    h = {**SB, "Prefer": "return=minimal"}
    r = requests.delete(f"{SUPABASE_URL}/rest/v1/{table}?{qs}", headers=h, timeout=10)
    r.raise_for_status()

# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.route("/health")
@auth
def health(): return jsonify({"status": "ok"})

# ══════════════════════════════════════════════════════════════════════════════
#  CR CALCULATION — exakt portiert aus points_auto_fixed_28.py
# ══════════════════════════════════════════════════════════════════════════════

def _parse_time_ms(time_str: str):
    """Parst RaceNet Zeitstring → Millisekunden. Format: '00:04:28.7960000'"""
    if not time_str: return None
    try:
        t = time_str.strip()
        parts = t.split(":")
        if len(parts) == 3:
            secs = int(parts[0])*3600 + int(parts[1])*60 + float(parts[2])
        elif len(parts) == 2:
            secs = int(parts[0])*60 + float(parts[1])
        else:
            secs = float(t)
        return int(secs * 1000)
    except Exception:
        return None

def _is_dnf_ms(ms, all_ms=None):
    """
    DNF-Erkennung — exakt wie is_dnf_time() in points_auto_fixed_28.py.
    Racenet DNF-Zeiten: exakt ganzzahlig in Sekunden, Vielfaches von 60, mind. 4 Minuten,
    und deutlich über dem Feld (>30% über Median wenn andere Zeiten bekannt).
    """
    if ms is None: return False
    secs = ms / 1000
    # Muss exakt ganzzahlig sein (keine Millisekunden)
    if abs(secs - round(secs)) > 0.01: return False
    secs_int = round(secs)
    # Muss Vielfaches von 60 (ganze Minuten)
    if secs_int % 60 != 0: return False
    # Mindestens 4 Minuten
    if secs_int < 240: return False
    # Wenn andere Zeiten bekannt: >30% über Median
    if all_ms:
        valid = [t for t in all_ms if t and abs(t/1000 - round(t/1000)) > 0.01]
        if valid:
            med = statistics.median(valid)
            if ms < med * 1.3: return False
    return True

def _compute_cr(vehicle_times: dict, all_times_ms: list, top_pct: float, exponent: float, min_n: int):
    """
    CR-Berechnung — portiert aus points_auto_fixed_28.py Normalisierungs-Logik.

    vehicle_times: {vehicle_name: [time_ms, ...]}
    all_times_ms:  alle validen Zeiten (für Top-% Referenz)
    top_pct:       Top-Prozent als Referenzzeit (z.B. 25 = Top 25%)
    exponent:      CR-Kurven-Exponent
    min_n:         Mindestanzahl Einträge pro Fahrzeug

    CR = (ref_time / car_avg_time) ^ exponent
    ref_time = Durchschnitt der Top-top_pct% aller validen Zeiten
    """
    if not all_times_ms: return []

    # Globale Referenzzeit: Top-% der schnellsten Zeiten über alle Fahrzeuge
    sorted_times = sorted(all_times_ms)
    cutoff       = max(1, int(len(sorted_times) * top_pct / 100))
    ref_time_ms  = statistics.mean(sorted_times[:cutoff])

    results = []
    for vehicle, times in vehicle_times.items():
        if len(times) < min_n: continue
        avg_ms = statistics.mean(times)
        cr     = round((ref_time_ms / avg_ms) ** exponent, 4)
        results.append({
            "vehicle":      vehicle,
            "n":            len(times),
            "car_avg_ms":   int(avg_ms),
            "ref_time_ms":  int(ref_time_ms),
            "cr":           cr,
        })

    results.sort(key=lambda x: x["cr"], reverse=True)
    return results


@app.route("/cr/values", methods=["GET"])
@auth
def cr_values():
    """Lädt Locations + VehicleClasses + Routes von RaceNet."""
    try:
        client = RacenetClient()
        data   = client._get("/api/wrc2023Stats/values")
        return jsonify({
            "locations":       data.get("locations", {}),
            "vehicle_classes": data.get("vehicleClasses", {}),
            "location_routes": data.get("locationRoute", {}),
            "routes":          data.get("routes", {}),
        })
    except Exception as e:
        import traceback
        tb  = traceback.format_exc()
        msg = str(e)
        if "401" in msg or "403" in msg or "token" in msg.lower() or "refresh" in msg.lower():
            detail = "RaceNet Token abgelaufen — RACENET_TOKEN_RESET=1 setzen"
        elif "timeout" in msg.lower() or "connection" in msg.lower():
            detail = "RaceNet nicht erreichbar (Timeout)"
        else:
            detail = msg
        return jsonify({"error": detail, "trace": tb}), 500


@app.route("/cr/calculate", methods=["POST"])
@auth
def cr_calculate():
    """
    Berechnet CR aus RaceNet Time Trial Leaderboards.
    Body: { route_ids, class_ids, top_pct, min_n, exponent, max_results }
    """
    body        = request.json or {}
    route_ids   = [int(x) for x in body.get("route_ids", [])]
    class_ids   = [int(x) for x in body.get("class_ids", [])]
    top_pct     = float(body.get("top_pct",  25))
    min_n       = int(body.get("min_n",      10))
    exponent    = float(body.get("exponent", 1.5))
    max_results = int(body.get("max_results", 200))

    if not route_ids or not class_ids:
        return jsonify({"error": "route_ids and class_ids required"}), 400

    client = RacenetClient()
    combos = [(r, c) for r in route_ids for c in class_ids]

    try:
        leaderboards = client.get_public_leaderboards_parallel(
            combos, max_results=max_results, max_workers=8
        )
    except Exception as e:
        return jsonify({"error": f"RaceNet error: {e}"}), 500

    # Alle Einträge sammeln
    vehicle_times: dict[str, list] = {}
    all_valid_ms  = []
    total_entries = 0
    stages_loaded = 0

    for (route_id, class_id), entries in leaderboards.items():
        if not entries: continue
        stages_loaded += 1

        # Alle Zeiten dieser Stage für DNF-Kontext
        stage_ms = [_parse_time_ms(e.get("time","")) for e in entries]
        stage_ms = [t for t in stage_ms if t]

        for e in entries:
            ms = _parse_time_ms(e.get("time",""))
            if not ms: continue
            if _is_dnf_ms(ms, stage_ms): continue  # DNF raus
            vehicle = e.get("vehicle", "Unknown")
            vehicle_times.setdefault(vehicle, []).append(ms)
            all_valid_ms.append(ms)
            total_entries += 1

    if not all_valid_ms:
        return jsonify({"error": "No valid times found"}), 404

    results = _compute_cr(vehicle_times, all_valid_ms, top_pct, exponent, min_n)

    return jsonify({
        "results": results,
        "stats": {
            "total_entries": total_entries,
            "stages_loaded": stages_loaded,
            "ref_time_ms":   int(statistics.mean(sorted(all_valid_ms)[:max(1, int(len(all_valid_ms)*top_pct/100))])),
            "vehicles_found": len(vehicle_times),
        }
    })


@app.route("/cr/save", methods=["POST"])
@auth
def cr_save():
    """
    Speichert ein berechnetes CR-Set wiederverwendbar, UNABHÄNGIG von einer
    Championship. Es gibt keine eigene cr_sets-Tabelle (existiert nicht in
    Supabase) — Sets sind schlicht car_ratings-Zeilen mit championship_id
    = NULL und einem gemeinsamen set_name. /cr/assign kopiert sie später auf
    eine konkrete Championship (siehe unten).
    Body: { name, results: [{vehicle, cr}, ...] }
    (Rezept-Parameter wie top_pct/exponent werden NICHT mitgespeichert —
    Owner-Entscheidung: bei Bedarf im set-Namen selbst vermerken.)
    """
    body    = request.json or {}
    name    = body.get("name","").strip()
    results = body.get("results", [])
    if not name or not results:
        return jsonify({"error": "name and results required"}), 400

    try:
        sb_url = f"{SUPABASE_URL}/rest/v1/car_ratings"
        # Falls unter diesem Namen schon ein Set existiert: ersetzen statt duplizieren
        r_del = requests.delete(
            sb_url, headers=SB,
            params={"championship_id": "is.null", "set_name": f"eq.{name}"},
        )
        if not r_del.ok:
            print(f"[cr_save DELETE ERROR] {r_del.status_code}: {r_del.text}")
            r_del.raise_for_status()

        rows = [{
            "championship_id": None,
            "set_name":  name,
            "vehicle":   r["vehicle"],
            "cr_value":  r["cr"],
        } for r in results if r.get("vehicle") and r.get("cr") is not None]

        if rows:
            r_post = requests.post(sb_url, headers=SB, json=rows)
            if not r_post.ok:
                print(f"[cr_save POST ERROR] {r_post.status_code}: {r_post.text}")
                r_post.raise_for_status()

        return jsonify({"name": name, "saved": len(rows)})
    except Exception as e:
        return jsonify({"error": _err_detail(e)}), 500


@app.route("/cr/sets", methods=["GET"])
@auth
def cr_list_sets():
    """
    Alle gespeicherten CR-Sets auflisten (gruppiert nach set_name, nur
    Zeilen mit championship_id IS NULL — das sind per Definition die Sets).
    """
    try:
        rows = sb_get_all("car_ratings", "championship_id=is.null&select=set_name,vehicle,cr_value")
        sets: dict = {}
        for r in rows:
            n = r.get("set_name")
            if not n:
                continue
            sets.setdefault(n, []).append({"vehicle": r["vehicle"], "cr_value": r["cr_value"]})
        result = [{"name": n, "vehicle_count": len(v)} for n, v in sets.items()]
        result.sort(key=lambda x: x["name"])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/sets/values", methods=["GET"])
@auth
def cr_set_values():
    """Die einzelnen Fahrzeug/CR-Werte eines gespeicherten Sets. Query: ?name=..."""
    name = request.args.get("name","").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        rows = sb_get("car_ratings", f"championship_id=is.null&set_name=eq.{requests.utils.quote(name)}&select=vehicle,cr_value")
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/sets/delete", methods=["POST"])
@auth
def cr_delete_set():
    """
    Löscht ein CR-Set komplett. POST statt DELETE-mit-Pfad-Parameter, weil
    set_name Freitext ist (Leerzeichen/Sonderzeichen) — keine gültige
    valid_id()-ID wie sonst überall in diesem File.
    Body: { name }
    """
    body = request.json or {}
    name = body.get("name","").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        sb_delete("car_ratings", f"championship_id=is.null&set_name=eq.{requests.utils.quote(name)}")
        return jsonify({"deleted": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/assign", methods=["POST"])
@auth
def cr_assign():
    """
    Kopiert die Werte eines gespeicherten CR-Sets auf eine konkrete
    Championship — schreibt frische car_ratings-Zeilen mit der Ziel-
    championship_id (macht das Set fuer die echte Punkteberechnung wirksam,
    die in grf_sync.py ausschließlich über championship_id liest).
    Ersetzt vorhandene car_ratings dieser Championship komplett (gleiches
    Verhalten wie /cr/manual-save).
    Body: { championship_id, set_name }
    """
    body     = request.json or {}
    champ    = body.get("championship_id")
    set_name = body.get("set_name","").strip()
    if not champ or not set_name:
        return jsonify({"error": "championship_id and set_name required"}), 400
    if not valid_id(champ):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        sb_url = f"{SUPABASE_URL}/rest/v1/car_ratings"

        set_rows = sb_get("car_ratings", f"championship_id=is.null&set_name=eq.{requests.utils.quote(set_name)}&select=vehicle,cr_value")
        if not set_rows:
            return jsonify({"error": f"CR-Set '{set_name}' nicht gefunden oder leer"}), 404

        r_del = requests.delete(sb_url, headers=SB, params={"championship_id": f"eq.{champ}"})
        if not r_del.ok:
            print(f"[cr_assign DELETE ERROR] {r_del.status_code}: {r_del.text}")
            r_del.raise_for_status()

        new_rows = [{"championship_id": champ, "vehicle": r["vehicle"], "cr_value": r["cr_value"]} for r in set_rows]
        r_post = requests.post(sb_url, headers=SB, json=new_rows)
        if not r_post.ok:
            print(f"[cr_assign POST ERROR] {r_post.status_code}: {r_post.text}")
            r_post.raise_for_status()

        return jsonify({"ok": True, "assigned": len(new_rows)})
    except Exception as e:
        return jsonify({"error": _err_detail(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  CHAMPIONSHIP — 3 MODI
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/championship/create", methods=["POST"])
@auth
def championship_create():
    """
    Modus 1: Create without RaceNet
    Modus 2: Create with RaceNet (racenet_id + events werden importiert)
    Body: { championship: {...}, rules: [...], mode: "manual"|"racenet" }
    """
    body  = request.json or {}
    champ = body.get("championship", {})
    rules = body.get("rules", [])
    mode  = body.get("mode", "manual")

    if not champ.get("name"):
        return jsonify({"error": "name required"}), 400

    try:
        # WICHTIG: Wenn Mode 2 (RaceNet-Import), muss die Championship-ID
        # exakt der RaceNet-ID entsprechen. grf_sync.py upserted laufende
        # Championships über genau diese ID (on_conflict="id") — wird hier
        # keine explizite id gesetzt, generiert Supabase eine eigene und
        # der Cronjob legt für dieselbe Championship eine ZWEITE, separate
        # Zeile an. Alle Live-Events/Ergebnisse landen dann dort, nicht
        # bei der hier im Admin erstellten Zeile (CR/Bonuses/Narrative
        # wären dadurch "wirkungslos" für die laufende Saison).
        if mode == "racenet" and champ.get("racenet_id"):
            champ["id"] = champ["racenet_id"]

        # Nur bekannte Spalten — racenet_id o.ä. würden Supabase 400 geben
        CHAMP_FIELDS = {"id","club_id","name","narrative","vehicle_class","season_number","start_date","end_date"}
        champ = {k: v for k, v in champ.items() if k in CHAMP_FIELDS}

        # Upsert statt Insert — vermeidet 409 wenn dieselbe RaceNet-ID nochmal importiert wird
        h_upsert = {**SB, "Prefer": "resolution=merge-duplicates,return=representation"}
        r_c = requests.post(
            f"{SUPABASE_URL}/rest/v1/championships?on_conflict=id",
            headers=h_upsert, json=champ, timeout=10
        )
        if not r_c.ok:
            return jsonify({"error": f"Championships upsert failed: {r_c.text}"}), 500
        created  = r_c.json()
        champ_id = created[0]["id"] if isinstance(created, list) else created["id"]

        # Rules speichern
        if rules:
            for r in rules: r["championship_id"] = champ_id
            sb_post("championship_rules", rules)

        # Modus 2: Events von RaceNet importieren
        imported_events = []
        if mode == "racenet" and champ.get("racenet_id"):
            try:
                client = RacenetClient()
                imported_events = _import_racenet_events(
                    client, champ_id, champ["racenet_id"], champ.get("club_id","")
                )
            except Exception as e:
                # Events-Import Fehler ist nicht fatal
                return jsonify({
                    "id": champ_id, "name": champ.get("name"),
                    "warning": f"Championship created but RaceNet event import failed: {e}",
                    "events_imported": 0,
                })

        return jsonify({
            "id":             champ_id,
            "name":           champ.get("name"),
            "events_imported": len(imported_events),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _import_racenet_events(client, champ_id: str, racenet_champ_id: str, club_id: str) -> list:
    """
    Importiert Events einer RaceNet Championship.
    Benennt Events als 'Rd.N — Location'.
    """
    # Club-Championships laden um Events zu finden
    club_data = client.get_championship(club_id, racenet_champ_id)
    events_raw = club_data.get("events", []) or club_data.get("legs", [])

    imported = []
    for i, ev in enumerate(events_raw, 1):
        location = ev.get("location", {})
        loc_name = location.get("name", "") if isinstance(location, dict) else str(location)
        if not loc_name:
            loc_name = ev.get("locationName", ev.get("name", f"Event {i}"))

        name = f"Rd.{i} — {loc_name}"

        start_date = ev.get("startAt") or ev.get("startDate") or None
        end_date   = ev.get("closeAt") or ev.get("endDate")   or None
        row = {
            "championship_id":  champ_id,
            "racenet_event_id": str(ev.get("id", ev.get("eventId", ""))),
            "name":             name,
            "location":         loc_name,
            "round_number":     i,
            "start_date":       start_date[:10] if start_date else None,
            "end_date":         end_date[:10]   if end_date   else None,
        }
        sb_post("events", row)
        imported.append(row)

    return imported


@app.route("/championship/racenet-list", methods=["POST"])
@auth
def championship_racenet_list():
    """
    Lädt verfügbare Championships von RaceNet für einen Club.
    Body: { club_id: "23799" }
    """
    body    = request.json or {}
    club_id = body.get("club_id","").strip()
    if not club_id:
        return jsonify({"error": "club_id required"}), 400
    try:
        client = RacenetClient()
        ids    = client.get_all_championship_ids(club_id)
        result = []
        for cid in ids:
            try:
                c = client.get_championship(club_id, cid)
                # TEMPORÄR (Session 3 Debugging) — rohe RaceNet-Antwort einmal
                # loggen, um die echten Feldnamen für Datum/Fahrzeugklasse zu
                # sehen (start_at/close_at kamen bisher leer zurück). Nach
                # Bestätigung der richtigen Keys wieder entfernen.
                import json as _json
                print(f"[DEBUG championship {cid}] {_json.dumps(c)[:2000]}")
                result.append({
                    "racenet_id": str(c.get("id", cid)),
                    "name":       c.get("name", ""),
                    "start_at":   c.get("startAt") or c.get("startDate", ""),
                    "close_at":   c.get("closeAt") or c.get("endDate", ""),
                })
            except Exception:
                result.append({"racenet_id": str(cid), "name": f"Championship {cid}", "start_at": "", "close_at": ""})
        # Sort by start_at descending (newest first)
        result.sort(key=lambda x: x.get("start_at") or "", reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/championship/alter", methods=["POST"])
@auth
def championship_alter():
    """
    Modus 3: Alter running championship.
    Body: { championship_id, fields: {...} }  — patcht nur gegebene Felder
    """
    body    = request.json or {}
    champ_id = body.get("championship_id")
    fields   = body.get("fields", {})
    if not champ_id or not fields:
        return jsonify({"error": "championship_id and fields required"}), 400
    if not valid_id(champ_id):
        return jsonify({"error": "invalid championship_id"}), 400
    ALLOWED = {"name","vehicle_class","season_number","start_date","end_date","narrative"}
    fields = {k: v for k, v in fields.items() if k in ALLOWED and v is not None}
    if not fields:
        return jsonify({"error": "no valid fields to update"}), 400
    try:
        sb_patch("championships", f"id=eq.{champ_id}", fields)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/rules/save", methods=["POST"])
@auth
def rules_save():
    """
    Speichert die globalen Championship-Regeln (championship_rules) für
    eine BEREITS EXISTIERENDE Championship. War bisher nur beim initialen
    /championship/create moeglich, nicht nachtraeglich fuer eine schon
    angelegte (z.B. "upcoming") Championship editierbar.
    Loescht bestehende Regeln zuerst, dann neu anlegen (gleiches Muster wie
    /teams/save und /cr/manual-save).
    Body: { championship_id, rules: [{rule_type, is_active, points, description}, ...] }
    """
    body     = request.json or {}
    champ_id = body.get("championship_id")
    rules    = body.get("rules", [])
    if not champ_id:
        return jsonify({"error": "championship_id required"}), 400
    if not valid_id(champ_id):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        sb_delete("championship_rules", f"championship_id=eq.{champ_id}")
        if rules:
            for r in rules:
                r["championship_id"] = champ_id
            sb_post("championship_rules", rules)
        return jsonify({"ok": True, "saved": len(rules)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/rules/<champ_id>", methods=["GET"])
@auth
def rules_get(champ_id):
    """
    Bestehende Regeln einer Championship laden — ueber den service_role-Key
    (nicht den anon-Key), da championship_rules aktuell keine SELECT-Policy
    fuer public hat (gleiches Muster wie /teams/<champ_id>).
    """
    if not valid_id(champ_id):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        rules = sb_get("championship_rules", f"championship_id=eq.{champ_id}&select=rule_type,is_active,points,description")
        return jsonify(rules)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Teams ─────────────────────────────────────────────────────────────────────

@app.route("/teams/save", methods=["POST"])
@auth
def teams_save():
    """
    Speichert Teams + Fahrerzuordnungen für eine Championship.
    Löscht bestehende Teams zuerst, dann neu anlegen.
    Body: { championship_id, teams: [{name, color, members: [driver_name,...]}, ...] }
    """
    body    = request.json or {}
    champ   = body.get("championship_id")
    teams   = body.get("teams", [])
    if not champ:
        return jsonify({"error": "championship_id required"}), 400
    if not valid_id(champ):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        # Bestehende Teams löschen (cascade löscht team_members)
        sb_delete("teams", f"championship_id=eq.{champ}")

        created_teams = []
        for t in teams:
            team_row = sb_post("teams", {
                "championship_id": champ,
                "name":  t.get("name","").strip(),
                "color": t.get("color",""),
            })
            team_id = team_row[0]["id"] if isinstance(team_row, list) else team_row["id"]

            members = [m.strip() for m in t.get("members",[]) if m.strip()]
            if members:
                # HINWEIS: team_members hat KEINE championship_id-Spalte
                # (bestätigt per Railway-Log: PGRST204) — braucht sie auch
                # nicht, haengt schon ueber team_id an einer Championship.
                sb_post("team_members", [{
                    "team_id":     team_id,
                    "driver_name": m,
                } for m in members])

            created_teams.append({"id": team_id, "name": t.get("name"), "members": members})

        return jsonify({"teams": created_teams})
    except Exception as e:
        return jsonify({"error": _err_detail(e)}), 500


@app.route("/teams/<champ_id>", methods=["GET"])
@auth
def teams_get(champ_id):
    """Teams + Mitglieder einer Championship laden."""
    if not valid_id(champ_id):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        teams = sb_get("teams", f"championship_id=eq.{champ_id}&select=id,name,color,team_members(driver_name)")
        return jsonify(teams)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Drivers ───────────────────────────────────────────────────────────────────

@app.route("/drivers/search", methods=["GET"])
@auth
def drivers_search():
    """
    Teilstring-Suche gegen die kanonische `drivers`-Tabelle (dieselbe
    Namensbasis, die auch ELO/Standings/event_results verwenden). Genutzt
    fuer die Team-Mitglieder-Zuordnung im Championship Setup, wo man
    Fahrernamen sonst nicht auswaehlen koennte bevor sie in der Championship
    Ergebnisse haben.
    Query: ?q=<teilname>  (mind. 2 Zeichen, sonst leeres Ergebnis)
    Gibt mehrere Treffer zurueck falls der Teilname mehrdeutig ist — die
    Auswahl der richtigen Person passiert im Admin selbst, kein Auto-Match.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        # PostgREST ilike-Wildcard: %text% -> muss selbst URL-encoded werden,
        # requests uebernimmt das ueber params.
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/drivers",
            headers=SB,
            params={
                "name": f"ilike.*{q}*",
                "select": "name",
                "order": "name.asc",
                "limit": "20",
            },
            timeout=10,
        )
        r.raise_for_status()
        return jsonify([row["name"] for row in r.json()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Manual Bonuses ────────────────────────────────────────────────────────────

@app.route("/bonus/add", methods=["POST"])
@auth
def bonus_add():
    body = request.json or {}
    for f in ["championship_id","event_id","driver_name","bonus_type","points"]:
        if not body.get(f): return jsonify({"error": f"{f} required"}), 400
    if not valid_id(body["championship_id"]) or not valid_id(body["event_id"]):
        return jsonify({"error": "invalid championship_id or event_id"}), 400
    try:
        sb_post("manual_bonuses", {
            "championship_id": body["championship_id"],
            "event_id":        body["event_id"],
            "driver_name":     body["driver_name"],
            "bonus_type":      body["bonus_type"],
            "bonus_name":      body.get("bonus_name",""),
            "points":          int(body["points"]),
            "note":            body.get("note",""),
        })
        # event_results bonus_points aktualisieren
        driver = body["driver_name"]
        ev_id  = body["event_id"]
        pts    = int(body["points"])
        rows   = sb_get("event_results",
                        f"event_id=eq.{ev_id}&driver_name=eq.{requests.utils.quote(driver)}&select=id,bonus_points,cr_points")
        if rows:
            r = rows[0]
            new_bonus = (r.get("bonus_points") or 0) + pts
            new_total = (r.get("cr_points") or 0) + new_bonus
            sb_patch("event_results", f"id=eq.{r['id']}",
                     {"bonus_points": new_bonus, "total_points": new_total})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/bonus/delete/<bonus_id>", methods=["DELETE"])
@auth
def bonus_delete(bonus_id):
    if not valid_id(bonus_id):
        return jsonify({"error": "invalid bonus_id"}), 400
    try:
        sb_delete("manual_bonuses", f"id=eq.{bonus_id}")
        return jsonify({"deleted": bonus_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Narratives ────────────────────────────────────────────────────────────────

@app.route("/narrative/championship", methods=["POST"])
@auth
def narrative_championship():
    body = request.json or {}
    champ_id = body.get("championship_id")
    if not champ_id: return jsonify({"error": "championship_id required"}), 400
    if not valid_id(champ_id): return jsonify({"error": "invalid championship_id"}), 400
    try:
        sb_patch("championships", f"id=eq.{champ_id}", {"narrative": body.get("narrative","")})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/narrative/event", methods=["POST"])
@auth
def narrative_event():
    body = request.json or {}
    ev_id = body.get("event_id")
    if not ev_id: return jsonify({"error": "event_id required"}), 400
    if not valid_id(ev_id): return jsonify({"error": "invalid event_id"}), 400
    try:
        sb_patch("events", f"id=eq.{ev_id}", {"narrative": body.get("narrative","")})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/event/alter", methods=["POST"])
@auth
def event_alter():
    """
    Erlaubt das nachtraegliche Umbenennen eines Events (z.B. wenn der
    auto-generierte "Rd.N — Location"-Name nicht passt). Gab es bisher
    nirgends, nur /narrative/event existierte fuer Events.
    Body: { event_id, name }
    """
    body  = request.json or {}
    ev_id = body.get("event_id")
    name  = (body.get("name") or "").strip()
    if not ev_id or not name:
        return jsonify({"error": "event_id and name required"}), 400
    if not valid_id(ev_id):
        return jsonify({"error": "invalid event_id"}), 400
    try:
        sb_patch("events", f"id=eq.{ev_id}", {"name": name})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── CR MANUAL SAVE ───────────────────────────────────────────────────────────

@app.route("/cr/manual-save", methods=["POST"])
@auth
def cr_manual_save():
    """
    Speichert manuell eingegebene CR-Werte für eine Championship direkt in car_ratings.
    Body: { championship_id, ratings: [{vehicle, cr_value}, ...] }
    """
    body    = request.json or {}
    champ   = body.get("championship_id","").strip()
    ratings = body.get("ratings", [])
    if not champ or not ratings:
        return jsonify({"error": "championship_id and ratings required"}), 400
    if not valid_id(champ):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        # Alte manuelle CR-Werte fuer diese Championship löschen.
        # HINWEIS: car_ratings hat KEINE cr_set_id-Spalte (bestätigt per
        # Railway-Log: "column car_ratings.cr_set_id does not exist") — das
        # ganze cr_set_id-Konzept fehlt im echten Schema. Fuer die manuelle
        # Direkteingabe pro Championship wird es auch nicht gebraucht, daher
        # hier komplett entfernt statt zu versuchen es nachzubilden.
        sb_url = f"{SUPABASE_URL}/rest/v1/car_ratings"
        r_del = requests.delete(
            sb_url,
            headers=SB,
            params={"championship_id": f"eq.{champ}"},
        )
        if not r_del.ok:
            print(f"[cr_manual_save DELETE ERROR] {r_del.status_code}: {r_del.text}")
            r_del.raise_for_status()
        # Neue einfügen
        rows = [
            {"championship_id": champ, "vehicle": r["vehicle"],
             "cr_value": float(r["cr_value"])}
            for r in ratings if r.get("vehicle") and r.get("cr_value") is not None
        ]
        if rows:
            r_post = requests.post(sb_url, headers=SB, json=rows)
            if not r_post.ok:
                print(f"[cr_manual_save POST ERROR] {r_post.status_code}: {r_post.text}")
                r_post.raise_for_status()
        # (Frühere sb_patch("championships", ..., {"cr_set_id": None}) hier
        # entfernt — championships hat ebenfalls keine cr_set_id-Spalte, war
        # ohnehin wirkungslos/unnötig für die manuelle Eingabe.)
        # /cr/assign hat ein separates, tieferes Problem (siehe Chat) — dort
        # absichtlich nicht mitgefixt, da unklar ob das CR-Set-Feature aktiv
        # genutzt wird und wie es eigentlich funktionieren soll.
        return jsonify({"ok": True, "saved": len(rows)})
    except Exception as e:
        return jsonify({"error": _err_detail(e)}), 500


@app.route("/cr/vehicles", methods=["GET"])
@auth
def cr_vehicles():
    """
    Gibt alle Fahrzeuge (distinct) für eine Championship zurück.
    Query: ?championship_id=xxx
    """
    champ = request.args.get("championship_id","").strip()
    if not champ:
        return jsonify({"error": "championship_id required"}), 400
    if not valid_id(champ):
        return jsonify({"error": "invalid championship_id"}), 400
    try:
        # Aus stage_results alle vehicles sammeln
        rows = sb_get("stage_results", f"championship_id=eq.{champ}&select=vehicle")
        vehicles = sorted({r["vehicle"] for r in rows if r.get("vehicle")})
        # Aktuelle CR-Werte laden
        cr_rows = sb_get("car_ratings", f"championship_id=eq.{champ}&select=vehicle,cr_value")
        cr_map  = {r["vehicle"]: r["cr_value"] for r in cr_rows}
        return jsonify([{"vehicle": v, "cr_value": cr_map.get(v)} for v in vehicles])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Vehicle Classes (statische Referenzliste, siehe vehicle_classes_data.py) ──

@app.route("/vehicles/classes", methods=["GET"])
@auth
def vehicles_classes():
    """
    Statische Klasse->Fahrzeug-Liste (vehicle_classes_data.py, aus
    vehicles_by_class.csv). Genutzt im Championship Setup, damit CR-Werte
    ueber ein Dropdown ausgewaehlt statt frei getippt werden (verhindert
    Namens-Mismatches gegenueber echten RaceNet/stage_results-Werten).

    Ohne Query-Param: gibt alle Klassennamen zurueck (fuer das Klassen-Dropdown).
    Mit ?class=<Name>: gibt nur die Fahrzeuge dieser einen Klasse zurueck.

    HINWEIS: einige Fahrzeuge tauchen absichtlich in mehreren Klassen auf
    (z.B. Rally2 + WRC2/WRC3/WRC4, siehe vehicle_classes_data.py-Docstring) —
    kein Bug, noch nicht dedupliziert, betrifft nur aktuell ungenutzte
    moderne Klassen.
    """
    cls = request.args.get("class", "").strip()
    if cls:
        if cls not in VEHICLE_CLASSES:
            return jsonify({"error": f"unknown class '{cls}'"}), 404
        return jsonify(VEHICLE_CLASSES[cls])
    return jsonify(sorted(VEHICLE_CLASSES.keys()))


# ── ELO CLUBS ────────────────────────────────────────────────────────────────

@app.route("/elo/clubs", methods=["GET"])
@auth
def elo_clubs():
    """
    Lädt alle Clubs des verknüpften RaceNet-Dummy-Accounts.
    Gibt [{id, name}] zurück — wird im Admin-Tab für die ELO-Club-Auswahl genutzt.
    Fallback: distinct club_ids aus der championships-Tabelle in Supabase.
    """
    racenet_error = None
    result = []

    # Versuch 1: RaceNet live
    try:
        client = RacenetClient()
        clubs  = client.get_active_clubs()
        for c in clubs:
            club_id   = str(c.get("clubID") or c.get("id") or "")
            club_name = (c.get("clubSettings") or c.get("settings") or {}).get("name") \
                        or c.get("clubName") or c.get("name") or f"Club {club_id}"
            if club_id:
                result.append({"id": club_id, "name": club_name})
    except Exception as e:
        racenet_error = str(e)

    # Fallback: club_ids aus Supabase championships
    if not result:
        try:
            rows = sb_get("championships", "select=club_id,name&order=start_date.desc")
            seen = {}
            for r in rows:
                cid = str(r.get("club_id") or "")
                if cid and cid not in seen:
                    seen[cid] = r.get("name", f"Club {cid}")
            # Nutze club_id als ID, championship-Name als Hinweis
            for cid, cname in seen.items():
                result.append({"id": cid, "name": f"Club {cid}", "note": cname})
        except Exception as e2:
            if not result:
                err = racenet_error or str(e2)
                return jsonify({"error": f"RaceNet: {err}"}), 500

    return jsonify(result)


# ── ELO UPDATE ───────────────────────────────────────────────────────────────

@app.route("/elo/update", methods=["POST"])
@auth
def elo_update():
    """
    Führt echten ELO-Update durch.
    Body: { club_ids: ["23799", "23834"], force_reset: false }

    Liest event_results aus Supabase (chronologisch nach event start_date),
    baut RawEvent-Objekte und verarbeitet sie mit elo_pipeline.
    Speichert Ergebnisse in elo_ratings Tabelle.
    """
    import json, sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    body       = request.json or {}
    club_ids   = body.get("club_ids", [])
    force      = body.get("force_reset", False)

    if not club_ids:
        return jsonify({"error": "club_ids required"}), 400

    try:
        from elo_engine   import Rating
        from elo_pipeline import RawEvent, process_racenet_events, summarize_track
        from elo_state    import EloState
        from elo_categories import build_lookups

        log_lines = []
        def log(msg):
            log_lines.append(msg)

        log(f"Starting ELO update for clubs: {', '.join(club_ids)}")

        # Surface (Location) + Vehicle (Drivetrain/Era) Lookups — statische
        # Dateien, EA WRC bekommt keine Updates mehr, Autos/Locations ändern
        # sich nie wieder. Liegen neben elo_categories.py.
        lookup_dir     = os.path.dirname(os.path.abspath(__file__))
        surface_path   = os.path.join(lookup_dir, "category_lookups_surface.json")
        vehicle_path   = os.path.join(lookup_dir, "category_lookups_vehicles.json")
        lookups = build_lookups(surface_path, vehicle_path)
        log(f"Lookups geladen: {len(lookups.surface_by_location)} Locations, "
            f"{len(lookups.vehicle_meta)} Fahrzeuge")

        # State aus Supabase laden
        state_rows = sb_get("elo_state", "select=*&limit=1")
        if state_rows and not force:
            state = EloState.from_dict(json.loads(state_rows[0]["state_json"]))
            log(f"Loaded existing ELO state ({len(state.processed_event_ids)} events already processed)")
        else:
            state = EloState(profile_name="grf")
            log("Starting fresh ELO state")

        # ── Championships chronologisch laden ─────────────────────────────
        champ_rows = sb_get(
            "championships",
            "select=id,club_id,name,start_date,end_date&order=start_date.asc"
        )
        champ_rows = [c for c in champ_rows if str(c.get("club_id","")) in club_ids]
        log(f"Found {len(champ_rows)} championships across clubs")

        # ── Events laden: pro Championship chronologisch nach round_number ─
        # Wir speichern (champ_start_date, round_number, RawEvent) für globale Sortierung
        raw_events_with_date = []
        # Auch end_date der letzten Stage pro Fahrer für Decay-Berechnung
        driver_last_event_date: dict = {}  # {driver_name: "YYYY-MM-DD"}

        # ── Events + Ergebnisse in Batches bulk-laden statt N+1 Einzel-Reads ──
        # Vorher: 1 sb_get() pro Championship (Events) + 1 sb_get() pro
        # abgeschlossenem Event (Results) — bei 822 Events across alle Clubs
        # bis zu ~1064 sequenzielle HTTP-Roundtrips, dominanter Grund für die
        # 120s-Timeouts trotz des Bulk-Upsert-Fixes auf der Schreibseite.
        # Jetzt: gebündelt über PostgREST's `in.()`-Filter in Batches von 100
        # IDs (URL bleibt bei den kurzen ID-Strings dieses Schemas komfortabel
        # unter jedem Längenlimit), sb_get_all() paginiert innerhalb jedes
        # Batches automatisch falls >1000 Zeilen zurückkommen.
        BATCH = 100

        champ_ids_list = [c["id"] for c in champ_rows]
        all_events = []
        for i in range(0, len(champ_ids_list), BATCH):
            id_list = ",".join(str(x) for x in champ_ids_list[i:i + BATCH])
            all_events.extend(sb_get_all(
                "events",
                f"championship_id=in.({id_list})&select=id,championship_id,name,"
                f"location,status,start_date,end_date,round_number&"
                f"order=championship_id.asc,round_number.asc"
            ))
        log(f"Found {len(all_events)} events across {len(champ_rows)} championships")

        events_by_champ: dict = {}
        for ev in all_events:
            events_by_champ.setdefault(ev["championship_id"], []).append(ev)

        completed_event_ids = [
            ev["id"] for evs in events_by_champ.values() for ev in evs
            if ev.get("status", 0) == 2
        ]

        results_by_event: dict = {}
        for i in range(0, len(completed_event_ids), BATCH):
            id_list = ",".join(str(x) for x in completed_event_ids[i:i + BATCH])
            rows = sb_get_all(
                "event_results",
                f"event_id=in.({id_list})&select=event_id,driver_name,position,"
                f"vehicle,is_dnf&order=event_id.asc,position.asc"
            )
            for r in rows:
                results_by_event.setdefault(r["event_id"], []).append(r)
        log(f"Loaded event_results for {len(completed_event_ids)} completed "
            f"events in {-(-len(completed_event_ids) // BATCH) if completed_event_ids else 0} batch(es)")

        for champ in champ_rows:
            champ_id    = champ["id"]
            champ_start = champ.get("start_date") or ""

            ev_rows = events_by_champ.get(champ_id, [])
            for ev in ev_rows:
                ev_id  = ev["id"]
                if ev.get("status", 0) != 2:
                    continue

                results = results_by_event.get(ev_id, [])
                if not results:
                    continue

                finishers = []
                dnf_list  = []
                for r in results:
                    driver  = r.get("driver_name","")
                    rank    = r.get("position")
                    vehicle = r.get("vehicle","Unknown")
                    is_dnf  = r.get("is_dnf", False)
                    if not driver:
                        continue
                    # Letztes Event-Datum pro Fahrer tracken (für Decay)
                    ev_end = (ev.get("end_date") or ev.get("start_date") or
                              champ.get("end_date") or champ_start or "")
                    if ev_end:
                        if driver not in driver_last_event_date or ev_end > driver_last_event_date[driver]:
                            driver_last_event_date[driver] = ev_end
                    if is_dnf:
                        dnf_list.append((driver, vehicle, driver))
                    elif rank:
                        finishers.append((driver, rank, vehicle, driver))

                if not finishers and not dnf_list:
                    continue

                event_id = f"{champ['club_id']}:{champ_id}:{ev_id}"
                raw_event = RawEvent(
                    event_id=event_id,
                    location=ev.get("location") or ev.get("name",""),
                    finishers=finishers,
                    dnf_drivers=dnf_list,
                )
                raw_events_with_date.append((champ_start, ev.get("round_number", 0), raw_event))

        # Global chronologisch sortieren: erst nach Championship-Startdatum, dann Round
        raw_events_with_date.sort(key=lambda x: (x[0] or "", x[1]))
        raw_events = [x[2] for x in raw_events_with_date]
        log(f"Loaded {len(raw_events)} completed events to process (chronological order)")

        # ── ELO berechnen ─────────────────────────────────────────────────
        if force:
            state = EloState(profile_name="grf")
            # Sequenziell + chronologisch (nicht Batch) für Force Reset
            logs = process_racenet_events(state, raw_events, lookups)
            log(f"Sequential fit complete: {len(logs)} events processed")
        else:
            logs = process_racenet_events(state, raw_events, lookups)
            log(f"Delta update: {len(logs)} new events processed")

        drivers_updated = len(state.ratings.get("overall", {}))
        log(f"Processed {drivers_updated} drivers")

        # ── Inaktivitäts-Decay anwenden ───────────────────────────────────
        # Decay: pro Woche Inaktivität bewegt sich mu um DECAY_PER_WEEK Richtung 1000
        # Fahrer gilt als inaktiv wenn letztes Event > INACTIVE_WEEKS Wochen her
        from datetime import date, timedelta
        DECAY_PER_WEEK  = 8.0   # mu-Punkte pro Woche Richtung Baseline
        INACTIVE_WEEKS  = 4     # ab wann inaktiv
        BASELINE_MU     = 1500.0
        today = date.today()

        overall_ratings = state.ratings.get("overall", {})
        # Nur Übergänge loggen (aktiv→inaktiv oder umgekehrt), nicht mehr alle
        # ~2219 Fahrer bei jedem Lauf. Der vorherige Fix (ein print() statt N)
        # hat das Problem NICHT gelöst — Railway zählt vermutlich Zeilen im
        # Ausgabestrom (newline-getrennt), nicht Python-print()-Aufrufe, daher
        # blieb die tatsächliche Zeilenzahl identisch. Diesmal wird die Zahl
        # der Zeilen selbst reduziert: im Normalfall ändern pro Lauf nur eine
        # Handvoll Fahrer ihren Status, nicht alle. Zähler für alle Kategorien
        # bleiben trotzdem vollständig sichtbar (no_date/active/inactive-Summe).
        transition_log = []
        no_date_count = 0
        for driver_name, rating in overall_ratings.items():
            last_date_str = driver_last_event_date.get(driver_name)
            if not last_date_str:
                no_date_count += 1
                continue
            try:
                last_date = date.fromisoformat(last_date_str[:10])
            except Exception:
                continue
            days_inactive   = (today - last_date).days
            weeks_inactive  = days_inactive / 7.0
            is_now_inactive = weeks_inactive >= INACTIVE_WEEKS
            was_inactive    = state.driver_inactive.get(driver_name, False)

            if is_now_inactive != was_inactive:
                transition_log.append(
                    f"  {driver_name}: last={last_date_str[:10]} days={days_inactive} "
                    f"{'active → INACTIVE' if is_now_inactive else 'INACTIVE → active'}"
                )
            if is_now_inactive:
                decay = DECAY_PER_WEEK * weeks_inactive
                if rating.mu > BASELINE_MU:
                    rating.mu = max(BASELINE_MU, rating.mu - decay)
                elif rating.mu < BASELINE_MU:
                    rating.mu = min(BASELINE_MU, rating.mu + decay)
                rating.sigma = min(rating.sigma * 1.02 ** weeks_inactive, 350.0)
                state.driver_inactive[driver_name] = True
            else:
                state.driver_inactive[driver_name] = False

        print(f"\n[INACTIVITY LOG] {len(overall_ratings)} drivers checked, "
              f"{no_date_count} no date, {len(transition_log)} status change(s)")
        if transition_log:
            print("\n".join(sorted(transition_log)))

        # Summaries nach Decay neu berechnen (inkl. inaktive)
        summaries = summarize_track(state, "overall", include_inactive=True)
        if summaries:
            top = [s for s in summaries if not s.is_inactive]
            if top:
                log(f"Top rated (active): {top[0].display_name} ({top[0].conservative_rating:.0f})")
            log(f"Top rated (overall): {summaries[0].display_name} ({summaries[0].conservative_rating:.0f})")

        inactive_count = sum(1 for s in summaries if s.is_inactive)
        log(f"Inactive drivers (>{INACTIVE_WEEKS}w): {inactive_count}")

        # ── State in Supabase speichern ───────────────────────────────────
        state_json = json.dumps(state.to_dict())
        existing   = sb_get("elo_state", "select=id&limit=1")
        if existing:
            sb_patch("elo_state", f"id=eq.{existing[0]['id']}", {"state_json": state_json})
        else:
            requests.post(
                f"{SUPABASE_URL}/rest/v1/elo_state",
                headers=SB, json={"state_json": state_json}
            )

        # ── Ratings in drivers Tabelle schreiben (Bulk-Upsert statt N PATCHes) ──
        driver_names_in_db = {r["name"] for r in sb_get_all("drivers", "select=name")}
        matched   = 0
        unmatched = []
        upsert_rows = []
        for summary in summaries:
            elo_val = round(summary.conservative_rating, 1)
            if summary.display_name not in driver_names_in_db:
                unmatched.append(summary.display_name)
                continue
            upsert_rows.append({
                "name":            summary.display_name,
                "elo":             elo_val,
                "elo_mu":          round(summary.mu, 2),
                "elo_sigma":       round(summary.sigma, 2),
                "elo_events":      summary.events_played,
                "elo_provisional": summary.is_provisional,
                "elo_inactive":    summary.is_inactive,
            })
            matched += 1
        sb_upsert_all("drivers", upsert_rows, on_conflict="name")
        log(f"ELO written: {matched} matched, {len(unmatched)} unmatched")
        if unmatched:
            log(f"Unmatched (not in drivers table): {', '.join(unmatched[:10])}")

        # ── Tages-Snapshot für "Delta 7 Tage" schreiben ─────────────────────
        # EIN Eintrag pro Fahrer pro Tag (on_conflict auf driver_name+
        # snapshot_date) — bei mehreren Läufen am selben Tag überschreibt der
        # neueste den Tageswert, kein Duplikat, keine unnötig wachsende
        # Tabelle. Nutzt dieselben bereits berechneten Werte wie der
        # drivers-Upsert oben, keine zusätzliche Berechnung nötig.
        today_str = date.today().isoformat()
        history_rows = [
            {"driver_name": r["name"], "elo": r["elo"], "elo_mu": r["elo_mu"],
             "snapshot_date": today_str}
            for r in upsert_rows
        ]
        sb_upsert_all("elo_history", history_rows, on_conflict="driver_name,snapshot_date")
        log(f"ELO history snapshot written: {len(history_rows)} drivers, date={today_str}")

        log(f"✓ ELO update complete. {drivers_updated} drivers updated.")
        return jsonify({"ok": True, "log": log_lines, "drivers": drivers_updated})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
