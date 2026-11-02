from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from veritas_core.feedback.constraint_store import ConstraintStore
from veritas_core.feedback.feedback_store import FeedbackStore

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from veritas_operational.app_pipeline import (
    build_sample_dataset,
    ensure_dirs,
    run_pipeline,
)

DATA_DIR, OUTPUT_DIR, UPLOAD_DIR = ensure_dirs(ROOT)
RULES_PATH = ROOT / "src" / "veritas_operational" / "config" / "default_rules.yaml"
RESULTS_PATH = OUTPUT_DIR / "results.json"
SAMPLE_PATH = DATA_DIR / "industrial_incidents.csv"
DASHBOARD_PATH = ROOT / "dashboard_template.html"
CONSTRAINT_STORE_PATH = OUTPUT_DIR / "veritas_constraint_store.json"
FEEDBACK_STORE_PATH = OUTPUT_DIR / "veritas_feedback.json"

app = FastAPI(title="Veritas Operational API", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _bootstrap() -> None:
    if not SAMPLE_PATH.exists():
        build_sample_dataset().to_csv(SAMPLE_PATH, index=False)
    if not RESULTS_PATH.exists():
        payload = run_pipeline(SAMPLE_PATH, RULES_PATH)
        _persist_results(payload)


def _load_results() -> dict:
    _bootstrap()
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))


def _persist_results(payload: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _get_constraint_store() -> ConstraintStore:
    return ConstraintStore(str(CONSTRAINT_STORE_PATH))


def _get_feedback_store() -> FeedbackStore:
    return FeedbackStore(str(FEEDBACK_STORE_PATH))


def _live_payload(payload: dict) -> dict:
    confidence_rows = payload.get("confidence", [])
    quality_issues = payload.get("quality_issues", [])
    advisories = payload.get("advisories", [])
    violations = payload.get("violations", [])
    audit_events = payload.get("audit_events", [])
    loader = payload.get("loader", {})
    rules_meta = payload.get("rules", {})
    pipeline_run = payload.get("pipeline_run", {})
    metrics = pipeline_run.get("metrics", {})
    now = datetime.now(UTC).isoformat()

    live_confidence = [_live_confidence_row(row, now) for row in confidence_rows]
    total_scored = int(loader.get("rows") or metrics.get("rows") or len(confidence_rows))
    anomaly_count = len(quality_issues) if quality_issues else (
        len(violations) + len([row for row in confidence_rows if row.get("verdict") != "trusted"])
    )
    avg_confidence = _avg(row.get("confidence") for row in confidence_rows)
    latest_log = (
        f"batch rows={total_scored} violations={len(violations)} "
        f"advisories={len(advisories)} cleaning_actions={len(payload.get('cleaning_actions', []))}"
    )

    return {
        "summary": {
            "total_scored": total_scored,
            "anomaly_count": anomaly_count,
            "avg_confidence": round(avg_confidence, 4),
            "total_audit": len(audit_events),
            "recent_rate": 0,
            "lag_p95_ms": 0,
            "sensor_count": len(confidence_rows),
            "violations_total": len(violations),
            "quality_issues": len(quality_issues),
            "cleaning_actions": len(payload.get("cleaning_actions", [])),
            "llm_calls": int(rules_meta.get("llm_calls") or 0),
            "cache_hits": int(rules_meta.get("cache_hits") or 0),
            "fallback_columns": rules_meta.get("fallback_columns", []),
            "fallback_used": rules_meta.get("fallback_used", False),
            "columns_analyzed": rules_meta.get("columns_analyzed"),
            "columns_skipped": rules_meta.get("columns_skipped"),
            "columns_uncapped": rules_meta.get("columns_uncapped"),
            "domain_interpretation": rules_meta.get("domain_interpretation"),
        },
        "runtime": {
            "rate_per_sec": 0,
            "scored": total_scored,
            "anomalies": anomaly_count,
            "malformed": 0,
            "errors": 0,
            "updated_at": pipeline_run.get("finished_at") or now,
            "latest_log": latest_log,
        },
        "confidence": live_confidence,
        "advisories": [_live_advisory_row(item, idx) for idx, item in enumerate(advisories[:12])],
    }


def _live_confidence_row(row: dict, now: str) -> dict:
    confidence = float(row.get("confidence") or 0.0)
    verdict = str(row.get("verdict") or "")
    column = str(row.get("column") or "unknown")
    return {
        "sensor_id": column,
        "scored_at": now,
        "tau": row.get("tau", 0),
        "delta": row.get("delta", 0),
        "alpha": row.get("alpha", 0),
        "confidence": confidence,
        "value": confidence,
        "unit": "C(v)",
        "is_anomaly": verdict != "trusted",
        "anomaly_label": verdict.upper() if verdict != "trusted" else None,
        "archetype": row.get("archetype"),
        "verdict": verdict,
        "related_rule_hits": row.get("related_rule_hits", 0),
    }


def _live_advisory_row(item: dict, idx: int) -> dict:
    metadata = item.get("metadata") or {}
    violation = metadata.get("violation") if isinstance(metadata, dict) else None
    score = metadata.get("score") if isinstance(metadata, dict) else None
    sensor_id = None
    if isinstance(violation, dict):
        sensor_id = violation.get("column") or violation.get("rule")
    if sensor_id is None and isinstance(score, dict):
        sensor_id = score.get("column")
    return {
        "sensor_id": str(sensor_id or f"advisory-{idx + 1}"),
        "message": (
            (violation.get("message") if isinstance(violation, dict) else None)
            or item.get("issue")
            or item.get("explanation")
            or item.get("recommended_action")
            or "Review advisory"
        ),
        "source": item.get("source"),
        "status": item.get("status"),
        "confidence": item.get("confidence"),
    }


def _avg(values) -> float:
    numeric = [float(value) for value in values if value is not None]
    return sum(numeric) / len(numeric) if numeric else 0.0


def _build_workbook(data: dict):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import DataBarRule
    from openpyxl.styles import Alignment, Font, PatternFill

    def compact_json(value: object) -> str:
        return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))

    def filename_only(value: object) -> str:
        if not value:
            return ""
        return Path(str(value)).name

    def as_float(value: object, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def as_percent(value: object) -> float:
        return as_float(value)

    def style_header(row, fill_color: str) -> None:
        fill = PatternFill("solid", fgColor=fill_color)
        for cell in row:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = fill
            cell.alignment = Alignment(vertical="center")

    def apply_autofilter(ws) -> None:
        if ws.max_row > 1 and ws.max_column > 1:
            ws.auto_filter.ref = ws.dimensions

    def extract_column_from_rule(rule_name: object) -> str:
        suffix_words = {"excessive", "above", "below", "negative", "unexpected", "high"}
        tokens = str(rule_name or "").split("_")
        prefix: list[str] = []
        for token in tokens:
            if token in suffix_words:
                break
            prefix.append(token)
        return "_".join(prefix) if prefix else str(rule_name or "")

    def build_advisory_lookup(violation_rows: list[dict]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for violation in violation_rows:
            advice = str(violation.get("expected_range_note") or violation.get("message") or "")
            if not advice:
                continue
            rule_column = extract_column_from_rule(violation.get("rule"))
            explicit_column = str(violation.get("column") or "")
            if rule_column and rule_column not in lookup:
                lookup[rule_column] = advice
            if explicit_column and explicit_column not in lookup:
                lookup[explicit_column] = advice
        return lookup

    domain = data.get("domain", {})
    loader = data.get("loader", {})
    profiler = data.get("profiler", {})
    rules = data.get("rules", {})
    confidence = list(data.get("confidence", []))
    violations = list(data.get("violations", []))
    profiles = list(data.get("profiles", []))
    discovery_profiles = list((data.get("discovery") or {}).get("profiles", []))
    advisory_lookup = build_advisory_lookup(violations)

    profile_by_name = {str(item.get("name")): item for item in profiles}
    discovery_by_name = {str(item.get("column_name")): item for item in discovery_profiles}
    constraint_source_by_column: dict[str, str] = {}
    for violation in violations:
        column = str(violation.get("column") or "")
        source = str(violation.get("constraint_source") or "")
        if column and source and column not in constraint_source_by_column:
            constraint_source_by_column[column] = source

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_scores = wb.create_sheet("Column Scores")
    ws_violations = wb.create_sheet("Violations & LLM Advice")
    ws_profiles = wb.create_sheet("Raw Profiles")

    # Sheet 1 - Summary
    ws_summary.sheet_view.showGridLines = False
    ws_summary.merge_cells("A1:B1")
    title = ws_summary["A1"]
    title.value = "ASSAY Data Quality Report"
    title.font = Font(bold=True, size=14, color="FFFFFF")
    title.fill = PatternFill("solid", fgColor="1A1A2E")
    title.alignment = Alignment(horizontal="center")
    ws_summary.column_dimensions["A"].width = 28
    ws_summary.column_dimensions["B"].width = 36

    verdict_counts = {"trusted": 0, "review": 0, "quarantine": 0}
    for row in confidence:
        verdict = str(row.get("verdict") or "").lower()
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1

    summary_rows = [
        ("Dataset", loader.get("uploaded_filename") or filename_only(loader.get("source_path"))),
        ("Domain", domain.get("label", "")),
        ("Domain interpretation", rules.get("domain_interpretation", "")),
        ("Columns analyzed by LLM", rules.get("columns_analyzed") if rules.get("columns_analyzed") is not None else "N/A"),
        ("Columns skipped", rules.get("columns_skipped") if rules.get("columns_skipped") is not None else "N/A"),
        ("Fallback used", "Yes" if rules.get("fallback_used") else "No"),
    ]
    if rules.get("fallback_used"):
        summary_rows.append(
            (
                "Fallback reason",
                "Anonymous column names — LLM constraint reasoning not applicable",
            )
        )
    summary_rows.extend(
        [
            ("Rows processed", loader.get("rows", "")),
            ("Columns profiled", profiler.get("column_count", "")),
            ("Pipeline run at", datetime.now(UTC).isoformat()),
            ("Rules evaluated", rules.get("rule_count", "")),
            ("Violations detected", rules.get("violations_total", "")),
            ("LLM calls made", rules.get("llm_calls", "")),
            ("Avg confidence C(v)", round(_avg(row.get("confidence") for row in confidence), 4)),
            ("Trusted columns", verdict_counts["trusted"]),
            ("Review columns", verdict_counts["review"]),
            ("Quarantine columns", verdict_counts["quarantine"]),
        ]
    )
    for idx, (metric, value) in enumerate(summary_rows, start=2):
        ws_summary.cell(idx, 1, metric)
        ws_summary.cell(idx, 2, value)
        fill_color = "F8F8F8" if idx % 2 == 0 else "FFFFFF"
        for cell in ws_summary[idx]:
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws_summary.cell(idx, 1).font = Font(bold=True)

    # Sheet 2 - Column Scores
    score_headers = [
        "Column",
        "Archetype",
        "Null ratio",
        "τ (tau)",
        "δ (delta)",
        "α (alpha)",
        "C(v)",
        "Verdict",
        "Rule hits",
        "Constraint src",
    ]
    ws_scores.append(score_headers)
    style_header(ws_scores[1], "0F3460")
    for item in sorted(confidence, key=lambda row: as_float(row.get("confidence"))):
        column = str(item.get("column") or "")
        profile = profile_by_name.get(column, {})
        ws_scores.append(
            [
                column,
                item.get("archetype", ""),
                as_percent(profile.get("null_ratio")),
                round(as_float(item.get("tau")), 4),
                round(as_float(item.get("delta")), 4),
                round(as_float(item.get("alpha")), 4),
                round(as_float(item.get("confidence")), 4),
                item.get("verdict", ""),
                int(item.get("related_rule_hits") or 0),
                constraint_source_by_column.get(column, ""),
            ]
        )
    ws_scores.freeze_panes = "A2"
    apply_autofilter(ws_scores)
    for col in ["C"]:
        for cell in ws_scores[col][1:]:
            cell.number_format = "0.00%"
    for col in ["D", "E", "F", "G"]:
        for cell in ws_scores[col][1:]:
            cell.number_format = "0.0000"
    if ws_scores.max_row > 1:
        ws_scores.conditional_formatting.add(
            f"G2:G{ws_scores.max_row}",
            DataBarRule(start_type="num", start_value=0.0, end_type="num", end_value=1.0, color="63C384"),
        )
    verdict_styles = {
        "trusted": ("D4EDDA", "155724"),
        "review": ("FFF3CD", "856404"),
        "quarantine": ("F8D7DA", "721C24"),
    }
    for row in range(2, ws_scores.max_row + 1):
        verdict = str(ws_scores.cell(row, 8).value or "").lower()
        if verdict in verdict_styles:
            fill_color, font_color = verdict_styles[verdict]
            ws_scores.cell(row, 8).fill = PatternFill("solid", fgColor=fill_color)
            ws_scores.cell(row, 8).font = Font(color=font_color)
    widths = [24, 16, 14, 12, 12, 12, 12, 14, 12, 16]
    for idx, width in enumerate(widths, start=1):
        ws_scores.column_dimensions[chr(64 + idx)].width = width

    # Sheet 3 - Violations & LLM Advice
    violation_headers = [
        "Row index",
        "Column",
        "Rule",
        "Severity",
        "Message",
        "LLM advice",
        "Constraint src",
        "Expected range",
    ]
    domain_interpretation = str(rules.get("domain_interpretation") or "")
    if domain_interpretation:
        ws_violations.append([f"Domain interpretation: {domain_interpretation}"] + [""] * 7)
        ws_violations.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
        context_cell = ws_violations["A1"]
        context_cell.font = Font(italic=True, color="0C5460")
        context_cell.fill = PatternFill("solid", fgColor="D1ECF1")
        context_cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws_violations.row_dimensions[1].height = 24
        violation_header_row = 2
    else:
        violation_header_row = 1
    ws_violations.append(violation_headers)
    style_header(ws_violations[violation_header_row], "0F3460")
    for violation in violations:
        column = str(violation.get("column") or "")
        if not column:
            column = extract_column_from_rule(violation.get("rule"))
        ws_violations.append(
            [
                violation.get("row_index", ""),
                column,
                violation.get("rule", ""),
                violation.get("severity", ""),
                violation.get("message", ""),
                advisory_lookup.get(column, "No advisory generated"),
                violation.get("constraint_source", ""),
                violation.get("expected_range_note", ""),
            ]
        )
    violation_data_start = violation_header_row + 1
    ws_violations.freeze_panes = f"A{violation_data_start}"
    if ws_violations.max_row >= violation_header_row:
        ws_violations.auto_filter.ref = f"A{violation_header_row}:H{ws_violations.max_row}"
    severity_styles = {
        "Critical": ("F8D7DA", "721C24"),
        "Major": ("FFF3CD", "856404"),
        "Minor": ("D1ECF1", "0C5460"),
    }
    for row in range(violation_data_start, ws_violations.max_row + 1):
        ws_violations.row_dimensions[row].height = 18
        severity = str(ws_violations.cell(row, 4).value or "")
        if severity in severity_styles:
            fill_color, font_color = severity_styles[severity]
            ws_violations.cell(row, 4).fill = PatternFill("solid", fgColor=fill_color)
            ws_violations.cell(row, 4).font = Font(color=font_color)
        ws_violations.cell(row, 5).alignment = Alignment(wrap_text=True, vertical="top")
        ws_violations.cell(row, 6).alignment = Alignment(wrap_text=True, vertical="top")
    violation_widths = [12, 22, 28, 12, 52, 52, 16, 44]
    for idx, width in enumerate(violation_widths, start=1):
        ws_violations.column_dimensions[chr(64 + idx)].width = width

    # Sheet 4 - Raw Profiles
    raw_headers = [
        "Column",
        "Archetype",
        "Role",
        "Dtype",
        "Null ratio",
        "Unique ratio",
        "Sample values",
        "Stats",
        "Discovery notes",
        "Discovery anomalies",
    ]
    ws_profiles.append(raw_headers)
    style_header(ws_profiles[1], "16213E")
    for profile in profiles:
        column = str(profile.get("name") or "")
        discovery = discovery_by_name.get(column, {})
        sample_values = profile.get("sample_values") or []
        anomalies = discovery.get("anomalies") or []
        ws_profiles.append(
            [
                column,
                profile.get("archetype", ""),
                discovery.get("role", ""),
                profile.get("dtype", ""),
                as_percent(profile.get("null_ratio")),
                as_percent(profile.get("unique_ratio")),
                " | ".join(str(value) for value in sample_values[:3]),
                compact_json(profile.get("stats")),
                discovery.get("notes", ""),
                ", ".join(str(item) for item in anomalies),
            ]
        )
    ws_profiles.freeze_panes = "A2"
    apply_autofilter(ws_profiles)
    for col in ["E", "F"]:
        for cell in ws_profiles[col][1:]:
            cell.number_format = "0.00%"
    raw_widths = [24, 16, 18, 16, 12, 12, 34, 40, 42, 36]
    for idx, width in enumerate(raw_widths, start=1):
        ws_profiles.column_dimensions[chr(64 + idx)].width = width
    for row in range(2, ws_profiles.max_row + 1):
        ws_profiles.cell(row, 8).alignment = Alignment(wrap_text=True, vertical="top")

    return wb


@app.get("/")
def root() -> FileResponse:
    return FileResponse(DASHBOARD_PATH)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/results")
def results() -> JSONResponse:
    return JSONResponse(_load_results())


@app.get("/api/export/excel")
def export_excel() -> StreamingResponse:
    data = _load_results()
    workbook = _build_workbook(data)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    filename = f"assay_report_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/live")
def live() -> JSONResponse:
    return JSONResponse(_live_payload(_load_results()))


@app.get("/api/confidence")
def confidence() -> JSONResponse:
    return JSONResponse(_load_results()["confidence"])


@app.get("/api/violations")
def violations() -> JSONResponse:
    return JSONResponse(_load_results()["violations"])


@app.get("/api/quality-issues")
def quality_issues() -> JSONResponse:
    return JSONResponse(_load_results().get("quality_issues", []))


@app.get("/api/advisories")
def advisories() -> JSONResponse:
    return JSONResponse(_load_results().get("advisories", []))


@app.post("/api/advisory/{advisory_id}/accept")
async def accept_advisory(advisory_id: str, request: Request) -> JSONResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    reason = body.get("reason", "") if isinstance(body, dict) else ""

    data = _load_results()
    target = _find_advisory(data.get("advisories", []), advisory_id)
    if target is None:
        return JSONResponse({"error": "advisory not found"}, status_code=404)

    target["status"] = "accepted"
    target["resolved_at"] = datetime.now(UTC).isoformat()
    _persist_results(data)

    feedback_store = _get_feedback_store()
    constraint_store = _get_constraint_store()
    feedback_store.record_advisory_outcome(
        advisory_id=advisory_id,
        column=target.get("column", "") or target.get("metadata", {}).get("column", ""),
        rule=target.get("rule", ""),
        status="accepted",
        constraint=target.get("constraint", {}),
        domain_label=target.get("domain_label", data.get("domain", {}).get("label", "")),
        reason=reason,
        constraint_store=constraint_store,
    )

    return JSONResponse(
        {
            "advisory_id": advisory_id,
            "status": "accepted",
            "constraint_learned": True,
            "message": (
                "Advisory accepted. Constraint stored for future runs "
                f"on columns matching '{target.get('column', '')}' "
                f"in '{target.get('domain_label', '')}' domain."
            ),
        }
    )


@app.post("/api/advisory/{advisory_id}/reject")
async def reject_advisory(advisory_id: str, request: Request) -> JSONResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    reason = body.get("reason", "") if isinstance(body, dict) else ""

    data = _load_results()
    target = _find_advisory(data.get("advisories", []), advisory_id)
    if target is None:
        return JSONResponse({"error": "advisory not found"}, status_code=404)

    target["status"] = "rejected"
    target["resolved_at"] = datetime.now(UTC).isoformat()
    _persist_results(data)

    feedback_store = _get_feedback_store()
    constraint_store = _get_constraint_store()
    feedback_store.record_advisory_outcome(
        advisory_id=advisory_id,
        column=target.get("column", "") or target.get("metadata", {}).get("column", ""),
        rule=target.get("rule", ""),
        status="rejected",
        constraint=target.get("constraint", {}),
        domain_label=target.get("domain_label", data.get("domain", {}).get("label", "")),
        reason=reason,
        constraint_store=constraint_store,
    )

    return JSONResponse(
        {
            "advisory_id": advisory_id,
            "status": "rejected",
            "constraint_weakened": True,
            "message": "Advisory rejected. Pattern confidence reduced.",
        }
    )


@app.get("/api/feedback/constraints")
def feedback_constraints() -> JSONResponse:
    store = _get_constraint_store()
    return JSONResponse({"count": len(store.patterns), "constraints": store.list_all()})


def _find_advisory(advisories: list[dict], advisory_id: str) -> dict | None:
    for advisory in advisories:
        if advisory.get("id") == advisory_id or advisory.get("advisory_id") == advisory_id:
            return advisory
    return None


@app.get("/api/cleaning-actions")
def cleaning_actions() -> JSONResponse:
    return JSONResponse(_load_results().get("cleaning_actions", []))


@app.get("/api/audit-events")
def audit_events() -> JSONResponse:
    return JSONResponse(_load_results().get("audit_events", []))


@app.get("/api/profiles")
def profiles() -> JSONResponse:
    return JSONResponse(_load_results()["profiles"])


@app.post("/api/upload")
async def upload(request: Request, filename: str = "upload.csv") -> JSONResponse:
    _bootstrap()
    uploaded_filename = Path(filename).name or "upload.csv"
    suffix = Path(uploaded_filename).suffix or ".csv"
    target = UPLOAD_DIR / f"uploaded{suffix}"
    target.write_bytes(await request.body())
    payload = run_pipeline(target, RULES_PATH)
    payload["loader"]["source_path"] = str(target)
    payload["loader"]["uploaded_filename"] = uploaded_filename
    _persist_results(payload)
    return JSONResponse(payload)
