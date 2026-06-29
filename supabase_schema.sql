-- ══════════════════════════════════════════════════════════════════════════════
--  GRF SYSTEM — SUPABASE SCHEMA UPDATE
--  Ausführen in: Supabase → SQL Editor → Run
-- ══════════════════════════════════════════════════════════════════════════════

-- ── system_config (Token-Persistenz für Railway) ──────────────────────────────
CREATE TABLE IF NOT EXISTS system_config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ── cr_sets (CR-Gruppen, unabhängig von Championships) ───────────────────────
-- Eine CR-Gruppe hat einen Namen, Klassen, Locations und Exponent.
-- Kann nachträglich einer Championship zugeordnet werden.
CREATE TABLE IF NOT EXISTS cr_sets (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT NOT NULL,                  -- z.B. "Group B — Monte Carlo 2026"
  vehicle_classes TEXT[],                      -- z.B. ["Group B", "Group A"]
  route_ids    INT[],                          -- RaceNet Route IDs
  class_ids    INT[],                          -- RaceNet Vehicle Class IDs
  top_pct      NUMERIC DEFAULT 25,
  min_n        INT     DEFAULT 10,
  exponent     NUMERIC DEFAULT 1.5,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- ── car_ratings (CR-Werte, gehören zu einem cr_set) ──────────────────────────
-- Bestehende Tabelle erweitern falls vorhanden, sonst neu erstellen.
CREATE TABLE IF NOT EXISTS car_ratings (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cr_set_id    UUID REFERENCES cr_sets(id) ON DELETE CASCADE,
  vehicle      TEXT NOT NULL,
  cr_value     NUMERIC NOT NULL,
  n_entries    INT,
  car_avg_ms   INT,
  ref_time_ms  INT,
  created_at   TIMESTAMPTZ DEFAULT now()
);

-- ── championships (erweitert) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS championships (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  club_id         TEXT,                        -- RaceNet Club ID (23799 / 23834 / ...)
  racenet_id      TEXT,                        -- RaceNet Championship ID (kann leer sein)
  season_number   INT,
  vehicle_class   TEXT,
  cr_set_id       UUID REFERENCES cr_sets(id), -- zugeordnete CR-Gruppe (nullable)
  start_date      DATE,
  end_date        DATE,
  narrative       TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── championship_rules ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS championship_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  rule_type       TEXT NOT NULL,
  is_active       BOOLEAN DEFAULT true,
  points          INT DEFAULT 0,
  description     TEXT
);

-- ── events ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  racenet_event_id TEXT,                       -- RaceNet Event ID
  name            TEXT NOT NULL,               -- "Rd.1 — Monte Carlo"
  location        TEXT,
  round_number    INT,
  start_at        TIMESTAMPTZ,
  close_at        TIMESTAMPTZ,
  narrative       TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── teams ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  color           TEXT                         -- optionaler Hex-Code für UI
);

-- ── team_members ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS team_members (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id         UUID REFERENCES teams(id) ON DELETE CASCADE,
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  driver_name     TEXT NOT NULL,
  UNIQUE (championship_id, driver_name)        -- ein Fahrer pro Championship in nur einem Team
);

-- ── manual_bonuses ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS manual_bonuses (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  event_id        UUID REFERENCES events(id)   ON DELETE CASCADE,
  driver_name     TEXT NOT NULL,
  bonus_type      TEXT NOT NULL,
  bonus_name      TEXT,
  points          INT NOT NULL,
  note            TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── event_results ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_results (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id        UUID REFERENCES events(id)   ON DELETE CASCADE,
  championship_id UUID REFERENCES championships(id) ON DELETE CASCADE,
  driver_name     TEXT NOT NULL,
  vehicle         TEXT,
  finish_rank     INT,
  total_time_ms   INT,
  cr_points       INT DEFAULT 0,
  bonus_points    INT DEFAULT 0,
  total_points    INT DEFAULT 0,
  is_dnf          BOOLEAN DEFAULT false,
  created_at      TIMESTAMPTZ DEFAULT now()
);

-- ── RLS: SELECT für alle offen, Writes nur via service_role ──────────────────
ALTER TABLE system_config     ENABLE ROW LEVEL SECURITY;
ALTER TABLE cr_sets           ENABLE ROW LEVEL SECURITY;
ALTER TABLE car_ratings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE championships     ENABLE ROW LEVEL SECURITY;
ALTER TABLE championship_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE events            ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams             ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members      ENABLE ROW LEVEL SECURITY;
ALTER TABLE manual_bonuses    ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_results     ENABLE ROW LEVEL SECURITY;

-- SELECT für anon (Website liest)
CREATE POLICY "public read" ON cr_sets           FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON car_ratings       FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON championships     FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON championship_rules FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON events            FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON teams             FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON team_members      FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON manual_bonuses    FOR SELECT TO anon USING (true);
CREATE POLICY "public read" ON event_results     FOR SELECT TO anon USING (true);
-- system_config: kein public read (enthält Token)

-- Writes NUR via service_role (Railway/Admin API) — anon kann nicht schreiben
-- service_role bypassed RLS automatisch → keine extra Policy nötig
