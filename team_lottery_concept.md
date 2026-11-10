# Team-Lotterie / Team-Reveal (Konzept, Stand 10.11.2026)

Status: Alle Punkte wurden mit dem Owner abgestimmt.
**Gebaut:** Anmeldung + Wunsch-Eingabe (team_signup.sql, team_signup.py, Sign-up-Modal auf der
Teamed-Seite, Admin-Tab "Team Sign-up" inkl. Gruppenerkennung), erwartete Punkte + Balancing
(team_lottery.py), Admin-Endpunkte Vorschau/Speichern (team_draw.py, team_draw.sql),
**Wunsch-Teamgröße im Admin-Setup (10.11.2026, team_size.sql)**.
**Noch nicht gebaut:** Reveal-Animation im Produkt (nur Demo), Teamnamen ändern ("Show team"),
Placement-Event-Logik.

## Ziel
Teams im Club (Themed) automatisch auslosen, mit Blick auf ELO/Ausgleich, und Spielern
ein Mitspracherecht geben. Die Auslosung ist zugleich der Team-Reveal (Website-Tool).

## Teamgrößen und Teamanzahl (neu geregelt 10.11.2026)
- **Die Teamanzahl ist nicht festgelegt, sondern ergibt sich aus der Teilnehmerzahl.** Es gibt
  keine Obergrenze (weder 8 noch 25). Technisches Limit nur 100 Anmeldungen (Rechenzeit).
- **Wunsch-Teamgröße (`pref`)** stellt der Admin pro Championship im Tab "Team Sign-up" ein
  (`championships.team_size_pref`, 2–8, Standard 3). Typisch 20–30 Fahrer, zuletzt 8 Teams à 3.
- **Normal erlaubt: pref −1 bis pref +1** (bei 4: 3er, 4er, 5er). **pref ±2 nur im äußersten Notfall**
  (hohe Strafe im Algorithmus, in der Vorschau als Warnung sichtbar; nie unter 2 Spieler).
  Bei pref 4 also 2er/6er nur, wenn der Ausgleich sonst nicht geht.
- Teamanzahl: der Algorithmus probiert die plausiblen Anzahlen rund um n/pref (nur solche, bei denen
  alle Teams im ±1-Band liegen können) und nimmt die beste Aufteilung. Beispiel pref 3, 20 Fahrer:
  6 Teams (3,3,3,3,4,4) oder 7 Teams (3×6 + ein 2er), je nachdem, was besser ausgleicht.
- Feste Wunschgruppen (Kreise) dürfen höchstens **pref + 1** Spieler haben (pref 3 → 4, pref 4 → 5).
- Mindestteilnehmer: 2 × max(2, pref − 1) (pref 3 → 4, pref 4 → 6).
- **Alle Fahrer zählen** im Team (keine „beste 3"-Regel), damit sich niemand ausgeschlossen fühlt.

## Ausgleich: erwartete Punkte, nicht ELO-Summe
- Grund: Punktetabelle (50, 44, 40, 38, … 6 ab Platz 30, × CR) ist oben steil. Ein starkes
  Duo auf P1+P2 holt 94 Punkte; ein 4er-Team auf Platz 12–15 kommt auf 24+23+22+21 = 90.
- Pro Fahrer: aus Mu/Sigma (elo_state) das Event oft simulieren → durchschnittlicher Platz →
  erwartete Punkte aus der Tabelle (Feldgröße fließt ein).
- Teamwert = Summe der erwarteten Punkte der Mitglieder.
- **Toleranz: 10 %** zwischen den Teams (Tagesform, Locations, Rally ist unvorhersehbar).
- CR-Faktor fließt nicht in den Ausgleich ein (Fahrer wählen ihr Auto selbst).
- Das Risiko, dass zwei sehr starke Fahrer als Duo stärker sind als gedacht, wird vorher
  kommuniziert und ist bewusst Teil des Spiels: Zwei sehr starke Fahrer, die sich wählen,
  bilden ein 2er-Team (und treffen auf größere Teams).
- Lässt sich eine feste Gruppe (Kette) auch bei bestem Ausgleich nicht in 10 % einordnen, werden
  die Wünsche dieser Gruppe aufgelöst und die Auslosung entscheidet; der Admin wird dabei
  informiert. Beides steht im Info-Text vor der Wunsch-Eingabe.

## Neue Spieler (kein ELO)
- Verpflichtendes **Placement-Event** vor der Auslosung (Selbsteinschätzung verworfen,
  weil Anreiz zum Tiefstapeln).
- Referenz-Stages, auf denen viele bekannte Fahrer Zeiten haben; Zeit wird eingeordnet →
  vorläufiges Rating mit hohem Sigma.
- **Festgelegt 10.11.2026 (Owner):** 3 Placement-Stages, alle trocken:
  1. Portugal – Celeiro – Rally3 · 2. Rally Iberia – Les Voltes – F2 Kit Car ·
  3. FANATEC Rally Oceania – Mangapai – Group B (RWD).
- **Autowahl frei:** jeder Fahrer nimmt das bekannte schnellste oder das Auto, in dem er sich wohlfühlt
  (v. a. Group B: schnellstes Auto = schwierigstes). Die Einordnung muss daher autokorrigiert sein
  (CR-/Stage-Faktor pro Auto wie im CR-Calculator, `_compute_stage_factors`) bzw. gegen Zeiten
  desselben Autos laufen, nicht pauschal gegen die ganze Klasse.
- **Datenquelle:** RaceNet-Time-Trial-Leaderboards `TIME_TRIAL_R{route}_VC{class}_{DRY|WET}`,
  nicht die Club-Tabelle `stages` (dort stehen diese Stages nicht, bei Club-Events teils nur Ziffern).
  Namen -> IDs: `placement_resolve.py` (`/admin/placement/resolve`, nutzt RaceNet `/values`).
- **2er-Teams nur für Fahrer mit gesichertem Rating.**
- Nebeneffekt: Erst nach dem Placement steht der Fahrer in `drivers` (nötig für die Namenssuche)
  und bekommt seinen PIN.
- Bis dahin: Standard-Rating 1500 / σ 350, in der Vorschau als "unrated" markiert.

## Anmeldung und Wünsche (Zugang über den vorhandenen Fahrer-PIN, kein Account)
- Gleiches Muster wie die Stage-Interview-Funktion: PIN pro Fahrer (`driver_pins`),
  Name kanonisch gegen `drivers` auflösen, Schreiben nur über Backend-Endpunkte mit PIN-Prüfung.
- **Anmeldung zum Event per Button**, authentifiziert über den PIN.
- **Genau ein Wunsch pro Fahrer.** Eingabe wie die ELO-Suche (Namensteil → Trefferliste →
  Klick → Bestätigen), Status daneben („angemeldet" / „noch nicht angemeldet").
- Wunsch auf nicht angemeldeten Fahrer erlaubt; zählt nur, wenn der Partner bis zur
  Deadline angemeldet ist (wird erst bei der Auslosung ausgewertet). Meldet sich ein Partner
  wieder ab, löst sich die Gruppe automatisch auf.
- Wunsch bis zur Deadline änderbar/zurückziehbar. Kein Wunsch auf sich selbst.
- Ob ein Wunsch gegenseitig ist, wird dem Spieler **nicht** angezeigt (erst beim Reveal),
  sonst werden Wünsche nach Einsicht umgebaut. Absprache untereinander ist erwünscht.
- Keine negativen Wünsche („nicht mit X").
- Neue Tabelle `team_wishes`, **nicht über anon lesbar** (Wünsche geheim bis Reveal);
  wegen Supabase-Änderung ab 30.10.2026 nur `service_role`-Grant.

## Gruppenerkennung (jeder Fahrer hat genau eine ausgehende Verbindung)
- **Kreis der Länge 2 bis pref + 1** (A↔B, A→B→C→A, …): feste Gruppe.
- **Längere Kreise sind verboten**: Info vor der Eingabe (keine Fehlermeldung beim Eingeben,
  der Text nennt die aktuelle Grenze); werden sie trotzdem eingegeben, zählen sie als weiche Wünsche.
- **Einseitige Wünsche** (nicht in einem Kreis, z. B. C→A, A→B): weich.

## Reihenfolge im Algorithmus
1. Feste Gruppen (Kreise) stehen als Einheiten fest.
2. Teamanzahl, Teamgrößen und Ausgleich über erwartete Punkte (10 %).
3. Mehrere gültige Aufteilungen mit festem Seed erzeugen, daraus die mit den meisten
   erfüllten einseitigen Wünschen wählen (nur wenn der Ausgleich nicht verletzt wird).

## Fairness und Reveal
- **Vorher berechnen, live aufdecken**, kein Live-Würfeln (sonst „nochmal würfeln"-Diskussion).
- Fester, öffentlicher Seed (z. B. aus Anmeldeliste + vorher angekündigter Zahl).
- Rechnen im Backend (`team_draw.py`), speichern über vorhandene Team-Tabellen
  (`teams`, `team_members`). Website spielt nur die Reveal-Animation ab.

## Offen
- ELO-Basis: derzeit Overall (nicht klassenspezifisch AWD/FWD) — Entscheidung steht noch aus.
- Deadline-Regeln (was passiert bei Abmeldung nach der Deadline, Nachmelder).
- Maximale Teilnehmerzahl: technisch 100 (Rechenzeit); fachlich keine Grenze festgelegt.
- Placement-Event: Stage-Namen kommen vom Owner.
- Reveal-Animation ins Produkt übernehmen, "Show team" + Teamnamen ändern.

## Backtest (20.10.2026, 4 S1600-Events, 19–21 Fahrer, ELO overall)
- `team_lottery.py` gebaut (expected_points + draw). Prognostizierte Abweichung der Teams: 0,3–11 %.
- Tatsaechliche Abweichung (max. Teamsumme vs. Schnitt) laut echten Ergebnissen: 24–40 %,
  Zufallsteams: 32–40 %. Rauschgrenze (Modell perfekt, nur Event-Zufall): Median 23–28 %.
  => Auslosung ist so gut wie moeglich; die 10 %-Toleranz gilt fuer die ERWARTETE Staerke, nicht
  fuer das Einzelergebnis. Kommunikation: "gleich stark im Erwartungswert, nicht im Ergebnis".
- Starke Duos sind nicht zwingend 2er-Teams; sie werden aufgefuellt, wenn es dem Ausgleich dient (Owner).

## Reveal-Animation (abgestimmt 21.10.2026, Demo: claude.ai/artifact/4AoM91zgne7otYnHyLCMD6)
- **Ablauf:** Live durch den Admin gestartet (läuft automatisch im eingestellten Tempo, Pause/Skip), später für alle als Wiederholung abrufbar. Der Server liefert nur so viele Zuordnungen, wie bis jetzt aufgedeckt sind.
- **Optik:** GRF-/Win98-Stil (VT323, anthrazit, Bevel-Rahmen, Bewegung in harten `steps()`).
- **Layout:** Ein Bildschirm, nie Seiten-Scroll. 1/3 Pool (alle Namen als Kacheln, gezogene wandern ans Ende und werden grau), 2/3 Teams, die IMMER komplett sichtbar sind (Spalten + Schriftgröße werden aus Teamanzahl und Fläche berechnet — die Teamanzahl ist dynamisch, nicht fest). Der Name-Banner schwebt über dem Pool. Menü versteckt hinter Zahnrad, Zähler in der Titelleiste.
- **Ziehung:** Zufällige Reihenfolge der Spieler. Banner-Trommel -> Name rastet ein -> Rahmen springt ZUFÄLLIG über die Teams (nie zweimal dasselbe Team hintereinander, Start weit vom Ziel, letzte 3 Sprünge langsam als Beinahe-Treffer) -> Name fliegt in Pixelschritten ins Team, Karte wächst. Letzte 3 Ziehungen bewusst langsamer.
- **Bewusst NICHT:** "Wunsch erfüllt"-Banner (keine Hervorhebung von Gruppen, niemand soll sich als "zuletzt gewählt" fühlen), Live-Emoji-Reaktionen, Sound (höchstens als Schalter), Hash-Veröffentlichung. Plätze leer/ohne Größenangabe: Karten wachsen.
- **Teambezeichnung zu Beginn:** statt "Team 1, Team 2 ..." wird die TEAMFARBE als Platzhalter-Name verwendet (z. B. "Team Pink"). 25 Farben sind hinterlegt; bei mehr Teams werden weitere Runden nummeriert ("Team Pink 2"), die Namen bleiben eindeutig (`team_draw.team_label`). Keine Teamobergrenze.
- **Teamnamen danach:** Button "Show team" (mit PIN), jedes Mitglied kann den Namen ändern (Längenbegrenzung, Zeichenfilter, Admin kann überschreiben). Vorher Struktur der Tabelle `teams` prüfen. Noch nicht gebaut.
