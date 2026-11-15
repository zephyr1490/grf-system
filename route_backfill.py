"""
Einmaliger Nachtrag der Strecken-Infos (route_id, route_name, distance_km) fuer bereits gespeicherte Stages.
Voraussetzung: route_columns.sql ausgefuehrt.

  POST /admin/routes/backfill   {offset?: 0, max_seconds?: 600, dry_run?: false}
  -> {done, total, next_offset, patched_stages, errors:[...]}   (next_offset null = fertig)

Je Championship ein RaceNet-Call, dann je Strecke EIN Patch (id=in.(...)). Laeuft im Zeitbudget
(Railway bricht nach ~15 min ab) -> bei next_offset != null einfach mit offset erneut aufrufen.
Aendert nur die drei neuen Spalten von stages.
"""
from __future__ import annotations

import re
import time

from flask import jsonify, request

_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def collect_route_rows(champ: dict) -> list:
    """[{id, route_id, route_name, distance_km}] aller Stages einer RaceNet-Championship."""
    rows = []
    for ev in (champ.get("events") or champ.get("legs") or []):
        for st in (ev.get("stages") or []):
            ss = (st or {}).get("stageSettings") or {}
            if not st.get("id") or ss.get("routeID") is None:
                continue
            rows.append({"id": str(st["id"]), "route_id": int(ss["routeID"]),
                         "route_name": str(ss.get("route") or ""), "distance_km": ss.get("distance")})
    return rows


def group_by_route(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        key = (r["route_id"], r["route_name"], r["distance_km"])
        out.setdefault(key, []).append(r["id"])
    return out


def register_routes(app, h):
    auth, err_detail = h["auth"], h["err_detail"]
    sb_get_all, sb_patch = h["sb_get_all"], h["sb_patch"]

    @app.route("/admin/routes/backfill", methods=["POST"])
    @auth
    def admin_routes_backfill():
        try:
            from racenet_client import RacenetClient
            body = request.json or {}
            offset = max(int(body.get("offset") or 0), 0)
            budget = min(max(int(body.get("max_seconds") or 600), 30), 780)
            dry = bool(body.get("dry_run"))
            t0 = time.time()
            champs = [c for c in sb_get_all("championships", "select=id,club_id&order=id.asc")
                      if c.get("id") and c.get("club_id")]
            client = RacenetClient()
            patched, errors, i = 0, [], offset
            while i < len(champs) and time.time() - t0 < budget:
                c = champs[i]
                try:
                    champ = client.get_championship(str(c["club_id"]), c["id"])
                    for (rid, rname, dist), ids in group_by_route(collect_route_rows(champ)).items():
                        ids = [x for x in ids if _ID.match(x)]
                        for k in range(0, len(ids), 100):
                            chunk = ids[k:k + 100]
                            if not dry:
                                sb_patch("stages", "id=in.(" + ",".join(chunk) + ")",
                                         {"route_id": rid, "route_name": rname, "distance_km": dist})
                            patched += len(chunk)
                except Exception as e:
                    errors.append({"championship_id": c["id"], "error": err_detail(e)})
                i += 1
            return jsonify({"done": i, "total": len(champs), "patched_stages": patched,
                            "next_offset": i if i < len(champs) else None,
                            "dry_run": dry, "errors": errors[:30], "error_count": len(errors)})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500
