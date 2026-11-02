from __future__ import annotations

import json
import math
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from veritas_operational.data_loader import LoaderConfig, OperationalDataLoader
from veritas_operational.profiler import OperationalProfiler
from veritas_operational.rules_engine import BusinessRulesEngine


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "app_data"
OUTPUT_DIR = ROOT / "app_output"
RULES_PATH = ROOT / "src" / "veritas_operational" / "config" / "default_rules.yaml"
DATASET_PATH = DATA_DIR / "industrial_incidents.csv"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build_dataset() -> pd.DataFrame:
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

        row = {
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
        rows.append(row)

    frame = pd.DataFrame(rows)
    frame["age_hours"] = (
        (pd.Timestamp.now(tz=UTC) - pd.to_datetime(frame["opened_at"], utc=True)).dt.total_seconds() / 3600
    ).round(2)

    anomaly_rows = [5, 12, 28, 41, 57, 73, 92, 117, 144, 168]
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
    frame.loc[144, "incident_id"] = "INC-2026001-EXTREMELY-LONG-IDENTIFIER-OVERFLOW-DEMO"
    frame.loc[168, "description"] = (
        "Repeated downtime spike after containment failed during second shift. "
        "Manual bypass left active and quality hold was delayed."
    )

    frame["anomaly_seed"] = False
    frame.loc[anomaly_rows, "anomaly_seed"] = True
    return frame


def detect_domain(columns: list[str]) -> dict[str, object]:
    joined = " ".join(col.lower() for col in columns)
    if any(token in joined for token in ("incident", "severity", "downtime", "owner_team", "defect")):
        return {
            "domain": "industrial_incident_bpm",
            "label": "Industrial incident / BPM operations",
            "reason": "incident_id, severity, downtime, status and defect KPIs were detected",
        }
    return {"domain": "generic_operational", "label": "Generic operational data", "reason": "fallback"}


def compute_confidence(frame: pd.DataFrame, profiles: list[dict[str, object]], violations: list[dict[str, object]]) -> list[dict[str, object]]:
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
            finite_ratio = float(np.isfinite(valid).mean()) if not valid.empty else 0.0
            if valid.empty:
                alpha = 0.0
            else:
                q1 = valid.quantile(0.25)
                q3 = valid.quantile(0.75)
                iqr = q3 - q1
                if iqr == 0:
                    outlier_ratio = 0.0
                else:
                    lo = q1 - 1.5 * iqr
                    hi = q3 + 1.5 * iqr
                    outlier_ratio = float(((valid < lo) | (valid > hi)).mean())
                alpha = max(0.0, finite_ratio * (1.0 - outlier_ratio))
            tau = 0.98
        elif archetype == "IDENTIFIER":
            tokens = series.dropna().astype(str)
            if tokens.empty:
                alpha = 0.0
            else:
                lengths = tokens.str.len()
                dominant_length = lengths.mode().iloc[0]
                format_ratio = float((lengths == dominant_length).mean())
                prefix_ratio = float(tokens.str.contains(r"^[A-Z]+[-_]", regex=True).mean())
                alpha = max(0.0, min(1.0, 0.5 * format_ratio + 0.5 * prefix_ratio))
            tau = 0.99
        elif archetype == "CATEGORICAL":
            cardinality_ratio = float(series.nunique(dropna=True) / max(series.notna().sum(), 1))
            alpha = max(0.0, 1.0 - min(cardinality_ratio, 1.0) * 0.2)
            tau = 0.97
        else:
            lengths = series.dropna().astype(str).str.len()
            alpha = 0.9 if lengths.empty else max(0.0, min(1.0, lengths.between(12, 180).mean()))
            tau = 0.96

        delta = non_null_ratio

        related_violations = 0
        for rule_name, row_ids in violation_rows_by_rule.items():
            if name in rule_name or name == "closed_at" and "closed_at" in rule_name:
                related_violations += len(row_ids)

        score = round(max(0.0, min(1.0, tau * delta * alpha)), 4)
        violation_penalty = 1.0
        if related_violations:
            violation_penalty = max(0.55, 1.0 - (related_violations / max(len(frame), 1)) * 2.5)
        score = round(max(0.0, min(1.0, score * violation_penalty)), 4)
        if score >= 0.85:
            verdict = "trusted"
        elif score >= 0.65:
            verdict = "review"
        else:
            verdict = "quarantine"

        results.append(
            {
                "column": name,
                "archetype": archetype,
                "tau": round(tau, 4),
                "delta": round(delta, 4),
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


def build_dashboard(payload: dict[str, object]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Veritas Operational Demo</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --text: #16202b;
      --muted: #5d6b7b;
      --line: #d8e0ea;
      --ok: #17803d;
      --warn: #c67900;
      --bad: #b42318;
      --accent: #0057b8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); }}
    .wrap {{ max-width: 1380px; margin: 0 auto; padding: 20px; }}
    .band {{ display: grid; gap: 16px; margin-bottom: 16px; }}
    .band.cols-4 {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .band.cols-2 {{ grid-template-columns: 1.3fr 1fr; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    h1, h2 {{ margin: 0 0 12px; font-size: 20px; }}
    h2 {{ font-size: 16px; }}
    .kpi {{ font-size: 30px; font-weight: 700; margin-bottom: 6px; }}
    .sub {{ color: var(--muted); font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 8px 6px; vertical-align: top; }}
    .pill {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
    .trusted {{ background: #dcfae6; color: var(--ok); }}
    .review {{ background: #fff0d5; color: var(--warn); }}
    .quarantine {{ background: #fee4e2; color: var(--bad); }}
    .bar {{ height: 12px; background: #e8edf4; border-radius: 999px; overflow: hidden; }}
    .bar > span {{ display: block; height: 100%; background: var(--accent); }}
    .stack {{ display: grid; gap: 12px; }}
    .toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }}
    .toolbar input, .toolbar select, .toolbar button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }}
    .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
    .tab-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .view[hidden] {{ display: none; }}
    .clickable-row {{ cursor: pointer; }}
    .clickable-row.active {{ background: #eef5ff; }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    pre {{ white-space: pre-wrap; margin: 0; font-size: 13px; color: var(--text); }}
    @media (max-width: 980px) {{
      .band.cols-4, .band.cols-2 {{ grid-template-columns: 1fr; }}
      .detail-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel" style="margin-bottom:16px;">
      <h1>Veritas Branch B Demo</h1>
      <div class="sub" id="domain"></div>
    </div>
    <div class="tabs">
      <button class="tab-btn active" data-view="overview">Overview</button>
      <button class="tab-btn" data-view="confidence">Confidence</button>
      <button class="tab-btn" data-view="violations">Violations</button>
      <button class="tab-btn" data-view="profiles">Profiles</button>
      <button class="tab-btn" data-view="report">Report</button>
    </div>
    <div class="band cols-4" id="kpis"></div>
    <div class="view" id="view-overview">
    <div class="band cols-2">
      <section class="panel">
        <h2>Confidence by Column</h2>
        <div class="toolbar">
          <input id="column-search" type="text" placeholder="Filter columns">
          <select id="verdict-filter">
            <option value="all">All verdicts</option>
            <option value="trusted">Trusted</option>
            <option value="review">Review</option>
            <option value="quarantine">Quarantine</option>
          </select>
        </div>
        <table id="confidence-table"></table>
      </section>
      <section class="panel">
        <h2>Stage Results</h2>
        <div class="stack" id="stages"></div>
      </section>
    </div>
    <div class="panel">
      <h2>Selected Column</h2>
      <div class="detail-grid" id="column-detail"></div>
    </div>
    </div>
    <div class="view" id="view-confidence" hidden>
      <div class="panel">
        <h2>Confidence Explorer</h2>
        <table id="confidence-table-full"></table>
      </div>
    </div>
    <div class="view" id="view-violations" hidden>
    <div class="band cols-2">
      <section class="panel">
        <h2>Top Violations</h2>
        <div class="toolbar">
          <select id="severity-filter">
            <option value="all">All severities</option>
            <option value="Critical">Critical</option>
            <option value="Major">Major</option>
            <option value="Minor">Minor</option>
          </select>
        </div>
        <table id="violations-table"></table>
      </section>
      <section class="panel">
        <h2>Violation Summary</h2>
        <div class="stack" id="violation-summary"></div>
      </section>
    </div>
    </div>
    <div class="view" id="view-profiles" hidden>
    <div class="panel">
      <h2>Column Profiles</h2>
      <table id="profiles-table"></table>
    </div>
    </div>
    <div class="view" id="view-report" hidden>
    <div class="panel">
      <h2>Mini Report</h2>
      <pre id="report"></pre>
    </div>
    </div>
  </div>
  <script>
    let payload = null;
    let confidence = [];
    let profiles = [];
    let violations = [];
    let verdicts = {{}};
    let selectedColumn = null;
    async function loadPayload() {{
      const res = await fetch('/api/results');
      payload = await res.json();
      confidence = payload.confidence;
      profiles = payload.profiles;
      violations = payload.violations;
      verdicts = confidence.reduce((acc, row) => {{
        acc[row.verdict] = (acc[row.verdict] || 0) + 1;
        return acc;
      }}, {{}});
      selectedColumn = confidence[0]?.column || null;
      document.getElementById('domain').textContent = payload.domain.label + ' | ' + payload.domain.reason;
      document.getElementById('report').textContent = payload.report;
      const kpis = [
        ['Rows loaded', payload.loader.rows],
        ['Violations', payload.rules.violations_total],
        ['Trusted columns', verdicts.trusted || 0],
        ['Quarantine columns', verdicts.quarantine || 0]
      ];
      document.getElementById('kpis').innerHTML = kpis.map(([label, value]) => `
        <section class="panel"><div class="kpi">${{value}}</div><div class="sub">${{label}}</div></section>
      `).join('');
    }}
    function renderConfidenceTable(targetId, rows) {{
      document.getElementById(targetId).innerHTML =
      '<tr><th>Column</th><th>Archetype</th><th>C(v)</th><th>Verdict</th></tr>' +
      rows.map(row => `
        <tr>
          <td>${{row.column}}</td>
          <td>${{row.archetype}}</td>
          <td><div class="bar"><span style="width:${{(row.confidence * 100).toFixed(1)}}%"></span></div><div class="sub">${{row.confidence.toFixed(4)}} | tau=${{row.tau.toFixed(2)}} delta=${{row.delta.toFixed(2)}} alpha=${{row.alpha.toFixed(2)}}</div></td>
          <td><span class="pill ${{row.verdict}}">${{row.verdict}}</span></td>
        </tr>`).join('');
    }}
    function renderInteractiveConfidence() {{
      const q = document.getElementById('column-search').value.toLowerCase();
      const verdict = document.getElementById('verdict-filter').value;
      const rows = confidence.filter(row => {{
        const okVerdict = verdict === 'all' || row.verdict === verdict;
        const okQuery = !q || row.column.toLowerCase().includes(q) || row.archetype.toLowerCase().includes(q);
        return okVerdict && okQuery;
      }});
      const tableHtml = '<tr><th>Column</th><th>Archetype</th><th>C(v)</th><th>Verdict</th></tr>' +
      rows.map(row => `
        <tr class="clickable-row ${{row.column === selectedColumn ? 'active' : ''}}" data-column="${{row.column}}">
          <td>${{row.column}}</td>
          <td>${{row.archetype}}</td>
          <td><div class="bar"><span style="width:${{(row.confidence * 100).toFixed(1)}}%"></span></div><div class="sub">${{row.confidence.toFixed(4)}} | tau=${{row.tau.toFixed(2)}} delta=${{row.delta.toFixed(2)}} alpha=${{row.alpha.toFixed(2)}}</div></td>
          <td><span class="pill ${{row.verdict}}">${{row.verdict}}</span></td>
        </tr>`).join('');
      document.getElementById('confidence-table').innerHTML = tableHtml;
      document.querySelectorAll('#confidence-table .clickable-row').forEach(el => {{
        el.addEventListener('click', () => {{
          selectedColumn = el.dataset.column;
          renderInteractiveConfidence();
          renderColumnDetail();
        }});
      }});
      renderConfidenceTable('confidence-table-full', rows);
    }}
    function renderViolations() {{
      const severity = document.getElementById('severity-filter').value;
      const rows = violations.filter(row => severity === 'all' || row.severity === severity).slice(0, 20);
      document.getElementById('violations-table').innerHTML =
      '<tr><th>Incident</th><th>Severity</th><th>Rule</th><th>Message</th></tr>' +
      rows.map(row => `
        <tr>
          <td>${{row.incident_id}}</td>
          <td>${{row.severity}}</td>
          <td>${{row.rule}}</td>
          <td>${{row.message}}</td>
        </tr>`).join('');
    }}
    function renderColumnDetail() {{
      const score = confidence.find(x => x.column === selectedColumn);
      const profile = profiles.find(x => x.name === selectedColumn);
      if (!score || !profile) return;
      document.getElementById('column-detail').innerHTML = `
        <div><strong>Column</strong><div class="sub">${{score.column}}</div></div>
        <div><strong>Archetype</strong><div class="sub">${{score.archetype}}</div></div>
        <div><strong>Verdict</strong><div class="sub"><span class="pill ${{score.verdict}}">${{score.verdict}}</span></div></div>
        <div><strong>Null ratio</strong><div class="sub">${{(profile.null_ratio * 100).toFixed(1)}}%</div></div>
        <div><strong>Unique ratio</strong><div class="sub">${{(profile.unique_ratio * 100).toFixed(1)}}%</div></div>
        <div><strong>Rule hits</strong><div class="sub">${{score.related_rule_hits}}</div></div>
        <div style="grid-column:1/-1;"><strong>Sample values / stats</strong><div class="sub">${{JSON.stringify({{sample_values: profile.sample_values, stats: profile.stats}})}}</div></div>
      `;
    }}
    document.getElementById('profiles-table').innerHTML =
      '<tr><th>Column</th><th>Archetype</th><th>Null%</th><th>Stats</th></tr>' +
      profiles.map(row => `
        <tr>
          <td>${{row.name}}</td>
          <td>${{row.archetype}}</td>
          <td>${{(row.null_ratio * 100).toFixed(1)}}</td>
          <td><div class="sub">${{JSON.stringify(row.stats)}}</div></td>
        </tr>`).join('');
    document.getElementById('violation-summary').innerHTML = Object.entries(payload.rules.violations_by_severity).map(([k, v]) => `
      <div><strong>${{k}}</strong><div class="sub">${{v}} triggered rows</div></div>
    `).join('');
    const stageMeta = [
      ['Loader', `${{payload.loader.rows}} rows | ${{payload.loader.columns.length}} columns | ${{payload.loader.elapsed_ms.toFixed(1)}} ms`],
      ['Profiler', `${{payload.profiler.column_count}} columns profiled | ${{payload.profiler.elapsed_ms.toFixed(1)}} ms`],
      ['Rules engine', `${{payload.rules.rule_count}} rules | ${{payload.rules.violations_total}} hits | ${{payload.rules.elapsed_ms.toFixed(1)}} ms`],
    ];
    document.getElementById('stages').innerHTML = stageMeta.map(([name, meta]) => `
      <div><strong>${{name}}</strong><div class="sub">${{meta}}</div></div>
    `).join('');
    document.getElementById('column-search').addEventListener('input', renderInteractiveConfidence);
    document.getElementById('verdict-filter').addEventListener('change', renderInteractiveConfidence);
    document.getElementById('severity-filter').addEventListener('change', renderViolations);
    document.querySelectorAll('.tab-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.tab-btn').forEach(x => x.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.view').forEach(view => view.hidden = true);
        document.getElementById('view-' + btn.dataset.view).hidden = false;
      }});
    }});
    loadPayload().then(() => {{
      renderInteractiveConfidence();
      renderViolations();
      renderColumnDetail();
    }});
  </script>
</body>
</html>"""


def main() -> None:
    ensure_dirs()
    dataset = build_dataset()
    dataset.to_csv(DATASET_PATH, index=False)

    loader = OperationalDataLoader(LoaderConfig(source_path=DATASET_PATH))
    loaded = loader.load()
    frame = loaded.data.copy()

    domain = detect_domain(list(frame.columns))
    profiler = OperationalProfiler().profile(frame)
    profile_dicts = [item.to_dict() for item in profiler.data]
    rules = BusinessRulesEngine(RULES_PATH).evaluate(frame)
    confidence = compute_confidence(rules.data["frame"], profile_dicts, rules.data["violations"])
    report = build_report(domain, rules.metadata, confidence)

    payload = {
        "domain": domain,
        "loader": {**loaded.metadata, "elapsed_ms": round(loaded.elapsed_ms, 2)},
        "profiler": {**profiler.metadata, "elapsed_ms": round(profiler.elapsed_ms, 2)},
        "rules": {**rules.metadata, "elapsed_ms": round(rules.elapsed_ms, 2)},
        "profiles": profile_dicts,
        "confidence": confidence,
        "violations": rules.data["violations"],
        "report": report,
        "verdict_breakdown": dict(Counter(item["verdict"] for item in confidence)),
    }

    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    (OUTPUT_DIR / "index.html").write_text(build_dashboard(payload), encoding="utf-8")

    print("VERITAS BRANCH B DEMO")
    print(f"Dataset     : {DATASET_PATH}")
    print(f"Domain      : {domain['label']}")
    print(f"Rows        : {loaded.metadata['rows']}")
    print(f"Violations  : {rules.metadata['violations_total']}")
    print("Worst columns by confidence:")
    for item in confidence[:5]:
        print(
            f"  - {item['column']}: C(v)={item['confidence']:.4f} "
            f"[tau={item['tau']:.4f}, delta={item['delta']:.4f}, alpha={item['alpha']:.4f}] => {item['verdict']}"
        )
    print("\nMini report")
    print(report)
    print(f"\nDashboard   : {OUTPUT_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
