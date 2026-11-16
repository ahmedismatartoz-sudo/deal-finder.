-- 16/11 (ramo fond-auto, cliente: "più auto senza cambiare nessuna regola").
-- 1) Recupero delle auto con pochi confronti (dealfinder/pricing/recupero.py): ogni auto ripresa si segna qui, così
--    il giro successivo prende le altre (ordine per valutazione più vecchia) e non riprova la stessa per 24 ore.
--    Solo id dell'annuncio, data ed esito (categoria): nessun dato del venditore. Il codice crea la tabella anche da solo.
CREATE TABLE IF NOT EXISTS recupero_provato (
    listing_id BIGINT PRIMARY KEY,
    provato_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tentativi  INTEGER NOT NULL DEFAULT 1,
    esito      TEXT
);
CREATE INDEX IF NOT EXISTS recupero_provato_at_idx ON recupero_provato (provato_at);

-- 2) Cascata dei confronti (dealfinder/pricing/ricalcolo._cascata): auto uguali SPARITE di recente, per modello.
CREATE INDEX IF NOT EXISTS listings_sparite_modello_idx ON listings (make, model, fuel, disappeared_at)
    WHERE status = 'scomparso';
