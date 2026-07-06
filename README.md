# sold

[![CI](https://github.com/onatozmenn/sold/actions/workflows/ci.yml/badge.svg)](https://github.com/onatozmenn/sold/actions/workflows/ci.yml)
[![Data refresh](https://github.com/onatozmenn/sold/actions/workflows/kfe-refresh.yml/badge.svg)](https://github.com/onatozmenn/sold/actions/workflows/kfe-refresh.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-54%20passing-brightgreen.svg)](tests/)
[![Data](https://img.shields.io/badge/data-TCMB%20%C2%B7%20T%C3%9C%C4%B0K-informational.svg)](#data-sources)

> Infer the **realized transaction price** of a Turkish home from its **asking** price — a provenance-aware valuation engine.

**sold** infers the **realized transaction price** of a Turkish home — the price it *actually* changes hands for — from its **asking** price. Because listing portals publish only asking prices and real transaction prices are public nowhere in Türkiye, the central challenge is **not** modeling but **label acquisition**. `sold` is therefore built as a *provenance-aware* inference engine: it learns the gap between asking and closing prices from sparse, trust-tagged transaction labels, and falls back to a transparent, official-data baseline (TCMB appraisal levels + TÜİK demand + published negotiation margins) until those labels arrive. No fabricated data is ever served.

## Table of Contents

- [Background](#background)
- [How It Works](#how-it-works)
- [Broker Data Flywheel](#broker-data-flywheel)
- [Public Label Bootstrap](#public-label-bootstrap)
- [Data Sources](#data-sources)
- [Labels & Provenance](#labels--provenance)
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

Consequently, automated valuation models (AVMs) trained naively on listings are biased upward, and there is no public label for "what it really sold for."

### The approach

The missing data is not *scraped* — it is *inferred*, the way the industry does (e.g. Endeksa) and the way the academic literature validates (see [References](#methodology--references)). The relationship of interest is the **sale-to-list ratio**:

> **realized price = asking price × (1 − negotiation margin)**

The honest framing is that **this is a label-acquisition problem, not a modeling problem.** The engine has two regimes:

- **With paired labels** — when real `asking → closing` records exist, a machine-learning model *learns* the margin from them, conditioned on overpricing, time-on-market, price cuts, liquidity, and location.
- **Without labels (fallback)** — the margin defaults to a **published prior** (İstanbul ≈ 10%, Ankara ≈ 5%, İzmir ≈ 8%), demand-adjusted via TÜİK volumes. This is a *baseline*, not ground truth — a fixed per-city rate is only ever a starting point, since the sale-to-list ratio moves with the market cycle (homes can even close **above** asking in hot markets).

An independent **appraisal-based value** (TCMB TL/m² × area) is provided as a cross-check.

## How It Works

```mermaid
flowchart TD
    subgraph FEATURES["Real feature data - weekly GitHub Actions, no PC"]
        B1["kfe.csv - price trend"]
        B2["house_sales.csv - demand"]
        B3["unit_prices.csv - appraisal TL/m2"]
    end

    subgraph MINER["Public Label Miner - observed public transactions"]
        U["UYAP (auction)"]
        K["KAP (corporate)"]
        T["TOKI / GYO (primary)"]
    end

    subgraph FLYWHEEL["Broker / Seller Flywheel - direct closing"]
        O["listing_outcomes: Sold / Withdrawn / Expired / Active / Lost / Unknown"]
        AN["Negotiation analytics"]
        O --> AN
        AN -. value returned .-> O
    end

    U & K & T --> REG["Provenance-aware label registry (domain + mechanism kept separate)"]
    O --> REG
    REG --> FV["FairValue calibration (appraisal / reserve -> realized)"]
    REG --> A2C["asking -> closing head (ONLY direct-closing, arm's-length)"]

    FEATURES --> C{RealValuator}
    FV --> C
    A2C --> C
    C -->|no paired labels| E["Fallback prior (published margin + demand)"]
    C -->|paired labels| Fm["RealizedValuator - ML (hedonic + discount)"]
    E & Fm --> G["Estimated realized price + provenance-aware confidence"]
    G --> H["CLI - REST API - web form"]
```

**Two-tier engine**

| Mode | When | What it does |
|------|------|--------------|
| `RealValuator` | Default (no labels yet) | `asking × (1 − published discount)`, demand-adjusted, with a TCMB TL/m² cross-check |
| `RealizedValuator` | Once you add real sold labels | Two-stage ML (hedonic price + sale-to-list discount) trained on your ground truth |

No synthetic or mock data is ever served. The simulator (`synthetic.py`) exists solely to unit-test the ML method.

### Model decomposition

The problem naturally splits into three models; conflating them is what makes naive AVMs biased:

| Model | Question | Status |
|---|---|---|
| **FairValue** `V(x, t)` | What is the home worth *before* negotiation? | appraisal-anchored (TCMB TL/m²) |
| **SaleProbability** `P(sold ≤ N days)` | Will this listing actually sell — or just be withdrawn? | roadmap |
| **ClosingDiscount** `log(closing / asking)` | How far from asking does it truly close? | fallback prior → ML on real labels |

A delisted listing is **not** necessarily a sale (the seller may have withdrawn, relisted, or switched agents), so `removed = sold` is deliberately avoided.

## Broker Data Flywheel

The scarce input — a paired `asking → closing` label — is collected through a **listing-outcome pipeline**, not a sold-only form. A broker (or the app) records the *outcome* of a listing and receives free **negotiation analytics** in return. That exchange is the flywheel, and it is a **first-class source for `RealValuator`**.

- **Outcomes collected:** `sold` · `withdrawn` · `expired` · `active` · `lost_to_other` · `unknown`. Closing-price fields appear **only** for `sold` — a delisting is never assumed to be a sale (this later trains **SaleProbability**).
- **Honest confidence:** a broker's self-reported closing does **not** get confidence `A`. It defaults to `B`, and is promoted to `A` only when independently verified (`evidence_verified`); declared deed values are `C`. Schema fields `evidence_type` / `evidence_verified` exist; document upload is intentionally deferred.
- **Analytics returned (non-ML, immediate):** transaction count, median & mean asking-to-closing discount, days to close, price-cut count, and discount split by price-cut status. The same function runs on a broker's own records and on the aggregate dataset, so **broker-vs-benchmark** comparison is ready to switch on once enough anonymized data accumulates.

```bash
sold flywheel record sold --province İstanbul --last-asking 3200000 \
     --sold-price 2900000 --price-cuts 1 --days-to-close 40
sold flywheel record withdrawn --province İzmir --last-asking 1500000
sold flywheel analytics
```

Equivalent REST endpoints: `POST /outcome` and `GET /analytics`. Sold arm's-length outcomes feed **ClosingDiscount**; all outcomes feed **SaleProbability** — the two stay separate conceptual components.

## Public Label Bootstrap

Rather than waiting on institutional access, `sold` can turn **operator-supplied** official public records into provenance-aware realized-price labels via a `PublicLabelMiner` with per-source adapters. This is a **parser layer, not continuous ingestion** — you hand it a record you already downloaded (an auction result, a KAP disclosure) and the adapter normalizes it. Nothing is discovered, fetched, or ingested automatically, and labels do **not** "flow in" on their own.

| Source | Adapter | Reference → Realized | Mechanism | Confidence |
|---|---|---|---|---|
| **UYAP** e-satış (judicial auction) | `UYAPAdapter` | appraisal → winning bid | `auction` | A |
| **KAP** (corporate disclosures) | `KAPAdapter` | appraisal *or* prior-appraisal → sale value | `corporate_negotiated_non_related` | A |
| **TOKİ / GYO** paired auction | `TOKIAdapter` | reserve → winning bid (same lot) | `public_auction` / `primary_market` | A |
| **TOKİ / GYO** project disclosure (**unpaired**) | `ProjectDisclosureAdapter` | *aggregate populations — not a pair* | `aggregation_level = cohort` | A |

Every label lands in a single **provenance-aware registry** with mandatory `domain`, `label_source`, `sale_mechanism`, and `reference_price_type` fields. `domain` is the **source-domain** axis (`kap` / `uyap` / `toki` / `broker` / `consumer`) so source-domain bias stays measurable, while `sale_mechanism` carries the economic mechanism separately. Two provenance subtleties are enforced by the KAP adapter. **Provenance boundary:** the **operator** manually extracts the structured fields (including `prior_appraisal_value`) from the audited disclosure representation — the adapter does **not** parse raw KAP free text. Given those structured fields it distinguishes a **current structured valuation** (`degerleme_raporu_hazirlandi` + `degerleme_tutari` → `appraisal`) from an operator-extracted **prior appraisal** (`prior_appraisal_value` → `prior_appraisal`), deriving the normalized `reference_price_type` and preserving its provenance. Second, it never calls a sale `arm_length` on the basis of `related_party = false` alone — it uses the more defensible `corporate_negotiated_non_related`.

**Not every official record is a paired label.** Some disclosures report **aggregate populations that must not be paired**. The TOKİ/GYO project disclosure *“Projede Benzer Nitelikte Olan Bağımsız Bölümlerin Ortalama Satış Fiyatları”* gives an **offered-inventory** average over one set of units and a **cumulative-realized-sales** average over a different, larger set — they are **different populations**, so turning them into a `reference_price → realized_price` pair (or computing a “closing discount” between them) would fabricate a relationship that isn’t in the data. These records are therefore **not** coerced into `RealizedLabel`; a separate **aggregate observation** abstraction ([`labels/aggregates.py`](src/sold/labels/aggregates.py)) represents each population on its own row — `aggregation_level = cohort`, `comparison_scope = unpaired_aggregate`, `observation_role ∈ {offered_inventory, cumulative_realized_sales}`, `project_id`, `as_of_date` — preserving room-type strata. It carries **no** `realized_price` / `reference_price` fields by construction, so it can never enter `asking_to_closing_labels()`.

### Three levels of validation, kept distinct

1. **Parser / adapter validation** — unit tests confirm each adapter maps fields to the schema correctly, on **illustrative fixtures**. ✅ done.
2. **Real-record validation** — the operator downloads one real official record per source, feeds its non-personal fields through the parser, and commits the **manually-audited expected output** (never the raw artifact) under [`validation/real_records/`](validation/real_records/). [`tests/test_real_records.py`](tests/test_real_records.py) then enforces `parser output == audited expectation` and pins `parser_version`. **Status: harness enforcing; all three real-record cases validated — zero skips.** (a) KAP notification `963554` ([`validation/real_records/kap.json`](validation/real_records/kap.json), a manually-audited Şişli/İstanbul disposal where the structured valuation is empty, so the operator-extracted `prior_appraisal_value` drives a `prior_appraisal` classification). (b) The TOKİ **Park Mavera III** project disclosure ([`validation/real_records/toki.json`](validation/real_records/toki.json)), validated as **two unpaired aggregate observations** (offered inventory vs cumulative realized sales) under a separate `AGGREGATE_PARSER_VERSION` — explicitly *not* a paired label and *not* a closing discount. (c) A **UYAP** completed judicial e-Satış auction ([`validation/real_records/uyap.json`](validation/real_records/uyap.json), an Ankara/Yenimahalle commercial unit): court appraisal `4,500,000 → 4,545,000` winning bid, `sale_mechanism = auction`, `reference_price_type = appraisal`, excluded from `asking_to_closing_labels()`. Its audit surfaced a real area-field trap — `509 m²` is the **cadastral parcel** surface and `32.5 m²` the unit's **net** usable area, so `gross_m2` stays **null** (509 is *not* injected); `gross_m2` was already optional, so no schema change was needed, and a per-record `compared_fields` asserts the null while `parcel_area_m2`/`unit_net_m2` are preserved as distinct provenance.
3. **Live source ingestion** — continuously fetching new records per source. ⬜ not built (a ToS-reviewed operator step, deliberately out of scope).

> Level-1 **illustrative fixtures** (e.g. [samples/labels/illustrative_kap.json](samples/labels/illustrative_kap.json)) use invented placeholder values to exercise the parser — the UYAP `5,000,000 → 5,400,000` figure is illustrative only. Level-2 **real-record** cases live separately under `validation/real_records/`, each with a manifest and manually-audited expected output — the validated ones are the real KAP disclosure **`963554`**, the TOKİ **Park Mavera III** project disclosure, and a **UYAP** completed judicial auction (Ankara/Yenimahalle). The two tiers are kept strictly separate and never conflated.

### Domains are never pooled

- `asking_to_closing_labels()` feeds the **asking → closing** ML head **only** with direct-closing observations (broker / seller, `reference = asking`, arm's-length). UYAP / KAP / TOKİ are **excluded**.
- `fair_value_labels()` is a **registry query, not a training set**. The four public relationships — appraisal→corporate-sale, appraisal→auction, reserve→auction, offered_avg→primary-market — are **distinct** and must not be pooled into one target. Use `fair_value_strata()` (splits by `domain` × `sale_mechanism` × `reference_price_type`, so source-domain bias stays measurable) and calibrate each stratum with its own model.

```bash
sold labels mine kap --file samples/labels/illustrative_kap.json --to-db
sold labels stats     # counts by domain / mechanism / confidence + the domain split
```

> **On collection:** the adapters *parse official records you provide*; live fetching is intentionally not shipped, consistent with the project's no-scraping stance.

## Data Sources

The datasets below are **features** (market context), not the prediction target. All are fetched from the official **TCMB EVDS** API and refreshed automatically; nothing is scraped. The prediction *label* — a paired `asking → closing` price — is separate and provenance-tracked (see [Labels & Provenance](#labels--provenance)).

| Dataset | Source | Meaning | Coverage |
|---|---|---|---|
| `datasets/kfe.csv` | TCMB | Residential Property Price Index (trend) | 2010 → now, monthly |
| `datasets/house_sales.csv` | TÜİK via EVDS | House sales counts (demand / liquidity) | 2013 → now, monthly, by province |
| `datasets/unit_prices.csv` | TCMB | Appraisal-based unit prices (TL/m²) | 2013 → now, quarterly, 77 provinces |
| `datasets/ground_truth.csv` | You | Real asking → sold examples (optional labels) | user-provided |

Published negotiation margins are used **only as a fallback prior** (İstanbul ≈ 10%, Ankara ≈ 5%, İzmir ≈ 8%), when no paired labels exist — see [References](#methodology--references).

## Labels & Provenance

The one genuinely scarce input is a *paired* `asking → closing` label. Not all labels are equally trustworthy, so every record in `datasets/ground_truth.csv` carries its provenance:

| Column | Meaning |
|---|---|
| `sale_mode` | `arm_length` · `auction` · `related_party` · `unknown` — non-arm's-length sales are excluded from the negotiation model |
| `label_source` | `broker_closing` · `bank_transfer_observed` · `deed_declared` · `uyap` · `manual` |
| `label_confidence` | `A` (observed transfer / broker closing) · `B` (manual) · `C` (declared deed value — understated) |

> A title-deed *declared* value is **not** the true consideration; it is systematically understated for tax reasons. The target is the **verified consideration** — the money that actually changes hands. Candidate high-quality label sources (in progress) include broker closings and TKGM Tapu Güvenilir Hesap bank-transfer records.

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

### Estimate a sale price (CLI)

```console
$ sold model value 3200000 --province İstanbul --gross-m2 120
İlan: 3,200,000 TL  (İstanbul, 120 m²)
Tahmini satış: 2,880,000 TL   (yayınlı pazarlık ~%10)      # est. sale ≈ 2.88M (~10% below asking)
Bu ilan: 26,667 TL/m²  ·  İstanbul ort. (TCMB): 79,306 TL/m²
→ İlan, il ortalamasının %66 altında.                      # listing is 66% below the provincial average
```

### Run the web app / REST API

```bash
sold serve            # → http://127.0.0.1:8000  (web form + REST endpoints)
```

`POST /valuate` returns the estimate as JSON; `GET /` serves a simple form.

### Refresh the real data (requires `EVDS_API_KEY`)

```bash
sold evds kfe          --out datasets/kfe.csv
sold evds house-sales  --out datasets/house_sales.csv
sold evds unit-prices  --out datasets/unit_prices.csv
```

### Add real sold labels (lets the model learn)

```bash
sold gt add ...                       # or edit datasets/ground_truth.csv directly
sold gt analyze                       # negotiation statistics from your own data
sold model evaluate --source gt --folds 5
```

### Validate the ML method on simulated data (not a real prediction)

```bash
sold model demo
```

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
  evds/              # TCMB EVDS client: KFE, house sales, unit prices
  features/          # demand signal (market_heat) + feature builder
  model/             # valuation (real engine), estimator (ML), synthetic (tests only)
  groundtruth/       # real-label loading + analysis
  scraper/           # ToS-respectful pipeline (local demo only)
  db/                # SQLAlchemy models + PostGIS schema
  api/app.py         # FastAPI service + web form
  cli.py             # `sold` command-line interface
datasets/            # real, version-controlled data (auto-refreshed)
scripts/             # helper scripts (data fetch, report)
tests/               # offline unit / end-to-end tests
.github/workflows/   # CI + data refresh + report
```

## Testing

```bash
pytest -q             # 54 tests, fully offline (no network or API key required)
```

## Methodology & References

Using listings plus a published margin (instead of unavailable transaction data) is an established, peer-reviewed method:

- *Real estate listings and their usefulness for hedonic regressions* — Springer, 2021.
- *Aggregated Housing Price Predictions with No Information About Transactions* (Warsaw) — MDPI, 2024.

Negotiation-margin figures from Turkish market reporting: İstanbul ≈ 10%, Ankara ≈ 5%, İzmir ≈ 8%, rising to 15–20% in slow or high-inventory markets. Drivers include building age, distance to centre, and local inventory — the latter captured here via TÜİK sales volume.

## Roadmap

- [x] Real TCMB/TÜİK data pipeline (KFE, sales, TL/m²) with weekly auto-refresh
- [x] Fallback valuation engine (published margin prior + demand adjustment)
- [x] Provenance-aware ground-truth labels with automatic ML takeover
- [x] **Broker Data Flywheel** — listing-outcome collection + non-ML negotiation analytics
- [x] **Public Label Bootstrap** — provenance-aware registry + UYAP/KAP/TOKİ adapters with strict domain separation, plus an unpaired **aggregate observation** abstraction for cohort disclosures (TOKİ project averages)
- [x] **Real-record (Level-2) validation** — three independent official records validated against the parsers with manually-audited expected output (KAP `963554`, TOKİ Park Mavera III, UYAP `16766356960`); zero skips, `parser_version`-pinned, raw artifacts never committed
- [ ] **SaleProbability** model (`P(sold ≤ N days)`) trained on collected outcomes
- [ ] Live, ToS-reviewed fetchers for the public label sources
- [ ] Broker-vs-benchmark analytics over an aggregate anonymized dataset
- [ ] Institutional label sources — TKGM Tapu Güvenilir Hesap, GABİM/TADEBİS, TÜİK microdata
- [ ] Public dashboard (GitHub Pages)

## Legal & Ethics

- **No scraping.** Only official APIs (TCMB / TÜİK via EVDS) are used; individual sold prices are never accessed.
- **Privacy (KVKK).** No personal data (names, phone numbers) is collected — only objective property attributes.
- **Purpose.** Valuation accuracy and price transparency, not tax enforcement or exposure.

## Contributing

Issues and pull requests are welcome. Please:

1. Open an issue to discuss significant changes first.
2. Keep the test suite green (`pytest`) and add tests for new behavior.
3. Never add scraped or fabricated data to the repository.

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE).

## Acknowledgements

- **TCMB EVDS** — appraisal-based price index and unit prices.
- **TÜİK** — housing sales statistics.
- Turkish real-estate market reporting for published negotiation margins.
