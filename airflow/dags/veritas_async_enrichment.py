"""
Veritas Async Enrichment DAG

Triggered by: new records in scored.observations Kafka topic (or scheduled interval).
Tasks:
  1. physics_llm_validate  — Claude API tool-use, updates physics_ok / physics_delta
  2. compliance_check      — ISO threshold evaluation, writes compliance_checks + artefacts
  3. twin_sync             — push score to MindSphere digital twin
  4. fed_update            — send model weight update to federated aggregator
  5. audit_write           — append immutable audit log entry

Rules (CLAUDE.md §4.2):
  - Module-level logger: _vlog = logging.getLogger(__name__)
  - NEVER use 'log' as a variable — Airflow reserves it
  - All heavy imports inside task function bodies
  - XCom = file paths only, never DataFrames
"""

import os

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

from veritas_core.logging.vlog import get_vlog

_vlog = get_vlog(__name__)

DB_URL = os.environ.get("VERITAS_DB_URL", "postgresql://veritas:veritas@postgres:5432/veritas")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINDSPHERE_BASE_URL = os.environ.get("MINDSPHERE_BASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


@dag(
    dag_id="veritas_async_enrichment",
    description="Physics LLM validation, compliance checks, twin sync, fed update, audit",
    schedule_interval="*/5 * * * *",   # every 5 minutes
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["veritas", "enrichment"],
)
def veritas_async_enrichment():

    @task
    def fetch_pending_scored_ids(**context) -> str:
        """
        Fetch IDs of scored_observations needing async enrichment.
        Returns a JSON file path via XCom.
        """
        import json
        import tempfile
        import asyncpg
        import asyncio

        async def _fetch():
            conn = await asyncpg.connect(DB_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT id, ts::text, sensor_id, site_id, value::text, unit,
                           archetype, confidence::text,
                           tau::text, delta::text, alpha::text,
                           skill_id, skill_version
                    FROM scored_observations
                    WHERE physics_ok IS NULL AND compliance_checked = FALSE
                    ORDER BY ts DESC
                    LIMIT 500
                    """
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        records = asyncio.run(_fetch())
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False,
            dir="/tmp", prefix="veritas_pending_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(records, fh)
        _vlog.info("Fetched %d pending scored records → %s", len(records), tmp.name)
        return tmp.name

    @task
    def physics_llm_validate(pending_path: str) -> str:
        """
        Run Claude API physics validation for each pending observation.
        Returns file path with results.
        """
        import asyncio
        import json
        import os
        import tempfile
        from decimal import Decimal

        from veritas_core.physics_llm.engine import PhysicsLLMEngine
        from veritas_core.skills.loader import SkillLoader

        with open(pending_path) as fh:
            records = json.load(fh)

        engine = PhysicsLLMEngine(api_key=ANTHROPIC_API_KEY)

        # Build a synchronous DB fetcher for SkillLoader
        import psycopg2
        import psycopg2.extras

        def _db_fetcher(sensor_id, archetype, tags):
            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM skill_registry WHERE archetype=%s AND active=TRUE",
                        (archetype,)
                    )
                    return [dict(r) for r in cur.fetchall()]

        loader = SkillLoader(db_fetcher=_db_fetcher)

        async def _validate_all():
            results = []
            for rec in records:
                try:
                    ctx = loader.resolve(
                        sensor_id=rec["sensor_id"],
                        archetype=rec["archetype"],
                        tags=[],
                    )
                    result = await engine.validate(
                        sensor_id=rec["sensor_id"],
                        archetype=rec["archetype"],
                        value=Decimal(rec["value"]),
                        previous_value=Decimal("0"),
                        unit=rec["unit"],
                        confidence=Decimal(rec["confidence"]),
                        age_seconds=0.0,
                        physics_laws=ctx.physics_laws,
                        metadata={},
                        llm_prompt_pack=ctx.llm_prompt_pack,
                    )
                    results.append({
                        "id": rec["id"],
                        "ts": rec["ts"],
                        "physics_ok": result.physics_ok,
                        "physics_delta": str(result.confidence_delta),
                        "physics_note": result.note,
                    })
                except Exception as exc:
                    _vlog.warning("Physics validation error for obs %s: %s", rec.get("id"), exc)
                    results.append({
                        "id": rec["id"],
                        "ts": rec["ts"],
                        "physics_ok": False,
                        "physics_delta": "-0.0500",
                        "physics_note": f"error: {exc}",
                    })
            return results

        results = asyncio.run(_validate_all())

        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_physics_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(results, fh)
        _vlog.info("Physics validation complete: %d records → %s", len(results), tmp.name)
        return tmp.name

    @task
    def compliance_check(pending_path: str, physics_path: str) -> str:
        """
        Evaluate ISO compliance for each scored observation.
        Writes compliance_checks and generates artefacts.
        Returns path to compliance results file.
        """
        import json
        import tempfile
        import asyncio
        import asyncpg
        from decimal import Decimal

        from veritas_core.compliance.engine import ComplianceEngine
        from veritas_core.compliance.artefacts import generate_artefacts
        from veritas_core.skills.loader import SkillLoader

        with open(pending_path) as fh:
            records = json.load(fh)
        with open(physics_path) as fh:
            physics_results = {r["id"]: r for r in json.load(fh)}

        engine = ComplianceEngine()

        import psycopg2, psycopg2.extras

        def _db_fetcher(sensor_id, archetype, tags):
            with psycopg2.connect(DB_URL) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM skill_registry WHERE archetype=%s AND active=TRUE",
                        (archetype,)
                    )
                    return [dict(r) for r in cur.fetchall()]

        loader = SkillLoader(db_fetcher=_db_fetcher)

        compliance_results = []
        for rec in records:
            try:
                ctx = loader.resolve(
                    sensor_id=rec["sensor_id"],
                    archetype=rec["archetype"],
                    tags=[],
                )
                phys = physics_results.get(rec["id"], {})
                delta = Decimal(phys.get("physics_delta", "0"))
                effective_confidence = max(
                    Decimal("0"),
                    Decimal(rec["confidence"]) + delta
                )

                results = engine.evaluate(
                    confidence=effective_confidence,
                    iso_standards=ctx.iso_standards,
                    sensor_id=rec["sensor_id"],
                    site_id=rec["site_id"],
                    scored_obs_id=rec["id"],
                )
                artefacts = generate_artefacts(results)
                compliance_results.append({
                    "id": rec["id"],
                    "passed": all(r.passed for r in results),
                    "results": [
                        {
                            "standard": r.standard,
                            "passed": r.passed,
                            "action_type": r.action_type,
                            "payload": r.action_payload,
                        }
                        for r in results
                    ],
                    "artefacts": [
                        {
                            "artefact_type": a.artefact_type,
                            "reference_no": a.reference_no,
                            "standard": a.standard,
                            "description": a.description,
                            "payload": a.payload,
                        }
                        for a in artefacts
                    ],
                })
            except Exception as exc:
                _vlog.warning("Compliance check error for obs %s: %s", rec.get("id"), exc)

        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_compliance_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(compliance_results, fh)
        _vlog.info("Compliance check complete: %d records → %s", len(compliance_results), tmp.name)
        return tmp.name

    @task
    def twin_sync(physics_path: str, compliance_path: str) -> str:
        """
        Push scored observations to MindSphere digital twin.
        Returns path to sync results file.
        """
        import json
        import tempfile

        if not MINDSPHERE_BASE_URL:
            _vlog.info("MINDSPHERE_BASE_URL not set, skipping twin sync")
            tmp = tempfile.NamedTemporaryFile(
                suffix=".json", delete=False, dir="/tmp", prefix="veritas_twin_"
            )
            with open(tmp.name, "w") as fh:
                json.dump({"skipped": True, "reason": "MINDSPHERE_BASE_URL not configured"}, fh)
            return tmp.name

        from veritas_twin.sync_task import push_scores_to_twin

        with open(physics_path) as fh:
            physics_results = json.load(fh)

        results = push_scores_to_twin(physics_results, MINDSPHERE_BASE_URL)
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_twin_"
        )
        with open(tmp.name, "w") as fh:
            json.dump(results, fh)
        return tmp.name

    @task
    def fed_update(pending_path: str) -> str:
        """
        Trigger federated model update using local sensor scores.
        Returns path to update status file.
        """
        import json
        import tempfile

        _vlog.info("Federated update step — placeholder (see veritas_federated/)")
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="/tmp", prefix="veritas_fed_"
        )
        with open(tmp.name, "w") as fh:
            json.dump({"status": "submitted"}, fh)
        return tmp.name

    @task
    def audit_write(
        pending_path: str,
        physics_path: str,
        compliance_path: str,
    ) -> None:
        """
        Write immutable audit log entries for this enrichment run.
        """
        import json
        import asyncio
        import asyncpg

        with open(pending_path) as fh:
            records = json.load(fh)
        with open(physics_path) as fh:
            physics = {r["id"]: r for r in json.load(fh)}
        with open(compliance_path) as fh:
            compliance = {r["id"]: r for r in json.load(fh)}

        async def _write_audit():
            conn = await asyncpg.connect(DB_URL)
            try:
                async with conn.transaction():
                    for rec in records:
                        obs_id = rec["id"]
                        await conn.execute(
                            """
                            INSERT INTO audit_log
                                (site_id, actor, action, entity_type, entity_id,
                                 after_state, metadata)
                            VALUES ($1,$2,$3,$4,$5,$6,$7)
                            """,
                            rec["site_id"],
                            "airflow:veritas_async_enrichment",
                            "ASYNC_ENRICHMENT",
                            "scored_observation",
                            str(obs_id),
                            json.dumps({
                                "physics": physics.get(obs_id),
                                "compliance": compliance.get(obs_id),
                            }),
                            json.dumps({"dag_run_id": ""}),
                        )
                _vlog.info("Audit log written for %d observations", len(records))
            finally:
                await conn.close()

        asyncio.run(_write_audit())

    @task
    def db_update(
        pending_path: str,
        physics_path: str,
        compliance_path: str,
    ) -> None:
        """
        Persist physics and compliance results back to scored_observations.
        """
        import json
        import asyncio
        import asyncpg
        from decimal import Decimal

        with open(pending_path) as fh:
            records = {r["id"]: r for r in json.load(fh)}
        with open(physics_path) as fh:
            physics = {r["id"]: r for r in json.load(fh)}
        with open(compliance_path) as fh:
            compliance = {r["id"]: r for r in json.load(fh)}

        async def _update():
            conn = await asyncpg.connect(DB_URL)
            try:
                async with conn.transaction():
                    for obs_id in records:
                        phys = physics.get(obs_id, {})
                        comp = compliance.get(obs_id, {})
                        await conn.execute(
                            """
                            UPDATE scored_observations SET
                                physics_ok = $1,
                                physics_note = $2,
                                physics_delta = $3,
                                compliance_checked = TRUE,
                                compliance_passed = $4,
                                compliance_actions = $5
                            WHERE id = $6
                            """,
                            phys.get("physics_ok"),
                            phys.get("physics_note"),
                            Decimal(phys.get("physics_delta", "0")),
                            comp.get("passed"),
                            json.dumps(comp.get("results", [])),
                            obs_id,
                        )
            finally:
                await conn.close()

        asyncio.run(_update())
        _vlog.info("DB update complete for %d observations", len(records))

    # ---- Wire up the DAG ----
    pending = fetch_pending_scored_ids()
    physics = physics_llm_validate(pending)
    compliance = compliance_check(pending, physics)
    twin = twin_sync(physics, compliance)
    fed = fed_update(pending)
    audit_write(pending, physics, compliance)
    db_update(pending, physics, compliance)


dag_instance = veritas_async_enrichment()
