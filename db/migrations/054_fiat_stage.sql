-- 13/11 (cliente: Fiat 500 rossa di nuovo sul sito): la 053 ha spento la voce "tolta", ma l'annuncio era rimasto
-- con stage='scartato' (il lavoro di avvio rimette solo le voci con "rimetti", e le voci spente non le legge).
-- Come fa il pannello quando si preme "rimetti": l'annuncio torna nell'elenco. Idempotente.
UPDATE listings SET stage = 'approfondito', stage_reason = 'da_verificare', last_checked_at = now()
WHERE make ILIKE 'fiat%' AND model ILIKE '%500%' AND mileage_km = 68000 AND price_eur = 7900
  AND stage = 'scartato' AND stage_reason ILIKE '%tolta dal cliente%';
