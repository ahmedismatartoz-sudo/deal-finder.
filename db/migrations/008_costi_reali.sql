-- Costi reali: niente trasporto, passaggio a nome azienda 90 €, solo pulizia 50 €
ALTER TABLE dealer_costs ALTER COLUMN transport_eur SET DEFAULT 0;
ALTER TABLE dealer_costs ALTER COLUMN paperwork_eur SET DEFAULT 90;
ALTER TABLE dealer_costs ALTER COLUMN preparation_eur SET DEFAULT 50;
UPDATE dealer_costs SET transport_eur = 0, paperwork_eur = 90, preparation_eur = 50;
