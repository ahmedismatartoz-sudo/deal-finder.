-- 13/11 (ramo piu-auto, cliente: "più auto sul sito senza cambiare le regole").
-- 1) Controllo AI con Claude al posto di Jev (dealfinder/ai/controllo_auto.py): stessa tabella decisione_ai, con la
--    fonte della decisione ('jev' per quelle vecchie, 'claude' per le nuove) e l'impronta del contenuto (testo, dati,
--    foto) per riusare la decisione quando cambia solo il prezzo. Il codice aggiunge le colonne anche da solo.
ALTER TABLE decisione_ai ADD COLUMN IF NOT EXISTS fonte TEXT, ADD COLUMN IF NOT EXISTS impronta TEXT;

-- 2) Stima di ripiego prudente delle auto "dati insufficienti" (dealfinder/pricing/ripiego_prudente.py): una riga per
--    auto, legata al prezzo e alla valutazione da cui è partita. stima NULL = nessuna stima (motivo).
CREATE TABLE IF NOT EXISTS ripiego_prudente (
    listing_id     BIGINT PRIMARY KEY,
    prezzo         INTEGER NOT NULL,
    valutazione_at TIMESTAMPTZ NOT NULL,
    stima          INTEGER,
    pv             JSONB,
    motivo         TEXT,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
