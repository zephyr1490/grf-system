# CR: Fahrerpool-Effekt — Befund 2026-10-20 (Monte Carlo, S2000 vs S1600)

Datenbasis: Detail-Excel (2 Stages: La Bollène-Vésubie Col de Turini + Peïra Cava,
Threshold 4, min N 10, Min rank 3, Exp 5) und Referenzzeiten EINES guten Fahrers,
trocken + nass, alle 8 Autos auf denselben 2 Stages. Leaderboard = DRY.

## Befund
- Der Referenzfahrer liegt in den Boards von 206, 207, Fiat, C2 auf Rang 1-3 (teils
  schneller als die Bestzeit des Boards), in den Boards von Opel, Saxo, Clio nur auf
  Rang 4-8. Dort stehen also Spitzenfahrer, die in den anderen Autos fehlen.
  Bestzeit Col de Turini: Opel 466,4 s, Saxo 472,6, Clio 473,5, 206 479,3, 207 480,1,
  Fiat 480,8, Puma 483,7, C2 495,2.
- Echte Autounterschiede (Fahrer, trocken, Mittel beider Stages, Zeitrueckstand zum
  Opel): 207 0,3 %, 206 0,4 %, Fiat 0,5 %, Clio 1,0 %, Saxo 1,05 %, Puma 1,8 %, C2 2,8 %.
  Leaderboard-CR (Thr 4): 206 1,3 %, 207 1,8 %, Clio 2,3 %, Fiat 2,8 %, Saxo 2,9 %,
  Puma 3,9 %, C2 4,7 %.
- Sweep ueber Threshold 0,5-70 auf den Rohdaten: KEIN Wert reproduziert die Fahrerdaten
  (kleinster RMSE 1,4 Prozentpunkte bei Thr 2). Fiat liegt bei jedem Threshold zu weit
  hinten (2,4-6,3 % statt 0,5 %). Kein Regler behebt das; Ursache ist die
  Zusammensetzung der Boards.
- Min rank hatte hier keinen Einfluss (alle N >= 366 -> Referenzrang >= 15).
- Gruppe A gegen RWD (grosse echte Unterschiede) wird dagegen sauber getrennt.

## Folgerung
- CR aus Leaderboards ist fuer Autos mit aehnlich starkem Spitzenfeld zuverlaessig,
  nicht fuer Autos, deren Spitzenfahrer fehlen (hier: Fiat, 207, 206, C2).
- Ausweg: Referenzfahrer-Zeiten als Eich-/Override-Quelle (CR = (1+Rueckstand)^Exp).
  Ein Fahrer, zwei Stages, nur Asphalt: nur Plausibilitaetscheck, keine Eichung.

## Nachtrag: Algorithmus-Test auf den Rohdaten (2026-10-20)
- Vergleich ZENTRIERT (Opel-Offset herausgerechnet), Ziel = Fahrerzeiten trocken.
  Nullmodell (alle Autos gleich) RMSE 0,87 Prozentpunkte. Alle Perzentil-/Mittel-Varianten
  mit kleinem x (Rang 2-8 %, Mittel Top 5 %) liegen bei 0,7; Median/breite Baender
  deutlich schlechter (1,5); absolute Raenge schlechter. Rangfolge (Spearman) bis 0,95.
- Entscheidend: nur Eintraege OHNE Assists (assistFlags == [0]) verbessern alle
  Varianten (Rang 4 %: 0,74 -> 0,59; Median 1,48 -> 0,92). Assist-Anteil unterscheidet sich
  je Auto (Opel 54 % ohne Assists, alle anderen 44-47 %): Opel-Feld ist haerter besetzt.
- Verbleibende Ausreisser: Opel zu schnell (~-1,0), Fiat zu langsam (~+0,7). Kein
  Parameter loest das; weiteres Feintuning waere Overfitting (16 Datenpunkte, 1 Fahrer).
- usingCustomTuning ist in allen Eintraegen False (kein Filtereffekt). Plattform-Filter
  bringt nichts.
- Umgesetzt: Option "Only entries without assists" im CR-Calculator (Standard aus).

## Nachtrag 2: Paarvergleich ueber gemeinsame Fahrer (Monte Carlo, Asphalt, 2 Stages)
Fahrer, die in beiden Autos eine Zeit haben (nur S2000 gegen S1600 moeglich, ein Fahrer steht
pro Klasse nur einmal im Board). Median Zeit A/B - 1, negativ = A schneller:
Opel vs Clio -0,84 % (n=141), Opel vs Saxo -0,93 % (133), 207 vs Clio +0,01 % (461),
207 vs Saxo -0,13 % (378), 207 vs 206 +0,45 % (156), Fiat vs Clio +0,54 % (101),
Fiat vs Saxo +0,40 % (83), 207 vs C2 -1,5 %, Opel vs C2 -2,0 %.
- Auch mit denselben Fahrern liegt der 207 nur gleichauf mit Clio/Saxo; nur der Opel ist
  rund 0,8-0,9 % schneller. AWD-Vorteil auf Asphalt ist klein.
- Der Fiat ist mit denselben Fahrern ~0,4-0,5 % LANGSAMER als Clio/Saxo (der Testfahrer hatte
  ihn davor). Die CR (Thr 4: Fiat 2,8 %, Clio 2,3 %) trifft das ordentlich.
- Neues Blatt "Paarvergleich" im Detail-Excel; fuer Schotter/Schnee noch nicht ausgewertet.

## Nachtrag 3: Portugal (Schotter, 6 Stages) + Paarlevels als Ziel (2026-10-20)
Ziel = Level je Auto aus allen Paarvergleichen ueber gemeinsame Fahrer (LSQ, gewichtet nach n),
nicht die Zeiten des Testfahrers. Vergleich der Reihenfolge (Spearman) je Verfahren:
- Monte Carlo Asphalt (15 Paare, bis 461 gemeinsame Fahrer): Rang 2-25 % ergibt Reihenfolge
  fast/ganz identisch zum Paarlevel (0,98-1,0); Median/Rang 40 % schlechter. Assist-Filter
  hilft leicht (Fehler 0,78 -> 0,56).
- Portugal Schotter (10 Paare, nur 23-74 gemeinsame Fahrer, verrauscht): Rang 4 % setzt Clio
  und Saxo vor den 207 (Spearman 0,71), Paarvergleich sagt 207 schneller als Clio (-0,68 %,
  n=55) und Saxo (-0,82 %, n=74), Opel vor Saxo/Clio um 0,75-0,93 %. Rang 40 % / Median
  bilden die Reihenfolge ab (0,93). Assist-Filter verschlechtert hier (0,57-0,64).
- Folgerung: Es gibt keinen Threshold, der fuer beide passt. Die Top-4-%-Referenz wird auf
  Schotter durch Spitzenfahrer in den Clio/Saxo/Opel-Boards verzerrt. Assist-Filter ist
  nicht allgemein besser (nicht als Standard).
- Portugal Rueckstand zum Opel (Exp 5): Thr 4: Clio 0,4 / 207 1,2 / Saxo 1,3 / 206 2,6 / Fiat 3,0
  / C2 3,3 / Puma 3,4; Thr 40: 207 1,65 / Saxo 1,95 / Clio 2,15 / 206 2,6 / Fiat 3,6 / Puma 4,1 / C2 4,2.
- Offen: mehr Schotter-/Schnee-Locations mit Blatt "Paarvergleich" auswerten (cr_export.py
  muss deployed sein), um eine Threshold-Empfehlung je Belag/Klassengruppe zu bestaetigen.

## Nachtrag 4: Sechs Locations (Monte Carlo, Portugal, Estonia, Scandia, Greece, Spain)
Ziel = Paarlevel (gemeinsame Fahrer, LSQ). Mittel ueber 6 Locations, Reihenfolge (Spearman) / Fehler:
Rang 2 % 0,74/1,18 · 4 % 0,77/1,10 · 8 % 0,75/1,06 · 18 % 0,83/0,98 · 25 % 0,80/0,96 · 40 % 0,83/1,01 ·
Median 0,80/1,15. Assist-Filter: kein Gewinn im Mittel (18 %: 0,79/0,96 gegen 0,83/0,98).
- Empfehlung S2000/S1600: Threshold 18-25. Threshold 4 nur auf Asphalt gleichwertig.
- Systematischer Fehler (CR minus Paarlevel, Thr 18): Opel -1,45 pp in ALLEN 6 Locations (zu schnell),
  Clio -0,7, Puma +1,0, C2 +0,8; 207 +0,3, Fiat +0,45, Saxo 0 (kein System). Das Opel-Board ist
  ueberall mit Spitzenfahrern besetzt.
- Per-Auto-Korrektur aus den uebrigen 5 Locations (Leave-one-out): Fehler 0,98 -> 0,72, aber
  Reihenfolge 0,83 -> 0,66 (schlechter). Nicht empfohlen; Unterschiede im Rauschen der Paarziele.
- Echter AWD-Vorteil (S2000 gegen S1600, gleiche Fahrer): Estonia Opel -2,1 % / 207 -1,1 % gegen Clio
  (n 126/130); Portugal -0,9 / -0,7; Spain Opel -0,7, 207 +0,05 (n 265); Monte Carlo Opel -0,85, 207 0,0;
  Greece und Scandia nur n 15-34, widerspruechlich.
