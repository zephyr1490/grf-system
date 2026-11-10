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
