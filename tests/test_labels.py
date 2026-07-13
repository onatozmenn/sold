"""PublicLabelMiner + birleşik etiket registry testleri (domain ayrımı dahil)."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base
from sold.labels import (
    KAPAdapter,
    LabelError,
    PublicLabelMiner,
    TOKIAdapter,
    UYAPAdapter,
    asking_to_closing_labels,
    confidence_for,
    fair_value_labels,
    load_labels,
    normalize_label,
    persist_labels,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


_UYAP = {
    "ihale_sonucu": "satıldı",
    "muhammen_bedel": 5_000_000,
    "ihale_bedeli": 5_400_000,
    "il": "İstanbul",
    "ilce": "Kadıköy",
    "tasinmaz_turu": "konut",
    "brut_m2": 120,
    "ihale_tarihi": "2026-03-15",
    "dosya_no": "2025/123",
}
_KAP = {
    "toplam_satis_bedeli": 5_508_474.60,
    "degerleme_raporu_hazirlandi": True,
    "degerleme_tutari": 5_200_000,
    "iliskili_taraf": False,
    "il": "İstanbul",
    "ilce": "Şişli",
    "brut_m2": 140,
    "islem_tarihi": "2026-02-10",
    "kap_id": "X1",
}


# ---- güven ----------------------------------------------------------------- #
def test_confidence_for_sources():
    assert confidence_for("uyap") == "A"
    assert confidence_for("kap") == "A"
    assert confidence_for("toki") == "A"
    assert confidence_for("seller_self_reported") == "B"
    assert confidence_for("broker_closing") == "B"
    assert confidence_for("deed_declared") == "C"


# ---- normalize ------------------------------------------------------------- #
def test_normalize_rejects_invalid():
    with pytest.raises(LabelError):
        normalize_label({"domain": "banana", "sale_mechanism": "auction", "reference_price_type": "appraisal", "realized_price": 1})
    with pytest.raises(LabelError):
        normalize_label({"domain": "kap", "sale_mechanism": "x", "reference_price_type": "appraisal", "realized_price": 1})
    with pytest.raises(LabelError):  # realized zorunlu
        normalize_label({"domain": "kap", "sale_mechanism": "corporate_negotiated_non_related", "reference_price_type": "appraisal"})


# ---- adapterlar ------------------------------------------------------------ #
def test_uyap_adapter():
    label = UYAPAdapter().parse(_UYAP)
    assert label["domain"] == "uyap"
    assert label["sale_mechanism"] == "auction"
    assert label["reference_price_type"] == "appraisal"
    assert label["reference_price"] == 5_000_000
    assert label["realized_price"] == 5_400_000
    assert normalize_label(label)["label_confidence"] == "A"


def test_uyap_adapter_skips_unsold():
    assert UYAPAdapter().parse({"ihale_sonucu": "satılmadı", "muhammen_bedel": 5_000_000}) is None
    assert UYAPAdapter().parse({"ihale_sonucu": "satıldı"}) is None  # bedel yok


def test_uyap_adapter_uses_public_record_id_as_external_ref():
    record = {
        "public_record_id": "16975383463",
        "appraised_value": 5_000_000,
        "winning_bid": 5_400_000,
        "auction_date": "2026-06-30",
    }

    label = UYAPAdapter().parse(record)
    assert label["external_ref"] == "16975383463"
    assert label["transaction_date"] == "2026-06-30"


def test_kap_adapter():
    label = KAPAdapter().parse(_KAP)
    assert label["domain"] == "kap"
    assert label["sale_mechanism"] == "corporate_negotiated_non_related"
    assert label["reference_price_type"] == "appraisal"  # güncel yapısal değerleme
    assert label["reference_price"] == 5_200_000
    assert label["realized_price"] == 5_508_474.60
    assert label["related_party"] is False


def test_kap_adapter_prior_appraisal_from_structured_field():
    # Operatör önceki ekspertizi prior_appraisal_value'ya ÇIKARMIŞ (adapter metin ayrıştırmaz).
    # Yapısal güncel değerleme BOŞ + rapor hazırlanmamış → prior_appraisal referans türü.
    label = KAPAdapter().parse(
        {
            "toplam_satis_bedeli": 5_508_474.60,
            "degerleme_raporu_hazirlandi": False,
            "degerleme_tutari": None,
            "prior_appraisal_value": 5_200_000,
            "iliskili_taraf": False,
            "deger_belirleme_yontemi": "negotiation",
        }
    )
    assert label["reference_price_type"] == "prior_appraisal"  # GÜNCEL değil
    assert label["reference_price"] == 5_200_000
    assert label["sale_mechanism"] == "corporate_negotiated_non_related"
    assert label["value_method"] == "negotiation"


def test_kap_adapter_related_party_and_no_reference():
    label = KAPAdapter().parse({"toplam_satis_bedeli": 1_000_000, "iliskili_taraf": True})
    assert label["sale_mechanism"] == "corporate_related_party"
    assert label["reference_price_type"] == "none"  # ne güncel ne önceki değerleme
    assert label["reference_price"] is None


def test_toki_adapter_auction_and_project():
    auction = TOKIAdapter().parse(
        {"kind": "auction", "muhammen_bedel_toplam": 50_125_000, "teklif_toplam": 53_970_000, "proje": "Park Mavera III"}
    )
    assert auction["sale_mechanism"] == "public_auction"
    assert auction["reference_price_type"] == "reserve"
    assert auction["realized_price"] == 53_970_000

    proj = TOKIAdapter().parse(
        {"kind": "project_avg", "offered_avg": 6_000_000, "realized_avg": 5_700_000, "proje": "Damla Kent"}
    )
    assert proj["sale_mechanism"] == "primary_market"
    assert proj["reference_price_type"] == "offered_avg"
    assert proj["realized_price"] == 5_700_000


# ---- miner ----------------------------------------------------------------- #
def test_miner_mine_and_skip():
    labels = PublicLabelMiner().mine("kap", [_KAP, {"toplam_satis_bedeli": 0}])
    assert len(labels) == 1  # ikincisi (bedel yok) atlanır
    assert labels[0]["domain"] == "kap"


def test_miner_unknown_source():
    with pytest.raises(LabelError):
        PublicLabelMiner().mine("sahibinden", [])


def test_mine_file_json(tmp_path):
    p = tmp_path / "recs.json"
    p.write_text(json.dumps([_UYAP]), encoding="utf-8")
    labels = PublicLabelMiner().mine_file("uyap", p)
    assert len(labels) == 1
    assert labels[0]["label_source"] == "uyap"


# ---- DOMAIN AYRIMI (metodolojik çekirdek) ---------------------------------- #
def _mixed_labels() -> pd.DataFrame:
    return pd.DataFrame(
        [
            normalize_label(UYAPAdapter().parse(_UYAP)),
            normalize_label(KAPAdapter().parse(_KAP)),
            normalize_label(
                {"domain": "broker", "sale_mechanism": "arm_length", "reference_price_type": "asking", "label_source": "broker_closing", "reference_price": 6_500_000, "realized_price": 5_950_000}
            ),
            normalize_label(
                {"domain": "consumer", "sale_mechanism": "arm_length", "reference_price_type": "asking", "label_source": "seller_self_reported", "reference_price": 6_250_000, "realized_price": 6_050_000, "origin": "consumer_submission"}
            ),
        ]
    )


def test_asking_to_closing_excludes_public_domains():
    df = _mixed_labels()
    a2c = asking_to_closing_labels(df)
    assert len(a2c) == 2  # yalnızca broker + seller (doğrudan closing)
    assert set(a2c["label_source"]) == {"broker_closing", "seller_self_reported"}
    # UYAP/KAP ASLA girmez
    assert "uyap" not in set(a2c["label_source"])
    assert "kap" not in set(a2c["label_source"])


def test_public_domain_cannot_be_disguised_as_direct_closing_label():
    with pytest.raises(LabelError, match="Public evidence domains"):
        normalize_label({
            "domain": "uyap",
            "label_source": "manual",
            "sale_mechanism": "arm_length",
            "reference_price_type": "asking",
            "reference_price": 100,
            "realized_price": 90,
        })


def test_fair_value_labels_are_public_domains():
    df = _mixed_labels()
    fv = fair_value_labels(df)
    assert len(fv) == 2  # uyap + kap (appraisal)
    assert set(fv["sale_mechanism"]) == {"auction", "corporate_negotiated_non_related"}


# ---- kalıcılık ------------------------------------------------------------- #
def test_persist_and_load_labels():
    session = _session()
    labels = [
        normalize_label(UYAPAdapter().parse(_UYAP)),
        normalize_label(KAPAdapter().parse(_KAP)),
    ]
    n = persist_labels(session, labels)
    session.commit()
    assert n == 2
    df = load_labels(session)
    assert len(df) == 2
    assert set(df["domain"]) == {"uyap", "kap"}


def test_persist_labels_is_idempotent_for_source_identity():
    session = _session()
    label = normalize_label(UYAPAdapter().parse(_UYAP))

    assert persist_labels(session, [label, label]) == 1
    session.commit()
    assert persist_labels(session, [label]) == 0
    session.commit()
    assert len(load_labels(session)) == 1


def test_fair_value_strata_not_pooled():
    """FairValue etiketleri (mechanism, reference_price_type) ile AYRI kalır — havuzlanmaz."""
    from sold.labels import fair_value_strata

    df = pd.DataFrame(
        [
            normalize_label(UYAPAdapter().parse(_UYAP)),  # auction / appraisal
            normalize_label(KAPAdapter().parse(_KAP)),  # corporate_negotiated_non_related / appraisal
            normalize_label(
                TOKIAdapter().parse(
                    {"kind": "auction", "muhammen_bedel_toplam": 50_000_000, "teklif_toplam": 54_000_000}
                )
            ),  # public_auction / reserve
            normalize_label(
                TOKIAdapter().parse(
                    {"kind": "project_avg", "offered_avg": 6_000_000, "realized_avg": 5_700_000}
                )
            ),  # primary_market / offered_avg
        ]
    )
    strata = fair_value_strata(df)
    assert set(strata.keys()) == {
        ("uyap", "auction", "appraisal"),
        ("kap", "corporate_negotiated_non_related", "appraisal"),
        ("toki", "public_auction", "reserve"),
        ("toki", "primary_market", "offered_avg"),
    }
    assert all(len(group) == 1 for group in strata.values())  # hiçbir strata havuzlanmadı
