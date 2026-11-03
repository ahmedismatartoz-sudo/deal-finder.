-- 3/11 notte (ricostruzione): abbonamenti per id, eventi Stripe, appuntamenti, invii una volta per destinatario,
-- disiscritti, consensi, dati riservati fuori dal repo, account preparati da Render. Tutto IF NOT EXISTS: alcune
-- tabelle esistevano già, create dal codice del sito.

-- Stripe: ogni evento si applica una volta sola
CREATE TABLE IF NOT EXISTS stripe_eventi (
    id          TEXT PRIMARY KEY,
    tipo        TEXT,
    created     BIGINT,
    ricevuto_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    esito       TEXT
);

-- Stripe: stato per abbonamento (un cliente può averne più di uno: la disdetta di uno non chiude l'altro)
CREATE TABLE IF NOT EXISTS abbonamenti (
    subscription_id TEXT PRIMARY KEY,
    dealer_id       BIGINT REFERENCES dealers(id) ON DELETE SET NULL,
    customer        TEXT,
    stato           TEXT,
    piano           TEXT,
    price_id        TEXT,
    fino            TIMESTAMPTZ,
    past_due_dal    TIMESTAMPTZ,
    evento_created  BIGINT NOT NULL DEFAULT 0,
    aggiornato_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS abbonamenti_dealer_idx ON abbonamenti (dealer_id);
CREATE INDEX IF NOT EXISTS abbonamenti_customer_idx ON abbonamenti (customer);
CREATE INDEX IF NOT EXISTS dealers_stripe_customer_idx ON dealers (stripe_customer) WHERE stripe_customer IS NOT NULL;

-- account spento dall'amministratore: il pagamento non lo riaccende
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS disattivato_da_admin BOOLEAN NOT NULL DEFAULT false;
-- il permesso "appuntamenti" dato dal piano Max (Stripe): si toglie solo se l'ha dato Stripe, non se l'ha dato l'admin
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS appuntamenti_da_stripe BOOLEAN NOT NULL DEFAULT false;
-- fine della tolleranza dopo un pagamento non riuscito (past_due): fino a quel momento l'accesso resta
ALTER TABLE dealers ADD COLUMN IF NOT EXISTS abbonamento_tolleranza TIMESTAMPTZ;

-- Abbonati Max: dati dell'azienda e richieste di appuntamento (prima create alla prima richiesta del sito)
CREATE TABLE IF NOT EXISTS aziende_abbonati (
    dealer_id  BIGINT PRIMARY KEY,
    dati       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS appuntamenti (
    id         BIGSERIAL PRIMARY KEY,
    dealer_id  BIGINT NOT NULL,
    listing_id BIGINT NOT NULL,
    giorno     DATE NOT NULL,
    fascia     TEXT NOT NULL,
    ora        TEXT,
    nota       TEXT,
    stato      TEXT NOT NULL DEFAULT 'richiesto',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE appuntamenti ADD COLUMN IF NOT EXISTS proposte JSONB;
ALTER TABLE appuntamenti ADD COLUMN IF NOT EXISTS impronta TEXT;
ALTER TABLE appuntamenti ADD COLUMN IF NOT EXISTS email_ok BOOLEAN;
ALTER TABLE appuntamenti ADD COLUMN IF NOT EXISTS email_esito TEXT;
CREATE INDEX IF NOT EXISTS appuntamenti_dealer_listing_idx ON appuntamenti (dealer_id, listing_id, created_at DESC);

-- Invii esterni (email, SMS, campagne) una volta per destinatario e per lavoro: un riavvio non rimanda mai.
-- Il destinatario è salvato solo come impronta (sha256), mai in chiaro.
CREATE TABLE IF NOT EXISTS invii (
    lavoro            TEXT NOT NULL,
    destinatario_hash TEXT NOT NULL,
    canale            TEXT NOT NULL DEFAULT 'email',
    stato             TEXT NOT NULL DEFAULT 'in_invio',     -- in_invio | inviato
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (lavoro, destinatario_hash)
);

-- Chi non vuole più ricevere nulla (disiscritti, bloccati, rimbalzati, chi ha detto no): esclusi da ogni invio e
-- da ogni import in Brevo, per sempre
CREATE TABLE IF NOT EXISTS disiscritti (
    email_hash TEXT PRIMARY KEY,
    motivo     TEXT,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Consenso esplicito a ricevere comunicazioni (obbligatorio per gli SMS promozionali)
CREATE TABLE IF NOT EXISTS consensi (
    canale        TEXT NOT NULL,                 -- sms | email
    contatto_hash TEXT NOT NULL,
    fonte         TEXT NOT NULL,                 -- es. "modulo sito 3/11", "risposta WhatsApp 2/11"
    dato_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revocato_at   TIMESTAMPTZ,
    PRIMARY KEY (canale, contatto_hash)
);

-- Liste con dati personali che servono al codice (prima in data/*.json nel repo): solo nel database
CREATE TABLE IF NOT EXISTS dati_riservati (
    nome        TEXT PRIMARY KEY,
    dati        JSONB NOT NULL,
    caricato_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Contatti già caricati in Brevo (prima creata dal codice di auto_invio)
CREATE TABLE IF NOT EXISTS brevo_inviati (
    email  TEXT PRIMARY KEY,
    quando TIMESTAMPTZ DEFAULT now()
);

-- Account preparati su Render (ACCOUNT_CLIENTI ecc.): impronta di ogni campo, per applicare solo quello che cambia
CREATE TABLE IF NOT EXISTS account_preparati (
    chiave        TEXT PRIMARY KEY,
    impronte      JSONB NOT NULL DEFAULT '{}',
    aggiornato_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- job_runs è letta a ogni avvio da _due/_una_volta
CREATE INDEX IF NOT EXISTS job_runs_job_idx ON job_runs (job, started_at DESC);
