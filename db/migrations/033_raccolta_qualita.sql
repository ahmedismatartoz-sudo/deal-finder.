-- 3/11 (ricostruzione, fase 4: raccolta e qualità dei dati)
-- 1) Regola del cliente: nessun dato personale del venditore, solo telefono sì/no. I cellulari dei commercianti di
--    AutoScout24 non si salvano più (un commerciante può essere una ditta individuale): si cancellano quelli salvati.
--    Restano l'id del venditore e la ragione sociale.
UPDATE commercianti_as24 SET cellulari = '{}' WHERE cardinality(cellulari) > 0;

-- 2) Motivo della sparizione: '404', 'reindirizzato', 'rimosso', 'scaduto', 'ripubblicato'. 'scaduto' e
--    'ripubblicato' hanno anche uno stato a parte (status), così non contano come vendute.
-- 3) Sottotipo dell'alimentazione (mild, full, plug-in, bifuel): fuel resta il gruppo dei confronti.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS disappear_reason TEXT,
                     ADD COLUMN IF NOT EXISTS fuel_tipo TEXT;
-- L'indice su vehicle_id (ripubblicazioni) lo crea il cron con CREATE INDEX CONCURRENTLY (db.assicura_indice_veicolo):
-- qui bloccherebbe la tabella per tutta la durata.

-- 4) Punto di arrivo dei lavori lunghi a pezzi (es. rinormalizza:v2): dopo un riavvio si riprende dall'ultimo id.
CREATE TABLE IF NOT EXISTS lavori_cursore (
    job         TEXT PRIMARY KEY,
    ultimo_id   BIGINT NOT NULL DEFAULT 0,
    aggiornato  TIMESTAMPTZ NOT NULL DEFAULT now()
);
