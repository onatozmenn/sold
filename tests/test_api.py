"""Değerleme API'si testleri (FastAPI TestClient)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sold.api.app import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Konut" in r.text


def test_valuate_returns_reasonable_estimate():
    r = client.post(
        "/valuate",
        json={
            "asking_price": 3_200_000,
            "district": "Kadıköy",
            "gross_m2": 120,
            "room_count": "2+1",
            "building_age": 5,
            "floor": 3,
            "heating": "Kombi (Doğalgaz)",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["realized_estimate"] > 0
    # gerçekleşen tahmin, ilan fiyatının makul bir aralığında olmalı
    assert 1_000_000 < data["realized_estimate"] < 5_000_000
    assert -20 < data["implied_discount_pct"] < 60


def test_valuate_rejects_bad_price():
    r = client.post("/valuate", json={"asking_price": 0})
    assert r.status_code == 422


def test_add_ground_truth(tmp_path, monkeypatch):
    db = tmp_path / "labels.db"
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{db.as_posix()}",
        raising=False,
    )
    r = client.post(
        "/ground-truth",
        json={"asking_price": 3_000_000, "sold_price": 2_800_000, "district": "Kadıköy"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["added"] is True
    assert data["total_labels"] == 1
    assert data["discount_pct"] == pytest.approx(6.67, abs=0.05)


def test_outcome_and_analytics(tmp_path, monkeypatch):
    db = tmp_path / "flywheel.db"
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{db.as_posix()}",
        raising=False,
    )
    r = client.post(
        "/outcome",
        json={
            "outcome": "sold",
            "province": "İstanbul",
            "last_asking_price": 3_000_000,
            "sold_price": 2_700_000,
            "price_cut_count": 1,
            "days_to_close": 40,
            "sale_mode": "arm_length",
        },
    )
    assert r.status_code == 200
    assert r.json()["label_confidence"] == "B"  # broker öz-beyanı otomatik A değil

    r2 = client.post(
        "/outcome",
        json={
            "outcome": "withdrawn",
            "province": "İstanbul",
            "last_asking_price": 1_500_000,
            "sold_price": 999,  # kapanış alanı yok sayılmalı
        },
    )
    assert r2.status_code == 200
    assert r2.json()["outcome"] == "withdrawn"

    r3 = client.get("/analytics")
    assert r3.status_code == 200
    data = r3.json()
    assert data["transaction_count"] == 1
    assert data["total_outcomes"] == 2


def test_outcome_sold_requires_price(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'fw2.db').as_posix()}",
        raising=False,
    )
    assert client.post("/outcome", json={"outcome": "sold"}).status_code == 422


def test_outcome_invalid_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'fw3.db').as_posix()}",
        raising=False,
    )
    assert client.post("/outcome", json={"outcome": "banana"}).status_code == 422


def test_consumer_sale_creates_direct_label_and_analytics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'consumer.db').as_posix()}",
        raising=False,
    )
    r = client.post(
        "/consumer/sale",
        json={
            "initial_asking_price": 4_000_000,
            "final_asking_price": 3_800_000,
            "closing_price": 3_500_000,
            "province": "İstanbul",
            "district": "Kadıköy",
            "property_type": "konut",
            "listing_date": "2024-01-01",
            "closing_date": "2024-02-20",
            "price_cut_count": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["recorded"] is True
    assert data["sale_mechanism"] == "ordinary_resale"
    assert data["label_confidence"] == "B"
    assert data["origin"] == "consumer_submission"  # API = GERÇEK ürün yolu
    assert data["quality_status"] == "accepted"
    assert data["enters_asking_to_closing"] is True
    assert data["analytics"]["days_to_close"] == 50
    assert data["segment_benchmark"]["enough_observations"] is False  # tek gözlem

    # Ürünün kendi edinim yolundan GERÇEK (genuine) bir doğrudan etiket sayıldı
    s = client.get("/consumer/stats")
    assert s.status_code == 200
    body = s.json()
    assert body["genuine_accepted"] == 1
    assert body["test_demo"] == 0


def test_consumer_sale_flagged_stays_out_of_head(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'consumer_flag.db').as_posix()}",
        raising=False,
    )
    # Aşırı closing/asking oranı → RED DEĞİL, flagged (kızgın piyasa gibi görünse de incele)
    r = client.post(
        "/consumer/sale",
        json={"final_asking_price": 3_800_000, "closing_price": 5_400_000},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["quality_status"] == "flagged"
    assert data["enters_asking_to_closing"] is False
    s = client.get("/consumer/stats").json()
    assert s["genuine_accepted"] == 0  # flagged genuine-accepted DEĞİL
    assert s["genuine_flagged"] == 1


def test_consumer_sale_rejects_structurally_impossible(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'consumer_bad.db').as_posix()}",
        raising=False,
    )
    # kapanış tarihi ilan tarihinden ÖNCE → yapısal red (422)
    r = client.post(
        "/consumer/sale",
        json={
            "final_asking_price": 3_800_000,
            "closing_price": 3_500_000,
            "listing_date": "2024-03-01",
            "closing_date": "2024-01-01",
        },
    )
    assert r.status_code == 422


def test_consumer_sale_rejects_extra_personal_field(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sold.config.settings.database_url",
        f"sqlite:///{(tmp_path / 'consumer2.db').as_posix()}",
        raising=False,
    )
    # extra='forbid': tanımsız (kişisel) alan API sınırında reddedilir
    r = client.post(
        "/consumer/sale",
        json={
            "final_asking_price": 3_800_000,
            "closing_price": 3_500_000,
            "seller_name": "Ahmet",
        },
    )
    assert r.status_code == 422

