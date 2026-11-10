"""
Placement-Event: Stage-Aufloesung (Name -> RaceNet route_id / class_id).

Die Placement-Stages sind RaceNet-Time-Trial-Leaderboards (TIME_TRIAL_R{route}_VC{class}_{DRY|WET}),
KEINE Club-Event-Stages. Deshalb steht in der Tabelle `stages` nichts darunter, und dort sieht man
bei Club-Events teils nur Ziffern. Die Namen liefert RaceNets /api/wrc2023Stats/values
(locations, routes, locationRoute, vehicleClasses) - dieselbe Quelle wie der CR-Calculator (/cr/values).

  POST /admin/placement/resolve
       {stages:[{location:"Portugal", route:"Celeiro", class:"Rally3", surface:"dry"}, ...],
        with_counts?: bool}
  -> je Stage: Kandidaten fuer Location / Route / Klasse (id + Name) und ob die Aufloesung eindeutig ist.
     Mit with_counts zusaetzlich: Anzahl Leaderboard-Eintraege gesamt und je Auto.

Schreibt nichts. Eingehaengt wie die anderen Module: placement_resolve.register_routes(app, h)
(h braucht auth, err_detail).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from flask import jsonify, request


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _match(query, id_to_name):
    """Kandidaten (id, name): erst exakter Treffer, sonst Teilstring in beide Richtungen."""
    q = _norm(query)
    if not q:
        return []
    items = [(str(i), str(n)) for i, n in id_to_name.items()]
    exact = [(i, n) for i, n in items if _norm(n) == q]
    if exact:
        return exact
    return [(i, n) for i, n in items if q in _norm(n) or (_norm(n) and _norm(n) in q)]


def resolve_stage(spec, values):
    """spec: {location, route, class, surface}; values: RaceNet-/values-Antwort. Rein, ohne Netzwerk."""
    locations = values.get("locations") or {}
    routes = values.get("routes") or {}
    loc_routes = values.get("locationRoute") or {}
    classes = values.get("vehicleClasses") or {}

    locs = _match(spec.get("location", ""), locations)
    routes_found = []
    for lid, lname in locs:
        allowed = {str(r) for r in loc_routes.get(lid, loc_routes.get(int(lid) if lid.isdigit() else lid, []))}
        sub = {r: routes[r] for r in routes if str(r) in allowed}
        routes_found += [(lid, rid, rname) for rid, rname in _match(spec.get("route", ""), sub)]
    cls = _match(spec.get("class", ""), classes)
    surface = str(spec.get("surface") or "dry").upper()
    surface = surface if surface in ("DRY", "WET") else "DRY"
    out = {
        "query": spec,
        "locations": [{"id": i, "name": n} for i, n in locs],
        "routes": [{"location_id": l, "id": r, "name": n} for l, r, n in routes_found],
        "classes": [{"id": i, "name": n} for i, n in cls],
        "surface": surface,
    }
    out["unique"] = len(locs) == 1 and len(routes_found) == 1 and len(cls) == 1
    if out["unique"]:
        out["leaderboard_id"] = f"TIME_TRIAL_R{routes_found[0][1]}_VC{cls[0][0]}_{surface}"
        out["route_id"], out["class_id"] = routes_found[0][1], cls[0][0]
    return out


def leaderboard_counts(client, route_id, class_id, limit=5000):
    entries = client.get_public_leaderboard(int(route_id), int(class_id), max_results=limit) or []
    cars = Counter(e.get("vehicle", "Unknown") for e in entries)
    out = {"entries": len(entries), "per_car": dict(cars.most_common())}
    return out



# ── Kandidatensuche: Stages mit vielen ELO-Fahrern ───────────────────────────
import json
import os
import statistics


def _time_s(t):
    """RaceNet-Zeitstring '00:04:28.7960000' -> Sekunden (None bei Fehler)."""
    try:
        p = str(t).strip().split(":")
        if len(p) == 3:
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
        if len(p) == 2:
            return int(p[0]) * 60 + float(p[1])
        return float(t)
    except Exception:
        return None


def _dnf_like(sec):
    """Ganzzahlig, volle Minuten, mind. 4 Minuten = RaceNet-Ausfall-Zeit (wie _is_dnf_ms im CR-Calculator)."""
    return sec is not None and abs(sec - round(sec)) < 0.01 and round(sec) % 60 == 0 and sec >= 240


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    i = max(0, min(len(sorted_vals) - 1, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def analyze_leaderboard(entries, elo_mu):
    """entries: Rangliste einer Stage/Klasse; elo_mu: {Fahrername(casefold): mu}.
    Zaehlt nur. Rueckgabe: Eintraege, davon mit ELO, Zeit-Median, Streuung der ELO-Fahrer (Mu P10/P50/P90)."""
    secs_all, secs_elo, mus = [], [], []
    for e in entries or []:
        sec = _time_s(e.get("time", ""))
        if not sec or _dnf_like(sec):
            continue
        secs_all.append(sec)
        mu = elo_mu.get(str(e.get("displayName", "")).casefold())
        if mu is not None:
            secs_elo.append(sec)
            mus.append(mu)
    mus.sort()
    ref = secs_elo or secs_all
    return {
        "entries": len(entries or []),
        "valid": len(secs_all),
        "elo_matched": len(secs_elo),
        "median_s": round(statistics.median(ref), 1) if ref else None,
        "mu_p10_p50_p90": [round(_pct(mus, p)) for p in (10, 50, 90)] if mus else None,
    }


def load_surface_lookup():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "category_lookups_surface.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def rank_candidates(results, max_minutes=8, per_group=15):
    """results: Liste von dicts (location, route, class, surface, + analyze_leaderboard-Felder).
    Filter: Zeit-Median < max_minutes. Gruppiert nach (Klasse, Belag), sortiert nach ELO-Fahrern."""
    groups = {}
    for r in results:
        if r.get("median_s") is None or r["median_s"] >= max_minutes * 60:
            continue
        groups.setdefault((r["class"], r["surface"]), []).append(r)
    out = []
    for (cls, surf), rows in sorted(groups.items()):
        rows.sort(key=lambda r: -r["elo_matched"])
        out.append({"class": cls, "surface": surf, "top": rows[:per_group], "candidates": len(rows)})
    return out


def register_routes(app, h):
    auth, err_detail = h["auth"], h["err_detail"]

    @app.route("/admin/placement/resolve", methods=["POST"])
    @auth
    def admin_placement_resolve():
        try:
            from racenet_client import RacenetClient
            body = request.json or {}
            specs = body.get("stages") or []
            if not isinstance(specs, list) or not specs or len(specs) > 10:
                return jsonify({"error": "stages: 1-10 Eintraege noetig"}), 400
            client = RacenetClient()
            values = client._get("/api/wrc2023Stats/values")
            res = [resolve_stage(s, values) for s in specs]
            if body.get("with_counts"):
                for r in res:
                    if r["unique"]:
                        try:
                            r["counts"] = leaderboard_counts(client, r["route_id"], r["class_id"])
                        except Exception as e:
                            r["counts"] = {"error": str(e)}
            return jsonify({"stages": res})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/placement/candidates", methods=["POST"])
    @auth
    def admin_placement_candidates():
        """Sucht Placement-Kandidaten: Stages (trocken) mit vielen Fahrern, die bei uns ein ELO haben.
        Body: {classes:["Rally3","F2 Kit Car","Group B (RWD)"], surfaces?:["gravel","tarmac"],
               locations?:[Namen], max_minutes?:8, per_group?:15}
        Antwort als NDJSON-Stream (progress / heartbeat / result), weil das viele Ranglisten laedt.
        Schreibt nichts, rechnet nichts ausser Zaehlen."""
        import json as _json, threading, queue, time
        from racenet_client import RacenetClient
        from team_draw import _load_overall
        body = request.json or {}
        cls_q = body.get("classes") or []
        surfaces = [str(x).lower() for x in (body.get("surfaces") or ["gravel", "tarmac"])]
        loc_q = body.get("locations") or []
        max_min = float(body.get("max_minutes") or 8)
        per_group = max(1, min(int(body.get("per_group") or 15), 50))
        if not isinstance(cls_q, list) or not cls_q or len(cls_q) > 6:
            return jsonify({"error": "classes: 1-6 Klassen noetig"}), 400
        try:
            client = RacenetClient()
            values = client._get("/api/wrc2023Stats/values")
            locations = values.get("locations") or {}
            routes = values.get("routes") or {}
            loc_routes = values.get("locationRoute") or {}
            classes = values.get("vehicleClasses") or {}
            cls_ids = {}
            for qn in cls_q:
                m = _match(qn, classes)
                if len(m) != 1:
                    return jsonify({"error": f"Klasse '{qn}' nicht eindeutig: {[n for _, n in m]}"}), 400
                cls_ids[m[0][0]] = m[0][1]
            surf_by_loc = load_surface_lookup()
            loc_filter = None
            if loc_q:
                loc_filter = set()
                for ln in loc_q:
                    loc_filter |= {i for i, _ in _match(ln, locations)}
            route_meta = {}
            for lid, rids in loc_routes.items():
                lname = locations.get(lid) or locations.get(str(lid)) or str(lid)
                if loc_filter is not None and str(lid) not in loc_filter:
                    continue
                surf = str(surf_by_loc.get(lname, "unknown")).lower()
                if surf not in surfaces:
                    continue
                for rid in rids:
                    rname = routes.get(str(rid)) or routes.get(rid) or str(rid)
                    route_meta[int(rid)] = {"location": lname, "route": rname, "surface": surf}
            combos = [(rid, int(cid)) for rid in route_meta for cid in cls_ids]
            if not combos:
                return jsonify({"error": "keine Stages nach Filter"}), 400
            if len(combos) > 3000:
                return jsonify({"error": f"{len(combos)} Ranglisten sind zu viel - Klassen oder Locations einschraenken"}), 400
            overall = _load_overall(h["sb_get"])
            elo_mu = {str(n).casefold(): float(v["mu"]) for n, v in overall.items() if v and v.get("mu") is not None}
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

        q = queue.Queue()

        def _worker():
            try:
                lbs = client.get_public_leaderboards_parallel(
                    combos, max_results=99999, max_workers=8,
                    on_progress=lambda done, total, sid, cid: q.put({"type": "progress", "done": done, "total": total}))
                rows = []
                for (rid, cid), entries in lbs.items():
                    if not entries:
                        continue
                    meta = route_meta.get(int(rid), {})
                    a = analyze_leaderboard(entries, elo_mu)
                    rows.append({"route_id": rid, "class_id": cid, "class": cls_ids.get(str(cid)) or cls_ids.get(cid),
                                 "location": meta.get("location"), "route": meta.get("route"),
                                 "surface": meta.get("surface"), **a})
                q.put({"type": "result", "status": 200, "data": {
                    "scanned": len(combos), "elo_drivers_total": len(elo_mu),
                    "max_minutes": max_min, "groups": rank_candidates(rows, max_min, per_group)}})
            except Exception as e:
                q.put({"type": "result", "status": 500, "data": {"error": f"RaceNet error: {e}"}})

        threading.Thread(target=_worker, daemon=True).start()

        def _gen():
            yield _json.dumps({"type": "progress", "done": 0, "total": len(combos)}) + "\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield _json.dumps({"type": "heartbeat"}) + "\n"
                    continue
                yield _json.dumps(ev) + "\n"
                if ev["type"] == "result":
                    return

        resp = app.response_class(_gen(), mimetype="application/x-ndjson")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        return resp
