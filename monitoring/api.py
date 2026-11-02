"""
Veritas Monitoring API — READ-ONLY
All tables are fully qualified as veritas.<table> — no search_path dependency.
"""

import json
import os
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    from veritas_core.logging.vlog import get_vlog
    _vlog = get_vlog(__name__)
except ImportError:
    import logging
    _vlog = logging.getLogger(__name__)

app = FastAPI(title="Veritas App API", version="1.1.0")
ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
DASHBOARD_PATH = REPO_ROOT / "veritas_operational" / "dashboard_template.html"
OP_SRC = REPO_ROOT / "veritas_operational" / "src"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def dashboard():
    return FileResponse(DASHBOARD_PATH)

DB_CONN = os.environ.get(
    "VERITAS_DB_CONN",
    "postgresql://veritas:veritas_app_2024@localhost:5434/veritas",
)

def _conn():
    return psycopg2.connect(DB_CONN)

def _serial(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")

def _json(data: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(data, default=_serial)))


def _summary_row(conn) -> dict[str, Any]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM veritas.scored_observations) AS total_scored,
                (SELECT ROUND(AVG(confidence)::numeric, 4)
                 FROM veritas.scored_observations
                 WHERE ts >= NOW() - INTERVAL '1 hour') AS avg_confidence,
                (SELECT COUNT(*)
                 FROM veritas.scored_observations
                 WHERE ts >= NOW() - INTERVAL '1 hour'
                   AND is_anomaly = TRUE) AS anomaly_count,
                (SELECT COUNT(*)
                 FROM veritas.scored_observations
                 WHERE physics_ok IS NULL) AS pending_enrichment,
                (SELECT COUNT(DISTINCT sensor_id)
                 FROM veritas.scored_observations
                 WHERE ts >= NOW() - INTERVAL '1 hour') AS sensor_count,
                (SELECT ROUND(COUNT(*) / 60.0, 2)
                 FROM veritas.scored_observations
                 WHERE scored_at >= NOW() - INTERVAL '1 minute') AS recent_rate,
                (SELECT ROUND(
                    percentile_cont(0.95) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (scored_at - ts)) * 1000
                    )::numeric, 2)
                 FROM veritas.scored_observations
                 WHERE scored_at >= NOW() - INTERVAL '1 hour') AS lag_p95_ms,
                (SELECT COUNT(*) FROM veritas.audit_log) AS total_audit
        """)
        row = dict(cur.fetchone())
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM veritas.quarantine_cards
                WHERE closed_at IS NULL
            """)
            row["quarantine_open"] = cur.fetchone()[0]
    except Exception:
        conn.rollback()
        row["quarantine_open"] = 0
    return row


def _runtime_metrics(conn) -> dict[str, Any]:
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    consumed,
                    scored,
                    anomalies,
                    malformed,
                    errors,
                    rate_per_sec,
                    latest_log,
                    updated_at
                FROM veritas.consumer_runtime_metrics
                WHERE id = 'veritas_consumer'
            """)
            row = cur.fetchone()
            if row:
                return dict(row)
    except Exception:
        conn.rollback()
    return {
        "consumed": 0,
        "scored": 0,
        "anomalies": 0,
        "malformed": 0,
        "errors": 0,
        "rate_per_sec": 0,
        "latest_log": "Consumer runtime metrics not available yet.",
        "updated_at": None,
    }


@app.get("/api/confidence")
def get_confidence():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    sensor_id,
                    ts              AS scored_at,
                    tau,
                    delta,
                    alpha,
                    confidence,
                    is_anomaly,
                    anomaly_label,
                    physics_ok,
                    llm_advisory
                FROM veritas.scored_observations
                ORDER BY ts DESC
                LIMIT 50
            """)
            return _json([dict(r) for r in cur.fetchall()])


@app.get("/api/stats")
def get_stats():
    with _conn() as conn:
        return _json(_summary_row(conn))


@app.get("/api/audit")
def get_audit():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT
                        ts              AS logged_at,
                        actor,
                        action,
                        entity_type     AS target_table,
                        entity_id       AS target_id,
                        metadata->>'notes' AS notes
                    FROM veritas.audit_log
                    ORDER BY ts DESC
                    LIMIT 30
                """)
                rows = [dict(r) for r in cur.fetchall()]
            except Exception:
                conn.rollback()
                rows = []
    return _json(rows)


@app.get("/api/sensors")
def get_sensors():
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ON (sensor_id)
                    sensor_id,
                    ts              AS latest_scored_at,
                    tau,
                    delta,
                    alpha,
                    confidence      AS latest_confidence,
                    value           AS latest_value,
                    unit,
                    is_anomaly,
                    physics_ok
                FROM veritas.scored_observations
                ORDER BY sensor_id, ts DESC
            """)
            return _json([dict(r) for r in cur.fetchall()])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/live")
def live():
    with _conn() as conn:
        summary = _summary_row(conn)
        runtime = _runtime_metrics(conn)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    sensor_id,
                    ts AS scored_at,
                    tau,
                    delta,
                    alpha,
                    confidence,
                    is_anomaly,
                    anomaly_label,
                    physics_ok,
                    llm_advisory,
                    value,
                    unit
                FROM veritas.scored_observations
                ORDER BY ts DESC
                LIMIT 50
            """)
            confidence_rows = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT DISTINCT ON (sensor_id)
                    sensor_id,
                    ts AS latest_scored_at,
                    tau,
                    delta,
                    alpha,
                    confidence AS latest_confidence,
                    value AS latest_value,
                    unit,
                    is_anomaly,
                    physics_ok
                FROM veritas.scored_observations
                ORDER BY sensor_id, ts DESC
            """)
            sensors = [dict(r) for r in cur.fetchall()]
            try:
                cur.execute("""
                    SELECT
                        ts AS logged_at,
                        actor,
                        action,
                        entity_type AS target_table,
                        entity_id AS target_id,
                        metadata->>'notes' AS notes
                    FROM veritas.audit_log
                    ORDER BY ts DESC
                    LIMIT 30
                """)
                audit = [dict(r) for r in cur.fetchall()]
            except Exception:
                conn.rollback()
                audit = []
    advisories = []
    for row in confidence_rows:
        if row.get("llm_advisory") or row.get("is_anomaly"):
            advisories.append(
                {
                    "sensor_id": row["sensor_id"],
                    "scored_at": row["scored_at"],
                    "confidence": row["confidence"],
                    "message": row.get("llm_advisory") or row.get("anomaly_label") or "Anomaly flagged",
                    "severity": "anomaly" if row.get("is_anomaly") else "info",
                }
            )
    return _json(
        {
            "summary": summary,
            "runtime": runtime,
            "confidence": confidence_rows,
            "sensors": sensors,
            "audit": audit,
            "advisories": advisories[:12],
        }
    )


@app.post("/api/upload")
async def upload(request: Request, filename: str = "upload.csv"):
    import sys

    if str(OP_SRC) not in sys.path:
        sys.path.insert(0, str(OP_SRC))
    from veritas_operational.app_pipeline import ensure_dirs, persist_payload, run_pipeline

    op_root = REPO_ROOT / "veritas_operational"
    _, output_dir, upload_dir = ensure_dirs(op_root)
    target = upload_dir / f"uploaded{Path(filename).suffix or '.csv'}"
    target.write_bytes(await request.body())
    payload = run_pipeline(
        target,
        op_root / "src" / "veritas_operational" / "config" / "default_rules.yaml",
    )
    payload["loader"]["source_path"] = str(target)
    persist_payload(output_dir, payload)
    return _json(payload)
