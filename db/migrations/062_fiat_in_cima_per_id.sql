-- 17/11 11:15: la voce "in cima" della Fiat 500 rossa (fiat, 68.000 km, 7.900 €) riconosce l'annuncio anche per id
-- (21604, Subito, Triuggio): la riprova del giudice AI la ricontrolla per prima. Idempotente.
UPDATE sito_impostazioni
SET valore = (SELECT jsonb_agg(CASE WHEN lower(coalesce(e->>'marca', '')) LIKE 'fiat%' AND (e->>'km') = '68000'
                                         AND (e->>'prezzo') = '7900' AND e->>'id' IS NULL
                                    THEN e || '{"id": 21604}'::jsonb ELSE e END ORDER BY n)
              FROM jsonb_array_elements(sito_impostazioni.valore) WITH ORDINALITY AS x(e, n)),
    aggiornato = now(), da = 'migrazione 062'
WHERE chiave = 'in_cima' AND jsonb_typeof(valore) = 'array' AND jsonb_array_length(valore) > 0;
