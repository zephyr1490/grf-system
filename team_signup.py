"""
Team Sign-up + Team-Wünsche (Teamed-Club).

Konzept: claude/team_lottery_concept.md. Liegt neben admin_api.py und wird dort
über register_routes() eingehängt (eigenes Modul, damit admin_api.py nicht noch
größer wird und die Gruppenerkennung ohne Flask testbar bleibt).

Identität läuft wie bei der Stage-Interview-Funktion ausschließlich über den
vom Admin vergebenen Fahrer-PIN (driver_pins), kein Login.

Tabellen: team_signups (öffentlich lesbar), team_wishes (geheim, nur service_role),
championships.signup_open / signup_deadline (s. team_signup.sql),
championships.team_size_pref = vom Admin gewählte Wunsch-Teamgröße (s. team_size.sql).
"""

from __future__ import annotations

import hmac
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from flask import jsonify, request

DEFAULT_TEAM_SIZE = 3   # Wunsch-Teamgröße, falls nichts gesetzt ist
MIN_TEAM_SIZE, MAX_TEAM_SIZE = 2, 8
MAX_GROUP_SIZE = DEFAULT_TEAM_SIZE + 1   # Standardwert für analyze_wishes; die Auslosung übergibt pref + 1
MIN_GROUP_SIZE = 2


def clamp_team_size(value):
    """Wunsch-Teamgröße auf 2..8 begrenzen; ungültig/leer -> Standard (3)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TEAM_SIZE
    return max(MIN_TEAM_SIZE, min(MAX_TEAM_SIZE, v))


# ─────────────────────────────────────────────────────────────────────────────
#  GRUPPENERKENNUNG (rein, ohne Flask/DB)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_wishes(signups, wishes, max_group=MAX_GROUP_SIZE):
    """
    signups: Iterable[str]            — angemeldete Fahrer (kanonische Namen)
    wishes:  dict[str, str]           — Fahrer -> Wunschpartner (genau EIN Wunsch)
    max_group: größte feste Gruppe (= Wunsch-Teamgröße + 1)

    Jeder Fahrer hat höchstens eine ausgehende Verbindung → jede Komponente ist
    entweder ein Baum, der in einen Kreis oder ins Leere läuft. Daraus folgt:

      firm_groups  — Kreise der Länge 2..max_group (A↔B, A→B→C→A, ...): feste Gruppen
      soft_wishes  — alle anderen Wünsche zwischen Angemeldeten (einseitig, Teil
                     einer Kette, die in keinen Kreis mündet, oder Kreis > max_group)
      void_wishes  — Wunsch von/auf einen NICHT angemeldeten Fahrer (zählt nicht)

    Wünsche auf nicht angemeldete Fahrer werden hier bewusst nicht gelöscht: meldet
    sich der Partner bis zur Deadline noch an, zählen sie automatisch wieder.
    """
    signed = set(signups)
    edges = {}
    void = []
    for a, b in wishes.items():
        if a not in signed:
            void.append({"from": a, "to": b, "reason": "wisher not signed up"})
        elif b not in signed:
            void.append({"from": a, "to": b, "reason": "partner not signed up"})
        elif a == b:
            void.append({"from": a, "to": b, "reason": "self"})
        else:
            edges[a] = b

    # Kreise finden: jeder Knoten hat max. eine ausgehende Kante.
    color = {}          # 0 = in Arbeit, 1 = fertig
    cycles = []
    for start in sorted(edges):
        if start in color:
            continue
        path, pos = [], {}
        node = start
        while node in edges and node not in color:
            if node in pos:                       # Kreis gefunden
                cycles.append(path[pos[node]:])
                break
            pos[node] = len(path)
            path.append(node)
            node = edges[node]
        for n in path:
            color[n] = 1

    firm, in_firm = [], set()
    too_long = []
    for cyc in cycles:
        # kanonische Reihenfolge: beim alphabetisch kleinsten Namen beginnen
        i = min(range(len(cyc)), key=lambda k: cyc[k].lower())
        cyc = cyc[i:] + cyc[:i]
        if MIN_GROUP_SIZE <= len(cyc) <= max_group:
            firm.append(cyc)
            in_firm.update(cyc)
        else:
            too_long.append(cyc)
    firm.sort(key=lambda c: (len(c), c[0].lower()))

    soft = []
    long_nodes = {n for c in too_long for n in c}
    for a, b in sorted(edges.items()):
        if a in in_firm:
            continue
        reason = "cycle longer than %d" % max_group if a in long_nodes else (
            "partner already in a fixed group" if b in in_firm else "not reciprocated")
        soft.append({"from": a, "to": b, "reason": reason})

    free = sorted(signed - in_firm, key=str.lower)
    return {"firm_groups": firm, "soft_wishes": soft, "void_wishes": void, "free_players": free}


# ─────────────────────────────────────────────────────────────────────────────
#  HILFEN
# ─────────────────────────────────────────────────────────────────────────────

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_FAILS = {}             # ip -> [timestamps] fehlgeschlagener PIN-Versuche
_FAIL_LIMIT, _FAIL_WINDOW = 12, 600


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"


def _throttled(ip):
    now = time.time()
    hits = [t for t in _FAILS.get(ip, []) if now - t < _FAIL_WINDOW]
    _FAILS[ip] = hits
    return len(hits) >= _FAIL_LIMIT


def _note_fail(ip):
    _FAILS.setdefault(ip, []).append(time.time())


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def signup_is_open(champ_row, now=None):
    """signup_open muss true sein UND die Deadline (falls gesetzt) darf nicht vorbei sein."""
    if not champ_row or not champ_row.get("signup_open"):
        return False
    deadline = _parse_ts(champ_row.get("signup_deadline"))
    if deadline and (now or datetime.now(timezone.utc)) >= deadline:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  ROUTEN
# ─────────────────────────────────────────────────────────────────────────────

# ── Teamnamen ändern ("Show team") ───────────────────────────────────────────
TEAM_NAME_MIN, TEAM_NAME_MAX = 2, 24
TEAM_NAME_ADMIN_MAX = 40
_TEAM_NAME_RE = re.compile(r"[\w .'\-!&]+")
_RESERVED_TEAM_NAMES = {"unassigned"}      # so heisst der Sammel-Eintrag in den Standings


def clean_team_name(raw, max_len=TEAM_NAME_MAX):
    """Rueckgabe (name, None) oder (None, Fehlertext). Leerraum wird zusammengezogen."""
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if len(name) < TEAM_NAME_MIN:
        return None, f"Team name must be at least {TEAM_NAME_MIN} characters."
    if len(name) > max_len:
        return None, f"Team name can be at most {max_len} characters."
    if not _TEAM_NAME_RE.fullmatch(name) or not any(c.isalnum() for c in name):
        return None, "Team name may only contain letters, numbers, spaces and . ' - ! &"
    if name.lower() in _RESERVED_TEAM_NAMES:
        return None, "This name is not available."
    return name, None


def rename_lock(events, now=None):
    """Umbenennen ist bis zum Start des ersten Events moeglich. events: Zeilen mit start_at/status.
    Rueckgabe None (offen) oder Text, warum gesperrt."""
    now = now or datetime.now(timezone.utc)
    for e in events or []:
        if e.get("status") in (1, 2):
            return "The first event has started."
        t = _parse_ts(e.get("start_at"))
        if t is not None and t <= now:
            return "The first event has started."
    return None


def register_routes(app, h):
    """
    h: dict mit den Helfern aus admin_api.py:
       sb_get, sb_post, sb_patch, sb_delete, auth, err_detail
    """
    sb_get, sb_post, sb_patch, sb_delete = h["sb_get"], h["sb_post"], h["sb_patch"], h["sb_delete"]
    auth, err_detail = h["auth"], h["err_detail"]

    def q(v):
        return quote(str(v), safe="")

    def _who(body):
        """PIN → kanonischer Fahrername, oder (None, Antwort) bei Fehler."""
        ip = _client_ip()
        if _throttled(ip):
            return None, (jsonify({"error": "Too many wrong attempts — try again in a few minutes."}), 429)
        pin = str(body.get("pin") or "").strip()
        if not pin:
            return None, (jsonify({"error": "PIN required"}), 400)
        rows = sb_get("driver_pins", f"pin=eq.{q(pin)}&select=driver_name,pin")
        rows = [r for r in rows if hmac.compare_digest(str(r.get("pin", "")), pin)]
        if not rows:
            _note_fail(ip)
            return None, (jsonify({"error": "PIN not recognized"}), 404)
        if len(rows) > 1:
            return None, (jsonify({"error": "This PIN matches more than one driver — ask zephyr for a fresh PIN"}), 409)
        return rows[0]["driver_name"], None

    def _champ(body):
        cid = body.get("championship_id")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            return None, (jsonify({"error": "invalid championship_id"}), 400)
        rows = sb_get("championships", f"id=eq.{q(cid)}&select=id,club_id,signup_open,signup_deadline&limit=1")
        if not rows:
            return None, (jsonify({"error": "championship not found"}), 404)
        return rows[0], None

    def _is_registered(cid, name):
        return bool(sb_get("team_signups", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}&select=driver_name&limit=1"))

    def _status(cid, name, champ):
        wish = None
        rows = sb_get("team_wishes", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}&select=partner_name&limit=1")
        if rows:
            p = rows[0]["partner_name"]
            wish = {"partner_name": p, "partner_registered": _is_registered(cid, p)}
        return {
            "driver_name": name,
            "open": signup_is_open(champ),
            "deadline": champ.get("signup_deadline"),
            "registered": _is_registered(cid, name),
            "wish": wish,
        }

    def _rating(names):
        """{name: {status, stages}} - ELO oder Placement-Stand. Faellt bei Fehlern still auf None zurueck."""
        try:
            import placement_resolve as PR
            from team_draw import _load_overall
            return PR.rating_status(names, _load_overall(sb_get))
        except Exception:
            return None

    def _rating_for(name):
        r = _rating([name])
        info = None
        try:
            import placement_resolve as PR
            info = PR.placement_info()
        except Exception:
            pass
        return {**(r[name] if r else {"status": "unknown", "stages": None}), "placement": info}

    def _prepare(body, need_open):
        name, err = _who(body)
        if err:
            return None, None, None, err
        champ, err = _champ(body)
        if err:
            return None, None, None, err
        if need_open and not signup_is_open(champ):
            return None, None, None, (jsonify({"error": "Sign-up is closed for this championship."}), 403)
        return name, champ["id"], champ, None

    @app.route("/teamsignup/status", methods=["POST"])
    def teamsignup_status():
        try:
            body = request.json or {}
            name, cid, champ, err = _prepare(body, need_open=False)
            if err:
                return err
            st = _status(cid, name, champ)
            st["rating"] = _rating_for(name)
            return jsonify(st)
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/teamsignup/register", methods=["POST"])
    def teamsignup_register():
        try:
            body = request.json or {}
            name, cid, champ, err = _prepare(body, need_open=True)
            if err:
                return err
            if not _is_registered(cid, name):
                sb_post("team_signups", {"championship_id": cid, "driver_name": name})
            st = _status(cid, name, champ)
            st["rating"] = _rating_for(name)
            return jsonify(st)
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/teamsignup/unregister", methods=["POST"])
    def teamsignup_unregister():
        try:
            body = request.json or {}
            name, cid, champ, err = _prepare(body, need_open=True)
            if err:
                return err
            # Abmelden entfernt auch den eigenen Wunsch. Wünsche ANDERER auf diesen
            # Fahrer bleiben bewusst stehen — bei der Auswertung zählen sie nicht,
            # solange er abgemeldet ist, und gelten wieder, falls er sich neu anmeldet.
            sb_delete("team_wishes", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            sb_delete("team_signups", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            return jsonify(_status(cid, name, champ))
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/teamsignup/wish", methods=["POST"])
    def teamsignup_wish():
        try:
            body = request.json or {}
            name, cid, champ, err = _prepare(body, need_open=True)
            if err:
                return err
            if not _is_registered(cid, name):
                return jsonify({"error": "Sign up first, then choose a team mate."}), 403
            wanted = str(body.get("partner_name") or "").strip()
            if not wanted:
                return jsonify({"error": "partner_name required"}), 400
            rows = sb_get("drivers", f"name=ilike.{q(wanted)}&select=name&limit=5")
            match = [r["name"] for r in rows if r["name"].lower() == wanted.lower()]
            if not match:   # neue Fahrer ohne Event stehen nicht in drivers, wohl aber unter den Angemeldeten
                rows = sb_get("team_signups", f"championship_id=eq.{q(cid)}&driver_name=ilike.{q(wanted)}&select=driver_name&limit=5")
                match = [r["driver_name"] for r in rows if r["driver_name"].lower() == wanted.lower()]
            if not match:
                return jsonify({"error": "Driver not found."}), 404
            partner = match[0]
            if partner.lower() == name.lower():
                return jsonify({"error": "You can't pick yourself."}), 400
            sb_delete("team_wishes", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            sb_post("team_wishes", {"championship_id": cid, "driver_name": name, "partner_name": partner})
            return jsonify(_status(cid, name, champ))
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/teamsignup/wish/delete", methods=["POST"])
    def teamsignup_wish_delete():
        try:
            body = request.json or {}
            name, cid, champ, err = _prepare(body, need_open=True)
            if err:
                return err
            sb_delete("team_wishes", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            return jsonify(_status(cid, name, champ))
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    # ── Admin ────────────────────────────────────────────────────────────────


    # ── Teamnamen: "Show team" (PIN) + Umbenennen bis zum ersten Event ──────────
    def _my_team(cid, name):
        # team_members hat KEINE championship_id-Spalte (nur team_id, driver_name) -> ueber die Teams der Championship gehen
        teams = sb_get("teams", f"championship_id=eq.{q(cid)}&select=id,name,color")
        if not teams:
            return None
        ids = ",".join(q(t["id"]) for t in teams)
        mem = sb_get("team_members", f"driver_name=eq.{q(name)}&team_id=in.({ids})&select=team_id&limit=1")
        if not mem:
            return None
        tid = mem[0]["team_id"]
        team = next((t for t in teams if str(t["id"]) == str(tid)), None)
        if not team:
            return None
        members = sb_get("team_members", f"team_id=eq.{q(tid)}&select=driver_name&order=driver_name.asc")
        return {**team, "members": [m["driver_name"] for m in members]}

    def _lock_reason(cid):
        evs = sb_get("events", f"championship_id=eq.{q(cid)}&select=start_at,status")
        return rename_lock(evs)

    def _name_taken(cid, new, exclude_id):
        rows = sb_get("teams", f"championship_id=eq.{q(cid)}&select=id,name")
        return any(str(r["id"]) != str(exclude_id) and str(r["name"]).lower() == new.lower() for r in rows)

    @app.route("/teamname/show", methods=["POST"])
    def teamname_show():
        try:
            body = request.json or {}
            name, err = _who(body)
            if err:
                return err
            champ, err = _champ(body)
            if err:
                return err
            team = _my_team(champ["id"], name)
            lock = _lock_reason(champ["id"]) if team else None
            return jsonify({"driver_name": name, "team": team, "can_rename": bool(team) and lock is None,
                            "lock_reason": lock, "min": TEAM_NAME_MIN, "max": TEAM_NAME_MAX})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/teamname/rename", methods=["POST"])
    def teamname_rename():
        try:
            body = request.json or {}
            name, err = _who(body)
            if err:
                return err
            champ, err = _champ(body)
            if err:
                return err
            cid = champ["id"]
            team = _my_team(cid, name)
            if not team:
                return jsonify({"error": "You are not in a team of this championship."}), 403
            lock = _lock_reason(cid)
            if lock:
                return jsonify({"error": f"Team names can no longer be changed. {lock}"}), 403
            new, msg = clean_team_name(body.get("name"))
            if msg:
                return jsonify({"error": msg}), 400
            if _name_taken(cid, new, team["id"]):
                return jsonify({"error": "Another team already uses this name."}), 409
            sb_patch("teams", f"id=eq.{q(team['id'])}", {"name": new})
            team["name"] = new
            return jsonify({"driver_name": name, "team": team, "can_rename": True, "lock_reason": None,
                            "min": TEAM_NAME_MIN, "max": TEAM_NAME_MAX})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/team-name/set", methods=["POST"])
    @auth
    def admin_team_name_set():
        """Admin ueberschreibt einen Teamnamen (ignoriert die Sperre ab dem ersten Event)."""
        try:
            body = request.json or {}
            cid, tid = body.get("championship_id"), str(body.get("team_id") or "")
            if not isinstance(cid, str) or not _ID_RE.match(cid) or not tid:
                return jsonify({"error": "championship_id and team_id required"}), 400
            rows = sb_get("teams", f"id=eq.{q(tid)}&championship_id=eq.{q(cid)}&select=id&limit=1")
            if not rows:
                return jsonify({"error": "team not found"}), 404
            new, msg = clean_team_name(body.get("name"), TEAM_NAME_ADMIN_MAX)
            if msg:
                return jsonify({"error": msg}), 400
            if _name_taken(cid, new, tid):
                return jsonify({"error": "Another team already uses this name."}), 409
            sb_patch("teams", f"id=eq.{q(tid)}", {"name": new})
            return jsonify({"ok": True, "name": new})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500


    # ── Team-Reveal: Serverzeit (alle Besucher sehen den Reveal zur selben Zeit) + Zeitpunkt setzen ──
    @app.route("/teamreveal/now", methods=["GET"])
    def teamreveal_now():
        return jsonify({"now_ms": int(time.time() * 1000)})

    @app.route("/admin/team-reveal/set", methods=["POST"])
    @auth
    def admin_team_reveal_set():
        """Setzt (oder loescht mit reveal_at=null) den Reveal-Zeitpunkt der Championship."""
        try:
            body = request.json or {}
            cid = body.get("championship_id")
            if not isinstance(cid, str) or not _ID_RE.match(cid):
                return jsonify({"error": "invalid championship_id"}), 400
            raw = body.get("reveal_at")
            if raw in (None, ""):
                value = None
            else:
                ts = _parse_ts(raw)
                if ts is None:
                    return jsonify({"error": "reveal_at: invalid date"}), 400
                value = ts.isoformat()
            try:
                sb_patch("championships", f"id=eq.{q(cid)}", {"reveal_at": value})
            except Exception as e:
                return jsonify({"error": f"{err_detail(e)} - team_reveal.sql in Supabase ausgefuehrt?"}), 500
            return jsonify({"ok": True, "reveal_at": value})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/team-signup/config", methods=["POST"])
    @auth
    def admin_team_signup_config():
        """Body: {championship_id, signup_open: bool, signup_deadline: ISO-String|null, team_size_pref?: 2..8}"""
        try:
            body = request.json or {}
            cid = body.get("championship_id")
            if not isinstance(cid, str) or not _ID_RE.match(cid):
                return jsonify({"error": "invalid championship_id"}), 400
            deadline = body.get("signup_deadline")
            if deadline in ("", None):
                deadline = None
            elif _parse_ts(deadline) is None:
                return jsonify({"error": "invalid signup_deadline"}), 400
            patch = {"signup_open": bool(body.get("signup_open")), "signup_deadline": deadline}
            if body.get("team_size_pref") not in (None, ""):
                patch["team_size_pref"] = clamp_team_size(body.get("team_size_pref"))
            sb_patch("championships", f"id=eq.{q(cid)}", patch)
            return jsonify({"ok": True, "team_size_pref": patch.get("team_size_pref")})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/team-signup/overview", methods=["GET"])
    @auth
    def admin_team_signup_overview():
        try:
            cid = request.args.get("championship_id", "")
            if not _ID_RE.match(cid):
                return jsonify({"error": "invalid championship_id"}), 400
            try:
                champ = sb_get("championships", f"id=eq.{q(cid)}&select=id,signup_open,signup_deadline,team_size_pref&limit=1")
            except Exception:   # team_size.sql noch nicht gelaufen -> ohne die neue Spalte
                champ = sb_get("championships", f"id=eq.{q(cid)}&select=id,signup_open,signup_deadline&limit=1")
            if not champ:
                return jsonify({"error": "championship not found"}), 404
            pref = clamp_team_size(champ[0].get("team_size_pref"))
            signups = sb_get("team_signups", f"championship_id=eq.{q(cid)}&select=driver_name,signed_up_at&order=signed_up_at.asc")
            wishes = sb_get("team_wishes", f"championship_id=eq.{q(cid)}&select=driver_name,partner_name")
            analysis = analyze_wishes([s["driver_name"] for s in signups],
                                      {w["driver_name"]: w["partner_name"] for w in wishes},
                                      max_group=pref + 1)
            rating = _rating([s["driver_name"] for s in signups]) if signups else {}
            if rating is not None:
                for s_ in signups:
                    s_["rating"] = rating.get(s_["driver_name"])
            return jsonify({
                "rating_ok": rating is not None,
                "signup_open": bool(champ[0].get("signup_open")),
                "signup_deadline": champ[0].get("signup_deadline"),
                "open_now": signup_is_open(champ[0]),
                "team_size_pref": pref,
                "max_group": pref + 1,
                "signups": signups,
                "wishes": wishes,
                "analysis": analysis,
            })
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500

    @app.route("/admin/team-signup/remove", methods=["POST"])
    @auth
    def admin_team_signup_remove():
        """Entfernt Anmeldung + eigenen Wunsch eines Fahrers (z. B. wenn jemand absagt)."""
        try:
            body = request.json or {}
            cid, name = body.get("championship_id"), str(body.get("driver_name") or "").strip()
            if not isinstance(cid, str) or not _ID_RE.match(cid) or not name:
                return jsonify({"error": "championship_id and driver_name required"}), 400
            sb_delete("team_wishes", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            sb_delete("team_signups", f"championship_id=eq.{q(cid)}&driver_name=eq.{q(name)}")
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": err_detail(e)}), 500
