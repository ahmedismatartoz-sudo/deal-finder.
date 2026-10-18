-- Taratura automatica del valore di mercato (pricing/ricalcolo.py): per ogni gruppo (metodo, numero di confronti, fascia
-- di prezzo) il rapporto mediano tra il prezzo VERO degli annunci e il valore di mercato che il sistema calcola per loro
-- senza guardarli. Se in un gruppo il sistema sbaglia sistematicamente (es. +8%), il fattore lo corregge al calcolo dopo.
CREATE TABLE IF NOT EXISTS taratura (
    chiave   TEXT PRIMARY KEY,
    fattore  REAL NOT NULL,
    n        INTEGER NOT NULL,
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
