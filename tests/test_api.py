"""Değerleme API'si testleri (FastAPI TestClient)."""

from __future__ import annotations

import json

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


# --- Yapısal ürün yüzeyi (final productization; DONMUŞ çekirdek) ------------ #
@pytest.fixture(scope="module")
def structural_ready():
    """Yapısal önbelleği KÜÇÜK aday sayısıyla kurar (test hızı) — ekonometrik çekirdek değişmez."""
    import sold.api.structural_product as sp

    sp.NEAR_FIT_CANDIDATES = 500
    sp.reset_near_fit_cache()
    yield
    sp.reset_near_fit_cache()


def test_structural_evidence_reports_genuine_counts(structural_ready):
    d = client.get("/structural/evidence").json()
    assert d["genuine_uyap_observations"] == 2
    assert d["genuine_kap_observations"] == 2
    assert d["toki_external_moments"] == 5
    assert set(d["smm_moments_used"]) == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
    assert d["jacobian_rank"] == 4 and d["parameter_dimension"] == 6
    # test sayıları (ör. "192 passed") KANIT olarak GÖSTERİLMEZ
    assert "passed" not in json.dumps(d)
    # açıklamalar: UYAP koşullu, KAP kurumsal (ordinary resale GT DEĞİL), TOKİ external (0 moment)
    assert "conditional" in d["explanations"]["uyap"].lower()
    assert "not ordinary residential resale ground truth" in d["explanations"]["kap"].lower()
    assert "zero moments" in d["explanations"]["toki"].lower()


def test_structural_method_view(structural_ready):
    d = client.get("/structural/method").json()
    assert any("trade iff B >= S" in s for s in d["pipeline"])
    assert any("P = eta * B" in s for s in d["pipeline"])
    assert d["definitions"]["eta"] == "seller bargaining power"
    assert d["definitions"]["B"].startswith("buyer") and d["definitions"]["S"].startswith("seller")
    joined = " ".join(d["clarifications"]).lower()
    assert "not directly measured from kap" in joined  # eta KAP'tan doğrudan ölçülmez
    assert "not the auction reserve" in joined          # UYAP appraised value rezerv DEĞİL
    assert "configurable" in d["conflict_bounds_note"].lower()  # [0.5,2.0] YAPILANDIRILABİLİR


def test_structural_valuate_machine_readable(structural_ready):
    d = client.post("/structural/valuate", json={
        "asking_price": 7_500_000, "province": "İstanbul", "gross_m2": 100,
    }).json()
    assert d["methodology"] == "structural_econometrics"
    assert d["identification_status"] == "STRUCTURALLY_UNDERIDENTIFIED"
    assert d["coverage_claim"] is None
    for k in ("central_structural_estimate", "within_theta_negotiation_interval",
              "between_theta_near_fit_band", "structural_sensitivity_range",
              "ask_to_fair_value_ratio", "input_conflict", "input_conflict_warning",
              "genuine_uyap_observations", "genuine_kap_observations", "toki_external_moments",
              "jacobian_rank", "parameter_dimension", "near_fit_parameter_count"):
        assert k in d
    # YASAK alanlar YOK
    assert "confidence_interval" not in d and "accuracy" not in d
    assert d["jacobian_rank"] == 4 and d["parameter_dimension"] == 6


def test_structural_valuate_input_conflict_warns_not_clamped(structural_ready):
    d = client.post("/structural/valuate", json={
        "asking_price": 30_000_000, "province": "İstanbul", "gross_m2": 100,  # oran > 2
    }).json()
    assert d["input_conflict"] is True and d["ask_to_fair_value_ratio"] > 2.0
    assert d["input_conflict_warning"]
    # olası açıklamalar YALNIZCA 6 aday kategorisi (kanıtsız seçilmez)
    assert len(d["input_conflict_candidate_explanations"]) == 6
    # KIRPILMADI/reddedilmedi: yapısal alanlar + coverage_claim=None yine döner
    assert "structural_sensitivity_range" in d and d["coverage_claim"] is None


def test_structural_index_page_action_wording(structural_ready):
    html = client.get("/").text
    assert "Konut" in html                                   # index korunur (legacy test)
    assert "Estimate inferred transaction outcome" in html   # doğru birincil eylem
    assert "Structural sensitivity range" in html
    # YASAKLI etiketler GÖSTERİLMEZ
    for forbidden in ("Predict actual sale price", "observed closing price", "true sale price"):
        assert forbidden not in html


def test_search_budget_stability_reproducible():
    # (8) arama-bütçesi kararlılık tanısı YENİDEN-ÜRETİLEBİLİR (aynı bütçe/seed → aynı sonuç)
    from sold.api.structural_product import search_budget_stability

    a = search_budget_stability(budgets=(200, 400))
    b = search_budget_stability(budgets=(200, 400))
    assert a["near_fit_search_stability"] == b["near_fit_search_stability"]
    assert a["cumulative_best_objective"] == b["cumulative_best_objective"]
    assert [r["cumulative_best_objective"] for r in a["table"]] == [r["cumulative_best_objective"] for r in b["table"]]
    assert a["near_fit_search_stability"] in ("STABLE", "SEARCH_SENSITIVE", "INSUFFICIENT_COVERAGE")
    # sayısal tanı; ekonometrik sınıflandırma DEĞİL (rank/moment değişmez)
    assert "identification classification" in a["note"].lower()


def test_cumulative_stability_monotone_and_incumbent_preserving():
    # (1)+(2) kümülatif best MONOTON AZALMAYAN + deterministik yeniden-değerlendirme
    from sold.api.structural_product import search_budget_stability

    st = search_budget_stability(budgets=(200, 400, 800))
    assert st["cumulative_best_objective_monotone_nonincreasing"] is True
    cb = st["cumulative_best_objective"]
    assert all(cb[i + 1] <= cb[i] + 1e-9 for i in range(len(cb) - 1))
    assert st["deterministic_objective_reproducible"] is True
    # ortak Q_ref/tol_ref alanları mevcut (hareketli eşik YOK)
    assert "Q_ref" in st and "tol_ref" in st
    # her satır production + common-threshold sayımlarını AYRI raporlar
    for r in st["table"]:
        assert "production_near_fit_count" in r and "common_threshold_stability_near_fit_count" in r


def test_stability_classification_not_solely_count_growth():
    # (6) sınıflandırma yalnızca yakın-uyum SAYISI büyümesine dayanmaz
    from sold.api.structural_product import _classify_stability

    ranges = {p: [0.0, 0.1] for p in ("mu_b", "sigma_b", "mu_s", "sigma_s", "eta", "auction_shift")}
    prev = {"common_threshold_param_ranges": ranges, "structural_sensitivity_range": [100.0, 200.0],
            "between_theta_near_fit_band": [120.0, 180.0], "central_structural_estimate": 150.0}
    last = {"common_threshold_param_ranges": ranges, "structural_sensitivity_range": [101.0, 201.0],
            "between_theta_near_fit_band": [121.0, 181.0], "central_structural_estimate": 151.0}  # ×4 sayı ama kararlı
    exp = {"cumulative_best_objective": [0.100, 0.099]}  # yakınsamış (<%10 iyileşme)
    status, _ = _classify_stability([prev, last], exp)
    assert status == "STABLE"  # sayı ×4 büyüse de tahmin/destek kararlı → STABLE (yalnızca sayıya DAYANMAZ)


def test_underidentification_separate_from_search_stability():
    # (7) STRUCTURALLY_UNDERIDENTIFIED, near_fit_search_stability'den AYRI (biri diğerini kanıtlamaz)
    from sold.api.structural_product import search_budget_stability

    st = search_budget_stability(budgets=(200, 400))
    assert "identification_separation" in st
    assert "does not establish underidentification" in st["identification_separation"].lower()
    assert st["near_fit_search_stability"] in ("STABLE", "SEARCH_SENSITIVE", "INSUFFICIENT_COVERAGE")

