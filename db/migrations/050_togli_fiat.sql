-- 12/11 19:13 (cliente): la Fiat 500 rossa (fiat, 68.000 km, prezzo dell'annuncio 7.900 €, oggi in cima a 6.900 €)
-- esce del tutto dal sito. Stesse tabelle della 047/048 (sito_impostazioni, sito_scarti). Idempotente.

-- 1) "in cima": si toglie solo la voce della Fiat (le altre eventualmente messe dal pannello restano).
--    Se non resta niente la lista è vuota ('[]'): vuota = nessuna auto in cima (come scovo.IN_CIMA, ora vuota).
UPDATE sito_impostazioni
SET valore = COALESCE((
        SELECT jsonb_agg(e ORDER BY n)
        FROM jsonb_array_elements(sito_impostazioni.valore) WITH ORDINALITY AS x(e, n)
        WHERE NOT (lower(coalesce(e->>'marca', '')) LIKE 'fiat%'
                   AND (e->>'km') = '68000' AND (e->>'prezzo') = '7900')
    ), '[]'::jsonb),
    aggiornato = now(),
    da = 'migrazione 050'
WHERE chiave = 'in_cima'
  AND jsonb_typeof(valore) = 'array'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(sito_impostazioni.valore) AS y(e)
              WHERE lower(coalesce(e->>'marca', '')) LIKE 'fiat%'
                AND (e->>'km') = '68000' AND (e->>'prezzo') = '7900');

-- 2) auto tolte: la voce della Fiat torna attiva (fuori dal sito)...
UPDATE sito_scarti
SET attivo = true, motivo = '12/11 19:13 il cliente la toglie dal sito', rimesso_at = NULL, rimesso_da = NULL,
    da = 'migrazione 050'
WHERE listing_id IS NULL AND lower(marca) LIKE 'fiat%' AND km = 68000 AND prezzo = 7900
  AND (attivo IS DISTINCT FROM true OR motivo IS DISTINCT FROM '12/11 19:13 il cliente la toglie dal sito');

-- ...oppure si aggiunge se manca. Solo se la tabella è già in uso (non vuota): con la tabella vuota vale ancora
-- data/scarti_sito.json (che ha già la Fiat tolta), e una sola riga qui farebbe sparire le altre voci del file.
INSERT INTO sito_scarti (listing_id, marca, modello, anno, km, prezzo, motivo, attivo, da)
SELECT NULL, 'fiat', '500', NULL, 68000, 7900, '12/11 19:13 il cliente la toglie dal sito', true, 'migrazione 050'
WHERE EXISTS (SELECT 1 FROM sito_scarti)
  AND NOT EXISTS (SELECT 1 FROM sito_scarti
                  WHERE listing_id IS NULL AND lower(marca) LIKE 'fiat%' AND km = 68000 AND prezzo = 7900);
