-- 3/11 (piano di ricostruzione, salvaguardie automatiche): tabelle della sentinella (dealfinder/sentinella.py).
-- Solo aggiunte: nessuna tabella esistente cambia. Si possono svuotare in qualunque momento senza effetti sul sito.

-- Fotografia dell'elenco del sito, riscritta a ogni costruzione dell'elenco (sentinella.salva_istantanea).
-- Il cron non vede la memoria del sito: legge da qui quali auto sono online.
CREATE TABLE IF NOT EXISTS sito_istantanea (
    listing_id    BIGINT PRIMARY KEY,
    prezzo        INTEGER,
    margine       INTEGER,
    soglia        INTEGER,               -- scovo.soglia(prezzo) calcolata dal sito al momento della fotografia
    fonte         TEXT,
    commerciante  BOOLEAN NOT NULL DEFAULT false,
    provincia     TEXT,
    area          TEXT,
    km            INTEGER,
    sezione       TEXT,                  -- affari | inc | eco | supercar
    categoria     TEXT,
    salvata_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una riga per ora: quante auto c'erano sul sito (per il confronto con la stessa ora di ieri) e l'esito dei controlli.
CREATE TABLE IF NOT EXISTS sentinella_stato (
    ora            TIMESTAMPTZ PRIMARY KEY,          -- date_trunc('hour', now())
    n_sito         INTEGER,
    n_privati      INTEGER,
    n_commercianti INTEGER,
    istantanea_at  TIMESTAMPTZ,
    processo       TEXT,
    esito          JSONB NOT NULL DEFAULT '{}',
    aggiornato_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Allarmi: lo stesso allarme (chiave) al massimo una volta ogni 6 ore; inviato_at NULL = da mandare per email
-- (di notte restano in coda e partono nel riepilogo delle 8).
CREATE TABLE IF NOT EXISTS sentinella_allarmi (
    id          BIGSERIAL PRIMARY KEY,
    chiave      TEXT NOT NULL,
    controllo   TEXT NOT NULL,
    testo       TEXT NOT NULL,
    dati        JSONB NOT NULL DEFAULT '{}',
    creato_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    visto_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    volte       INTEGER NOT NULL DEFAULT 1,
    inviato_at  TIMESTAMPTZ,
    tentativi   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sentinella_allarmi_chiave_idx ON sentinella_allarmi (chiave, creato_at DESC);
CREATE INDEX IF NOT EXISTS sentinella_allarmi_coda_idx ON sentinella_allarmi (creato_at) WHERE inviato_at IS NULL;

-- Impronta delle regole di ogni processo (sito, ogni cron): una riga per (processo, impronta), at = ultima volta vista.
-- impronta_prezzi copre solo le regole di prezzo; prezzi = quei valori (mai segreti: vengono da regole_log).
CREATE TABLE IF NOT EXISTS config_impronte (
    processo         TEXT NOT NULL,
    impronta         TEXT NOT NULL,
    impronta_prezzi  TEXT,
    prezzi           JSONB,
    commit_git       TEXT,
    at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (processo, impronta)
);
CREATE INDEX IF NOT EXISTS config_impronte_at_idx ON config_impronte (processo, at DESC);
