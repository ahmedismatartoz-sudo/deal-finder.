-- 13/11 13:17 (cliente): togli dal sito la Fiat Grande Punto 1.4 metano, 2009, 30.000 km, 2.400 €, Leffe (BG). Idempotente.
INSERT INTO sito_scarti (marca, anno, km, prezzo, motivo, attivo, da)
SELECT 'fiat', 2009, 30000, 2400, '13/11 13:17 il cliente la toglie dal sito (Grande Punto metano, Leffe BG)', true, 'migrazione 057'
WHERE NOT EXISTS (SELECT 1 FROM sito_scarti WHERE listing_id IS NULL AND marca = 'fiat' AND anno = 2009
                    AND km = 30000 AND prezzo = 2400);

UPDATE listings SET stage = 'scartato', stage_reason = 'analisi_manuale: tolta dal cliente'
WHERE make ILIKE 'fiat%' AND year = 2009 AND mileage_km = 30000 AND price_eur = 2400
  AND stage IS DISTINCT FROM 'scartato';
