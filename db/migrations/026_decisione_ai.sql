-- Decisioni dell'AI decisionale (TypeSafe Jev, pricing/jev.py): una riga per annuncio, rifatta se cambia il prezzo.
-- In PROVA: le decisioni si registrano e si confrontano con i calcoli, ma non cambiano il sito (JEV_MODO=prova).
CREATE TABLE IF NOT EXISTS decisione_ai (
    listing_id      BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    prezzo          INTEGER NOT NULL,              -- prezzo dell'annuncio quando è stata decisa
    decisione       TEXT NOT NULL,                 -- compra | tratta | scarta
    confidenza      REAL,
    p_compra        REAL,
    truffa          REAL,                          -- probabilità di truffa o prezzo falso (0-1)
    versione_ok     REAL,                          -- probabilità che i confronti siano la stessa versione
    dati_coerenti   REAL,
    stato           REAL,                          -- 0 = da riparare … 4 = ottimo
    correzione_pct  REAL,                          -- correzione suggerita alla rivendita dei calcoli (es. -5.0)
    categoria_calcoli TEXT,                        -- cosa avevano deciso i calcoli, per il confronto
    margine_calcoli INTEGER,
    risposta        JSONB,
    token           INTEGER,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisione_ai_at_idx ON decisione_ai (computed_at);
