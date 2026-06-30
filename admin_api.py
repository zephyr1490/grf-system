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

import os, statistics, requests
from flask import Flask, request, jsonify
from functools import wraps
from racenet_client import RacenetClient

app = Flask(__name__)

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
        if pw != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*a, **kw)
    return inner

# ── SUPABASE HELPERS ──────────────────────────────────────────────────────────

def sb_get(table, qs=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{qs}", headers=SB, timeout=10)
    r.raise_for_status(); return r.json()

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB, json=data, timeout=10)
    r.raise_for_status(); return r.json()

def sb_patch(table, qs, data):
    h = {**SB, "Prefer": "return=minimal"}
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{qs}", headers=h, json=data, timeout=10)
    if not r.ok:
        print(f"[sb_patch ERROR] {table}?{qs} → HTTP {r.status_code}: {r.text}")
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
    Speichert CR-Set + Werte in Supabase (unabhängig von Championship).
    Body: { name, route_ids, class_ids, top_pct, min_n, exponent, results }
    """
    body    = request.json or {}
    name    = body.get("name","").strip()
    results = body.get("results", [])
    if not name or not results:
        return jsonify({"error": "name and results required"}), 400

    try:
        # cr_sets Eintrag erstellen
        cr_set = sb_post("cr_sets", {
            "name":           name,
            "vehicle_classes": body.get("vehicle_classes", []),
            "route_ids":      body.get("route_ids", []),
            "class_ids":      body.get("class_ids", []),
            "top_pct":        body.get("top_pct", 25),
            "min_n":          body.get("min_n", 10),
            "exponent":       body.get("exponent", 1.5),
        })
        cr_set_id = cr_set[0]["id"] if isinstance(cr_set, list) else cr_set["id"]

        # car_ratings Einträge
        rows = [{
            "cr_set_id":   cr_set_id,
            "vehicle":     r["vehicle"],
            "cr_value":    r["cr"],
            "n_entries":   r.get("n"),
            "car_avg_ms":  r.get("car_avg_ms"),
            "ref_time_ms": r.get("ref_time_ms"),
        } for r in results]
        sb_post("car_ratings", rows)

        return jsonify({"cr_set_id": cr_set_id, "name": name, "saved": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/sets", methods=["GET"])
@auth
def cr_list_sets():
    """Alle CR-Sets laden."""
    try:
        sets = sb_get("cr_sets", "order=created_at.desc&select=*")
        return jsonify(sets)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/sets/<set_id>", methods=["DELETE"])
@auth
def cr_delete_set(set_id):
    try:
        sb_delete("cr_sets", f"id=eq.{set_id}")
        return jsonify({"deleted": set_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/assign", methods=["POST"])
@auth
def cr_assign():
    """CR-Set einer Championship zuordnen. Body: { championship_id, cr_set_id }"""
    body    = request.json or {}
    champ   = body.get("championship_id")
    cr_set  = body.get("cr_set_id")
    if not champ or not cr_set:
        return jsonify({"error": "championship_id and cr_set_id required"}), 400
    try:
        sb_patch("championships", f"id=eq.{champ}", {"cr_set_id": cr_set})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    club_data = client.get_championship_details(club_id, racenet_champ_id)
    events_raw = club_data.get("events", []) or club_data.get("legs", [])

    imported = []
    for i, ev in enumerate(events_raw, 1):
        location = ev.get("location", {})
        loc_name = location.get("name", "") if isinstance(location, dict) else str(location)
        if not loc_name:
            loc_name = ev.get("locationName", ev.get("name", f"Event {i}"))

        name = f"Rd.{i} — {loc_name}"

        row = {
            "championship_id":  champ_id,
            "racenet_event_id": str(ev.get("id", ev.get("eventId", ""))),
            "name":             name,
            "location":         loc_name,
            "round_number":     i,
            "start_at":         ev.get("startAt") or ev.get("startDate"),
            "close_at":         ev.get("closeAt") or ev.get("endDate"),
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
    try:
        sb_patch("championships", f"id=eq.{champ_id}", fields)
        return jsonify({"ok": True})
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
                sb_post("team_members", [{
                    "team_id":        team_id,
                    "championship_id": champ,
                    "driver_name":    m,
                } for m in members])

            created_teams.append({"id": team_id, "name": t.get("name"), "members": members})

        return jsonify({"teams": created_teams})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/teams/<champ_id>", methods=["GET"])
@auth
def teams_get(champ_id):
    """Teams + Mitglieder einer Championship laden."""
    try:
        teams = sb_get("teams", f"championship_id=eq.{champ_id}&select=id,name,color,team_members(driver_name)")
        return jsonify(teams)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Manual Bonuses ────────────────────────────────────────────────────────────

@app.route("/bonus/add", methods=["POST"])
@auth
def bonus_add():
    body = request.json or {}
    for f in ["championship_id","event_id","driver_name","bonus_type","points"]:
        if not body.get(f): return jsonify({"error": f"{f} required"}), 400
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
    try:
        sb_patch("events", f"id=eq.{ev_id}", {"narrative": body.get("narrative","")})
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
    try:
        # Alte manuelle CR-Werte löschen (nur die ohne cr_set_id)
        sb_url = f"{SUPABASE_URL}/rest/v1/car_ratings"
        requests.delete(
            sb_url,
            headers=SB,
            params={"championship_id": f"eq.{champ}", "cr_set_id": "is.null"},
        )
        # Neue einfügen
        rows = [
            {"championship_id": champ, "vehicle": r["vehicle"],
             "cr_value": float(r["cr_value"]), "cr_set_id": None}
            for r in ratings if r.get("vehicle") and r.get("cr_value") is not None
        ]
        if rows:
            requests.post(sb_url, headers=SB, json=rows)
        # Championship cr_set_id auf null (manuell, kein Set)
        sb_patch("championships", f"id=eq.{champ}", {"cr_set_id": None})
        return jsonify({"ok": True, "saved": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        from elo_pipeline import RawEvent, process_racenet_events, process_historical_batch, summarize_track
        from elo_state    import EloState
        from elo_categories import CategoryLookups

        log_lines = []
        def log(msg):
            log_lines.append(msg)

        log(f"Starting ELO update for clubs: {', '.join(club_ids)}")

        # Leere Lookups (keine Surface/Drivetrain-Metadaten verfügbar)
        lookups = CategoryLookups(surface_by_location={}, vehicle_meta={})

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
            "select=id,club_id,name,start_date&order=start_date.asc"
        )
        champ_rows = [c for c in champ_rows if str(c.get("club_id","")) in club_ids]
        log(f"Found {len(champ_rows)} championships across clubs")

        # ── Events laden: pro Championship chronologisch nach round_number ─
        # Wir speichern (champ_start_date, round_number, RawEvent) für globale Sortierung
        raw_events_with_date = []
        # Auch end_date der letzten Stage pro Fahrer für Decay-Berechnung
        driver_last_event_date: dict = {}  # {driver_name: "YYYY-MM-DD"}

        for champ in champ_rows:
            champ_id    = champ["id"]
            champ_start = champ.get("start_date") or ""

            ev_rows = sb_get(
                "events",
                f"championship_id=eq.{champ_id}&select=id,name,location,status,start_date,end_date,round_number&order=round_number.asc"
            )
            for ev in ev_rows:
                ev_id  = ev["id"]
                if ev.get("status", 0) != 2:
                    continue

                results = sb_get(
                    "event_results",
                    f"event_id=eq.{ev_id}&select=driver_name,position,vehicle,is_dnf&order=position.asc"
                )
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
        BASELINE_MU     = 1000.0
        today = date.today()

        overall_ratings = state.ratings.get("overall", {})
        for driver_name, rating in overall_ratings.items():
            last_date_str = driver_last_event_date.get(driver_name)
            if not last_date_str:
                continue
            try:
                last_date = date.fromisoformat(last_date_str[:10])
            except Exception:
                continue
            weeks_inactive = (today - last_date).days / 7.0
            if weeks_inactive >= INACTIVE_WEEKS:
                # Decay: mu bewegt sich Richtung Baseline
                decay = DECAY_PER_WEEK * weeks_inactive
                if rating.mu > BASELINE_MU:
                    rating.mu = max(BASELINE_MU, rating.mu - decay)
                elif rating.mu < BASELINE_MU:
                    rating.mu = min(BASELINE_MU, rating.mu + decay)
                # Sigma leicht erhöhen (mehr Unsicherheit durch Inaktivität)
                rating.sigma = min(rating.sigma * 1.02 ** weeks_inactive, 350.0)
                state.driver_inactive[driver_name] = True

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

        # ── Ratings in drivers Tabelle schreiben ──────────────────────────
        driver_names_in_db = {r["name"] for r in sb_get("drivers", "select=name")}
        matched   = 0
        unmatched = []
        for summary in summaries:
            elo_val = round(summary.conservative_rating, 1)
            if summary.display_name not in driver_names_in_db:
                unmatched.append(summary.display_name)
                continue
            sb_patch("drivers", f"name=eq.{requests.utils.quote(summary.display_name)}", {
                "elo":             elo_val,
                "elo_mu":          round(summary.mu, 2),
                "elo_sigma":       round(summary.sigma, 2),
                "elo_events":      summary.events_played,
                "elo_provisional": summary.is_provisional,
                "elo_inactive":    summary.is_inactive,
            })
            matched += 1
        log(f"ELO written: {matched} matched, {len(unmatched)} unmatched")
        if unmatched:
            log(f"Unmatched (not in drivers table): {', '.join(unmatched[:10])}")

        log(f"✓ ELO update complete. {drivers_updated} drivers updated.")
        return jsonify({"ok": True, "log": log_lines, "drivers": drivers_updated})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
