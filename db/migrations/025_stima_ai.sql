-- Stime del modello AI dei prezzi (pricing/ai_prezzi.py): una riga per annuncio, riscritta a ogni addestramento.
-- prezzo = prezzo di mercato stimato come venditore privato; errore = errore tipico misurato (0,12 = ±12%)
-- su annunci che il modello non aveva visto, per lo stesso modello d'auto (o marca, o tutto il mercato).
CREATE TABLE IF NOT EXISTS stima_ai (
    listing_id  BIGINT PRIMARY KEY,
    prezzo      INTEGER NOT NULL,
    errore      REAL NOT NULL,
    n_gruppo    INTEGER NOT NULL DEFAULT 0,
    livello     TEXT,
    versione    TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stima_ai_computed_idx ON stima_ai (computed_at);
