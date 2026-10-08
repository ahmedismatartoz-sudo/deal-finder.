-- Riserve imprevisti per guasti meccanici e alto rischio; mercato "da sistemare"
ALTER TABLE dealer_costs
    ADD COLUMN contingency_fault_pct     REAL NOT NULL DEFAULT 0.25,
    ADD COLUMN contingency_high_risk_pct REAL NOT NULL DEFAULT 0.35;
ALTER TABLE valuations
    ADD COLUMN asis_median INTEGER,     -- prezzo tipico delle auto simili da sistemare
    ADD COLUMN asis_n      INTEGER;
ALTER TABLE listings ADD COLUMN problem_search BOOLEAN NOT NULL DEFAULT false;   -- trovato con ricerca mirata a guasti
