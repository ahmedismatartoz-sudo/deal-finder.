-- 13/11 12:00 (cliente): togli i ribassi e la Fiat 500 rossa (fiat, 68.000 km, 7.900 €). Idempotente.
-- 1) ribasso spento (lo schema resta pronto da riaccendere dal pannello)
UPDATE sito_impostazioni
SET valore = jsonb_set(CASE WHEN jsonb_typeof(valore) = 'object' THEN valore ELSE '{}'::jsonb END, '{attivo}', 'false'::jsonb),
    aggiornato = now(), da = 'migrazione 056'
WHERE chiave = 'ribasso' AND coalesce(valore->>'attivo', 'true') <> 'false';

-- 2) Fiat fuori da "in cima" (le altre voci restano)
UPDATE sito_impostazioni
SET valore = COALESCE((SELECT jsonb_agg(e ORDER BY n)
                       FROM jsonb_array_elements(sito_impostazioni.valore) WITH ORDINALITY AS x(e, n)
                       WHERE NOT (lower(coalesce(e->>'marca', '')) LIKE 'fiat%'
                                  AND (e->>'km') = '68000' AND (e->>'prezzo') = '7900')), '[]'::jsonb),
    aggiornato = now(), da = 'migrazione 056'
WHERE chiave = 'in_cima' AND jsonb_typeof(valore) = 'array'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(sito_impostazioni.valore) AS y(e)
              WHERE lower(coalesce(e->>'marca', '')) LIKE 'fiat%' AND (e->>'km') = '68000' AND (e->>'prezzo') = '7900');

-- 3) voce "tolta" della Fiat di nuovo attiva (il lavoro di avvio la scarta anche nell'elenco)
UPDATE sito_scarti
SET attivo = true, motivo = '13/11 12:00 il cliente la toglie dal sito', rimesso_at = NULL, rimesso_da = NULL,
    da = 'migrazione 056'
WHERE listing_id IS NULL AND lower(marca) LIKE 'fiat%' AND km = 68000 AND prezzo = 7900 AND NOT attivo;

-- 4) e fuori subito dall'elenco
UPDATE listings SET stage = 'scartato', stage_reason = 'analisi_manuale: tolta dal cliente'
WHERE make ILIKE 'fiat%' AND model ILIKE '%500%' AND mileage_km = 68000 AND price_eur = 7900
  AND stage IS DISTINCT FROM 'scartato';
