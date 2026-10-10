-- Le foto segnate come non scaricabili dal ciclo con il codice vecchio (prima della correzione imgid) si riprovano
DELETE FROM photo_cache WHERE content IS NULL;
