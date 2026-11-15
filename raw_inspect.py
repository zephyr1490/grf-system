"""
Diagnose (nur lesen, schreibt nichts): zeigt die UNVERAENDERTEN RaceNet-Rohdaten von Events/Stages.

Zweck: herausfinden, ob RaceNet pro Club-Event-Stage eine Strecken-ID (route) mitliefert, die wir bisher
ignorieren (grf_sync.py liest nur id, name, leaderboardID, status).

  POST /admin/racenet/raw
       {club_id: "14317", championship_id?: "...", event_index?: 2 (1-basiert, Standard 1),
        max_events?: 1}
  -> {championship_keys, event_keys, events:[{index, raw_event_without_stages, stages:[<roh>...]}],
      stage_keys_union}
"""
from __future__ import annotations

from flask import jsonify, request


def register_routes(app, h):
    auth, err_detail = h["auth"], h["err_detail"]

    @app.route("/admin/racenet/raw", methods=["POST"])
    @auth
    def admin_racenet_raw():
        try:
            from racenet_client import RacenetClient
            body = request.json or {}
            club_id = str(body.get("club_id") or "").strip()
            if not club_id:
                return jsonify({"error": "club_id required"}), 400
            client = RacenetClient()
            champ_id = str(body.get("championship_id") or "").strip()
            if champ_id:
                champ = client.get_championship(club_id, champ_id)
            else:
                champ = client.get_club(club_id).get("currentChampionship") or {}
            events = champ.get("events") or champ.get("legs") or []
            start = max(int(body.get("event_index") or 1), 1) - 1
            count = min(max(int(body.get("max_events") or 1), 1), 3)
            out, union = [], set()
            for i, ev in enumerate(events[start:start + count], start=start + 1):
                stages = ev.get("stages") or []
                for s in stages:
                    if isinstance(s, dict):
                        union.update(s.keys())
                out.append({
                    "index": i,
                    "raw_event_without_stages": {k: v for k, v in ev.items() if k != "stages"},
                    "stages": stages,
                })
            return jsonify({
                "championship_id": champ.get("id"),
                "championship_keys": sorted(champ.keys()),
                "event_count": len(events),
                "event_keys": sorted(events[0].keys()) if events else [],
                "stage_keys_union": sorted(union),
                "events": out,
            })
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500
