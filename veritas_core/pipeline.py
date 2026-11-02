from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

from .models import (
    ActorType,
    Advisory,
    AdvisorySource,
    AdvisoryStatus,
    AuditEvent,
    CleaningAction,
    ColumnProfile,
    DataSource,
    Dataset,
    PipelineRun,
    QualityIssue,
    Severity,
    Verdict,
)


class VeritasPipeline:
    """
    Unified pipeline. Source-agnostic after ingest.
    Both Branch A (stream) and Branch B (batch) call run().
    """

    def run(
        self,
        df: pd.DataFrame,
        source: DataSource,
        rules_path=None,
        feedback_store=None,
        output_dir=None,
    ) -> PipelineRun:
        started = time.perf_counter()
        run = PipelineRun(
            source_id=source.id,
            branch="a" if source.source_type.value == "stream" else "b",
            source_type=source.source_type,
            domain_type=source.domain_type,
            domain_label=self._domain_label(source),
            status="running",
        )

        try:
            self._stage_ingest(df, source, run)
            run.stages_completed.append("ingest")

            profiles = self._stage_profile(df, run)
            run.stages_completed.append("profile")

            issues_raw = self._stage_validate(df, profiles, run, rules_path, feedback_store, output_dir)
            run.stages_completed.append("validate")

            scored_profiles = self._stage_score(df, profiles, issues_raw, run)
            run.profiles = scored_profiles
            run.stages_completed.append("score")

            advisories = self._stage_advise(issues_raw, scored_profiles, run)
            run.advisories = advisories
            run.stages_completed.append("advise")

            self._stage_audit(run)
            run.stages_completed.append("audit")

            run.status = "completed"

        except Exception:
            run.status = "failed"
            failed_after = run.stages_completed[-1] if run.stages_completed else "init"
            run.stages_completed.append(f"failed_at_{failed_after}")
            raise

        finally:
            run.elapsed_ms = (time.perf_counter() - started) * 1000
            run.completed_at = datetime.utcnow()

        return run

    def _stage_ingest(
        self,
        df: pd.DataFrame,
        source: DataSource,
        run: PipelineRun,
    ) -> Dataset:
        dataset = Dataset(
            source_id=source.id,
            pipeline_run_id=run.id,
            name=source.name,
            row_count=len(df),
            column_count=len(df.columns),
            domain_label=source.domain_type.value,
        )
        run.dataset_id = dataset.id
        return dataset

    def _stage_profile(
        self,
        df: pd.DataFrame,
        run: PipelineRun,
    ) -> list[ColumnProfile]:
        """
        Calls OperationalProfiler from veritas_operational.
        Returns list of ColumnProfile objects.
        """
        from veritas_core.discovery import SemanticDiscovery
        from veritas_operational.profiler import OperationalProfiler

        result = OperationalProfiler().profile(df)
        semantic_profiles = {
            profile.column_name: profile for profile in SemanticDiscovery().discover(df)
        }
        profiles = []
        for item in result.data:
            d = item.to_dict()
            semantic = semantic_profiles.get(d["name"])
            profiles.append(
                ColumnProfile(
                    column=d["name"],
                    archetype=d["archetype"],
                    role=semantic.role.value if semantic else "",
                    dtype=d["dtype"],
                    null_ratio=d["null_ratio"],
                    unique_ratio=d["unique_ratio"],
                    sample_values=d["sample_values"],
                    stats=d["stats"],
                )
            )
        return profiles

    def _stage_validate(
        self,
        df: pd.DataFrame,
        profiles: list[ColumnProfile],
        run: PipelineRun,
        rules_path=None,
        feedback_store=None,
        output_dir=None,
    ) -> list[QualityIssue]:
        """
        Calls ContextAwareRulesEngine or BusinessRulesEngine.
        Converts violation dicts to QualityIssue objects.
        """
        profile_dicts = [
            {
                "name": p.column,
                "archetype": p.archetype,
                "null_ratio": p.null_ratio,
                "unique_ratio": p.unique_ratio,
                "sample_values": p.sample_values,
                "stats": p.stats,
            }
            for p in profiles
        ]

        if rules_path and rules_path.exists() and self._should_use_business_rules(df, run):
            from veritas_operational.rules_engine import BusinessRulesEngine

            result = BusinessRulesEngine(rules_path).evaluate(df)
        else:
            from veritas_operational.rules_generator import ContextAwareRulesEngine

            result = ContextAwareRulesEngine(
                domain_label=run.domain_label,
                output_dir=str(output_dir) if output_dir else "",
            ).evaluate(df, profile_dicts)

        run.rule_count = result.metadata.get("rule_count", 0)
        run.llm_calls = result.metadata.get("llm_calls", 0)
        run.fallback_used = result.metadata.get("fallback_used", False)
        run.domain_interpretation = result.metadata.get("domain_interpretation", "")

        issues = []
        for v in result.data.get("violations", []):
            severity = self._severity(v.get("severity", "Minor"))
            issues.append(
                QualityIssue(
                    dataset_id=run.dataset_id,
                    column=str(v.get("column") or v.get("rule", "").split("_")[0]),
                    row_index=int(v.get("row_index", -1)),
                    issue_type=v.get("rule", ""),
                    severity=severity,
                    detected_by=v.get("constraint_source", "rules"),
                    value=v.get("value"),
                    rule=v.get("rule", ""),
                    message=v.get("message", ""),
                    confidence=1.0,
                )
            )
        run.violations_total = len(issues)
        run.issues = issues
        return issues

    def _stage_score(
        self,
        df: pd.DataFrame,
        profiles: list[ColumnProfile],
        issues: list[QualityIssue],
        run: PipelineRun,
    ) -> list[ColumnProfile]:
        """
        Calls compute_confidence from app_pipeline.
        Updates ColumnProfile objects with tau, delta, alpha, confidence, verdict.
        """
        from veritas_operational.app_pipeline import compute_confidence

        profile_dicts = [
            {
                "name": p.column,
                "archetype": p.archetype,
                "null_ratio": p.null_ratio,
            }
            for p in profiles
        ]
        violation_dicts = [
            {
                "rule": i.rule,
                "row_index": i.row_index,
            }
            for i in issues
        ]
        scored = compute_confidence(df, profile_dicts, violation_dicts)
        scored_map = {s["column"]: s for s in scored}
        for p in profiles:
            s = scored_map.get(p.column, {})
            p.tau = s.get("tau", 0.0)
            p.delta = s.get("delta", 0.0)
            p.alpha = s.get("alpha", 0.0)
            p.confidence = s.get("confidence", 0.0)
            p.verdict = Verdict(s.get("verdict", "trusted"))
        return profiles

    def _stage_advise(
        self,
        issues: list[QualityIssue],
        profiles: list[ColumnProfile],
        run: PipelineRun,
    ) -> list[Advisory]:
        """
        Generates Advisory records from QualityIssues.
        Rule-based advisories only for now.
        """
        advisories = []
        for issue in issues:
            adv = Advisory(
                issue_id=issue.id,
                dataset_id=run.dataset_id,
                column=issue.column,
                issue=issue.message,
                explanation=self._explain(issue),
                recommended_action=self._recommend(issue),
                confidence=issue.confidence,
                source=AdvisorySource.RULES,
                status=AdvisoryStatus.PENDING,
            )
            advisories.append(adv)
        return advisories

    def _stage_audit(self, run: PipelineRun) -> None:
        """
        Creates AuditEvent for every issue and advisory.
        Written once. Never modified.
        """
        for issue in run.issues:
            run.audit_events.append(
                AuditEvent(
                    pipeline_run_id=run.id,
                    dataset_id=run.dataset_id,
                    event_type="issue_detected",
                    entity_id=issue.id,
                    column=issue.column,
                    original=issue.value,
                    reason=issue.message,
                    actor="veritas_pipeline",
                    actor_type=ActorType.AUTO,
                )
            )
        for adv in run.advisories:
            run.audit_events.append(
                AuditEvent(
                    pipeline_run_id=run.id,
                    dataset_id=run.dataset_id,
                    event_type="advisory_created",
                    entity_id=adv.id,
                    column=adv.column,
                    reason=adv.recommended_action,
                    actor="veritas_pipeline",
                    actor_type=ActorType.AUTO,
                )
            )

    def _explain(self, issue: QualityIssue) -> str:
        explanations = {
            "missing": f"{issue.column} is null where a value is required.",
            "outlier": f"{issue.column} contains a value beyond expected range.",
            "physics": f"{issue.column} violates a physical constraint.",
            "range": f"{issue.column} value is outside allowed bounds.",
            "type": f"{issue.column} contains a value of unexpected type.",
            "cross_column": "Inconsistency detected between related columns.",
        }
        for key, text in explanations.items():
            if key in issue.issue_type.lower():
                return text
        return issue.message

    def _recommend(self, issue: QualityIssue) -> str:
        if "null" in issue.issue_type.lower() or "missing" in issue.issue_type.lower():
            return "Impute from domain context or flag for manual review."
        if "negative" in issue.message.lower():
            return "Quarantine row. Negative value is physically impossible."
        if "outlier" in issue.issue_type.lower():
            return "Review value. If confirmed anomaly, quarantine and flag."
        if "severity" in issue.column.lower() or "taxonomy" in issue.message.lower():
            return "Normalize to allowed taxonomy. Flag original for audit."
        return "Review and resolve before passing to decision layer."

    def _severity(self, value: object) -> Severity:
        text = str(value or "Minor")
        if text in {item.value for item in Severity}:
            return Severity(text)
        lower = text.lower()
        if lower in {"critical", "high"}:
            return Severity.CRITICAL
        if lower in {"major", "medium"}:
            return Severity.MAJOR
        return Severity.MINOR

    def _domain_label(self, source: DataSource) -> str:
        if source.config.get("domain_label"):
            return str(source.config["domain_label"])
        normalized_name = source.name.lower().replace("_", " ").replace("-", " ")
        if "retail" in normalized_name or "inventory" in normalized_name:
            return "Retail inventory"
        if "incident" in normalized_name:
            return "Industrial incident / BPM operations"
        if source.domain_type.value == "sensor":
            return "Sensor / telemetry data"
        if source.domain_type.value == "operational":
            return "Operational data"
        return source.name or "Unknown data"

    def _should_use_business_rules(self, df: pd.DataFrame, run: PipelineRun) -> bool:
        columns = {str(column).lower() for column in df.columns}
        industrial_markers = {"incident_id", "status", "severity"}
        return run.domain_type.value == "operational" and industrial_markers.issubset(columns)
