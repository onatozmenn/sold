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
