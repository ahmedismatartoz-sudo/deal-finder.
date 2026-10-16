-- Numero di telefono del venditore: si salva SOLO se c'è (sì/no), mai il numero.
-- AutoScout24: letto dalla pagina dell'annuncio (venditore con numero); Subito: numero scritto nel testo.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS telefono_ok BOOLEAN, ADD COLUMN IF NOT EXISTS telefono_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS tempo_reale (
    giro        TEXT PRIMARY KEY,           -- nuovi | verifica
    ultimo_at   TIMESTAMPTZ,
    stats       JSONB
);
