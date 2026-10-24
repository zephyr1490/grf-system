"""
Team-Auslosung (Teamed-Club): Admin-Endpunkte.

  POST /admin/team-draw/preview  {championship_id, seed?}  -> berechnet nur, schreibt NICHTS
  POST /admin/team-draw/save     {championship_id, seed, teams:[{name,color,members:[..]}]}
                                  -> schreibt teams/team_members (wie /teams/save) + Protokoll in team_draw_runs

Rechenlogik: team_lottery.py (liegt daneben). Ablauf, Regeln und Hintergruende:
claude/team_lottery_concept.md. ELO-Basis ist Overall (elo_state.state_json.ratings.overall).
Wer noch kein Rating hat (z. B. neuer Fahrer ohne Event in einem anderen Club), bekommt das
Standard-Rating (1500 / sigma 350) und wird in der Vorschau als "unrated" markiert.
"""
from __future__ import annotations

import json
import random
import re
import time
from urllib.parse import quote

from flask import jsonify, request

import team_lottery as TL
from team_signup import analyze_wishes

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MIN_PLAYERS, MAX_PLAYERS = 4, 100
DEFAULT_MU, DEFAULT_SIGMA = 1500.0, 350.0

# Platzhalter-Teamnamen "Team [Farbe]" (Anzeigename, Hex fuer teams.color). 25 Stueck reichen fuer 100 Spieler.
COLORS = [
    ("Pink", "#FF4FA3"), ("Hellblau", "#5BC8F5"), ("Gold", "#E8B923"), ("Rot", "#D93636"),
    ("Dunkelblau", "#1F3FA8"), ("Silber", "#C0C4C8"), ("Grün", "#2FB14E"), ("Orange", "#F28C28"),
    ("Lila", "#8E44C8"), ("Weiß", "#F4F4F4"), ("Türkis", "#1CB8A8"), ("Gelb", "#F5E336"),
    ("Schwarz", "#2A2A2A"), ("Braun", "#8B5A2B"), ("Mint", "#8FE3B5"), ("Magenta", "#C2185B"),
    ("Dunkelgrün", "#1B5E32"), ("Bordeaux", "#7A1F3D"), ("Beige", "#D8C3A0"), ("Grau", "#808488"),
    ("Koralle", "#FF6F61"), ("Indigo", "#3F3FA0"), ("Bronze", "#B0713A"), ("Limette", "#B5E61D"),
    ("Flieder", "#BFA2DB"),
]


_ELO_CACHE = {"t": 0.0, "data": None}
ELO_CACHE_SECONDS = 120   # elo_state ist ~1,5 MB: bei Best-of-N-Laeufen nicht bei jedem Teil-Aufruf neu laden (Egress)
EP_SEED = 1               # Seed der Punkte-Simulation: fest, damit ein Seed immer dieselben Teams ergibt
MAX_RUNS_PER_CALL = 6     # Laeufe pro HTTP-Aufruf (Timeout-Schutz); das Frontend ruft mehrfach auf


def _load_overall(sb_get):
    now = time.time()
    if _ELO_CACHE["data"] is not None and now - _ELO_CACHE["t"] < ELO_CACHE_SECONDS:
        return _ELO_CACHE["data"]
    rows = sb_get("elo_state", "select=state_json&limit=1")
    if not rows:
        return {}
    sj = rows[0]["state_json"]
    if isinstance(sj, str):
        sj = json.loads(sj)
    data = (sj.get("ratings") or {}).get("overall") or {}
    _ELO_CACHE.update(t=now, data=data)
    return data


def _rank_key(res):
    """Besser = kleiner. Erst Toleranz (<= 10 %), dann moeglichst wenige aufgeloeste Gruppen,
    dann moeglichst viele erfuellte Wuensche, dann kleinste Abweichung."""
    return (res["max_dev"] > 0.10, len(res["dissolved"]), -res["soft_ok"], res["max_dev"])


def compute_plan(sb_get, cid, seed, runs=1):
    """Berechnung fuer eine Championship: `runs` Laeufe mit den Seeds seed, seed+1, ... und der beste
    wird zurueckgegeben (s. _rank_key). Rueckgabe (dict, None) oder (None, (fehlertext, status))."""
    q = lambda v: quote(str(v), safe="")
    signups = [r["driver_name"] for r in sb_get("team_signups", f"championship_id=eq.{q(cid)}&select=driver_name&order=signed_up_at.asc")]
    n = len(signups)
    if n < MIN_PLAYERS:
        return None, (f"Mindestens {MIN_PLAYERS} Anmeldungen noetig (aktuell {n})", 400)
    if n > MAX_PLAYERS:
        return None, (f"Hoechstens {MAX_PLAYERS} Anmeldungen moeglich (aktuell {n})", 400)
    wishes = {w["driver_name"]: w["partner_name"]
              for w in sb_get("team_wishes", f"championship_id=eq.{q(cid)}&select=driver_name,partner_name")}
    overall = _load_overall(sb_get)

    rating, unrated = {}, []
    for nm in signups:
        r = overall.get(nm)
        if r and r.get("mu") is not None:
            rating[nm] = (float(r["mu"]), float(r.get("sigma", DEFAULT_SIGMA)))
        else:
            rating[nm] = (DEFAULT_MU, DEFAULT_SIGMA)
            unrated.append(nm)

    ep = TL.expected_points(rating, sims=4000, seed=EP_SEED)
    runs = max(1, min(int(runs), MAX_RUNS_PER_CALL))
    cand = []
    for i in range(runs):
        s = seed + i
        plan = TL.plan_teams(ep, signups, wishes, seed=s, iters=12000, restarts=2)
        info = plan["info"]
        cand.append({"seed": s, "plan": plan, "max_dev": info["max_dev"], "soft_ok": info["soft_ok"],
                     "soft_total": info["soft_total"], "dissolved": plan["dissolved"]})
    best = min(cand, key=_rank_key)
    seed, plan, info = best["seed"], best["plan"], best["plan"]["info"]
    teams = plan["teams"]
    sums = [sum(ep[m] for m in t) for t in teams]
    mean = sum(sums) / len(sums)
    order = list(range(len(teams)))
    random.Random(seed).shuffle(order)          # Farb-Zuordnung zufaellig, aber mit dem Seed reproduzierbar
    out_teams = []
    for k, i in enumerate(order):
        name, color = COLORS[k % len(COLORS)]
        members = sorted(teams[i], key=lambda m: -ep[m])
        out_teams.append({
            "name": f"Team {name}", "color": color,
            "members": [{"name": m, "ep": round(ep[m], 2), "mu": round(rating[m][0], 1),
                         "sigma": round(rating[m][1], 1), "unrated": m in unrated} for m in members],
            "sum": round(sums[i], 2), "dev": round((sums[i] - mean) / mean, 4),
        })
    out_teams.sort(key=lambda t: -t["sum"])
    return {
        "seed": seed, "players": n, "unrated": unrated,
        "teams": out_teams,
        "sizes": sorted(len(t["members"]) for t in out_teams),
        "mean_sum": round(mean, 2),
        "max_dev": round(info["max_dev"], 4),
        "within_tolerance": info["max_dev"] <= 0.10,
        "soft_total": info["soft_total"], "soft_ok": info["soft_ok"],
        "firm_groups": [list(g) for g in plan["analysis"]["firm_groups"]],
        "kept_groups": [list(g) for g in plan["kept_groups"]],
        "dissolved": [list(g) for g in plan["dissolved"]],
        "void_wishes": plan["analysis"]["void_wishes"],
        "runs": runs,
        "runs_summary": [{"seed": c["seed"], "max_dev": round(c["max_dev"], 4), "soft_ok": c["soft_ok"],
                          "dissolved": len(c["dissolved"])} for c in cand],
    }, None


def register_routes(app, h):
    sb_get, sb_post, sb_delete = h["sb_get"], h["sb_post"], h["sb_delete"]
    auth, err_detail = h["auth"], h["err_detail"]

    def _cid(body):
        cid = body.get("championship_id")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            return None
        return cid

    @app.route("/admin/team-draw/preview", methods=["POST"])
    @auth
    def admin_team_draw_preview():
        try:
            body = request.json or {}
            cid = _cid(body)
            if not cid:
                return jsonify({"error": "invalid championship_id"}), 400
            seed = body.get("seed")
            seed = int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() \
                else random.SystemRandom().randrange(1, 10**9)
            res, err = compute_plan(sb_get, cid, seed, runs=body.get("runs") or 1)
            if err:
                return jsonify({"error": err[0]}), err[1]
            return jsonify(res)
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/team-draw/save", methods=["POST"])
    @auth
    def admin_team_draw_save():
        """Speichert genau die in der Vorschau gezeigten Teams (kein Neuberechnen, damit sich
        zwischenzeitliche ELO-Updates nicht auf das Ergebnis auswirken). Prueft, dass die Teams
        exakt die aktuell Angemeldeten enthalten."""
        try:
            body = request.json or {}
            cid = _cid(body)
            if not cid:
                return jsonify({"error": "invalid championship_id"}), 400
            teams = body.get("teams") or []
            q = lambda v: quote(str(v), safe="")
            signed = {r["driver_name"] for r in sb_get("team_signups", f"championship_id=eq.{q(cid)}&select=driver_name")}
            seen = []
            for t in teams:
                seen += [m if isinstance(m, str) else m.get("name") for m in t.get("members", [])]
            if len(seen) != len(set(seen)) or set(seen) != signed:
                return jsonify({"error": "Anmeldungen haben sich seit der Vorschau geaendert — bitte neu auslosen",
                                "code": "stale"}), 409
            sb_delete("teams", f"championship_id=eq.{q(cid)}")
            created = []
            for t in teams:
                row = sb_post("teams", {"championship_id": cid, "name": str(t.get("name", "")).strip(),
                                        "color": t.get("color", "")})
                tid = row[0]["id"] if isinstance(row, list) else row["id"]
                members = [m if isinstance(m, str) else m.get("name") for m in t.get("members", [])]
                if members:
                    sb_post("team_members", [{"team_id": tid, "driver_name": m} for m in members])
                created.append({"id": tid, "name": t.get("name"), "members": members})
            try:  # Protokoll (Seed + komplettes Ergebnis); Fehler hier darf das Speichern nicht kippen
                sb_post("team_draw_runs", {"championship_id": cid, "seed": int(body.get("seed") or 0),
                                           "result_json": body.get("preview") or {"teams": teams}})
            except Exception as e:
                print(f"[team_draw] Protokoll nicht gespeichert: {e}")
            return jsonify({"ok": True, "teams": created})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500
