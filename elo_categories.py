"""
elo_categories.py
════════════════════════════════════════════════════════════════════════════
RallyLab — ELO Kategorie-Zuordnung & Gruppierung

Zuständig für:
  - Lookup-Tabellen: Rally-Location → Surface (Gravel/Tarmac/Snow, inkl.
    Soft/Hard Gravel), Fahrzeug → Drivetrain (AWD/RWD/FWD) + Ära (Modern/Classic)
  - Laden dieser Lookups aus bereits vorhandenen Daten (Sheet/JSON/dict)
  - Gruppierung von Event-Teilnehmern in die richtigen Rating-"Tracks"

KEINE Abhängigkeit von Tkinter/openpyxl — reine Datenlogik, wie elo_engine.py.
Die eigentlichen Lookup-WERTE (welche Rally hat welchen Untergrund, welches
Auto hat welchen Antrieb) kommen von außen (vorhandenes Sheet/Export) — siehe
load_surface_lookup() / load_vehicle_meta() weiter unten für die Ladepfade.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Iterable
import json
import csv
import os


# ─────────────────────────────────────────────────────────────────────────────
#  KATEGORIE-KONSTANTEN
# ─────────────────────────────────────────────────────────────────────────────

SURFACE_GRAVEL_SOFT = "gravel_soft"
SURFACE_GRAVEL_HARD = "gravel_hard"
SURFACE_TARMAC       = "tarmac"
SURFACE_SNOW          = "snow"
SURFACE_UNKNOWN       = "unknown"

# Für ein gröberes Rating-Track (falls Soft/Hard Gravel zusammengefasst
# werden sollen) — Engine kann beides: granular ODER zusammengefasst.
SURFACE_GROUP = {
    SURFACE_GRAVEL_SOFT: "gravel",
    SURFACE_GRAVEL_HARD: "gravel",
    SURFACE_TARMAC:        "tarmac",
    SURFACE_SNOW:           "snow",
    SURFACE_UNKNOWN:        "unknown",
}

DRIVETRAIN_AWD = "AWD"
DRIVETRAIN_RWD = "RWD"
DRIVETRAIN_FWD = "FWD"
DRIVETRAIN_UNKNOWN = "unknown"

ERA_MODERN  = "modern"
ERA_CLASSIC = "classic"
ERA_UNKNOWN  = "unknown"


# ─────────────────────────────────────────────────────────────────────────────
#  DATENMODELLE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VehicleMeta:
    drivetrain: str = DRIVETRAIN_UNKNOWN
    era: str = ERA_UNKNOWN
    vehicle_class: Optional[str] = None   # optional, z.B. "Rally1" / "Rally2" — für 3c falls gewünscht


@dataclass
class CategoryLookups:
    """Hält alle Zuordnungstabellen, die für die Kategorie-Ratings gebraucht werden."""
    surface_by_location: Dict[str, str]          # location-name (lowercase) -> SURFACE_*
    vehicle_meta: Dict[str, VehicleMeta]          # vehicle-name (lowercase) -> VehicleMeta

    def surface_for(self, location: str) -> str:
        return self.surface_by_location.get(_norm(location), SURFACE_UNKNOWN)

    def meta_for(self, vehicle: str) -> VehicleMeta:
        return self.vehicle_meta.get(_norm(vehicle), VehicleMeta())


def _norm(s: str) -> str:
    return (s or "").strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
#  LADEN DER LOOKUPS
# ─────────────────────────────────────────────────────────────────────────────
#  Drei unterstützte Quellen, je nachdem was am bequemsten ist:
#    1. JSON-Datei  (eigenes, gepflegtes Format)
#    2. CSV-Datei   (z.B. Export aus dem vorhandenen Sheet)
#    3. Direkt als Python-dict übergeben (z.B. aus einem bereits geladenen
#       openpyxl-Workbook im aufrufenden Code)
# ─────────────────────────────────────────────────────────────────────────────

def load_surface_lookup_json(filepath: str) -> Dict[str, str]:
    """
    Erwartet JSON-Format:
    {
      "Rally Sweden":  "snow",
      "Rally Finland": "gravel_soft",
      "Rally Greece":  "gravel_hard",
      "Monte Carlo":   "tarmac"
    }
    """
    if not os.path.exists(filepath):
        return {}
    with open(filepath, encoding="utf-8") as f:
        raw = json.load(f)
    return {_norm(k): v for k, v in raw.items()}


def load_surface_lookup_csv(filepath: str, location_col="Location", surface_col="Surface") -> Dict[str, str]:
    """Erwartet CSV mit mind. den Spalten Location, Surface (Werte wie oben: snow/gravel_soft/gravel_hard/tarmac)."""
    if not os.path.exists(filepath):
        return {}
    out = {}
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            loc = row.get(location_col, "").strip()
            surf = row.get(surface_col, "").strip().lower()
            if loc and surf:
                out[_norm(loc)] = surf
    return out


def load_vehicle_meta_json(filepath: str) -> Dict[str, VehicleMeta]:
    """
    Erwartet JSON-Format:
    {
      "Toyota GR Yaris Rally1": {"drivetrain": "AWD", "era": "modern", "vehicle_class": "Rally1"},
      "Lancia Stratos":         {"drivetrain": "RWD", "era": "classic"}
    }
    """
    if not os.path.exists(filepath):
        return {}
    with open(filepath, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        _norm(k): VehicleMeta(
            drivetrain=v.get("drivetrain", DRIVETRAIN_UNKNOWN),
            era=v.get("era", ERA_UNKNOWN),
            vehicle_class=v.get("vehicle_class"),
        )
        for k, v in raw.items()
    }


def load_vehicle_meta_csv(filepath: str, vehicle_col="Vehicle", drivetrain_col="Drivetrain",
                           era_col="Era", class_col="Class") -> Dict[str, VehicleMeta]:
    """Erwartet CSV mit mind. Vehicle, Drivetrain, Era (Class optional)."""
    if not os.path.exists(filepath):
        return {}
    out = {}
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            veh = row.get(vehicle_col, "").strip()
            if not veh:
                continue
            out[_norm(veh)] = VehicleMeta(
                drivetrain=row.get(drivetrain_col, "").strip() or DRIVETRAIN_UNKNOWN,
                era=row.get(era_col, "").strip().lower() or ERA_UNKNOWN,
                vehicle_class=(row.get(class_col, "").strip() or None) if class_col in row else None,
            )
    return out


def build_lookups(surface_source: dict | str, vehicle_source: dict | str) -> CategoryLookups:
    """
    Komfort-Funktion: nimmt entweder fertige dicts ODER Dateipfade (.json/.csv)
    und baut daraus ein CategoryLookups-Objekt.
    """
    surface = _resolve_source(surface_source, load_surface_lookup_json, load_surface_lookup_csv)
    vehicle = _resolve_source(vehicle_source, load_vehicle_meta_json, load_vehicle_meta_csv)
    return CategoryLookups(surface_by_location={_norm(k): v for k, v in surface.items()},
                            vehicle_meta={_norm(k): (v if isinstance(v, VehicleMeta) else VehicleMeta(**v))
                                          for k, v in vehicle.items()})


def _resolve_source(source, json_loader, csv_loader):
    if isinstance(source, dict):
        return source
    if isinstance(source, str):
        if source.lower().endswith(".json"):
            return json_loader(source)
        if source.lower().endswith(".csv"):
            return csv_loader(source)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
#  GRUPPIERUNG: Event-Teilnehmer → Rating-Tracks
# ─────────────────────────────────────────────────────────────────────────────
#  Surface-Track: GANZES Feld eines Events (Untergrund ist Eigenschaft des Events)
#  Drivetrain/Era-Track: nur die Teilnehmer MIT GLEICHEM Wert innerhalb des Events
#                         (Antrieb/Ära ist Eigenschaft des einzelnen Fahrzeugs)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawResult:
    """Minimale Eingabe pro Fahrer für die Gruppierung — driver+rank+vehicle."""
    driver: str
    rank: int
    vehicle: str


def tracks_for_event(location: str, results: List[RawResult], lookups: CategoryLookups,
                      surface_granular: bool = True) -> Dict[str, List[RawResult]]:
    """
    Baut für EIN Event alle relevanten Tracks.

    Gibt zurück: {track_name: [RawResult, ...]}
      - "overall"               -> alle Teilnehmer (immer)
      - "surface:<surface>"     -> alle Teilnehmer (nur wenn Surface bekannt)
      - "drivetrain:<AWD/RWD/FWD>" -> nur Teilnehmer mit diesem Antrieb (wenn >= 2)
      - "era:<modern/classic>"  -> nur Teilnehmer mit dieser Ära (wenn >= 2)

    surface_granular=True  -> "gravel_soft"/"gravel_hard" getrennt
    surface_granular=False -> beide zu "gravel" zusammengefasst
    """
    tracks: Dict[str, List[RawResult]] = {"overall": list(results)}

    surface = lookups.surface_for(location)
    if surface != SURFACE_UNKNOWN:
        key = surface if surface_granular else SURFACE_GROUP.get(surface, surface)
        tracks[f"surface:{key}"] = list(results)

    by_drivetrain: Dict[str, List[RawResult]] = {}
    by_era: Dict[str, List[RawResult]] = {}
    for r in results:
        meta = lookups.meta_for(r.vehicle)
        if meta.drivetrain != DRIVETRAIN_UNKNOWN:
            by_drivetrain.setdefault(meta.drivetrain, []).append(r)
        if meta.era != ERA_UNKNOWN:
            by_era.setdefault(meta.era, []).append(r)

    for dt, rs in by_drivetrain.items():
        if len(rs) >= 2:
            tracks[f"drivetrain:{dt}"] = rs
    for era, rs in by_era.items():
        if len(rs) >= 2:
            tracks[f"era:{era}"] = rs

    return tracks
