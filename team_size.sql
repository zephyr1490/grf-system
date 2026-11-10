-- Team-Auslosung: vom Admin gewaehlte Wunsch-Teamgroesse pro Championship.
-- Ausfuehren in Supabase -> SQL Editor (idempotent). championships ist bereits oeffentlich lesbar,
-- neue Grants sind nicht noetig (ALTER TABLE auf bestehender Tabelle).
-- Normal sind Wunschgroesse -1 .. +1 erlaubt, +-2 nur im aeussersten Notfall (nie unter 2).
ALTER TABLE championships ADD COLUMN IF NOT EXISTS team_size_pref INTEGER NOT NULL DEFAULT 3
  CHECK (team_size_pref BETWEEN 2 AND 8);
