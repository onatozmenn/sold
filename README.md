# sold

[![CI](https://github.com/onatozmenn/sold/actions/workflows/ci.yml/badge.svg)](https://github.com/onatozmenn/sold/actions/workflows/ci.yml)
[![Data refresh](https://github.com/onatozmenn/sold/actions/workflows/kfe-refresh.yml/badge.svg)](https://github.com/onatozmenn/sold/actions/workflows/kfe-refresh.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-215%20passing-brightgreen.svg)](tests/)
[![Data](https://img.shields.io/badge/evidence-UYAP%20%C2%B7%20KAP%20%C2%B7%20TCMB%20%C2%B7%20TOK%C4%B0-informational.svg)](#provenance-audited-public-structural-evidence)

> A **mechanism-aware structural econometric prototype** that infers a **structural transaction-price distribution** for a Turkish home from an **asking-price signal** and public economic evidence.

**sold** does **not** observe the actual ordinary-resale closing price — no source publishes it in Türkiye. Instead it treats ordinary negotiated resale as **generalized Nash bargaining** and calibrates the structural parameters to **source-specific public moments** by Simulated Method of Moments (SMM). Concretely:

- The actual ordinary-resale closing price **is not observed**; the output is an inferred distribution, never an observed, actual or true sale price.
- **TCMB** provides the **fair-value level anchor** (appraisal TL/m²), not transactions.
- Genuine **audited UYAP completed-sale auctions** and **KAP negotiated corporate disposals** provide **source-specific structural moments**.
- **TOKİ** is an **external cross-mechanism benchmark**, not an SMM moment source.
- The current **local Jacobian rank is 4** for a **six-dimensional** parameter vector, so the identification status is **`STRUCTURALLY_UNDERIDENTIFIED`**.
- Prediction sensitivity is evaluated across the **`admissible_near_fit_set` (`Θ_A`)**, and every transaction-price distribution is **`conditional_on_trade`** (`B ≥ S`).
- The reported **structural sensitivity range is not a confidence interval** and carries **no frequentist coverage claim**.

No fabricated data is ever served, and the system never reports a measured ordinary-resale prediction accuracy.

## Table of Contents

- [Background](#background)
- [How It Works](#how-it-works)
- [Structural inference engine](#structural-inference-engine)
- [Provenance-Audited Public Structural Evidence](#provenance-audited-public-structural-evidence)
- [UYAP Evidence Ingestion Pipeline V1](#uyap-evidence-ingestion-pipeline-v1)
- [Optional direct-label validation channel](#optional-direct-label-validation-channel)
- [Data Sources](#data-sources)
- [Install](#install)
- [Usage](#usage)
- [Automation](#automation)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Methodology & References](#methodology--references)
- [Roadmap](#roadmap)
- [Legal & Ethics](#legal--ethics)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Background

### The problem

In the United States, MLS "sold data" makes transaction prices transparent. Türkiye has no equivalent:

- **Title deeds (Tapu)** record declared values that are systematically understated to reduce transfer tax.
- **No MLS** — no system publishes the price a home actually sold for.
- **Listing portals** expose only the **asking** price.

So the ordinary-resale closing price is genuinely unobserved, and any model trained naively on listings is biased.

### The approach

`sold` does **not** learn an asking→closing gap from supervised labels, and it does **not** fall back to a published city negotiation margin. Instead it **infers a structural transaction-price distribution** from an economic model of negotiated resale. Ordinary resale is modeled as **generalized Nash bargaining**:

- `B` = buyer valuation
- `S` = seller reservation value
- trade occurs **iff `B ≥ S`**
- the closing price is `P = η·B + (1 − η)·S`, where `η` (seller bargaining power) is **estimated, never hard-coded**

The **asking price is a noisy strategic seller-side signal** of the seller reservation — it is **not** ground truth and **not** a ceiling. Public sources enter the model through **source-specific structural moments under their own mechanism** — never pooled as ordinary-resale labels. The six-dimensional structural parameter vector is fit by **Simulated Method of Moments** to those genuine moments; identification and near-fit search sensitivity are then reported honestly (the current fit is **structurally underidentified**).

## How It Works

```mermaid
flowchart TD
    TCMB["TCMB EVDS<br/>appraisal TL/m2 + KFE"] --> FV["Fair-value level anchor V(x,t)"]

    UYAP["UYAP e-Satis<br/>audited completed-sale auctions"] --> MU["conditional auction moments<br/>winning_bid / appraised_value | completed sale"]
    KAP["KAP<br/>negotiated corporate disposals"] --> MK["negotiated-sale moments<br/>log(sale / appraisal)"]

    MU --> SMM["Simulated Method of Moments"]
    MK --> SMM

    SMM --> IDENT["Jacobian identification diagnostics<br/>rank 4 / dim 6 -> STRUCTURALLY_UNDERIDENTIFIED"]
    SMM --> THETAA["admissible_near_fit_set (Theta_A)"]

    ASK["asking price<br/>noisy strategic seller signal"] --> SIM
    FV --> SIM
    THETAA --> SIM["structural bargaining simulation<br/>trade iff B >= S, P = eta*B + (1-eta)*S"]

    SIM --> DIST["conditional-on-trade distribution<br/>central / within-theta / between-theta / structural sensitivity range"]
    DIST --> OUT["CLI / REST API / web form"]

    TOKI["TOKI<br/>external cross-mechanism benchmark"] -. 0 SMM moments .-> SMM
```

The pipeline has **no fixed-margin fallback**, **no supervised asking→closing head**, and **no automatic ML takeover**. Fair value is a TCMB-anchored **level**; the two genuinely-observed public mechanisms (UYAP completed auctions, KAP negotiated disposals) contribute **source-specific moments** to SMM; TOKİ is held aside as an **external benchmark**; and every price output is a **`conditional_on_trade` structural distribution**, never an observed closing price.

### Structural components

The core lives in [`src/sold/structural/`](src/sold/structural/). Each module maps to one part of the model and is **frozen**:

| Module | Structural role |
|---|---|
| [`params.py`](src/sold/structural/params.py) | The structural parameter vector `θ` and its transforms (bounds, packing, defaults) |
| [`bargaining.py`](src/sold/structural/bargaining.py) | Generalized Nash bargaining: draw `B`, `S`; trade iff `B ≥ S`; `P = η·B + (1−η)·S` |
| [`hedonic.py`](src/sold/structural/hedonic.py) | TCMB-anchored fair-value level `V(x, t)` (relative premiums only; no listing intercept as level) |
| [`auction.py`](src/sold/structural/auction.py) | UYAP completed-sale auction mechanism and its **conditional-on-completed-sale** moments |
| [`kap.py`](src/sold/structural/kap.py) | KAP negotiated-disposal `log(sale/appraisal)` moments (currency/VAT normalization) |
| [`toki.py`](src/sold/structural/toki.py) | TOKİ cumulative-disclosure differencing → external cross-mechanism benchmark cohorts |
| [`moments.py`](src/sold/structural/moments.py) | Observed vs simulated moment vectors and the weighting matrix `W` |
| [`smm.py`](src/sold/structural/smm.py) | Simulated Method of Moments objective + numpy-only Nelder-Mead minimizer |
| [`identify.py`](src/sold/structural/identify.py) | Numerical moment-Jacobian identification diagnostics (rank, condition, profiles) |
| [`partial.py`](src/sold/structural/partial.py) | `admissible_near_fit_set` (`Θ_A`), tolerance sensitivity, cumulative search-stability study |
| [`predict.py`](src/sold/structural/predict.py) | Identification-aware, `conditional_on_trade` prediction over `Θ_A` |
| [`datasets.py`](src/sold/structural/datasets.py) | Genuine audited evidence loader (`source_audited`, kept apart from fixtures) |

#### The structural parameter vector `θ`

`θ` is **six-dimensional** and is exactly the free set in [`params.py`](src/sold/structural/params.py) (`DEFAULT_FREE`, `dim(θ) = 6`). These are the parameters SMM estimates:

| Free parameter | Meaning |
|---|---|
| `mu_b` | Buyer-valuation location (log, relative to fair value) |
| `sigma_b` | Buyer-valuation dispersion (log) |
| `mu_s` | Seller-reservation location (log, relative to fair value) |
| `sigma_s` | Seller-reservation dispersion (log) |
| `eta` | Seller bargaining power `η ∈ (0, 1)` in `P = η·B + (1−η)·S` |
| `auction_shift` | UYAP forced-sale / auction buyer-valuation shift (log) |

Additional **fixed** context parameters (buyer-arrival intensity, market-tightness sensitivity, the KAP corporate mechanism shift, and the asking→reservation signal weight) are held constant in the current frozen configuration and are **not** part of the estimated six-dimensional vector — they are documented as fixed inputs, not free parameters.

## Structural inference engine

Convergence of the optimizer is **not** identification, so the engine separates estimation, identification diagnostics, and prediction sensitivity.

**Estimation.** The six-dimensional `θ` is fit by Simulated Method of Moments to the genuinely-observed public moments:

$$\hat\theta = \arg\min_\theta\; (m_{obs} - m_{sim}(\theta))' \, W \, (m_{obs} - m_{sim}(\theta))$$

Only **two** public mechanisms enter the SMM moment vector, under their own mechanism, never pooled as ordinary-resale labels:

| Source | Structural role | In SMM? |
|---|---|---|
| **UYAP** e-Satış | **Audited completed-sale auction evidence** — `winning_bid / appraised_value`, evaluated **conditional on a completed sale**. `muhammen_bedel = appraised value Q` (never a reserve or exact statutory floor). The public UYAP outcome taxonomy does not currently identify a comparable negative auction trade class, so no `uyap_sale_prob` moment is used. | ✅ moments |
| **KAP** | Non-related **negotiated corporate disposals** → moments over `log(sale / appraisal)`, with currency/VAT normalization at documented TCMB EVDS rates. Helps jointly calibrate `η` and the corporate mechanism shift (KAP does *not* "give `η`"). | ✅ moments |
| **TOKİ** | `external_cross_mechanism_benchmark` — genuine cumulative-disclosure cohort/composition moments that are **observed but held outside SMM** (`moments_used_in_identification = 0`, `rank(J_TOKİ) = 0`). No primary-market mechanism was invented to force a rank. | ❌ external only |

The **four** SMM moments currently in `m_obs` are:

1. `uyap_win_over_appraisal_mean`
2. `uyap_win_over_appraisal_sd`
3. `kap_log_ratio_mean`
4. `kap_log_ratio_sd`

**Five** genuine TOKİ external-benchmark moments are observed, but **zero** enter the current SMM objective — the simulator has **no primary-market mechanism** to produce a model-implied counterpart for them. UYAP, KAP and TOKİ records are **never** converted into synthetic ordinary-resale `asking → closing` ground truth.

**Identification.** `sold structural identify` computes a numerical moment Jacobian `J(θ) = ∂m_sim/∂θ'` (central differences, common random numbers) **restricted to genuinely-observed moments**, and reports per-source Jacobian ranks, singular values, condition number, weak directions, and per-parameter profiles. The current fit has **`rank(J) = 4`** for **`dim(θ) = 6`**, so the reported status is:

> **`identification_status = STRUCTURALLY_UNDERIDENTIFIED`** (an econometric statement about the moment structure — `rank(J) < dim(θ)`).

Because the model is underidentified, prediction runs in **sensitivity mode** across the **`admissible_near_fit_set` (`Θ_A`)**, defined as *the set of economically admissible structural parameter vectors whose SMM criterion lies within the documented near-fit tolerance of the best observed-moment fit*. `Θ_A` is explicitly **not** a formally estimated identified set, **not** a confidence region, and **not** a set with nominal coverage; it is an admissible near-fit region used only to expose parameter sensitivity.

**Search stability is reported separately from identification, and never conflated with it.** The numerical coverage of `Θ_A` by the descent-based sampler is a distinct question from econometric identification. The accepted cumulative, incumbent-preserving, common-threshold experiment (commit `3ef1208`) preserves the global incumbent as the search budget grows, so `cumulative_best_objective` **is monotone non-increasing** across increasing budgets. Its current diagnostic is:

> **`near_fit_search_stability = INSUFFICIENT_COVERAGE`** — the common-threshold parameter support is still materially expanding at the largest audited search budget. This makes a search-stability judgment premature. The numerical coverage diagnostic is **separate from and does not establish** structural underidentification.

**Prediction semantics.** For an ordinary listing, the asking price conditions the seller reservation; `B`/`S` are drawn; trades (`B ≥ S`) are retained; and a **`conditional_on_trade`** closing distribution is returned.

> The transaction-price distribution is computed **conditional on the structural simulation producing trade, `B ≥ S`**. It is **not** an unconditional expected sale outcome.

The prediction fields are:

| Field | Meaning |
|---|---|
| `price_estimate_condition` | Always `conditional_on_trade` — the estimate is a price *given a sale occurs*, never an unconditional/observed closing price |
| `central_structural_estimate` | Central estimate from a single deterministic **representative trading near-fit configuration** (lies inside its own interval by construction) |
| `within_theta_negotiation_interval` | Bargaining dispersion **at a fixed `θ`** (buyer/seller heterogeneity) |
| `between_theta_near_fit_band` | Movement of the central estimate **across `Θ_A`** (parameter sensitivity) |
| `structural_sensitivity_range` | The combined envelope — a **structural sensitivity range, not a confidence interval**, with no frequentist coverage |
| `simulated_trade_share_band` | The **model-implied Monte-Carlo share** of simulated draws satisfying `B ≥ S`. It is `not_empirically_calibrated_to_observed_uyap_no_trade_outcomes` — **not** a probability of sale, a sale likelihood, or an empirically estimated trade probability |

No prediction ever returns a `confidence_interval` or an `accuracy` field.

## Provenance-Audited Public Structural Evidence

`sold` turns **operator-supplied** official public records into provenance-audited structural evidence via per-source parsers. This is a **parser layer, not continuous ingestion**: you hand it a record you already downloaded (an auction result, a KAP disclosure), and the parser normalizes its non-personal fields. Nothing is discovered, fetched, or ingested automatically.

Each source has a distinct, non-pooled structural role:

| Source (`domain`) | Structural role | Enters SMM? |
|---|---|---|
| **UYAP** e-Satış | Audited completed-sale auction moments (`winning_bid / appraised_value` \| sale) | ✅ SMM moments |
| **KAP** | Non-related negotiated corporate-disposal moments (`log(sale/appraisal)`) | ✅ SMM moments |
| **TOKİ** | `external_cross_mechanism_benchmark` cohort moments | ❌ observed, outside SMM |
| **consumer** (direct label) | Optional self-reported validation channel | ❌ **frozen**, outside SMM |

The **SMM moment vector uses exactly four moments** (two UYAP, two KAP). TOKİ and the consumer channel are deliberately **excluded** from identification.

> **Evidence expansion batches.** Genuine audited records are admitted in explicit batches under a predeclared selection rule (e.g. *UYAP Evidence Expansion Batch 1* added five audited completed-sale auctions, bringing the genuine UYAP total to 7). Each admitted UYAP record uses the official **İhale Bedeli** over the audited appraisal `Q` — never a deposit-adjusted, ownership-share, creditor-setoff, or KDV-adjusted amount. Audited-but-non-terminal records are preserved as excluded candidates in [`validation/structural/uyap_candidates.json`](validation/structural/uyap_candidates.json) (mirroring the KAP-candidate manifest); they never enter the genuine set, the SMM moments, or any negative-class construction (`uyap_sale_prob` is never created). Current genuine counts are reported by `sold structural dataset` (separate from fixtures), not hardcoded here.

### Three levels of validation, kept distinct

1. **Parser / adapter validation** — unit tests confirm each parser maps fields to the schema on **illustrative fixtures**. ✅ done.
2. **Real-record validation** — the operator downloads one real official record per source, feeds its non-personal fields through the parser, and commits the **manually-audited expected output** (never the raw artifact) under [`validation/`](validation/). Tests then enforce `parser output == audited expectation`. The genuine audited structural seed lives under [`validation/structural/`](validation/structural/) (`source_audited = true`), strictly separate from fixtures.
3. **Live source ingestion** — continuously fetching new records per source. ⬜ **not built** (a ToS-reviewed operator step, deliberately out of scope; consistent with the project's no-scraping stance).

> Level-1 **illustrative fixtures** (e.g. [samples/labels/illustrative_kap.json](samples/labels/illustrative_kap.json)) use invented placeholder values to exercise the parser. Level-2 **genuine audited** records live separately and drive the structural moments. The two tiers are kept strictly separate and never conflated.

### Domains are never pooled

- UYAP and KAP enter SMM **only** through their own source-specific moments — never merged into a single asking→closing target.
- TOKİ cohort moments are observed but **held outside** the SMM system (external benchmark).
- The consumer direct-label channel is **frozen** and never contributes to SMM.
- `sold structural dataset` reports the genuine audited counts per source **separately** from fixtures, demo data, and near-fit search candidates.

## UYAP Evidence Ingestion Pipeline V1

A **provenance-aware data-acquisition subsystem** ([`src/sold/ingestion/uyap/`](src/sold/ingestion/uyap/)) that reduces the manual work of discovering, collecting, extracting, auditing, reviewing, and admitting UYAP e-Satış completed-sale evidence. It is **not** a new methodology or pricing mechanism and does **not** modify the frozen structural core; admission writes to the existing genuine UYAP schema and preserves the UYAP P/Q moment definition.

**Workflow.** `discovery → collection → extraction → same-asset reconciliation → rule-based completed-sale audit → human review → explicit admission → existing UYAP evidence schema`. Extraction is deterministic (no ML, no weak supervision, no classifier); each field is traceable to the artifact it came from. **Audit is not admission** — a parser never writes genuine `uyap.json` directly; admission is a separate, explicit, idempotent operator action that validates the existing schema and dedupes by `public_record_id`.

**Completed-sale admission rule** (formalizing the seven genuine observations + Batch 1): a candidate is admissible only with (A) an auditable appraisal `Q` for the same asset, (B) an **explicit official İhale Bedeli**, and (C) terminal completed-sale evidence (`Satıldı` / `Satış İşlemleri Tamamlandı`). The **auction-price numerator is always the explicit İhale Bedeli** — never the *Ödenmesi Gereken Bedel*, deposit-adjusted balance, ownership-share settlement, creditor-setoff, or KDV-adjusted amount; KDV never adjusts `P` or `Q`; `ALACAĞA MAHSUBEN` never invalidates an explicit İhale Bedeli. Non-terminal records (e.g. `Birinci Alıcıya Süre Verildi`) become `EXCLUDED_NON_TERMINAL` in [`validation/structural/uyap_candidates.json`](validation/structural/uyap_candidates.json) — never admitted, never a negative sale-probability observation (`uyap_sale_prob` is never created). Ambiguous candidates (missing appraisal / missing explicit İhale Bedeli / missing terminal evidence / reconciliation ambiguity) are surfaced to a **human-review queue** with the exact blocking reason and are never silently promoted.

**Browser-assisted, not authentication-bypassing.** The optional browser collector (Playwright, `pip install -e ".[browser]"`) operates **only within a user-controlled, already-authenticated or public session** (attach to a browser you launched via a CDP endpoint, or a local profile you signed into yourself). It **never** automates e-Devlet login, MFA or CAPTCHA, never bypasses access controls, and never stores credentials, cookies, session tokens, or browser profiles in the repository (raw artifacts and profiles live under gitignored `data/`). If a live browser is unavailable, the **manual artifact-import** path (saved HTML/PDF/text) is the fallback.

**Privacy.** Only non-personal institution / official file identifier / property / economic / public-record fields are retained. Party, debtor, creditor, counsel, and personal identifiers (names, TC IDs, phones, IBANs, accounts) are never propagated into normalized extraction or analytical records.

```bash
sold uyap discover --institution "Ankara ... Satış Memurluğu" --file-id "2026/43 Satış"
sold uyap import-artifacts --candidate-id <id> --type auction_result --path saved_result.html
sold uyap extract  --candidate-id <id>      # deterministic fields (not admission)
sold uyap audit    --candidate-id <id>      # rule-based completed-sale audit (not admission)
sold uyap review                            # human-review queue with blocking reasons
sold uyap admit    --candidate-id <id>      # EXPLICIT, idempotent admission to uyap.json
sold uyap status                            # discovered / audited / admissible / admitted
```

> **Live UYAP access status.** Live end-to-end UYAP access was **not** available in the development environment and was **not** tested; there is **no** official UYAP API integration. The browser adapter is implemented and its prerequisites documented honestly, the deterministic parsers run against local fixtures and manually saved artifacts, and the **automated test suite requires no network**.

### UYAP Live Browser Pilot 1 (verification workflow)

A **non-mutating verification** milestone that checks whether the pipeline can, through a **user-controlled browser session**, collect the already-known completed-sale record **2026/263 Esas** and reproduce its manually-audited truth (`appraisal 6,800,000` / `İhale Bedeli 5,715,000` / `P/Q 0.8404411764705882` / `ADMISSIBLE_COMPLETED_SALE`). Because **2026/263 is already admitted**, the pilot **never** admits it again — the genuine UYAP count stays **7** and `uyap.json` is unchanged (a mutation guard fingerprints the file, count, and four-moment SMM vector before/after). e-Devlet authentication is always **manual**; the pilot only attaches to a session you launched and signed into yourself.

Windows operator workflow (PowerShell; adjust the Chrome path for your machine):

```powershell
# 1) install the optional browser extra
pip install -e ".[browser]"; python -m playwright install chromium

# 2) start Chrome with remote debugging and a DEDICATED non-repo profile (sign in yourself in this window)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:LOCALAPPDATA\uyap_pilot_profile"
#   (Chrome may also live at 'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe' or "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")

# 3) in that Chrome window: authenticate to e-Devlet/UYAP and open the 2026/263 result page

# 4) run the non-mutating pilot (uses the current tab; never admits, never fabricates)
sold uyap pilot --cdp-endpoint http://127.0.0.1:9222

# 5) inspect the JSON report (gitignored runtime artifact)
Get-Content data\ingestion\uyap\pilot_report.json
```

The dedicated profile lives **outside** the repository; browser profiles, cookies, tokens, and the pilot report are gitignored and never committed. Outcome semantics: **`PASS`** (real live session reached, required evidence extracted, reconciliation passed, audit `ADMISSIBLE_COMPLETED_SALE`, known-truth matched, `uyap.json` unchanged, count still 7); **`PARTIAL`** (live session worked but a required artifact/parser path was unsupported, reconciliation ambiguous, or terminal evidence not collected); **`FAIL`** (a real live run produced incorrect required evidence or an incorrect audit decision); **`NOT_RUN`** (no real user-controlled UYAP browser session was available). Offline regression success does **not** convert `NOT_RUN` into `PASS`.

> **Six real live runs, all `FAIL` (interoperability); Fix 5 is now proven live and Fix 6 resolves the row-local icon controls.** A real operator Chrome/CDP session connected and the real UYAP `2026/263` page was reached; the appraisal (`6,800,000`), terminal status (`satildi`) and KDV (`20.0`) are extracted, but no run has yet collected the official auction-result document. Every run left `uyap.json` byte-unchanged with the genuine count at **7** — a collection defect, not a structural/admission defect. **Fix 1** hardened KDV and found the "İhale Evrak Listesi" control but wrongly waited for a modal. **Fix 2** added same-page tab/panel detection with a row-local eye opening a new-tab `viewer.jsp?mimeType=Udf` **UDF viewer**. **Fix 3** made document entry **page-state-aware and target-record-card-scoped**. **Fix 4** hardened active-page state classification against embedded-reference false positives. **Fix 5** detected the visibly-open document modal by **semantic content** (div/portal overlay, no `.modal`/`role=dialog` required). On the **sixth** run Fix 5 worked live: `document_list_opened=true`, `document_modal_opened=true`, `document_list_container_kind=listing_modal`, the four real evidence rows recognized (`sale_notice`, `sale_spec`, `appraisal_report`, `auction_result`), and `document_actions_observed=8` (each row `action_count=2`) — **but every row reported `view_action_resolved=false` and `download_action_detected=false`**, so all attempts stopped at `row_action_unresolved:no_view_action`. The operator has directly observed that each row has two right-side **icon-only** controls (a red download/down-arrow and a separate eye/view), and that clicking the eye opens `/pp/viewer.jsp?mimeType=Udf&evrakId=...`. The defect was purely **row-local icon-control semantics**: the resolver inspected only each control's own text/class, never its descendant icon metadata (SVG `<title>`, `<use href="#...">`, nested `<i>` classes). **Fix 6** adds per-action **introspection** (tag, role, accessible name, `title`/`aria-label`, `href_kind`, `download` attribute, `onclick` presence, safe class tokens, descendant icon tokens from SVG `<title>`/`<use>` fragments/icon-font classes, safe handler tokens) and a deterministic **download-vs-view resolver** with precedence: accessibility/title → descendant icon tokens → href/download attribute → onclick tokens → else unresolved. Resolution is **positive-view only** — a control is chosen solely when it is positively classified `view`; there is **no** "the other one is download, so this is view" inference, **no** positional/right-most/Nth-action guessing, and **color is never a classifier** (the observed red is ignored). New privacy-safe diagnostics (`recognized_document_rows[].action_summaries`, `action_resolution_strategy`) record the real observed tokens/`href_kind` **without** raw hrefs, opaque `evrakId` values, `onclick` bodies, or DOM dumps, so an unresolved live run remains debuggable. **No live `PASS` is claimed**, the viewer stage was **not** exercised live, and **UDF source extraction / İhale Bedeli extraction remain unproven**; the operator must rerun `sold uyap pilot` from the real search/listing page (`2026/263 İcra` card visible) so a new real report can measure whether the auction-result row now resolves its view action and reaches the viewer (or fails honestly later at click / new-tab / UDF representation).

> **Additional real viewer observation (before the post-Fix-6 rerun) — `download_required`; Fix 6.1 adds a same-row download fallback.** The operator manually clicked a row-local eye/view action: a real UYAP viewer tab opened at `/pp/viewer.jsp?mimeType=Udf&evrakId=...`, but the document did **not** render — the viewer visibly displayed **"Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz."**. So reaching the viewer does **not** guarantee content availability: a positively resolved view action has at least two real outcomes — content available, or **download required**. **Fix 6.1** adds a deterministic **viewer-outcome classifier** (`classify_viewer_outcome` → `content_available` / `download_required` / `unsupported_representation` / `viewer_error` / `unknown`) whose `download_required` requires the **combined** viewer-failure + download-instruction semantics (`viewer_download_instruction_detected`, with Turkish folding + constrained mojibake repair) — a bare `indir` or a generic "program indir" instruction is **not** enough. When the viewer reports `download_required`, the collector falls back to the **same** `DocumentRow`'s **positively resolved Fix-6 download action** (never a global/first/Nth/other-row download; if that row has no positively resolved download action it reports `download_required_but_download_action_unresolved` rather than clicking arbitrarily), captures the official artifact via a bounded Playwright download, and preserves it through the existing gitignored artifact store. View-first is preserved (documents are not downloaded unless the viewer says so). Deterministic extraction is attempted **only** for genuinely supported formats (`extraction_supported_for` → `.txt`/`.html` only); a raw `.udf`/`.pdf` download is **preserved but reported unsupported** (`downloaded_artifact_extraction_supported=false`) — a `mimeType=Udf` URL hint alone never implies support, and **no** raw-UDF parser, OCR, or known-truth injection is added. New privacy-safe diagnostics (`viewer_outcome`, `viewer_download_instruction_detected`, `download_fallback_attempted`, `download_fallback_resolved_same_row`, `download_action_resolved`, `download_event_detected`, `downloaded_artifact_extension`/`mime_hint`/`size`, `downloaded_artifact_collected`, `downloaded_artifact_extraction_supported`, `download_fallback_blocking_reason`) carry **no** opaque `evrakId`, full URLs, cookies/tokens, or document text. **No live `PASS` is claimed**: the same-row download fallback has **not** been exercised live, no official UDF download has been captured live, raw-UDF parsing is **not** proven, and the İhale Bedeli was **not** programmatically extracted — a post-Fix-6.1 operator rerun is required.

> **Seventh real live run, `FAIL`; Fix 6 is now live-proven for downloads and Fix 7 corrects the logical row boundary.** On the seventh run Fix 5's modal/document-row detection worked again and **Fix 6 positively resolved the real download icon live** (`fa-arrow-down` → `download`, `download_action_resolved=true` for all four rows) — but every row still reported `view_action_resolved=false` / `row_action_unresolved:no_view_action` and `viewer_pages_opened=0`. The Run-7 action summaries showed **both** reported controls as bare `<i>` icons — a document icon (`icon-docs …`) and the `fa-arrow-down` download arrow — with **no** eye/view control present. Root cause: the logical **document-row boundary was too narrow** and action introspection was counting **icon descendants** inside the label/download control instead of the owning **actionable controls**; the separate eye/view control is a sibling **outside** the selected semantic-label ancestor. **Fix 7** makes the logical `DocumentRow` the smallest single-recognized-label ancestor that contains **actual actionable controls** (`button`/`a`/`[role=button]`/`[onclick]`), with a constrained **actionable-sibling expansion** so the adjacent eye/view button is included, bounded by a **unique-recognized-label guard** (an ancestor spanning two document identities, or `body`/`html`/the whole modal, is rejected). An `<i>`/SVG/`<use>` icon is treated as **semantic metadata of its owning actionable control**, never as its own `ActionSpec`: two icons inside one button now yield **one** actionable control (with `fa-arrow-down` in its `descendant_icon_tokens`), and one download button + one view button yield **two**. Fix-6's positive precedence is reused unchanged (accessibility → descendant icon tokens → href/download → onclick; positive-view only, no positional/Nth/"the other one" inference — an unknown sibling stays unresolved), and Fix-6.1's same-row download fallback now receives the corrected row's **own** download action. A separate non-clean rerun also showed a **pre-opened/stale document list** already visible before the entry click (`pre_click_visible_document_types` already populated, then the control click timed out); Fix 7 adds a narrow guard (`preopened_document_list_reusable`) that, **before** clicking, reuses an already-open **valid** list (supported page state + target file identity present + strict `detect_document_list` container — hidden/raw-HTML labels never qualify) and sets `document_entry_state = preopened_document_list_reused`. New privacy-safe diagnostics: `row_boundary_strategy`, `logical_row_ancestor_kind`, `logical_row_recognized_type_count`, `logical_row_actionable_control_count`, `actionable_control_tags`, `document_entry_state`. **No live `PASS` is claimed**: eye/view semantics have **not** been resolved live, the viewer has **not** been reached programmatically, the Fix-6.1 download-required fallback has **not** run live, no official UDF has downloaded live, raw-UDF extraction is **not** proven, and the İhale Bedeli was **not** extracted — a post-Fix-7 operator rerun is required.

> **Eighth real live run, `FAIL`; Fix 7 is now live-proven end-to-end to the viewer, and Fix 8 introspects the image-backed UDF viewer without OCR.** On the eighth run Fix 7 corrected the `auction_result` row live (two real `button` controls; `fa-arrow-down` → `download`, `fa-eye` → `view`, `view_action_resolved=true`, `download_action_resolved=true`), the collector **programmatically clicked the real eye control and opened a real UYAP UDF viewer tab** (`viewer_url_kind=udf_viewer`, `access_pattern=modal_new_tab_udf_viewer`), and `sale_notice` did the same — `viewer_pages_opened=2`. But both viewers exposed **no** DOM text, **no** iframe/embed/object, **zero** canvas, and exactly **one** image (`viewer_image_count=1`), which the old classifier collapsed into the misleading `unsupported_representation:canvas_image_only`; no download-required instruction was shown, so **Fix 6.1 correctly did not trigger**. The exact blocker is the **image-backed UDF viewer representation**. **Fix 8** (a) corrects the representation naming — `classify_viewer_representation` now returns `image_only` / `canvas_only` / `canvas_and_image` (never reporting canvas when none was observed); (b) routes an image-backed viewer to a distinct `image_backed` outcome (behind the strict `download_required`/`viewer_error` precedence, so a decorative image never triggers the Fix-6.1 download fallback); (c) **introspects the actual `<img>` elements** for privacy-safe structural metadata (natural/rendered dimensions, visibility, viewer-content scope, `same_origin`, `src_kind`) and selects a **document-render candidate** by material dimensions inside the viewer-content region — never the first/global image, never a logo/icon; and (d) **captures exact browser-accessible source bytes without OCR** — a `data:` URL is decoded directly, a `blob:`/same-origin resource is fetched via a narrowly scoped in-page fetch in the already-authenticated viewer context — storing the exact renderer image in the existing gitignored artifact store (type + extension + size + short sha). A captured image is an **official viewer-render source artifact but is not text-extractable** (`extraction_supported_for` still only accepts `.txt`/`.html`; a `mimeType=Udf` hint never implies support): `viewer_image_artifact_collected=true`, `viewer_image_text_extraction_supported=false`, extracted auction price **null** — an honest result. **No OCR, no screenshot OCR, no template digit reading, no known-truth injection.** New privacy-safe diagnostics (`viewer_representation`, `viewer_image_candidate_count`, `viewer_document_image_candidate_count`, `viewer_image_candidates`, `selected_viewer_image_candidate_index`, `viewer_image_source_kind`, `viewer_image_source_capture_supported`/`_strategy`, `viewer_image_source_bytes_captured`, `viewer_image_artifact_collected`/`_extension`/`_mime_hint`/`_size`/`_sha256`, `viewer_image_text_extraction_supported`, `viewer_image_capture_blocking_reason`) emit **no** raw src/blob/data-URL bodies, base64, `evrakId`, cookies, tokens, or document text. **No live `PASS` is claimed**: image source capture has **not** been proven live, UDF/image text extraction does **not** work, the İhale Bedeli was **not** extracted, and the same-row download fallback did **not** run live — a post-Fix-8 operator rerun is required.

> **Ninth real live run, `FAIL`; Fix 8's exact source-byte capture is now live-proven, and Fix 9 prevents a generic/shared viewer-asset false positive.** On the ninth run the collector reached both UDF viewers end-to-end and **Fix 8's browser-accessible source-byte capture worked live**: for `auction_result` and `sale_notice` alike the viewer was `image_only` → `image_backed`, `viewer_image_source_kind=http_resource`, `viewer_image_source_capture_strategy=same_origin_page_fetch`, `viewer_image_source_bytes_captured=true`, and each stored a `.png` (natural `298×298`, rendered `100×120`, `63334` bytes, sha256 `074e28d977997b50…`). **But the two viewers produced a byte-identical PNG — same size and same SHA256 — for two logically distinct official documents** (`Artırma Sonuç / Uzatma Tutanağı` vs `Satış İlanı`). Fix 8 promoted **both** as row source artifacts (`artifact_types_collected=[auction_result, sale_notice, status_card]`), which is a **high-confidence generic/shared viewer-asset false positive**: a byte-identical image cannot be independent document-render content for two different documents (it is more likely a placeholder/loader/application graphic), and the operator also observed each viewer tab opening and closing very quickly (compatible with capturing a pre-document-ready image — a hypothesis to measure, not a proven fact). We do **not** claim the PNG is definitively a logo/placeholder until DOM/resource evidence proves it. **Fix 9** (identity/promotion only — Fix 8's byte capture is preserved; no OCR, no screenshot, no image-text parsing, no auto-download, and no page-state/card/modal/row/action/download-fallback/structural change): (a) adds a **bounded viewer ready-state observation** (`classify_viewer_ready_state` → `stable_image_representation` / `stable_text_representation` / `download_required` / `viewer_error` / `timeout_unstable`) that polls a privacy-safe representation signature across a few short bounded observations (`wait_for_timeout`, never an unbounded sleep) so the collector no longer captures the **first** qualifying image immediately — a placeholder replaced by a later render is caught (`viewer_image_fingerprint_changed`), and an ever-changing viewer honestly reports `timeout_unstable`; (b) separates a **viewer asset** (a browser-accessible image observed inside the viewer) from a **document source artifact** (a resource positively associated with the specific `DocumentRow`) — `visible` + material dimensions + viewer-content scope + captured bytes now prove only that a viewer asset was captured; (c) assigns a deterministic **document-render identity** (`viewer_image_document_identity` ∈ `document_specific` / `shared_cross_document_asset` / `generic_viewer_asset` / `renderer_asset_unresolved` / `not_document_candidate`); (d) runs a **cross-document exact-SHA256 duplicate guard** within a single collection (`detect_cross_document_image_duplicates`, full 64-char SHA, never a short prefix) so a byte-identical image shared across ≥2 distinct artifact types is downgraded to `shared_cross_document_asset`; and (e) **restricts promotion** — only a `document_specific` capture is appended as the row's source artifact (`document_source_artifact_collected=true`), so a shared/unresolved capture stays a preserved diagnostic viewer asset (`viewer_asset_captured=true`, `document_source_artifact_collected=false`) and **does not** add `auction_result` or `sale_notice` to `artifact_types_collected` (`status_card` is unaffected). Fix 6.1's strict `download_required` precedence still interrupts stabilization and takes priority, `auction_result` remains the priority official price source, and known truth stays verifier-only. Added 43 offline tests. Full suite: **518 passed** (475 baseline + 43 tests). **No live `PASS` is claimed: document-source identity was not proven live, image/UDF text extraction still does not work, the İhale Bedeli was not extracted, and whether the identical 298×298 PNG is an early placeholder or the final stable viewer image is exactly what the post-Fix-9 operator rerun must measure.** Structural core, four SMM moments, `conditional_on_trade`, `Θ_A`, TOKİ external status (5 observed / 0 SMM), and the numerical-search convention are unchanged; the pilot remains non-mutating (genuine count stays 7).

## Optional direct-label validation channel

Separately from the structural evidence above, `sold` retains a **frozen** listing-outcome channel that lets a broker or seller record the *outcome* of a listing and receive simple **non-ML negotiation analytics** in return. This channel is **not** part of the structural model:

- It contributes **no moments to SMM** and calibrates **no `SaleProbability` / `ClosingDiscount` model** — no supervised asking→closing head is trained.
- Outcomes (`sold` · `withdrawn` · `expired` · `active` · `lost_to_other` · `unknown`) and their analytics (counts, discount summaries) are descriptive only; a delisting is never assumed to be a sale.
- Genuine self-reported submissions are kept **strictly separate** from fixtures, demo data, and test data by an origin/quality gate. The current genuine direct-label count is **0**.

```bash
sold flywheel record sold --province İstanbul --last-asking 3200000 \
     --sold-price 2900000 --price-cuts 1 --days-to-close 40
sold flywheel analytics
```

Equivalent REST endpoints `POST /outcome` and `GET /analytics` are retained as a legacy/optional surface. This channel is a possible **future validation** source for the structural model, not an input to it.

## Data Sources

Sources play **four distinct roles** and are never pooled into a single training target.

**1. Market anchors (official automated market data).** Fetched from the official **TCMB EVDS** API and refreshed automatically; nothing is scraped. These provide the fair-value **level anchor** and market context, not transactions:

| Dataset | Source | Meaning | Coverage |
|---|---|---|---|
| `datasets/kfe.csv` | TCMB | Residential Property Price Index (trend / rolling) | 2010 → now, monthly |
| `datasets/unit_prices.csv` | TCMB | Appraisal-based unit prices (TL/m²) | 2013 → now, quarterly, 77 provinces |
| `datasets/house_sales.csv` | TÜİK via EVDS | House sales counts (demand / liquidity context) | 2013 → now, monthly, by province |

**2. Structural evidence (manually-audited public official records).** Genuine, operator-audited **UYAP** completed-sale auctions and **KAP** negotiated corporate disposals under [`validation/structural/`](validation/structural/) (`source_audited = true`). These supply the **four SMM moments**; they are hand-audited from official records, never bulk-scraped.

**3. External benchmark.** Genuine **TOKİ** cumulative-disclosure cohorts — observed and available, but **outside** the SMM system (`external_cross_mechanism_benchmark`).

**4. Frozen optional validation channel.** The consumer/broker direct-label channel (see [Optional direct-label validation channel](#optional-direct-label-validation-channel)) and the legacy `datasets/ground_truth.csv` are **optional** and **not** part of the structural model. `datasets/ground_truth.csv` is a **legacy** user-provided file retained for the legacy `sold model` / `sold ground-truth` paths only.

> No unauthorized scraping is performed. Automated fetching is limited to official EVDS market series; structural evidence is added by manual audit of official public records. Title-deed *declared* values are **not** treated as consideration, and the system never claims to observe the actual ordinary-resale closing price.

## Install

**Prerequisites:** Python 3.11+ and a free [TCMB EVDS API key](https://evds2.tcmb.gov.tr) (only required to refresh data yourself).

```bash
git clone https://github.com/onatozmenn/sold.git
cd sold

python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"            # optional extras: .[model] .[api] .[postgres]
cp .env.example .env               # then set EVDS_API_KEY in .env
```

## Usage

The primary workflow is the **structural** command group over the frozen econometric core.

### Inspect the genuine structural evidence

```bash
sold structural dataset      # genuine audited UYAP / KAP / TOKİ counts, separate from fixtures
```

### Check identification before trusting any estimate

```bash
sold structural identify     # numerical moment-Jacobian: rank, condition, weak directions
                             # → STRUCTURALLY_UNDERIDENTIFIED (rank 4 / dim 6)
sold structural partial      # admissible_near_fit_set (Θ_A) + tolerance sensitivity
```

### Produce a structural, identification-aware price distribution

```bash
sold structural value 3200000 --province İstanbul --gross-m2 120 --partial
```

Output is an explicitly **`conditional_on_trade`** structural sensitivity range (central estimate, within-θ interval, between-θ near-fit band, combined structural sensitivity range) — **never** an observed closing price and **never** a confidence interval.

### Run the web app / REST API

```bash
sold serve            # → http://127.0.0.1:8000
```

Structural endpoints over the frozen core:

| Method | Endpoint | Returns |
|---|---|---|
| `POST` | `/structural/valuate` | Identification-aware `conditional_on_trade` structural distribution (machine-readable) |
| `GET`  | `/structural/evidence` | Model Evidence: genuine public structural counts (separate from fixtures/tests) |
| `GET`  | `/structural/method` | Method overview: anchor → moments → SMM → `Θ_A` → sensitivity |
| `GET`  | `/structural/stability` | Cumulative near-fit search-stability diagnostic |

### Refresh the market-anchor data (requires `EVDS_API_KEY`)

```bash
sold evds kfe          --out datasets/kfe.csv
sold evds house-sales  --out datasets/house_sales.csv
sold evds unit-prices  --out datasets/unit_prices.csv
```

### Legacy and experimental paths

These predate the structural pivot and are retained only for reference — they are **not** the active methodology:

```bash
sold model value 3200000 --province İstanbul --gross-m2 120   # legacy fixed-anchor baseline
sold model demo                                               # ML-method self-test on simulated data
sold gt analyze                                               # legacy ground-truth analytics
sold flywheel analytics                                       # frozen optional validation channel
sold structural estimate                                      # SMM method-validation demo (not a real fit)
```

The legacy `POST /valuate`, `POST /outcome`, and `GET /analytics` endpoints remain available for these paths.

## Automation

Three GitHub Actions keep the project alive without your machine:

| Workflow | Trigger | Action |
|---|---|---|
| `kfe-refresh.yml` | weekly + manual | Pulls KFE, house sales, and unit prices; commits the updated CSVs |
| `report.yml` | on label change + weekly | Regenerates `datasets/report.md` |
| `ci.yml` | every push / PR | Runs the test suite |

Set the `EVDS_API_KEY` repository secret (Settings → Secrets and variables → Actions) to enable data refresh.

## Project Structure

```
src/sold/
  config.py          # settings (.env)
  structural/        # FROZEN core: params, bargaining, hedonic, auction, kap, toki,
                     #   moments, smm, identify, partial, predict, datasets
  api/               # FastAPI service: structural endpoints + structural_product assembly
  evds/              # TCMB EVDS client: KFE, house sales, unit prices
  features/          # demand signal (market context) + feature builder
  model/             # legacy valuation / estimator / synthetic (method self-test)
  groundtruth/       # legacy ground-truth loading + analysis
  labels/            # provenance registry + per-source parsers + aggregate observations
  flywheel/          # frozen optional direct-label validation channel
  consumer/          # frozen consumer submission path + quality gate
  scraper/           # ToS-respectful local-example pipeline (no live scraping)
  ingestion/uyap/    # UYAP evidence ingestion V1 (discovery→audit→explicit admission)
  tuik/              # TÜİK client
  db/                # SQLAlchemy models + schema
  cli.py             # `sold` command-line interface (incl. `sold structural ...`)
validation/
  structural/        # GENUINE audited structural seed (source_audited=true)
  real_records/      # manually-audited parser expectations (KAP / TOKİ / UYAP)
datasets/            # market-anchor data (auto-refreshed) + legacy ground_truth.csv
docs/                # DEVELOPMENT_HISTORY.md (superseded + frozen milestones)
scripts/             # helper scripts (data fetch, report)
tests/               # offline unit / end-to-end tests
```

## Testing

```bash
pytest -q             # 215 tests, fully offline (no network or API key required)
```

## Methodology & References

The methodology is a **structural econometric** one; the following foundations describe *what the code implements*, not a claim of published validation of this specific prototype:

- **Generalized Nash bargaining** — the closing price `P = η·B + (1 − η)·S` with trade iff `B ≥ S`, and `η` estimated rather than assumed.
- **Simulated Method of Moments (SMM)** — parameters fit by minimizing a weighted distance between observed and simulated moments; used because the ordinary-resale closing price is unobserved.
- **Housing search-and-bargaining models** — the economic framing of buyer/seller valuations, market tightness, and negotiated trade.
- **Local (moment-Jacobian) identification** — assessing identification numerically via the rank/conditioning of `∂m_sim/∂θ'`, and reporting structural underidentification honestly (`rank(J) = 4 < dim(θ) = 6`).
- **Set / near-fit sensitivity analysis** — reporting an admissible near-fit region (`Θ_A`) and prediction sensitivity across it, explicitly *not* a confidence region.
- **Cumulative, incumbent-preserving numerical-search diagnostics** — a common-threshold search-coverage study kept **separate** from econometric identification; `cumulative_best_objective` is monotone non-increasing across increasing budgets, and the current coverage is `INSUFFICIENT_COVERAGE`.
- **Appraisal-anchored (hedonic) fair value** — using TCMB appraisal levels as the fair-value anchor, with relative characteristic premiums only.
- **Source-specific mechanism moments** — UYAP completed-sale auctions and KAP negotiated corporate disposals contribute moments under their own mechanism; TOKİ is an external cross-mechanism benchmark.

> A formal, page-verified bibliography for these foundations is **future documentation work**; specific citations are intentionally not listed here rather than fabricated.

## Roadmap

The structural and prediction-semantics core is frozen. Current work focuses on expanding genuine evidence and testing how sensitive the structural conclusions are to data and assumptions.

- [ ] Expand the genuine, provenance-audited UYAP completed-auction evidence set
- [ ] Expand the genuine KAP negotiated-disposal evidence set
- [ ] Recompute observed moments, Jacobian diagnostics, and `Theta_A` after each evidence expansion batch
- [ ] Evaluate whether cumulative near-fit search coverage remains `INSUFFICIENT_COVERAGE` as evidence grows
- [ ] Run UYAP and KAP leave-one-out and source-removal robustness analyses
- [ ] Run structural-assumption sensitivity analyses for the asking-to-seller-signal specification, parameter bounds, distributional assumptions, and TCMB anchor perturbations
- [ ] Compare structural behavior with transparent baselines: asking price, fixed-markdown rules, and the TCMB fair-value anchor, without making unsupported accuracy claims
- [ ] Track how genuine evidence changes structural moments, Jacobian rank, near-fit parameter ranges, simulated trade-share behavior, and the structural sensitivity range
- [ ] Produce a reproducible research report documenting the model, public evidence, identification limits, numerical search diagnostics, robustness results, and limitations

_Completed pre-pivot and structural milestones are preserved in [docs/DEVELOPMENT_HISTORY.md](docs/DEVELOPMENT_HISTORY.md); the structural and prediction-semantics core and the numerical search-approximation layer are frozen._

## Legal & Ethics

- **Official automated data + manually-audited public records.** Automated fetching is limited to official market-data series (TCMB / TÜİK via EVDS). Structural evidence (UYAP completed-sale auctions, KAP disposals, TOKİ disclosures) is added by **manual audit of official public records** — the project *does* consult these individual public records, so it makes **no** claim that individual sale figures are never accessed.
- **No unauthorized scraping.** No listing portals or public registries are bulk-scraped; live source ingestion is deliberately out of scope.
- **Privacy (KVKK) & data minimization.** No personal data is collected. Party, representative, counsel, payment, account, and case-party fields are **excluded** from audited records; only non-personal economic, property, and public-record fields are retained.
- **Provenance integrity.** Genuine audited evidence is kept strictly separate from illustrative fixtures, demo seeds, and test data; no evidence is fabricated, and no title-deed *declared* value is treated as the consideration.
- **Honesty of claims.** The system **does not observe** the actual ordinary-resale closing price and never reports a measured ordinary-resale prediction accuracy; every price output is an explicitly `conditional_on_trade` structural inference.
- **Purpose.** Structural price transparency and methodological honesty, not tax enforcement or exposure.

## Contributing

Issues and pull requests are welcome. Please:

1. Open an issue to discuss significant changes first.
2. Keep the test suite green (`pytest`) and add tests for new behavior.
3. Never add scraped or fabricated data to the repository.

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE).

## Acknowledgements

- **TCMB EVDS** — appraisal-based price index and unit prices (fair-value level anchor).
- **TÜİK** — housing sales statistics (demand / liquidity context).
- **UYAP e-Satış** — audited completed-sale judicial auction records (structural SMM moments).
- **KAP** — audited non-related negotiated corporate-disposal disclosures (structural SMM moments).
- **TOKİ** — audited cumulative project-disclosure cohorts (external cross-mechanism benchmark).

> All institutional records used as structural evidence are **manually audited** from official public sources; none are bulk-scraped.