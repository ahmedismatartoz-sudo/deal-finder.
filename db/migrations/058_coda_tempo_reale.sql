-- 16/11 (ramo fond-velocita, FOND_VELOCITA.md): coda PERSISTENTE degli annunci nuovi del tempo reale.
-- Un annuncio da valutare entra qui nella stessa transazione in cui si salva in listings: se il worker (o il sito) si
-- riavvia prima di valutarlo, il giro dopo lo riprende da qui (prima l'elenco degli id stava solo in memoria).
-- Le colonne di tempo servono alla misura del ritardo vero: annuncio_at (pubblicato sul portale, Subito) → letto_at
-- (letto e salvato da noi) → valutato_at → pubblicato_at (messo sul sito dal worker) → sito_at (comparso nell'elenco).
-- Solo dati dell'annuncio, nessun dato del venditore. Le righe chiuse da più di 7 giorni si cancellano da sole.
CREATE TABLE IF NOT EXISTS tr_coda (
    listing_id    BIGINT PRIMARY KEY,
    fonte         TEXT,
    evento        TEXT,                  -- nuovo | prezzo | riapparso
    annuncio_at   TIMESTAMPTZ,
    letto_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    preso_at      TIMESTAMPTZ,           -- preso da un giro (per non valutarlo in due)
    tentativi     INTEGER NOT NULL DEFAULT 0,
    valutato_at   TIMESTAMPTZ,           -- NULL = ancora da valutare
    esito         TEXT,                  -- valutata | pubblicata | abbandonata
    pubblicato_at TIMESTAMPTZ,
    sito_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tr_coda_da_valutare ON tr_coda (letto_at) WHERE valutato_at IS NULL;
CREATE INDEX IF NOT EXISTS tr_coda_valutato ON tr_coda (valutato_at);
