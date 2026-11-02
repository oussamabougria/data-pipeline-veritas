from __future__ import annotations

import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from veritas_core.advisory import AdvisoryEngine
from veritas_core.cleaning import CleaningEngine
from veritas_core.discovery import SemanticDiscovery, SemanticRole, TimeSeriesAligner
from veritas_core.feedback import DatasetRegistry, FeedbackStore, PrecisionTracker
from veritas_core.pipeline import VeritasPipeline
from veritas_core.router import VeritasRouter

from .data_loader import LoaderConfig, OperationalDataLoader
from .profiler import OperationalProfiler
from .rules_generator import ContextAwareRulesEngine
from .rules_engine import BusinessRulesEngine


def ensure_dirs(root: Path) -> tuple[Path, Path, Path]:
    data_dir = root / "app_data"
    output_dir = root / "app_output"
    upload_dir = root / "uploads"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, output_dir, upload_dir


def build_sample_dataset() -> pd.DataFrame:
    random.seed(7)
    np.random.seed(7)
    base = datetime.now(UTC) - timedelta(days=14)
    rows: list[dict[str, object]] = []
    sites = ["Tiflet-Aero", "Kenitra-Auto", "Casa-MRO"]
    teams = ["Maintenance", "Quality", "Methods", "Supply"]
    causes = ["Bearing wear", "Calibration drift", "Operator error", "Cooling fault", "Sensor dropout"]
    severities = ["Low", "Medium", "High", "Critical"]
    statuses = ["Open", "In Progress", "Resolved"]

    for idx in range(180):
        opened_at = base + timedelta(minutes=idx * 47)
        severity = random.choices(severities, weights=[4, 3, 2, 1], k=1)[0]
        status = random.choices(statuses, weights=[2, 3, 5], k=1)[0]
        response_minutes = max(5, int(np.random.normal(42, 18)))
        downtime = max(0, int(np.random.normal(58, 35)))
        scrap_kg = round(max(0, np.random.normal(24, 18)), 2)
        defect_rate = round(max(50, np.random.normal(620, 280)), 1)
        recurrence = max(0, int(np.random.poisson(0.7)))
        containment = random.choice([True, True, True, False])
        owner_team = random.choice(teams)
        closed_at = opened_at + timedelta(minutes=downtime + response_minutes + random.randint(10, 240))
        first_response_at = opened_at + timedelta(minutes=response_minutes)
        if status != "Resolved":
            closed_at = pd.NaT
        rows.append(
            {
                "incident_id": f"INC-{2026000 + idx}",
                "site": random.choice(sites),
                "line_id": f"L-{random.randint(1, 6):02d}",
                "asset_id": f"ASSET-{random.randint(100, 180)}",
                "opened_at": opened_at.isoformat(),
                "first_response_at": first_response_at.isoformat(),
                "closed_at": closed_at.isoformat() if pd.notna(closed_at) else None,
                "status": status,
                "severity": severity,
                "owner_team": owner_team,
                "root_cause": random.choice(causes),
                "downtime_min": downtime,
                "response_minutes": response_minutes,
                "scrap_kg": scrap_kg,
                "defect_rate_ppm": defect_rate,
                "recurrence_count": recurrence,
                "containment_in_place": containment,
                "description": f"{severity} issue affecting {owner_team.lower()} workflow on asset loop.",
            }
        )
    frame = pd.DataFrame(rows)
    frame["age_hours"] = (
        (pd.Timestamp.now(tz=UTC) - pd.to_datetime(frame["opened_at"], utc=True)).dt.total_seconds() / 3600
    ).round(2)
    anomaly_rows = [5, 12, 28, 41, 57, 73, 92, 117]
    frame.loc[5, "downtime_min"] = -12
    frame.loc[12, "response_minutes"] = 187
    frame.loc[28, "scrap_kg"] = 188.4
    frame.loc[41, "severity"] = "Urgent"
    frame.loc[57, "closed_at"] = None
    frame.loc[57, "status"] = "Resolved"
    frame.loc[73, "defect_rate_ppm"] = 2410.0
    frame.loc[92, "owner_team"] = None
    frame.loc[117, "status"] = "Open"
    frame.loc[117, "severity"] = "Critical"
    frame.loc[117, "age_hours"] = 31.5
    frame["anomaly_seed"] = False
    frame.loc[anomaly_rows, "anomaly_seed"] = True
    return frame


def detect_domain(columns: list[str]) -> dict[str, object]:
    joined = " ".join(str(col).lower() for col in columns)
    if any(token in joined for token in ("incident", "severity", "downtime", "owner_team", "defect")):
        return {
            "domain": "industrial_incident_bpm",
            "label": "Industrial incident / BPM operations",
            "reason": "incident_id, severity, downtime, status and defect KPIs were detected",
        }
    if len(columns) > 200 and any(str(col).replace(".", "").replace("-", "").isdigit() for col in columns[:10]):
        return {
            "domain": "high_dimensional_sensor",
            "label": "High-dimensional sensor / process data",
            "reason": "large number of anonymous numeric sensor columns detected",
        }
    if any(token in joined for token in ("footfall", "temperature", "voc", "aq", "fail", "sensor")):
        return {
            "domain": "iot_room_sensor",
            "label": "IoT building / room sensor data",
            "reason": "room sensor signals such as footfall, temperature, air quality or failure flags were detected",
        }
    return {"domain": "generic_operational", "label": "Generic operational data", "reason": "fallback"}


def compute_confidence(frame: pd.DataFrame, profiles: list[dict[str, object]], violations: list[dict[str, object]]) -> list[dict[str, object]]:
    import re

    def clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def iqr_outlier_ratios(valid: pd.Series) -> tuple[float, float]:
        if valid.empty:
            return 0.0, 0.0
        q1 = valid.quantile(0.25)
        q3 = valid.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            return 0.0, 0.0
        mild_lo = q1 - 1.5 * iqr
        mild_hi = q3 + 1.5 * iqr
        severe_lo = q1 - 3.0 * iqr
        severe_hi = q3 + 3.0 * iqr
        mild_outliers = float(((valid < mild_lo) | (valid > mild_hi)).mean())
        severe_outliers = float(((valid < severe_lo) | (valid > severe_hi)).mean())
        return mild_outliers, severe_outliers

    def identifier_prefix_pattern(value: str) -> str:
        match = re.match(r"^([A-Za-z]+[-_])", value)
        if match:
            return match.group(1)
        return "NO_PREFIX"

    violation_rows_by_rule: dict[str, set[int]] = {}
    for item in violations:
        violation_rows_by_rule.setdefault(item["rule"], set()).add(int(item["row_index"]))

    results: list[dict[str, object]] = []
    now = pd.Timestamp.now(tz=UTC)
    for profile in profiles:
        name = profile["name"]
        series = frame[name]
        archetype = profile["archetype"]
        non_null_ratio = float(series.notna().mean())

        if archetype == "TEMPORAL":
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
            valid_ratio = float(parsed.notna().mean())
            future_ratio = float((parsed > now).fillna(False).mean())
            tau = max(0.0, valid_ratio - future_ratio)
            alpha = valid_ratio
        elif archetype == "NUMERICAL":
            numeric = pd.to_numeric(series, errors="coerce")
            valid = numeric.dropna()
            if valid.empty:
                tau = 0.85
                alpha = 0.0
            else:
                mild_outliers, severe_outliers = iqr_outlier_ratios(valid)
                zero_ratio = float((valid == 0).mean())
                zero_penalty = zero_ratio * 0.1 if zero_ratio > 0.30 else 0.0
                tau = clamp(1.0 - (severe_outliers * 0.5) - zero_penalty, 0.85, 0.99)
                alpha = clamp(1.0 - (mild_outliers * 0.3) - (severe_outliers * 0.7), 0.0, 1.0)
        elif archetype == "IDENTIFIER":
            tokens = series.dropna().astype(str)
            if tokens.empty:
                tau = 0.85
                alpha = 0.0
            else:
                lengths = tokens.str.len()
                dominant_length = lengths.mode().iloc[0]
                prefix_patterns = tokens.map(identifier_prefix_pattern)
                dominant_prefix = prefix_patterns.mode().iloc[0]
                format_consistency_ratio = float(
                    ((lengths == dominant_length) & (prefix_patterns == dominant_prefix)).mean()
                )
                tau = clamp(format_consistency_ratio, 0.85, 0.99)
                prefix_ratio = float(tokens.str.contains(r"^[A-Z]+[-_]", regex=True).mean())
                alpha = clamp(0.5 * float((lengths == dominant_length).mean()) + 0.5 * prefix_ratio, 0.0, 1.0)
        elif archetype == "CATEGORICAL":
            non_null = series.dropna()
            if non_null.empty:
                tau = 0.85
            else:
                top_freq = float(non_null.value_counts(dropna=True).iloc[0] / len(non_null))
                dominance_penalty = 0.0 if top_freq < 0.70 else (top_freq - 0.70) * 0.5
                tau = clamp(1.0 - dominance_penalty, 0.85, 0.99)
            cardinality_ratio = float(series.nunique(dropna=True) / max(series.notna().sum(), 1))
            alpha = max(0.0, 1.0 - min(cardinality_ratio, 1.0) * 0.2)
        else:
            lengths = series.dropna().astype(str).str.len()
            alpha = 0.9 if lengths.empty else max(0.0, min(1.0, lengths.between(12, 180).mean()))
            tau = clamp(alpha if not lengths.empty else 0.85, 0.85, 0.99)

        related_violations = 0
        for rule_name, row_ids in violation_rows_by_rule.items():
            if name in rule_name or (name == "closed_at" and "closed_at" in rule_name):
                related_violations += len(row_ids)

        score = round(max(0.0, min(1.0, tau * non_null_ratio * alpha)), 4)
        if related_violations:
            score = round(max(0.0, min(1.0, score * max(0.55, 1.0 - (related_violations / max(len(frame), 1)) * 2.5))), 4)
        verdict = "trusted" if score >= 0.85 else "review" if score >= 0.65 else "quarantine"
        results.append(
            {
                "column": name,
                "archetype": archetype,
                "tau": round(tau, 4),
                "delta": round(non_null_ratio, 4),
                "alpha": round(alpha, 4),
                "confidence": score,
                "verdict": verdict,
                "related_rule_hits": related_violations,
            }
        )
    return sorted(results, key=lambda item: item["confidence"])


def build_report(domain: dict[str, object], rule_meta: dict[str, object], confidence: list[dict[str, object]]) -> str:
    worst = confidence[:5]
    lines = [
        f"Domain detected: {domain['label']} ({domain['reason']}).",
        f"Rules evaluated: {rule_meta['rule_count']} with {rule_meta['violations_total']} total violations.",
        "Highest-risk columns:",
    ]
    for item in worst:
        lines.append(
            f"- {item['column']}: C(v)={item['confidence']:.4f} "
            f"(tau={item['tau']:.4f}, delta={item['delta']:.4f}, alpha={item['alpha']:.4f}) => {item['verdict']}"
        )
    lines.append("Production-style summary: incidents are flowing, but SLA breaches, invalid severity taxonomy, scrap spikes and incomplete closure data require operator review.")
    return "\n".join(lines)


def run_pipeline(source_path: Path, rules_path: Path) -> dict[str, object]:
    loaded = OperationalDataLoader(LoaderConfig(source_path=source_path)).load()
    frame = loaded.data.copy()
    _, output_dir, _ = ensure_dirs(Path(__file__).resolve().parents[2])

    router = VeritasRouter()
    source = router.route(
        connector="csv",
        columns=list(frame.columns),
        source_name=source_path.name,
        config={"source_path": str(source_path)},
    )

    pipeline = VeritasPipeline()
    run = pipeline.run(
        df=frame,
        source=source,
        rules_path=rules_path,
        output_dir=output_dir,
    )

    return _run_to_payload(run, loaded, source_path)


def _run_to_payload(run, loaded, source_path) -> dict[str, object]:
    """
    Converts PipelineRun to the existing results.json dict shape.
    This adapter keeps app.py, /api/live, and the Excel export stable.
    """
    confidence_list = [
        {
            "column": p.column,
            "archetype": p.archetype,
            "tau": p.tau,
            "delta": p.delta,
            "alpha": p.alpha,
            "confidence": p.confidence,
            "verdict": p.verdict.value,
            "related_rule_hits": sum(1 for i in run.issues if i.column == p.column),
        }
        for p in sorted(run.profiles, key=lambda x: x.confidence)
    ]

    violations_list = [
        {
            "row_index": i.row_index,
            "rule": i.rule,
            "severity": i.severity.value,
            "message": i.message,
            "column": i.column,
            "value": i.value,
        }
        for i in run.issues
    ]

    advisory_payload = AdvisoryEngine().evaluate(
        violations=violations_list,
        confidence=confidence_list,
        dataset_id=run.dataset_id,
    )
    quality_issues = advisory_payload["quality_issues"]
    issues_by_id = {issue.get("issue_id"): issue for issue in quality_issues}
    advisories_raw = advisory_payload["advisories"]
    advisories = [
        _enrich_advisory_for_feedback(advisory, issues_by_id, run.domain_label)
        for advisory in advisories_raw
    ]
    cleaning_payload = CleaningEngine().evaluate(
        quality_issues=quality_issues,
        advisories=advisories_raw,
    )
    cleaning_actions = cleaning_payload["cleaning_actions"]
    audit_events = cleaning_payload["audit_events"]

    verdict_breakdown: dict[str, int] = {}
    archetype_breakdown: dict[str, int] = {}
    for p in run.profiles:
        verdict_breakdown[p.verdict.value] = verdict_breakdown.get(p.verdict.value, 0) + 1
        archetype_breakdown[p.archetype] = archetype_breakdown.get(p.archetype, 0) + 1

    return {
        "domain": {
            "domain": run.domain_type.value,
            "label": run.domain_interpretation or run.domain_label or source_path.name,
            "reason": "veritas router",
        },
        "loader": {**loaded.metadata, "source_path": str(source_path), "elapsed_ms": round(loaded.elapsed_ms, 2)},
        "discovery": {
            "profiles": [
                {
                    "column_name": p.column,
                    "role": p.role,
                    "archetype": p.archetype,
                    "confidence": p.confidence,
                    "anomalies": [],
                    "notes": "",
                }
                for p in run.profiles
            ],
            "corrections_applied": [],
            "elapsed_ms": 0,
        },
        "alignment": None,
        "registry": {
            "dataset_id": run.dataset_id,
            "registered_datasets": 0,
        },
        "precision": {
            "accuracy": 0,
            "confidence_calibration": 0,
            "incorrect_inferences": [],
            "recommendation": "",
            "most_confused_roles": [],
            "worst_columns": [],
        },
        "profiler": {
            "column_count": len(run.profiles),
            "archetype_breakdown": archetype_breakdown,
            "elapsed_ms": run.elapsed_ms,
        },
        "rules": {
            "rule_count": run.rule_count,
            "violations_total": run.violations_total,
            "violations_by_severity": {},
            "llm_calls": run.llm_calls,
            "columns_analyzed": len(run.profiles),
            "columns_skipped": 0,
            "columns_uncapped": 0,
            "domain_interpretation": run.domain_interpretation,
            "fallback_used": run.fallback_used,
            "elapsed_ms": run.elapsed_ms,
        },
        "profiles": [
            {
                "name": p.column,
                "archetype": p.archetype,
                "role": p.role,
                "dtype": p.dtype,
                "null_ratio": p.null_ratio,
                "unique_ratio": p.unique_ratio,
                "sample_values": p.sample_values,
                "stats": p.stats,
            }
            for p in run.profiles
        ],
        "confidence": confidence_list,
        "violations": violations_list,
        "quality_issues": quality_issues,
        "advisories": advisories,
        "cleaning_actions": cleaning_actions,
        "audit_events": audit_events,
        "report": run.domain_label,
        "verdict_breakdown": verdict_breakdown,
        "pipeline_run": {
            **run.to_dict(),
            "metrics": {
                "rows": int(loaded.metadata.get("rows") or 0),
                "columns": len(run.profiles),
                "violations_total": run.violations_total,
                "verdict_breakdown": verdict_breakdown,
                "quality_issues": len(quality_issues),
                "advisories": len(advisories),
                "cleaning_actions": len(cleaning_actions),
                "audit_events": len(audit_events),
            },
        },
        "data_source": {
            "id": run.source_id,
            "name": run.domain_label,
            "source_type": run.source_type.value,
            "domain_type": run.domain_type.value,
            "connector": "csv",
            "config": {"source_path": str(source_path)},
        },
        "dataset": {
            "dataset_id": run.dataset_id,
            "source_id": run.source_id,
            "name": str(source_path),
            "rows": int(loaded.metadata.get("rows") or 0),
            "columns": list(loaded.metadata.get("columns") or []),
            "domain": run.domain_type.value,
        },
    }


def _enrich_advisory_for_feedback(
    advisory: dict[str, object],
    issues_by_id: dict[str, dict[str, object]],
    domain_label: str,
) -> dict[str, object]:
    issue = issues_by_id.get(advisory.get("issue_id")) or {}
    column = str(
        advisory.get("column")
        or issue.get("column_name")
        or issue.get("evidence", {}).get("column")
        or advisory.get("metadata", {}).get("column")
        or ""
    )
    constraint = _constraint_from_issue(issue)
    enriched = dict(advisory)
    enriched.setdefault("id", advisory.get("advisory_id"))
    enriched["column"] = column
    enriched["rule"] = str(issue.get("issue_type") or advisory.get("metadata", {}).get("rule") or "")
    enriched["constraint"] = constraint
    enriched["domain_label"] = domain_label
    return enriched


def _constraint_from_issue(issue: dict[str, object]) -> dict[str, object]:
    issue_type = str(issue.get("issue_type") or "").lower()
    message = str(issue.get("message") or "").lower()
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    constraint: dict[str, object] = {"severity_if_violated": str(issue.get("severity") or "Critical").title()}
    if "negative" in issue_type or "negative" in message:
        constraint["allow_negative"] = False
        constraint["min_valid"] = 0
    elif "confidence_quarantine" in issue_type:
        constraint["allow_negative"] = False
        constraint["min_valid"] = 0
    if evidence.get("expected") is not None:
        constraint["expected"] = evidence.get("expected")
    return constraint


def _resolve_domain_rules(domain_name: str, default_path: Path) -> Path | None:
    if domain_name == "industrial_incident_bpm" and default_path.exists():
        return default_path

    candidate = default_path.with_name(f"{domain_name}.yaml")
    if candidate.exists():
        return candidate
    return None


def _run_batch_pipeline(source_path: Path, rules_path: Path) -> dict[str, object]:
    loaded = OperationalDataLoader(LoaderConfig(source_path=source_path)).load()
    frame = loaded.data.copy()
    domain = detect_domain(list(frame.columns))
    _, output_dir, _ = ensure_dirs(source_path.parent.parent)
    feedback_store = FeedbackStore(str(output_dir / "veritas_feedback.json"))
    registry = DatasetRegistry(str(output_dir / "veritas_registry.json"))
    discovery_started = datetime.now(UTC)
    discovery = SemanticDiscovery()
    raw_semantic_profiles = discovery.discover(frame)
    semantic_profiles = feedback_store.apply_corrections(raw_semantic_profiles)
    discovery_elapsed_ms = (datetime.now(UTC) - discovery_started).total_seconds() * 1000
    corrections_applied = [
        profile.column_name
        for raw, profile in zip(raw_semantic_profiles, semantic_profiles, strict=False)
        if raw.role != profile.role or raw.archetype != profile.archetype
    ]
    time_candidates = [
        profile.column_name
        for profile in semantic_profiles
        if profile.role == SemanticRole.TEMPORAL and "at" in profile.column_name.lower()
    ]
    alignment_payload: dict[str, object] | None = None
    if time_candidates:
        alignment = TimeSeriesAligner().align(frame, time_col=time_candidates[0])
        alignment_payload = {
            "reference_column": alignment.reference_column,
            "resolution": alignment.resolution,
            "gaps": alignment.gaps,
            "bursts": alignment.bursts,
            "aligned_rows": int(len(alignment.aligned_df)),
        }
    profiler = OperationalProfiler().profile(frame)
    profile_dicts = [item.to_dict() for item in profiler.data]
    domain_rules_path = _resolve_domain_rules(str(domain["domain"]), rules_path)
    if domain_rules_path and domain_rules_path.exists():
        rules = BusinessRulesEngine(domain_rules_path).evaluate(frame)
    else:
        rules = ContextAwareRulesEngine(domain_label=str(domain["label"])).evaluate(
            frame,
            profile_dicts,
            semantic_profiles,
        )
    confidence = compute_confidence(rules.data["frame"], profile_dicts, rules.data["violations"])
    dataset_id = registry.register(
        source_path=str(source_path),
        profiles=semantic_profiles,
        sector=None,
        corrections_applied=corrections_applied,
    )
    advisory_payload = AdvisoryEngine().evaluate(
        violations=rules.data["violations"],
        confidence=confidence,
        dataset_id=dataset_id,
    )
    cleaning_payload = CleaningEngine().evaluate(
        quality_issues=advisory_payload["quality_issues"],
        advisories=advisory_payload["advisories"],
    )
    precision_report = PrecisionTracker(feedback_store, registry).evaluate(semantic_profiles)
    report = build_report(domain, rules.metadata, confidence)
    return {
        "domain": domain,
        "loader": {**loaded.metadata, "elapsed_ms": round(loaded.elapsed_ms, 2)},
        "discovery": {
            "profiles": [profile.to_dict() for profile in semantic_profiles],
            "corrections_applied": corrections_applied,
            "elapsed_ms": round(discovery_elapsed_ms, 2),
        },
        "alignment": alignment_payload,
        "registry": {
            "dataset_id": dataset_id,
            "registered_datasets": len(registry.list_datasets()),
        },
        "precision": {
            "accuracy": round(precision_report.accuracy, 4),
            "confidence_calibration": round(precision_report.confidence_calibration, 4),
            "incorrect_inferences": precision_report.incorrect_inferences,
            "recommendation": precision_report.recommendation,
            "most_confused_roles": precision_report.most_confused_roles,
            "worst_columns": precision_report.worst_columns,
        },
        "profiler": {**profiler.metadata, "elapsed_ms": round(profiler.elapsed_ms, 2)},
        "rules": {**rules.metadata, "elapsed_ms": round(rules.elapsed_ms, 2)},
        "profiles": profile_dicts,
        "confidence": confidence,
        "violations": rules.data["violations"],
        "quality_issues": advisory_payload["quality_issues"],
        "advisories": advisory_payload["advisories"],
        "cleaning_actions": cleaning_payload["cleaning_actions"],
        "audit_events": cleaning_payload["audit_events"],
        "report": report,
        "verdict_breakdown": dict(Counter(item["verdict"] for item in confidence)),
    }


def persist_payload(output_dir: Path, payload: dict[str, object]) -> None:
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
