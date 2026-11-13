-- 13/11 10:52 (cliente): "rimettimi la Fiat 500 rossa ... a 69(00)" e "attivare ribassi".
-- Fiat 500 rossa (fiat, 68.000 km, prezzo annuncio 7.900 €) di nuovo sul sito, in cima, mostrata a 6.900 €,
-- con la scheda completa come tutte le altre. Ribasso acceso senza scadenza (si spegne dal pannello). Idempotente.

-- 1) in cima: si aggiunge la Fiat se non c'è già (le altre voci restano).
UPDATE sito_impostazioni
SET valore = (CASE WHEN jsonb_typeof(valore) = 'array' THEN valore ELSE '[]'::jsonb END)
             || '[{"marca": "fiat", "km": 68000, "prezzo": 7900, "mostra": 6900, "nota": "Fiat 500 rossa"}]'::jsonb,
    aggiornato = now(), da = 'migrazione 053'
WHERE chiave = 'in_cima'
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(sito_impostazioni.valore) = 'array'
                                                          THEN sito_impostazioni.valore ELSE '[]'::jsonb END) AS y(e)
                  WHERE lower(coalesce(e->>'marca', '')) LIKE 'fiat%'
                    AND (e->>'km') = '68000' AND (e->>'prezzo') = '7900');

INSERT INTO sito_impostazioni (chiave, valore, da)
SELECT 'in_cima', '[{"marca": "fiat", "km": 68000, "prezzo": 7900, "mostra": 6900, "nota": "Fiat 500 rossa"}]'::jsonb,
       'migrazione 053'
WHERE NOT EXISTS (SELECT 1 FROM sito_impostazioni WHERE chiave = 'in_cima');

-- 2) la voce "tolta" della Fiat non vale più.
UPDATE sito_scarti
SET attivo = false, motivo = '13/11 10:52 il cliente la rimette sul sito, in cima a 6.900 €',
    rimesso_at = now(), rimesso_da = 'migrazione 053', da = 'migrazione 053'
WHERE listing_id IS NULL AND lower(marca) LIKE 'fiat%' AND km = 68000 AND prezzo = 7900 AND attivo;

-- 3) ribasso acceso, senza scadenza, con lo stesso schema di fasce di prima.
UPDATE sito_impostazioni
SET valore = jsonb_build_object('attivo', true,
                                'schema', coalesce(valore->>'schema', '2000:700,6000:1000,10000:1500,15000:2000,20000:2500,30000:3000'),
                                'fino', NULL),
    aggiornato = now(), da = 'migrazione 053'
WHERE chiave = 'ribasso';

INSERT INTO sito_impostazioni (chiave, valore, da)
SELECT 'ribasso', '{"attivo": true, "schema": "2000:700,6000:1000,10000:1500,15000:2000,20000:2500,30000:3000", "fino": null}'::jsonb,
       'migrazione 053'
WHERE NOT EXISTS (SELECT 1 FROM sito_impostazioni WHERE chiave = 'ribasso');
