-- Entra con Google e con Apple
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS google_sub TEXT;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS apple_sub TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS dealers_google_sub_idx ON dealers (google_sub) WHERE google_sub IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS dealers_apple_sub_idx ON dealers (apple_sub) WHERE apple_sub IS NOT NULL;
ALTER TABLE access_requests ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'modulo';   -- modulo | google | apple
