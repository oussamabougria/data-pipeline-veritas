-- =============================================================================
-- STEP 04 — scored_observations (TimescaleDB Hypertable)
-- Fix: confidence is GENERATED ALWAYS AS (tau * delta * alpha) STORED.
--      The DB owns the formula — Python cannot write a wrong confidence value.
-- Rule 3: zero FLOAT columns — tau/delta/alpha/confidence are NUMERIC(6,4).
-- Rule 4: confidence GENERATED; every row has skill_id, skill_version, tau, delta, alpha.
-- Rule 5: physics_ok = NULL means pending enrichment — Flink never calls the LLM.
-- Rule 6: search_path enforced.
-- V1 health check: this is hypertable #2 of 3, compression_enabled = true.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS scored_observations (
    id                  BIGSERIAL,
    site_id             UUID,
    sensor_id           VARCHAR(64)   NOT NULL,
    ts                  TIMESTAMPTZ   NOT NULL,
    value               NUMERIC(20,6),
    unit                VARCHAR(16),
    archetype           VARCHAR(12)   CHECK (archetype IN ('INERTIAL','CUMULATIVE','CYCLICAL','BINARY')),

    -- ── Scoring factors — NUMERIC(6,4), Rule 3: zero floats ──────────────────
    tau                 NUMERIC(6,4)  NOT NULL CHECK (tau   BETWEEN 0 AND 1),
    delta               NUMERIC(6,4)  NOT NULL CHECK (delta BETWEEN 0 AND 1),
    alpha               NUMERIC(6,4)  NOT NULL CHECK (alpha BETWEEN 0 AND 1),

    -- ── Rule 4: GENERATED column — DB owns the formula, Python never writes it ─
    confidence          NUMERIC(6,4)  GENERATED ALWAYS AS (tau * delta * alpha) STORED,

    -- ── Rule 4: Traceability — every row must carry these ────────────────────
    skill_id            VARCHAR(64)   NOT NULL,
    skill_version       VARCHAR(16)   NOT NULL,

    -- ── Anomaly detection ─────────────────────────────────────────────────────
    is_anomaly          BOOLEAN       NOT NULL DEFAULT FALSE,
    anomaly_score       NUMERIC(6,4),
    anomaly_label       TEXT,

    -- ── Rule 5: Physics enrichment (async Airflow only) ──────────────────────
    -- Flink writes NULL here. Airflow sweeps every 5 min and populates.
    physics_ok          BOOLEAN,                 -- NULL = pending enrichment
    physics_note        TEXT,
    physics_delta       NUMERIC(6,4),

    -- ── LLM advisory (async, Enrichment Path only — Rule 5) ──────────────────
    llm_advisory        TEXT,
    llm_model_version   TEXT,
    llm_enriched_at     TIMESTAMPTZ,

    -- ── ISO compliance (async Airflow only) ──────────────────────────────────
    compliance_checked  BOOLEAN       NOT NULL DEFAULT FALSE,
    compliance_passed   BOOLEAN,
    compliance_actions  JSONB,

    scored_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, ts)
);

COMMENT ON TABLE scored_observations IS
    'Output of the Critical Path (Flink scoring). '
    'confidence = tau * delta * alpha is a GENERATED column — the DB enforces the formula. '
    'physics_ok = NULL means Airflow enrichment is pending.';

COMMENT ON COLUMN scored_observations.confidence IS
    'GENERATED ALWAYS AS (tau * delta * alpha) STORED. '
    'Python must never write this column directly.';

COMMENT ON COLUMN scored_observations.physics_ok IS
    'NULL = Flink wrote this row, Airflow has not yet validated physics. '
    'TRUE/FALSE = Airflow enrichment complete. Critical Path never sets this.';

-- ── Hypertable ───────────────────────────────────────────────────────────────
SELECT create_hypertable(
    'veritas.scored_observations',
    'ts',
    chunk_time_interval => INTERVAL '1 week',
    if_not_exists       => TRUE
);

-- ── Compression (V1 check: compression_enabled = true) ───────────────────────
ALTER TABLE scored_observations SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sensor_id',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy(
    'veritas.scored_observations',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_scored_sensor_ts
    ON scored_observations(sensor_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_scored_confidence
    ON scored_observations(confidence, ts DESC);

CREATE INDEX IF NOT EXISTS idx_scored_skill
    ON scored_observations(skill_id, skill_version);

-- Airflow sweep query — physics_ok IS NULL finds pending rows fast
CREATE INDEX IF NOT EXISTS idx_scored_pending_physics
    ON scored_observations(ts DESC)
    WHERE physics_ok IS NULL;
