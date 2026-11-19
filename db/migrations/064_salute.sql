-- 19/11 (automatismi, salute giornaliera: dealfinder/salute.py): una riga per giorno (ora di Roma) con quante auto
-- c'erano sul sito (mediana delle ore 9-21 di sentinella_stato), i motivi anomali visti e i giri di azioni fatti.
-- Solo aggiunta, idempotente: nessuna tabella esistente cambia; si può svuotare in qualunque momento (si riempie
-- da sola dallo storico della sentinella).
CREATE TABLE IF NOT EXISTS salute_giorno (
    giorno          DATE PRIMARY KEY,
    n_sito          INTEGER,
    n_privati       INTEGER,
    n_commercianti  INTEGER,
    motivi          JSONB NOT NULL DEFAULT '[]',
    azioni          JSONB NOT NULL DEFAULT '[]',
    aggiornato      TIMESTAMPTZ NOT NULL DEFAULT now()
);
