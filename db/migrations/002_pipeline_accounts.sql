-- Fase 2-4: stato della pipeline, cache ricambi, costi AI, account, notifiche

ALTER TABLE listings
    ADD COLUMN stage            TEXT NOT NULL DEFAULT 'nuovo',   -- nuovo, scartato, candidato, approfondito, errore
    ADD COLUMN stage_reason     TEXT,
    ADD COLUMN screened_at      TIMESTAMPTZ,
    ADD COLUMN deep_at          TIMESTAMPTZ,
    ADD COLUMN vehicle_identity JSONB,       -- {make, model, generation, engine, power_kw, method, confidence}
    ADD COLUMN photo_screen     JSONB,       -- esito filtro foto
    ADD COLUMN ai_extract       JSONB;       -- esito lettura testo
CREATE INDEX listings_stage_idx ON listings (stage, first_seen_at);

ALTER TABLE listing_photos ADD COLUMN width INTEGER, ADD COLUMN height INTEGER;
CREATE INDEX listing_photos_phash_idx ON listing_photos (phash);

ALTER TABLE valuations
    ADD COLUMN damage_items   JSONB,
    ADD COLUMN motivation     TEXT[],
    ADD COLUMN checks         TEXT[],
    ADD COLUMN default_margin JSONB;   -- margine con i costi standard (per la soglia di pubblicazione)

-- Prezzi ricambi già cercati: stessa auto + stesso pezzo = nessuna nuova ricerca per 14 giorni
CREATE TABLE parts_cache (
    vehicle_key   TEXT NOT NULL,
    part          TEXT NOT NULL,
    result        JSONB NOT NULL,
    searched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_key, part)
);

-- Consumo AI, per controllare i costi giorno per giorno
CREATE TABLE ai_usage (
    id             BIGSERIAL PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    task           TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    web_searches   INTEGER NOT NULL DEFAULT 0,
    listing_id     BIGINT
);
CREATE INDEX ai_usage_at_idx ON ai_usage (at);

-- Esecuzioni dei lavori programmati (per il pannello amministratore)
CREATE TABLE job_runs (
    id          BIGSERIAL PRIMARY KEY,
    job         TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok          BOOLEAN,
    stats       JSONB
);

-- Misura della qualità delle stime
CREATE TABLE model_runs (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    engine_version TEXT NOT NULL,
    n_cases       INTEGER,
    median_abs_pct_error REAL,
    prudent_coverage     REAL,     -- quota di casi in cui il prudente è sotto il prezzo finale (obiettivo ~0.75)
    suggested_negotiation_discount REAL,
    by_segment    JSONB
);

-- Account
ALTER TABLE dealers
    ADD COLUMN password_hash    TEXT,
    ADD COLUMN role             TEXT NOT NULL DEFAULT 'commerciante',   -- commerciante | admin
    ADD COLUMN company          TEXT,
    ADD COLUMN phone            TEXT,
    ADD COLUMN last_seen_opps_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN preferred_parts  TEXT NOT NULL DEFAULT 'aftermarket';   -- originale | aftermarket | usato

-- La vista originale contava tutte le aperture: sostituita da logica nell'API
DROP VIEW IF EXISTS visible_listings;
