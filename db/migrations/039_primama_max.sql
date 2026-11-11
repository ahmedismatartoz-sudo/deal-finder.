-- 11/11 (cliente): Primama ha già pagato il Max: niente blocco "deve pagare con carta", appuntamenti attivi.
UPDATE dealers SET paga_carta = false,
       permessi = CASE WHEN 'appuntamenti' = ANY(COALESCE(permessi, '{}')) THEN permessi
                       ELSE array_append(COALESCE(permessi, '{}'), 'appuntamenti') END
 WHERE role <> 'admin' AND created_at >= '2026-11-11 00:00:00+00'
   AND email ILIKE '%@primama.it';
-- 11/11 (cliente): tutti gli abbonati Max attivi hanno il permesso appuntamenti
UPDATE dealers d SET permessi = array_append(COALESCE(d.permessi, '{}'), 'appuntamenti')
 WHERE NOT ('appuntamenti' = ANY(COALESCE(d.permessi, '{}')))
   AND EXISTS (SELECT 1 FROM abbonamenti a WHERE a.dealer_id = d.id AND a.piano IN ('pro','max')
                AND a.stato IN ('active','trialing','past_due'));
