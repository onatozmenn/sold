# Development history

This document preserves the historical development record of **sold**. It is **not** the
current methodology. The active system is a **mechanism-aware structural econometric
prototype** described in [`README.md`](../README.md); the structural and
prediction-semantics core is **frozen** and the numerical search-approximation layer is
frozen. Several early directions below were **superseded** by the structural pivot and are
retained here only for provenance.

---

## Phase A — Pre-pivot feasibility work (SUPERSEDED)

The project originally framed the task as **label acquisition** for a supervised
asking→closing model, with a published-negotiation-margin fallback. That framing has been
**abandoned** as the active methodology. The following components were built during the
feasibility phase and are now either frozen or historical:

- **Fallback valuation engine** — `asking × (1 − published city margin)` (İstanbul ≈ 10%,
  Ankara ≈ 5%, İzmir ≈ 8%), demand-adjusted via TÜİK volumes, with a TCMB TL/m²
  cross-check. This is **no longer** the active method; it survives only as a legacy
  `RealValuator` module and the legacy `sold model` / `POST /valuate` path.
- **Two-tier ML takeover** — `RealValuator` (no labels) → `RealizedValuator` (two-stage
  hedonic + sale-to-list discount ML, once paired labels exist). **No supervised
  SaleProbability or ClosingDiscount model is trained today.**
- **Broker Data Flywheel** — a listing-outcome pipeline (`sold` / `withdrawn` / `expired`
  / `active` / `lost_to_other` / `unknown`) returning non-ML negotiation analytics
  (`POST /outcome`, `GET /analytics`). Retained as a legacy channel; it does **not** enter
  structural SMM calibration.
- **Public Label Bootstrap** — a provenance-aware `PublicLabelMiner` with per-source
  adapters (UYAP / KAP / TOKİ / project-disclosure), a provenance registry
  (`domain` / `label_source` / `sale_mechanism` / `reference_price_type`), and an unpaired
  **aggregate observation** abstraction for cohort disclosures. The registry and the three
  manually-audited Level-2 real-record cases (KAP `963554`, TOKİ Park Mavera III, UYAP
  `16766356960`) are retained; the "realized-price label" framing is superseded by
  **source-specific structural moments**.
- **Consumer direct-label acquisition path + quality gate** — a self-serve seller
  collector (`domain=consumer` · `seller_self_reported` · `ordinary_resale` ·
  `reference=asking` · confidence `B`) with an `origin` / `quality_status` / duplicate
  **fingerprint** quality gate that keeps genuine submissions separate from
  fixtures/demo/test. **Frozen** as an optional future validation channel; it does not
  enter structural SMM. Current genuine direct-label count remains **0**.

---

## Phase B — Structural econometric core (ACTIVE, FROZEN)

The pivot replaced weak-label aggregation with a **mechanism-aware structural econometric
model** (generalized Nash bargaining fit by Simulated Method of Moments). Completed
milestones:

- **Structural econometric core** — generalized Nash bargaining (`P = η·B + (1−η)·S`, `η`
  estimated, not hard-coded) fit by SMM; TCMB-anchored hedonic fair value (relative
  premiums only, no listing intercept as level); structural UYAP auctions with the
  statutory floor (`muhammen_bedel = Q`, never the reserve); KAP `log(sale/appraisal)`
  moments with a corporate mechanism shift; TOKİ cumulative-disclosure differencing.

- **Statutory-floor fix, TCMB double-count audit & identification diagnostics** —
  corrected the İİK acceptance floor to `max(0.5·Q, priority_claims) + realization_costs`;
  audited the fair-value anchor against temporal double counting (KFE ratio only when
  rolling an older anchor); added `sold structural identify` (Jacobian rank / singular
  values / condition number / weak directions / profile diagnostics).

- **Genuine structural dataset (measured, not synthetic)** — wired the validated Level-2
  records as the genuine audited seed under `validation/structural/`
  (`source_audited=true`, distinct from fixtures). Early state: 1 UYAP / 1 KAP / 1 TOKİ →
  `NOT_IDENTIFIED` (rank 2 / dim 6).

- **Source-specific Jacobian + snapshots** — per-source Jacobian ranks
  (`J_UYAP` / `J_KAP` / `J_TOKİ` / `J_combined`) restricted to each family's genuinely
  observed moments, plus a `--save-snapshot` before→after comparison.

- **Genuine PMVR3 disclosure series → TOKİ period cohorts** — two operator-audited
  consecutive Park Mavera III disclosures derived 4 valid period cohorts; the TOKİ moments
  became observed but had **no simulated counterpart** (a model-mapping gap).

- **TOKİ reclassified as `external_cross_mechanism_benchmark`** — the 5 genuine TOKİ
  cohort/composition moments are observed and available but **outside** the SMM system
  (`moments_used_in_identification = 0`). `rank(J_TOKİ)` stays 0; no primary-market
  mechanism was invented, `θ` was not shrunk to force rank.

- **Genuine audited second KAP disposal** — the source-audited KAP chain `265789→312317`
  (one Kapadık/Esenyurt disposal; `7,533,161 USD + KDV` normalized to TRY at the documented
  TCMB EVDS rate `2.0365` on `2013-10-01`; both sides excluding VAT; related-party from the
  official old-form) unlocked `kap_log_ratio_sd` → `rank(J_combined)` 2→3.

- **Genuine audited second sold UYAP auction** — e-Satış `16662608597` (Ankara/Altındağ
  dükkan; `Q = 13,000,000 TRY`; `İhale Bedeli 6,550,000 TRY`; areas preserved and never
  interchanged) unlocked `uyap_win_over_appraisal_sd` → `rank(J_combined)` 3→4.

- **UYAP outcome-taxonomy correction + `uyap_sale_prob` removed + partial-identification
  pivot** — the authenticated e-Satış interface exposes four top-level states (`Satıldı`,
  `Birinci Alıcıya Süre Verildi`, `Malın Satışının Düşmesi`, `İhale Sonucu Girilmemiştir`);
  no fifth status was invented. `uyap_sale_prob` was removed from `m_obs` / simulated
  moments / the Jacobian (documented reason: *the public UYAP outcome taxonomy does not
  currently identify a comparable negative auction trade class*). Removing the degenerate
  `sale_prob = 1.0` moment improved conditioning (condition number `1.7e17 → 61.8`).

- **Econometric terminology correction (near-fit set) + input-conflict diagnostic** — the
  near-minimum SMM criterion level set was renamed `admissible_near_fit_set` (`Θ_A`) and is
  explicitly **not** a formally estimated identified set / confidence region / coverage
  claim. Status reported as `STRUCTURALLY_UNDERIDENTIFIED` (`rank = 4`, `dim = 6`). Added
  the `ask_to_fair_value_ratio` input-conflict diagnostic with six **candidate** economic
  explanation categories (never auto-assigned; never silently clamped).

- **Final product surface** — a single-page structural valuation UI (tabs
  *Değerle / Model Evidence / Method*) and a machine-readable API (`POST /structural/valuate`,
  `GET /structural/evidence`, `GET /structural/method`) over the frozen core; no
  `confidence_interval` / `accuracy` field is ever returned.

- **Prediction-semantics correctness + Θ_A numerical-robustness audit** — fixed the
  central/within-θ inconsistency via a single deterministic **representative trading
  near-fit configuration** (so the central estimate lies inside its own interval by
  construction); made price outputs explicitly `conditional_on_trade`; reconciled
  trading/non-trading/envelope counts; corrected the trade field to a **model-implied
  Monte-Carlo `B ≥ S` share** (`not_empirically_calibrated_to_observed_uyap_no_trade_outcomes`).

- **Search-budget stability methodology audit (cumulative, incumbent-preserving)** —
  found that the old per-budget searches were **independent / non-nested**, so a larger
  budget could report a *worse* best objective purely from incumbent loss. Redesigned the
  study as a cumulative incumbent-preserving experiment (`cumulative_near_fit_experiment`):
  nested pools, common random numbers, retained global incumbent → **monotone
  non-increasing** `cumulative_best_objective`; all budgets compared under one common
  `Q_ref` / `tol_ref`; a documented **multi-part** rule →
  `near_fit_search_stability = INSUFFICIENT_COVERAGE`, reported **separately** from
  `identification_status`.

- **UYAP Evidence Expansion Batch 1 (genuine audited, +5 completed-sale auctions → 7)** —
  admitted five manually-audited UYAP completed-sale auctions (Ankara; files `2026/43`,
  `2026/89`, `2026/45` Satış and `2026/23`, `2026/263` Esas) as one batch under a
  **predeclared first-five-admissible** selection rule (auditable appraisal + auditable
  official İhale Bedeli + terminal completed-sale evidence; *not* selected on P/Q magnitude
  or direction). For every record the moment numerator is the official **İhale Bedeli**
  (never the *Ödenmesi Gereken Bedel* / deposit-adjusted / ownership-share / creditor-setoff
  / KDV-adjusted amount) over the audited appraisal `Q`. One encountered non-terminal record
  (`2026/316 Talimat`, *Birinci Alıcıya Süre Verildi*) was **excluded** to
  [`validation/structural/uyap_candidates.json`](../validation/structural/uyap_candidates.json)
  (`EXCLUDED_NON_TERMINAL`, mirroring the KAP-candidate manifest) — not admitted, not used
  in `uyap_win_over_appraisal`, not turned into a negative sale-probability observation;
  `uyap_sale_prob` is still not created. Frozen semantics preserved: still exactly four SMM
  moments, `dim(θ) = 6`, TOKİ external (0 SMM moments). Measured recompute (BEFORE → AFTER):

  | Diagnostic | Before (2) | After (7) |
  |---|---|---|
  | genuine UYAP completed-sale auctions | 2 | 7 |
  | `uyap_win_over_appraisal_mean` | 0.756923 | 0.854758 |
  | `uyap_win_over_appraisal_sd` (ddof=0) | 0.253077 | 0.163152 |
  | `kap_log_ratio_mean` / `_sd` | 0.024084 / 0.033545 | unchanged |
  | 4-moment SMM vector | 4 | 4 |
  | Jacobian rank / dim | 4 / 6 | 4 / 6 |
  | singular values | [1.760, 0.975, 0.214, 0.0285] | [1.723, 0.579, 0.207, 0.0410] |
  | condition number | 61.774 | 42.06 |
  | `identification_status` | STRUCTURALLY_UNDERIDENTIFIED | STRUCTURALLY_UNDERIDENTIFIED |
  | `J_UYAP` / `J_KAP` / `J_TOKİ` / `J_combined` rank | 2 / 2 / 0 / 4 | 2 / 2 / 0 / 4 |
  | Θ_A best objective / tolerance | 0.0763 / 0.0191 | ≈0.0 / 1e-4 |
  | Θ_A admissible count | 15 | 7 |
  | wide (weakly-constrained) params | mu_s, sigma_s, eta | mu_b, eta, auction_shift |
  | `near_fit_search_stability` | INSUFFICIENT_COVERAGE | STABLE |

  Rank stayed **4 / 6** (no forced identification; `θ` not shrunk, no moment added);
  conditioning improved; the numerical near-fit search — a diagnostic **separate from and
  not establishing** identification — re-measured to `STABLE` at the audited budgets on the
  better-fitting 7-record evidence. Full suite: **217 passed** (215 baseline + 2 Batch-1 tests).

- **UYAP Evidence Ingestion Pipeline V1 (data-acquisition subsystem)** — added
  [`src/sold/ingestion/uyap/`](../src/sold/ingestion/uyap/): a provenance-aware
  `discovery → collection → extraction → same-asset reconciliation → rule-based
  completed-sale audit → human review → explicit admission` pipeline plus a `sold uyap`
  operator CLI (`discover` / `import-artifacts` / `collect` / `extract` / `audit` /
  `review` / `admit` / `status`). It **does not touch the frozen structural core**:
  admission validates against the existing `normalize_auction` schema, writes genuine
  `uyap.json` idempotently (dedupe by `public_record_id`), and preserves the UYAP P/Q
  moment (numerator = explicit **İhale Bedeli**, never *Ödenmesi Gereken Bedel* / deposit /
  share / setoff / KDV; KDV never adjusts P or Q; `ALACAĞA MAHSUBEN` never invalidates an
  explicit İhale Bedeli). Non-terminal records → `EXCLUDED_NON_TERMINAL` (never a negative
  sale-probability observation; `uyap_sale_prob` never created). Extraction is deterministic
  (no ML / weak supervision / classifier). The browser collector (optional Playwright extra)
  operates **only** within a user-controlled, already-authenticated or public session —
  never automating e-Devlet login / MFA / CAPTCHA and never committing credentials, cookies,
  tokens, or browser profiles; a manual artifact-import fallback keeps the pipeline usable
  offline. **Live UYAP access was not available/tested in the dev environment and there is no
  official UYAP API integration**; deterministic parsers run against local fixtures and the
  automated suite needs no network. Added 20 offline regression/behavioral tests (six audited
  price-semantics cases + admission idempotency/duplication/freeze). Full suite: **237 passed**
  (217 baseline + 20 ingestion tests). Structural moments, `θ`, mechanism, SMM, `Θ_A`,
  `conditional_on_trade`, and the numerical-search convention are unchanged.
