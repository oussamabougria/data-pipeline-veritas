# VERITAS

**Per-observation confidence scoring for industrial sensor telemetry.**

VERITAS assigns every sensor reading a trust value `C(v) = τ_age × δ × α` — a multiplicative, fail-closed score so that *any* collapsed factor drives confidence to zero. Built for environments where a bad reading isn't a nuisance, it's a scrapped batch or a failed audit.

```
C(v) = τ_age  ×  δ  ×  α
        │        │      └─ signal stability (σ-drift, CUSUM, dead-sensor, monotonicity)
        │        └──────── sample density (how much data backs this reading)
        └───────────────── temporal freshness (exponential age decay)
```

> **Honest status:** the formula is a working *trust index*, not yet a calibrated confidence — see [docs/C_v_MATHEMATICAL_ANALYSIS.md](docs/C_v_MATHEMATICAL_ANALYSIS.md) for the rigorous treatment. The defensible product is the audit trail it feeds, not the number alone.

---

## What's inside

| Area | Path | What it does |
|---|---|---|
| **Scoring engine** | `veritas_core/` | Archetype scorers (INERTIAL/CUMULATIVE/CYCLICAL/BINARY), the `τ·δ·α` formula, `SkillContext` (per-sensor config), `ConfidenceScorer` |
| **Drift detection** | `veritas_core/cusum/` | Tabular CUSUM for sustained-drift alarms |
| **Physics validation** | `veritas_core/physics_llm/` | LLM-orchestrated *deterministic* physics validators (Nyquist, conservation, thermal, Newton) — LLM only returns a confidence delta, never writes values, degrades gracefully on API failure |
| **Compliance gate** | `veritas_core/compliance/` | ISO 9001 / AS9100D / IATF 16949 thresholds → `NCR` / `BLOCK_RELEASE` actions |
| **Connectors** | `veritas_core/connectors/` | csv, modbus, mqtt, opcua, rest |
| **Feedback loop** | `veritas_core/feedback/` | constraint/dataset/feedback stores + precision tracker — human accept/reject strengthens or weakens learned patterns |
| **Stream path** | `veritas_consumer.py` | Kafka → score → TimescaleDB (`confidence` is a DB-generated column, never written by Python) |
| **Operational app** | `veritas_operational/` | FastAPI app + batch column-quality pipeline + dashboard |
| **Schema** | `sql/` | TimescaleDB: sites/sensors, skill registry, raw & scored observations, audit log, CUSUM state |
| **Async enrichment** | `airflow/dags/` | physics-LLM + compliance sweep (5-min), human-gated calibration |
| **Simulator** | `veritas_simulator.py` | Kafka stress/anomaly injector for load + quarantine testing |

## Design rules the code enforces

1. **`Decimal` everywhere** — never `float` in the scoring path (audit-grade precision).
2. **`confidence` is a DB `GENERATED` column** (`tau * delta * alpha` stored) — Python computes factors, the DB owns the product.
3. **Skills are versioned** — every scored row stamps `skill_id` + `skill_version` for reproducibility.
4. **LLM suggests, never writes** — physics validation returns a delta only; deterministic checks remain the auditable truth.
5. **Calibration is human-gated** — the calibration DAG proposes new skill versions; an engineer approves, never auto-live.

## Architecture

```
sources ──► connectors (stream / batch / poll)
              │
   stream ▼   │   ▼ batch
   veritas_consumer          VeritasPipeline.run()
   (Kafka → score → DB)      (ingest→profile→validate→score→advise→audit)
              │
              ▼
   TimescaleDB · scored_observations  (confidence = GENERATED)
              │  physics_ok = NULL
              ▼   every 5 min
   Airflow: physics-LLM validate → compliance (NCR/BLOCK) → calibration
              │
              ▼
   veritas_operational (FastAPI + dashboard + feedback)
```

## Quick start

```bash
# deps
pip install -e ".[api,test]"

# run tests
pytest tests/ -q          # 68 tests

# stream path: simulator → kafka → consumer → timescaledb
python veritas_simulator.py            # inject sensor traffic
python veritas_consumer.py             # score + write scored_observations

# operational app (batch/operational-data quality)
uvicorn veritas_operational.app:app --port 8000
# → http://localhost:8000  (dashboard), /api/live
```

## Sensor archetypes

| Archetype | For | τ | δ | α |
|---|---|---|---|---|
| `INERTIAL` | accelerometers, IMUs, vibration | `exp(−λT)` | centered sigmoid | `exp(−k(σ−σ_ref)²)` + dead-sensor/CUSUM floors |
| `CUMULATIVE` | flow meters, counters, odometers | `1−T/T_max` | `min(1, n/n_min)` | `1` if monotone else `0` |
| `CYCLICAL` | temperature, pressure, RPM | `\|cos(πT/T_per)\|^β` | harmonic mean | `1−σ/amplitude` |
| `BINARY` | limit switches, alarms, relays | step `<T_hold` | `n≥1` | chatter-aware (legit flips ≠ unstable) |

## Status & known limits

- ✅ 68 tests passing; scoring path is Decimal-safe; DB owns the formula.
- ⚠️ **C(v) is a heuristic trust index, not a calibrated probability** — constants are fit to signal statistics, not labeled correctness. See the math analysis.
- ⚠️ Two divergent scoring implementations existed (stream vs. core); the canonical archetype scorer is being consolidated.
- ⚠️ Not a product yet — no auth/multi-tenancy; the physics-LLM needs an external API (off the air-gapped critical path).

## Roadmap

- [ ] Falsification benchmark — does `C(v)` separate known-good/faulty readings better than SPC + device quality flags? *(the deciding experiment)*
- [ ] Canonical 4-factor scorer `τ_age × τ_rhythm × δ × α` (cadence regularity as its own term)
- [ ] Empirical calibration `C(v) → P(correct)` via isotonic/logistic fit
- [ ] Edge-agent packaging — deterministic checks inside the plant, evidence/stats only off-site
- [ ] Productize the audit/NCR layer (the actual sellable asset)

---

*Analysis docs: [C(v) math](docs/C_v_MATHEMATICAL_ANALYSIS.md) · [prior art](patent/PRIOR_ART_REPORT.md)*
