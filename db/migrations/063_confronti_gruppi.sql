-- 19/11 notte (automatismo 2, confronti mirati notturni): rotazione dei gruppi cercati, che resta tra una notte e
-- l'altra. Un gruppo (marca, modello, alimentazione, zona) cercato non si ricerca prima di tre giorni se non ha dato
-- confronti, di un giorno altrimenti (pricing/confronti_mirati.py: gruppi_cercati, segna_gruppo, in_riposo).
-- Solo conteggi: nessun dato di persone. Additiva e idempotente (il codice la crea uguale al primo uso).
CREATE TABLE IF NOT EXISTS confronti_gruppi (
    make       TEXT NOT NULL,
    model      TEXT NOT NULL,
    fuel       TEXT NOT NULL,
    zona       TEXT NOT NULL,
    cercato_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    trovati    INTEGER NOT NULL DEFAULT 0,
    salvati    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (make, model, fuel, zona)
);
