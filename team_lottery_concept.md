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
