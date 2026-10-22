
## Zufaellig uebersprungene Stages: Token-Refresh-Wettlauf (gefunden + behoben 2026-10-20)
- Ursache (Railway-Log): `/cr/calculate` nutzt EINEN `RacenetClient` fuer 8 parallele
  Threads. Bei abgelaufenem Access Token startete jeder Thread einen eigenen Refresh.
  Der Refresh-Token rotiert bei jeder Benutzung -> nur ein Thread gewinnt, die anderen
  bekommen HTTP 204 + Cookie-Loeschung -> "Kein gueltiger Access Token" -> `_fetch`
  schluckte den Fehler und lieferte [] -> Strecke uebersprungen (je Lauf andere).
- Fix in `racenet_client.py`: (1) `_TokenManager.get_token()` mit RLock + Double-Check,
  (2) Token vor dem Thread-Pool einmal vorwaermen, (3) gescheiterte Leaderboards einmal
  sequentiell wiederholen; Rest in `client.last_load_failures`. `admin_api.py` gibt
  `stats.load_failed` zurueck, `index.html` zeigt es getrennt von min-N-Skips an.
- Test mit Simulation: alt 8 Refreshes / 7 verlorene Stages, neu 1 Refresh / 0 Verluste.
- Wichtig: beide Railway-Services (grf_sync cron + admin_api) importieren
  `racenet_client.py`. Vergleiche zwischen frueheren CR-Laeufen waren wegen
  wechselnder Stage-Mengen nur eingeschraenkt gueltig -> neu rechnen.
- Noch offen (optional): `_bootstrap_from_env` verwirft bei jedem neuen Client den
  Access Token (erzwingt Refresh pro Client).
