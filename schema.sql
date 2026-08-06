CREATE TABLE IF NOT EXISTS devices (
    token       TEXT PRIMARY KEY,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP INDEX IF EXISTS idx_devices_updated_at;

ALTER TABLE devices DROP COLUMN IF EXISTS region;
ALTER TABLE devices DROP COLUMN IF EXISTS localities;

DROP TABLE IF EXISTS feed_messages;

CREATE TABLE IF NOT EXISTS pushes (
    id        BIGSERIAL PRIMARY KEY,
    channel   TEXT NOT NULL,
    type      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    text      TEXT NOT NULL,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pushes_ts ON pushes (ts DESC);
