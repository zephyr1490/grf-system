-- Multiclass: Partner-Championships + Doppelstarter-Regel (Hauptchampionship = Themed)
ALTER TABLE championships ADD COLUMN IF NOT EXISTS multiclass_partners TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE championships ADD COLUMN IF NOT EXISTS double_starter_rule TEXT NOT NULL DEFAULT 'worst'
  CHECK (double_starter_rule IN ('worst','best','both'));
-- (ALTER TABLE auf bestehender Tabelle: keine neuen Grants noetig)
