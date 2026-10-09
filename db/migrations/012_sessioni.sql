-- Ogni accesso è una sessione personale: al massimo 2 dispositivi per account, uscita vera dal server
CREATE TABLE IF NOT EXISTS dealer_sessions (
    id           TEXT PRIMARY KEY,
    dealer_id    BIGINT NOT NULL REFERENCES dealers(id) ON DELETE CASCADE,
    method       TEXT NOT NULL,                 -- password | google | apple
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    revoked_why  TEXT
);
CREATE INDEX IF NOT EXISTS dealer_sessions_dealer_idx ON dealer_sessions (dealer_id, created_at DESC);
