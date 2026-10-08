-- Deal Finder — schema PostgreSQL
-- Livello condiviso: annunci, storico, veicoli, valutazioni, ricambi.
-- Livello per cliente: commercianti, profili costi, aperture link.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------
-- Catalogo veicoli normalizzato (versione standard)
-- ---------------------------------------------------------------
CREATE TABLE vehicle_catalog (
    id              BIGSERIAL PRIMARY KEY,
    make            TEXT NOT NULL,
    model           TEXT NOT NULL,
    generation      TEXT,              -- es. "Mk7", "Mk7.5"
    body_type       TEXT,              -- berlina, sw, suv, ...
    fuel            TEXT NOT NULL,     -- benzina, diesel, gpl, metano, ibrida, elettrica
    displacement_cc INTEGER,
    power_kw        INTEGER,
    gearbox         TEXT,              -- manuale, automatico
    trim            TEXT,
    year_from       INTEGER,
    year_to         INTEGER,
    UNIQUE (make, model, generation, fuel, displacement_cc, power_kw, gearbox, trim)
);

-- ---------------------------------------------------------------
-- Annunci (uno per fonte). Le auto fisiche sono in vehicles.
-- ---------------------------------------------------------------
CREATE TABLE vehicles (
    id              BIGSERIAL PRIMARY KEY,
    fingerprint     TEXT UNIQUE NOT NULL,   -- impronta per deduplica
    plate_hash      TEXT,                   -- hash della targa, mai in chiaro
    catalog_id      BIGINT REFERENCES vehicle_catalog(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listings (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL,        -- subito, autoscout24, facebook, ...
    source_id           TEXT NOT NULL,
    url                 TEXT NOT NULL,
    vehicle_id          BIGINT REFERENCES vehicles(id),
    catalog_id          BIGINT REFERENCES vehicle_catalog(id),
    catalog_confidence  REAL,                 -- 0..1 sicurezza sulla versione

    title               TEXT,
    description         TEXT,
    make                TEXT,
    model               TEXT,
    version_raw         TEXT,
    year                INTEGER,
    mileage_km          INTEGER,
    fuel                TEXT,
    gearbox             TEXT,
    power_kw            INTEGER,

    price_raw           TEXT,
    price_eur           INTEGER,              -- prezzo IVA inclusa, normalizzato
    price_flags         TEXT[] NOT NULL DEFAULT '{}',  -- plus_iva, leasing, civetta, trattabile

    seller_type         TEXT,                 -- privato, commerciante, sconosciuto
    seller_info         JSONB,                -- solo dati minimi (anzianità account, n. annunci)
    city                TEXT,
    province            TEXT,
    region              TEXT,
    lat                 DOUBLE PRECISION,
    lon                 DOUBLE PRECISION,

    -- Danni
    damage_declared     BOOLEAN,
    damage_class        TEXT,                 -- nessuno, leggero, grave, sconosciuto
    damage_items        JSONB,                -- [{part, side, action, severity, source, confidence}]

    -- Dati mancanti, espliciti
    missing_fields      TEXT[] NOT NULL DEFAULT '{}',
    field_origin        JSONB,                -- {campo: dichiarato|dedotto_ai|mancante}

    status              TEXT NOT NULL DEFAULT 'attivo',  -- attivo, scomparso, rimosso_aperture
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at     TIMESTAMPTZ,
    disappeared_at      TIMESTAMPTZ,

    raw                 JSONB,                -- payload originale della fonte
    UNIQUE (source, source_id)
);
CREATE INDEX listings_cmp_idx ON listings (make, model, fuel, year, mileage_km) WHERE status = 'attivo';
CREATE INDEX listings_status_idx ON listings (status, last_seen_at);
CREATE INDEX listings_title_trgm ON listings USING gin (title gin_trgm_ops);

CREATE TABLE listing_photos (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    source_url      TEXT NOT NULL,
    storage_key     TEXT,                    -- copia su R2/S3/disco
    phash           TEXT,                    -- hash percettivo per deduplica/foto rubate
    UNIQUE (listing_id, position)
);

-- Storico: una riga per ogni cambiamento (non per ogni giorno)
CREATE TABLE price_events (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    event           TEXT NOT NULL,           -- apparso, prezzo, scomparso, riapparso
    price_eur       INTEGER,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX price_events_listing_idx ON price_events (listing_id, at);

-- ---------------------------------------------------------------
-- Valutazioni (ricalcolabili, versionate)
-- ---------------------------------------------------------------
CREATE TABLE valuations (
    id                   BIGSERIAL PRIMARY KEY,
    listing_id           BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    engine_version       TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    private_median       INTEGER,            -- mercato privato
    dealer_median        INTEGER,            -- mercato commercianti (richiesto)
    resale_prudent       INTEGER,            -- rivendita prudente (incassato stimato)
    resale_median        INTEGER,
    comparables_used     JSONB,              -- [{listing_id, price, adjusted_price, level}]
    comparable_level     INTEGER,            -- 1 stretto ... 3 largo
    n_comparables        INTEGER,
    dispersion           REAL,               -- (P75-P25)/mediana
    liquidity_days       REAL,               -- giorni online tipici del segmento
    confidence           TEXT NOT NULL,      -- affidabile, da_verificare
    confidence_reasons   TEXT[] NOT NULL DEFAULT '{}',

    parts_cost_low       INTEGER,
    parts_cost_high      INTEGER,
    parts_detail         JSONB,              -- [{part, codice, fonti:[{url, prezzo, tipo}]}]

    discount_vs_private  REAL,               -- quanto è sotto il mercato privato (0.25 = -25%)
    fraud_flags          TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX valuations_listing_idx ON valuations (listing_id, created_at DESC);

-- ---------------------------------------------------------------
-- Livello per cliente
-- ---------------------------------------------------------------
CREATE TABLE dealers (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    telegram_chat   TEXT,
    provinces       TEXT[] NOT NULL DEFAULT '{MI,MB,BG,BS}',
    max_purchase    INTEGER NOT NULL DEFAULT 20000,
    accept_damage   BOOLEAN NOT NULL DEFAULT true,
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Costi standard: valori di default modificabili da ogni commerciante
CREATE TABLE dealer_costs (
    dealer_id               BIGINT PRIMARY KEY REFERENCES dealers(id) ON DELETE CASCADE,
    transport_eur           INTEGER NOT NULL DEFAULT 150,
    paperwork_eur           INTEGER NOT NULL DEFAULT 450,
    preparation_eur         INTEGER NOT NULL DEFAULT 300,
    contingency_pct         REAL    NOT NULL DEFAULT 0.05,
    contingency_damaged_pct REAL    NOT NULL DEFAULT 0.15,
    warranty_reserve_eur    INTEGER NOT NULL DEFAULT 200,
    vat_margin_scheme       BOOLEAN NOT NULL DEFAULT true,
    threshold_low_eur       INTEGER NOT NULL DEFAULT 2000,
    threshold_high_eur      INTEGER NOT NULL DEFAULT 3000,
    threshold_split_eur     INTEGER NOT NULL DEFAULT 5000   -- riferito al prezzo di acquisto
);

-- Aperture del link: l'annuncio sparisce dopo N commercianti diversi
CREATE TABLE listing_opens (
    listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    dealer_id       BIGINT NOT NULL REFERENCES dealers(id) ON DELETE CASCADE,
    first_opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, dealer_id)
);

CREATE TABLE dealer_feedback (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      BIGINT NOT NULL REFERENCES listings(id),
    dealer_id       BIGINT NOT NULL REFERENCES dealers(id),
    status          TEXT NOT NULL,           -- scartata, contattato, comprata, venduta
    reason          TEXT,
    bought_eur      INTEGER,
    sold_eur        INTEGER,
    days_to_sell    INTEGER,
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vista: opportunità ancora visibili (sotto il limite di aperture)
CREATE VIEW visible_listings AS
SELECT l.*
FROM listings l
WHERE l.status = 'attivo'
  AND (SELECT count(*) FROM listing_opens o WHERE o.listing_id = l.id) < 7;
