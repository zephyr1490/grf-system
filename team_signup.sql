-- ═══════════════════════════════════════════════════════════════════════════
-- Team Sign-up + Team-Wünsche (Teamed-Club) — Ausführen in: Supabase → SQL Editor
--
-- Konzept: claude/team_lottery_concept.md. Spieler melden sich über ihren
-- Fahrer-PIN (driver_pins) für die Championship an und geben GENAU EINEN
-- Wunschpartner an. Gruppen/Kreise werden später von der Auslosung berechnet.
--
-- WICHTIG (Supabase ab 30.10.2026): neue Tabellen im public-Schema bekommen
-- nicht mehr automatisch Data-API-Zugriff → Grants stehen deshalb hier explizit.
--
--   team_signups  — öffentlich LESBAR (der Anmelde-Pool wird auf der Seite
--                   gezeigt), Schreiben nur über das Backend (service_role).
--   team_wishes   — komplett GEHEIM bis zum Reveal: kein anon-/authenticated-
--                   Zugriff, nur service_role (wie driver_pins).
--
-- championship_id ist bewusst TEXT ohne Foreign Key (championships.id hat in
-- der Praxis RaceNet-IDs, nicht nur UUIDs).
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Sign-up-Steuerung pro Championship ──────────────────────────────────────
-- championships ist öffentlich lesbar → das Frontend sieht den Schalter direkt.
-- Gesetzt wird er im Admin-Panel (Tab "Team Sign-up"), nicht per Hand.
ALTER TABLE championships ADD COLUMN IF NOT EXISTS signup_open     BOOLEAN DEFAULT false;
ALTER TABLE championships ADD COLUMN IF NOT EXISTS signup_deadline TIMESTAMPTZ;

-- ── Anmeldungen ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS team_signups (
  championship_id TEXT        NOT NULL,
  driver_name     TEXT        NOT NULL,
  signed_up_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (championship_id, driver_name)
);

ALTER TABLE team_signups ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read" ON team_signups;
CREATE POLICY "public read" ON team_signups FOR SELECT TO anon USING (true);

GRANT SELECT                         ON public.team_signups TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.team_signups TO service_role;

-- ── Wünsche (geheim) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS team_wishes (
  championship_id TEXT        NOT NULL,
  driver_name     TEXT        NOT NULL,
  partner_name    TEXT        NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (championship_id, driver_name)
);

ALTER TABLE team_wishes ENABLE ROW LEVEL SECURITY;
-- Bewusst KEINE Policy und KEIN Grant für anon/authenticated.

GRANT SELECT, INSERT, UPDATE, DELETE ON public.team_wishes TO service_role;
