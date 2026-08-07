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
    ts        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pushes_ts ON pushes (ts DESC);
