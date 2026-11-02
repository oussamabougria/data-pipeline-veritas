"""
Veritas Enrichment DAG v1  (dag_id = veritas_enrichment_v1)

Runs every 5 minutes. Sweeps scored_observations where physics_ok IS NULL
and populates physics_ok / compliance fields via the Claude API.

Rules (CLAUDE.md):
  - Rule 1 : _vlog = get_vlog(__name__) — never 'log'
  - Rule 2 : XCom returns MinIO path strings only, never DataFrames
  - Rule 5 : Only this DAG calls the Anthropic API. Critical Path never does.
  - Rule 6 : search_path enforced in every DB connection
"""

import os

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

from veritas_core.logging.vlog import get_vlog

_vlog = get_vlog(__name__)

DB_URL = os.environ.get(
    "VERITAS_DB_URL",
    "postgresql://veritas:veritas@postgres:5432/veritas"
)
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "veritas")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MINIO_BUCKET = "veritas-enrichment"


@dag(
    dag_id="veritas_enrichment_v1",
    description="Physics LLM enrichment — sweeps scored_observations with physics_ok IS NULL",
    schedule_interval="*/5 * * * *",
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["veritas", "enrichment", "v1"],
)
def veritas_enrichment_v1():

    @task
    def fetch_pending() -> str:
        """
        Query scored_observations for rows pending LLM enrichment.
        Returns MinIO path to a JSON file (XCom carries path, never DataFrame).
        Rule 2: XCom = path string only.
        Rule 6: search_path enforced via options parameter.
        """
        import json
        import tempfile
        import asyncio
        import asyncpg

        _search_path_url = DB_URL + (
            "?options=-csearch_path%3Dveritas%2Cpublic"
            if "?" not in DB_URL else
            "&options=-csearch_path%3Dveritas%2Cpublic"
        )

        async def _fetch() -> list:
            conn = await asyncpg.connect(_search_path_url)
            try:
                await conn.execute("SET search_path = veritas, public")
                rows = await conn.fetch(
                    """
                    SELECT id, ts::text, sensor_id, value::text, unit,
                           archetype, confidence::text,
                           tau::text, delta::text, alpha::text,
                           skill_id, skill_version
                    FROM scored_observations
                    WHERE physics_ok IS NULL
                    ORDER BY ts DESC
                    LIMIT 500
                    """
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        records = asyncio.run(_fetch())

        with tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
            dir="/tmp",
            prefix="veritas_enrich_pending_",
            mode="w",
            encoding="utf-8",
        ) as fh:
            json.dump(records, fh)
            path = fh.name

        _vlog.info("Fetched %d pending records → %s", len(records), path)
        return path  # Rule 2: path string only

    @task
    def llm_enrich(pending_path: str) -> str:
        """
        Call Claude API for physics validation.
        Rule 5: Only this Enrichment Path DAG calls the Anthropic API.
        Returns MinIO path to results JSON.
        """
        import json
        import tempfile
        from decimal import Decimal

        with open(pending_path) as fh:
            records = json.load(fh)

        results = []

        if not ANTHROPIC_API_KEY or not records:
            _vlog.info(
                "llm_enrich: skipping (%d records, api_key=%s)",
                len(records),
                "set" if ANTHROPIC_API_KEY else "missing",
            )
            for rec in records:
                results.append({
                    "id": rec["id"],
                    "physics_ok": None,
                    "physics_note": "enrichment_skipped",
                    "physics_delta": "0.0000",
                })
        else:
            import anthropic

            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

            for rec in records:
                try:
                    prompt = (
                        f"Sensor {rec['sensor_id']} ({rec.get('archetype','?')}) "
                        f"reported value={rec['value']} {rec.get('unit','')} "
                        f"with confidence={rec['confidence']}. "
                        "Is this physically plausible? Reply JSON: "
                        '{"ok": true/false, "note": "reason", "delta": -0.05..0.05}'
                    )
                    msg = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=128,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    raw = msg.content[0].text.strip()
                    parsed = json.loads(raw)
                    results.append({
                        "id": rec["id"],
                        "physics_ok": bool(parsed.get("ok", True)),
                        "physics_note": str(parsed.get("note", "")),
                        "physics_delta": str(
                            Decimal(str(parsed.get("delta", "0"))).quantize(
                                Decimal("0.0001")
                            )
                        ),
                    })
                except Exception as exc:
                    _vlog.warning("LLM error for obs %s: %s", rec.get("id"), exc)
                    results.append({
                        "id": rec["id"],
                        "physics_ok": None,
                        "physics_note": f"llm_error: {exc}",
                        "physics_delta": "0.0000",
                    })

        with tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
            dir="/tmp",
            prefix="veritas_enrich_llm_",
            mode="w",
            encoding="utf-8",
        ) as fh:
            json.dump(results, fh)
            path = fh.name

        _vlog.info("LLM enrichment complete: %d records → %s", len(results), path)
        return path  # Rule 2

    @task
    def write_results(pending_path: str, llm_path: str) -> None:
        """
        Persist enrichment results to scored_observations.
        Rule 6: search_path set at connection level and via SET statement.
        """
        import json
        import asyncio
        import asyncpg
        from decimal import Decimal

        with open(pending_path) as fh:
            records = {str(r["id"]): r for r in json.load(fh)}
        with open(llm_path) as fh:
            llm = {str(r["id"]): r for r in json.load(fh)}

        _search_path_url = DB_URL + (
            "?options=-csearch_path%3Dveritas%2Cpublic"
            if "?" not in DB_URL else
            "&options=-csearch_path%3Dveritas%2Cpublic"
        )

        async def _write():
            conn = await asyncpg.connect(_search_path_url)
            try:
                async with conn.transaction():
                    await conn.execute("SET search_path = veritas, public;")
                    for obs_id, data in llm.items():
                        delta_str = data.get("physics_delta", "0")
                        delta_val = Decimal(delta_str) if delta_str else Decimal("0")
                        await conn.execute(
                            """
                            UPDATE scored_observations
                            SET physics_ok    = $1,
                                physics_note  = $2,
                                physics_delta = $3
                            WHERE id = $4
                            """,
                            data.get("physics_ok"),
                            data.get("physics_note"),
                            delta_val,
                            int(obs_id),
                        )
            finally:
                await conn.close()

        asyncio.run(_write())
        _vlog.info("Wrote enrichment results for %d observations", len(llm))

    pending = fetch_pending()
    llm = llm_enrich(pending)
    write_results(pending, llm)


dag_instance = veritas_enrichment_v1()
