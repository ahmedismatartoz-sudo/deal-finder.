-- Modulo Incentivi (prototipo): imprese, documenti cifrati, valutazioni versionate, condivisioni, log e avvisi
CREATE TABLE IF NOT EXISTS inc_imprese (
    id          BIGSERIAL PRIMARY KEY,
    sessione    TEXT NOT NULL,
    piva        TEXT NOT NULL,
    profilo     JSONB NOT NULL,
    fonte       JSONB NOT NULL,           -- fornitore, ambiente, data di acquisizione
    creato      TIMESTAMPTZ NOT NULL DEFAULT now(),
    aggiornato  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inc_imprese_sessione ON inc_imprese (sessione);

CREATE TABLE IF NOT EXISTS inc_documenti (
    id          BIGSERIAL PRIMARY KEY,
    impresa_id  BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL,            -- visura | allegato
    misura_id   TEXT,
    doc_id      TEXT,                     -- es. preventivi, durc
    nome        TEXT NOT NULL,
    mime        TEXT NOT NULL,
    dati        BYTEA NOT NULL,           -- cifrati (Fernet)
    sha256      TEXT NOT NULL,            -- impronta del file originale
    fonte       TEXT,                     -- es. "Openapi Visure Camerali (sandbox) — richiesta 625f…"
    creato      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inc_visure_richieste (
    id           BIGSERIAL PRIMARY KEY,
    impresa_id   BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    tipo         TEXT NOT NULL,
    richiesta_id TEXT NOT NULL,
    stato        TEXT NOT NULL,
    creato       TIMESTAMPTZ NOT NULL DEFAULT now(),
    aggiornato   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inc_valutazioni (
    id                BIGSERIAL PRIMARY KEY,
    impresa_id        BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    risposte          JSONB NOT NULL,
    risultato         JSONB NOT NULL,
    versione_catalogo TEXT NOT NULL,      -- versione delle regole usata
    creato            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inc_checklist (
    impresa_id  BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    misura_id   TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    pronto      BOOLEAN NOT NULL DEFAULT false,
    aggiornato  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (impresa_id, misura_id, doc_id)
);

CREATE TABLE IF NOT EXISTS inc_condivisioni (
    token          TEXT PRIMARY KEY,
    impresa_id     BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    valutazione_id BIGINT REFERENCES inc_valutazioni(id) ON DELETE CASCADE,
    misure         TEXT[] NOT NULL,
    scade          TIMESTAMPTZ NOT NULL,
    creato         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inc_accessi (
    id       BIGSERIAL PRIMARY KEY,
    token    TEXT NOT NULL,
    cosa     TEXT NOT NULL,
    quando   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_hash  TEXT
);

CREATE TABLE IF NOT EXISTS inc_stato_misure (
    misura_id  TEXT PRIMARY KEY,
    stato      TEXT NOT NULL,
    etichetta  TEXT,
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inc_job_log (
    id        BIGSERIAL PRIMARY KEY,
    quando    TIMESTAMPTZ NOT NULL DEFAULT now(),
    livello   TEXT NOT NULL,              -- info | novita | revisione | errore
    messaggio TEXT NOT NULL,
    dettagli  JSONB
);

CREATE TABLE IF NOT EXISTS inc_avvisi (
    id         BIGSERIAL PRIMARY KEY,
    impresa_id BIGINT NOT NULL REFERENCES inc_imprese(id) ON DELETE CASCADE,
    misura_id  TEXT,
    tipo       TEXT NOT NULL,             -- apre_domani | aperto | chiude_presto | chiuso | regolamento | documento
    canale     TEXT NOT NULL,             -- email (simulata) | push (simulata)
    titolo     TEXT NOT NULL,
    testo      TEXT NOT NULL,
    urgente    BOOLEAN NOT NULL DEFAULT false,
    creato     TIMESTAMPTZ NOT NULL DEFAULT now()
);
