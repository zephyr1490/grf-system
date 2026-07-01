"""
elo_state.py
════════════════════════════════════════════════════════════════════════════
RallyLab — ELO State-Persistenz

Hält den kompletten Rating-Stand EINES Projekts/Profils fest:
  - Ratings pro Track pro Fahrer (overall, surface:gravel_soft, drivetrain:AWD, ...)
  - Welche Racenet-Event-IDs schon verarbeitet wurden (Delta-Update-Schutz)
  - Ob/wann die historische CSV-Basis geladen wurde (Lock, damit sie nie
    aus Versehen doppelt verarbeitet wird)

Mehrere Projekte = mehrere Profile = mehrere State-Dateien
(z.B. "elo_state_grfclub.json" und "elo_state_anderesprojekt.json"),
damit sich ein Racenet-Club-Profil und ein reines CSV-Projekt niemals vermischen.

KEINE Abhängigkeit von Tkinter/openpyxl — nur json + dataclasses.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import json
import os
from datetime import datetime, timezone

from elo_engine import Rating


# ─────────────────────────────────────────────────────────────────────────────
#  STATE-OBJEKT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EloState:
    profile_name: str

    # {track_name: {driver_name: Rating}}
    ratings: Dict[str, Dict[str, Rating]] = field(default_factory=dict)

    # Racenet Event-IDs, die schon in die Ratings eingerechnet wurden.
    processed_event_ids: List[str] = field(default_factory=list)

    # GRF-Pool Whitelist: nur diese Fahrer werden im "GRF Pool"-Sheet gezeigt
    # (beeinflusst NICHT die Berechnung — alle Teilnehmer fließen immer ein).
    pool_drivers: List[str] = field(default_factory=list)

    # {driver_id: zuletzt gesehener Anzeigename}. driver_id ist bei Racenet-
    # Quellen die stabile ssid ("ssid:12345"), Name dient nur der Anzeige in
    # Excel/GUI — so bleibt die Rating-Historie auch bei Namensänderungen
    # demselben Spieler zugeordnet.
    driver_labels: Dict[str, str] = field(default_factory=dict)

    # ── Hilfsfunktionen ─────────────────────────────────────────────────────

    # {driver_id: gesehene Clubs}. Zeigt, wie gut ein Fahrer über Clubs hinweg
    # "verbunden" ist — wer nur in einem Club auftaucht, dessen Cross-Club-
    # Einordnung im Bradley-Terry-Fit ist unsicherer als sein sigma (das nur
    # die Event-Anzahl zählt) vermuten lässt.
    driver_clubs: Dict[str, List[str]] = field(default_factory=dict)

    # Inaktivitäts-Tracking: {driver_id: globaler Event-Zähler beim letzten Auftauchen}
    # Nach INACTIVITY_THRESHOLD verpassten Events wird ein Fahrer als inaktiv markiert.
    driver_last_seen: Dict[str, int] = field(default_factory=dict)
    driver_inactive: Dict[str, bool] = field(default_factory=dict)
    total_events_processed: int = 0   # globaler Zähler über alle Events (Basis für Inaktivität)
    inactivity_threshold: int = 20    # verpasste Events bis zur Inaktivierung

    def add_driver_club(self, driver_id: str, club_id: str) -> None:
        clubs = self.driver_clubs.setdefault(driver_id, [])
        if club_id not in clubs:
            clubs.append(club_id)

    def mark_driver_seen(self, driver_id: str) -> None:
        """Markiert einen Fahrer als aktiv beim aktuellen Event-Zählerstand."""
        self.driver_last_seen[driver_id] = self.total_events_processed
        self.driver_inactive[driver_id] = False

    def update_inactivity(self) -> List[str]:
        """
        Prüft nach jedem verarbeiteten Event alle bekannten Fahrer auf Inaktivität.
        Gibt Liste der neu inaktiv markierten Fahrer zurück (für Logging).
        """
        newly_inactive = []
        for driver_id in list(self.driver_last_seen.keys()):
            missed = self.total_events_processed - self.driver_last_seen.get(driver_id, 0)
            was_inactive = self.driver_inactive.get(driver_id, False)
            if missed >= self.inactivity_threshold and not was_inactive:
                self.driver_inactive[driver_id] = True
                newly_inactive.append(driver_id)
        return newly_inactive

    def is_inactive(self, driver_id: str) -> bool:
        return self.driver_inactive.get(driver_id, False)

    def connectivity(self, driver_id: str) -> int:
        """Anzahl unterschiedlicher Clubs, in denen dieser Fahrer aufgetaucht ist."""
        return len(self.driver_clubs.get(driver_id, []))

    def label(self, driver_id: str) -> str:
        """Anzeigename für eine driver_id — fällt auf die ID selbst zurück, falls unbekannt."""
        return self.driver_labels.get(driver_id, driver_id)

    def update_label(self, driver_id: str, display_name: str) -> None:
        if display_name:
            self.driver_labels[driver_id] = display_name

    def get_rating(self, track: str, driver: str) -> Rating:
        track_dict = self.ratings.setdefault(track, {})
        return track_dict.setdefault(driver, Rating())

    def all_tracks(self) -> List[str]:
        return sorted(self.ratings.keys())

    def drivers_in_track(self, track: str) -> List[str]:
        return sorted(self.ratings.get(track, {}).keys())

    def is_event_processed(self, event_id: str) -> bool:
        return event_id in self.processed_event_ids

    def mark_event_processed(self, event_id: str) -> None:
        if event_id not in self.processed_event_ids:
            self.processed_event_ids.append(event_id)

    # ── Serialisierung ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "ratings": {
                track: {driver: r.to_dict() for driver, r in drivers.items()}
                for track, drivers in self.ratings.items()
            },
            "processed_event_ids": self.processed_event_ids,
            "pool_drivers": self.pool_drivers,
            "driver_labels": self.driver_labels,
            "driver_clubs": self.driver_clubs,
            "driver_last_seen": self.driver_last_seen,
            "driver_inactive": self.driver_inactive,
            "total_events_processed": self.total_events_processed,
            "inactivity_threshold": self.inactivity_threshold,
        }

    @staticmethod
    def from_dict(d: dict) -> "EloState":
        st = EloState(profile_name=d.get("profile_name", "default"))
        st.ratings = {
            track: {driver: Rating.from_dict(rd) for driver, rd in drivers.items()}
            for track, drivers in d.get("ratings", {}).items()
        }
        st.processed_event_ids = d.get("processed_event_ids", [])
        st.pool_drivers = d.get("pool_drivers", [])
        st.driver_labels = d.get("driver_labels", {})
        st.driver_clubs = d.get("driver_clubs", {})
        st.driver_last_seen = d.get("driver_last_seen", {})
        st.driver_inactive = {k: bool(v) for k, v in d.get("driver_inactive", {}).items()}
        st.total_events_processed = d.get("total_events_processed", 0)
        st.inactivity_threshold = d.get("inactivity_threshold", 20)
        return st


# ─────────────────────────────────────────────────────────────────────────────
#  DATEI-I/O
# ─────────────────────────────────────────────────────────────────────────────

def state_path(profile_name: str, directory: Optional[str] = None) -> str:
    directory = directory or os.path.dirname(os.path.abspath(__file__))
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile_name)
    return os.path.join(directory, f"elo_state_{safe_name}.json")


def load_state(profile_name: str, directory: Optional[str] = None) -> EloState:
    path = state_path(profile_name, directory)
    if not os.path.exists(path):
        return EloState(profile_name=profile_name)
    try:
        with open(path, encoding="utf-8") as f:
            return EloState.from_dict(json.load(f))
    except Exception:
        # Korrupte/leere Datei -> sauber neu anfangen statt zu crashen.
        return EloState(profile_name=profile_name)


def save_state(state: EloState, directory: Optional[str] = None) -> str:
    path = state_path(state.profile_name, directory)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)   # atomarer Write, kein halb-geschriebenes File bei Absturz
    return path


def list_profiles(directory: Optional[str] = None) -> List[str]:
    directory = directory or os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(directory):
        return []
    profiles = []
    for fname in os.listdir(directory):
        if fname.startswith("elo_state_") and fname.endswith(".json"):
            profiles.append(fname[len("elo_state_"):-len(".json")])
    return sorted(profiles)
