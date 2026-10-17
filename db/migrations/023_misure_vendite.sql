-- Misura delle stime (17/10): ogni auto che era sul sito e sparisce (probabilmente venduta) viene registrata con
-- prezzo, mercato e rivendita stimati e giorni online. Serve a calibrare le rivendite con dati veri.
CREATE TABLE IF NOT EXISTS misure_vendite (
    listing_id        BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    sparita_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    fonte             TEXT,
    make              TEXT,
    model             TEXT,
    anno              INTEGER,
    km                INTEGER,
    prezzo_iniziale   INTEGER,      -- primo prezzo visto
    prezzo_finale     INTEGER,      -- prezzo quando è sparita
    ribassi           INTEGER,      -- quante volte il prezzo è sceso
    mercato_m         INTEGER,
    rivendita_r       INTEGER,
    margine           INTEGER,
    n_confronti       INTEGER,
    metodo            TEXT,
    giorni_online     NUMERIC,      -- dalla prima volta che l'abbiamo visto
    giorni_sul_sito   NUMERIC,      -- da quando era sul sito (deep_at)
    policy            TEXT
);
