-- 12/11 (margini precisi): rivendita dai commercianti (pricing/rivendita.py). Per ogni gruppo marca/modello/carburante
-- si leggono gli annunci di commercianti attivi e quelli spariti negli ultimi 90 giorni: indice parziale sui soli
-- commercianti (l'indice dei confronti listings_cmp_idx copre solo gli annunci attivi).
CREATE INDEX IF NOT EXISTS listings_comm_rivendita_idx ON listings (make, model, fuel, status)
    WHERE seller_type = 'commerciante';
