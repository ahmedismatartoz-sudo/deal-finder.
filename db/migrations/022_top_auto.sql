-- Le migliori auto per margine (rispetto alla soglia della loro fascia), scelte a ogni ricalcolo completo:
-- vanno sul sito anche se il margine è un po' sotto la soglia piena (mai sotto TOP_MIN_EURO / TOP_QUOTA della soglia).
CREATE TABLE IF NOT EXISTS top_auto (
    listing_id  BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    posizione   INTEGER NOT NULL,
    margine     INTEGER,
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
