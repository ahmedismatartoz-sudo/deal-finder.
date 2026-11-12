-- 12/11 (cliente): prezzi di Autohero Italia come riferimento di rivendita (tabella separata, non entra nel sito)
CREATE TABLE IF NOT EXISTS autohero_prezzi (
    uuid TEXT PRIMARY KEY,
    url TEXT,
    slug TEXT,
    testo TEXT,
    prezzo INTEGER,
    prezzo_barrato INTEGER,
    prezzo_iniziale INTEGER,
    rata INTEGER,
    anno INTEGER,
    km INTEGER,
    carburante TEXT,
    cambio TEXT,
    primo_visto TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_visto TIMESTAMPTZ NOT NULL DEFAULT now(),
    sparita_il TIMESTAMPTZ,
    attivo BOOLEAN NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS autohero_prezzi_slug ON autohero_prezzi (slug, anno);
