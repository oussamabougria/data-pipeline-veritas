-- =============================================================================
-- STEP 03 — raw_observations (TimescaleDB Hypertable)
-- Rule 3: value is NUMERIC(20,6) — no FLOAT or DOUBLE PRECISION anywhere.
-- Rule 6: search_path enforced.
-- V1 health check: this is hypertable #1 of 3, compression_enabled = true.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS raw_observations (
    id          BIGSERIAL,
    site_id     UUID,
    sensor_id   VARCHAR(64)   NOT NULL,
    ts          TIMESTAMPTZ   NOT NULL,
    value       NUMERIC(20,6) NOT NULL,
    unit        VARCHAR(16)   NOT NULL DEFAULT '',
    protocol    VARCHAR(16)   NOT NULL DEFAULT 'MQTT',
    raw_payload JSONB         NOT NULL DEFAULT '{}',
    ingested_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, ts)
);

COMMENT ON TABLE raw_observations IS
    'Immutable ingest ring-buffer. Flink writes here. Never UPDATE or DELETE.';

-- ── Hypertable ───────────────────────────────────────────────────────────────
SELECT create_hypertable(
    'veritas.raw_observations',
    'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE
);

-- ── Compression (V1 check: compression_enabled = true) ───────────────────────
ALTER TABLE raw_observations SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy(
    'veritas.raw_observations',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_raw_sensor_ts
    ON raw_observations(sensor_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_raw_site_ts
    ON raw_observations(site_id, ts DESC);
