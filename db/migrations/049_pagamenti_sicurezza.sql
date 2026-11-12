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
--    b) quelli aggiunti dalla migrazione 039 a chi ha (o ha avuto) un abbonamento Max su Stripe: erano la copia del
--       pagamento, non una scelta dell'amministratore, e non si toglievano più alla disdetta. Diventano automatici.
--       Chi ha il permesso senza aver mai avuto un Max su Stripe (dato a mano) lo tiene.
UPDATE dealers d SET permessi = array_remove(d.permessi, 'appuntamenti')
 WHERE 'appuntamenti' = ANY(COALESCE(d.permessi, '{}'))
   AND EXISTS (SELECT 1 FROM abbonamenti a WHERE a.dealer_id = d.id AND a.piano IN ('pro', 'max'));
