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

- **UYAP Live Browser Pilot 1 (non-mutating verification; outcome = `NOT_RUN`)** — added
  [`src/sold/ingestion/uyap/pilot.py`](../src/sold/ingestion/uyap/pilot.py) and a
  `sold uyap pilot` command that attempt, through a **user-controlled** Chrome/CDP session,
  to collect the already-admitted record **2026/263 Esas** and reproduce its manually-audited
  truth (`appraisal 6,800,000` / `İhale Bedeli 5,715,000` / `P/Q 0.8404411764705882` /
  `ADMISSIBLE_COMPLETED_SALE`). The known truth is a **verification target only** — never
  injected into extraction. The pilot is **non-mutating**: it never admits, a mutation guard
  fingerprints `uyap.json` (sha256 + count + four-moment SMM vector) before/after, the genuine
  UYAP count stays **7**, and no eighth observation is created. `BrowserCollector` gained a
  minimal live `collect_record` (real-DOM document-link discovery via `discover_document_links`;
  unsupported `javascript:`/popup/PDF patterns reported honestly, never fabricated). **Measured
  outcome in the dev environment: `NOT_RUN`** (`browser_connection_status = playwright_missing`,
  `live_page_reached = false`) — no user-controlled UYAP session was reachable, so no live run
  occurred; the operator-side live run is still pending. Added 12 offline pilot tests (verification
  layer, appraisal/İhale/P-Q/decision mismatch reporting, mutation guard, NOT_RUN semantics,
  “offline fixture is not a live PASS”, no eighth observation, structural freeze). Full suite:
  **249 passed** (237 baseline + 12 pilot tests). Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status, and the numerical-search convention
  are unchanged.

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 1 (measured first live result: `FAIL`)** —
  the operator executed the pilot against a **real** Chrome/CDP session and the **real** UYAP
  `2026/263` page (`esatis.uyap.gov.tr/pp/index.jsp?...kayitId=16737826545`). Measured pre-fix
  live signature: `mode=live`, `browser_connection_status=connected`, `live_page_reached=true`,
  `extracted_appraisal=6800000`, `terminal_evidence=satildi`, but `artifact_types_collected=['status_card']`,
  `document_access_patterns=[]`, `extracted_auction_price=null`, `kdv_rate=null`,
  `reconciliation_status=ambiguous`, `audit_decision=MISSING_AUCTION_PRICE`, `pilot_outcome=FAIL`.
  The mutation guard passed (identical `uyap.json` sha256, genuine count `7 -> 7`, SMM unchanged),
  so this was a **collection/parser interoperability** defect, not a structural/admission defect.
  **Root cause:** UYAP exposes documents through an “İhale Evrak Listesi” **button → modal →
  row-local view/eye-action** UI; the anchor-only `discover_document_links` collected nothing from it,
  so the official *Artırma Sonuç Tutanağı* (explicit `İhale Bedeli 5.715.000`) was never collected.
  **Fix 1 (narrow):** a live `collect_record` modal path (text/role-based control detection,
  `select_row_document_actions` row-local association — never a global Nth-eye, bounded
  popup/new-tab/same-page/PDF/download event handling classified by `classify_access_pattern`);
  token-based `classify_document_label` (incl. `BLR_BILIRKISI_RAPORU`, `Artırma Sonuç / Uzatma
  Tutanağı`); KDV parsing for separated `KDV Oranı : %20` nodes; both-ordering asset-identifier
  extraction (`50984 Ada, 1 Parsel` / `Ada 50984, Parsel 1` / `12. Kat` / `60 Nolu B.B.`); shared
  `asset_descriptors` used by extraction and reconciliation; and privacy-safe report diagnostics
  (`document_list_control_found`, `document_modal_opened`, `document_labels_observed`,
  `document_collection_attempts`, …). Result-card `Satış Tutarı` is still **not** substituted for the
  explicit İhale Bedeli. Added 13 offline tests (reproducing the FAIL signature and validating the
  fixed ADMISSIBLE/reconciled path, modal logic, KDV, identifiers, non-mutation, freeze). Full suite:
  **262 passed** (249 baseline + 13 tests). **No live `PASS` is claimed** — the operator rerun against
  real UYAP is still required to establish a live `PASS`. Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status, and the numerical-search convention are unchanged.

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 2 (measured second live result: `FAIL`)** —
  chronology: first real live pilot = **FAIL** → Fix 1 → second real live pilot = **FAIL** → Fix 2 →
  operator rerun pending. On the second real run against the same real UYAP `2026/263` page, Fix 1
  **improved KDV** (`kdv_rate: null -> 20.0`) and **found** the “İhale Evrak Listesi” control
  (`document_list_control_found=true`, `kind=link`), but then **wrongly waited for a modal/dialog**
  (`document_modal_opened=false`, `document_labels_observed=[]`, `document_actions_observed=0`), so the
  official *Artırma Sonuç Tutanağı* was still not collected → `extracted_auction_price=null`,
  `audit_decision=MISSING_AUCTION_PRICE`, `pilot_outcome=FAIL`. The mutation guard passed again
  (`uyap.json` unchanged, count `7 -> 7`, SMM unchanged). The operator then **directly observed** the real
  UI: “İhale Evrak Listesi” is a **same-page tab/panel** (no modal); the document list renders inside the
  detail page; and the row-local **eye action** for *Artırma Sonuç / Uzatma Tutanağı* opens a **new UYAP
  tab** at `viewer.jsp?mimeType=Udf&evrakId=...` (“Evrak Görüntüleme”) rendering the official UDF, whose
  visible content includes `İhale Bedeli: 5.715.000,00` and `ALACAĞA MAHSUBEN`. **Fix 2 (narrow):**
  first-class same-page document-panel detection (`classify_document_list_container` → `same_page_tab_panel`;
  modal/dialog only as fallback), testable panel row extraction (`extract_panel_document_rows` /
  `panel_has_documents`), row-local eye association (never a global Nth-eye), new-tab detection via the
  browser-context `pages` delta, UDF viewer URL/mime classification (`classify_viewer_url`,
  `viewer_mime_hint`), viewer-representation classification (`classify_viewer_representation`:
  dom_text / iframe / embed_object / canvas_image_only / unknown), a new access-pattern name
  (`same_page_tab_new_tab_udf_viewer`), and privacy-safe report diagnostics (`document_list_opened`,
  `document_list_container_kind`, `viewer_pages_opened`, per-attempt viewer counts). The collector
  captures the UDF **only** from a deterministically accessible source (DOM/iframe text); if the viewer
  proves canvas/image-only it is reported unsupported — **no OCR, no fabrication, no injected truth**.
  Result-card `Satış Tutarı` is still not substituted for the explicit İhale Bedeli. Added 15 offline
  tests. Full suite: **277 passed** (262 baseline + 15 tests). **No live `PASS` is claimed and the UDF
  viewer content representation is not asserted** until a runtime operator rerun observes it. Structural
  core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status, and the numerical-search
  convention are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 3 (measured third live result: `FAIL`)** —
  chronology: first real live pilot = **FAIL** → Fix 1 → second real live pilot = **FAIL** → Fix 2 →
  third real live pilot = **FAIL** → Fix 3 → operator rerun pending. On the third real run the operator
  launched from the **search/listing page** (`esatis.uyap.gov.tr/pp/index.jsp`, one tab). Fix 2's
  document-entry logic used a **global page-level text locator** (`get_by_text("evrak listesi")`) that was
  **not scoped to the `2026/263` record card**; a generic text node is not an actionable control, so the
  click **timed out** (`document_list_control_found=true`, `kind=text`, then Timeout 5000ms;
  `document_list_opened=false`, `document_labels_observed=[]`, `document_actions_observed=0`), yielding
  `audit_decision=MISSING_APPRAISAL`, `pilot_outcome=FAIL`. The mutation guard passed again (`uyap.json`
  unchanged, count `7 -> 7`, SMM unchanged). The operator also confirmed **two genuine entry paths** to the
  same documents: **(A)** on the **listing card** ("Ankara … 2026/263 İcra") the card-local "İhale Evrak
  Listesi" opens a **modal/overlay**; **(B)** "İncele" opens a **detail page** whose "İhale Evrak Listesi"
  **tab** renders a **same-page panel** (Fix 2). Both paths reach the same document rows → row-local eye →
  new-tab UDF viewer. **Fix 3 (narrow):** a page-state classifier (`classify_page_state` →
  `search_listing` / `record_detail` / `udf_viewer` / `unknown`, by content semantics not just URL);
  **target-record-card matching by official file identity** (`find_target_record_card` +
  `normalize_file_identity` + `file_identity_matches` with an Esas/İcra listing alias) that is **never**
  chosen by price and **never** a first/Nth card, guarded so a whole-list container (multiple file numbers)
  is not mistaken for a card; an **actionable card-local control** requirement (`card_document_list_control`
  returns `actionable=true` only for a button/link, else `non_actionable_text_only`); entry-path derivation
  (`classify_document_entry_path` → `listing_card_modal` / `detail_tab_panel`); a **row-local eye vs
  download-arrow** distinction (`_is_download_action` + `_DOWNLOAD_TOKENS`; downloads are skipped so the
  eye/view action is used); and a page-state-aware live collector (`_collect_documents(page, context,
  target_file_id, target_institution)`) that scopes the click to the target card in the live DOM
  (`_locate_card_control`, not a global locator) and converges **both** paths onto one shared document-row
  collector (`_collect_from_container`) → new-tab UDF viewer. New report diagnostics: `page_state`,
  `document_entry_path`, `target_record_card_found`, `target_record_card_match_fields`,
  `target_record_card_file_text`, `target_record_card_control_labels`. The collector still captures the UDF
  **only** from a deterministically accessible source — **no OCR, no fabrication, no injected truth**, and
  result-card `Satış Tutarı` is never substituted for the explicit İhale Bedeli. Added 27 offline tests.
  Full suite: **304 passed** (277 baseline + 27 tests). **No live `PASS` is claimed and the UDF viewer
  content representation is not asserted** until a runtime operator rerun observes it. Structural core, four
  SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status, and the numerical-search convention are
  unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 4 (measured fourth live result: `FAIL`)** —
  chronology: run 1 = **FAIL** → Fix 1 → run 2 = **FAIL** → Fix 2 → run 3 = **FAIL** → Fix 3 → run 4 =
  **FAIL** → Fix 4 → operator rerun pending. On the fourth real run the operator did everything right:
  a single Chrome/CDP session, the real UYAP **search/listing page** (active source ref
  `https://esatis.uyap.gov.tr/pp/index.jsp`), the `2026/263 İcra` result card visible, no modal or detail
  page manually opened. The real terminal evidence `Satış İşlemleri Tamamlandı` was even extracted from the
  listing content. **But `classify_page_state(...)` returned `udf_viewer`** — because its top-priority rule
  treated a weak viewer reference in the *raw HTML* (a hidden "Evrak Görüntüleme" string / a
  `viewer.jsp?mimeType=Udf` fragment inside script/template markup) as decisive, overriding the strong
  visible listing semantics of the active page. That yielded `document_entry_path = unsupported`
  (`document_collection_attempts=[{stage:"page_state", blocking_reason:"unsupported_page_state:udf_viewer"}]`),
  so the **Fix-3 target-record-card logic never executed**. The mutation guard passed again (`uyap.json`
  unchanged, count `7 -> 7`, SMM unchanged, `uyap_sale_prob` absent). Run 4 therefore did **not** disprove
  the Fix-3 card logic — it never reached it; the defect was a **page-state false positive**. **Fix 4
  (narrow):** a deterministic **active-page state evidence precedence** (`_page_state_evidence` /
  `page_state_evidence`) — (1) strong active-**URL** viewer evidence via `_active_viewer_url`, which parses
  only the live `page.url` path/query (`/pp/viewer.jsp` + `mimeType=Udf`), never URLs embedded in HTML;
  (2) strong visible detail semantics → `record_detail`; (3) strong visible listing semantics
  (`\bincele\b` + "evrak listesi") → `search_listing`; (4) viewer semantics (`Evrak Görüntüleme`) only when
  no listing/detail is present → `udf_viewer`; else `unknown`. A raw-HTML `viewer.jsp` / `mimeType=Udf`
  reference is recorded as `weak_embedded_viewer_reference_ignored` and is **never** decisive. Multi-tab
  safety adds a pure `select_target_page_index` (+ live `_select_target_page`) that prefers a supported
  target page (`search_listing`/`record_detail` + file-identity match) over a stale UDF viewer tab, never
  selecting a viewer merely because it is a UYAP page. New privacy-safe report diagnostics: `page_state_evidence`,
  `page_candidates_seen`, `selected_page_url_kind`, `selected_page_state`,
  `selected_page_target_identity_match` (no raw HTML/URLs, no cookies/tokens/personal data). Fix-3
  target-card matching (file identity, Esas/İcra alias, no price/Nth selection) and both entry paths are
  **preserved unchanged**; after classifier repair, `search_listing` again flows into the Fix-3 listing-card
  path. Added 28 offline tests. Full suite: **332 passed** (304 baseline + 28 tests). **No live `PASS` is
  claimed**, the listing modal is **not** asserted to open programmatically, and the UDF viewer is **not**
  asserted to be programmatically reached — a post-Fix-4 operator rerun is required. Structural core, four
  SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the
  numerical-search convention are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 5 (measured fifth live result: `FAIL`)** —
  chronology: run 1 = **FAIL** → Fix 1 → run 2 = **FAIL** → Fix 2 → run 3 = **FAIL** → Fix 3 → run 4 =
  **FAIL** → Fix 4 → run 5 = **FAIL** → Fix 5 → operator rerun pending. Run 5 proved live that everything
  up to and including the click works: `page_state = search_listing`, the `2026/263` card was found by
  `file_id` + `institution`, `document_entry_path = listing_card_modal`, the card-local "İhale Evrak
  Listesi" control was found (`document_list_control_kind = card_link`), and the click succeeded. The
  operator **directly watched the real Chrome window during the collector run and confirmed the UYAP "İhale
  Evrak Listesi" modal visibly opened** (dimmed background, foreground overlay, document rows: *Satış İlanı*,
  *Belediye İmar Durumu*, *Satış Şartnamesi Ve Tutanağı*, *BİLİRKİŞİ RAPORU 2026 263 ESAS.udf*, *Artırma
  Sonuç / Uzatma Tutanağı*, each with a download and an eye control). Yet the collector reported
  `document_list_opened = false`, `document_modal_opened = false`, `document_labels_observed = []`,
  `document_actions_observed = 0`, `blocking_reason = "document list did not become visible after control
  click"`. The mutation guard passed again (`uyap.json` unchanged, count `7 -> 7`, SMM unchanged). The exact
  defect was a **visible-modal / document-list recognition false negative**: detection required `.modal` /
  `role=dialog` / `<tr>` markup, but the real overlay is a div/portal, and run-5 diagnostics also showed
  mojibake-like label text. **Fix 5 (narrow, recognition only):** detect the open list from **visible
  semantic content** — `detect_document_list` requires a document-list title **and ≥2 distinct recognized
  document types** (never the title alone); `detect_document_container` locates the **nearest common
  ancestor** of the recognized visible label elements, guarded so `html`/`body`/`[document]` and any
  whole-multi-record results container (`spans_multiple_records`) can never be chosen and **without**
  requiring `.modal`/`role=dialog` (`_container_strategy` reports `semantic_dialog` / `semantic_modal_class`
  / `semantic_common_ancestor`); `extract_document_rows_semantic` anchors **document rows on the label
  elements** (no `<tr>`/`li`/`class*=row` assumption, hidden templates ignored via `_is_hidden`) and reuses
  the unified DocumentRow abstraction; `resolve_row_view_action` distinguishes a **row-local download vs
  eye/view action** and returns `resolved=False` (never clicking arbitrarily) when a row is download-only or
  ambiguous; `document_list_semantic_transition` tracks a **pre-click→post-click** change over *visible*
  labels; and a constrained `_demojibake` (UTF-8-as-Latin-1/cp1252) repairs `İhale`/`Satış`/`Bilirkişi`/
  `Artırma` for classification input only, guarded so correct Turkish Unicode is untouched and source
  artifacts are never mutated. On success the live collector sets `document_list_opened = true`,
  `document_list_container_kind = listing_modal` (from the entry path via `document_container_kind_for_entry`,
  not CSS), populates labels/actions, and flows into the existing shared collection → new-tab UDF viewer
  (Fix-2 handling unchanged). New privacy-safe diagnostics: `pre_click_visible_document_types`,
  `post_click_visible_document_types`, `document_list_semantic_transition_detected`,
  `document_container_strategy`, `document_container_recognized_types`, `document_row_detection_strategy`,
  `recognized_document_rows` (per row: `artifact_type`, `normalized_label`, `action_count`,
  `view_action_resolved`, `download_action_detected` — no source text, no personal data). Collection
  priority (auction_result > appraisal_report > sale_notice > sale_spec) is preserved; `Belediye İmar
  Durumu` is not collected; result-card `Satış Tutarı` is never substituted for the explicit İhale Bedeli.
  Added 31 offline tests. Full suite: **363 passed** (332 baseline + 31 tests). **No live `PASS` is claimed**,
  the modal is **not** asserted to open programmatically in a live run, and **UDF source extraction is not
  proven** — a post-Fix-5 operator rerun is required. Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search
  convention are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 6 (measured sixth live result: `FAIL`)** —
  chronology: runs 1–5 = **FAIL** → Fixes 1–5 → run 6 = **FAIL** → Fix 6 → operator rerun pending. Run 6
  **proved Fix 5 live**: `page_state=search_listing`, target `2026/263` card matched on `file_id`+
  `institution`, `document_entry_path=listing_card_modal`, `document_list_opened=true`,
  `document_modal_opened=true`, `document_list_container_kind=listing_modal`, the four real evidence rows
  recognized (`sale_notice`, `sale_spec`, `appraisal_report`, `auction_result`), and
  `document_actions_observed=8` (each row `action_count=2`). **But every row reported
  `view_action_resolved=false` and `download_action_detected=false`**, so all four collection attempts
  stopped at `row_action_unresolved:no_view_action`, no view control was clicked, `viewer_pages_opened=0`,
  `audit_decision=MISSING_APPRAISAL`, `pilot_outcome=FAIL`. The mutation guard passed again (`uyap.json`
  unchanged, count `7 -> 7`, SMM unchanged). The operator has directly observed that each row carries two
  right-side **icon-only** controls (a red download/down-arrow and a separate eye/view) and that the eye
  opens `/pp/viewer.jsp?mimeType=Udf&evrakId=...`. The exact defect was **row-local icon-control
  semantics**: the resolver inspected only each control's own text/class, never its descendant icon
  metadata. **Fix 6 (row-local action semantics only; no page-state/card/modal/document-row/structural
  change):** each recognized document row's clickable controls are introspected with nesting flattened
  (`_row_action_elements` — `<a><i></i></a>` is one action, the `<i>` is icon metadata; icon-only `<i>`/`svg`
  are clickables when no anchor/button/onclick exists) into a rich, privacy-safe spec (`_action_spec`):
  tag, role, accessible name, `title`/`aria-label`, `href_kind`, `download` attribute, `onclick` presence,
  safe class tokens, descendant icon tokens (own + descendant class tokens, SVG `<title>` text,
  `<use href="#…">` fragment, descendant `title`/`aria-label`/`alt`), and safe handler tokens (from the
  `onclick` body and href **path** only — never the query/opaque `evrakId`). A deterministic
  `classify_action_semantic` returns `download`/`view`/`ambiguous`/`unknown` by precedence
  (accessibility/title → descendant icon tokens → href/`download` attribute → onclick/handler tokens →
  else `unknown`); generic icon families are supported (download: `download`/`arrow-down`/`file-download`/
  `cloud-download`/`indir`/`kaydet`; view: `eye`/`view`/`visibility`/`preview`/`goruntule`/`goster`/`incele`/
  `onizleme`, incl. `fa-*`/`glyphicon-*`/`mdi-*`/`bi-*` token variants) without assuming the real UYAP icon
  framework in advance. `resolve_row_view_action` now resolves a row **only** when exactly one action is
  positively classified `view` — there is **no** "the other is download, so this is view" inference, **no**
  positional/right-most/Nth-action guessing (the earlier weak `single_non_download_beside_download` rule was
  removed), and **color is never a classifier** (the observed red is ignored). The live click uses
  `_locate_row_view_action`, which builds discriminative selectors **only** from the resolved view spec
  (accessible name / view icon token / viewer href) — never a global Nth-eye — then flows into the existing
  Fix-2/Fix-3 new-tab UDF viewer handling. New privacy-safe report diagnostics: `action_resolution_strategy`
  and `recognized_document_rows[].action_summaries` (per action: `local_index`, `tag`, `role`,
  `accessible_name_present`/short name, `title`, `aria_label`, `href_kind`, `download_attribute_present`,
  `onclick_present`, `safe_class_tokens`, `descendant_icon_tokens`, `semantic_candidates`,
  `resolved_semantic`) — with **no** raw hrefs, opaque `evrakId` values, `onclick` bodies, cookies/tokens,
  or DOM dumps, so an unresolved live run stays debuggable. Result-card `Satış Tutarı` is still never
  substituted for the explicit İhale Bedeli. Added 29 offline tests. Full suite: **392 passed** (363
  baseline + 29 tests). **No live `PASS` is claimed, the viewer stage was not exercised live, and UDF source
  extraction / İhale Bedeli extraction remain unproven** — a post-Fix-6 operator rerun is required.
  Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0
  SMM), and the numerical-search convention are unchanged; the pilot remains non-mutating (genuine count
  stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 6.1 (additional real viewer observation;
  viewer download-required same-row fallback)** — recorded as a **direct live viewer observation**, not a
  formal seventh pilot rerun. Between Fix 6 and the post-Fix-6 rerun the operator manually clicked a
  row-local eye/view action: a real UYAP viewer tab opened at `/pp/viewer.jsp?mimeType=Udf&evrakId=...`, but
  the document did **not** render — the viewer visibly displayed **"Evrak Görüntülenemedi, Evrağı indirerek
  Görüntüleyebilirsiniz."**. So reaching the viewer does not guarantee content availability; a positively
  resolved view action has at least two real outcomes (content available, or download required). **Fix 6.1
  (narrow follow-up; Fix-6 action architecture reused, not reopened):** a deterministic viewer-outcome
  classifier `classify_viewer_outcome(text, representation) → content_available / download_required /
  unsupported_representation / viewer_error / unknown`, where `download_required` requires the **combined**
  viewer-failure + download-instruction semantics via `viewer_download_instruction_detected` (Turkish fold +
  constrained mojibake repair; a bare `indir`, a generic "program indir" instruction, a failure message
  without the download instruction, or the instruction without the failure are all **not** sufficient).
  `resolve_row_view_action` now also exposes the row's **positively resolved single** download action
  (`download_action`, `download_action_resolved`) reusing the Fix-6 `classify_action_semantic` — never a
  positional/Nth/"the other one" inference. The live collector branches on the viewer outcome: on
  `content_available` it uses the existing viewer-backed source collection; on `download_required` it closes
  the failed viewer tab (preserving the operator's original listing/detail page), then falls back to the
  **same** `DocumentRow`'s resolved download action via `_locate_row_download_action` (selectors derived
  only from the resolved download spec — never a global/first/Nth/other-row download; if the row has no
  positively resolved download action it reports `download_required_but_download_action_unresolved` rather
  than clicking arbitrarily), captures the official artifact with a bounded `page.expect_download`, and
  preserves it in the existing gitignored artifact store (type + extension + size + short sha). View-first
  is preserved — documents are not downloaded unless the viewer says so. Deterministic extraction runs
  **only** for genuinely supported formats (`extraction_supported_for` / `EXTRACTABLE_ARTIFACT_EXTENSIONS`
  = `.txt`/`.html`/`.htm`); a raw `.udf`/`.pdf` download is preserved but reported unsupported
  (`downloaded_artifact_extraction_supported=false`) and is **not** fed to the extractor (no garbage/UDF
  fabrication) — a `mimeType=Udf` URL hint alone never implies support, and **no** raw-UDF parser, OCR, or
  known-truth injection is added (this task deliberately does not build a speculative UDF parser). New
  privacy-safe per-attempt diagnostics: `viewer_outcome`, `viewer_download_instruction_detected`,
  `download_fallback_attempted`, `download_fallback_resolved_same_row`, `download_action_resolved`,
  `download_event_detected`, `downloaded_artifact_extension`/`_mime_hint`/`_size`/`_sha256`,
  `downloaded_artifact_collected`, `downloaded_artifact_extraction_supported`,
  `download_fallback_blocking_reason` — with **no** opaque `evrakId`, full URLs, cookies/tokens, onclick
  bodies, or document text. The auction numerator remains the explicit İhale Bedeli (result-card `Satış
  Tutarı` / `Ödenmesi Gereken Bedel` / deposit / share / setoff / KDV never substituted); if a downloaded
  official artifact cannot yet be parsed, the official auction price simply stays missing. Added 22 offline
  tests. Full suite: **414 passed** (392 baseline + 22 tests). **No live `PASS` is claimed: the same-row
  download fallback has not been exercised live, no official UDF download has been captured live, raw-UDF
  parsing is not proven, and the İhale Bedeli was not programmatically extracted** — a post-Fix-6.1 operator
  rerun is required. Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status
  (5 observed / 0 SMM), and the numerical-search convention are unchanged; the pilot remains non-mutating
  (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 7 (measured seventh live result: `FAIL`)** —
  chronology: runs 1–6 = **FAIL** → Fixes 1–6 → Fix 6.1 → run 7 = **FAIL** → Fix 7 → operator rerun
  pending. Run 7 **live-proved** Fix 5's modal/document-row detection and, newly, **Fix 6's download
  semantics**: `document_list_opened=true`, `document_modal_opened=true`, `document_list_container_kind=
  listing_modal`, the four rows recognized, `document_actions_observed=8`, and for every row the real
  `fa-arrow-down` icon was positively classified `download` (`download_action_detected=true`,
  `download_action_resolved=true`). **But every row still reported `view_action_resolved=false` /
  `row_action_unresolved:no_view_action` and `viewer_pages_opened=0`.** The Run-7 action summaries showed
  **both** reported controls as bare `<i>` icons — the document icon (`icon-docs …`) and the `fa-arrow-down`
  download arrow — with **no** eye/view control in the report. The mutation guard passed again (`uyap.json`
  unchanged, count `7 -> 7`, SMM unchanged). Root cause: the logical **document-row boundary was too
  narrow** and action introspection was operating on **icon descendants** inside the label/download control
  rather than the owning **actionable controls**; the separate eye/view control is a sibling **outside** the
  selected semantic-label ancestor. **Fix 7 (row boundary / actionable-control ownership only; no
  page-state/card/modal/label/viewer/fallback/structural change):** `_semantic_row_for_label` now builds the
  single-recognized-label ancestor chain (stopping before any ancestor that spans two document identities)
  and selects the **smallest ancestor that contains actual actionable controls** via a new
  `_row_actionable_controls` (`button`/`a`/`[role=button]`/`[onclick]`, nesting-flattened, **excluding** bare
  `<i>`/`svg` descendants), with a constrained **actionable-sibling expansion** (`row_boundary_strategy` =
  `label_actionable_ancestor` / `actionable_sibling_expansion` / `icon_only_ancestor` / `unresolved`) so the
  adjacent eye/view button is captured without climbing into another row, `body`/`html`, or the whole modal
  (guarded by `logical_row_recognized_type_count == 1`). An icon (`<i>`/SVG/`<use>`) is metadata of its
  owning actionable control, never its own `ActionSpec`: two icons inside one button yield **one** actionable
  control (with `fa-arrow-down` in its `icon_tokens`/`descendant_icon_tokens`), one download button + one view
  button yield **two**, and `action_count` counts actionable controls (not icons/`<i>`/SVG/tokens). Fix-6's
  positive precedence and Fix-6.1's same-row download fallback are reused unchanged — the corrected row now
  exposes **both** the view and the row's **own** positively-resolved download action; an unknown/positional/
  right-most sibling is never inferred as view. A separate non-clean rerun also showed a **pre-opened/stale
  document list** already visible before the entry click (`pre_click_visible_document_types` already the four
  types, then the card-local control click timed out); Fix 7 adds a narrow guard
  `preopened_document_list_reusable(html, url, target_file_id)` that, **before** clicking, reuses an
  already-open **valid** list only when the page is a supported doc-entry state, the target file identity is
  visible (candidate scoping), and the strict `detect_document_list` container holds (hidden templates /
  raw-HTML-only labels never qualify via the existing visibility/container guards), setting
  `document_entry_state = preopened_document_list_reused` and flowing straight into the Fix-7 row-boundary
  collection (no pre-click→post-click transition required when the list was already open). New privacy-safe
  diagnostics: `row_boundary_strategy`, `logical_row_ancestor_kind`, `logical_row_recognized_type_count`,
  `logical_row_actionable_control_count`, `actionable_control_tags`, `document_entry_state`. Result-card
  `Satış Tutarı` is still never substituted for the explicit İhale Bedeli, and known truth remains
  verifier-only. Added 26 offline tests. Full suite: **440 passed** (414 baseline + 26 tests). **No live
  `PASS` is claimed: eye/view semantics have not been resolved live, the viewer has not been reached
  programmatically, the Fix-6.1 download-required fallback has not run live, no official UDF has downloaded
  live, raw-UDF extraction is not proven, and the İhale Bedeli was not extracted** — a post-Fix-7 operator
  rerun is required. Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status
  (5 observed / 0 SMM), and the numerical-search convention are unchanged; the pilot remains non-mutating
  (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 8 (measured eighth live result: `FAIL`)** —
  chronology: runs 1–7 = **FAIL** → Fixes 1–7 → run 8 = **FAIL** → Fix 8 → operator rerun pending. Run 8
  **live-proved Fix 7 end-to-end to the viewer**: for `auction_result` the corrected logical row exposed two
  real `button` controls (`row_boundary_strategy=label_actionable_ancestor`, `actionable_control_tags=
  ["button","button"]`), Fix 6 resolved `fa-arrow-down`→`download` and `fa-eye`→`view`
  (`view_action_resolved=true`, `download_action_resolved=true`), and the collector **programmatically
  clicked the real eye control and opened a real UYAP UDF viewer tab** (`new_page_detected=true`,
  `viewer_url_kind=udf_viewer`, `access_pattern=modal_new_tab_udf_viewer`); `sale_notice` did the same, so
  `viewer_pages_opened=2`. **But both viewers had no DOM text, no iframe/embed/object, `canvas_count=0`, and
  exactly `image_count=1`** — the old `classify_viewer_representation` collapsed this into the misleading
  `unsupported_representation:canvas_image_only` (reporting canvas when none was observed), and since no
  download-required instruction was present, **Fix 6.1 correctly did not trigger**. The mutation guard
  passed again (`uyap.json` unchanged, count `7 -> 7`, SMM unchanged). Root cause: the collector obtained
  deterministic source only from DOM-text/iframe paths and never inspected whether the single **image**
  element exposed browser-accessible source bytes — the blocker is the **image-backed UDF viewer
  representation**. **Fix 8 (viewer representation / image-source capture only; no page-state/card/modal/
  row/action/download-fallback/structural change, and no OCR):** (1) `classify_viewer_representation` now
  splits canvas and image → `dom_text`/`iframe`/`embed_object`/`canvas_and_image`/`canvas_only`/`image_only`/
  `unknown`; (2) `classify_viewer_outcome` routes `image_only`/`canvas_and_image` to a distinct `image_backed`
  outcome placed **after** the strict `download_required` and `viewer_error` precedence (a decorative image
  never triggers the Fix-6.1 same-row download fallback); (3) new pure helpers introspect and classify the
  live image safely — `classify_image_src_kind` (`data_url`/`blob_url`/`http_resource`/`relative_resource`/
  `empty`/`unknown`, never the raw URL), `classify_document_image_candidate` (material visible image inside
  the viewer-content scope; logos/icons/out-of-scope rejected — no OCR/visual text), `select_viewer_image_
  candidate` (largest material **document** candidate, never the first/global image), `image_source_capture_
  supported`, `viewer_image_candidate_summary` (privacy-safe), and `decode_data_url`/`image_mime_to_extension`;
  (4) live (pragma) `_viewer_image_candidates` (safe per-image metadata via a scoped `eval_on_selector_all`),
  `_capture_image_source` (data: decoded directly; blob:/same-origin fetched by a narrowly scoped in-page
  `fetch` in the already-authenticated viewer context — no manual cookie/token handling, no screenshot), and
  `_collect_viewer_image` (select → capture exact bytes → store the renderer image in the existing gitignored
  artifact store with type+ext+size+short sha). A captured image is an **official viewer-render source
  artifact but is not text-extractable** — `extraction_supported_for` still accepts only `.txt`/`.html`/`.htm`
  (a `mimeType=Udf`/image MIME never implies text support), so `viewer_image_artifact_collected=true`,
  `viewer_image_text_extraction_supported=false`, and the extracted auction price stays **null** (honest).
  New privacy-safe per-attempt diagnostics (`viewer_representation`, `viewer_image_candidate_count`,
  `viewer_document_image_candidate_count`, `viewer_image_candidates`, `selected_viewer_image_candidate_index`,
  `viewer_image_source_kind`, `viewer_image_source_capture_supported`/`_strategy`,
  `viewer_image_source_bytes_captured`, `viewer_image_artifact_collected`/`_extension`/`_mime_hint`/`_size`/
  `_sha256`, `viewer_image_text_extraction_supported`, `viewer_image_capture_blocking_reason`) emit **no** raw
  src/blob/data-URL bodies, base64, `evrakId`, cookies, tokens, or document text. `auction_result` remains the
  priority official price source and `sale_notice` an accepted appraisal-side path; result-card `Satış Tutarı`
  is never substituted and known truth stays verifier-only. Added 35 offline tests. Full suite: **475 passed**
  (440 baseline + 35 tests). **No live `PASS` is claimed: image source capture has not been proven live,
  UDF/image text extraction does not work, the İhale Bedeli was not extracted, and the same-row download
  fallback did not run live** — a post-Fix-8 operator rerun is required. Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search convention
  are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 9 (measured ninth live result: `FAIL`)** —
  chronology: runs 1–8 = **FAIL** → Fixes 1–8 → run 9 = **FAIL** → Fix 9 → operator rerun pending. Run 9
  **live-proved Fix 8's exact browser-accessible source-byte capture**: for both `auction_result` and
  `sale_notice` the collector reached the real UDF viewer (`viewer_representation=image_only`,
  `viewer_outcome=image_backed`), classified the image source (`viewer_image_source_kind=http_resource`),
  captured the exact bytes in the authenticated viewer context (`viewer_image_source_capture_strategy=
  same_origin_page_fetch`, `viewer_image_source_bytes_captured=true`), computed size/hash, and stored the
  renderer image — each a `.png`, natural `298×298`, rendered `100×120`, `63334` bytes, sha256
  `074e28d977997b50…`. **But the two viewers produced a byte-identical PNG — the same size and the same
  SHA256 — for two logically distinct official documents** (`Artırma Sonuç / Uzatma Tutanağı` vs
  `Satış İlanı`), and Fix 8 promoted **both** as row source artifacts
  (`artifact_types_collected=[auction_result, sale_notice, status_card]`). The mutation guard passed again
  (`uyap.json` unchanged, count `7 → 7`, SMM unchanged). Root cause: **a captured viewer image was treated as
  the row's document source artifact merely because it was visible, materially sized, viewer-content-scoped,
  and its bytes were captured** — those facts prove only that a *viewer asset* was captured, not that the
  specific official *document content* was captured; a byte-identical image shared across two different
  documents is a high-confidence **generic/shared viewer-asset** signal (placeholder / loader / application
  graphic), and the operator additionally observed each viewer tab opening and closing very quickly
  (compatible with capturing a **pre-document-ready** image — treated as a hypothesis to measure, not a
  proven fact). We do **not** claim the PNG is definitively a logo/placeholder until DOM/resource evidence
  proves it. **Fix 9 (viewer ready-state + document-render image identity + cross-document generic-asset
  guard only; Fix 8's byte capture is preserved; no OCR, no screenshot, no image-text parsing, no
  auto-download, and no page-state/card/modal/row/action/download-fallback/structural change):** (1) new pure
  helpers — `classify_viewer_ready_state` (bounded stabilization decision → `stable_image_representation` /
  `stable_text_representation` / `download_required` / `viewer_error` / `timeout_unstable`, with
  `download_required`/`viewer_error` taking precedence), `viewer_observation_signature` and
  `viewer_image_fingerprint` (privacy-safe DOM signature/fingerprint over natural dimensions + `src_kind` +
  extension hint — never raw URLs or bytes), `detect_cross_document_image_duplicates` (exact full 64-char
  SHA256 across ≥2 **distinct** artifact types — never a short prefix), `classify_viewer_image_document_
  identity` (`document_specific` / `shared_cross_document_asset` / `generic_viewer_asset` /
  `renderer_asset_unresolved` / `not_document_candidate`), and `resolve_viewer_image_identities` (per-capture
  identity + a promotion decision that is `true` **only** for `document_specific`); (2) live (pragma)
  `_observe_viewer_stabilization` polls the safe signature across `VIEWER_STABILIZATION_MAX_OBSERVATIONS`
  bounded steps with a short `wait_for_timeout` (never an unbounded sleep), so the collector no longer
  captures the **first** qualifying image immediately — a placeholder later replaced by a document render is
  detected (`viewer_image_fingerprint_changed`), and an ever-changing viewer honestly reports
  `timeout_unstable`; (3) `_collect_viewer_image` still captures and stores the exact source bytes (Fix 8
  preserved) but **no longer promotes** — it records `viewer_asset_captured=true` and returns a capture with
  the **full** SHA256, and the per-container post-loop pass assigns identity and appends a document **only**
  when the identity is `document_specific`; (4) a `shared_cross_document_asset` (the Run-9 case) is preserved
  as a diagnostic viewer asset but is **not** appended, so it does **not** add `auction_result`/`sale_notice`
  to `artifact_types_collected` (`status_card` is unaffected). New privacy-safe diagnostics
  (`viewer_ready_state`, `viewer_stabilization_observation_count`, `viewer_stabilization_transition_detected`,
  `viewer_representation_sequence`, `viewer_image_fingerprint_changed`, `viewer_asset_captured`,
  `document_source_artifact_collected`, `viewer_image_document_identity`, `viewer_image_cross_document_
  duplicate`, `viewer_image_duplicate_artifact_types`, `viewer_asset_only`,
  `viewer_asset_identity_blocking_reason`) emit no raw URLs or image bytes (SHA256 only). Fix 6.1's strict
  `download_required` precedence still interrupts stabilization and takes priority, and `auction_result`
  remains the priority official price source. Added 43 offline tests. Full suite: **518 passed** (475 baseline
  + 43 tests). **No live `PASS` is claimed: document-source identity was not proven live, image/UDF text
  extraction still does not work, the İhale Bedeli was not extracted, and whether the identical 298×298 PNG is
  an early placeholder or the final stable viewer image is exactly what the post-Fix-9 operator rerun must
  measure** — a post-Fix-9 operator rerun is required. Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search convention
  are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 10 (measured tenth live result: `FAIL`)** —
  chronology: runs 1–9 = **FAIL** → Fixes 1–9 → run 10 = **FAIL** → Fix 10 → operator rerun pending. Run 10
  **crossed the viewer representation barrier**: `auction_result` transitioned `image_only → dom_text →
  dom_text` and `sale_notice` reached `dom_text`, both `viewer_ready_state=stable_text_representation`,
  `viewer_asset_captured=false`, `document_source_artifact_collected=true` — the byte-identical 298×298 PNG
  was **transitional**, and `artifact_types_collected` became `[auction_result, sale_notice, status_card]`.
  Same-asset **reconciliation succeeded** (`ada=50984`, `parsel=1`, `section_no=60`, `floor=12`) and **KDV
  `20.0` was deterministically extracted**, proving the collected source is real, structured official text.
  But extraction failed at the field level: appraisal was **ambiguous** (`[1.0, 6800000.0]`), the explicit
  **İhale Bedeli was missing**, **ALACAĞA MAHSUBEN** was not recognized, and the audit stayed
  `PENDING_REVIEW`. The mutation guard passed again (`uyap.json` unchanged, count `7 → 7`, SMM unchanged).
  Two structural, code-evident root causes: (1) appraisal/İhale extraction took the **first number in a wide
  60-char window** (`_all_amounts_after`/`_amount_after`) with no monetary-format or field-boundary
  constraint, so a bare `1` (a parcel/row identifier) was admitted as an appraisal candidate alongside the
  genuine `6.800.000,00`; and (2) `extract.py` applied **no mojibake repair** while `collect.py` did, so
  Turkish-special-character labels (`İhale Bedeli`, `ALACAĞA`) silently failed to fold/match whereas
  pure-ASCII labels (`Muhammen Bedel`, `KDV`, `ada`, `parsel`) matched — precisely the observed
  success/failure split (the raw stabilized DOM text is not persisted to disk, so the code, the Run-10
  diagnostics, and the known document label structure are the source of truth). **Fix 10 (stabilized-source
  finalization + label-bounded official field extraction only; no OCR, no ML, no known-truth fallback, and no
  page-state/card/modal/view-action/Fix-8-9-image-promotion or structural change):** (a) the ordinary
  per-attempt viewer diagnostics now report the **final stabilized** state — when stabilization reaches
  `stable_text_representation` the collector sets `viewer_representation=dom_text`,
  `viewer_text_available=true`, `viewer_outcome=content_available`, and explicit
  `final_viewer_representation`/`final_viewer_text_available`/`final_viewer_outcome`, while retaining the
  pre-stabilization snapshot as `initial_viewer_representation`/`initial_viewer_text_available`/
  `initial_viewer_outcome` and preserving the `image_only → dom_text → dom_text` sequence; (b) new
  label-bounded extraction in `extract.py` splits the source into newline-preserving segments and, for each
  recognized field label, reads the value **only** from a bounded region (the same segment after the label,
  cut at the next field/identifier label, or the adjacent segment for a `LABEL`/`VALUE` block split), and a
  value must be a **Turkish monetary literal** (`MONEY_LITERAL_RE`: grouping dots and/or a decimal comma) — a
  bare integer such as a parcel, row, section, or `ada` number is **structurally excluded** (no amount
  threshold, no `max()`, no verifier value, no magnitude heuristic), which removes the spurious `1.0`; (c) the
  explicit **İhale Bedeli** is recovered from its own label/value relation (never `Satış Tutarı`, `Ödenmesi
  Gereken Bedel`, or an `ALACAĞA MAHSUBEN` phrase, and never a bare number); (d) **ALACAĞA MAHSUBEN** is
  recognized from the actual `Ödenmesi Gereken Bedel` settlement field or the standalone phrase, including
  block-split forms, and a generic `mahsuben` elsewhere does not set the flag; and (e) a shared
  `demojibake`/`_looks_mojibake` helper (moved into `models.py`) is applied at ingestion in `extract.py`
  before length-preserving folding, so mojibaked Turkish labels match while offsets stay aligned. New
  privacy-safe field-level provenance (`auction_price_field_label_found`, `auction_price_candidate_count`,
  `auction_price_value_relation_strategy`, `appraisal_field_label_found`, `appraisal_candidate_count`,
  `appraisal_value_relation_strategies`, `settlement_field_label_found`, `alacaga_mahsuben_detected`,
  `settlement_value_relation_strategy`) is surfaced in the pilot report’s `field_extraction` block and carries
  no full source text or personal data. Reconciliation and KDV extraction are preserved (regression-tested on
  a sanitized Run-10-style fixture), `auction_result` remains the İhale-Bedeli source, and `sale_notice`
  remains an accepted appraisal-side source. Added 39 offline tests. Full suite: **557 passed** (518 baseline
  + 39 tests). **No live `PASS` is claimed: the parser has not been proven live after Fix 10 — the İhale
  Bedeli and appraisal have not been extracted from the real viewer, no audit admission has occurred, and a
  post-Fix-10 operator rerun is required.** Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`,
  TOKİ external status (5 observed / 0 SMM), and the numerical-search convention are unchanged; the pilot
  remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 11 (measured eleventh live result: `FAIL`)** —
  chronology: runs 1–10 = **FAIL** → Fixes 1–10 → run 11 = **FAIL** → Fix 11 → operator rerun pending. Run 11
  **live-proved Fix 10's appraisal fix**: `final_viewer_representation=dom_text`,
  `final_viewer_text_available=true`, `document_source_artifact_collected=true` for both `auction_result` and
  `sale_notice`, and appraisal extracted cleanly — `extracted_appraisal=6800000.0`,
  `appraisal_candidate_count=1`, `appraisal_candidates=[6800000.0]`,
  `appraisal_value_relation_strategies=[same_segment]` — so the earlier spurious `1.0` is **live-resolved**.
  Same-asset reconciliation stayed `reconciled` (`ada=50984`, `floor=12`, `parsel=1`, `section_no=60`) and KDV
  stayed `20.0`. The remaining required blocker is precise: `auction_price_field_label_found=true` but
  `auction_price_candidate_count=0` and `extracted_auction_price=null` — the real stabilized source **contains
  a recognized İhale Bedeli label but the current label→value relation does not reach a money value** — and on
  the settlement side `settlement_field_label_found=false` / `alacaga_mahsuben_detected=false`, so the audit
  stayed `PENDING_REVIEW` (the mutation guard passed again: `uyap.json` unchanged, count `7 → 7`, SMM
  unchanged). Because the previous task never persisted the raw stabilized DOM text, the exact real
  serialization could not be inspected — so **Fix 11 is a source-persistence + bounded field-layout adapter
  task, not a layout guess. Fix 11 (no OCR, no ML, no known-truth injection; no
  page-state/card/modal/view-action/viewer/reconciliation/audit/structural change):** (a) when a viewer
  reaches `stable_text_representation` and a document-specific DOM-text source is collected, the **exact
  source text is persisted locally** through the existing gitignored ingestion store
  (`data/ingestion/uyap/artifacts/viewer_sources/`), and only privacy-safe provenance
  (`source_text_persisted`, `source_text_artifact_sha256`, `source_text_artifact_size`) is recorded — the body
  is **never** committed, copied into README/DEVELOPMENT_HISTORY, emitted into pilot JSON, printed in full, or
  placed in test fixtures verbatim; (b) `extract.py` gains `_ihale_bedeli_relation`, a **bounded multi-segment
  label→value relation** (`same_segment → adjacent_segment → bounded_following` over at most a few following
  segments, **stopping immediately** at the next recognized field label or property identifier via
  `_VALUE_BOUNDARY_RE`), still accepting only a Turkish monetary literal — no whole-document scan, no
  first-money-anywhere, no `max()`, no threshold, no verifier value; (c) `_bounded_token_sequence` /
  `_settlement_relation` add a **bounded token-sequence label matcher** requiring the complete
  `odenmesi → gereken → bedel` identity within a small segment window (a generic `bedel`/`gereken` never
  qualifies), then read `ALACAĞA MAHSUBEN` only from that field's bounded value region (including split
  serializations), never inferring it from price/creditor/zero-balance/known truth; and (d) a new privacy-safe
  `field_neighborhood` (auction-price and settlement) carries only structural counts, boundary-stop reasons,
  and normalized field-label *types* — never segment text, money, personal data, or viewer URLs — and is
  surfaced in the pilot report's `field_extraction` block alongside `source_text_persisted`/`_sha256`/`_size`,
  so the next live report can explain the actual İhale Bedeli and settlement layout without exposing content.
  Fix 10's live-proven appraisal (`6800000.0`, one candidate, bare `1` still excluded) and viewer
  finalization, plus reconciliation and KDV `20.0`, are preserved unchanged (regression-tested), and the
  explicit İhale Bedeli requirement is not weakened (`Satış Tutarı`/`Ödenmesi Gereken Bedel`/status-card
  amounts are never the auction price). Added 33 offline tests. Full suite: **590 passed** (557 baseline + 33
  tests). **No live `PASS` is claimed: the İhale Bedeli value has not been extracted from the real viewer, the
  settlement field is not yet confirmed from the real source, no audit admission has occurred, and a
  post-Fix-11 operator rerun — which will persist and reveal the real layout — is required.** Structural core,
  four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the
  numerical-search convention are unchanged; the pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 12 (measured twelfth live result: `FAIL`; native
  UDF source format discovered)** — chronology: runs 1–11 = **FAIL** → Fixes 1–11 → run 12 = **FAIL** → a real
  official `.udf` was manually downloaded and inspected → Fix 12 → operator rerun pending. Run 12's viewer
  sequence for `auction_result` ended `image_only → unknown → unknown` within the observation window, so no
  auction-result DOM text was collected. The operator then **manually clicked the real row-local download** for
  `1- Artırma Sonuç / Uzatma Tutanağı` and supplied the exact `.udf`, inspected byte-for-byte: **file size
  4406 bytes, a ZIP-compatible (`PK`) container** with top-level members `documentproperties.xml`,
  `content.xml`, `sign.sgn`; **`content.xml` is 17940 bytes uncompressed, UTF-8 XML**, and carries the official
  document source text directly inside its `content` element's CDATA — including the explicit `İhale Bedeli`
  monetary literal, `Ödenmesi Gereken Bedel` / `ALACAĞA MAHSUBEN`, KDV, and the same-asset descriptors used by
  the existing reconciliation path. The prior proposed transition-gap/viewer-wait Fix 12 was **cancelled**:
  root cause is that for the measured auction-result document **the viewer is not the best deterministic
  evidence layer** — the official downloaded `.udf` is itself a native container whose `content.xml` holds
  deterministic UTF-8 text. **Fix 12 (deterministic native UDF container extraction + same-row official
  download collection only; no OCR, no ML, no rendering/LibreOffice/GUI, no additional viewer waits, no
  known-truth injection, no page-state/card/modal/row/view-action/reconciliation/audit/structural change; the
  viewer code and architecture are preserved):** a new `sold/ingestion/uyap/udf.py`
  `extract_udf_source_text(path_or_bytes)` validates the ZIP container and reads **only** the root
  `content.xml` directly (no `extractall`; rejects path-traversal member names, duplicate/ambiguous
  `content.xml`, encrypted/malformed archives, non-ZIP input, and bounds the decompressed size with
  `MAX_UDF_DECOMPRESSED_BYTES` against zip-bombs), parses the XML **safely** with the standard library (no
  external entities/DTD/XInclude/network — a `DOCTYPE`/`ENTITY`/`XInclude`/stylesheet is refused before
  parsing), finds the `content` element namespace-agnostically, and returns its CDATA/text exactly (UTF-8
  Turkish preserved), with honest per-stage `blocking_reason`s and a `native_udf_supported(diag)` predicate
  that requires validated structure + `content.xml` + parsed content text (never the extension alone). The
  collector adds `_collect_native_udf_download`: for an `auction_result` DocumentRow whose **row-local download
  action is positively resolved by the existing Fix-6 semantics** and which emits a **real browser download
  event**, it clicks that same-row control (never a page-global/first/Nth control, and *not* the Fix-6.1
  "unsupported viewer ⇒ download" trigger), stores the exact `.udf` bytes **unchanged** in the existing
  gitignored artifact store (full SHA256 internally, short SHA256 in diagnostics), runs the native reader, and
  on success feeds the extracted `content.xml` text into the **existing** label-bounded `extract_evidence`
  (source acquisition is solved by the native adapter; field extraction remains the Fix-10/11 parser). This is
  gated as a new evidence policy — `NATIVE_DOWNLOAD_TYPES = (auction_result,)`, priority-first — and on any
  failure it falls through to the preserved viewer path; `sale_notice`'s working stable-DOM-text path is not
  in the native set and is unchanged. `artifact_types_collected` may now include `auction_result` with the
  provenance reason **official same-row native UDF artifact** (`native_udf_source_relation`), not a viewer
  image or a merely-opened viewer page. The audit numerator stays the explicit official `İhale Bedeli` only
  (`Satış Tutarı`/`Ödenmesi Gereken Bedel`/`ALACAĞA MAHSUBEN` are never the auction price; `ALACAĞA MAHSUBEN`
  is settlement context only, and there is no KDV gross-up/net-down). New privacy-safe per-attempt diagnostics
  (`native_download_attempted`/`_action_resolved`/`_event_detected`, `native_artifact_collected`/`_extension`/
  `_size`/`_sha256`, `native_container_kind`, `native_udf_zip_valid`, `native_udf_member_names_safe_summary`
  (only a known-member-name summary + an unsafe-path flag), `native_udf_content_xml_found`/`_content_xml_size`/
  `_xml_parse_succeeded`/`_content_element_found`/`_source_text_available`/`_text_extraction_supported`,
  `native_udf_source_relation`, `native_udf_blocking_reason`) are surfaced in the pilot report's `native_udf`
  block and carry **no** native source text, full `content.xml`, names, TC IDs, IBANs, addresses, download/
  viewer URLs, cookies, or tokens. Added 37 offline tests using **sanitized synthetic** UDF fixtures (a ZIP of
  `documentproperties.xml` + a synthetic `content.xml` + `sign.sgn`, with synthetic amounts/identifiers such
  as `1.234.567,89` and `123 Ada, 4 Parsel` — the real official `.udf` and its exact `content.xml` are
  **never** committed). Full suite: **627 passed** (590 baseline + 37 tests). **The manually supplied artifact
  proves the measured native format only; it does *not* yet prove the automated row-local browser download +
  native extraction path works live, and no pilot `PASS` is claimed — a post-Fix-12 operator rerun is
  required.** Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status (5
  observed / 0 SMM), and the numerical-search convention are unchanged; the pilot remains non-mutating (genuine
  count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 13 (measured thirteenth live result: `FAIL`;
  cross-row native download misbinding)** — chronology: runs 1–12 = **FAIL** → Fixes 1–12 → run 13 = **FAIL**
  → the exact automated artifact was inspected byte-for-byte → Fix 13 → operator rerun pending. Run 13
  **live-proved the native UDF path technically**: `native_download_event_detected=true`,
  `native_artifact_collected=true`, `native_artifact_extension=.udf`, and `native_udf_zip_valid` /
  `native_udf_content_xml_found` / `native_udf_xml_parse_succeeded` / `native_udf_content_element_found` /
  `native_udf_source_text_available` all `true`. But direct byte/container inspection of the **exact automated
  artifact** (`12109` bytes, `content.xml` `127346` bytes) proved it is the **sale-notice** UDF — its
  `content.xml` title is `TAŞINMAZIN ELEKTRONİK SATIŞ ORTAMINDA AÇIK ARTIRMA İLANI` with `Kıymeti` + `KDV`,
  **not** the manually downloaded correct `Artırma Sonuç / Uzatma Tutanağı` result document (`4406` bytes,
  `content.xml` `17940` bytes, carrying explicit `İhale Bedeli` and `Ödenmesi Gereken Bedel : ALACAĞA
  MAHSUBEN`). The automated `auction_result` path therefore **downloaded the sale-notice UDF and misbound it as
  `auction_result`**, and the sale-notice boilerplate's inflected prose (`ihale bedelini`/`ihale bedelinin`)
  falsely set `auction_price_field_label_found=true` (`auction_price_candidate_count=0`). This is not a parser
  hypothesis — the exact automated bytes were inspected. Root cause: **DocumentRow identity was not preserved
  through the native download click** — `_locate_row_download_action` filtered candidate rows by a broad
  `has_text` label and returned the **first** matching download control, which in DOM order (sale-notice
  first) belonged to a different row. **Fix 13 (DocumentRow-bound native download + native document-type
  corroboration only; native UDF parsing, viewer stabilization, and appraisal parsing are unchanged; no OCR,
  no ML, no known-truth injection; structural freeze):** (a) a new pure `select_unique_document_row`
  reacquires the **unique** logical row by recognized identity (matching `artifact_type` + normalized label
  with `logical_row_recognized_type_count == 1`; 0 or >1 matches, or a multi-identity row, means **no
  download** with an honest `ambiguous_or_unresolved_row_reacquisition` blocker); (b) `_locate_row_download_
  action` now requires the row-scoped download control to be **exactly one** (`count() == 1`) — a count > 1
  means the matched "row" is actually a container spanning multiple rows, so it is skipped rather than grabbing
  the first (never a page-global/first/Nth control); (c) new pure `classify_udf_document_type` /
  `corroborate_native_document_type` deterministically detect the native source document type from **semantic
  title/field tokens** (`artirma sonuc/uzatma tutanagi` and an explicit whole-word `İhale Bedeli` field ⇒
  `auction_result`; `elektronik satis ortaminda`/`acik artirma ilani`/`satis ilani` ⇒ `sale_notice`; plus
  `sale_spec`/`appraisal_report`/`unknown`) — **never from monetary values or verifier numbers** — and
  `_collect_native_udf_download` promotes the artifact **only** when the detected type matches the requested
  `artifact_type`; on the measured mismatch (`requested=auction_result` / `detected=sale_notice`) the artifact
  is **not** appended, `document_source_artifact_collected` stays false, `auction_result` is **not** added to
  `artifact_types_collected`, and the exact bytes are preserved only for local diagnostics; and (d)
  `_ihale_bedeli_relation` now recognizes the `İhale Bedeli` field label as a **whole word** (`\bihale
  bedeli\b`), so inflected prose (`ihale bedelini`/`bedelinin`) no longer sets `auction_price_field_label_
  found`, while an explicit `İhale Bedeli : <money>` field still yields exactly one candidate; the auction
  numerator semantics (explicit official `İhale Bedeli` field only) are unchanged. New privacy-safe diagnostics
  (`native_requested_artifact_type`/`_normalized_label`, `native_row_reacquired`/`_artifact_type`/`_label_
  match`, `native_action_owner_same_row`/`_semantic_revalidated`/`_fingerprint_match`, `native_detected_
  document_type`, `native_document_type_corroborated`/`_mismatch`/`_corroboration_reason`) are surfaced in the
  pilot report's `native_udf` block and carry no source text, personal data, full URLs, or opaque IDs. Added 30
  offline tests. Full suite: **657 passed** (627 baseline + 30 tests). **Run 13 proved the native UDF download/
  container/text path works live but downloaded the wrong document for the requested row; Fix 13 rejects that
  misbinding and adds document-type corroboration, and is offline-tested only — no live correction and no pilot
  `PASS` is claimed until the operator reruns.** Structural core, four SMM moments, `conditional_on_trade`,
  `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search convention are unchanged; the
  pilot remains non-mutating (genuine count stays 7).

- **UYAP Live Browser Pilot 1 — Live Interoperability Fix 14 (measured fourteenth live result: `FAIL`;
  sale-notice native UDF collection)** — chronology: runs 1–13 = **FAIL** → Fixes 1–13 → run 14 = **FAIL** →
  Fix 14 → operator rerun pending. Run 14 **live-proved the Fix-13 auction-result native path end-to-end**:
  `native_row_reacquired=true`, `native_row_reacquired_artifact_type=auction_result`,
  `native_row_reacquired_label_match=true`, `native_action_owner_same_row=true`,
  `native_action_owner_semantic_revalidated=true`, `native_action_owner_fingerprint_match=true`,
  `native_download_event_detected=true`, `native_artifact_collected=true` (`native_artifact_size=4406`,
  `native_udf_content_xml_size=17940`), `native_detected_document_type=auction_result`,
  `native_document_type_corroborated=true` / `native_document_type_mismatch=false`, and the **existing**
  extractor independently recovered `extracted_auction_price=5715000.0` (`auction_price_candidate_count=1`,
  `auction_price_value_relation_strategy=same_segment`), `settlement_field_label_found=true`,
  `alacaga_mahsuben_detected=true` (`settlement_value_relation_strategy=odenmesi_gereken_bedel_segment`) — so
  the auction-result native source path is **solved live and is not modified**. Run 14 failed **only** because
  `sale_notice` was still on the viewer path, whose real sequence was `image_only → unknown → dom_text` with
  `viewer_stabilization_observation_count=3`, `viewer_ready_state=timeout_unstable`,
  `blocking_reason=viewer_representation_did_not_stabilize`, `artifact_collected=false` /
  `document_source_artifact_collected=false` → `extracted_appraisal=null`, `appraisal_candidate_count=0`,
  `kdv_rate=null`, `reconciliation_status=ambiguous`, `audit_decision=MISSING_APPRAISAL`, even though the same
  `sale_notice` row already exposes a positively resolved row-local download action
  (`download_action_detected=true`). **Fix 14 (a one-policy extension only; the native UDF parser, the
  auction-result path, viewer stabilization, and the appraisal parser are all unchanged; no OCR, no ML, no
  known-truth injection; structural freeze):** `NATIVE_DOWNLOAD_TYPES` is extended from `(auction_result,)` to
  `(auction_result, sale_notice)`, so `sale_notice` now uses the **same** Fix-13 DocumentRow-bound,
  pre-click-revalidated, document-type-corroborated native UDF download path — `select_unique_document_row`
  reacquires the unique `sale_notice` row by recognized identity, `_locate_row_download_action` requires an
  exactly-one row-scoped download control (never a page-global/first/Nth control), a real `expect_download`
  event is required, the unchanged native reader parses the `.udf`, and `corroborate_native_document_type`
  promotes the artifact **only** when `native_detected_document_type == sale_notice` (a `requested=sale_notice`
  / `detected=auction_result` mismatch keeps the bytes for diagnostics but is not promoted). `_DOC_PRIORITY`
  keeps `auction_result` first and `sale_notice` second, `appraisal_report`/`sale_spec` are **not** added, and
  the viewer path remains as fallback — no viewer stabilization wait is needed once the native download
  succeeds. The corroborated `sale_notice` source text feeds the **existing** `extract_evidence` (which already
  live-proved `extracted_appraisal=6800000.0` with one candidate and `kdv_rate=20.0` when the source is
  available), and reconciliation is expected to return `reconciled`. Added 21 offline tests (the Fix-12
  `sale_notice`-not-native test was updated to the Fix-14 policy). Full suite: **678 passed** (657 baseline +
  21 tests). **No pilot `PASS` is claimed: the automated `sale_notice` native download + corroboration has not
  yet run live, so a post-Fix-14 operator rerun is required.** Structural core, four SMM moments,
  `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search convention
  are unchanged; the pilot remains non-mutating (genuine count stays 7).

## 2026-07-09 — Repository-wide integrity remediation

A full-system audit identified fifteen correctness, provenance, packaging and operational-integrity
issues. They were closed together so the public product, offline evidence workflow and installed wheel
share the same fail-closed contracts:

- every near-fit candidate is scored against the same four finite SMM moments; candidates may no longer
  improve their objective by dropping KAP moments;
- all ten structural primitives remain in the bounded near-fit space (`rank(J)=4 < dim(theta)=10`), exact
  null-space directions are reported, and direct prediction sensitivity over `asking_signal` and
  zero-context `tightness_beta` is included in the price envelope;
- required audited evidence and all 77 current province anchors ship in the wheel; missing, empty,
  duplicate or stale evidence/snapshots fail closed rather than producing a fallback price;
- the corrected production near-fit snapshot contains 28 admissible configurations and is fingerprinted
  to the current four-moment vector; public valuation/evidence/stability requests do not launch searches;
- the corrected bounded search diagnostic is `INSUFFICIENT_COVERAGE`, kept separate from structural
  underidentification and never relabeled as stable;
- partial UYAP legal floors are modeled as interval-censored thresholds between the known statutory lower
  bound and observed completed-sale price, never as exact auction reserves;
- PostgreSQL DDL splitting is comment-safe, ORM metadata creates all current tables, and additive legacy
  ground-truth provenance columns are applied idempotently;
- UYAP admission re-extracts, reconciles and audits verified store-local artifacts; one-field asset
  matches, mutable cached audits, P/Q identity dedupe and pilot report/evidence path aliasing are rejected;
- UYAP operational JSON contains no inline document bodies; artifact paths are store-confined, carry full
  SHA-256 hashes, and candidate/bulk/admission/exclusion/report writes are locked and atomic;
- partial crawler runs cannot delist unseen inventory, and EVDS refreshes cannot replace canonical files
  after material scope or date regression; all refresh writes are atomic;
- public-domain labels cannot be disguised as direct resale labels, malformed dates are rejected,
  duplicate/flagged rows cannot unlock benchmarks, and callers cannot self-award confidence A;
- the unsupported property-type selector was removed, mixed-property calibration scope is explicit,
  null provinces are rejected, and every official province in the current anchor snapshot is supported;
- API extras declare eager model-import dependencies, cache initialization is synchronized, and the
  distributable wheel is smoke-tested outside the repository working directory.

Validation after the remediation: **743 passed** in one complete offline run. The genuine structural set
contains 18 UYAP completed sales, 2 KAP negotiated disposals and 3 TOKI disclosures. The remaining
`INSUFFICIENT_COVERAGE` status is an explicit numerical-search limitation, not an unresolved software
failure or an identification claim.
