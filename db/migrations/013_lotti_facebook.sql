-- Lotti Facebook di Bright Data già importati: ogni giro importa solo quelli nuovi, senza riscaricarli
CREATE TABLE IF NOT EXISTS fb_snapshots (
    sid          TEXT PRIMARY KEY,
    rows         INTEGER,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
