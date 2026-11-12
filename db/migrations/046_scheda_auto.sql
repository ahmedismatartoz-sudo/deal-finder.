-- 12/11 (cliente: "sul sito l'auto deve essere più completa: allestimento, cambio, cilindrata ecc."):
-- scheda tecnica di ogni annuncio, chiavi in italiano (dealfinder/core/dati_auto.py).
--   dati_auto = {"ricerca": {...}, "pagina": {...}, "letto_il": "...", "esito_pagina": "ok|non_disponibile|errore",
--                "tentativi_pagina": n}
--   "ricerca": quello che le pagine di ricerca danno e che prima si buttava (solo i dati senza una colonna già loro);
--   "pagina":  la pagina dell'annuncio letta dal cron opportunità (solo auto sul sito o candidate, tetto giornaliero).
-- ADD COLUMN senza valore di default: istantaneo anche su una tabella grande (nessuna riscrittura).
ALTER TABLE listings ADD COLUMN IF NOT EXISTS dati_auto JSONB;

-- Quante pagine di annunci sono state lette per la scheda tecnica, giorno per giorno (tetto giornaliero).
CREATE TABLE IF NOT EXISTS scheda_letture (
    giorno  DATE PRIMARY KEY,
    lette   INTEGER NOT NULL DEFAULT 0,
    errori  INTEGER NOT NULL DEFAULT 0,
    aggiornato TIMESTAMPTZ NOT NULL DEFAULT now()
);
