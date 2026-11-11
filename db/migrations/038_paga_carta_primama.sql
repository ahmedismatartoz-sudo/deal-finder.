-- 11/11 (cliente): account Primama creato dal pannello, deve ancora pagare con carta: nessun accesso alle auto
UPDATE dealers SET paga_carta = true
 WHERE role <> 'admin' AND created_at >= '2026-11-11 00:00:00+00'
   AND email ILIKE '%@primama.it';
