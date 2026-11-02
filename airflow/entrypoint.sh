#!/bin/bash
# =============================================================================
# VERITAS Airflow Container Entrypoint
# =============================================================================
# Responsibilities:
#   1. Wait for dependent services (Postgres, Redis) to be reachable.
#   2. Initialise / migrate the Airflow metadata database.
#   3. Create the default admin user if it does not already exist.
#   4. Exec the requested Airflow sub-command (webserver, scheduler, worker…).
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    echo "[VERITAS-ENTRYPOINT] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
}

wait_for_tcp() {
    local host="$1"
    local port="$2"
    local max_attempts="${3:-30}"
    local attempt=0

    log "Waiting for ${host}:${port} ..."
    until nc -z "${host}" "${port}" 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ "${attempt}" -ge "${max_attempts}" ]; then
            log "ERROR: ${host}:${port} not reachable after ${max_attempts} attempts. Aborting."
            exit 1
        fi
        log "  attempt ${attempt}/${max_attempts} — retrying in 2s"
        sleep 2
    done
    log "${host}:${port} is reachable."
}

# ---------------------------------------------------------------------------
# Parse the Postgres DSN into host / port so we can wait for it
# ---------------------------------------------------------------------------
# DSN format: postgresql+psycopg2://user:pass@host:port/db
PG_HOST=$(echo "${AIRFLOW__CORE__SQL_ALCHEMY_CONN}" | sed -E 's|.*@([^:/]+).*|\1|')
PG_PORT=$(echo "${AIRFLOW__CORE__SQL_ALCHEMY_CONN}" | sed -E 's|.*:([0-9]+)/.*|\1|')
PG_PORT="${PG_PORT:-5432}"

# Parse the Redis DSN for host / port
# DSN format: redis://:@host:port/db
REDIS_HOST=$(echo "${AIRFLOW__CELERY__BROKER_URL}" | sed -E 's|redis://[^@]*@([^:/]+).*|\1|')
REDIS_PORT=$(echo "${AIRFLOW__CELERY__BROKER_URL}" | sed -E 's|.*:([0-9]+)/.*|\1|')
REDIS_PORT="${REDIS_PORT:-6379}"

# ---------------------------------------------------------------------------
# Wait for backend services
# ---------------------------------------------------------------------------
# Only block on service connectivity for roles that actually need the DB/broker
# immediately. The airflow-init container runs as root and handles migrations.
AIRFLOW_COMMAND="${1:-}"

case "${AIRFLOW_COMMAND}" in
    webserver|scheduler|worker|triggerer|"celery worker"|"celery flower")
        wait_for_tcp "${PG_HOST}" "${PG_PORT}"
        wait_for_tcp "${REDIS_HOST}" "${REDIS_PORT}"
        ;;
    *)
        # For version / help / other introspection commands, skip wait
        ;;
esac

# ---------------------------------------------------------------------------
# Database initialisation (performed only by the init container, uid=0)
# ---------------------------------------------------------------------------
if [ "${_AIRFLOW_DB_UPGRADE:-false}" = "true" ]; then
    log "Running airflow db upgrade ..."
    airflow db upgrade
    log "Database upgrade complete."
fi

# ---------------------------------------------------------------------------
# Admin user creation (performed only by the init container)
# ---------------------------------------------------------------------------
if [ "${_AIRFLOW_WWW_USER_CREATE:-false}" = "true" ]; then
    USERNAME="${_AIRFLOW_WWW_USER_USERNAME:-admin}"
    PASSWORD="${_AIRFLOW_WWW_USER_PASSWORD:-veritas2024}"

    # Check whether the user already exists; create only if absent.
    if airflow users list 2>/dev/null | grep -q "^${USERNAME} "; then
        log "Admin user '${USERNAME}' already exists — skipping creation."
    else
        log "Creating admin user '${USERNAME}' ..."
        airflow users create \
            --username  "${USERNAME}" \
            --password  "${PASSWORD}" \
            --firstname VERITAS \
            --lastname  Admin \
            --role      Admin \
            --email     "admin@veritas.local"
        log "Admin user '${USERNAME}' created."
    fi
fi

# ---------------------------------------------------------------------------
# Hand off to the real Airflow entrypoint / command
# ---------------------------------------------------------------------------
log "Starting: airflow $*"
exec airflow "$@"
