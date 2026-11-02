-- =============================================================================
-- STEP 01 — Sites and Sensors
-- Rule 6: search_path enforced at top of file.
-- Rule 3: all numeric columns use NUMERIC — zero FLOAT/REAL/DOUBLE PRECISION.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS sites (
    site_id     UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    location    VARCHAR(256),
    timezone    VARCHAR(64)  NOT NULL DEFAULT 'UTC',
    strict_mode BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sensors (
    sensor_id   VARCHAR(64)  NOT NULL PRIMARY KEY,
    site_id     UUID         REFERENCES veritas.sites(site_id),
    name        VARCHAR(128) NOT NULL DEFAULT '',
    archetype   VARCHAR(12)  NOT NULL DEFAULT 'INERTIAL'
                    CHECK (archetype IN ('INERTIAL','CUMULATIVE','CYCLICAL','BINARY')),
    unit        VARCHAR(16)  NOT NULL DEFAULT '',
    protocol    VARCHAR(16)  NOT NULL DEFAULT 'MQTT'
                    CHECK (protocol IN ('MQTT','OPCUA','MODBUS','REST','CSV','MANUAL')),
    tags        TEXT[]       NOT NULL DEFAULT '{}',
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensors_site ON sensors(site_id);
CREATE INDEX IF NOT EXISTS idx_sensors_archetype ON sensors(archetype);
