"""
Veritas Calibration DAG

Scheduled daily. Runs SkillCalibrator for all sensors with >= 7 days of data.
Stores calibration_runs record. Does NOT auto-activate — engineer must approve.

Rules (CLAUDE.md §4.2 + §5.5):
  - _vlog for all logging, never 'log'
  - All heavy imports inside task bodies
  - XCom = file paths only
  - Auto-calibration output is stored as a new skill version (not live update)
"""

import os

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

from veritas_core.logging.vlog import get_vlog

_vlog = get_vlog(__name__)

DB_URL = os.environ.get("VERITAS_DB_URL", "postgresql://veritas:veritas@postgres:5432/veritas")
MIN_HISTORY_DAYS = 7


@dag(
    dag_id="veritas_calibration",
    description="Daily skill auto-calibration for sensors with >= 7 days history",
    schedule_interval="0 2 * * *",   # 02:00 UTC daily
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["veritas", "calibration"],
)
def veritas_calibration():

    @task
    def list_sensors_for_calibration() -> str:
        """
        Find all sensors that have >= MIN_HISTORY_DAYS of scored_observations.
        Returns file path with sensor list.
        """
        import json
        import tempfile
        import asyncio
        import asyncpg

        async def _fetch():
            conn = await asyncpg.connect(DB_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT sensor_id, site_id::text AS site_id, archetype,
                           MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                           COUNT(*) AS obs_count
                    FROM scored_observations
                    GROUP BY sensor_id, site_id, archetype
                    HAVING (MAX(ts) - MIN(ts)) >= INTERVAL '7 days'
                    ORDER BY sensor_id
                    """
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        sensors = asyncio.run(_fetch())
        # Convert non-serialisable types
        for s in sensors:
            s["first_ts"] = str(s["first_ts"])
            s["last_ts"] = str(s["last_ts"])

        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_calib_sensors_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(sensors, fh)
        _vlog.info("Found %d sensors eligible for calibration → %s", len(sensors), tmp.name)
        return tmp.name

    @task
    def run_calibration(sensors_path: str) -> str:
        """
        Run SkillCalibrator for each eligible sensor.
        Returns file path with calibration results.
        """
        import json
        import tempfile
        import asyncio
        import asyncpg
        from datetime import timezone

        from veritas_core.skills.calibrator import SkillCalibrator

        with open(sensors_path) as fh:
            sensors = json.load(fh)

        calibrator = SkillCalibrator()
        results = []

        async def _fetch_history(sensor_id: str, site_id: str):
            conn = await asyncpg.connect(DB_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT ts, value::float AS value,
                           confidence::float AS confidence,
                           tau::float AS tau,
                           delta::float AS delta,
                           alpha::float AS alpha
                    FROM scored_observations
                    WHERE sensor_id = $1 AND site_id = $2::uuid
                    ORDER BY ts ASC
                    """,
                    sensor_id, site_id,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        for sensor in sensors:
            sensor_id = sensor["sensor_id"]
            site_id = sensor["site_id"]
            try:
                rows = asyncio.run(_fetch_history(sensor_id, site_id))
                result = calibrator.calibrate(
                    sensor_id=sensor_id,
                    site_id=site_id,
                    rows=rows,
                )
                if result:
                    results.append({
                        "sensor_id": sensor_id,
                        "site_id": site_id,
                        "days_of_history": result.days_of_history,
                        "sigma_ref": str(result.sigma_ref),
                        "decay_lambda": str(result.decay_lambda),
                        "n_ref": result.n_ref,
                        "T_period_s": result.T_period_s,
                        "quarantine_thr": str(result.quarantine_thr),
                        "archetype": sensor.get("archetype", "INERTIAL"),
                    })
                    _vlog.info("Calibrated sensor %s: %s", sensor_id, result)
            except Exception as exc:
                _vlog.warning("Calibration failed for sensor %s: %s", sensor_id, exc)

        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_calib_results_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(results, fh)
        _vlog.info("Calibration complete: %d sensors → %s", len(results), tmp.name)
        return tmp.name

    @task
    def store_calibration_runs(sensors_path: str, results_path: str) -> None:
        """
        Persist calibration_runs records to DB.
        Does NOT activate any skill — engineer approval required.
        """
        import json
        import asyncio
        import asyncpg

        with open(results_path) as fh:
            results = json.load(fh)

        async def _store():
            conn = await asyncpg.connect(DB_URL)
            try:
                async with conn.transaction():
                    for r in results:
                        await conn.execute(
                            """
                            INSERT INTO calibration_runs
                                (sensor_id, site_id, days_of_history, computed_params)
                            VALUES ($1, $2::uuid, $3, $4)
                            """,
                            r["sensor_id"],
                            r["site_id"],
                            r["days_of_history"],
                            json.dumps({
                                "sigma_ref": r["sigma_ref"],
                                "decay_lambda": r["decay_lambda"],
                                "n_ref": r["n_ref"],
                                "T_period_s": r["T_period_s"],
                                "quarantine_thr": r["quarantine_thr"],
                                "archetype": r["archetype"],
                            }),
                        )
            finally:
                await conn.close()

        asyncio.run(_store())
        _vlog.info("Stored %d calibration_runs records (awaiting engineer approval)", len(results))

    # ---- Wire up the DAG ----
    sensors = list_sensors_for_calibration()
    results = run_calibration(sensors)
    store_calibration_runs(sensors, results)


dag_instance = veritas_calibration()
