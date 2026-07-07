"""
elo_pipeline.py
════════════════════════════════════════════════════════════════════════════
RallyLab — ELO Pipeline (Orchestrierung)

Verbindet elo_engine (Mathematik) + elo_categories (Zuordnung/Gruppierung) +
elo_state (Persistenz) zu einem Ablauf, der eine Liste von Events verarbeitet.

Erwartetes Event-Eingabeformat (RawEvent) ist absichtlich simpel/neutral
gehalten, damit es sowohl aus Racenet-Daten als auch aus CSV-Importen
gebaut werden kann — die Übersetzung "Racenet-JSON -> RawEvent" bzw.
"CSV-Zeilen -> RawEvent" passiert AUSSERHALB dieses Moduls, damit die
Pipeline selbst unabhängig von der Datenquelle bleibt.

EIN Eintrittspunkt:
  - process_racenet_events(...)  chronologische, sequenzielle Verarbeitung
    ALLER Championships/Events eines Zeitraums. Der Aufrufer sortiert die
    Event-Liste chronologisch (championships.start_date, dann
    events.round_number) — diese Funktion verarbeitet sie strikt in dieser
    Reihenfolge, Event für Event, egal ob "historisch" oder "gerade neu".
    Bereits verarbeitete event_ids werden übersprungen (Delta-Update).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from elo_engine import Rating, process_event
from elo_categories import CategoryLookups, RawResult, tracks_for_event
from elo_state import EloState


# ─────────────────────────────────────────────────────────────────────────────
#  NEUTRALES EVENT-FORMAT (quellenunabhängig)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawEvent:
    """
    Ein einzelnes Event, quellenunabhängig.

    event_id:      stabiler Schlüssel. MUSS über alle Quellen/Clubs hinweg
                    eindeutig sein (z.B. "{club_id}:{championship_id}:{index}").
                    NIEMALS nur die Location verwenden — Locations wiederholen
                    sich (nur ~14 reale Rallyes), das würde verschiedene echte
                    Events fälschlich als dasselbe behandeln.
    location:       Rally-Name, NUR für die Surface-Zuordnung (Kategorie-Tracks),
                    spielt für die Event-Identität/Dedup KEINE Rolle.
    finishers:      Liste von Tupeln pro klassiertem Fahrer. Unterstützt:
                      (driver_id, rank, vehicle)                       — Name als ID
                      (driver_id, rank, vehicle, display_name)         — z.B. ssid als ID,
                                                                          Klarname separat
    dnf_drivers:    wie finishers, aber ohne rank (kein gefahrener Rang nötig):
                      (driver_id, vehicle) oder (driver_id, vehicle, display_name)
                    Nicht gestartete Fahrer kommen hier gar nicht erst rein.
    """
    event_id: str
    location: str
    finishers: List[tuple]
    dnf_drivers: List[tuple] = field(default_factory=list)


def _unpack_finisher(t: tuple) -> tuple:
    """(id, rank, vehicle[, display_name]) -> (id, rank, vehicle, display_name)."""
    if len(t) == 4:
        return t
    driver, rank, vehicle = t
    return driver, rank, vehicle, driver


def _unpack_dnf(t: tuple) -> tuple:
    """(id, vehicle[, display_name]) -> (id, vehicle, display_name)."""
    if len(t) == 3:
        return t
    driver, vehicle = t
    return driver, vehicle, driver


@dataclass
class EventProcessingLog:
    event_id: str
    location: str
    tracks_updated: List[str]
    driver_count: int


# ─────────────────────────────────────────────────────────────────────────────
#  EVENT -> TRACK-WEISE VERARBEITUNG
# ─────────────────────────────────────────────────────────────────────────────

def _process_single_event(state: EloState, event: RawEvent, lookups: CategoryLookups,
                           surface_granular: bool = True) -> EventProcessingLog:
    """Verarbeitet EIN Event über alle relevanten Tracks (overall/surface/drivetrain/era)."""
    finishers = [_unpack_finisher(t) for t in event.finishers]
    dnfs      = [_unpack_dnf(t) for t in event.dnf_drivers]

    raw_results = [RawResult(driver=d, rank=rk, vehicle=v) for d, rk, v, _ in finishers]
    worst_rank = (max((r.rank for r in raw_results), default=0)) + 1
    raw_results += [RawResult(driver=d, rank=worst_rank, vehicle=v) for d, v, _ in dnfs]

    for d, _, _, name in finishers:
        state.update_label(d, name)
        state.mark_driver_seen(d)
    for d, _, name in dnfs:
        state.update_label(d, name)
        state.mark_driver_seen(d)

    club_id = event.event_id.split(":")[0] if ":" in event.event_id else event.event_id
    for r in raw_results:
        state.add_driver_club(r.driver, club_id)

    state.total_events_processed += 1
    state.update_inactivity()

    tracks = tracks_for_event(event.location, raw_results, lookups, surface_granular=surface_granular)

    for track_name, results in tracks.items():
        if len(results) < 1:
            continue
        ratings_for_track = state.ratings.setdefault(track_name, {})
        from elo_engine import EventEntry
        entries = [EventEntry(driver=r.driver, rank=r.rank) for r in results]
        process_event(ratings_for_track, entries)

    return EventProcessingLog(event_id=event.event_id, location=event.location,
                               tracks_updated=sorted(tracks.keys()),
                               driver_count=len(raw_results))


# ─────────────────────────────────────────────────────────────────────────────
#  ÖFFENTLICHE EINTRITTSPUNKTE
# ─────────────────────────────────────────────────────────────────────────────
#
#  HINWEIS (Design-Entscheidung): Es gibt bewusst nur noch EINEN
#  Verarbeitungspfad — process_racenet_events(). Der Bradley-Terry-Batch-Fit
#  (ordnungsunabhängig, für Historie ohne verlässliches Datum) und die
#  sequenzielle CSV-Baseline sind entfernt worden: seit start_date/end_date
#  zuverlässig aus der RaceNet-API kommen, gibt es keinen Grund mehr, Events
#  ohne Chronologie zu behandeln. ALLE Championships/Events (historisch wie
#  laufend) werden strikt chronologisch (championships.start_date, dann
#  events.round_number) sequenziell durch process_racenet_events() gejagt —
#  exakt wie ein Event, das gerade frisch passiert.
# ─────────────────────────────────────────────────────────────────────────────

def process_racenet_events(state: EloState, events: List[RawEvent], lookups: CategoryLookups,
                            surface_granular: bool = True) -> List[EventProcessingLog]:
    """
    Verarbeitet eine Liste von Racenet-Events als Delta-Update:
    Events, deren event_id bereits in state.processed_event_ids steht,
    werden übersprungen. Reihenfolge der übergebenen Liste = Verarbeitungsreihenfolge,
    der Aufrufer muss chronologisch sortieren (z.B. nach Datum/Round-Nummer).

    Volle Tracks (overall + surface + drivetrain + era).

    Für eine komplette Neuberechnung (z.B. initiale Baseline über ALLE
    Championships/Clubs) einfach mit frischem EloState (state.processed_event_ids
    leer) und der vollständigen, chronologisch sortierten Event-Liste aufrufen.
    """
    logs = []
    for event in events:
        if state.is_event_processed(event.event_id):
            continue
        log = _process_single_event(state, event, lookups, surface_granular=surface_granular)
        state.mark_event_processed(event.event_id)
        logs.append(log)
    return logs


# ─────────────────────────────────────────────────────────────────────────────
#  EXPORT-HILFEN (für elo_excel.py / GUI)
# ─────────────────────────────────────────────────────────────────────────────

CONSERVATIVE_FACTOR = 1.5   # mu - CONSERVATIVE_FACTOR * sigma für Sortierung
ESTABLISHED_EVENTS  = 20    # Mindest-Events für "Etabliert"-Status


@dataclass
class DriverSummary:
    driver: str
    display_name: str
    mu: float
    sigma: float
    k_sigma: float = 0.0   # Momentum-Wert (siehe elo_engine.py), für die neue kσ-Spalte
    events_played: int = 0
    is_provisional: bool = False
    is_inactive: bool = False
    in_pool: bool = False
    connectivity: int = 1
    conservative_rating: float = 0.0   # mu - 1.5*sigma, für Sortierung

    @property
    def is_established(self) -> bool:
        return self.events_played >= ESTABLISHED_EVENTS


def summarize_track(state: EloState, track: str,
                    include_inactive: bool = False) -> List[DriverSummary]:
    """
    Liefert Fahrer eines Tracks, sortiert nach konservativem Rating
    (mu - 1.5×sigma). Inaktive standardmäßig ausgeblendet.
    """
    drivers = state.ratings.get(track, {})
    summaries = []
    for driver_id, r in drivers.items():
        inactive = state.is_inactive(driver_id)
        if inactive and not include_inactive:
            continue
        conservative = r.mu - CONSERVATIVE_FACTOR * r.sigma
        summaries.append(DriverSummary(
            driver=driver_id,
            display_name=state.label(driver_id),
            mu=r.mu, sigma=r.sigma,
            k_sigma=getattr(r, "k_sigma", r.sigma),  # Fallback falls je ein altes Rating ohne k_sigma auftaucht
            events_played=r.events_played,
            is_provisional=r.is_provisional,
            is_inactive=inactive,
            in_pool=(state.label(driver_id) in state.pool_drivers
                     or driver_id in state.pool_drivers),
            connectivity=state.connectivity(driver_id) or 1,
            conservative_rating=conservative,
        ))
    summaries.sort(key=lambda s: -s.conservative_rating)
    return summaries


def driver_category_breakdown(state: EloState, driver: str) -> Dict[str, Rating]:
    """Alle Track-Ratings eines einzelnen Fahrers (für eine 'Stärken/Schwächen'-Ansicht)."""
    out = {}
    for track, drivers in state.ratings.items():
        if driver in drivers:
            out[track] = drivers[driver]
    return out
