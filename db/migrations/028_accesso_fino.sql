-- Accesso a tempo per gli account preparati da noi (es. un mese di prova): dopo questa data non si entra più.
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS accesso_fino TIMESTAMPTZ;
