# Prior Art Search Report — VERITAS

> **Search caveat:** Google Patents and Espacenet were unreachable in this environment (HTTP 403 / JS-rendered), and Semantic Scholar was rate-limited (HTTP 429). The patent results below are therefore drawn from known industry IP areas and are marked **[VERIFY]** — treat them as leads for a proper FTO run, not confirmed citations. The non-patent literature is from live arXiv API queries and is real. Classical references (FDI canon, W3C DQV) are marked as established-knowledge citations — they are real, widely known works but their exact identifiers should be confirmed before relying on them in a filing.

---

## Invention Summary

A per-observation confidence scalar for industrial sensor telemetry, **C(v) = τ(temporal freshness) × δ(sample density) × α(signal stability)** — a *multiplicative* product so any collapsed factor drives trust to zero — parameterized per sensor *archetype* (inertial/cumulative/cyclical/binary), gated by CUSUM drift detection, optionally adjusted by an LLM-mediated physics-law validator (deterministic physics checks called via tool-use, LLM only returns a confidence delta and never writes values), and mapped onto a **confidence-threshold → compliance-action** table (ISO 9001 / AS9100D / IATF 16949 → NCR / BLOCK_RELEASE).

## Search Strategy

- **Keywords used:** sensor data quality/confidence score, telemetry veracity, multiplicative quality index, freshness×density×stability, CUSUM drift detection, LLM sensor anomaly detection, physics-informed validation, data-quality nonconformance quarantine.
- **IPC/CPC classes (predicted):** G05B 23/02 (electric testing/monitoring of control systems — *the canonical FDI home*), G06F 17/18 & G06F 18/24 (statistical data processing / classification), G06Q 10/0639 & 10/06393 (quality management/analysis of data), G01D 3/028 (measuring-device reliability/fault detection), G06N 5/04 (inference/LLM reasoning applied to data).
- **Databases searched:** arXiv API (live, results verified); Google Patents & Espacenet & FreePatentsOnline (unreachable — 403/ECONNRESET); Semantic Scholar (rate-limited 429); Google Scholar (not reachable).
- **Date range:** ~2019–2026 for arXiv; classical references pre-2010 included as canonical background.

---

## Non-Patent Literature Found (verified via arXiv)

| # | Reference | Year | Key Contribution | Overlap Risk |
|---|-----------|------|------------------|--------------|
| 1 | **Distinguishing Process Change, Instrument Fault and Data-Path Corruption in Streaming Telemetry** (Klymenko) | 2026 | Detector bank — Kalman tracker + CUSUM + EWMA on innovation — to *attribute* anomalies to process change vs. instrument fault vs. data corruption. **Closest single work to VERITAS's per-reading diagnostic intent.** | **HIGH** |
| 2 | **Multi-Dimensional Entropy for Vibration Data Quality Control in Wind Turbines** (×2 related) | 2026 | Entropy-based front-end *quality gate* flagging faulty vibration signals from sensor malfunction/shutdown at >99% accuracy — an industrial per-signal quality score already deployed. | **HIGH** |
| 3 | **Multivariate Time Series Anomaly Detection in Industry 5.0** | 2025 | Embedding-based anomaly detection on a real plant, robust to unlabeled/mislabeled sensor data. | MEDIUM |
| 4 | **A feature-based framework for detecting technical outliers in water-quality data** (`oddwater` R package) | 2019 | Unsupervised outlier *scoring* of in-situ river sensor readings w/ extreme-value thresholds. | MEDIUM |
| 5 | **Physics-guided denoiser network for additive-manufacturing data quality** | 2025 | Enforces physical consistency on sensor data via energy-based model + Fisher regularization — physics-constraint validation of telemetry. | MEDIUM |
| 6 | **SPADE: LLM framework for soil-moisture anomaly detection** | 2025 | GPT-4.1 zero-shot detection/classification of sensor anomalies — LLM-in-loop sensor fault detection, though generative not tool-use-mediated. | MEDIUM |
| 7 | **LLM-Enhanced RL for Time-Series Anomaly Detection** | 2026 | LLM reward-shaping + RL + VAE for sensor-network anomaly detection. | MEDIUM |
| 8 | **Domain-Prior-Regularized Graph Modeling for CPS Anomaly Detection** | 2026 | LLM mines system docs for physical sensor couplings to gate a graph model (SKAB benchmark) — LLM *extracting physics constraints*, the closest analog to the physics-LLM pattern. | **HIGH** (for the LLM-physics element) |
| 9 | **Data Quality Audit + Temp-Humidity Analysis of LoRaWAN Network** | 2026 | Per-variable coverage audit + modified Z-score + Isolation Forest scoring on multi-sensor IoT data. | MEDIUM |
| 10 | **Text-Informed Training-Data Filtering for Virtual Flow Meter** | 2026 | LLM combines operator logs + sensor readings to filter anomalous data. | LOW |
| 11 | **Multimodal AI for Laying-Hen Welfare — "Data Reliability Index (DRI)"** | 2025 | An explicit named *sensor-data-quality score* (DRI) + domain-transfer score. | MEDIUM |
| 12 | **Asynchronous Multi-Sensor Change-Point Detection (Subspace-CUSUM)** | 2019 | Multi-sensor CUSUM change-point detection with delay estimation — CUSUM-on-telemetry prior art. | MEDIUM |
| 13 | **AgenticTwin: agentic LLM + digital twin for anomaly detection** | 2026 | LLM reasoning agents explain anomalies in sensor data + operator QA. | MEDIUM |

---

## Prior Art Landscape (by technical element)

### Element A — Multiplicative per-reading quality scalar `τ×δ×α`
**Novelty appears strongest here.** The literature has many *sensor-data-quality scores* (DRI #11, MDE entropy #2, Z-score/Isolation-Forest scoring #9, oddwater #4) and many *freshness/staleness* and *density/completeness* metrics individually — data-quality-dimension research has treated freshness, completeness, and accuracy as separate axes for two decades. **What I did not find is anyone combining them multiplicatively into a single per-reading trust scalar with the explicit "any factor → 0 kills trust" semantics.** That decomposition is the most defensible claim — but it's an *engineering-composition* novelty, likely patentable only narrowly (the specific formula + threshold-action coupling), not a broad concept.

### Element B — CUSUM drift gating
**Fully anticipated / textbook.** Subspace-CUSUM (#12) and Klymenko's Kalman+CUSUM+EWMA detector bank (#1) cover CUSUM on telemetry; CUSUM itself is Page (1954) canonical SPC. No novelty — this is prior art, exactly as the council's reviewer flagged.

### Element C — Per-archetype scoring parameters (inertial/cumulative/cyclical/binary)
**Partially anticipated.** Sensor-archetype-specific detection exists across FDI; the specific four-archetype taxonomy with per-archetype τ/δ/α formulas is an implementation detail unlikely to be independently claimed but also unlikely to be broadly novel.

### Element D — LLM-mediated physics-law validation
**Active research area, crowded as of 2025–2026.** SPADE (#6), LLM-RL (#7), graph-model LLM-coupling (#8), AgenticTwin (#13) all put LLMs in the sensor-anomaly loop. VERITAS's *specific* pattern — LLM orchestrating *deterministic* physics validators via tool-use, returning only a confidence delta, never writing values — is a defensible and somewhat distinctive sub-pattern, but the broad claim "use an LLM to validate sensor readings against physics" is anticipated. **Note: the council separately concluded this component should be cut or demoted for air-gap/audit reasons anyway** — so its patentability may be moot.

### Element E — Confidence-threshold → compliance-action mapping (NCR/BLOCK_RELEASE)
**Most differentiated commercially, least covered in literature.** I found no academic work coupling a data-quality score to a *formal nonconformance/release-gate action* under ISO 9001/AS9100D/IATF 16949. This is the wedge the council identified. As prior art it's thin — but it's also more of a *business-process/workflow* feature than a technical invention, which weakens both its patentability *and* its protection from fast-followers.

---

## Classical / Canonical Prior Art (established knowledge — verify exact refs before citing)

These are the 40-year-old bodies of work the council said all five advisors missed. They are real and foundational; I'm listing them by canonical name rather than fabricating identifiers:

- **Page, E.S. (1954), "Continuous Inspection Schemes," *Biometrika* 41** — the original CUSUM. Fully anticipates Element B.
- **Chow & Willsky (1984), "Analytical redundancy and the design of robust failure detection systems," *IEEE Trans. Auto. Control*** — parity-relations FDI; anticipates the *detection* half of the pipeline.
- **Isermann, R. — *Fault-Diagnosis Systems/Applications* (2005, Springer) and survey canon** — model-based and observer-based FDI for sensors/actuators. Anticipates per-sensor fault-model structure.
- **Western Electric / Nelson run-rules (1956/1984)** — the rule-set the council's reviewer named the true incumbent: certified, embedded in QMS release workflow.
- **NAMUR NE 107 & HART device diagnostics** — *per-value device-quality StatusCodes already shipped in OPC UA telemetry.* Reviewer 5's point: plants already receive quality flags they ignore. This is prior art for "associate a quality status with each reading."
- **W3C Data Quality Vocabulary (DQV)** — formal quality-dimension taxonomy (accuracy/completeness/timeliness) for datasets; anticipates the decomposition in Element A at the dataset level.

---

## Freedom-to-Operate Preliminary Assessment

- **Broad claim — "a confidence score for sensor data" — is NOT novel.** DRI (#11), MDE entropy gates (#2), anomaly/outlier scoring (#4, #9), and NAMUR/OPC-UA per-value quality flags already occupy that space. Any filing claiming the general idea would be anticipated.
- **Broad claim — "use an LLM to detect/validate sensor faults" — is NOT novel** (crowded 2025–2026 literature, items #6–8, #13).
- **CUSUM drift detection — pure prior art** (Page 1954 + telemetry applications).
- **Narrowly-defensible elements (highest novelty):**
  1. The *specific multiplicative* trust composition `τ×δ×α` with fail-closed semantics;
  2. The coupling of a data-quality score to a *formal ISO nonconformance/release action* (Element E);
  3. Possibly the LLM-orchestrates-deterministic-physics-validators sub-pattern (Element D).
- **Overall risk:** the *engine* is largely anticipated by FDI/SPC canon; the *product* (score→compliance-action with audit trail) is the thinner-but-more-defensible novelty, and even that is more of a workflow invention than a hard technical one.

**Bottom line:** The council's prior-art suspicion is confirmed — **VERITAS is largely a reinvention of established FDI/SPC detection wrapped in a newer packaging (per-reading score + LLM + compliance gate).** True whitespace is narrow: the multiplicative fail-closed score and, more so, the *quality-score→ISO-nonconformance-action* coupling. If any IP is pursued, claim only that coupling — and expect it to be a thin method patent, not a moat.

**This is a preliminary assessment only.** A professional FTO analysis by a patent attorney — including real Google Patents/Espacenet pulls that this environment couldn't reach — is required before filing or asserting novelty.

---

## Recommendations

1. **Do not file on the formula or the anomaly-detection pipeline** — anticipated by FDI/SPC/telemetry canon.
2. **If filing anything, claim only Element E** (data-quality score → standardized nonconformance/release action with evidence trail) and treat even that as a thin method claim.
3. **Before any filing, run a real patent search** — Espacenet/Google Patents were unreachable here; the [VERIFY] items and any assignee landscape (AVEVA, Cognite, Seeq, Siemens, AspenTech, Honeywell FDI patents) were not enumerated and could contain direct anticipations.
4. **Redirect the "novelty" effort to validation, not IP** — the council's recommendation stands: the defensible asset is a *proven* score (does C(v) actually separate good/bad readings vs. the SPC layer plants already own), not a patentable algorithm.
