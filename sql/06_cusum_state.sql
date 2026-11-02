-- =============================================================================
-- STEP 06 — CUSUM Accumulator State
-- Rule 6: search_path enforced.
-- Rule 3: no float columns.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS sensor_cusum_state (
    sensor_id   VARCHAR(64)   NOT NULL,
    site_id     UUID          NOT NULL,
    s_high      NUMERIC(12,6) NOT NULL DEFAULT 0,
    s_low       NUMERIC(12,6) NOT NULL DEFAULT 0,
    mu_ref      NUMERIC(20,6) NOT NULL,
    k_cusum     NUMERIC(8,4)  NOT NULL DEFAULT 0.5,
    h_threshold NUMERIC(8,4)  NOT NULL DEFAULT 5.0,
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sensor_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_cusum_sensor
    ON sensor_cusum_state(sensor_id);
