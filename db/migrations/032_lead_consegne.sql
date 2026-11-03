-- 4/11: le liste di commercianti trovate (nome, telefono, email) non si consegnano più come righe nei log di Render
-- (LEAD, LEADC, LEADE, LEADT, ...): vanno qui e si scaricano dal pannello admin (/api/admin/lead.csv?tipo=...).
CREATE TABLE IF NOT EXISTS lead_consegne (
    id    BIGSERIAL PRIMARY KEY,
    tipo  TEXT NOT NULL,
    dati  JSONB NOT NULL,
    at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lead_consegne_tipo_idx ON lead_consegne (tipo, at DESC);
