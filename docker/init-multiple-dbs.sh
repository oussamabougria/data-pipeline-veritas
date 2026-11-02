#!/usr/bin/env bash
# =============================================================================
# VERITAS — PostgreSQL multi-database initialiser
#
# Responsibilities:
#   1. Create the 'veritas' application role with password from VERITAS_DB_PASSWORD.
#   2. Create the 'veritas' and 'airflow' databases.
#   3. Run every *.sql file in /docker-entrypoint-initdb.d/sql/ (lexicographic
#      order) against the veritas database.
#   4. Grant the veritas role full access to the veritas schema.
#
# This script is mounted at:
#   /docker-entrypoint-initdb.d/00_init_dbs.sh
# and executed once by the postgres Docker entrypoint on first boot.
# =============================================================================

set -euo pipefail

VERITAS_PASS="${VERITAS_DB_PASSWORD:-veritas}"

log() { echo "[veritas-init] $*"; }

# ── 1. Create application roles ───────────────────────────────────────────────
log "Creating application roles ..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'veritas') THEN
        CREATE ROLE veritas WITH LOGIN PASSWORD '${VERITAS_PASS}';
        RAISE NOTICE 'Role veritas created.';
    ELSE
        RAISE NOTICE 'Role veritas already exists — skipping.';
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airflow') THEN
        CREATE ROLE airflow WITH LOGIN PASSWORD 'airflow';
        RAISE NOTICE 'Role airflow created.';
    ELSE
        RAISE NOTICE 'Role airflow already exists — skipping.';
    END IF;
END
\$\$;
EOSQL

# ── 2. Create databases ───────────────────────────────────────────────────────
log "Creating databases ..."

if psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -tc "SELECT 1 FROM pg_database WHERE datname='veritas'" | grep -q 1; then
    log "Database 'veritas' already exists — skipping."
else
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
         -c "CREATE DATABASE veritas OWNER veritas;"
    log "Database 'veritas' created."
fi

if psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -tc "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1; then
    log "Database 'airflow' already exists — skipping."
else
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
         -c "CREATE DATABASE airflow OWNER airflow;"
    log "Database 'airflow' created."
fi

# ── 3. Run schema migrations ──────────────────────────────────────────────────
SQL_DIR="/docker-entrypoint-initdb.d/sql"

if [ -d "$SQL_DIR" ]; then
    log "Running schema migrations from $SQL_DIR ..."
    # sort -V for natural version order (00_, 01_, 02_, ...)
    for f in $(ls -1 "$SQL_DIR"/*.sql 2>/dev/null | sort -V); do
        log "  Applying: $(basename "$f") ..."
        psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname veritas < "$f"
    done
    log "Schema migrations complete."
else
    log "WARNING: $SQL_DIR not found — no migrations applied."
fi

# ── 4. Grant permissions ──────────────────────────────────────────────────────
log "Granting permissions to veritas role ..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname veritas <<EOSQL
-- Rule 6: search_path set on every connection
SET search_path = veritas, public;

GRANT USAGE  ON SCHEMA veritas TO veritas;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA veritas TO veritas;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA veritas TO veritas;
ALTER DEFAULT PRIVILEGES IN SCHEMA veritas GRANT ALL ON TABLES    TO veritas;
ALTER DEFAULT PRIVILEGES IN SCHEMA veritas GRANT ALL ON SEQUENCES TO veritas;
EOSQL

log "Initialisation complete."
