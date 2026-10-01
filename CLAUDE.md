# GRF ELO-System

> ## 🔴 SESSION-UNTERBRECHUNG — HIER ZUERST WEITERMACHEN
> *(unabhängig von den normalen "Offene Punkte" weiter unten — dieser
> Abschnitt bündelt nur den laufenden RaceNet-Token-Vorfall und seine
> direkten Folgen, chronologisch zuletzt bearbeitet)*
>
> **Ausgangspunkt:** Website zeigte "Last synced 3d ago" — der automatische
> Sync lief seit ~3 Tagen nicht mehr durch. Grund: RaceNets Refresh-Token
> (läuft bewusst nur 24h, deshalb der ganze Rotations-Automatismus über
> `system_config.racenet_refresh_token`) war abgelaufen/ungültig geworden.
> Mutmaßliche Ursache: entweder die zeitgleiche Supabase-"Intermittent
> latency in Eastern US"-Störung (ließ vermutlich einen routinemäßigen
> Rotations-Schreibvorgang scheitern) oder ein manueller Browser-Login des
> Owners im selben automatisierten Account — nicht zweifelsfrei
> unterscheidbar, Details s. "Bekannte Stolperfallen" weiter unten.
>
> **Was bereits erledigt ist:**
> - Ursache diagnostiziert, beide Theorien dokumentiert (s.
>   Stolperfallen-Abschnitt).
> - `racenet_client.py` wurde bereits angepasst: (a) Debug-Logging bei
>   `_bootstrap_from_env()` (zeigt `force_reset`-Wert + Token-Länge) und bei
>   einem fehlgeschlagenen Refresh (Response-Header + -Body, nicht nur der
>   nackte Statuscode), (b) Resilienz-Fix in
>   `_supabase_save_refresh_token()` — 3 Versuche mit Backoff (2s/4s) und
>   10s Timeout statt nur einem Versuch mit 5s Timeout.
> - Wichtige Erkenntnis dabei: **ZWEI** Railway-Services
>   (`grf_sync.py` UND `admin_api.py`) nutzen `racenet_client.py` und
>   brauchen beide ihre eigene `RACENET_REFRESH_TOKEN`-Variable aktuell
>   gehalten — `RACENET_TOKEN_RESET=1` darf aber immer nur bei EINEM der
>   beiden gleichzeitig aktiv sein (sonst überschreiben sie sich
>   gegenseitig die frisch rotierte Version, s. Stolperfallen-Abschnitt für
>   die genaue Reihenfolge).
>
> **⚠️ NICHT bestätigt / nächster Schritt:**
> - Die reparierte `racenet_client.py` (Debug + Resilienz-Fix) wurde vom
>   Owner **noch nicht auf Railway hochgeladen**.
> - Ob der letzte manuelle Recovery-Versuch (nach dem Fix der
>   doppelten-RESET=1-Falle) tatsächlich erfolgreich war, ist **nicht
>   bestätigt** — letzter bekannter Stand war ein HTTP-204-Fehlschlag, der
>   Owner wollte danach mit frischer Session/sofortigem Einfügen erneut
>   versuchen, aber es kam keine Rückmeldung mehr vor dem Session-Ende.
> - **Erster Schritt im neuen Chat:** Owner fragen, ob der Sync inzwischen
>   wieder läuft (Website-Statuskachel "Last synced" prüfen). Falls nicht:
>   `racenet_client.py` (liegt bereits fertig vor, s.u.) hochladen lassen,
>   dann Recovery-Prozedur aus den Stolperfallen erneut durchgehen.
>
> **Separates, noch unbegonnenes Feature-Wunsch aus demselben Gespräch:**
> Owner möchte bei einem Sync-Ausfall benachrichtigt werden (Discord-
> Webhook, analog zur bestehenden Stage-Interview-Webhook-Infrastruktur),
> statt es zufällig nach Tagen selbst zu bemerken. **Noch nicht
> umgesetzt** — nur besprochen. Geplanter Ansatz: neue Env-Variable
> `ADMIN_ALERT_WEBHOOK_URL`, Alert an den drei `sys.exit(1)`-Stellen in
> `grf_sync.py main()` (RaceNet-Verbindung, fehlende
> `SUPABASE_SERVICE_KEY`, Supabase-Verbindung), mit einem zeitbasierten
> Gate (z.B. alle 6h erneut erinnern statt bei jedem 10-Minuten-Lauf zu
> spammen) über einen neuen `system_config`-Schlüssel, nach demselben
> Muster wie `last_sync_at`.
>
> **Technischer Hinweis zur Dateilage:** Die Sandbox-Arbeitskopie von
> `grf_sync.py` ging während der Bearbeitung verloren (Umgebungs-Reset
> mitten in der Session) — die Rekonstruktion aus dem Gedächtnis wurde
> abgebrochen, weil nicht zweifelsfrei garantiert werden konnte, dass sie
> exakt dem tatsächlich deployten Stand entspricht. Owner wollte die echte,
> aktuell auf Railway laufende `grf_sync.py` aus GitHub ins Projekt
> hochladen, bevor an dieser Datei weitergearbeitet wird — **falls das
> noch nicht geschehen ist, zuerst danach fragen**, bevor an `grf_sync.py`
> irgendwas geändert wird. `racenet_client.py` ist davon NICHT betroffen
> (die fertige, fehlerfrei durchgetestete Debug+Resilienz-Version liegt
> bereits vor, s.o.).

---

Rating-/Ranking-System für eine WRC-Rally-Liga (GRF). Frontend + Backend +
periodischer Sync gegen RaceNet-Daten, Speicherung in Supabase. Ursprünglich
für einen einzelnen Club (GRF Themed) gebaut, seit Session 10 ein
vollwertiges Multi-Club-Portal (~11 Clubs) mit generischem Club-Template,
Club-übergreifenden Stats, optionalem Custom-Punktesystem pro Club und einer
Stage-Interview-/Kommentarfunktion mit Discord-Anbindung.

## Architektur

- **Frontend:** `index.html` (statisch, kein Framework), deployt über
  **Vercel** per Git-Push. Domain: `grf-system.vercel.app`.
- **Backend:** `admin_api.py` (Flask), läuft dauerhaft auf **Railway**
  (**Hobby-Plan**, seit 2026-07 — vorher Free/Trial, die Trial-Phase lief
  aus und der Service wurde deshalb gestoppt).
- **Datenbank:** **Supabase** (extern, nicht auf Railway). Railway hostet
  nur die API, kein eigenes DB-Hosting nötig.
- **Sync:** `grf_sync.py` / `racenet_client.py` ziehen Daten von RaceNet.
  Drei Modi:
  - **Normal** (Cron, alle 10 Min, `*/10 * * * *`) — nur die jeweils
    aktuelle Championship jedes Clubs.
  - **`--full`** — alle Championships (aktuell + historisch) jedes Clubs,
    inkl. Events/Stages/Results. Schwer, nur bei Bedarf manuell laufen
    lassen (z.B. nach Aufnahme eines neuen Clubs mit Altdaten).
  - **`--standings-only`** (Session 10, neu) — lädt NUR
    `championship_standings` (RaceNet-eigener Punktestand) für JEDE
    Championship jedes Clubs nach, ohne Events/Stages/Results anzufassen.
    Deutlich leichter als `--full`. Gedacht für den Fall, dass
    `sync_championship_standings()` nachträglich eingeführt wurde und
    historische Championships ihre Standings nachträglich brauchen.
  - `grf_sync.py` triggert nach jedem normalen/`--full`-Lauf selbst den
    Endpoint `/elo/update` in `admin_api.py` für die ELO-Neuberechnung
    (kein separater Cron dafür) — plus manuell über das Admin-Panel (mit
    `force_reset`-Option für kompletten Neuaufbau).
- **Discord-Integration** (Session 10, neu): `admin_api.py` postet über
  club-spezifische Discord-Webhooks (Tabelle `club_discord_webhooks`,
  Fallback auf die Umgebungsvariable `DISCORD_WEBHOOK_URL`). Kein
  laufender Bot-Prozess — reine HTTP-POST-Webhooks, ausgelöst on-demand
  vom Backend.
- **~11 Clubs** werden synchronisiert; größere Clubs haben Events mit
  ~80-100+ Teilnehmern, manche Championships über 100 Fahrer insgesamt.

**Wichtig für Hosting-Entscheidungen:** `/elo/update` ([admin_api.py:1121](admin_api.py:1121))
ist kein leichter CRUD-Call, sondern lädt im `force_reset`-Fall die
komplette Historie (mehrere hundert Championships/Events/Result-Sets) und
rechnet die ELO-Pipeline komplett neu. Das schließt Plattformen mit hartem
Function-Timeout (z.B. Vercel Serverless, 10s/60s) für diesen Endpoint aus
— Railway (oder ein anderer Dauerprozess-Host) ist hier Voraussetzung,
solange dieser Endpoint nicht umgebaut wird.

## Aktuelle ELO-Formel (unverändert seit Session 8, live)

- `BASE_K = 55`
- `SIGMA_DECAY = 0.94` (Anzeige-Sigma)
- `K_SIGMA_DECAY = 0.99` (Momentum-kσ, treibt nur den K-Faktor, öffentlich
  sichtbar als "Momentum (kσ)"-Spalte)
- Gewichtete Duelle nutzen **Anzeige-Sigma**, nicht Momentum-kσ
- Angezeigtes "ELO" = `mu − 1,5×sigma` (konservatives Rating), nicht rohes mu
- **Inaktivität:** rein kalenderbasiert, `INACTIVE_WEEKS = 6`, ändert das
  Rating NICHT mehr (kein Decay), nur ein Flag (`elo_inactive`), das die
  Standardansicht ausblendet. Das Datum "letztes Rennen" pro Fahrer wird
  in `elo_state.state_json.driver_last_event_date` gepflegt, ausschließlich
  monoton nach oben (neueres Datum überschreibt älteres, nie umgekehrt) —
  ein `--full`- oder `--standings-only`-Lauf, der alte Historie
  nachverarbeitet, kann einen Fahrer daher nie fälschlich wieder als aktiv
  markieren (per Test verifiziert, Session 10).
- **Track-Ansichten (Package 4):** Era (Historic/Modern), Surface
  (Gravel/Tarmac/Snow), Drivetrain (AWD/RWD/FWD) — je komplett unabhängige
  Rating-Berechnung, gespeichert geprefixt in `elo_state.state_json.ratings`
  (`era:historic`, `surface:gravel`, ...). Keine Kombination mehrerer
  Tracks, keine Quervergleiche (andere Skala).

### Δ 7 Tage (elo_history) — Mechanik + Session-9-Fix

- **Kein wöchentliches Ereignis** — rollierender Vergleich, bei jedem
  Seitenaufruf neu berechnet: `ELO jetzt − ELO von vor genau 7 Tagen`. Dafür
  braucht's täglich (nicht nur einmal pro Woche) einen Snapshot in
  `elo_history` — der wird bei JEDEM Cron-Lauf (alle 10 Min) für den
  aktuellen Tag überschrieben (`on_conflict="driver_name,snapshot_date"`).
- **Gefixt (Session 9):** `fetchEloDeltaMap()` im Frontend nutzte `sb()`
  statt `sbAll()` — bei der Fahrerzahl übers Gesamtsystem kam die
  Tages-Abfrage über `elo_history` in die Nähe von Supabase/PostgRESTs
  ~1000-Zeilen-Cap, wodurch zufällige Fahrer beim Δ7 nicht angezeigt wurden
  (Daten in `elo_history` selbst waren immer korrekt, reines
  Anzeige-/Pagination-Problem). **Vorsicht bei jeder neuen Abfrage gegen
  potenziell große Tabellen: `sb()` cappt bei ~1000 Zeilen, `sbAll()`
  paginiert vollständig — im Zweifel `sbAll()` nutzen.**
- **Erwartetes Verhalten nach `--full`/`--standings-only`-Läufen:** wenn
  ein solcher Lauf viele bisher fehlende historische Ergebnisse auf einen
  Schlag nachträgt, verschiebt das die aktuellen Ratings spürbar an einem
  einzigen Tag — Δ7D vergleicht das aber gegen eine 7 Tage alte
  Momentaufnahme mit dem ALTEN, unvollständigen Datenstand. Für bis zu 7
  Tage können dadurch unrealistisch hohe Δ7D-Werte auftreten — das ist
  Daten, die aufholen, kein Bug, und pendelt sich von selbst ein, sobald
  der 7-Tage-Vergleichspunkt selbst hinter den Lauf zurückfällt.
- **Bekannte, nicht gefixte Eigenheit:** `date.today()` im Backend
  (`admin_api.py`, Snapshot-Schreibstelle) läuft ohne explizite Zeitzone —
  nutzt die Server-Zeit des Railway-Containers, vermutlich UTC. Der
  Tageswechsel für den Snapshot passiert dadurch um Mitternacht UTC =
  1–2 Uhr CET/CEST, nicht um Mitternacht bei der Community. Owner-Wunsch
  (noch nicht umgesetzt): Snapshot-Tag an CET/Club-Wochenrhythmus
  ausrichten statt reiner Kalendertage.

## Car-Rating-System (Session 9, komplett überarbeitet)

- **Algorithmus** (`admin_api.py`, `_compute_stage_factors` + `_compute_cr`) ist
  1:1 nach dem Original-Tool `points_auto_fixed_28.py` (Desktop-App,
  Owner-lokal, **nicht im Repo**) portiert:
  1. Pro Strecke einzeln normalisieren (jedes Auto bekommt seine EIGENE
     Top-`top_pct`%-Zeit als Referenz)
  2. `min_n` gilt PRO STRECKE, nicht global über alle Strecken summiert
  3. Gewichteter Durchschnitt über alle Strecken (Gewicht = Teilnehmerzahl)
  4. Doppelte Normierung (nach Mittelung UND nach Exponent) — garantiert,
     dass das schnellste Auto in JEDER Berechnung exakt CR=1.0 bekommt
- **CR Sets** sind bewusst championship-unabhängig speicherbar (`car_ratings`
  mit `championship_id IS NULL` + `set_name`), Zuweisung zu einer konkreten
  Championship über `/cr/assign`.
- **Bekannter Stolperstein, gefixt:** `/cr/vehicles` baute seine Fahrzeug-
  liste früher NUR aus `stage_results` — für eine Championship OHNE
  Ergebnisse blieb die Liste leer. Jetzt Vereinigung aus `stage_results`
  UND `car_ratings`. **Vorsicht bei ähnlichen Endpoints:** jeder Endpoint,
  der eine Liste nur aus tatsächlichen Renn-Ergebnissen baut, hat
  potenziell dasselbe Problem für noch nicht gestartete Championships.
- **Gilt NICHT für jeden Club** — nur GRF Themed/Teamed nutzen das
  klassische `base_points × CR`-System. Die generischen Clubs (s.u.) haben
  standardmäßig kein CR, außer explizit über `CUSTOM_SCORING` konfiguriert.

## Club-Stats-System (Session 10)

Jeder Club (nicht nur Themed/Teamed) hat einen Stats-Tab mit einem
3-fach-Umschalter — wichtig für zukünftige Änderungen, da hier schon
zweimal Missverständnisse zu echten Bugs geführt haben (s. Stolperfallen):

- **Current Championship** — 4 Kennzahlen, alle auf die aktuell
  laufende/zuletzt geöffnete Championship des Clubs beschränkt: Event
  Wins (Platz 1 im Gesamtergebnis), Event Podiums (Platz 1-3), Stage Wins
  (schnellste Stage-Zeit), Stage Podiums (Top-3-Stage-Zeit).
- **Championships Overall** — wer hat wie viele Championships insgesamt
  gewonnen (Rang 1 in `championship_standings`) bzw. auf dem
  Championship-Podium gestanden (Rang 1-3), über die komplette
  Club-Historie.
- **Events Overall** — dieselben Event Wins/Podiums wie bei "Current
  Championship", aber über ALLE Events der kompletten Club-Historie
  summiert, nicht nur die aktuelle Saison.

**Sechs Postgres-Funktionen** liefern die Daten (alle mit optionalem
`p_championship_id`-Parameter für Current/Overall-Unterscheidung, außer den
Championship-Funktionen, die immer club-weit sind):
`club_event_win_stats`, `club_podium_stats`, `club_stage_win_stats`,
`club_stage_podium_stats`, `club_championship_wins`,
`club_championship_podiums`. Grund für DB-seitige Aggregation statt
Frontend-Queries: mehrere gescheiterte Vorgänger-Ansätze (Batch-IN-Listen
zu lang bei 300+ Championships, PostgREST-Embedded-Filter warfen
NetworkErrors) — s. Stolperfallen.

**`championship_standings`** (neue Tabelle) speichert RaceNets EIGENEN,
dynamischen (fahreranzahl-abhängigen) Punktestand pro Championship —
abgeholt über `racenet_client.get_championship_standings()`, eine Methode,
die schon vor Session 10 im Client existierte, aber nie aufgerufen wurde.
Wird von `sync_championship_standings()` befüllt (delete-then-insert pro
Championship, da RaceNets Antwort ein kompletter Snapshot ist).

## Custom-Punktesysteme pro Club (Session 10)

Manche Clubs wollen ein eigenes, von GRFs Standard-Punktetabelle
abweichendes Scoring. Aktuell konfiguriert: **Club 14317** — Positions-
Punkte `[20,16,14,12,10,8,6,5,4,3,2,1,0]`, DNF = 0 Positions-Punkte
(behält aber Stage-Bonus-Punkte für Stages, die vor dem Ausfall gewonnen
wurden), +1 Punkt pro Stage-Sieg on top.

- **Backend:** `CUSTOM_SCORING`-Dict in `grf_sync.py`, geschlüsselt nach
  **championship_id** (nicht club_id!) — gilt bewusst nur für die explizit
  eingetragene Championship, nicht automatisch für eine spätere Saison
  desselben Clubs.
- **Frontend:** `CLUB_SCORING_INFO` in `index.html`, geschlüsselt nach
  club_id, enthält dieselben Zahlen zur Anzeige (Standings-Tab, Rules-Tab).
  Beide Stellen müssen bei Änderungen manuell synchron gehalten werden —
  kein Admin-UI dafür (bewusste Owner-Entscheidung, kleine Community).
- **Wichtiger, bereits gefundener Bug (behoben):** "Championships Overall"
  für einen Custom-Scoring-Club darf NICHT pauschal die eigene
  `event_results.total_points`-Aggregation für ALLE Championships des
  Clubs verwenden — nur für die eine tatsächlich konfigurierte
  Championship gilt das Custom-System. Jede ANDERE (auch historische)
  Championship desselben Clubs muss stattdessen `championship_standings`
  (RaceNets echte Platzierung) nutzen, sonst weicht der angezeigte
  Sieger vom tatsächlichen RaceNet-Ergebnis ab (konkret aufgetreten und
  gefixt: Club 14317, Championship "nr2").

## Stage-Interview-Funktion (Session 10, komplett neu)

RBR-artiges "Stage-End Interview": Fahrer klickt sich nach dem ersten
Stage-Ergebnis durch alle Stages eines Events, hinterlässt optional pro
Stage einen Kommentar, sieht am Ende eine Zusammenfassung mit
Bearbeiten/Kopieren/Discord-Posten-Optionen. Erreichbar über einen
🎙️-Button in der aufgeklappten Stage-Zeiten-Ansicht jedes Ergebnisses
(Themed, Teamed, generische Clubs — alle drei Kontexte).

**Kein Login.** Identitätsschutz stattdessen über einen 4-stelligen PIN
pro Fahrer:
- **Kein Self-Signup** — PINs werden AUSSCHLIESSLICH vom Admin (aktuell:
  nur der Owner selbst, "zephyr") im Admin-Panel-Tab "Interviews" vergeben.
  Bewusste Entscheidung gegen ein sichereres, aber aufwendigeres System
  (Hash, Discord-OAuth) — bei ~20 Nutzern in einer vertrauten Community
  nicht nötig.
- PIN wird im **Klartext** gespeichert (`driver_pins`-Tabelle) — die
  eigentliche Absicherung ist, dass diese Tabelle NICHT über den
  `anon`-Key erreichbar ist (kein RLS-Grant), nur der Service-Role-Key im
  Backend kommt ran.
- Beim Vergeben/Nachschlagen eines PINs wird der eingegebene Fahrername
  case-insensitiv gegen die kanonische `drivers`-Tabelle abgeglichen
  (`_resolve_canonical_driver_name()`) und IMMER unter der korrekten
  RaceNet-Schreibweise gespeichert — sonst würde die spätere
  Kommentar-PIN-Prüfung (case-sensitiver Exakt-Vergleich) den PIN nie
  finden. Eingabefeld hat Autocomplete gegen `/drivers/search`.
- **PIN-Reset hat KEINE Auswirkung auf bereits gespeicherte Interviews** —
  `driver_pins` und `stage_comments` sind komplett getrennte Tabellen,
  der PIN wird nirgends mit einem Kommentar verknüpft gespeichert.
- Admin-Panel-Tab "Interviews" → "Registered PINs" zeigt alle vergebenen
  PINs im Klartext (schneller Überblick, wer schon registriert ist) mit
  Lösch-Option pro Fahrer (löscht nur den PIN, keine Kommentare).

**Discord-Anbindung:** einfache Webhooks (kein laufender Bot). Jeder Club
kann einen eigenen Kanal bekommen (`club_discord_webhooks`-Tabelle,
Admin-Panel-Verwaltung), Auflösung über `event_id → championship_id →
club_id`. Ohne club-spezifischen Eintrag: Fallback auf die globale
`DISCORD_WEBHOOK_URL`-Umgebungsvariable (Railway). **Stand Session 10:
nur ein Test-Webhook eingerichtet — für die anderen ~10 Clubs müssen die
jeweiligen Kanal-Webhooks noch nacheinander im Admin-Panel eingetragen
werden, sobald die jeweiligen Club-Admins Bescheid wissen.**

**Bekannte Einschränkung:** Kommentare werden aktuell NIRGENDS direkt auf
der Website angezeigt (weder inline in der Ergebnistabelle noch sonstwo)
— nur über "Kopieren" oder "An Discord posten" sichtbar zu machen, plus
(bewusst nicht mehr vorhanden, s.u.) im Admin-Panel. Falls das später
gewünscht wird, bräuchte es eine eigene Lese-Ansicht — noch nicht gebaut.

**Bewusst NICHT gebaut / wieder entfernt:** eine Kommentar-Moderations-
Ansicht (einzelne Kommentare auflisten + löschen können) wurde gebaut,
dann auf Owner-Wunsch wieder aus dem Admin-Panel entfernt — Begründung:
Löschen im Admin-Panel entfernt den Post NICHT aus Discord, und Fahrer
können dort ohnehin frei posten, der praktische Nutzen einer
Lösch-Funktion für einzelne Kommentare war zu gering. Die Backend-
Endpunkte (`GET/DELETE /admin/comments`) existieren noch (harmlos,
ungenutzt), falls das Feature später doch gebraucht wird.

**Mobile-Feinschliff** (mehrere Runden Nutzer-Feedback):
- Interview-Button war bei Themed/Teamed anfangs immer sichtbar statt nur
  bei aufgeklappter Zeile — CSS-Klassen-Fehler (`.slim-detail-extra`
  fehlte), gefixt.
- Bildschirmtastatur verdeckte den "Next"-Button — gelöst über die
  **VisualViewport-API**: Modal-Höhe/-Position wird live an
  `window.visualViewport` angepasst, reagiert auf `resize`/`scroll`-
  Events während die Tastatur auf-/zuklappt.
- Autofokus (Tastatur öffnet automatisch beim Erscheinen eines
  Eingabefelds) ging beim Viewport-Fix verloren — Ursache: `.focus()`
  wurde aufgerufen, BEVOR das Overlay auf `display:flex` gesetzt war;
  Elemente in einem noch `display:none`-Elternteil sind in echten
  Browsern grundsätzlich nicht fokussierbar (jsdom, die Test-Umgebung,
  erzwingt diese Regel NICHT so streng wie echte Browser — Vorsicht bei
  ähnlichen Fokus-Tests, jsdom kann hier falsch-positiv sein). Fix:
  Overlay zuerst sichtbar machen, dann erst rendern/fokussieren.
- Generische Clubs zeigten aufgeklappte Stage-Zeiten auf Mobile als
  starre `<table>`-Spalten (liefen seitlich aus dem Bild) — Themed/Teamed
  hatten das nie, weil sie für Mobile schon immer ein eigenes,
  umbrechendes Badge-Grid (`.slim-badge`) statt einer Tabelle nutzen.
  Generische Clubs haben jetzt dieselbe Zwei-Renderer-Aufteilung
  (Desktop: Tabelle, Mobile: Badge-Grid) wie Themed/Teamed.

**FAQ-Text für Stats + Interview-Funktion ist geschrieben (Session 10),
aber noch NICHT ins Handbook eingebettet — wartet auf Owner-Freigabe.**
Beim nächsten Mal: prüfen ob freigegeben, dann in den `page-handbook`-Block
in `index.html` einbauen (Muster: bestehende `.faq-card`-Einträge dort).

## Wichtige Tabellen (Supabase)

- `drivers` — Overall-Ratings + `elo_inactive`-Flag; auch kanonische
  Namensbasis für Fahrer-Autocomplete (`/drivers/search`)
- `driver_track_ratings` — Track-Ratings (Frontend liest Track-Daten
  aktuell aber aus `elo_state.state_json`, nicht direkt aus dieser Tabelle)
- `elo_state` — einzige Zeile, kompletter Rating-Stand als JSON (~1,5 MB)
- `elo_history` — tägliche Overall-Snapshots für Δ 7D
- `championship_standings` (Session 10) — RaceNets eigener Punktestand
  pro Championship, `(championship_id, driver_name)` PK. Öffentlich
  lesbar (anon SELECT), Schreiben nur über den Sync.
- `stage_comments` (Session 10) — Stage-Interview-Kommentare,
  `(event_id, stage_id, driver_name)` UNIQUE (editierbar statt Duplikate).
  Öffentlich LESBAR, aber NICHT über den anon-Key beschreibbar — Schreiben
  läuft ausschließlich über die Backend-Endpunkte (prüfen den PIN vorher).
- `driver_pins` (Session 10) — PIN pro Fahrer, Klartext. **Komplett vom
  anon-Key abgeschottet** (kein RLS-Grant), nur Service-Role-Key kommt ran.
- `club_discord_webhooks` (Session 10) — Discord-Webhook-URL pro Club.
  Ebenfalls **komplett vom anon-Key abgeschottet**, wie `driver_pins`.
- RLS: `drivers`, `elo_history`, `elo_state`, `driver_track_ratings`,
  `championship_standings`, `stage_comments` haben `"public read"`
  (`FOR SELECT USING (true)`) für den `anon`-Key. `driver_pins` und
  `club_discord_webhooks` bewusst NICHT.

## Bekannte Stolperfallen

- **RaceNet-Feldpfade NIE unabhängig neu raten** (Session 9): `grf_sync.py`s
  `extract_dates()` ist die einzige geprüfte, funktionierende Referenz für
  Datum/Location-Parsing — `admin_api.py` importiert sie
  (`from grf_sync import extract_dates, parse_date`) statt sie zu
  duplizieren. Bei jedem neuen RaceNet-Feldzugriff: erst in `grf_sync.py`/
  `racenet_client.py` nachsehen, ob's das schon gibt, bevor man rät.
- **"Klasse" (vehicle_class) kommt NICHT zuverlässig von RaceNet** — ist eine
  rein owner-gepflegte Taxonomie (`vehicle_classes_data.py`), RaceNet kennt
  sie nicht in nutzbarer Form. Bleibt bewusst manuelle Admin-Auswahl im
  Championship Setup, kein Bug.
- **Versions-Badge** im Seitenkopf (`#site-version-badge` in `index.html`)
  ist reines statisches HTML, wird NICHT automatisch aus dem
  `CHANGELOG`-Array gezogen — bei jedem neuen Changelog-Eintrag separat von
  Hand anpassen. `renderChangelog()` selbst nimmt automatisch
  `CHANGELOG[0]` für die Home-/Handbook-Anzeige, nur das Badge im Header
  ist der manuelle Teil.
- **"Code ist nachweislich korrekt, aber Seite zeigt's nicht"** → zuerst
  Vercel-Deployments-Tab prüfen (Branch + Commit-Hash des
  Production-Deployments vs. letzter GitHub-Commit), bevor im Code gesucht
  wird. War in Session 8 schon einmal die Ursache (Branch-Mismatch).
- **Supabase Free Plan, Egress-Limit 5GB/Monat:** `/elo/update` hat früher
  bei JEDEM Sync (auch Delta) die komplette Event-Historie neu geladen →
  Egress-Notfall (89% verbraucht). Fix: Delta-Syncs (`force=False`) laden
  seit Session 8 nur noch Championships der letzten 90 Tage. `force_reset`
  lädt weiterhin alles. **Bei Egress-Problemen zuerst hier nachsehen.**
- **`CREATE OR REPLACE FUNCTION` mit geänderter Parameterliste ersetzt
  NICHT die alte Funktion, sondern legt eine zweite, überladene Version
  an** (Session 10, tatsächlich aufgetreten): beim Erweitern von
  `club_podium_stats`/`club_stage_win_stats` um einen zusätzlichen
  optionalen Parameter entstanden zwei parallele Funktionen mit
  demselben Namen — PostgREST konnte beim Aufruf mit weniger Argumenten
  nicht mehr eindeutig entscheiden, welche gemeint ist (PGRST203-
  Mehrdeutigkeitsfehler), was sich im Frontend als leises "No Results
  Yet" tarnte, nicht als sichtbarer Fehler. **Fix: bei jeder Änderung an
  einer Postgres-Funktionssignatur immer erst `DROP FUNCTION IF EXISTS
  <name>(<alte Parameterliste>);` ausführen, dann erst neu anlegen.**
  Verifikation: `select proname, pronargs from pg_proc where proname =
  '...'` sollte danach genau eine Zeile pro Funktionsname zeigen.
- **RaceNet-Client-Methoden können eingebaute, stille Limits haben, auch
  wenn die Methode selbst Pagination unterstützt** (Session 10,
  tatsächlich aufgetreten): `get_championship_standings()` hatte einen
  `max_results=100`-Default-Parameter, der die (funktionierende!)
  Cursor-Pagination nach der ersten Seite abbrechen ließ — sichtbar nur
  als auffällig viele exakt gleiche "100 driver(s)"-Log-Zeilen bei
  größeren Championships. Fix: Aufrufer muss `max_results` explizit
  hoch genug setzen (aktuell 10000). **Bei jeder Nutzung einer
  RaceNet-Client-Methode mit einem `max_results`-artigen Parameter:
  Default-Wert im Client-Code prüfen, nicht blind vertrauen, dass "die
  Methode ja paginiert" automatisch bedeutet, dass sie auch alles holt.**
- **jsdom (Test-Sandbox) bildet nicht jede Browser-Eigenheit korrekt ab**
  (Session 10): `.focus()` auf ein Element innerhalb eines
  `display:none`-Elternteils schlägt in echten Browsern fehl (Element
  nicht fokussierbar), in jsdom aber nicht — ein entsprechender
  automatisierter Test wäre fälschlich grün, obwohl der Bug in echten
  mobilen Browsern real war. Bei fokus-/sichtbarkeitsbezogenen Bugs im
  Zweifel auf reales Geräte-Feedback verlassen, nicht nur auf jsdom-Tests.
- **RaceNet-Refresh-Token läuft bewusst nur 24h** (untypisch kurz für
  einen Refresh-Token — genau deshalb existiert der ganze
  Auto-Rotations-Mechanismus über `system_config.racenet_refresh_token`).
  Läuft die Rotation einmal aus (z.B. weil ein Sync länger fehlgeschlagen
  ist), braucht's eine einmalige manuelle Erneuerung. **Zwei Dinge dabei
  leicht übersehen (Session 10, tatsächlich passiert):**
  1. **ZWEI Railway-Services nutzen `racenet_client.py` und brauchen BEIDE
     den frischen Cookie-Wert in ihrer eigenen `RACENET_REFRESH_TOKEN`-
     Variable** — nicht nur `grf_sync.py`, auch `admin_api.py` (importiert
     `RacenetClient` direkt, für Admin-Panel-Funktionen wie den Car Rating
     Calculator). Nur eine der beiden Variablen zu aktualisieren reicht
     nicht.
  2. **`RACENET_TOKEN_RESET=1` darf immer nur bei EINEM der beiden
     Services gleichzeitig gesetzt sein, nie bei beiden.** Mit `RESET=1`
     ignoriert der Bootstrap bewusst Supabases bereits rotierten Token und
     erzwingt stattdessen den statischen Env-Var-Wert — haben beide
     Services das gleichzeitig aktiv, überschreibt sich das gegenseitig
     (wer zuletzt startet, wirft die frische Rotation des anderen wieder
     raus und erzwingt den alten, bei RaceNet schon "verbrauchten" Wert
     zurück). Vorgehen: `RESET=1` nur bei `grf_sync.py` setzen (läuft
     zuverlässig alle 10 Min), `admin_api.py` NICHT anfassen — holt sich
     den frisch rotierten Wert danach automatisch selbst aus Supabase.
     Sobald ein Lauf erfolgreich war: `RACENET_TOKEN_RESET` auch bei
     `grf_sync.py` wieder auf 0/löschen, sonst wird bei jedem künftigen
     Neustart wieder der alte statische Wert erzwungen statt der
     automatischen Rotation zu vertrauen.
  3. **Symptom bei falsch kopiertem/schon wieder rotiertem Cookie-Wert:
     HTTP 204 (nicht 400/401) beim Refresh-Versuch** — kam in der Praxis
     vor, wenn der Browser-Tab zwischen Kopieren und Einfügen noch im
     Hintergrund mit racenet.com kommuniziert und den Cookie dadurch
     bereits erneut rotiert hat, bevor Railway ihn verwenden konnte. Fix:
     alle anderen RaceNet-Sessions/Tabs/Geräte vorher schließen, dann
     kopieren → sofort einfügen → sofort redeployen, ohne Pause dazwischen.

  **Mutmaßliche Ursache des konkreten Vorfalls (zwei Theorien, beide
  plausibel, nicht zweifelsfrei unterscheidbar):** (a) die zeitgleiche
  Supabase-"Intermittent latency in Eastern US"-Störung ließ vermutlich
  einen routinemäßigen Token-Rotations-Schreibvorgang scheitern — da jeder
  Cron-Lauf in einem frischen, flüchtigen Container startet (kein lokaler
  Fallback, Supabase ist der EINZIGE Gedächtnis-Ort), macht ein einziger
  verpasster Schreibvorgang den in Supabase gespeicherten Token sofort
  wertlos (RaceNet rotiert bei jeder Nutzung, der alte Wert ist dann schon
  "verbraucht"). (b) Ein manueller Browser-Login desselben Owners in den
  automatisierten Account kann unabhängig davon ebenfalls eine Rotation
  ausgelöst haben, die dem Skript "den Boden unter den Füßen wegzieht".
  **Resilienz-Fix (Session 10):** `_supabase_save_refresh_token()` hatte
  bisher nur einen einzigen Versuch mit 5s Timeout — jetzt 3 Versuche mit
  Backoff (2s/4s) und 10s Timeout, plus eine unübersehbare
  "❌ ACHTUNG"-Logzeile, falls auch das scheitert. Macht das System robuster
  gegen genau solche kurzen Supabase-Latenzspitzen, behebt aber nicht
  Theorie (b) — ein manueller Login in den automatisierten Account bleibt
  ein Risiko, nach Möglichkeit vermeiden.

## Bewusst NICHT übernommene Ansätze

TrueSkill, Glicko-2, rollierendes Form-Fenster, kompletter Sigma-Verzicht
(getestet: führt zu Überreaktion/Instabilität statt mehr Fairness),
kombinierte Track-Filter gleichzeitig (Engine unterstützt das strukturell
nicht — jeder Track ist unabhängig). Für die Interview-Funktion: echtes
Login/OAuth (PIN-System reicht der Community-Größe), PIN-Hashing (Klartext
+ abgeschottete Tabelle reicht), voller Discord-Bot statt einfachem
Webhook (kein Bedarf an Zwei-Wege-Interaktion).

## Championship Setup — Zielbild: 3 Modi (Owner-Vorgabe, Session 9)

Admin → Championship Setup soll perspektivisch klar in drei Modi getrennt
sein, mit unterschiedlichen Untermenüs. Aktueller Bau-Status unverändert
seit Session 9:

1. **Classic** (normales Themed, ein Club) — größtenteils fertig: Name,
   Best-of, Klasse (manuell), CR-Set-Zuweisung, Bonus-Regeln, Narrative
   funktionieren.
2. **Teams** (wie Classic, plus Team-Erstellung, nur für den Teamed-Club) —
   Team-Erstellung funktioniert. **Nachträgliches Bearbeiten bestehender
   Teams ist noch nicht getestet worden** — offener Punkt.
3. **Multiclass** (zwei parallele Championships/Clubs, gemeinsame Wertung) —
   **komplett unbegonnen**, technisch unklar wie zu lösen.

## Offene Punkte

1. **Δ 7 Tage für Track-Ansichten** existiert nicht — `elo_history` hat
   keine Track-Dimension.
2. **`/stats/pageview` wirft CORS-Fehler**, Seitenaufruf-Zähler zeigt "—".
   Vermutung: `ALLOWED_ORIGIN`-Env-Var auf Railway stimmt nicht exakt mit
   Produktions-Domain überein. Noch nicht verifiziert.
3. **Draft-basiertes Team-Format** für Team-Events — großes, in Session 8
   durchgesprochenes Vorhaben. Owner-Plan: erst ein analoger Testlauf mit
   den Captains, bevor irgendetwas gebaut wird. Noch nicht begonnen.
4. **Teams nachträglich bearbeiten** (Championship Setup, Teams-Modus) —
   Erstellung funktioniert, Bearbeiten noch nicht getestet.
5. **Multiclass-Modus** (Championship Setup) — komplett unbegonnen, braucht
   eigene Planungsrunde.
6. **Δ7-Snapshot an CET/Club-Wochenrhythmus ausrichten** statt UTC-
   Kalendertag — Design-Entscheidung, noch nicht umgesetzt.
7. **Ressourceneffizienz beim 10-Minuten-Cron** — Owner will das
   irgendwann besprechen, noch keine Details.
8. **Hall of Fame** — eigener, gleichrangiger Top-Level-Nav-Punkt für
   statische/All-Time-Rekorde (Idee seit Session 10, bewusst
   zurückgestellt). Soll laut Owner auch club-spezifische Rekorde zeigen
   können, nicht nur ELO — passt deshalb vermutlich nicht als reiner
   Sub-Tab unter "ELO Rankings". Noch nicht begonnen, eigene
   Planungsrunde nötig.
9. **Discord-Webhooks für die restlichen Clubs eintragen** (Session 10) —
   Infrastruktur steht (`club_discord_webhooks`, Admin-Panel-Verwaltung),
   aber nur ein Test-Club hat aktuell einen eigenen Kanal konfiguriert.
   Für jeden weiteren Club: Webhook im jeweiligen Discord-Kanal anlegen,
   URL im Admin-Panel unter "Interviews" eintragen.
10. **FAQ-Text für Stats + Interview-Funktion** ist geschrieben, wartet
    auf Owner-Freigabe zum Einbetten ins Handbook (s. Abschnitt
    "Stage-Interview-Funktion" oben für Details/Fundstelle).
11. **Kommentar-Lese-Ansicht auf der Website** — aktuell landen Interview-
    Kommentare nirgends sichtbar auf der Seite selbst, nur über Kopieren/
    Discord. Kein aktiver Auftrag dafür, aber als mögliche künftige
    Ergänzung im Hinterkopf behalten, falls gewünscht.
12. **Status-Kacheln** (Supabase OK / RaceNet OK / Event Active) und die
    "LIVE"-Kachel oben rechts sind weiterhin reine Platzhalter ohne
    Funktion — unverändert seit Session 9, noch keine Entscheidung vom
    Owner, was damit passieren soll.

## Multi-Club-Ausbau — Status: ABGESCHLOSSEN (Session 10)

Auslöser war ursprünglich ein anderer Club-Admin ("GRF Daily"), der um
Club-Statistiken gebeten hatte — daraus wurde ein großer, mehrteiliger
Umbau der gesamten Seite. Wichtige Klärung, die die ganze Architektur
prägt: **"GRF" (Global Rally Fans) ist die Community/der Discord-Server,
der alle ~11 Clubs (inkl. "GRF Themed" und "GRF Teamed") hostet — nicht
ein Club, der andere optional mit einbindet.** Alle Clubs sind technisch
gleichberechtigt.

Was gebaut wurde (in dieser Reihenfolge):

1. **PWA-Grundgerüst** — installierbar (Homescreen-Icon), kein Browser-
   Chrome. Bewusst kein Offline-Caching der Daten (Live-Ergebnisse/ELO
   sind per Definition aktuell).
2. **Hash-basiertes URL-Routing** — jede Seite/jeder Sub-Tab hat einen
   eigenen, teilbaren Link (`#clubs/<id>/<tab>`), domain-unabhängig.
3. **Club-Namen-API** — `/elo/clubs` liefert live Namen von RaceNet,
   Encoding-Sorgen unbegründet (kein Mapping-Tabelle nötig geworden).
4. **Navigations-Umbau** — "GRF Themed"/"GRF Teamed" haben keinen
   bevorzugten Platz mehr in der Top-Nav, neuer gleichrangiger
   "Clubs"-Einstiegspunkt für alle Clubs.
5. **Home-Redesign** — mobile-first, Club Directory statt alter,
   funktionsloser Kacheln, Most Improved auf 10 erweitert.
6. **Generisches Club-Template** — ein Renderer für alle ~9 schlanken
   Clubs (Live Results, Stats, Standings, optional Rules), Themed/Teamed
   behalten ihren eigenen, unangetasteten Code.
7. **Club-Stats** — s. eigener Abschnitt oben, deutlich über die
   ursprüngliche Anfrage hinausgewachsen (3-fach-Umschalter,
   Championship-Ebene, Custom-Scoring-Sonderfall).
8. **Custom-Punktesysteme pro Club** — s. eigener Abschnitt oben.
9. **Stage-Interview-Funktion** — s. eigener Abschnitt oben, größtes
   Einzelfeature dieser Session.

**Bewusst zurückgestellt, nicht Teil dieses Umbaus:** Hall of Fame (s.
Offene Punkte).

## Hosting-Historie (für Kontext, kein offener Punkt)

Backend lief zunächst auf Railway Trial → Trial lief nach 30 Tagen aus,
Service wurde automatisch gestoppt → auf Hobby-Plan ($5/Monat) umgestellt.
Ein Umbau auf eine andere Plattform (z.B. Google Cloud Run, wegen $0-Kosten
bei diesem Traffic-Volumen) ist nicht ausgeschlossen, aber nicht akut
geplant — siehe Einschränkung zu `/elo/update` oben, falls das jemals
angegangen wird.
