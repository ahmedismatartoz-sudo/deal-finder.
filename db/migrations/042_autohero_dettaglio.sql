-- 12/11 (cliente): dalla pagina di ogni auto Autohero si legge tutto (allestimento, potenza, optional...)
ALTER TABLE autohero_prezzi ADD COLUMN IF NOT EXISTS titolo TEXT;
ALTER TABLE autohero_prezzi ADD COLUMN IF NOT EXISTS versione TEXT;
ALTER TABLE autohero_prezzi ADD COLUMN IF NOT EXISTS allestimento TEXT;
ALTER TABLE autohero_prezzi ADD COLUMN IF NOT EXISTS dettaglio JSONB;
ALTER TABLE autohero_prezzi ADD COLUMN IF NOT EXISTS dettaglio_at TIMESTAMPTZ;
