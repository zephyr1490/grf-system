"""
GRF Admin API — Railway Web Service
════════════════════════════════════════════════════════════════════════════════
Kleiner Flask-Server der auf Railway neben dem Cron-Job läuft.
Übernimmt alle Admin-Aktionen die Auth brauchen:
  - CR-Berechnung (aus RaceNet Time Trial Leaderboards)
  - Championship erstellen
  - Championship Rules speichern
  - Manual Bonuses speichern / löschen
  - Narratives speichern
  - CR-Einträge speichern / löschen

Sicherheit:
  - ADMIN_PASSWORD als Railway Environment Variable (nie im Code)
  - Supabase service_role key als Railway Environment Variable
  - Alle Writes via service_role key (umgeht RLS)
  - CORS nur für die eigene Vercel-Domain

Environment Variables (in Railway setzen):
  ADMIN_PASSWORD          → Admin-Passwort (frei wählbar)
  SUPABASE_URL            → https://ixuhhzdijvtlfdjtrnyi.supabase.co
  SUPABASE_SERVICE_KEY    → service_role key aus Supabase Settings → API
  RACENET_REFRESH_TOKEN   → bereits vorhanden in Railway
  ALLOWED_ORIGIN          → https://deine-domain.vercel.app (oder * zum Testen)
════════════════════════════════════════════════════════════════════════════════
"""

import os
import time
import statistics
import requests
from flask import Flask, request, jsonify
from functools import wraps

# Import des bestehenden RaceNet Clients
from racenet_client import RacenetClient

app = Flask(__name__)

# ── Config aus Environment ────────────────────────────────────────────────────

ADMIN_PASSWORD   = os.environ.get("ADMIN_PASSWORD", "")
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_SVC_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALLOWED_ORIGIN   = os.environ.get("ALLOWED_ORIGIN", "*")

SB_HEADERS = {
    "apikey":        SUPABASE_SVC_KEY,
    "Authorization": f"Bearer {SUPABASE_SVC_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}


# ── CORS Helper ───────────────────────────────────────────────────────────────

def _cors(response):
    response.headers["Access-Control-Allow-Origin"]  = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Password"
    return response

@app.after_request
def after_request(response):
    return _cors(response)

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return _cors(jsonify({}))


# ── Auth Decorator ────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return jsonify({"error": "ADMIN_PASSWORD not configured on server"}), 500
        pw = request.headers.get("X-Admin-Password") or (request.json or {}).get("password", "")
        if pw != ADMIN_PASSWORD:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── Supabase Helpers ──────────────────────────────────────────────────────────

def sb_get(table, params=""):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}?{params}", headers=SB_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, json=data, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_patch(table, filter_str, data):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filter_str}",
                       headers={**SB_HEADERS, "Prefer": "return=minimal"},
                       json=data, timeout=10)
    r.raise_for_status()

def sb_delete(table, filter_str):
    r = requests.delete(f"{SUPABASE_URL}/rest/v1/{table}?{filter_str}",
                        headers={**SB_HEADERS, "Prefer": "return=minimal"},
                        timeout=10)
    r.raise_for_status()


# ── Health Check ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "grf-admin-api"})


# ══════════════════════════════════════════════════════════════════════════════
#  CR BERECHNUNG
# ══════════════════════════════════════════════════════════════════════════════

def _parse_time_ms(time_str: str) -> int | None:
    """
    Parst RaceNet Zeitstring zu Millisekunden.
    Format: "00:04:28.7960000" oder "4:28.796"
    """
    if not time_str:
        return None
    try:
        t = time_str.strip()
        # Format: HH:MM:SS.fffffff oder MM:SS.fff
        parts = t.split(":")
        if len(parts) == 3:
            h, m, s = parts
            secs = int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            secs = int(m) * 60 + float(s)
        else:
            secs = float(t)
        return int(secs * 1000)
    except Exception:
        return None

def _is_dnf_ms(ms: int) -> bool:
    """DNF-Erkennung: exakte ganze Minuten >= 4min."""
    if ms is None:
        return False
    if ms % 1000 != 0:
        return False
    secs = ms // 1000
    if secs % 60 != 0:
        return False
    if secs < 240:
        return False
    return True

def _compute_cr(entries: list[dict], top_pct: float, exponent: float) -> dict:
    """
    Berechnet CR für eine Gruppe von Einträgen (gleiche Stage + Klasse).

    entries: [{"vehicle": str, "time_ms": int}, ...]
    Rückgabe: {"Fahrzeugname": cr_value, ...}
    """
    # Nur valide Zeiten (keine DNF)
    valid = [e for e in entries if e.get("time_ms") and not _is_dnf_ms(e["time_ms"])]
    if not valid:
        return {}

    # Top-% der Gesamtzeiten als Referenz (Basis für "was ist schnell")
    all_times = sorted(e["time_ms"] for e in valid)
    cutoff_idx = max(1, int(len(all_times) * top_pct / 100))
    top_times  = all_times[:cutoff_idx]
    ref_time   = statistics.mean(top_times)  # Durchschnitt der Top-%

    # Pro Fahrzeug: Durchschnitt der Zeiten
    vehicle_times: dict[str, list[int]] = {}
    for e in valid:
        v = e.get("vehicle", "Unknown")
        vehicle_times.setdefault(v, []).append(e["time_ms"])

    cr_values = {}
    for vehicle, times in vehicle_times.items():
        avg = statistics.mean(times)
        # CR = (ref_time / avg) ^ exponent
        # Schnelleres Auto → kleinere avg → CR > 1
        cr = (ref_time / avg) ** exponent
        cr_values[vehicle] = round(cr, 4)

    return cr_values


@app.route("/cr/values", methods=["GET"])
@require_auth
def cr_get_values():
    """Lädt Locations + VehicleClasses aus RaceNet /api/wrc2023Stats/values."""
    try:
        client = RacenetClient()
        data   = client._get("/api/wrc2023Stats/values")
        return jsonify({
            "locations":      data.get("locations", {}),
            "vehicle_classes": data.get("vehicleClasses", {}),
            "location_routes": data.get("locationRoute", {}),
            "routes":         data.get("routes", {}),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/calculate", methods=["POST"])
@require_auth
def cr_calculate():
    """
    Berechnet CR aus RaceNet Time Trial Leaderboards.

    Body:
    {
        "route_ids":   [252, 253, ...],   // RaceNet Route IDs
        "class_ids":   [19, 21, ...],     // RaceNet Vehicle Class IDs
        "top_pct":     25,                // Top-% als Referenz
        "min_n":       10,                // Mindestanzahl Einträge pro Fahrzeug
        "exponent":    1.5,               // CR-Kurven-Exponent
        "max_results": 200                // Max Einträge pro Leaderboard
    }

    Rückgabe:
    {
        "results": [
            {
                "vehicle":        "Subaru Impreza S5 WRC",
                "class_id":       21,
                "n":              87,
                "avg_top_time_ms": 245300,
                "car_avg_ms":     248100,
                "cr":             1.234
            }, ...
        ],
        "stats": {"total_entries": 1240, "stages_loaded": 6}
    }
    """
    body       = request.json or {}
    route_ids  = body.get("route_ids", [])
    class_ids  = body.get("class_ids", [])
    top_pct    = float(body.get("top_pct", 25))
    min_n      = int(body.get("min_n", 10))
    exponent   = float(body.get("exponent", 1.5))
    max_results= int(body.get("max_results", 200))

    if not route_ids or not class_ids:
        return jsonify({"error": "route_ids and class_ids required"}), 400

    client = RacenetClient()

    # Alle Einträge sammeln: vehicle → [time_ms, ...]
    vehicle_entries: dict[str, list[int]] = {}
    # Für Statistik
    total_entries  = 0
    stages_loaded  = 0
    all_entries_flat = []  # für globale Top-% Berechnung

    combos = [(int(r), int(c)) for r in route_ids for c in class_ids]

    # Parallel laden
    leaderboards = client.get_public_leaderboards_parallel(
        combos, max_results=max_results, max_workers=8
    )

    for (route_id, class_id), entries in leaderboards.items():
        if not entries:
            continue
        stages_loaded += 1
        for e in entries:
            ms = _parse_time_ms(e.get("time", ""))
            if ms and not _is_dnf_ms(ms):
                vehicle = e.get("vehicle", "Unknown")
                vehicle_entries.setdefault(vehicle, []).append(ms)
                all_entries_flat.append(ms)
                total_entries += 1

    if not all_entries_flat:
        return jsonify({"error": "No valid times found for the selected stages/classes"}), 404

    # Globale Referenzzeit (Top-% aller Einträge über alle Stages)
    all_sorted  = sorted(all_entries_flat)
    cutoff_idx  = max(1, int(len(all_sorted) * top_pct / 100))
    ref_time_ms = statistics.mean(all_sorted[:cutoff_idx])

    results = []
    for vehicle, times in vehicle_entries.items():
        n = len(times)
        if n < min_n:
            continue
        avg_ms = statistics.mean(times)
        cr     = round((ref_time_ms / avg_ms) ** exponent, 4)
        results.append({
            "vehicle":         vehicle,
            "n":               n,
            "avg_top_time_ms": int(ref_time_ms),
            "car_avg_ms":      int(avg_ms),
            "cr":              cr,
        })

    # Sortieren nach CR absteigend
    results.sort(key=lambda x: x["cr"], reverse=True)

    return jsonify({
        "results": results,
        "stats": {
            "total_entries": total_entries,
            "stages_loaded": stages_loaded,
            "ref_time_ms":   int(ref_time_ms),
        }
    })


# ══════════════════════════════════════════════════════════════════════════════
#  CR IN SUPABASE SPEICHERN / LÖSCHEN
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/cr/save", methods=["POST"])
@require_auth
def cr_save():
    """
    Speichert CR-Werte in Supabase car_ratings.
    Body: { "championship_id": "uuid", "results": [{vehicle, cr, ...}, ...] }
    """
    body    = request.json or {}
    champ   = body.get("championship_id")
    results = body.get("results", [])
    if not champ or not results:
        return jsonify({"error": "championship_id and results required"}), 400

    # Bestehende CR für diese Championship löschen
    try:
        sb_delete("car_ratings", f"championship_id=eq.{champ}")
    except Exception:
        pass  # Ignore wenn noch keine vorhanden

    rows = [{
        "championship_id": champ,
        "vehicle":         r["vehicle"],
        "cr_value":        r["cr"],
        "surface":         "all",
        "era":             r.get("era", ""),
    } for r in results]

    try:
        sb_post("car_ratings", rows)
        return jsonify({"saved": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cr/delete/<row_id>", methods=["DELETE"])
@require_auth
def cr_delete(row_id):
    try:
        sb_delete("car_ratings", f"id=eq.{row_id}")
        return jsonify({"deleted": row_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  CHAMPIONSHIP SETUP
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/championship/create", methods=["POST"])
@require_auth
def championship_create():
    """
    Erstellt eine neue Championship + Rules.
    Body: { championship: {...}, rules: [{...}, ...] }
    """
    body  = request.json or {}
    champ = body.get("championship", {})
    rules = body.get("rules", [])

    if not champ.get("name") or not champ.get("club_id"):
        return jsonify({"error": "name and club_id required"}), 400

    try:
        created = sb_post("championships", champ)
        champ_id = created[0]["id"] if isinstance(created, list) else created["id"]

        if rules:
            for r in rules:
                r["championship_id"] = champ_id
            sb_post("championship_rules", rules)

        return jsonify({"id": champ_id, "name": champ.get("name")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  MANUAL BONUSES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/bonus/add", methods=["POST"])
@require_auth
def bonus_add():
    """
    Fügt einen Manual Bonus hinzu und aktualisiert event_results.
    Body: { championship_id, event_id, driver_name, bonus_type, bonus_name, points, note }
    """
    body = request.json or {}
    required = ["championship_id", "event_id", "driver_name", "bonus_type", "points"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f"{f} required"}), 400

    try:
        # Bonus speichern
        sb_post("manual_bonuses", {
            "championship_id": body["championship_id"],
            "event_id":        body["event_id"],
            "driver_name":     body["driver_name"],
            "bonus_type":      body["bonus_type"],
            "bonus_name":      body.get("bonus_name", ""),
            "points":          int(body["points"]),
            "note":            body.get("note", ""),
        })

        # event_results aktualisieren
        driver  = body["driver_name"]
        ev_id   = body["event_id"]
        pts     = int(body["points"])

        rows = sb_get("event_results",
                      f"event_id=eq.{ev_id}&driver_name=eq.{requests.utils.quote(driver)}&select=id,bonus_points,cr_points,total_points")
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
@require_auth
def bonus_delete(bonus_id):
    try:
        sb_delete("manual_bonuses", f"id=eq.{bonus_id}")
        return jsonify({"deleted": bonus_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  NARRATIVES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/narrative/championship", methods=["POST"])
@require_auth
def narrative_championship():
    body = request.json or {}
    champ_id  = body.get("championship_id")
    narrative = body.get("narrative", "")
    if not champ_id:
        return jsonify({"error": "championship_id required"}), 400
    try:
        sb_patch("championships", f"id=eq.{champ_id}", {"narrative": narrative})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/narrative/event", methods=["POST"])
@require_auth
def narrative_event():
    body = request.json or {}
    ev_id     = body.get("event_id")
    narrative = body.get("narrative", "")
    if not ev_id:
        return jsonify({"error": "event_id required"}), 400
    try:
        sb_patch("events", f"id=eq.{ev_id}", {"narrative": narrative})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
