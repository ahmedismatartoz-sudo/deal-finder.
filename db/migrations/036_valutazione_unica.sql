-- 4/11/2026 (fase 2: una sola valutazione, pricing/valutazione.py). Solo aggiunte.
-- Guardia sulle scritture: una valutazione non si sostituisce con una fatta con MENO confronti (n), salvo auto
-- cambiata (impronta_auto), regole cambiate (impronta_regole, dealfinder/regole.py) o riga più vecchia di 36 ore.
ALTER TABLE valutazioni_policy ADD COLUMN IF NOT EXISTS n INTEGER;
ALTER TABLE valutazioni_policy ADD COLUMN IF NOT EXISTS impronta_auto TEXT;
ALTER TABLE valutazioni_policy ADD COLUMN IF NOT EXISTS impronta_regole TEXT;
ALTER TABLE valutazioni_policy ADD COLUMN IF NOT EXISTS fonte TEXT;      -- cron | cron-utilitarie | web-nuovi | commercianti
ALTER TABLE valutazioni_policy ADD COLUMN IF NOT EXISTS metodo TEXT;     -- valore_reale | valore_reale_combinato | mediana | modello | ai ...
CREATE INDEX IF NOT EXISTS valutazioni_policy_impronta_idx ON valutazioni_policy (impronta_regole);
-- Risultati delle prove dei prezzi (pricing/prova_correzione.py e prove future), con l'impronta delle regole.
CREATE TABLE IF NOT EXISTS prove_prezzi (
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    prova     TEXT NOT NULL,
    impronta  TEXT,
    metriche  JSONB NOT NULL,
    PRIMARY KEY (at, prova)
);
