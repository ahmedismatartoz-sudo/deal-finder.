-- 11/11 12:16 (cliente): account di Felice creato dal pannello verso le 12:14, deve ancora pagare con carta
UPDATE dealers SET paga_carta = true
 WHERE id = (SELECT id FROM dealers
              WHERE role <> 'admin' AND created_at >= '2026-11-11 11:05:00+00'
                AND email NOT ILIKE '%@primama.it'
              ORDER BY created_at DESC LIMIT 1);
