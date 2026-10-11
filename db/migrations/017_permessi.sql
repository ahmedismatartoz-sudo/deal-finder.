-- Permessi per account: "auto" (affari e vendi) e "incentivi". Gli amministratori vedono tutto.
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS permessi TEXT[] NOT NULL DEFAULT '{auto}';
