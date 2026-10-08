-- Auto economiche (1.000-2.000 €): soglia di margine dedicata
ALTER TABLE dealer_costs
    ADD COLUMN threshold_cheap_eur     INTEGER NOT NULL DEFAULT 1000,
    ADD COLUMN threshold_cheap_max_eur INTEGER NOT NULL DEFAULT 2000;
