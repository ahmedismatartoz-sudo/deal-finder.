-- 27/11 sera (decisione del cliente): la posizione del commerciante si chiede UNA volta sola e si ricorda.
-- Si salvano solo la posizione arrotondata (lat/lon a 2 decimali, circa 1 km, colonne della 018) e il nome del comune.
-- pos_fonte: 'gps' (consenso del browser), 'manuale' (città o CAP scritti), 'milano' (rifiutata: centro di default).
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS pos_comune TEXT;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS pos_fonte TEXT;
-- le posizioni già salvate con 3 decimali (circa 100 m) si arrotondano a 2
UPDATE dealers SET lat = round(lat::numeric, 2), lon = round(lon::numeric, 2) WHERE lat IS NOT NULL OR lon IS NOT NULL;
