-- 23/11 (proprietario): registrazione dal sito con email e password, oltre a Google (POST /api/auth/registrati).
-- Solo aggiunta, idempotente: due colonne facoltative su dealers. Il codice le scrive solo se ci sono
-- (abbonamento.ha_colonna), quindi la registrazione funziona anche se questa migrazione viene rimandata.
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS citta TEXT;                 -- città o provincia scritta alla registrazione
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS consenso_at TIMESTAMPTZ;    -- quando ha accettato condizioni d'uso e privacy
