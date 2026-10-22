# Team-Lotterie / Team-Reveal (Konzept, Stand 20.10.2026)

Status: Alle Punkte wurden mit dem Owner abgestimmt.
**Gebaut (20.10.2026):** Anmeldung + Wunsch-Eingabe (team_signup.sql, team_signup.py,
Sign-up-Modal auf der Teamed-Seite, Admin-Tab "Team Sign-up" inkl. Gruppenerkennung).
**Noch nicht gebaut:** Berechnung der erwarteten Punkte (Teil 1), Auslosung/Balancing (Teil 2),
Reveal-Animation, Placement-Event-Logik. Nächster Schritt: Simulation an einem echten Feld
(braucht elo_state-Export + Endplatzierungen früherer Events).

## Ziel
Teams im Club (Themed) automatisch auslosen, mit Blick auf ELO/Ausgleich, und Spielern
ein Mitspracherecht geben. Die Auslosung ist zugleich der Team-Reveal (Website-Tool).

## Teamgrößen
- Typisch 20–30 Fahrer, zuletzt 8 Teams à 3.
- Teamgröße wird erst festgelegt, wenn die Teilnehmerzahl feststeht.
- **Variable Größen 2, 3 oder 4.** So viele 3er wie möglich; 2er und 4er nur, wenn
  Wunschgruppen oder Ausgleich es erfordern. Größenunterschied höchstens 1 (3 ± 1).
- Jede Teilnehmerzahl ab 6 ist damit abgedeckt. Owner als Lückenfüller nur Notlösung.
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
- Lässt sich eine feste 3er-/4er-Gruppe (Kette) auch bei bestem Ausgleich nicht in 10 %
  einordnen, werden die Wünsche dieser Gruppe aufgelöst und die Auslosung entscheidet;
  der Admin wird dabei informiert. Beides steht im Info-Text vor der Wunsch-Eingabe.

## Neue Spieler (kein ELO)
- Verpflichtendes **Placement-Event** vor der Auslosung (Selbsteinschätzung verworfen,
  weil Anreiz zum Tiefstapeln).
- Referenz-Stage, auf dem viele bekannte Fahrer Zeiten haben; Neuer fährt ihn im selben Auto →
  Zeit wird eingeordnet → vorläufiges Rating mit hohem Sigma.
- **2er-Teams nur für Fahrer mit gesichertem Rating.**
- Nebeneffekt: Erst nach dem Placement steht der Fahrer in `drivers` (nötig für die Namenssuche)
  und bekommt seinen PIN.

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
- Neue Tabelle, z. B. `team_wishes`, **nicht über anon lesbar** (Wünsche geheim bis Reveal);
  wegen Supabase-Änderung ab 30.10.2026 nur `service_role`-Grant.

## Gruppenerkennung (jeder Fahrer hat genau eine ausgehende Verbindung)
- **Kreis der Länge 2** (A↔B): festes Duo.
- **Kreis der Länge 3** (A→B→C→A): festes Trio.
- **Kreis der Länge 4:** festes Vierer-Team.
- **Kreise ab 5 sind verboten**: Info vor der Eingabe (keine Fehlermeldung beim Eingeben);
  werden sie trotzdem eingegeben, zählen sie als weiche Wünsche.
- **Einseitige Wünsche** (nicht in einem Kreis, z. B. C→A, A→B): weich.

## Reihenfolge im Algorithmus
1. Feste Gruppen (Kreise) stehen als Einheiten fest.
2. Teamgrößen und Ausgleich über erwartete Punkte (10 %).
3. Mehrere gültige Aufteilungen mit festem Seed erzeugen, daraus die mit den meisten
   erfüllten einseitigen Wünschen wählen (nur wenn der Ausgleich nicht verletzt wird).

## Fairness und Reveal
- **Vorher berechnen, live aufdecken**, kein Live-Würfeln (sonst „nochmal würfeln"-Diskussion).
- Fester, öffentlicher Seed (z. B. aus Anmeldeliste + vorher angekündigter Zahl).
- Hash des Ergebnisses vor dem Reveal veröffentlichen, danach nachprüfbar.
- Rechnen im Backend (`admin_api.py`), speichern über vorhandene Team-Tabellen
  (`teams`, `team_members`, `/teams/save`). Website spielt nur die Reveal-Animation ab.

## Offen
- ELO-Basis: Overall oder klassenspezifisch (AWD/FWD)?
- Deadline-Regeln, Mindest-/Maximalteilnehmerzahl.
- Simulation am echten Feld (24 Fahrer) zur Prüfung, ob 10 % realistisch sind.

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
- **Layout:** Ein Bildschirm, nie Seiten-Scroll. 1/3 Pool (alle Namen als Kacheln, gezogene wandern ans Ende und werden grau), 2/3 Teams, die IMMER komplett sichtbar sind (Spalten + Schriftgröße werden aus Teamanzahl und Fläche berechnet). Der Name-Banner schwebt über dem Pool. Menü versteckt hinter Zahnrad, Zähler in der Titelleiste.
- **Ziehung:** Zufällige Reihenfolge der Spieler. Banner-Trommel -> Name rastet ein -> Rahmen springt ZUFÄLLIG über die Teams (nie zweimal dasselbe Team hintereinander, Start weit vom Ziel, letzte 3 Sprünge langsam als Beinahe-Treffer) -> Name fliegt in Pixelschritten ins Team, Karte wächst. Letzte 3 Ziehungen bewusst langsamer.
- **Bewusst NICHT:** "Wunsch erfüllt"-Banner (keine Hervorhebung von Gruppen, niemand soll sich als "zuletzt gewählt" fühlen), Live-Emoji-Reaktionen, Sound (höchstens als Schalter), Hash-Veröffentlichung. Plätze leer/ohne Größenangabe: Karten wachsen.
- **Teambezeichnung zu Beginn:** statt "Team 1, Team 2 ..." heißt jedes Team "Team [Farbe]" (z. B. "Team Pink", "Team Hellblau", "Team Gold"). Dann ist es für Spieler, die ihren Teamnamen nie ändern, trotzdem eindeutig. Genug Farben vorhanden (Hell-/Dunkelblau, Gold, Silber, Weiß, ...) — die Demo nutzt aktuell noch 8 Farben + "Team N" und muss darauf umgestellt werden.
- **Teamnamen danach:** Button "Show team" (mit PIN), jedes Mitglied kann den Namen ändern (Längenbegrenzung, Zeichenfilter, Admin kann überschreiben). Vorher Struktur der Tabelle `teams` prüfen.
