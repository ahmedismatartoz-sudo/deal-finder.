-- 12/11 (cliente: "odio che per ogni azione il sito si debba riavviare"): impostazioni del sito nel database,
-- cambiate dal pannello /gestione → Sito senza deploy (web/impostazioni.py), e copie dell'ultimo elenco del sito e
-- delle statistiche della scheda, rilette all'avvio (web/elenco_salvato.py): niente sito vuoto dopo un riavvio.
CREATE TABLE IF NOT EXISTS sito_impostazioni (
    chiave TEXT PRIMARY KEY,                  -- 'ribasso', 'in_cima'
    valore JSONB NOT NULL,
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now(),
    da TEXT                                   -- chi l'ha cambiata (id dell'amministratore o 'migrazione')
);

CREATE TABLE IF NOT EXISTS sito_scarti (       -- auto tolte dal sito dal cliente (prima data/scarti_sito.json)
    id SERIAL PRIMARY KEY,
    listing_id BIGINT,                        -- annuncio preciso, oppure:
    marca TEXT, modello TEXT, anno INT, km INT, prezzo INT,   -- marca (+ modello, anno) + km + prezzo dell'annuncio
    motivo TEXT,
    attivo BOOLEAN NOT NULL DEFAULT true,     -- false = rimessa sul sito
    creato_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    da TEXT,
    rimesso_at TIMESTAMPTZ,
    rimesso_da TEXT
);
CREATE INDEX IF NOT EXISTS sito_scarti_attivi ON sito_scarti (attivo, listing_id);

CREATE TABLE IF NOT EXISTS sito_cache (        -- 'elenco' (auto del sito), 'scheda_stats' (STATS di trattativa.py)
    chiave TEXT PRIMARY KEY,
    dati BYTEA NOT NULL,                      -- JSON compresso (zlib)
    n INT,
    firma TEXT,
    salvato_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
