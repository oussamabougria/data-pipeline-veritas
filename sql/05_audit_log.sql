-- =============================================================================
-- STEP 05 — audit_log (Immutable TimescaleDB Hypertable)
-- Fix: site_id in compress_segmentby (from 11_audit_log.sql root file).
--      At 5 sites with 1TB of logs, unsegmented scans are a cluster killer.
--      With site_id in the segment, TimescaleDB skips irrelevant chunks entirely.
-- Rule 3: no float columns.
-- Rule 6: search_path enforced.
-- V1 health check: this is hypertable #3 of 3, compression_enabled = true.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL,
    ts          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    site_id     UUID,
    actor       VARCHAR(128) NOT NULL,
    action      VARCHAR(64)  NOT NULL,
    entity_type VARCHAR(64)  NOT NULL,
    entity_id   VARCHAR(128),
    before_state JSONB,
    after_state  JSONB,
    metadata    JSONB        NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
);

COMMENT ON TABLE audit_log IS
    'Immutable platform audit trail. Rows are NEVER updated or deleted. '
    'Hypertable partitioned monthly. site_id in compress_segmentby ensures '
    'multi-site deployments do not degrade into full-table scans.';

-- ── Hypertable ───────────────────────────────────────────────────────────────
SELECT create_hypertable(
    'veritas.audit_log',
    'ts',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists       => TRUE
);

-- ── Compression — site_id in segmentby (Fix from 11_audit_log.sql) ───────────
ALTER TABLE audit_log SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'site_id',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy(
    'veritas.audit_log',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ── Immutability (belt-and-suspenders) ───────────────────────────────────────
CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_site_ts
    ON audit_log(site_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON audit_log(actor, ts DESC);

CREATE INDEX IF NOT EXISTS idx_audit_entity
    ON audit_log(entity_type, entity_id, ts DESC);
