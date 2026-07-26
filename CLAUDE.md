# GRF ELO-System

Rating-/Ranking-System für eine WRC-Rally-Liga (GRF). Frontend + Backend +
periodischer Sync gegen RaceNet-Daten, Speicherung in Supabase.

## Architektur

- **Frontend:** `index.html` (statisch, kein Framework), deployt über
  **Vercel** per Git-Push. Domain: `grf-system.vercel.app`.
- **Backend:** `admin_api.py` (Flask), läuft dauerhaft auf **Railway**
  (**Hobby-Plan**, seit 2026-07 — vorher Free/Trial, die Trial-Phase lief
  aus und der Service wurde deshalb gestoppt).
- **Datenbank:** **Supabase** (extern, nicht auf Railway). Railway hostet
  nur die API, kein eigenes DB-Hosting nötig.
- **Sync:** `grf_sync.py` / `racenet_client.py` ziehen Daten von RaceNet.
  ELO-Neuberechnung läuft über den Endpoint `/elo/update` in `admin_api.py`,
  ausgelöst als **Railway Cron Job, alle 4 Stunden** (`0 */4 * * *`), plus
  manuell über das Admin-Panel (mit `force_reset`-Option für kompletten
  Neuaufbau).
- **9 Clubs** werden synchronisiert; größere Clubs haben Events mit ~80+
  Teilnehmern.

**Wichtig für Hosting-Entscheidungen:** `/elo/update` ([admin_api.py:1121](admin_api.py:1121))
ist kein leichter CRUD-Call, sondern lädt im `force_reset`-Fall die
komplette Historie (243 Championships/857 Events/829 Result-Sets) und
rechnet die ELO-Pipeline komplett neu. Das schließt Plattformen mit hartem
Function-Timeout (z.B. Vercel Serverless, 10s/60s) für diesen Endpoint aus
— Railway (oder ein anderer Dauerprozess-Host) ist hier Voraussetzung,
solange dieser Endpoint nicht umgebaut wird.

## Aktuelle ELO-Formel (Stand: nach Session 8, live)

- `BASE_K = 55`
- `SIGMA_DECAY = 0.94` (Anzeige-Sigma)
- `K_SIGMA_DECAY = 0.99` (Momentum-kσ, treibt nur den K-Faktor, öffentlich
  sichtbar als "Momentum (kσ)"-Spalte)
- Gewichtete Duelle nutzen **Anzeige-Sigma**, nicht Momentum-kσ
- Angezeigtes "ELO" = `mu − 1,5×sigma` (konservatives Rating), nicht rohes mu
- **Inaktivität:** rein kalenderbasiert, `INACTIVE_WEEKS = 6`, ändert das
  Rating NICHT mehr (kein Decay), nur ein Flag (`elo_inactive`), das die
  Standardansicht ausblendet
- **Track-Ansichten (Package 4):** Era (Historic/Modern), Surface
  (Gravel/Tarmac/Snow), Drivetrain (AWD/RWD/FWD) — je komplett unabhängige
  Rating-Berechnung, gespeichert geprefixt in `elo_state.state_json.ratings`
  (`era:historic`, `surface:gravel`, ...). Keine Kombination mehrerer
  Tracks, keine Quervergleiche (andere Skala).

## Wichtige Tabellen (Supabase)

- `drivers` — Overall-Ratings + `elo_inactive`-Flag
- `driver_track_ratings` — Track-Ratings (Frontend liest Track-Daten aktuell
  aber aus `elo_state.state_json`, nicht direkt aus dieser Tabelle)
- `elo_state` — einzige Zeile, kompletter Rating-Stand als JSON (~1,5 MB)
- `elo_history` — tägliche Overall-Snapshots für Δ 7D
- RLS: `drivers`, `elo_history`, `elo_state`, `driver_track_ratings` haben
  alle `"public read"` (`FOR SELECT USING (true)`) für den `anon`-Key

## Bekannte Stolperfallen

- **Versions-Badge** im Seitenkopf (`#site-version-badge` in `index.html`,
  ~Zeile 714) ist reines statisches HTML, wird NICHT automatisch aus dem
  `CHANGELOG`-Array gezogen — bei jedem neuen Changelog-Eintrag separat von
  Hand anpassen.
- **"Code ist nachweislich korrekt, aber Seite zeigt's nicht"** → zuerst
  Vercel-Deployments-Tab prüfen (Branch + Commit-Hash des
  Production-Deployments vs. letzter GitHub-Commit), bevor im Code gesucht
  wird. War in Session 8 schon einmal die Ursache (Branch-Mismatch).
- **Supabase Free Plan, Egress-Limit 5GB/Monat:** `/elo/update` hat früher
  bei JEDEM Sync (auch Delta) die komplette Event-Historie neu geladen →
  Egress-Notfall (89% verbraucht). Fix: Delta-Syncs (`force=False`) laden
  seit Session 8 nur noch Championships der letzten 90 Tage. `force_reset`
  lädt weiterhin alles. **Bei Egress-Problemen zuerst hier nachsehen.**

## Bewusst NICHT übernommene Ansätze

TrueSkill, Glicko-2, rollierendes Form-Fenster, kompletter Sigma-Verzicht
(getestet: führt zu Überreaktion/Instabilität statt mehr Fairness),
kombinierte Track-Filter gleichzeitig (Engine unterstützt das strukturell
nicht — jeder Track ist unabhängig).

## Offene Punkte

1. **Δ 7 Tage für Track-Ansichten** existiert nicht — `elo_history` hat
   keine Track-Dimension. Bräuchte `track`-Spalte oder eigene
   Snapshot-Tabelle nach Muster von `driver_track_ratings`.
2. **`/stats/pageview` wirft CORS-Fehler**, Seitenaufruf-Zähler zeigt "—".
   Vermutung: `ALLOWED_ORIGIN`-Env-Var auf Railway stimmt nicht exakt mit
   Produktions-Domain überein. Noch nicht verifiziert.
3. **Car-Rating-Idee** (Autostärke als ELO-Handicap): ganz am Anfang. Es
   gibt ein separates Tool für Sieg-Wahrscheinlichkeit pro Auto/Location
   (Code/Output noch nicht vorgelegt). `car_ratings`-Tabelle aktuell nur
   manuell gepflegt (1,0–2,0-Skala). Eigenständiges Projekt, blockiert
   nichts am ELO-System und wird davon nicht blockiert.
4. **Draft-basiertes Team-Format** für Team-Events — großes, konzeptionell
   in Session 8 durchgesprochenes Vorhaben (rundenbasierter Snake-Draft,
   Elite-Tier-Cap statt Zwangs-Captains, Magic-Link-Autorisierung pro
   Captain statt vollem Account-System). Owner-Plan: erst ein analoger
   Testlauf mit den Captains, bevor irgendetwas gebaut wird. Noch nicht
   begonnen.

## Angekündigt für separate, eigene Chats (noch keine Details)

- **"Themed" und "Teamed" im Backend trennen** — unklar ob DB-Schema,
  API, Frontend oder alles. Owner erklärt Details im entsprechenden Chat.
- **"3 von 4 Events zählen"-Regel** — Streichresultat fürs
  Championship-Ranking (bestes 3 von 4 zählt, schlechtestes wird
  automatisch gestrichen). Aktuell zählt vermutlich noch jedes Event
  gleichwertig. Offen: greift das nur am Championship-Punktestand oder
  auch in der ELO-Berechnung selbst?

## Hosting-Historie (für Kontext, kein offener Punkt)

Backend lief zunächst auf Railway Trial → Trial lief nach 30 Tagen aus,
Service wurde automatisch gestoppt → auf Hobby-Plan ($5/Monat) umgestellt.
Ein Umbau auf eine andere Plattform (z.B. Google Cloud Run, wegen $0-Kosten
bei diesem Traffic-Volumen) ist nicht ausgeschlossen, aber nicht akut
geplant — siehe Einschränkung zu `/elo/update` oben, falls das jemals
angegangen wird.
