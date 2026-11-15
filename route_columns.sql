-- Strecken-Infos pro Stage (RaceNet liefert sie in stage.stageSettings; bisher ignoriert).
-- Einmal im Supabase SQL-Editor ausfuehren, BEVOR grf_sync.py neu deployt wird.
ALTER TABLE stages ADD COLUMN IF NOT EXISTS route_id    INTEGER;
ALTER TABLE stages ADD COLUMN IF NOT EXISTS route_name  TEXT;
ALTER TABLE stages ADD COLUMN IF NOT EXISTS distance_km NUMERIC;
CREATE INDEX IF NOT EXISTS stages_route_id_idx ON stages (route_id);
