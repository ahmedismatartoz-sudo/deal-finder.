-- Sito Scovo: limiti dei servizi a pagamento, foto salvate, richieste di accesso, indici per l'elenco

-- Ogni uso di Vendi/Ricambi (per i tetti giornalieri: le ricerche targa costano)
CREATE TABLE IF NOT EXISTS service_usage (
    id          BIGSERIAL PRIMARY KEY,
    dealer_id   BIGINT NOT NULL REFERENCES dealers(id) ON DELETE CASCADE,
    service     TEXT NOT NULL,
    ok          BOOLEAN,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS service_usage_idx ON service_usage (service, at);
CREATE INDEX IF NOT EXISTS service_usage_dealer_idx ON service_usage (dealer_id, at);

-- Copia ridotta delle foto delle auto proposte: i link di Facebook scadono dopo pochi giorni
CREATE TABLE IF NOT EXISTS photo_cache (
    listing_id  BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    content     BYTEA,                    -- JPEG ridotto (lato lungo 960 px); NULL = foto non scaricabile
    saved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, position)
);

-- Richieste di accesso dalla pagina pubblica
CREATE TABLE IF NOT EXISTS access_requests (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT,
    company     TEXT,
    email       TEXT NOT NULL,
    phone       TEXT,
    note        TEXT,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    handled     BOOLEAN NOT NULL DEFAULT false
);

-- L'elenco degli affari legge solo le auto approfondite e attive
CREATE INDEX IF NOT EXISTS listings_affari_idx ON listings (last_checked_at) WHERE stage = 'approfondito' AND status = 'attivo';
CREATE INDEX IF NOT EXISTS listing_opens_dealer_idx ON listing_opens (dealer_id);
