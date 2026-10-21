-- 21/10: confronti delle auto di fascia alta con la base prezzi di tutta Italia (marca/modello scritti in modo diverso)
CREATE INDEX IF NOT EXISTS listings_cmp_lower_idx ON listings (lower(make), lower(model), lower(fuel)) WHERE status = 'attivo';
