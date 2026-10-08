-- Rivendita sui due mercati e scelta per commerciante (default: vende come privato)
ALTER TABLE valuations
    ADD COLUMN resale_prudent_private INTEGER,
    ADD COLUMN resale_median_private  INTEGER,
    ADD COLUMN resale_prudent_dealer  INTEGER,
    ADD COLUMN resale_median_dealer   INTEGER;
ALTER TABLE dealers ADD COLUMN resale_as TEXT NOT NULL DEFAULT 'privato';   -- privato | commerciante
