-- Posizione del commerciante (data dal browser con il suo consenso): serve al filtro per distanza
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS pos_at TIMESTAMPTZ;
