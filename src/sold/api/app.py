"""Değerleme API'si + YAPISAL ürün yüzeyi.

Uçlar:
- GET  /                    cilalı tek-sayfa YAPISAL değerleme arayüzü (Değerle / Model Evidence / Method)
- POST /structural/valuate  donmuş yapısal motorla çıkarımsal işlem-fiyatı dağılımı (makine-okunur meta)
- GET  /structural/evidence Model Evidence: gerçek kamu yapısal kanıtı (fixture/test'ten AYRI)
- GET  /structural/method   Method: mekanizma açıklaması (çıpa → moment → SMM → Θ_A → duyarlılık)
- POST /valuate             (legacy) ilan fiyatından realized tahmini
- GET  /kfe                 DB'deki gerçek KFE serisi (trend)
- GET  /discount-summary    etiketlerden gerçek indirim özeti
- GET  /health              sağlık kontrolü

Yapısal yüzey DONMUŞ ekonometrik çekirdeği (Nash pazarlık + SMM + admissible_near_fit_set)
yalnızca ÇAĞIRIR; ``confidence_interval`` / ``accuracy`` ASLA döndürülmez. Legacy /valuate
uçları geriye dönük uyumluluk için korunur.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..features.build import parse_room_count
from ..model.estimator import RealizedValuator
from ..model.valuation import RealValuator

MODEL_PATH = Path("data/models/valuator.joblib")
STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="sold — Gerçekleşen Konut Fiyatı Tahmini", version="0.1.0")
app.mount("/dashboard-assets", StaticFiles(directory=STATIC_DIR), name="dashboard-assets")

_valuator: "RealizedValuator | RealValuator | None" = None
_real = RealValuator()  # gerçek-veri motoru (piyasa değeri çıprazı + etiketsiz default)


def get_valuator():
    """GERÇEK satış etiketiyle eğitilmiş ML modeli varsa onu, yoksa gerçek-veri
    (yayınlı pazarlık payı) motorunu döndürür. Sentetik/mock ASLA servis edilmez."""
    global _valuator
    if _valuator is None:
        _valuator = RealizedValuator.load(MODEL_PATH) if MODEL_PATH.exists() else _real
    return _valuator


class PropertyIn(BaseModel):
    asking_price: float = Field(..., gt=0, description="İlan (istenen) fiyat, TL")
    province: str | None = "İstanbul"
    district: str | None = None
    neighborhood: str | None = None
    gross_m2: float | None = None
    room_count: str | None = None  # örn. "2+1"
    building_age: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    heating: str | None = None
    listing_type: str = "sale"
    lat: float | None = None
    lon: float | None = None
    days_on_market: int | None = None


class ValuationOut(BaseModel):
    asking_price: float
    realized_estimate: float
    implied_discount_pct: float
    province_avg_tl_m2: float | None = None
    basis: str = "yayınlı pazarlık payı + TCMB ekspertiz (gerçek veri)"


class SoldRecordIn(BaseModel):
    asking_price: float = Field(..., gt=0, description="İlan (istenen) fiyat, TL")
    sold_price: float = Field(..., gt=0, description="Gerçekleşen satış fiyatı, TL")
    province: str | None = "İstanbul"
    district: str | None = None
    neighborhood: str | None = None
    gross_m2: float | None = None
    room_count: str | None = None
    building_age: int | None = None
    floor: int | None = None
    heating: str | None = None
    source: str = "web"
    sale_date: str | None = None


def _to_frame(prop: PropertyIn) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "province": prop.province,
                "district": prop.district,
                "neighborhood": prop.neighborhood,
                "listing_type": prop.listing_type,
                "gross_m2": prop.gross_m2,
                "net_m2": None,
                "room_count_num": parse_room_count(prop.room_count),
                "building_age": prop.building_age,
                "floor": prop.floor,
                "total_floors": prop.total_floors,
                "heating": prop.heating,
                "lat": prop.lat,
                "lon": prop.lon,
                "initial_price": prop.asking_price,
                "last_price": prop.asking_price,
                "num_snapshots": 1,
                "num_price_changes": 0,
                "days_on_market": prop.days_on_market if prop.days_on_market is not None else 0,
                "total_drop_pct": 0.0,
            }
        ]
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/uyap-dashboard", include_in_schema=False)
def uyap_dashboard_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "uyap-dashboard.html", media_type="text/html")


@app.get("/uyap-data")
def uyap_dashboard_data_endpoint() -> dict:
    from .uyap_dashboard import dashboard_data

    return dashboard_data()


@app.post("/valuate", response_model=ValuationOut)
def valuate(prop: PropertyIn) -> ValuationOut:
    if prop.asking_price <= 0:
        raise HTTPException(status_code=422, detail="asking_price > 0 olmalı")
    valuator = get_valuator()
    estimate = float(valuator.estimate(_to_frame(prop))[0])
    discount = (1 - estimate / prop.asking_price) * 100 if prop.asking_price else 0.0
    mv = _real.province_ppm2(prop.province)
    basis = (
        "gerçek satış etiketleriyle eğitilmiş ML"
        if isinstance(valuator, RealizedValuator)
        else "yayınlı pazarlık payı + TCMB ekspertiz (gerçek veri)"
    )
    return ValuationOut(
        asking_price=prop.asking_price,
        realized_estimate=round(estimate, 0),
        implied_discount_pct=round(discount, 2),
        province_avg_tl_m2=round(mv, 0) if mv is not None else None,
        basis=basis,
    )


@app.get("/kfe")
def kfe(series: str = "TP.KFE.TR") -> dict:
    from ..db import get_engine, get_sessionmaker, init_db
    from ..model.calibrate import load_kfe_from_db

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        data = load_kfe_from_db(session, series)
    return {
        "series": series,
        "observations": [{"period": k, "index": v} for k, v in data.items()],
    }


@app.get("/discount-summary")
def discount_summary_endpoint() -> dict:
    from ..db import get_engine, get_sessionmaker, init_db
    from ..groundtruth import discount_summary, load_frame_from_db

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        frame = load_frame_from_db(session)
    if frame.empty:
        return {"count": 0, "message": "Ground-truth yok. `sold gt import` ile etiket ekleyin."}
    return discount_summary(frame)["overall"]


@app.post("/ground-truth")
def add_ground_truth(rec: SoldRecordIn) -> dict:
    import pandas as pd
    from sqlalchemy import func, select

    from ..db import get_engine, get_sessionmaker, init_db
    from ..db.models import GroundTruthSale
    from ..groundtruth import persist_to_db

    row = {
        "source": rec.source,
        "listing_type": "sale",
        "province": rec.province,
        "district": rec.district,
        "neighborhood": rec.neighborhood,
        "gross_m2": rec.gross_m2,
        "room_count": rec.room_count,
        "building_age": rec.building_age,
        "floor": rec.floor,
        "heating": rec.heating,
        "asking_price": rec.asking_price,
        "sold_price": rec.sold_price,
        "sale_date": rec.sale_date,
    }
    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        persist_to_db(session, pd.DataFrame([row]), source=rec.source)
        session.commit()
        total = session.scalar(select(func.count()).select_from(GroundTruthSale)) or 0

    discount = (1 - rec.sold_price / rec.asking_price) * 100 if rec.asking_price else 0.0
    return {"added": True, "total_labels": int(total), "discount_pct": round(discount, 2)}


class OutcomeIn(BaseModel):
    outcome: str = Field(
        ..., description="sold/withdrawn/expired/active/lost_to_other/unknown"
    )
    province: str | None = "İstanbul"
    district: str | None = None
    neighborhood: str | None = None
    gross_m2: float | None = None
    room_count: str | None = None
    building_age: int | None = None
    floor: int | None = None
    heating: str | None = None
    initial_asking_price: float | None = None
    last_asking_price: float | None = None
    price_cut_count: int | None = 0
    days_on_market: int | None = None
    # Kapanış — yalnızca outcome='sold' için anlamlı; diğerlerinde yok sayılır.
    sold_price: float | None = None
    sale_date: str | None = None
    days_to_close: int | None = None
    sale_mode: str | None = "arm_length"
    # Provenance / kanıt
    source: str = "web"
    label_source: str | None = None
    evidence_type: str | None = "none"


@app.post("/outcome")
def add_outcome(rec: OutcomeIn) -> dict:
    """İlan SONUCU kaydeder (flywheel). Kapanış alanları yalnızca 'sold'da tutulur."""
    from ..db import get_engine, get_sessionmaker, init_db
    from ..flywheel import OutcomeError, record_outcome

    engine = get_engine()
    init_db(engine)
    try:
        with get_sessionmaker(engine)() as session:
            row = record_outcome(session, rec.model_dump())
            session.commit()
            return {
                "recorded": True,
                "outcome": row.outcome,
                "sale_mode": row.sale_mode,
                "label_confidence": row.label_confidence,
                "label_source": row.label_source,
            }
    except OutcomeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/analytics")
def analytics_endpoint(source: str | None = None) -> dict:
    """Broker müzakere analitiği (NON-ML): indirim, kapanış süresi, fiyat-kesinti."""
    from ..db import get_engine, get_sessionmaker, init_db
    from ..flywheel import load_outcomes, negotiation_analytics

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        df = load_outcomes(session, source)
    return negotiation_analytics(df)


class ConsumerSaleIn(BaseModel):
    """Ev satmış tüketicinin öz-beyan satışı — KVKK: yalnızca nesnel alanlar.

    ``extra='forbid'``: tanımsız (ör. kişisel) alanlar API sınırında REDDEDİLİR.
    """

    model_config = ConfigDict(extra="forbid")

    final_asking_price: float = Field(..., gt=0, description="SON ilan fiyatı, TL")
    closing_price: float = Field(..., gt=0, description="Gerçekleşen satış fiyatı, TL")
    initial_asking_price: float | None = Field(None, description="İLK ilan fiyatı, TL")
    province: str | None = "İstanbul"
    district: str | None = None
    property_type: str | None = "konut"
    gross_m2: float | None = None
    room_count: str | None = None
    price_cut_count: int | None = 0
    listing_date: str | None = None
    closing_date: str | None = None


@app.post("/consumer/sale")
def add_consumer_sale(rec: ConsumerSaleIn) -> dict:
    """Tüketici satışını kaydeder → DOĞRUDAN asking→closing etiketi üretir.

    Anında NON-ML analitik + (yeterliyse) anonim segment benchmark döner. Kamu
    (UYAP/KAP/TOKİ) gözlemleri asking→closing head'inden HARİÇ kalır.
    """
    from ..consumer import (
        ConsumerSaleError,
        record_consumer_sale,
        sale_analytics,
        sale_as_dict,
        segment_benchmark,
    )
    from ..db import get_engine, get_sessionmaker, init_db

    engine = get_engine()
    init_db(engine)
    try:
        with get_sessionmaker(engine)() as session:
            # /consumer/sale GERÇEK ürün yoludur → origin varsayılan consumer_submission.
            row = record_consumer_sale(session, rec.model_dump())
            session.commit()
            sale = sale_as_dict(row)
            genuine = row.origin == "consumer_submission" and row.quality_status == "accepted"
            return {
                "recorded": True,
                "domain": "consumer",
                "label_source": "seller_self_reported",
                "sale_mechanism": "ordinary_resale",
                "reference_price_type": "asking",
                "label_confidence": "B",
                "origin": row.origin,
                "quality_status": row.quality_status,
                "quality_flags": list(row.quality_flags or []),
                "enters_asking_to_closing": bool(genuine),
                "analytics": sale_analytics(sale),
                "segment_benchmark": segment_benchmark(session, sale),
            }
    except ConsumerSaleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/consumer/stats")
def consumer_stats_endpoint() -> dict:
    """Doğrudan etiket durumu — GERÇEK (genuine) vs test/demo AYRI sayılır.

    ``genuine_accepted`` yalnızca origin=consumer_submission + quality=accepted; gerçek
    bir satıcı gönderimi gelene dek 0'dır. Fixture/demo bu sayıyı şişirmez.
    """
    from ..consumer import direct_label_counts, load_consumer_sales
    from ..db import get_engine, get_sessionmaker, init_db

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        sales = load_consumer_sales(session)
        counts = direct_label_counts(session)
    return {"consumer_sales": int(len(sales)), **counts}


class StructuralValuationIn(BaseModel):
    """Yapısal değerleme girdisi — YALNIZCA fair-value katmanının DESTEKLEDİĞİ alanlar.

    Şu an fair value TCMB il-bazlı ekspertiz TL/m² × brüt m² ile kurulur; ``district`` /
    ``room_count`` HENÜZ hedonik özellik katmanında olmadığından tahmini DEĞİŞTİRMEZ
    (sadece meta olarak kabul edilir; görsel tamlık için zorunlu girdi yapılmaz).
    """

    model_config = ConfigDict(extra="forbid")

    asking_price: float = Field(..., gt=0, description="İlan (asking) fiyatı, TL")
    province: str = Field("İstanbul", min_length=1)
    gross_m2: float | None = Field(100.0, gt=0, description="Brüt m²")
    tightness: float = 0.0


@app.post("/structural/valuate")
def structural_valuate(prop: StructuralValuationIn) -> dict:
    """Donmuş yapısal ekonometrik motorla ÇIKARIMSAL işlem-fiyatı dağılımı (gözlenen fiyat DEĞİL).

    Makine-okunur meta döner (methodology, identification_status, coverage_claim=None,
    central_structural_estimate, within/between belirsizlik, structural_sensitivity_range,
    input_conflict…). ``confidence_interval`` / ``accuracy`` alanı ASLA döndürülmez.
    """
    from .structural_product import structural_valuation

    if prop.asking_price <= 0:
        raise HTTPException(status_code=422, detail="asking_price > 0 olmalı")
    try:
        result = structural_valuation(
            prop.asking_price, province=prop.province, gross_m2=prop.gross_m2, tightness=prop.tightness
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=422,
            detail=f"{prop.province} için TCMB ekspertiz TL/m² bulunamadı (gross_m2 > 0 olmalı).",
        )
    return result


@app.get("/structural/evidence")
def structural_evidence() -> dict:
    """Model Evidence: gerçek kamu yapısal kanıtı (fixture/test'ten AYRI; test sayısı GÖSTERİLMEZ)."""
    from .structural_product import model_evidence

    return model_evidence()


@app.get("/structural/method")
def structural_method() -> dict:
    """Method: yapısal mekanizmanın özlü açıklaması (çıpa → moment → SMM → Θ_A → duyarlılık)."""
    from .structural_product import method_overview

    return method_overview()


@app.get("/structural/stability")
def structural_stability() -> dict:
    """Θ_A arama-bütçesi KARARLILIK tanılaması (sayısal; ekonometrik sınıflandırma DEĞİL).

    Artan aday yoğunluklarında parametre bant-payları + duyarlılık zarfını karşılaştırır →
    STABLE / SEARCH_SENSITIVE / INSUFFICIENT_COVERAGE. Tolerans/bound/moment değişmez.
    """
    from .structural_product import stability_snapshot

    try:
        return stability_snapshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Konut — Yapısal İşlem-Fiyatı Çıkarımı</title>
  <style>
    :root { --bg:#0f1419; --card:#fff; --ink:#1a2027; --muted:#5c6773; --line:#e3e8ee; --brand:#1a73e8; --warn:#b26a00; --warnbg:#fff8ec; --ok:#0b8043; }
    * { box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:0; color:var(--ink); background:#f5f7fa; }
    header { background:linear-gradient(135deg,#12233b,#1a73e8); color:#fff; padding:22px 20px; }
    header h1 { font-size:20px; margin:0; font-weight:650; }
    header p { margin:6px 0 0; font-size:13px; opacity:.9; }
    .wrap { max-width:760px; margin:0 auto; padding:0 16px 48px; }
    .tabs { display:flex; gap:4px; margin:18px 0 0; border-bottom:1px solid var(--line); }
    .tab { padding:10px 16px; cursor:pointer; font-size:14px; color:var(--muted); border-bottom:2px solid transparent; }
    .tab.active { color:var(--brand); border-bottom-color:var(--brand); font-weight:600; }
    .panel { display:none; } .panel.active { display:block; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin:16px 0; }
    label { display:block; margin:12px 0 4px; font-size:13px; color:var(--muted); }
    input, select { width:100%; padding:9px 10px; border:1px solid #ccd3db; border-radius:8px; font-size:15px; }
    button { margin-top:18px; padding:12px 20px; font-size:15px; font-weight:600; cursor:pointer; border:0; border-radius:8px; background:var(--brand); color:#fff; width:100%; }
    button:hover { background:#1666d0; }
    .estimate { text-align:center; padding:8px 0 4px; }
    .estimate .lbl { font-size:13px; color:var(--muted); }
    .estimate .big { font-size:34px; font-weight:700; margin:4px 0; color:var(--ink); }
    .row { display:flex; justify-content:space-between; padding:10px 0; border-top:1px solid var(--line); font-size:14px; }
    .row .k { color:var(--muted); } .row .v { font-weight:600; }
    .expl { font-size:12.5px; color:var(--muted); margin:2px 0 0; }
    .envelope { background:#eef4ff; border:1px solid #cfe0ff; border-radius:10px; padding:12px 14px; margin-top:12px; }
    .envelope .rng { font-size:19px; font-weight:700; color:#12233b; }
    .nocov { font-size:12.5px; color:#8a4b00; margin-top:6px; font-weight:600; }
    .status { margin-top:12px; padding:10px 12px; border-radius:8px; background:#fff4f4; border:1px solid #f3c9c9; font-size:13px; }
    .status b { color:#b00020; }
    .warn { margin-top:12px; padding:12px 14px; border-radius:8px; background:var(--warnbg); border:1px solid #f0d9a8; font-size:13px; color:#5f4400; }
    .warn .cats { margin:6px 0 0; font-size:12.5px; }
    .pill { display:inline-block; background:#eef1f5; border-radius:20px; padding:2px 10px; margin:2px 3px 0 0; font-size:12px; color:#3a4652; }
    .disc { font-size:12px; color:var(--muted); margin-top:22px; padding-top:14px; border-top:1px solid var(--line); line-height:1.55; }
    .muted { color:var(--muted); font-size:13px; }
    code { background:#f0f2f5; padding:1px 5px; border-radius:4px; font-size:12.5px; }
    .flow { list-style:none; padding:0; margin:0; }
    .flow li { padding:8px 0; border-top:1px solid var(--line); font-size:13.5px; }
    .flow li:first-child { border-top:0; }
    h2 { font-size:15px; margin:0 0 2px; }
  </style>
</head>
<body>
  <header>
    <h1>Konut — Yapısal İşlem-Fiyatı Çıkarımı</h1>
    <p>Kamu ekspertiz / açık artırma / müzakereli-satış kanıtına kalibre yapısal ekonomik model. Gözlenen kapanış fiyatı DEĞİL.</p>
  </header>
  <div class="wrap">
    <div class="tabs">
      <div class="tab active" data-tab="estimate">Değerle</div>
      <div class="tab" data-tab="evidence">Model Evidence</div>
      <div class="tab" data-tab="method">Method</div>
    </div>

    <div id="estimate" class="panel active">
      <div class="card">
        <h2>Yapısal değerleme</h2>
        <p class="muted">Fair-value katmanının şu an desteklediği alanlar (il + brüt m²). Desteklenmeyen alanlar görsel tamlık için gösterilmez.</p>
        <form id="valForm">
          <label>İlan (asking) fiyatı — TL</label>
          <input name="asking_price" type="number" value="5000000" required />
          <label>İl</label>
          <input name="province" value="İstanbul" />
          <label>Brüt m²</label>
          <input name="gross_m2" type="number" value="100" />
          <button type="submit">Estimate inferred transaction outcome</button>
        </form>
      </div>
      <div id="valResult"></div>
    </div>

    <div id="evidence" class="panel">
      <div class="card"><h2>Model Evidence</h2><div id="evBody" class="muted">Yükleniyor…</div></div>
    </div>

    <div id="method" class="panel">
      <div class="card"><h2>Method</h2><div id="mBody" class="muted">Yükleniyor…</div></div>
    </div>

    <div class="disc" id="disclaimer"></div>
  </div>

  <script>
    const tl = (x) => (x==null? '—' : Number(x).toLocaleString('tr-TR') + ' TL');
    const numF = ['asking_price','gross_m2','tightness'];
    function collect(form){ const fd=new FormData(form),b={}; for(const [k,v] of fd.entries()){ if(v!=='') b[k]=numF.includes(k)?Number(v):v; } return b; }

    document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
      t.classList.add('active'); document.getElementById(t.dataset.tab).classList.add('active');
      if(t.dataset.tab==='evidence') loadEvidence();
      if(t.dataset.tab==='method') loadMethod();
    }));

    function renderConflict(ic){
      if(!ic || !ic.input_conflict) return '';
      const cats = (ic.input_conflict_candidate_explanations||[]).map(c=>'<span class="pill">'+c+'</span>').join('');
      return '<div class="warn"><b>input_conflict</b> · ask/fair-value oranı = <b>'+ic.ask_to_fair_value_ratio+'</b>'+
        '<div>The asking-price signal strongly disagrees with the TCMB-anchored fair-value signal. '+
        'Structural inference may therefore be highly sensitive. The estimate was not rejected or silently clamped.</div>'+
        '<div class="cats">Olası açıklamalar (yalnızca aday — kanıtsız SEÇİLMEZ): '+cats+'</div></div>';
    }

    document.getElementById('valForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const el = document.getElementById('valResult');
      el.innerHTML = '<div class="card muted">Θ_A üzerinde simüle ediliyor…</div>';
      try {
        const r = await fetch('/structural/valuate', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(collect(e.target)) });
        const d = await r.json();
        if(!r.ok){ el.innerHTML = '<div class="card">Hata: '+(d.detail||'bilinmeyen')+'</div>'; return; }
        const ic = { input_conflict:d.input_conflict, ask_to_fair_value_ratio:d.ask_to_fair_value_ratio, input_conflict_candidate_explanations:d.input_conflict_candidate_explanations };
        const wt = d.within_theta_negotiation_interval, bt = d.between_theta_near_fit_band, sr = d.structural_sensitivity_range;
        const sh = d.simulated_trade_share_band || [null,null];
        const noTrade = (d.central_structural_estimate == null);
        const body = noTrade
          ? '<div class="status">Bu senaryoda Θ_A boyunca simüle ticaret payı ~0 → koşullu-ticaret fiyatı raporlanmaz (dürüst null). Monte Carlo ~0 payı, popülasyon ticaret olasılığının matematiksel olarak 0 olduğu anlamına GELMEZ.</div>'
          : ('<div class="row"><span class="k">Within-θ negotiation uncertainty</span><span class="v">'+tl(wt[0])+' – '+tl(wt[1])+'</span></div>' +
             '<div class="expl">Sabit bir yapısal parametre yapılandırmasında simüle alıcı/satıcı değerlerinin ve pazarlık sürecinin doğurduğu değişim.</div>' +
             '<div class="row"><span class="k">Between-θ near-fit uncertainty</span><span class="v">'+tl(bt[0])+' – '+tl(bt[1])+'</span></div>' +
             '<div class="expl">Ekonomik olarak kabul edilebilir birden çok yapısal parametre yapılandırması, mevcut kamu momentlerine neredeyse eşit iyi uyar.</div>' +
             '<div class="envelope"><div class="lbl muted">Structural sensitivity range (iki belirsizlik birlikte)</div>' +
             '<div class="rng">'+tl(sr[0])+' – '+tl(sr[1])+'</div>' +
             '<div class="nocov">'+d.no_coverage_statement+'</div></div>');
        el.innerHTML = '<div class="card">' +
          '<div class="estimate"><div class="lbl">Central structural estimate, conditional on simulated trade</div>' +
          '<div class="big">'+tl(d.central_structural_estimate)+'</div>' +
          '<div class="muted">İlan '+tl(d.asking_price)+' · fair value (TCMB çıpa) '+tl(d.fair_value)+'</div>' +
          '<div class="expl">'+d.conditional_on_trade_statement+'</div></div>' +
          renderConflict(ic) + body +
          '<div class="row"><span class="k">Model-implied simulated trade-share band</span><span class="v">'+(sh[0]==null?'—':(sh[0]+' – '+sh[1]))+'</span></div>' +
          '<div class="expl">Monte Carlo simüle B≥S payı — GÖZLENEN UYAP no-trade sonuçlarına KALİBRE DEĞİL; satış olasılığı / satış ihtimali DEĞİL.</div>' +
          '<div class="row"><span class="k">Near-fit configs (trading / total)</span><span class="v">'+d.trading_near_fit_parameter_count+' / '+d.near_fit_parameter_count+'</span></div>' +
          '<div class="status">Identification status: <b>'+d.identification_status+'</b> · Jacobian rank '+d.jacobian_rank+' / '+d.parameter_dimension+' · near-fit search: '+d.near_fit_search_stability+
          '<div class="expl">The available genuine public moments do not uniquely determine all structural parameters, so the system reports sensitivity across near-fit parameter configurations instead of pretending one parameter vector is known exactly.</div></div>' +
          '</div>';
      } catch(err){ el.innerHTML = '<div class="card">İstek başarısız: '+err+'</div>'; }
    });

    async function loadEvidence(){
      const el = document.getElementById('evBody');
      try {
        const d = await (await fetch('/structural/evidence')).json();
        el.innerHTML =
          '<div class="row"><span class="k">UYAP completed-sale auctions</span><span class="v">'+d.genuine_uyap_observations+'</span></div>' +
          '<div class="row"><span class="k">KAP negotiated disposals</span><span class="v">'+d.genuine_kap_observations+'</span></div>' +
          '<div class="row"><span class="k">TOKİ external cross-mechanism benchmark moments</span><span class="v">'+d.toki_external_moments+'</span></div>' +
          '<div class="row"><span class="k">SMM moments used</span><span class="v">'+d.n_smm_moments_used+'</span></div>' +
          '<div class="expl">'+d.smm_moments_used.map(m=>'<code>'+m+'</code>').join(' ')+'</div>' +
          '<div class="row"><span class="k">Identification</span><span class="v">'+d.identification_status+' · rank '+d.jacobian_rank+'/'+d.parameter_dimension+'</span></div>' +
          '<p class="expl">• '+d.explanations.uyap+'</p>' +
          '<p class="expl">• '+d.explanations.kap+'</p>' +
          '<p class="expl">• '+d.explanations.toki+'</p>' +
          '<p class="expl">• uyap_sale_prob: '+d.excluded_from_smm.uyap_sale_prob+'</p>';
      } catch(err){ el.textContent = 'Yüklenemedi: '+err; }
    }

    async function loadMethod(){
      const el = document.getElementById('mBody');
      try {
        const d = await (await fetch('/structural/method')).json();
        const steps = d.pipeline.map(s=>'<li>'+s+'</li>').join('');
        const defs = Object.entries(d.definitions).map(([k,v])=>'<div class="row"><span class="k"><code>'+k+'</code></span><span class="v">'+v+'</span></div>').join('');
        const clar = d.clarifications.map(c=>'<p class="expl">• '+c+'</p>').join('');
        el.innerHTML = '<ul class="flow">'+steps+'</ul>'+defs+
          '<p class="expl" style="margin-top:10px">'+d.conflict_bounds_note+'</p>'+clar+
          '<p class="expl">'+d.identification.future_methodology_note+'</p>';
      } catch(err){ el.textContent = 'Yüklenemedi: '+err; }
    }

    (async () => {
      try { const d = await (await fetch('/structural/method')).json(); document.getElementById('disclaimer').textContent = d.disclaimer; } catch(e){}
    })();
  </script>
</body>
</html>
"""
