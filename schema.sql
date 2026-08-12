CREATE TABLE IF NOT EXISTS devices (
    token       TEXT PRIMARY KEY,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pushes (
    id        BIGSERIAL PRIMARY KEY,
    channel   TEXT NOT NULL,
    type      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    text      TEXT NOT NULL,
    pushed    BOOLEAN NOT NULL DEFAULT true,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE pushes ADD COLUMN IF NOT EXISTS pushed BOOLEAN NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS idx_pushes_ts ON pushes (ts DESC);
