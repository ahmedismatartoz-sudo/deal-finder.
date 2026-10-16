-- Valutazioni con la nuova policy prudente (pricing/policy_v2.py). Le valutazioni della policy precedente
-- restano intatte in `valuations` (riconoscibili dal loro engine_version): qui solo il nuovo calcolo.
CREATE TABLE IF NOT EXISTS valutazioni_policy (
    listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    policy_version  TEXT NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    categoria       TEXT NOT NULL,          -- opportunita | candidato | non_conveniente | dati_insufficienti
    margine         INTEGER,
    riassunto       JSONB NOT NULL,         -- numeri per l'elenco del sito
    dati            JSONB NOT NULL,         -- valutazione completa: confronti, esclusi, costi, ipotesi, motivi
    prima           JSONB,                  -- numeri della policy precedente (per il confronto prima/dopo)
    PRIMARY KEY (listing_id, policy_version)
);
CREATE INDEX IF NOT EXISTS valutazioni_policy_cat_idx ON valutazioni_policy (policy_version, categoria);
