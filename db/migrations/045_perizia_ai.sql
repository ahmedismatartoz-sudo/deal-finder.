-- 12/11 (cliente: "ChatGPT valuta meglio di noi"): perizia AI "da commerciante" (dealfinder/ai/perizia.py).
-- Una riga per auto + prezzo: stessa auto allo stesso prezzo = nessuna nuova chiamata. Le regole del sito
-- (web/sito_regole.py, PERIZIA_AI) leggono giudizio e stima media; la scheda legge tutto il JSON validato.
CREATE TABLE IF NOT EXISTS perizia_ai (
    listing_id   BIGINT NOT NULL,
    prezzo       INTEGER NOT NULL,            -- prezzo dell'annuncio quando è stata fatta la perizia
    giudizio     TEXT NOT NULL,               -- affare | nella media | caro | da verificare
    medio        INTEGER,                     -- prezzo di vendita realistico (forbice)
    basso        INTEGER,
    alto         INTEGER,
    acquisto_max INTEGER,                     -- prezzo massimo per un commerciante che vuole guadagnare
    dati         JSONB NOT NULL,              -- perizia completa validata (rischi, giorni, motivazione)
    modello      TEXT,
    token_in     INTEGER,
    token_out    INTEGER,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, prezzo)
);
CREATE INDEX IF NOT EXISTS perizia_ai_at_idx ON perizia_ai (computed_at);
