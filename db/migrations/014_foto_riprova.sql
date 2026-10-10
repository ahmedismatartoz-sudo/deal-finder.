-- Le foto fallite prima della correzione degli indirizzi di Subito ("imgid:") si riprovano
DELETE FROM photo_cache WHERE content IS NULL;
