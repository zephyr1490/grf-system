"""
RallyLab — Racenet API Client
════════════════════════════════════════════════════════════════════════════════
Mittelsmann zwischen RallyLab-Tools und der Racenet API.
Wird von wrc_points.py, ELO-Rechner und anderen Tools importiert.

Funktionen:
  - Login via manuellem Refresh Token (einmalig aus Browser kopieren)
  - Auto-Refresh wenn Access Token abläuft (via Refresh Token)
  - Refresh Token verlängert sich automatisch bei jedem Refresh
  - Wenn Refresh Token abläuft → Anleitung zum erneuten Kopieren ausgeben
  - Token-Persistenz in racenet_tokens.json
  - Alle aktiven Clubs des Dummy-Accounts laden
  - Club-Details, Championships, Events, Stages
  - Stage-Ergebnisse und Championship-Punkte
  - Vergangene Championships
  - Stage-Sektor-Informationen (Anzahl, Zeiten, Distanzen)

Konfiguration:
  racenet_tokens.json  →  Tokens (manuell eingerichtet, dann automatisch verwaltet)

Einrichtung (einmalig, oder wenn Refresh Token abläuft):
  python racenet_client.py setup
  → Im Browser bei racenet.com einloggen (inkl. 2FA)
  → DevTools → Application → Cookies → RACENET-REFRESH-TOKEN kopieren
  → Token eingeben → wird in racenet_tokens.json gespeichert
  → Danach läuft alles automatisch

Abhängigkeiten (einmalig installieren):
  pip install requests

Verwendung:
  from racenet_client import RacenetClient
  client = RacenetClient()
  clubs = client.get_active_clubs()
════════════════════════════════════════════════════════════════════════════════

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PROJEKTSTATUS & NÄCHSTES ZIEL (für Claude-Kontext)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ZIEL:
  Einmalig eine komplette Excel-Datei aller WRC-Locations und Stages erstellen,
  mit allen verfügbaren Informationen pro Stage:
    - Location (Name, ID)
    - Stage/Route (Name, ID)
    - Surface Condition (Dry/Wet)
    - Streckenlänge (maxDistance aus ghost-Response, in Metern)
    - Sektoranzahl
    - Fahrzeugklassen für die diese Stage verfügbar ist

BEKANNTE API-ENDPUNKTE:
  GET /api/wrc2023Stats/values
    → Liefert Lookup-Tabellen: locations {id: name}, routes {id: name},
      vehicleClasses, surfaceConditions, locationRoute {location_id: [route_ids]}
    → locationRoute ist der Schlüssel: zeigt welche Routen zu welcher Location gehören
    → Daraus kann man alle Location+Route Kombinationen aufbauen

  GET /api/wrc2023Stats/performanceAnalysis/ghost
      ?WrcRivalLeaderboardId=TIME_TRIAL_R{route_id}_VC{class_id}_DRY
      &WrcRivalPlayerId={player_id}
    → Liefert in data.rival.performanceAnalysisMetadata:
        sectorTimes   (8) ["01:17:322", ...]   → Anzahl = Sektoranzahl
        sectorMillis  (8) [77200, ...]          → kumulierte ms pro Sektor
        sectorIndex   (8) [386, ...]            → Telemetrie-Indizes
        lapTime       "09:56:315"
        vehicleClassId, vehicleId
    → Liefert in data.rival.maxDistance: Streckenlänge in Metern
    → ACHTUNG: Braucht eine gültige player_id (ssid) eines Fahrers der diese
      Stage gefahren ist. Diese aus get_public_leaderboard holen.

  GET /api/wrc2023Stats/leaderboard/{route_id}/{class_id}/0
    → Öffentliches Leaderboard einer Stage+Klasse
    → entries[x]["ssid"] = player_id die man für ghost braucht

  Leaderboard-ID Format: TIME_TRIAL_R{route_id}_VC{class_id}_{DRY|WET}

IMPLEMENTIERTE METHODEN FÜR DAS EXCEL-ZIEL:
  client.get_stage_sector_info(leaderboard_id, player_id)
    → dict mit sector_count, sector_times, sector_millis, lap_time, ...
  client.get_stage_sector_count(leaderboard_id, player_id)
    → int
  client.get_public_leaderboard(route_id, class_id, max_results=1)
    → reicht um eine player_id zu bekommen (nur 1 Eintrag nötig)

NOCH ZU BAUEN:
  - Skript das values-Endpoint abruft, alle Location+Route Kombinationen
    durchiteriert, pro Route die Sektoranzahl + Länge holt und alles als
    Excel speichert. Surface Dry/Wet verdoppelt die Einträge.
  - Achtung: Nicht alle Route+Class Kombinationen existieren → Fehlerbehandlung
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import json
import time
import base64
import os
from datetime import datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────────────────────
#  PFADE
# ─────────────────────────────────────────────────────────────────────────────

_DIR        = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE  = os.path.join(_DIR, "racenet_tokens.json")

# Rückwärtskompatibilität: alte club_ids aus racenet_config.json werden übernommen
_LEGACY_CONFIG = os.path.join(_DIR, "racenet_config.json")

# ─────────────────────────────────────────────────────────────────────────────
#  KONSTANTEN
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL     = "https://web-api.racenet.com"
REFRESH_URL  = "https://web-api.racenet.com/api/identity/refresh-auth"
CLIENT_ID    = "RACENET_1_JS_WEB_APP"
REDIRECT_URI = "https://racenet.com/oauthCallback"
ORIGIN       = "https://racenet.com"

HEADERS_BASE = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
    "Origin":          ORIGIN,
    "Referer":         ORIGIN + "/",
    "DNT":             "1",
}

PAGE_SIZE = 20  # Racenet API Maximum pro Request


# ─────────────────────────────────────────────────────────────────────────────
#  HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────────────────

def _decode_jwt_expiry(token: str) -> int:
    """Liest exp-Feld aus JWT ohne Signatur-Prüfung."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp", 0)
    except Exception:
        return int(time.time()) + 3600


def _load_json(filepath: str) -> dict:
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(filepath: str, data: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
#  TOKEN MANAGER
# ─────────────────────────────────────────────────────────────────────────────

def _supabase_get_refresh_token() -> str:
    """
    Liest den zuletzt gespeicherten Refresh Token aus Supabase system_config.
    Gibt leeren String zurück wenn nicht vorhanden oder Fehler.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return ""
    try:
        r = requests.get(
            f"{url}/rest/v1/system_config?key=eq.racenet_refresh_token&select=value",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=5,
        )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                token = rows[0].get("value", "").strip()
                if token:
                    print("  ☁️  Refresh Token aus Supabase geladen.")
                    return token
    except Exception as e:
        print(f"  ⚠  Supabase Token-Lesen fehlgeschlagen: {e}")
    return ""


def _supabase_save_refresh_token(token: str):
    """
    Speichert den aktuellen Refresh Token in Supabase system_config.
    Upsert — erstellt oder aktualisiert den Eintrag.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key or not token:
        return
    try:
        r = requests.post(
            f"{url}/rest/v1/system_config",
            headers={
                "apikey":        key,
                "Authorization": f"Bearer {key}",
                "Content-Type":  "application/json",
                "Prefer":        "resolution=merge-duplicates",
            },
            json={"key": "racenet_refresh_token", "value": token},
            timeout=5,
        )
        if r.status_code in (200, 201):
            print("  ☁️  Refresh Token in Supabase gesichert.")
        else:
            print(f"  ⚠  Supabase Token-Speichern fehlgeschlagen: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠  Supabase Token-Speichern fehlgeschlagen: {e}")


def _bootstrap_from_env():
    """
    Railway/Cloud: Stellt sicher dass ein gültiger Refresh Token in der
    Token-Datei steht.

    Reihenfolge (wichtig — RaceNet rotiert den Token bei jedem Refresh):
      1. Supabase system_config → hat immer den zuletzt rotierten Token
      2. RACENET_REFRESH_TOKEN Env-Variable → Fallback (erster Start / manueller Reset)

    RACENET_TOKEN_RESET=1 → erzwingt Überschreiben mit Env-Variable
    (nach Token-Ablauf setzen, dann wieder entfernen)
    """
    rt_env = os.environ.get("RACENET_REFRESH_TOKEN", "").strip()
    if not rt_env:
        return  # lokale Entwicklung — normale Datei-basierte Logik

    force_reset = os.environ.get("RACENET_TOKEN_RESET", "").strip() == "1"

    # Zuerst Supabase prüfen — hat den zuletzt rotierten Token
    rt_supabase = _supabase_get_refresh_token() if not force_reset else ""

    if rt_supabase:
        # Supabase-Token ist aktueller als Env-Variable → verwenden
        existing = _load_json(TOKEN_FILE)
        existing["refresh_token"] = rt_supabase
        existing.pop("access_token", None)
        existing.pop("token_expiry", None)
        _save_json(TOKEN_FILE, existing)
        print("  ✅ Refresh Token aus Supabase übernommen.")
        return

    # Kein Supabase-Token → Env-Variable verwenden (erster Start)
    existing = _load_json(TOKEN_FILE)
    if existing.get("refresh_token") and not force_reset:
        return  # Datei hat bereits einen Token → nicht anfassen

    print("  🌐 Railway: Schreibe Refresh Token aus Env-Variable in Token-Datei...")
    existing["refresh_token"] = rt_env
    existing.pop("access_token", None)
    existing.pop("token_expiry", None)
    _save_json(TOKEN_FILE, existing)
    print("  ✅ Refresh Token geschrieben.")


class _TokenManager:
    """
    Verwaltet Access Token + Refresh Token.

    Reihenfolge:
      1. Gespeicherter Access Token noch gültig → direkt verwenden
      2. Access Token abgelaufen → Refresh via Refresh Token (schnell, kein Browser)
      3. Refresh Token abgelaufen → Anleitung ausgeben + RuntimeError

    Refresh Token verlängert sich automatisch bei jedem erfolgreichen Refresh.
    Solange das Programm regelmäßig läuft, ist kein manueller Eingriff nötig.

    Einrichtung (einmalig):
      python racenet_client.py setup
    """

    def __init__(self):
        self.access_token  = None
        self.refresh_token = None
        self.token_expiry  = 0
        self._load_tokens()

    def _load_tokens(self):
        _bootstrap_from_env()  # Env-Variable immer zuerst prüfen
        data = _load_json(TOKEN_FILE)
        self.access_token  = data.get("access_token")
        self.refresh_token = data.get("refresh_token", "")
        self.token_expiry  = data.get("token_expiry", 0)

        if self.access_token:
            remaining = int(self.token_expiry - time.time())
            exp_str   = datetime.fromtimestamp(self.token_expiry).strftime("%H:%M:%S")
            if remaining > 60:
                print(f"  📂 Tokens geladen — Access gültig bis {exp_str} (noch {remaining // 60} min)")
            else:
                print(f"  📂 Tokens geladen — Access ABGELAUFEN (bis {exp_str})")
        elif self.refresh_token:
            print(f"  📂 Nur Refresh Token gefunden — erneuere Access Token...")
        else:
            print(f"  ⚠  Keine Tokens gefunden. Bitte 'python racenet_client.py setup' ausführen.")

    def _save_tokens(self):
        _save_json(TOKEN_FILE, {
            "access_token":  self.access_token,
            "refresh_token": self.refresh_token,
            "token_expiry":  self.token_expiry,
            "saved_at":      datetime.now().isoformat(),
        })
        # Refresh Token auch in Supabase persistieren — überlebt Container-Neustarts
        if self.refresh_token:
            _supabase_save_refresh_token(self.refresh_token)

    def _is_expired(self) -> bool:
        return time.time() >= (self.token_expiry - 60)

    # ── Refresh via Refresh Token ─────────────────────────────────────────────

    def _refresh(self) -> bool:
        """
        Holt neuen Access Token via Refresh Token Cookie.
        Speichert auch den neuen Refresh Token falls die API einen zurückgibt
        (verlängert die Session automatisch bei regelmäßiger Nutzung).
        """
        if not self.refresh_token:
            return False

        print("  🔄 Versuche Token-Refresh...")
        cookie_str = (
            "notice_preferences=0:; notice_gdpr_prefs=0:; "
            f"RACENET-REFRESH-TOKEN={self.refresh_token}; "
            "notice_behavior=implied,eu; notice_location=at"
        )
        headers = {
            **HEADERS_BASE,
            "Content-Type": "application/json",
            "Cookie":       cookie_str,
        }
        body = {
            "clientId":     CLIENT_ID,
            "grantType":    "refresh_token",
            "refreshToken": "",
            "redirectUri":  REDIRECT_URI,
            "authCode":     "",
            "codeVerifier": "",
        }
        try:
            r = requests.post(REFRESH_URL, headers=headers, json=body, timeout=15)
            if r.status_code == 200:
                data    = r.json()
                token   = data.get("access_token") or data.get("accessToken")
                new_rt  = data.get("refresh_token") or data.get("refreshToken")
                if token:
                    self.access_token = token
                    self.token_expiry = _decode_jwt_expiry(token)
                    if new_rt:
                        self.refresh_token = new_rt  # automatisch verlängert
                    self._save_tokens()
                    exp = datetime.fromtimestamp(self.token_expiry).strftime("%H:%M:%S")
                    print(f"  ✅ Token erneuert — gültig bis {exp}")
                    return True
            print(f"  ⚠ Refresh fehlgeschlagen (HTTP {r.status_code})")
            if r.status_code in (401, 403):
                self._print_setup_help()
            return False
        except Exception as e:
            print(f"  ⚠ Refresh-Fehler: {e}")
            return False

    def _print_setup_help(self):
        print()
        print("  ════════════════════════════════════════════════════════════")
        print("  ❌  Refresh Token abgelaufen — einmalige Erneuerung nötig")
        print("  ════════════════════════════════════════════════════════════")
        print()
        print("  1. Browser öffnen → https://racenet.com")
        print("  2. Einloggen (manuell, inkl. 2FA falls nötig)")
        print("  3. DevTools öffnen (F12)")
        print("  4. Application → Cookies → https://racenet.com")
        print("  5. Cookie 'RACENET-REFRESH-TOKEN' kopieren")
        print("  6. Ausführen:  python racenet_client.py setup")
        print()

    # ── Öffentliche Methode ───────────────────────────────────────────────────

    def get_token(self) -> str | None:
        """
        Gibt gültigen Access Token zurück.
        Reihenfolge: gespeicherter Token → Refresh via Refresh Token
        """
        if self.access_token and not self._is_expired():
            return self.access_token

        if self.refresh_token and self._refresh():
            return self.access_token

        self._print_setup_help()
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  RACENET CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class RacenetClient:
    """
    Haupt-Client für die Racenet API.

    Verwendung:
        client = RacenetClient()   # lädt Tokens aus racenet_tokens.json

    Einrichtung (einmalig):
        python racenet_client.py setup

    Alle Methoden geben Python-Dicts zurück (direkt aus JSON).
    Bei Fehler wird eine Exception geworfen.
    """

    def __init__(self, email: str = None, password: str = None):
        # email/password für Rückwärtskompatibilität akzeptiert, aber nicht mehr verwendet
        if email or password:
            print("  ⚠  Email/Passwort werden nicht mehr verwendet.")
            print("     Login erfolgt jetzt via Refresh Token.")
            print("     Bitte 'python racenet_client.py setup' ausführen falls noch nicht geschehen.")

        self._tm      = _TokenManager()
        self._session = requests.Session()

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        token = self._tm.get_token()
        if not token:
            raise RuntimeError("Kein gültiger Access Token — Login fehlgeschlagen.")
        return {**HEADERS_BASE, "authorization": f"Bearer {token}"}

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = BASE_URL + endpoint
        r   = self._session.get(url, headers=self._headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    # ── Identity ──────────────────────────────────────────────────────────────

    def test_auth(self) -> dict:
        """Prüft Auth — gibt Account-Info zurück."""
        return self._get("/api/identity/secured")

    # ── Clubs ─────────────────────────────────────────────────────────────────

    def get_active_clubs(self) -> list[dict]:
        """
        Gibt alle Club-Daten zurück.
        Versucht zuerst den active-Endpoint (funktioniert wenn mindestens ein Club aktiv ist).
        Neue Club-IDs werden automatisch in racenet_tokens.json gespeichert.
        Fallback: gespeicherte club_ids aus racenet_tokens.json (oder racenet_config.json).
        """
        cfg   = _load_json(TOKEN_FILE)
        known = cfg.get("club_ids", [])

        # Rückwärtskompatibilität: club_ids aus altem racenet_config.json übernehmen
        if not known and os.path.exists(_LEGACY_CONFIG):
            legacy = _load_json(_LEGACY_CONFIG)
            known  = legacy.get("club_ids", [])

        # Versuch 1: memberships/active — OHNE params (take>20 gibt HTTP 400)
        try:
            all_memberships = []
            data  = self._get("/api/wrc2023clubs/memberships/active")
            total = int(data.get("totalActiveMemberships", 0))
            all_memberships.extend(data.get("activeMemberships", []))
            # Paginieren falls nötig (>20 Clubs)
            while len(all_memberships) < total:
                page  = self._get("/api/wrc2023clubs/memberships/active",
                                  params={"take": 20, "skip": len(all_memberships)})
                batch = page.get("activeMemberships", [])
                if not batch:
                    break
                all_memberships.extend(batch)
            memberships = all_memberships
            print(f"  📡 {len(memberships)}/{total} Memberships geladen")
            if memberships:
                # Club-IDs extrahieren
                found_ids = [m.get("clubID") or m.get("clubId") or m.get("id") for m in memberships]
                found_ids = [x for x in found_ids if x]
                merged    = list(dict.fromkeys(known + found_ids))  # dedup, Reihenfolge erhalten
                if merged != known:
                    cfg["club_ids"] = merged
                    _save_json(TOKEN_FILE, cfg)
                    new_clubs = [cid for cid in found_ids if cid not in known]
                    if new_clubs:
                        print(f"  🆕 Neue Clubs gefunden und gespeichert: {new_clubs}")
                # Vollständige Club-Daten laden
                clubs = []
                for m in memberships:
                    try:
                        clubs.append(self.get_club(m["clubID"]))
                        time.sleep(0.2)
                    except Exception as e:
                        print(f"  ⚠ Club {m['clubID']}: {e}")
                # Auch bekannte inaktive Clubs laden
                for club_id in known:
                    if club_id not in found_ids:
                        try:
                            clubs.append(self.get_club(club_id))
                            time.sleep(0.2)
                        except Exception as e:
                            print(f"  ⚠ Club {club_id}: {e}")
                return clubs
        except Exception:
            pass  # Fallback

        # Fallback: gespeicherte IDs
        clubs = []
        for club_id in known:
            try:
                clubs.append(self.get_club(club_id))
                time.sleep(0.2)
            except Exception as e:
                print(f"  ⚠ Club {club_id}: {e}")
        return clubs

    def get_club(self, club_id: str) -> dict:
        """
        Vollständige Club-Daten inkl. aktuelle Championship mit allen Events + Stages.
        Enthält auch alle vergangenen championshipIDs.
        """
        return self._get(f"/api/wrc2023clubs/{club_id}", params={"includeChampionship": "true"})

    # ── Championships ─────────────────────────────────────────────────────────

    def get_championship(self, club_id: str, championship_id: str) -> dict:
        """
        Lädt eine spezifische Championship (auch vergangene).
        Enthält alle Events + Stages mit leaderboardIDs.

        WICHTIG: Vergangene Championships sind NICHT über
        /api/wrc2023clubs/{club_id}/championship/{id} erreichbar (404).
        Der korrekte Endpoint ist /api/wrc2023clubs/championships/{id}
        (ohne Club-ID in der URL).
        """
        club    = self._get(f"/api/wrc2023clubs/{club_id}", params={"includeChampionship": "true"})
        current = club.get("currentChampionship", {})
        if current.get("id") == championship_id:
            return current
        return self._get(f"/api/wrc2023clubs/championships/{championship_id}")

    def get_all_championship_ids(self, club_id: str) -> list[str]:
        """Gibt alle Championship-IDs eines Clubs zurück (aktuell + vergangene)."""
        club = self.get_club(club_id)
        return club.get("championshipIDs", [])

    def get_championship_standings(self, club_id: str, championship_id: str,
                                    max_results: int = 100) -> list[dict]:
        """
        Championship-Gesamtpunktestand.
        Gibt Liste von {ssid, displayName, rank, pointsAccumulated} zurück.
        """
        entries = []
        cursor  = None
        while len(entries) < max_results:
            params = {"ResultCount": PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            data  = self._get(
                f"/api/wrc2023clubs/{club_id}/championship/points/{championship_id}",
                params=params,
            )
            batch  = data.get("entries", [])
            entries.extend(batch)
            cursor  = data.get("cursorNext")
            if not cursor or not batch:
                break
            time.sleep(0.2)
        return entries[:max_results]

    # ── Events & Stages ───────────────────────────────────────────────────────

    def get_event_leaderboard(self, club_id: str, leaderboard_id: str,
                               max_results: int = 200) -> list[dict]:
        """
        Ergebnisse eines Events oder einer Stage.
        Gibt Liste von {displayName, rank, time, vehicle, points, platform, assists, ...} zurück.
        """
        entries = []
        cursor  = None
        while len(entries) < max_results:
            params = {
                "MaxResultCount": PAGE_SIZE,
                "FocusOnMe":      "false",
                "Platform":       0,
                "SortCumulative": "false",
            }
            if cursor:
                params["cursor"] = cursor
            data   = self._get(
                f"/api/wrc2023clubs/{club_id}/leaderboard/{leaderboard_id}",
                params=params,
            )
            batch  = data.get("entries", [])
            entries.extend(batch)
            cursor  = data.get("next")
            if not cursor or not batch:
                break
            time.sleep(0.2)
        return entries[:max_results]

    def get_stage_results(self, club_id: str, leaderboard_id: str) -> list[dict]:
        """Alias für get_event_leaderboard — semantisch klarer für Stage-Ergebnisse."""
        return self.get_event_leaderboard(club_id, leaderboard_id)

    # ── Stage-Info (Sektoren) ─────────────────────────────────────────────────

    def get_stage_sector_info(self, leaderboard_id: str, player_id: str) -> dict:
        """
        Lädt Sektor-Informationen einer Stage via Performance Analysis Ghost-Endpoint.

        Parameter:
          leaderboard_id  z.B. "TIME_TRIAL_R252_VC19_DRY"
          player_id       WRC Player-ID eines beliebigen Fahrers auf dieser Stage
                          (z.B. aus get_public_leaderboard entries[x]["ssid"])

        Rückgabe:
        {
            "sector_count":  int,          # Anzahl Sektoren (z.B. 8)
            "sector_times":  [str, ...],   # Zeiten als Strings ("01:17:322", ...)
            "sector_millis": [int, ...],   # Kumulierte Millisekunden pro Sektor
            "sector_index":  [int, ...],   # Telemetrie-Indizes der Sektorgrenzen
            "lap_time":      str,          # Gesamtzeit ("09:56:315")
            "vehicle_class_id": int,
            "vehicle_id":    int,
        }

        Wirft ValueError wenn keine Metadaten gefunden.
        """
        data = self._get(
            "/api/wrc2023Stats/performanceAnalysis/ghost",
            params={
                "WrcRivalLeaderboardId": leaderboard_id,
                "WrcRivalPlayerId":      player_id,
            },
        )
        rival    = data.get("data", {}).get("rival") or {}
        metadata = rival.get("performanceAnalysisMetadata")
        if not metadata:
            raise ValueError(
                f"Keine performanceAnalysisMetadata für {leaderboard_id} / {player_id}"
            )
        sector_times = metadata.get("sectorTimes", [])
        return {
            "sector_count":     len(sector_times),
            "sector_times":     sector_times,
            "sector_millis":    metadata.get("sectorMillis", []),
            "sector_index":     metadata.get("sectorIndex", []),
            "lap_time":         metadata.get("lapTime"),
            "vehicle_class_id": metadata.get("vehicleClassId"),
            "vehicle_id":       metadata.get("vehicleId"),
        }

    def get_stage_sector_count(self, leaderboard_id: str, player_id: str) -> int:
        """
        Gibt nur die Sektoranzahl einer Stage zurück.
        Kurzform von get_stage_sector_info()[\"sector_count\"].

        Beispiel:
            count = client.get_stage_sector_count("TIME_TRIAL_R252_VC19_DRY", "2LTnWCawBEwXvibLs")
            # → 8
        """
        return self.get_stage_sector_info(leaderboard_id, player_id)["sector_count"]

    # ── Öffentliche Leaderboards (für CR-Berechnung) ──────────────────────────

    def get_public_leaderboard(self, stage_id: int, class_id: int,
                                max_results: int = 100) -> list[dict]:
        """
        Öffentliches Leaderboard für eine Stage + Fahrzeugklasse.
        Für CR-Berechnung (via wrcsetups.com Scraper).
        """
        entries = []
        cursor  = None
        pages   = (max_results + PAGE_SIZE - 1) // PAGE_SIZE
        for _ in range(pages):
            if len(entries) >= max_results:
                break
            params = {
                "maxResultCount": PAGE_SIZE,
                "focusOnMe":      "false",
                "platform":       0,
            }
            if cursor:
                params["cursor"] = cursor
            data   = self._get(
                f"/api/wrc2023Stats/leaderboard/{stage_id}/{class_id}/0",
                params=params,
            )
            batch  = data.get("entries", [])
            entries.extend(batch)
            cursor  = data.get("next")
            if not cursor or not batch:
                break
            time.sleep(0.3)
        return entries[:max_results]

    def get_public_leaderboards_parallel(
        self,
        combos: list[tuple[int, int]],
        max_results: int = 99999,
        max_workers: int = 10,
        on_progress=None,
    ) -> dict[tuple[int, int], list[dict]]:
        """
        Lädt mehrere Stage+Klasse Leaderboards parallel.

        Args:
            combos:       Liste von (stage_id, class_id) Tupeln
            max_results:  Max Einträge pro Stage (default: alle)
            max_workers:  Parallele Threads (default: 10)
            on_progress:  Optionaler Callback(done, total, stage_id, class_id)
                          wird nach jeder fertigen Stage aufgerufen

        Returns:
            Dict {(stage_id, class_id): [entries]}
            Bei Fehler einer Stage: leere Liste für dieses Tupel

        Beispiel:
            combos = [(252, 21), (253, 21), (254, 21)]
            results = client.get_public_leaderboards_parallel(combos)
            entries_252 = results[(252, 21)]
        """
        import threading
        results = {}
        total   = len(combos)
        done    = 0
        lock    = threading.Lock()

        def _fetch(stage_id, class_id):
            try:
                return (stage_id, class_id), self.get_public_leaderboard(
                    stage_id, class_id, max_results=max_results
                )
            except Exception as e:
                print(f"  ⚠ Stage {stage_id}/{class_id}: {e}")
                return (stage_id, class_id), []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch, s, c): (s, c) for s, c in combos}
            for fut in as_completed(futures):
                key, entries = fut.result()
                results[key] = entries
                with lock:
                    done += 1
                if on_progress:
                    on_progress(done, total, key[0], key[1])

        return results

    # ── Convenience: Alle Daten einer aktiven Championship ────────────────────

    def get_full_championship_data(self, club_id: str,
                                    championship_id: str = None) -> dict:
        """
        Lädt alle Daten einer Championship komplett:
        - Championship-Info (Settings, Zeitraum)
        - Alle Events mit Stages
        - Pro Stage: Ergebnisse
        - Gesamtpunktestand

        Wenn championship_id=None → aktuelle Championship.

        Rückgabe:
        {
            "club_id":         str,
            "championship_id": str,
            "championship":    dict,   # Settings, Events, Stages
            "stage_results":   {leaderboard_id: [entries]},
            "standings":       [entries],
        }
        """
        club  = self.get_club(club_id)
        champ = club.get("currentChampionship", {})

        if championship_id and champ.get("id") != championship_id:
            try:
                champ = self.get_championship(club_id, championship_id)
            except Exception as ex:
                # WICHTIG: NICHT mehr stillschweigend auf die aktuelle Championship
                # zurückfallen — das hat zuvor dazu geführt, dass beim Versuch eine
                # vergangene Championship zu laden lautlos immer dieselbe (aktuelle)
                # Championship zurückkam, ohne erkennbaren Fehler. Lieber sichtbar
                # crashen, damit der eigentliche API-Fehler sichtbar wird.
                raise RuntimeError(
                    f"Konnte Championship {championship_id} für Club {club_id} nicht laden "
                    f"(get_championship fehlgeschlagen: {ex}). Fällt NICHT mehr automatisch "
                    f"auf die aktuelle Championship zurück."
                ) from ex

        if not champ:
            raise ValueError(f"Keine Championship gefunden für Club {club_id}")

        champ_id = champ.get("id", championship_id)
        events   = champ.get("events", [])

        stage_results = {}
        for event in events:
            # HINWEIS: status==0 wird NICHT mehr als Skip-Kriterium genutzt.
            # Bei abgeschlossenen/historischen Championships hatten 7 von 8
            # Events status==0, obwohl sie echte Stage-Ergebnisse hatten —
            # das Feld scheint dort nicht zuverlässig "noch nicht gestartet"
            # zu bedeuten. Stattdessen wird einfach versucht, jede Stage zu
            # laden; Events ohne echte Ergebnisse liefern dann schlicht eine
            # leere Liste zurück (kostet nur ein paar zusätzliche, harmlose
            # API-Calls für tatsächlich noch nicht gestartete Events).
            if not event.get("stages"):
                continue
            for stage in event.get("stages", []):
                lb_id = stage.get("leaderboardID")
                if lb_id:
                    try:
                        results = self.get_stage_results(club_id, lb_id)
                        stage_results[lb_id] = results
                    except Exception as e:
                        print(f"  ⚠ Stage {lb_id}: {e}")
                        stage_results[lb_id] = []

        standings = []
        try:
            standings = self.get_championship_standings(club_id, champ_id)
        except Exception as e:
            print(f"  ⚠ Standings: {e}")

        return {
            "club_id":         club_id,
            "championship_id": champ_id,
            "championship":    champ,
            "stage_results":   stage_results,
            "standings":       standings,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  SETUP — einmaliger Refresh Token Setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_token(refresh_token: str = None):
    """
    Einmaliger Setup: Refresh Token speichern und sofort ersten Access Token holen.

    So kommst du an den Refresh Token:
      1. Browser öffnen → https://racenet.com
      2. Einloggen (manuell, inkl. 2FA falls nötig)
      3. DevTools öffnen (F12)
      4. Application → Cookies → https://racenet.com
      5. Cookie 'RACENET-REFRESH-TOKEN' kopieren

    Danach läuft alles automatisch. Der Refresh Token verlängert sich
    bei jedem Programmstart selbst — kein erneuter Setup nötig solange
    das Programm regelmäßig läuft.
    """
    print("=" * 60)
    print("  RallyLab — Racenet Token Setup")
    print("=" * 60)
    print()
    print("  Anleitung:")
    print("  1. Browser öffnen → https://racenet.com")
    print("  2. Einloggen (manuell, inkl. 2FA falls nötig)")
    print("  3. DevTools öffnen (F12)")
    print("  4. Application → Cookies → https://racenet.com")
    print("  5. Cookie 'RACENET-REFRESH-TOKEN' kopieren")
    print()

    if not refresh_token:
        refresh_token = input("  Refresh Token einfügen: ").strip()

    if not refresh_token:
        print("  ❌ Kein Token eingegeben.")
        return

    print()
    print("  🔄 Teste Refresh Token...")
    cookie_str = (
        "notice_preferences=0:; notice_gdpr_prefs=0:; "
        f"RACENET-REFRESH-TOKEN={refresh_token}; "
        "notice_behavior=implied,eu; notice_location=at"
    )
    headers = {
        **HEADERS_BASE,
        "Content-Type": "application/json",
        "Cookie":       cookie_str,
    }
    body = {
        "clientId":     CLIENT_ID,
        "grantType":    "refresh_token",
        "refreshToken": "",
        "redirectUri":  REDIRECT_URI,
        "authCode":     "",
        "codeVerifier": "",
    }
    try:
        r = requests.post(REFRESH_URL, headers=headers, json=body, timeout=15)
        if r.status_code == 200:
            data    = r.json()
            token   = data.get("access_token") or data.get("accessToken")
            new_rt  = data.get("refresh_token") or data.get("refreshToken")
            if token:
                expiry = _decode_jwt_expiry(token)
                # Bestehende Datei laden um club_ids nicht zu überschreiben
                existing = _load_json(TOKEN_FILE)
                existing.update({
                    "access_token":  token,
                    "refresh_token": new_rt or refresh_token,
                    "token_expiry":  expiry,
                    "saved_at":      datetime.now().isoformat(),
                })
                _save_json(TOKEN_FILE, existing)
                exp_str = datetime.fromtimestamp(expiry).strftime("%H:%M:%S")
                print(f"  ✅ Erfolgreich! Access Token gültig bis {exp_str}")
                print(f"  💾 Gespeichert in: {TOKEN_FILE}")
                print()
                print("  Du kannst jetzt RallyLab normal starten.")
                return
            else:
                print(f"  ❌ Response ohne Token: {list(data.keys())}")
        else:
            print(f"  ❌ HTTP {r.status_code}: {r.text[:300]}")
    except Exception as e:
        print(f"  ❌ Fehler: {e}")

    print()
    print("  ❌ Setup fehlgeschlagen. Bitte Schritte 1–5 wiederholen.")


# Rückwärtskompatibilität
def setup(email: str = None, password: str = None):
    """Veraltet — bitte setup_token() verwenden."""
    print("  ⚠  setup() mit Email/Passwort wird nicht mehr unterstützt.")
    setup_token()


# ─────────────────────────────────────────────────────────────────────────────
#  QUICK TEST
# ─────────────────────────────────────────────────────────────────────────────

def _run_test():
    print("=" * 60)
    print("  RallyLab — Racenet Client Test")
    print("=" * 60)

    if not os.path.exists(TOKEN_FILE):
        print(f"\n⚠ Keine Token-Datei gefunden ({TOKEN_FILE})")
        setup_token()
        return

    client = RacenetClient()

    # 1. Auth
    print("\n── 1. Auth ──────────────────────────────────────────────")
    try:
        identity = client.test_auth()
        print(f"✅ Eingeloggt als: {identity.get('displayName')} ({identity.get('ssid')})")
    except Exception as e:
        print(f"❌ {e}")
        return

    # 2. Aktive Clubs
    print("\n── 2. Aktive Clubs ──────────────────────────────────────")
    try:
        clubs = client.get_active_clubs()
        for c in clubs:
            champ   = c.get("currentChampionship", {})
            active  = "✅" if champ.get("settings") else "⏸"
            name    = c.get("clubName", "?")
            cid     = c.get("clubID", "?")
            members = c.get("activeMemberCount", "?")
            champ_id = champ.get("id", "—")
            print(f"  {active} [{cid}] {name} — {members} Mitglieder | Championship: {champ_id}")
    except Exception as e:
        print(f"❌ {e}")

    # 3. Club-Details (H2FWD)
    print("\n── 3. Club H2FWD (23834) ────────────────────────────────")
    try:
        club   = client.get_club("23834")
        champ  = club.get("currentChampionship", {})
        events = champ.get("events", [])
        print(f"✅ {club['clubName']} — Championship: {champ.get('id')}")
        for ev in events:
            status_map = {0: "⏳ Offen", 1: "🟢 Aktiv", 2: "✅ Beendet"}
            status = status_map.get(ev.get("status", 0), "?")
            loc    = ev.get("eventSettings", {}).get("location", "?")
            stages = len(ev.get("stages", []))
            print(f"  {status} {loc} — {stages} Stages")
    except Exception as e:
        print(f"❌ {e}")

    # 4. Stage-Ergebnisse
    print("\n── 4. Stage-Ergebnisse (Hartje, Croatia) ───────────────")
    try:
        results = client.get_stage_results("23834", "gQegrAmxqzJHFamK")
        print(f"✅ {len(results)} Einträge")
        for r in results[:3]:
            print(f"  P{r['rank']:2} | {r['displayName']:<25} | {r['vehicle']:<25} | {r['time']}")
    except Exception as e:
        print(f"❌ {e}")

    # 5. Championship Standings
    print("\n── 5. Championship Standings ────────────────────────────")
    try:
        standings = client.get_championship_standings("23834", "2c5EuH3xYYKhwvnSA")
        print(f"✅ {len(standings)} Fahrer")
        for s in standings[:5]:
            print(f"  P{s['rank']:2} | {s['displayName']:<25} | {s['pointsAccumulated']} Pkt")
    except Exception as e:
        print(f"❌ {e}")

    print("\n" + "=" * 60)
    print("  Test abgeschlossen")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_token()
    else:
        _run_test()
