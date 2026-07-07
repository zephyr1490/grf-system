"""
elo_engine.py
════════════════════════════════════════════════════════════════════════════
RallyLab — ELO Rating Engine (iRacing-artiges Modell)

Reine Berechnungslogik. KEINE Abhängigkeit von Tkinter, openpyxl, Racenet
oder Dateisystem. Dadurch:
  - frei testbar (siehe elo_engine_test.py / Beispiele unten)
  - später 1:1 wiederverwendbar in einem Web-Backend oder anderen Tools

MODELL (kurz):
  Jeder Fahrer hat pro "Track" (z.B. "overall", "gravel", "awd", "modern")
  ein Rating bestehend aus:
    mu     — Skill-Schätzwert (Start: 1500)
    sigma  — Unsicherheit (Start: hoch, sinkt mit jedem gefahrenen Event)

  Pro Event wird für jeden Fahrer die ERWARTETE Platzierung aus den
  EINZELNEN paarweisen Gewinnwahrscheinlichkeiten gegen jeden anderen
  Teilnehmer berechnet (nicht nur "eigenes Rating vs. Durchschnitt").
  Das sorgt automatisch dafür, dass ein Sieg gegen einen höher bewerteten
  Gegner mehr zählt als gegen einen niedriger bewerteten.

  Die paarweisen Überraschungen werden NICHT mehr einfach gemittelt,
  sondern per inverser-Varianz-Gewichtung (1/sigma_gegner^2) kombiniert:
  ein Duell gegen einen etablierten (niedriges sigma) Gegner ist
  verlässlichere Evidenz als eines gegen einen unsicheren (hohes sigma)
  Gegner mit demselben mu, und zählt entsprechend mehr. Bei einem Feld mit
  überall ähnlichem sigma (der Normalfall bei etablierten Fahrern) ist das
  praktisch identisch zum einfachen Mittel von vorher — der Unterschied
  greift gezielt dort, wo etablierte und brandneue Fahrer gemischt
  aufeinandertreffen. Die Feldgrößen-Neutralität (großes Feld erzeugt nicht
  automatisch krassere Ausschläge als kleines) bleibt dabei erhalten,
  solange die Gegner-Unsicherheiten ähnlich verteilt sind.

  Der K-Faktor (= maximale Rating-Bewegung pro Event) skaliert mit sigma:
  unsichere (neue) Fahrer bewegen sich schneller, etablierte Fahrer mit
  niedrigem sigma bewegen sich nur noch wenig. sigma sinkt nach jedem
  gefahrenen Event Richtung eines Mindestwerts (Floor).

  DNF zählt als letzter Platz (alle DNFs im selben Event sind gleichauf
  Letzte). Nicht gestartete Fahrer tauchen schlicht nicht in den Event-
  Entries auf und werden komplett ignoriert.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
#  KONSTANTEN
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MU      = 1500.0   # Start-Rating für jeden neuen Fahrer/Track
DEFAULT_SIGMA   = 350.0    # Start-Unsicherheit (hoch = bewegt sich schnell)
SIGMA_FLOOR     = 60.0     # Unsicherheit sinkt nie unter diesen Wert
SIGMA_DECAY     = 0.94     # pro Event: DISPLAY-sigma sinkt Richtung Floor (schnell,
                            # steuert NUR die Anzeige/is_provisional/Conservative Rating)
K_SIGMA_DECAY   = 0.99     # pro Event: K-SIGMA sinkt Richtung Floor (langsam, steuert
                            # NUR den K-Faktor -- lässt mu über einen viel längeren
                            # Zeitraum reagieren, ohne dass die ANGEZEIGTE Unsicherheit
                            # aufgebläht aussieht. Siehe Konversation: zwei komplett
                            # unabhängige Zahlen aus demselben Event-Verlauf.]
BASE_K          = 55.0     # K-Faktor bei DEFAULT_SIGMA (skaliert linear mit sigma)
                            # [geändert von 40.0 -> 55.0: etwas mehr Trennschärfe/Bewegung,
                            #  bekannter Kompromiss: auch etwas mehr Rauschen pro Event]
ELO_SCALE       = 400.0    # Standard-Elo-Skalierungskonstante

# Unterhalb dieser Event-Anzahl gilt ein Fahrer als "Newbie" (nur als
# Fallback-Anzeige falls man NICHT nach sigma filtern will — die sauberere
# Methode ist ohnehin: sigma > NEWBIE_SIGMA_THRESHOLD prüfen).
NEWBIE_SIGMA_THRESHOLD = 200.0


# ─────────────────────────────────────────────────────────────────────────────
#  DATENMODELLE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Rating:
    """
    Rating-Zustand eines Fahrers auf EINEM Track (z.B. 'overall' oder 'gravel').

    ZWEI getrennte Unsicherheits-Werte, jeder mit eigenem, unabhängigem Decay:
      sigma    — "Anzeige-Sigma" (schneller Decay, SIGMA_DECAY). Treibt die
                 ANGEZEIGTE Unsicherheit, is_provisional und geht ins
                 Conservative Rating (mu - 1.5*sigma) ein.
      k_sigma  — "K-Sigma" (langsamer Decay, K_SIGMA_DECAY). Treibt NUR den
                 K-Faktor, also wie stark sich mu pro Event noch bewegen darf.
                 Wird NIE direkt angezeigt.
    Beide starten aus demselben Event-Verlauf, laufen aber unabhängig.
    """
    mu: float = DEFAULT_MU
    sigma: float = DEFAULT_SIGMA
    k_sigma: float = DEFAULT_SIGMA
    events_played: int = 0

    @property
    def is_provisional(self) -> bool:
        """True solange die (Anzeige-)Unsicherheit über dem Newbie-Schwellwert liegt."""
        return self.sigma > NEWBIE_SIGMA_THRESHOLD

    def to_dict(self) -> dict:
        return {"mu": round(self.mu, 2), "sigma": round(self.sigma, 2),
                "k_sigma": round(self.k_sigma, 2), "events_played": self.events_played}

    @staticmethod
    def from_dict(d: dict) -> "Rating":
        sigma = d.get("sigma", DEFAULT_SIGMA)
        return Rating(mu=d.get("mu", DEFAULT_MU),
                       sigma=sigma,
                       # Migration: alte gespeicherte Ratings kennen noch kein k_sigma
                       # -> mit dem vorhandenen sigma initialisieren (bestmögliche
                       # Annahme, kein Datenverlust, kein harter Reset nötig).
                       k_sigma=d.get("k_sigma", sigma),
                       events_played=d.get("events_played", 0))


@dataclass
class EventEntry:
    """Ein Teilnehmer-Ergebnis innerhalb eines Events (für EINEN Rating-Track)."""
    driver: str
    rank: int          # 1 = bester Platz. DNF-Fahrer bekommen alle denselben
                        # (schlechtesten) Rang zugewiesen — siehe build_entries_with_dnf().


@dataclass
class DriverEventResult:
    """Ergebnis der Rating-Berechnung für einen einzelnen Fahrer in einem Event."""
    driver: str
    rank: int
    field_size: int
    mu_before: float
    mu_after: float
    sigma_before: float
    sigma_after: float
    performance: float     # Durchschnittliche "Überraschung" ggü. Erwartung, Bereich [-1, +1]
    delta_mu: float

    def to_dict(self) -> dict:
        return {
            "driver": self.driver, "rank": self.rank, "field_size": self.field_size,
            "mu_before": round(self.mu_before, 2), "mu_after": round(self.mu_after, 2),
            "sigma_before": round(self.sigma_before, 2), "sigma_after": round(self.sigma_after, 2),
            "performance": round(self.performance, 4), "delta_mu": round(self.delta_mu, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  KERNFORMELN
# ─────────────────────────────────────────────────────────────────────────────

def expected_score(mu_a: float, mu_b: float, scale: float = ELO_SCALE) -> float:
    """Wahrscheinlichkeit, dass Fahrer A gegen Fahrer B gewinnt (klassische Elo-Formel)."""
    return 1.0 / (1.0 + 10 ** ((mu_b - mu_a) / scale))


def actual_score(rank_a: int, rank_b: int) -> float:
    """1.0 = A vor B, 0.0 = A hinter B, 0.5 = gleicher Rang (z.B. beide DNF)."""
    if rank_a < rank_b:
        return 1.0
    if rank_a > rank_b:
        return 0.0
    return 0.5


def k_factor(sigma: float, base_k: float = BASE_K, default_sigma: float = DEFAULT_SIGMA) -> float:
    """K-Faktor skaliert linear mit der Unsicherheit: unsichere Fahrer bewegen sich schneller."""
    return base_k * (sigma / default_sigma)


def decay_sigma(sigma: float, decay: float = SIGMA_DECAY, floor: float = SIGMA_FLOOR) -> float:
    """Unsicherheit sinkt nach jedem Event Richtung Floor, erreicht ihn aber nie ganz."""
    new_sigma = floor + (sigma - floor) * decay
    return max(floor, new_sigma)


# ─────────────────────────────────────────────────────────────────────────────
#  EVENT-VERARBEITUNG
# ─────────────────────────────────────────────────────────────────────────────

def build_entries_with_dnf(finishers: List[tuple], dnf_drivers: List[str]) -> List[EventEntry]:
    """
    Baut die EventEntry-Liste für ein Event.

    finishers:    Liste von (driver_name, rank) für klassierte Fahrer (rank = Position)
    dnf_drivers:  Liste von driver_names, die DNF hatten (kein 'rank' nötig)

    DNF-Fahrer bekommen alle denselben Rang: (höchster gefahrener Rang) + 1.
    Nicht gestartete Fahrer dürfen hier schlicht NICHT auftauchen.
    """
    entries = [EventEntry(driver=name, rank=rank) for name, rank in finishers]
    if dnf_drivers:
        worst_rank = (max((e.rank for e in entries), default=0)) + 1
        entries.extend(EventEntry(driver=name, rank=worst_rank) for name in dnf_drivers)
    return entries


def process_event(ratings: Dict[str, Rating], entries: List[EventEntry],
                   base_k: float = BASE_K) -> List[DriverEventResult]:
    """
    Verarbeitet EIN Event für EINEN Rating-Track und aktualisiert `ratings` in-place.

    ratings: dict {driver_name: Rating} — wird für alle in `entries` vorkommenden
             Fahrer automatisch mit Default-Werten angelegt, falls noch nicht vorhanden.
    entries: Teilnehmerliste DIESES Tracks (z.B. nur die Gravel-Events, oder nur
             die AWD-Fahrer innerhalb eines gemischten Feldes — die Auswahl macht
             der Aufrufer, dieser Engine-Code kennt keine Kategorien).

    Gibt eine Liste von DriverEventResult zurück (vorher/nachher, Performance, Delta) —
    nützlich für Logging/Anzeige/Debugging.
    """
    n = len(entries)
    if n < 2:
        # Ein Einzelstarter (z.B. einzige Person in dieser Kategorie) hat keine
        # Gegner zum Vergleichen — kein Rating-Update möglich, aber Event zählt mit.
        results = []
        for e in entries:
            r = ratings.setdefault(e.driver, Rating())
            r.events_played += 1
            results.append(DriverEventResult(
                driver=e.driver, rank=e.rank, field_size=n,
                mu_before=r.mu, mu_after=r.mu,
                sigma_before=r.sigma, sigma_after=r.sigma,
                performance=0.0, delta_mu=0.0))
        return results

    # Sicherstellen, dass alle Fahrer ein Rating haben, BEVOR wir mit den
    # (fixen!) Vorher-Werten rechnen — simultanes Update, kein Reihenfolge-Bias.
    for e in entries:
        ratings.setdefault(e.driver, Rating())
    pre = {e.driver: (ratings[e.driver].mu, ratings[e.driver].sigma, ratings[e.driver].k_sigma)
           for e in entries}

    results: List[DriverEventResult] = []
    for e in entries:
        mu_i, sigma_i, k_sigma_i = pre[e.driver]
        weighted_delta_sum = 0.0
        weight_sum = 0.0
        for o in entries:
            if o.driver == e.driver:
                continue
            mu_j, sigma_j, _ = pre[o.driver]
            exp = expected_score(mu_i, mu_j)
            act = actual_score(e.rank, o.rank)
            # Inverse-Varianz-Gewichtung (Standard-Statistik-Technik): ein Duell
            # gegen einen etablierten (niedriges sigma) Gegner ist verlässlichere
            # Evidenz als eines gegen einen unsicheren (hohes sigma) Gegner mit
            # demselben mu -- und wird entsprechend höher gewichtet, statt wie
            # zuvor 1:1 gleich behandelt zu werden. Nutzt die Anzeige-Sigma des
            # Gegners (die konservativere, seriösere Schätzung seiner Zuverlässigkeit).
            weight = 1.0 / (sigma_j ** 2)
            weighted_delta_sum += weight * (act - exp)
            weight_sum += weight

        performance = weighted_delta_sum / weight_sum        # Bereich ca. [-1, +1]
        k = k_factor(k_sigma_i, base_k=base_k)               # <- nutzt K-SIGMA, nicht Anzeige-Sigma
        delta_mu = k * performance

        r = ratings[e.driver]
        r.mu = mu_i + delta_mu
        r.sigma = decay_sigma(sigma_i, decay=SIGMA_DECAY)       # Anzeige-Sigma: schneller Decay
        r.k_sigma = decay_sigma(k_sigma_i, decay=K_SIGMA_DECAY) # K-Sigma: langsamer Decay
        r.events_played += 1

        results.append(DriverEventResult(
            driver=e.driver, rank=e.rank, field_size=n,
            mu_before=mu_i, mu_after=r.mu,
            sigma_before=sigma_i, sigma_after=r.sigma,
            performance=performance, delta_mu=delta_mu))

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  BATCH-ENGINE (Bradley-Terry) — ORDNUNGSUNABHÄNGIG
# ─────────────────────────────────────────────────────────────────────────────
#  Für eine einmalige historische Basis OHNE verlässliche Chronologie (z.B.
#  mehrere Clubs gleichzeitig, kein Datum verfügbar). Im Gegensatz zu
#  process_event() (sequenziell, σ sinkt mit jedem Event, Reihenfolge zählt)
#  wertet diese Methode ALLE historischen Ergebnisse GLEICHZEITIG aus —
#  das Resultat hängt nicht davon ab, in welcher Reihenfolge die Events
#  hereinkommen.
#
#  Mathematisch: klassisches Bradley-Terry-Modell, gelöst per MM-Iteration
#  (Zermelo-Algorithmus). Die Bradley-Terry-"Stärke" s_i hängt mit unserem
#  mu über die exakt selbe Formel zusammen wie expected_score() oben:
#      s_i = 10 ** (mu_i / ELO_SCALE)
#  Dadurch sind Batch-Ergebnis und sequenzielles Live-Tracking später auf
#  derselben Skala kompatibel.
# ─────────────────────────────────────────────────────────────────────────────

BT_ANCHOR_WEIGHT = 1.0   # "virtueller Durchschnittsgegner" pro Fahrer, verhindert
                          # Divergenz bei Fahrern mit 100% Sieg-/Verlustquote oder
                          # fehlender Verbindung zum Rest des Vergleichsgraphen.
BT_MAX_ITERATIONS = 300
BT_CONVERGENCE_EPS = 1e-6


def batch_fit_ratings(all_entries_per_event: List[List[EventEntry]],
                       anchor_mu: float = DEFAULT_MU,
                       anchor_weight: float = BT_ANCHOR_WEIGHT,
                       max_iterations: int = BT_MAX_ITERATIONS,
                       eps: float = BT_CONVERGENCE_EPS) -> Dict[str, Rating]:
    """
    Schätzt Ratings für ALLE Fahrer aus einer Liste historischer Events
    GLEICHZEITIG (nicht sequenziell) — Bradley-Terry-Modell, ordnungsunabhängig.

    all_entries_per_event: Liste von Events, jedes Event eine Liste von
                            EventEntry (driver, rank) — exakt wie process_event(),
                            nur dass hier mehrere Events auf einmal reinkommen.

    Gibt {driver_name: Rating} zurück. sigma wird im Anschluss separat aus der
    Anzahl gefahrener Events abgeleitet (siehe sigma_from_event_count()), NICHT
    aus dem Bradley-Terry-Fit selbst (der kennt nur die Stärke, keine Unsicherheit).
    """
    # 1) Paarweise Siege/Spiele über ALLE Events aggregieren (reihenfolge-egal,
    #    weil hier nur noch summiert wird).
    drivers = set()
    wins: Dict[str, float] = {}                       # driver -> gewichtete Siege
    matches: Dict[tuple, float] = {}                   # (a,b) sorted -> Anzahl Duelle
    win_against: Dict[tuple, float] = {}                # (winner, loser) -> Anzahl/Gewicht

    for entries in all_entries_per_event:
        n = len(entries)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = entries[i], entries[j]
                drivers.add(a.driver); drivers.add(b.driver)
                key = tuple(sorted((a.driver, b.driver)))
                matches[key] = matches.get(key, 0.0) + 1.0
                s = actual_score(a.rank, b.rank)   # 1.0 = a vor b, 0.0 = b vor a, 0.5 = gleich
                wins[a.driver] = wins.get(a.driver, 0.0) + s
                wins[b.driver] = wins.get(b.driver, 0.0) + (1.0 - s)
                win_against[(a.driver, b.driver)] = win_against.get((a.driver, b.driver), 0.0) + s
                win_against[(b.driver, a.driver)] = win_against.get((b.driver, a.driver), 0.0) + (1.0 - s)

    if not drivers:
        return {}

    # 2) Virtueller Anker: jeder Fahrer bekommt zusätzlich `anchor_weight` Siege
    #    UND `anchor_weight` Niederlagen gegen einen fixen "Durchschnittsgegner"
    #    bei anchor_mu. Verhindert Divergenz (s -> 0 oder unendlich) bei
    #    Fahrern mit 100% Quote oder ohne Verbindung zum restlichen Feld.
    anchor_strength = 10 ** (anchor_mu / ELO_SCALE)

    # 3) MM-Iteration (Zermelo-Algorithmus) bis Konvergenz.
    strength = {d: 10 ** (DEFAULT_MU / ELO_SCALE) for d in drivers}

    # Nachbarschaftsliste für Effizienz (nur tatsächlich gespielte Paare)
    opponents: Dict[str, List[str]] = {d: [] for d in drivers}
    for (a, b) in matches:
        opponents[a].append(b)
        opponents[b].append(a)

    for _ in range(max_iterations):
        new_strength = {}
        max_change = 0.0
        for d in drivers:
            numerator = wins.get(d, 0.0) + anchor_weight
            denom = anchor_weight / (strength[d] + anchor_strength)
            for o in opponents[d]:
                n_do = matches[tuple(sorted((d, o)))]
                denom += n_do / (strength[d] + strength[o])
            denom = max(denom, 1e-12)
            new_s = numerator / denom
            new_strength[d] = new_s
            if strength[d] > 0:
                max_change = max(max_change, abs(new_s - strength[d]) / strength[d])
        strength = new_strength
        if max_change < eps:
            break

    # 4) Zurück auf mu-Skala bringen, auf DEFAULT_MU zentrieren (Bradley-Terry
    #    legt die absolute Skala nicht fest, nur relative Stärkeverhältnisse —
    #    wir verschieben das Mittel der Anker-bezogenen Stärken auf 1500).
    import math
    mus = {d: ELO_SCALE * math.log10(s) for d, s in strength.items()}
    mean_mu = sum(mus.values()) / len(mus)
    shift = DEFAULT_MU - mean_mu
    mus = {d: mu + shift for d, mu in mus.items()}

    events_played = {d: 0 for d in drivers}
    for entries in all_entries_per_event:
        for e in entries:
            events_played[e.driver] = events_played.get(e.driver, 0) + 1

    return {
        d: Rating(mu=mus[d], sigma=sigma_from_event_count(events_played[d]),
                   events_played=events_played[d])
        for d in drivers
    }


def sigma_from_event_count(n: int, start_sigma: float = DEFAULT_SIGMA,
                            decay: float = SIGMA_DECAY, floor: float = SIGMA_FLOOR) -> float:
    """
    Leitet eine plausible Unsicherheit direkt aus der Event-Anzahl ab (statt
    iterativ Event für Event zu simulieren) — liefert dasselbe Endergebnis wie
    n-faches Anwenden von decay_sigma(), aber in einem Schritt und garantiert
    ordnungsunabhängig (hängt nur von der ANZAHL ab, nicht von der Reihenfolge).
    """
    return floor + (start_sigma - floor) * (decay ** max(0, n))


# ─────────────────────────────────────────────────────────────────────────────
#  SELBSTTEST (python3 elo_engine.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Kleiner Sanity-Check: A gewinnt durchgehend gegen ein gemischtes Feld.
    ratings: Dict[str, Rating] = {}

    field = ["A", "B", "C", "D"]
    for event_no in range(1, 6):
        # A immer 1., Reihenfolge der anderen bleibt B,C,D
        entries = build_entries_with_dnf(
            finishers=[("A", 1), ("B", 2), ("C", 3), ("D", 4)],
            dnf_drivers=[])
        results = process_event(ratings, entries)
        line = "  ".join(f"{r.driver}:{ratings[r.driver].mu:7.1f}(σ{ratings[r.driver].sigma:5.1f})"
                          for r in results)
        print(f"Event {event_no}: {line}")

    print()
    print("Erwartung: A's Rating steigt deutlich, D's Rating fällt deutlich,")
    print("B/C bewegen sich nur leicht (Ergebnis entsprach in etwa der Erwartung).")
