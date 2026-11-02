# Mathematical Analysis of the VERITAS Confidence Score

**A rigorous examination of `C(v) = τ × δ × α` (and the proposed 4-factor `τ_age × τ_rhythm × δ × α`): what the formulas compute, their mathematical properties, and where the model is sound, unjustified, or broken.**

Status: analysis only. No code claims are made beyond what the implementations compute.
Date basis: code as of `veritas_core/confidence/` + `veritas_consumer.py` + `app_pipeline.py`.

---

## 0. Framing

VERITAS assigns each sensor observation a scalar trust value

```
C(v) = τ(T) · δ(n) · α(σ),   C(v) ∈ [0,1]
```

with three claimed-independent factors:

- **τ(T)** — temporal factor (age / cadence)
- **δ(n)** — sampling-density factor
- **α(σ)** — signal-stability factor

The stated intent is *fail-closed multiplicative composition*: if any factor → 0, then C(v) → 0, so a reading with any one catastrophic defect is untrusted regardless of the other two.

This document asks four questions:

1. What does each implemented factor *actually* compute (as written, not as intended)?
2. Is the multiplicative form justified — i.e., does it correspond to a coherent probability/independence model?
3. Is C(v) a *confidence* in any defensible sense (calibrated, identifiable, monotone in truth)?
4. Which properties are mathematically sound, which are merely assumed, and which are broken?

---

## 1. Notation and the implemented factors

Let a reading arrive at time `t_now` with value `x`. Define:

- `T = t_now − t_emit` — reading age (seconds)
- `Δt = t_now − t_prev` — inter-arrival gap since the sensor's previous reading
- `f*` — expected sampling rate (Hz); `p* = 1/f*` expected period
- `n` — number of samples in the current window/batch
- `σ_cur` — observed rolling std-dev of the signal window
- `σ_ref` — reference (calibrated) std-dev
- `x_prev` — previous reading's value

There are **two distinct implementations** of "τ" in the codebase, and they measure different quantities.

### 1.1 Core archetype τ (in `veritas_core/confidence/archetypes/`)

| Archetype | τ | δ | α |
|---|---|---|---|
| INERTIAL | `exp(−λ T)` | `1/(1+exp(−n/n_ref))` | `exp(−k(σ_cur−σ_ref)²)`, with hard floors `0.15` (CUSUM alarm) and `0.20` (σ_cur<0.1σ_ref) |
| CUMULATIVE | `max(0, 1−T/T_max)` | `min(1, n/n_min)` | `1` if `x ≥ x_prev` else `0` (monotonicity) |
| CYCLICAL | `\|cos(π T / T_per)\|^β` | `min(1, H(n,n_ref)/n_ref)` | `max(0, 1−σ_cur/A)` |
| BINARY | `1 if T<T_hold else 0` | `1 if n≥1 else 0` | `1 if x=x_prev else 0` |

### 1.2 Consumer τ (in `veritas_consumer.py`, the live stream path)

- `τ_c = exp(−(Δt/p* − 1)²)` — Gaussian on the inter-arrival *ratio* (cadence regularity)
- `δ_c = n/W` over a sliding deque of width `W=20`
- `α_c = exp(−|σ_cur/σ_exp − 1|)` — Laplace on the σ-ratio

### 1.3 The third function (batch, `app_pipeline.compute_confidence`)

A column-quality scorer (null-ratio, outlier-ratio, format-consistency) that reuses the τ/δ/α names but measures *dataset column health*, not per-reading trust. **It is a different mathematical object and is excluded from this analysis except as a naming collision** — it is the function the consumer's `pipeline.run()` actually feeds.

**Conclusion 0:** "C(v)" currently denotes *at least two* different functions on the live path (archetype scorer defined, batch scorer actually invoked) plus a dead inline scorer. There is no single canonical τ today; the user's decision to split τ into `τ_age` + `τ_rhythm` is therefore not a tweak — it is the first honest definition.

---

## 2. Per-factor mathematical properties

### 2.1 τ_age = exp(−λ T)  (INERTIAL / canonical freshness)

**Form:** exponential survival function. Properties:

- **Monotone decreasing** in T, τ(0)=1, τ→0 as T→∞. ✓ correct shape for "freshness."
- **Memorylessness:** `τ(T+s)/τ(T) = exp(−λ s)`, independent of T. The *relative* trust lost per additional second is constant — this is the defining property of exponential decay and is the **only** natural choice if freshness is "rate of staleness."
- **Interpretation:** `exp(−λT)` is the survival function `S(T)=P(τ_event > T)` of an exponential clock — i.e., "probability the reading is still current," which is a coherent probabilistic reading of τ.
- **Open constant:** `λ` is hand-set (`0.05` default). `exp(−λT)` has a *family* of decays; nothing identifies λ. The calibrator fits `λ = ln2 / mean_age` — i.e., sets the *median* half-life to the observed mean age. That is a reasonable one-parameter fit, but it calibrates τ to the *data's own cadence*, not to truth — see §4.

**Verdict: mathematically coherent, well-motivated, but parameter-uncalibrated to correctness.**

### 2.2 τ_rhythm = exp(−(Δt/p* − 1)²)  (consumer cadence → proposed canonical)

**Form:** a Gaussian (RBF) bump centered at `Δt = p*`. Properties:

- Peak `τ_rhythm = 1` exactly when the gap equals the expected period; symmetric penalty for arriving *early or late*.
- **This measures timing jitter, not freshness.** A sensor that is *consistently* 2× slow but perfectly regular gets `exp(−1)=0.37` every reading — it flags the *pattern*, not staleness. Distinct and useful signal.
- **Asymmetry problem:** the Gaussian on `Δt/p*` penalizes `Δt→0` (burst) as `exp(−1)=0.37`, but `Δt=2p*` also `0.37`, `Δt=3p*` `0.14`. A *dropped* reading (Δt=2p*) is scored the same as a *slightly jittery* one — the response is not sharp where drops matter.
- **Not a probability:** unlike `exp(−λT)`, `exp(−(r−1)²)` is a *similarity kernel*, not a survival function. It has no survival interpretation; it is a hand-shaped penalty. Fine as a feature, but it is not "confidence."

**Verdict: legitimate factor, but it is a kernel penalty, not a probability — mixing it multiplicatively with τ_age (which *is* a survival function) is a type mismatch. See §3.**

### 2.3 δ — sampling density

Three different shapes across archetypes:

- INERTIAL `δ = sigmoid(n/n_ref) = 1/(1+e^{−n/n_ref})`. **Problem:** `sigmoid(n/n_ref)` at `n=0` gives `0.5`, not `0` — an empty window yields density `0.5`, and it only approaches `0` for `n≪0` (impossible). The intended "no data → no trust" is broken: `δ(0)=0.5`, `δ(n_ref)≈0.73`. The sigmoid is centered at `n=0` rather than at a meaningful `n*`; it should be `sigmoid((n−n*)/s)` for some midpoint `n*`. **As written, δ never gets small** — the fail-closed guarantee fails on the density axis.
- CUMULATIVE `δ=min(1, n/n_min)` — a saturating ramp, sensible.
- CYCLICAL `δ=min(1, H(n,n_ref)/n_ref)` where `H` is the 2-element harmonic mean — odd but bounded.
- Consumer `δ=n/W` — a hard linear ramp to `W=20`, fine.

**Verdict: the INERTIAL sigmoid has a centering bug — δ(0)=0.5 means density never collapses, so "low density → low trust" does not hold for the most important archetype. This is a real defect, independent of tuning.**

### 2.4 α — signal stability

- INERTIAL `α = exp(−k(σ_cur−σ_ref)²)` — a Gaussian penalty on σ *deviation from reference*. Sensible: peaks at `σ_cur=σ_ref`.
  - **Dead-sensor floor:** `σ_cur < 0.1σ_ref ⇒ α=0.20` — a *partial* penalty, not `0`. A flatlined sensor still contributes `0.2` to the product — so C(v) is reduced but not zeroed. Fail-closed is softened here.
  - **CUSUM floor:** `alarm ⇒ α=0.15` — again partial.
- CUMULATIVE `α=1 if monotone else 0` — correct hard fail for counters.
- BINARY `α=1 if x=x_prev else 0` — penalizes a single flip, which is *wrong*: a binary sensor's legitimate job is to change state. α=0 on any transition means an honest 0→1 event scores as fully unstable. **This factor conflates oscillation with legitimate state change.**
- Consumer `α = exp(−|σ_cur/σ_exp −1|)` — Laplace on σ-ratio; heavier-tailed than Gaussian, symmetric in ratio-space, reasonable but again a kernel penalty.

**Verdict: α is the weakest factor.** INERTIAL's is reasonable but softened at exactly the failure mode that matters (dead sensor → 0.2 not 0); BINARY's is conceptually wrong (penalizes real state changes); the CUSUM hook is good but partial.

---

## 3. Is the multiplicative form justified?

### 3.1 When multiplication *is* right

If `τ, δ, α` were **independent probabilities** — `τ=P(still fresh)`, `δ=P(sufficiently sampled)`, `α=P(signal healthy)` — then

```
C(v) = τ·δ·α = P(fresh ∧ sampled ∧ healthy)
```

is the probability that *all three* hold, under conditional independence. That is exactly the "AND-gate / fail-closed" semantics the design intends, and `exp(−λT)` does carry a survival-probability reading (§2.1). **As a product of independent survival/latent probabilities, multiplication is the defensible choice — and strictly better than summation** (which would let one good factor compensate a catastrophic one).

### 3.2 Why the current implementation does *not* realize that model

Three problems break the independence/probability reading:

1. **The factors are not independent.** A dead/stuck sensor produces *both* low σ (α low) *and* abnormal Δt (τ_rhythm low) *and* — if it stops emitting — low n (δ low). Correlated failure modes mean the product *over-penalizes* (it multiplies probabilities that are not independent, so joint probability is mis-estimated). Under dependence, `P(A∧B∧C) ≠ P(A)P(B)P(C)`.
2. **The factors are not all probabilities.** `τ_rhythm` and `α` are similarity kernels (arbitrary penalty shape), `δ` (INERTIAL) is a miscentered sigmoid that floors at 0.5, `α`'s dead-sensor branch is a hand-picked `0.20`. These are not calibrated probabilities; their product is not a probability.
3. **Identifiability collapse.** Given only C(v), infinitely many `(τ,δ,α)` triples produce the same product — the score is *not identifiable* to a single defect cause. The consumer already works around this by taking `argmin` of the three for the anomaly label — an admission that the scalar alone cannot attribute cause.

### 3.3 The 4-factor extension `τ_age × τ_rhythm × δ × α`

Splitting τ into freshness + cadence is *conceptually* correct — they are different failure modes. But it **worsens the identifiability and dependence problems**: now four correlated, non-probabilistic factors multiply. The product remains a sensible *engineering score* (fail-closed, interpretable factors) but drifts further from any probabilistic reading. To keep it defensible you must either:

- **(a)** calibrate the product empirically (fit a logistic/isotonic mapping `C(v) → P(correct)` against ground truth), or
- **(b)** give up the "C(v) is a probability" claim and treat it as an *index* — in which case thresholds (ISO 0.70/0.75) are pure policy, not confidence levels.

**Verdict on form:** multiplicative = right *shape* for fail-closed semantics; but the current factors are not independent probabilities, so **C(v) is a heuristic trust index, not a calibrated confidence.** Calling it "confidence" overclaims; calling it a "quality index" is honest.

---

## 4. The calibration / identifiability problem (the real gap)

For `C(v)` to be a *confidence*, one needs `P(correct | C(v)=c)` to be increasing in `c` — i.e., the score must at least *rank-order* correctness. Nothing in the formulas guarantees this:

- All constants (`λ, k, σ_ref, n_ref, T_max, floors`) are **hand-set or self-calibrated to the data's own statistics** — never fit to *labeled correctness*. The calibrator tunes `σ_ref`, `λ`, `n_ref` from the signal itself; it answers "what is normal for this sensor," not "when is this sensor wrong."
- **Ground-truth scarcity** (the Contrarian's point, partially answered by reviewers): real labels *do* exist — ISO calibration/maintenance records and OPC UA/NE107 device-reported quality flags are latent supervision signals the current math ignores entirely.
- **The correct validation** is therefore: does `C(v)` separate known-good from known-faulty readings better than (a) naive staleness, (b) standard SPC/Western-Electric rules, (c) the device-reported flags themselves? Until that number exists, the formula is an *unvalidated hypothesis*, not a metric.

---

## 5. Sound vs. assumed vs. broken — summary table

| Component | Status | Why |
|---|---|---|
| Multiplicative fail-closed form | **Sound** | Correct semantics for "any defect ⇒ distrust"; strictly better than additive |
| `τ_age = exp(−λT)` | **Sound, uncalibrated** | Only natural freshness decay (memoryless survival); λ fit to cadence not truth |
| `τ_rhythm = exp(−(Δt/p*−1)²)` | **Useful, not a probability** | Kernel penalty on cadence; distinct signal; type-mismatched with τ_age |
| `δ` (INERTIAL sigmoid) | **Broken** | `δ(0)=0.5` — density never collapses; fail-closed fails on the data-starvation axis |
| `δ` (CUMULATIVE/consumer ramps) | **Sound** | Reasonable saturating ramps |
| `α` (INERTIAL Gaussian σ-dev) | **Sound but softened** | Good shape; dead-sensor floor `0.20` keeps corpse-certification possible |
| `α` (BINARY `x=x_prev`) | **Broken** | Penalizes legitimate state changes as "unstable" |
| `α` CUSUM floor `0.15` | **Sound, partial** | Right hook, but doesn't zero the score |
| Independence of factors | **Assumed, false** | Correlated failure modes ⇒ product mis-estimates joint |
| `C(v)` = a calibrated confidence | **Unsupported** | Constants fit to signal statistics, never to labeled correctness |
| Identifiability (score→cause) | **Broken by design** | Many (τ,δ,α) map to same C(v); argmin attribution is a workaround |

---

## 6. Mathematical verdict

1. **The architecture (multiplicative, fail-closed, factorized) is sound engineering** and matches the intended "AND of survival conditions" semantics.
2. **Two real defects**, independent of tuning: the **INERTIAL δ centering bug** (`δ(0)=0.5`) and the **BINARY α state-change penalty**. Both are cheap to fix.
3. **The central unsoundness is conceptual, not algebraic:** `C(v)` is presented as a *confidence* but is implemented as an *uncalibrated heuristic index*. The factors are not independent probabilities, so their product is not a probability — and no constant is fit to ground truth.
4. **The 4-factor split (τ_age + τ_rhythm) is the right decomposition** and worth adopting — but it makes the "confidence" claim *harder* to defend, not easier, unless followed by empirical calibration `C(v) → P(correct)`.
5. **Path to a defensible metric:** (a) fix δ and BINARY α; (b) treat the product as a *trust index* or calibrate it to a probability via isotonic/logistic fit on labeled faults; (c) benchmark against standard SPC + the device-reported quality flags already in the channel — that comparison, not the formula, is what makes it a metric worth money.

**Bottom line:** The formula is a reasonable *trust index* with two fixable bugs and one honest mislabel (it is not a confidence). Its value is decided by the falsification benchmark against ground truth and SPC — not by the algebra, which is already serviceable once δ and α are corrected.
