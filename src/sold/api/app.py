"""Değerleme API'si: ilan (asking) fiyatından gerçekleşen fiyat tahmini.

Uçlar:
- GET  /                 basit web arayüzü (form)
- POST /valuate          tek bir ilan için realized tahmini
- GET  /kfe              DB'deki gerçek KFE serisi (trend)
- GET  /discount-summary etiketlerden gerçek indirim özeti
- GET  /health           sağlık kontrolü

Varsayılan tahmin GERÇEK veriyle çalışır (MOCK YOK): ilan fiyatı × (1 − yayınlı
pazarlık payı), TCMB ekspertiz TL/m² çıprazıyla. GERÇEK satış etiketi (ground_truth)
eklenip ML modeli üretilirse (RealizedValuator), otomatik devralınır.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..features.build import parse_room_count
from ..model.estimator import RealizedValuator
from ..model.valuation import RealValuator

MODEL_PATH = Path("data/models/valuator.joblib")

app = FastAPI(title="sold — Gerçekleşen Konut Fiyatı Tahmini", version="0.1.0")

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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sold — Konut Değerleme</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; }
    h1 { font-size: 22px; }
    h2 { font-size: 16px; margin: 0 0 4px; }
    .card { border: 1px solid #e2e6ea; border-radius: 10px; padding: 16px 18px; margin: 16px 0; }
    label { display: block; margin: 10px 0 2px; font-size: 13px; color: #444; }
    input { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }
    button { margin-top: 16px; padding: 10px 18px; font-size: 15px; cursor: pointer; border: 0; border-radius: 6px; background: #1a73e8; color: #fff; }
    .result { margin-top: 16px; padding: 14px; background: #f4f6f8; border-radius: 8px; display: none; }
    .big { font-size: 26px; font-weight: 600; margin: 6px 0; }
    .muted { color: #666; font-size: 13px; }
    #labelbar { margin: 8px 0 4px; }
  </style>
</head>
<body>
  <h1>Gerçekleşen Konut Fiyatı Tahmini</h1>
  <div id="labelbar" class="muted">Etiket özeti yükleniyor…</div>

  <div class="card">
    <h2>1) Değerle</h2>
    <p class="muted">İlan fiyatı + özelliklerden tahmini gerçekleşen fiyatı hesaplar.</p>
    <form id="valForm">
      <label>İlan fiyatı (TL)</label>
      <input name="asking_price" type="number" value="3200000" required />
      <label>İlçe</label><input name="district" value="Kadıköy" />
      <label>Brüt m²</label><input name="gross_m2" type="number" value="120" />
      <label>Oda</label><input name="room_count" value="2+1" />
      <label>Bina yaşı</label><input name="building_age" type="number" value="5" />
      <label>Kat</label><input name="floor" type="number" value="3" />
      <label>Isıtma</label><input name="heating" value="Kombi (Doğalgaz)" />
      <button type="submit">Tahmin et</button>
    </form>
    <div id="valResult" class="result"></div>
  </div>

  <div class="card">
    <h2>2) Gerçek satış ekle (etiket)</h2>
    <p class="muted">Bildiğin GERÇEK bir satışı ekle — model bunlarla öğrenir. Kişisel veri (ad/telefon) girme.</p>
    <form id="gtForm">
      <label>İlan fiyatı (TL)</label><input name="asking_price" type="number" required />
      <label>Gerçek satış fiyatı (TL)</label><input name="sold_price" type="number" required />
      <label>İlçe</label><input name="district" />
      <label>Brüt m²</label><input name="gross_m2" type="number" />
      <label>Oda</label><input name="room_count" />
      <label>Bina yaşı</label><input name="building_age" type="number" />
      <button type="submit">Etiketi ekle</button>
    </form>
    <div id="gtResult" class="result"></div>
  </div>

  <script>
    const tl = (x) => Number(x).toLocaleString('tr-TR');
    const numFields = ['asking_price', 'sold_price', 'gross_m2', 'building_age', 'floor'];
    function collect(form) {
      const fd = new FormData(form); const body = {};
      for (const [k, v] of fd.entries()) { if (v !== '') body[k] = numFields.includes(k) ? Number(v) : v; }
      return body;
    }
    async function loadSummary() {
      try {
        const r = await fetch('/discount-summary'); const d = await r.json();
        const bar = document.getElementById('labelbar');
        if (d.count && d.count > 0) {
          bar.innerHTML = 'Toplam etiket: <b>' + d.count + '</b> · ortalama gerçek indirim: <b>%' + d.mean_pct.toFixed(1) + '</b>';
        } else {
          bar.textContent = 'Henüz gerçek etiket yok — aşağıdan ekleyebilirsin.';
        }
      } catch (e) { /* yoksay */ }
    }
    document.getElementById('valForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const el = document.getElementById('valResult'); el.style.display = 'block'; el.innerHTML = 'Hesaplanıyor…';
      try {
        const r = await fetch('/valuate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collect(e.target)) });
        const d = await r.json();
        if (!r.ok) { el.innerHTML = 'Hata: ' + (d.detail || 'bilinmeyen'); return; }
        el.innerHTML = '<div class="muted">İlan: ' + tl(d.asking_price) + ' TL</div>' +
          '<div class="big">Tahmini gerçekleşen: ' + tl(d.realized_estimate) + ' TL</div>' +
          '<div class="muted">Örtük indirim: %' + d.implied_discount_pct + '</div>';
      } catch (err) { el.innerHTML = 'İstek başarısız: ' + err; }
    });
    document.getElementById('gtForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const el = document.getElementById('gtResult'); el.style.display = 'block'; el.innerHTML = 'Kaydediliyor…';
      try {
        const r = await fetch('/ground-truth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collect(e.target)) });
        const d = await r.json();
        if (!r.ok) { el.innerHTML = 'Hata: ' + (d.detail || 'bilinmeyen'); return; }
        el.innerHTML = '<div class="muted">Eklendi \u2713 (%' + d.discount_pct + ' indirim). Toplam etiket: <b>' + d.total_labels + '</b></div>';
        e.target.reset(); loadSummary();
      } catch (err) { el.innerHTML = 'İstek başarısız: ' + err; }
    });
    loadSummary();
  </script>
</body>
</html>
"""
