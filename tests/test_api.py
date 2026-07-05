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
