-- Raccolte che riprendono da dove erano rimaste dopo un riavvio del sito
CREATE TABLE IF NOT EXISTS raccolta_passi (
    job      TEXT NOT NULL,
    chiave   TEXT NOT NULL,
    fatto_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job, chiave)
);
-- Abbonamento a pagamento (Stripe) per chi si iscrive da solo (es. con Google)
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS richiede_abbonamento BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS stripe_customer TEXT;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS abbonamento_stato TEXT;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS abbonamento_fino TIMESTAMPTZ;
