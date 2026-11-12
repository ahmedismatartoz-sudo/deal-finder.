-- 12/11 (buchi della raccolta, §3.1 del controllo 1): le stesse ~4.500 auto venivano rivalutate a ogni giro anche se
-- nulla era cambiato. Per saltarle serve sapere QUANDO è cambiato l'ultima volta il contenuto dell'annuncio (prezzo,
-- testo, dati dell'auto, telefono): contenuto_at, scritta da un trigger solo quando uno di questi campi cambia davvero
-- (una rilettura identica non la tocca). Le foto non si aggiornano mai dopo il primo salvataggio (db._save_photos):
-- non servono qui. Idempotente.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS contenuto_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION listings_contenuto_at() RETURNS trigger AS $$
BEGIN
    NEW.contenuto_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS listings_contenuto_at_trg ON listings;
CREATE TRIGGER listings_contenuto_at_trg
    BEFORE UPDATE OF price_eur, title, description, make, model, version_raw, year, mileage_km, fuel, gearbox,
                     power_kw, damage_declared, damage_class, telefono_ok, seller_type, status
    ON listings
    FOR EACH ROW
    WHEN (OLD.price_eur IS DISTINCT FROM NEW.price_eur
       OR OLD.title IS DISTINCT FROM NEW.title
       OR OLD.description IS DISTINCT FROM NEW.description
       OR OLD.make IS DISTINCT FROM NEW.make
       OR OLD.model IS DISTINCT FROM NEW.model
       OR OLD.version_raw IS DISTINCT FROM NEW.version_raw
       OR OLD.year IS DISTINCT FROM NEW.year
       OR OLD.mileage_km IS DISTINCT FROM NEW.mileage_km
       OR OLD.fuel IS DISTINCT FROM NEW.fuel
       OR OLD.gearbox IS DISTINCT FROM NEW.gearbox
       OR OLD.power_kw IS DISTINCT FROM NEW.power_kw
       OR OLD.damage_declared IS DISTINCT FROM NEW.damage_declared
       OR OLD.damage_class IS DISTINCT FROM NEW.damage_class
       OR OLD.telefono_ok IS DISTINCT FROM NEW.telefono_ok
       OR OLD.seller_type IS DISTINCT FROM NEW.seller_type
       OR OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION listings_contenuto_at();
