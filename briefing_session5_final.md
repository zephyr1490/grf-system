## 🧠 ELO-SYSTEM — Session 5: Formel-Überarbeitung ENTSCHIEDEN, Dateien fertig, noch NICHT deployed

**Status: alles final entschieden und in Dateien umgesetzt. Deployment steht noch aus.**

### Finale Werte (nach Testing entschieden)
- `BASE_K = 55` (war 40) — bewusst NICHT weiter fein-getuned zwischen 50-60,
  da der Unterschied dort klein ist (Gain 5,56 vs 6,68 im Owner-eigenen
  213-Event-Testfall). K=100 hätte deutlich mehr Effekt gehabt (Gain 11,13),
  aber mit dauerhaft mehr Rauschen — bewusst nicht gewählt.
- `SIGMA_DECAY = 0.94` (Anzeige-Sigma, unverändert — schnell, wie bisher)
- `K_SIGMA_DECAY = 0.99` (NEU, Momentum-Sigma — langsam, treibt NUR den
  K-Faktor). Getestet gegen 0,995 (verdoppelt die Zeit bis Floor auf ~600
  Events) — bewusst NICHT gewählt, 0,99 (~300 Events) reicht.
- Gewichtete Duelle: nutzt **Anzeige-Sigma** (nicht Momentum-Sigma) als
  Zuverlässigkeits-Signal — bewusste, inhaltlich begründete Entscheidung
  (Anzeige-Sigma beantwortet "wie verlässlich ist die AKTUELLE Zahl",
  Momentum-Sigma beantwortet eine andere Frage: "wie stark darf sie sich
  noch bewegen"). Realer Effekt in der Praxis kleiner als im Demo-Beispiel
  (Demo nutzte extreme Sigma-Unterschiede, echte Felder sind moderater).

### NEU: öffentliche Momentum-Spalte (kσ)
Der bisher rein interne "K-Sigma"-Wert wird jetzt öffentlich als **"Momentum
(kσ)"** angezeigt — eigene Spalte auf der ELO-Seite, kompakter Spaltenkopf
"kσ ⓘ", volle Erklärung im Klick-Popup (`ELO_INFO_TEXT.ksigma`, gleicher
Mechanismus wie die bestehenden ELO/μ/σ-Tooltips).

### Geänderte Dateien (alle in diesem Chat als Download bereitgestellt)
1. **`elo_engine.py`** — Rating-Klasse hat jetzt `k_sigma`-Feld (Migration
   für alte Daten eingebaut: `from_dict()` setzt `k_sigma` beim ersten Laden
   automatisch auf den vorhandenen `sigma`-Wert). `process_event()` nutzt
   `k_sigma` für den K-Faktor, `sigma` bleibt für Anzeige/Gewichtung/
   `is_provisional`. Inverse-Varianz-Gewichtung der Duelle (1/σ_Gegner²).
2. **`elo_pipeline.py`** — `DriverSummary` hat jetzt zusätzlich `k_sigma`
   (mit Fallback auf `sigma`, falls doch mal ein Rating ohne `k_sigma`
   auftaucht). `summarize_track()` gibt das durch.
3. **`admin_api.py`** — Driver-Sync schreibt jetzt zusätzlich `elo_k_sigma`
   in die `drivers`-Tabelle.
4. **`index.html`** — neue kσ-Spalte (Header, Tooltip, Daten-Mapping,
   Zeilen-Rendering, colspan 11→12 angepasst), Changelog v1.2 mit dem
   finalen Community-Text (Owner-Ton, erste Person, Name "Zephyr",
   TrueSkill/Glicko-2-Erwähnung, "Honest note" zum eigenen, geringen Gain).

### ⚠️ Für zukünftige Updates merken
Der Versions-Badge im Seitenkopf (`#site-version-badge`, Zeile ~714) ist
**reines statisches HTML**, wird NICHT automatisch aus dem `CHANGELOG`-Array
gezogen — bei jedem neuen Changelog-Eintrag muss diese Stelle SEPARAT von
Hand mit angepasst werden, sonst zeigt der Header eine veraltete Version an
(genau das ist bei diesem Update erst im zweiten Anlauf aufgefallen).

### ⚠️ Vor dem Deployment nötig: DB-Migration
**Die `drivers`-Tabelle braucht eine neue Spalte, bevor `admin_api.py`
gepusht wird**, sonst schlägt der Sync fehl:
```sql
ALTER TABLE drivers ADD COLUMN elo_k_sigma numeric;
```

### Deployment-Reihenfolge
1. SQL-Migration oben in Supabase ausführen.
2. `elo_engine.py`, `elo_pipeline.py`, `admin_api.py` pushen (Railway
   deployed automatisch).
3. `index.html` pushen.
4. `/elo/update` mit `force_reset: true` für alle 9 Clubs auslösen (über
   Admin-Panel, Club-Checkboxes + Reset-Toggle — kein manueller API-Call
   nötig).
5. Changelog-Text (bereits in `index.html` als v1.2 hinterlegt) geht damit
   automatisch live — kein separater Community-Post nötig, der Changelog-
   Tab IST die Ankündigung.

### Reale Auswirkung (aus Owners eigenem Testlauf, echte Namen)
Beispiele Gewinner (Karriere-CR alt → neu, ungefähr K=60 statt final 55):
CA_Toolmaker 1670→1823 (+153), sunbeam16ti 1613→1762 (+148), JoeMVR_GRF
1563→1685 (+122). Beispiele Verlierer: zabora666 1459→1363 (-95),
Bleifussler 1485→1392 (-93), Penchii 1505→1433 (-72). Kein Muster nach
"gut/schlecht" — eher: wer von der alten schnellen Einfrierung nach einem
starken frühen Abschnitt profitiert hat, verliert jetzt relativ (siehe
Community-Text "Why might your number drop").

### Bewusst NICHT übernommen (zur Erinnerung, siehe Session 4/5-Testing)
TrueSkill, Glicko-2, rollierendes Form-Fenster, Car-Rating-Integration
(eigenes, noch unbegonnenes Projekt — siehe unten), kompletter Sigma-
Verzicht (getestet: führt zu Überreaktion/Instabilität statt zu mehr
Fairness).

### Offen: Car-Rating-Integration ins ELO (eigenständiges neues Projekt)
Idee: Autostärke (Car Rating) als Handicap in die ELO-Erwartungswert-
Berechnung einfließen lassen (Unterdog-Auto-Sieg zählt mehr). Aktuell ganz
am Anfang:
- Es gibt schon ein Tool, das aus RaceNet-Leaderboards eine Sieg-
  Wahrscheinlichkeit pro Auto/Location berechnet — Code/Output noch nicht
  vorgelegt, im nächsten Chat mitbringen.
- Car Ratings selbst sind aktuell ohnehin nur MANUELL gepflegt (`car_ratings`-
  Tabelle, 1,0-2,0-Skala, real bis ~1,8 beobachtet), nicht automatisch
  berechnet — müsste zuerst global (jede Fahrzeugklasse × jede Location)
  berechnet werden, bevor überhaupt etwas ins ELO einfließen kann.
- Komplett eigenes Paket, blockiert den aktuellen Elo-Reset NICHT und wird
  davon auch nicht blockiert.

### Für den nächsten Chat mitzubringen
Bestätigung, dass Deployment (inkl. SQL-Migration) durchgeführt wurde, plus
— falls die Car-Rating-Idee weiterverfolgt wird — das bestehende
Sieg-Wahrscheinlichkeits-Tool (Code oder Beispiel-Output).

---
