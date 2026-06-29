"""
elo_pipeline.py
════════════════════════════════════════════════════════════════════════════
RallyLab — ELO Pipeline (Orchestrierung)

Verbindet elo_engine (Mathematik) + elo_categories (Zuordnung/Gruppierung) +
elo_state (Persistenz) zu einem Ablauf, der eine Liste von Events verarbeitet.

Erwartetes Event-Eingabeformat (RawEvent) ist absichtlich simpel/neutral
gehalten, damit es sowohl aus Racenet-Daten als auch aus CSV-Importen
gebaut werden kann — die Übersetzung "Racenet-JSON -> RawEvent" bzw.
"CSV-Zeilen -> RawEvent" passiert AUSSERHALB dieses Moduls (z.B. in der
GUI-Schicht von points_auto.py), damit die Pipeline selbst unabhängig
von der Datenquelle bleibt.

Zwei Eintrittspunkte:
  - process_csv_baseline(...)   einmaliger historischer Seed, sperrt sich danach selbst
  - process_racenet_events(...) laufende Delta-Updates über Event-IDs
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

def process_historical_batch(state: EloState, events: List[RawEvent], lookups: CategoryLookups,
                              surface_granular: bool = True, clubs: Optional[List[str]] = None,
                              force: bool = False) -> List[EventProcessingLog]:
    """
    Einmaliger historischer Seed über die Bradley-Terry-Batch-Engine —
    ORDNUNGSUNABHÄNGIG. Im Gegensatz zu process_csv_baseline() (sequenziell,
    daher reihenfolge-empfindlich) wertet diese Funktion ALLE übergebenen
    Events gleichzeitig aus. Das macht sie sicher für Multi-Club-Historie
    OHNE verlässliches Datum — die Events können in beliebiger Reihenfolge
    hereinkommen, das Ergebnis ist garantiert identisch.

    Befüllt sowohl den "overall"-Track als auch alle Kategorie-Tracks
    (surface/drivetrain/era), sofern die Lookups das hergeben — im
    Gegensatz zur alten CSV-Baseline (dort gab's nur "overall", weil CSVs
    keine Location/Vehicle-Metadaten zuverlässig lieferten). Hier kommen
    die Daten aus Racenet, also sind Location+Vehicle vorhanden.

    Sperrt sich danach selbst (state.historical_batch_locked). Erneuter
    Aufruf wird verweigert außer force=True.
    """
    if state.historical_batch_locked and not force:
        raise RuntimeError(
            f"Historische Basis für Profil '{state.profile_name}' wurde bereits am "
            f"{state.historical_batch_loaded_at} geladen "
            f"({state.historical_batch_event_count} Events, Clubs: {state.historical_batch_clubs}). "
            f"Mit force=True erneut ausführen, falls das wirklich gewollt ist."
        )

    from elo_engine import EventEntry, batch_fit_ratings

    track_entries: Dict[str, List[List["EventEntry"]]] = {}
    all_driver_ids = set()
    logs = []

    for event in events:
        finishers = [_unpack_finisher(t) for t in event.finishers]
        dnfs      = [_unpack_dnf(t) for t in event.dnf_drivers]

        raw_results = [RawResult(driver=d, rank=rk, vehicle=v) for d, rk, v, _ in finishers]
        worst_rank = (max((r.rank for r in raw_results), default=0)) + 1
        raw_results += [RawResult(driver=d, rank=worst_rank, vehicle=v) for d, v, _ in dnfs]

        for d, _, _, name in finishers:
            state.update_label(d, name)
        for d, _, name in dnfs:
            state.update_label(d, name)

        club_id = event.event_id.split(":")[0] if ":" in event.event_id else event.event_id
        for r in raw_results:
            state.add_driver_club(r.driver, club_id)

        all_driver_ids.update(r.driver for r in raw_results)

        tracks = tracks_for_event(event.location, raw_results, lookups, surface_granular=surface_granular)
        for track_name, results in tracks.items():
            if len(results) < 2:
                continue   # Batch-Fit braucht mind. 2 Teilnehmer zum Vergleichen
            entries = [EventEntry(driver=r.driver, rank=r.rank) for r in results]
            track_entries.setdefault(track_name, []).append(entries)

        logs.append(EventProcessingLog(event_id=event.event_id, location=event.location,
                                        tracks_updated=sorted(tracks.keys()),
                                        driver_count=len(raw_results)))

    for track_name, ev_list in track_entries.items():
        fitted = batch_fit_ratings(ev_list)
        state.ratings[track_name] = fitted   # frischer Batch ersetzt den Track komplett

    state.lock_historical_batch(event_count=len(events), driver_count=len(all_driver_ids),
                                 clubs=clubs or [])
    return logs


# ─────────────────────────────────────────────────────────────────────────────
#  JRC ELO IMPORT — auskommentiert/inert, als Referenz belassen.
#  Sequenzielle CSV-Baseline (reihenfolge-empfindlich, siehe Begründung in
#  process_historical_batch oben für die Racenet-Variante). Für ein
#  zukünftiges Projekt ohne Racenet-Zugriff ggf. wieder aktivieren.
# ─────────────────────────────────────────────────────────────────────────────

def process_csv_baseline(state: EloState, events: List[RawEvent], lookups: CategoryLookups,
                          surface_granular: bool = True, force: bool = False
                          ) -> List[EventProcessingLog]:
    """
    Verarbeitet die historische CSV-Basis EINMALIG, in der übergebenen
    Reihenfolge (= chronologische Reihenfolge, die der Aufrufer sicherstellen muss).

    Sperrt sich danach selbst (state.csv_baseline_locked = True). Ein erneuter
    Aufruf wird verweigert, außer force=True wird explizit übergeben.

    WICHTIG: Surface/Drivetrain/Era-Tracks werden für die CSV-Basis bewusst
    NICHT befüllt (siehe Projekt-Entscheidung: CSV-Daten sind dafür nicht
    zuverlässig genug) — nur der "overall"-Track bekommt die CSV-Historie.
    """
    if state.csv_baseline_locked and not force:
        raise RuntimeError(
            f"CSV-Baseline für Profil '{state.profile_name}' wurde bereits am "
            f"{state.csv_baseline_loaded_at} geladen ({state.csv_baseline_event_count} Events). "
            f"Mit force=True erneut ausführen, falls das wirklich gewollt ist."
        )

    logs = []
    all_drivers = set()
    for event in events:
        raw_results = [RawResult(driver=d, rank=rk, vehicle=v) for d, rk, v in event.finishers]
        worst_rank = (max((r.rank for r in raw_results), default=0)) + 1
        raw_results += [RawResult(driver=d, rank=worst_rank, vehicle=v) for d, v in event.dnf_drivers]
        all_drivers.update(r.driver for r in raw_results)

        ratings_overall = state.ratings.setdefault("overall", {})
        from elo_engine import EventEntry
        entries = [EventEntry(driver=r.driver, rank=r.rank) for r in raw_results]
        process_event(ratings_overall, entries)

        logs.append(EventProcessingLog(event_id=event.event_id, location=event.location,
                                        tracks_updated=["overall"], driver_count=len(raw_results)))

    state.lock_csv_baseline(event_count=len(events), driver_count=len(all_drivers))
    return logs


def process_racenet_events(state: EloState, events: List[RawEvent], lookups: CategoryLookups,
                            surface_granular: bool = True) -> List[EventProcessingLog]:
    """
    Verarbeitet eine Liste von Racenet-Events als Delta-Update:
    Events, deren event_id bereits in state.processed_event_ids steht,
    werden übersprungen. Reihenfolge der übergebenen Liste = Verarbeitungsreihenfolge,
    der Aufrufer muss chronologisch sortieren (z.B. nach Datum/Round-Nummer).

    Volle Tracks (overall + surface + drivetrain + era), im Gegensatz zur CSV-Basis.
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
    events_played: int
    is_provisional: bool
    is_inactive: bool
    in_pool: bool
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
