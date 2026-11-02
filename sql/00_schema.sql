-- =============================================================================
-- STEP 00 — Schema bootstrap
-- Creates the veritas application schema and required extensions.
-- Must run first (lexicographic order: 00_*).
-- Rule 6: search_path enforced.
-- =============================================================================

SET search_path = veritas, public;

-- Application schema
CREATE SCHEMA IF NOT EXISTS veritas;

-- Extensions — require superuser, run before any table creation
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
