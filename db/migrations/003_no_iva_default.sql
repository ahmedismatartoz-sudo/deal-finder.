-- Compravendita tra privati: nessuna IVA e nessuna riserva garanzia come valori predefiniti
ALTER TABLE dealer_costs ALTER COLUMN vat_margin_scheme SET DEFAULT false;
ALTER TABLE dealer_costs ALTER COLUMN warranty_reserve_eur SET DEFAULT 0;
UPDATE dealer_costs SET vat_margin_scheme = false, warranty_reserve_eur = 0;
