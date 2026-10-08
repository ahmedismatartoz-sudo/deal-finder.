-- Modello dei prezzi addestrato: valuta gli annunci all'istante durante la raccolta
CREATE TABLE price_models (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    version     TEXT NOT NULL,
    model       JSONB NOT NULL,
    metrics     JSONB,
    active      BOOLEAN NOT NULL DEFAULT false
);
ALTER TABLE listings
    ADD COLUMN model_p50   INTEGER,
    ADD COLUMN model_p25   INTEGER,
    ADD COLUMN prescreen   TEXT,         -- interessante | non_interessante | nessun_modello | modello_incerto
    ADD COLUMN prescreen_potential INTEGER;
CREATE INDEX listings_prescreen_idx ON listings (prescreen) WHERE prescreen = 'interessante';
