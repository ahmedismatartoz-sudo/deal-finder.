-- 12/11 (pagamenti e sicurezza)

-- 1. Pagamento in corso: sessione di checkout Stripe aperta per l'account. Serve a non aprirne una seconda (doppio
--    addebito) e a verificare il pagamento al ritorno da Stripe anche prima che arrivi il webhook.
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS checkout_session TEXT;
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS checkout_session_at TIMESTAMPTZ;

-- 2. Link di accesso monouso: impronta (sha256) del link già usato, mai il link in chiaro.
CREATE TABLE IF NOT EXISTS link_accesso_usati (
    impronta   TEXT PRIMARY KEY,
    dealer_id  BIGINT NOT NULL,
    usato_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS link_accesso_usati_at_idx ON link_accesso_usati (usato_at);

-- 3. Permesso "appuntamenti": da ora quello del piano Max è AUTOMATICO (calcolato a ogni richiesta dagli abbonamenti:
--    Max active/trialing, o past_due entro la tolleranza) e non si scrive più in dealers.permessi. In permessi restano
--    solo quelli dati a mano dall'amministratore, che durano finché l'amministratore non li toglie.
--    a) quelli scritti da Stripe (appuntamenti_da_stripe) diventano automatici
UPDATE dealers SET permessi = array_remove(permessi, 'appuntamenti'), appuntamenti_da_stripe = false
 WHERE appuntamenti_da_stripe;
--    b) quelli aggiunti dalla migrazione 039 a chi OGGI ha un Max attivo o in prova su Stripe: sono già coperti dal
--       permesso automatico (che alla disdetta sparisce da solo), quindi si tolgono da permessi. Tutti gli altri lo
--       tengono come permesso dato a mano: chi non ha Stripe, chi ha un Max ancora "incomplete", chi ha disdetto il
--       Max ma aveva avuto il permesso a mano. Lo toglie solo l'amministratore.
UPDATE dealers d SET permessi = array_remove(d.permessi, 'appuntamenti')
 WHERE 'appuntamenti' = ANY(COALESCE(d.permessi, '{}'))
   AND EXISTS (SELECT 1 FROM abbonamenti a WHERE a.dealer_id = d.id AND a.piano IN ('pro', 'max')
                 AND a.stato IN ('active', 'trialing'));

-- 4. Aperture: si distingue quella nata da una richiesta di appuntamento (annullabile dallo staff, che libera il
--    posto) da quella del contatto vero ("Parla col venditore"), che l'annullamento non tocca.
ALTER TABLE listing_opens ADD COLUMN IF NOT EXISTS da_appuntamento BOOLEAN NOT NULL DEFAULT false;
