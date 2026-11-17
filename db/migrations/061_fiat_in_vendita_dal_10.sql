-- 17/11 10:59 (cliente): la Fiat 500 rossa in cima risulta in vendita da 7 giorni (dal 10/11). Idempotente.
UPDATE sito_impostazioni
SET valore = (SELECT jsonb_agg(CASE WHEN lower(coalesce(e->>'marca', '')) LIKE 'fiat%' AND (e->>'km') = '68000'
                                         AND (e->>'prezzo') = '7900'
                                    THEN e || '{"visto_dal": "2026-11-10T10:00:00+01:00"}'::jsonb ELSE e END ORDER BY n)
              FROM jsonb_array_elements(sito_impostazioni.valore) WITH ORDINALITY AS x(e, n)),
    aggiornato = now(), da = 'migrazione 061'
WHERE chiave = 'in_cima' AND jsonb_typeof(valore) = 'array' AND jsonb_array_length(valore) > 0;
