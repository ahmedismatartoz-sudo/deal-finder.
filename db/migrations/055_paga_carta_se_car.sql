-- 13/11 11:46 (cliente): account nuovo creato dal pannello verso le 11:44 (ora di Roma), "togli subito tutto, devono
-- ancora pagare": come per Felice (040), deve pagare con carta, quindi nessun accesso finché non paga.
UPDATE dealers SET paga_carta = true
 WHERE id = (SELECT id FROM dealers
              WHERE role <> 'admin' AND created_at >= '2026-11-13 10:20:00+00'
              ORDER BY created_at DESC LIMIT 1);
