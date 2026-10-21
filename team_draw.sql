-- Team-Auslosung: Protokoll jeder gespeicherten Auslosung (Seed + Ergebnis). Ausfuehren in Supabase -> SQL Editor.
-- Geheim: nur service_role (kein anon/authenticated).
CREATE TABLE IF NOT EXISTS team_draw_runs (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  championship_id TEXT        NOT NULL,
  seed            BIGINT      NOT NULL,
  result_json     JSONB       NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE team_draw_runs ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.team_draw_runs TO service_role;
