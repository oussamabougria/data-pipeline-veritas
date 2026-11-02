-- =============================================================================
-- STEP 02 — Skill Registry
-- Rule 6: search_path enforced.
-- Rule 3: no float columns.
-- =============================================================================

SET search_path = veritas, public;

CREATE TABLE IF NOT EXISTS skill_registry (
    skill_id        VARCHAR(64)  NOT NULL,
    version         VARCHAR(16)  NOT NULL,
    author          VARCHAR(128) NOT NULL DEFAULT 'system',
    archetype       VARCHAR(12)  NOT NULL DEFAULT 'INERTIAL'
                        CHECK (archetype IN ('INERTIAL','CUMULATIVE','CYCLICAL','BINARY')),
    tags            TEXT[]       NOT NULL DEFAULT '{}',
    format          VARCHAR(8)   NOT NULL DEFAULT 'yaml'
                        CHECK (format IN ('yaml','python')),
    content         TEXT         NOT NULL DEFAULT '',
    source          VARCHAR(32)  NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','llm_suggested','calibrated')),
    active          BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_skill_active
    ON skill_registry(skill_id)
    WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_skill_archetype ON skill_registry(archetype);
