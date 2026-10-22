-- 22/10 (cliente): piccoli commercianti di AutoScout24 con cellulare aziendale (mai privati), per la prova WhatsApp
CREATE TABLE IF NOT EXISTS commercianti_as24 (
    seller_id   TEXT PRIMARY KEY,
    nome        TEXT,
    cellulari   TEXT[] NOT NULL DEFAULT '{}',
    aggiornato  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS commercianti_as24_annunci (
    seller_id   TEXT NOT NULL,
    listing_id  TEXT NOT NULL,
    prezzo      INTEGER,
    marca       TEXT,
    carburante  TEXT,
    comune      TEXT,
    prov        TEXT,
    visto       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_id, listing_id)
);
