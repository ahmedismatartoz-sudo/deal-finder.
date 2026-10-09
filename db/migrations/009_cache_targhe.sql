-- Risultati della ricerca targhe (a pagamento): una targa già cercata non si paga di nuovo.
-- Si salva solo l'impronta della targa (hash), mai la targa in chiaro.
CREATE TABLE IF NOT EXISTS plate_cache (
    plate_hash TEXT PRIMARY KEY,
    vehicle JSONB,
    searched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
