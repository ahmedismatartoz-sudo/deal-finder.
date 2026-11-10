-- 10/11 (cliente): account creati dal pannello che devono comunque pagare con carta prima di vedere le auto
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS paga_carta BOOLEAN NOT NULL DEFAULT false;
-- l'account Autoriparazione BP creato il 10/11 pomeriggio: deve pagare con carta (Piano Base)
UPDATE dealers SET paga_carta = true
 WHERE id = (SELECT id FROM dealers
              WHERE role <> 'admin' AND created_at >= '2026-11-10 12:00:00+00'
                AND (name ILIKE '%autoripar%' OR company ILIKE '%autoripar%' OR email ILIKE '%autoripar%')
              ORDER BY created_at DESC LIMIT 1);
