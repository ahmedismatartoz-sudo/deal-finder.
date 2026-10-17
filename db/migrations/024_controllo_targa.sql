-- Controllo targa (17/10): per le auto senza numero di telefono ammesse sul sito (le "super valide", max 20%) si
-- guarda nelle foto se la targa si vede. Targa coperta + niente numero = possibile truffa: fuori.
-- Si salva SOLO l'esito, mai la targa.
CREATE TABLE IF NOT EXISTS controllo_targa (
    listing_id   BIGINT PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    esito        TEXT NOT NULL,          -- visibile | coperta | non_inquadrata | incerta | foto_internet | senza_foto | errore
    foto         INTEGER,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
