"""UYAP TOPLU (bulk) keşif + iterasyon katmanı — OFFLINE testler (canlı UYAP oturumu GEREKMEZ).

Kaydırma/tıklama/canlı DOM yolları ``# pragma: no cover``dır; burada SAF orkestrasyon çekirdeği
(tarih pencereleri, page-0-hariç sayfalama, Satıldı-yalnız süzme, kart-yerel eşleme, uzun sayfa,
kalıcılık/yeniden-başlama, oturum-sona-ermesi, mevcut boru hattını yeniden kullanım) fixture/mock
ile test edilir. Yapısal ekonometrik çekirdeğin donmuş olduğu ayrıca doğrulanır.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from sold.ingestion.uyap import bulk, store
from sold.ingestion.uyap.models import (
    AUDIT_DECISIONS,
    STATE_AUDITED,
    STATE_COLLECTED,
    STATE_DISCOVERED,
    STATE_EXCLUDED,
    STATE_PENDING_REVIEW,
)


# --------------------------------------------------------------------------- #
# 1) Tarih penceresi üretimi — deterministik, boşluksuz, örtüşmesiz, ≤7 gün.
# --------------------------------------------------------------------------- #
def test_date_windows_canonical_seven_day_inclusive():
    ws = bulk.generate_date_windows("2025-01-01", "2025-01-21")
    assert ws == [
        {"start": "2025-01-01", "end": "2025-01-07"},
        {"start": "2025-01-08", "end": "2025-01-14"},
        {"start": "2025-01-15", "end": "2025-01-21"},
    ]


def test_date_windows_no_gaps_no_overlaps():
    ws = bulk.generate_date_windows("2025-03-03", "2025-04-10")
    for a, b in zip(ws, ws[1:]):
        prev_end = dt.date.fromisoformat(a["end"])
        next_start = dt.date.fromisoformat(b["start"])
        assert next_start == prev_end + dt.timedelta(days=1)  # boşluk/örtüşme yok
    # her pencere ≤ 7 gün
    for w in ws:
        span = dt.date.fromisoformat(w["end"]) - dt.date.fromisoformat(w["start"])
        assert span.days + 1 <= 7


def test_date_windows_final_window_may_be_shorter():
    ws = bulk.generate_date_windows("2025-01-01", "2025-01-10")
    assert ws[-1] == {"start": "2025-01-08", "end": "2025-01-10"}


def test_date_windows_month_year_and_leap_transitions():
    # ay geçişi
    ws = bulk.generate_date_windows("2025-01-28", "2025-02-05")
    assert ws[0] == {"start": "2025-01-28", "end": "2025-02-03"}
    # yıl geçişi (5 gün → tek pencere)
    wy = bulk.generate_date_windows("2024-12-30", "2025-01-03")
    assert wy == [{"start": "2024-12-30", "end": "2025-01-03"}]
    # artık yıl (29 Şubat 2024 pencere içinde)
    wl = bulk.generate_date_windows("2024-02-25", "2024-03-05")
    assert wl[0] == {"start": "2024-02-25", "end": "2024-03-02"}
    assert wl[1] == {"start": "2024-03-03", "end": "2024-03-05"}


def test_date_windows_reject_reversed_range():
    with pytest.raises(ValueError):
        bulk.generate_date_windows("2025-02-01", "2025-01-01")


def test_dense_window_splits_without_gaps_or_overlap():
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    children = bulk.split_date_window(window)
    assert children == [
        {"start": "2026-06-01", "end": "2026-06-04"},
        {"start": "2026-06-05", "end": "2026-06-07"},
    ]
    assert bulk.should_split_result_window(
        window, {"result_count": 200, "total_pages": 10, "per_page": 20}
    ) is True
    assert bulk.should_split_result_window(
        {"start": "2026-06-01", "end": "2026-06-01"},
        {"result_count": 200, "total_pages": 10, "per_page": 20},
    ) is False
    assert bulk.should_split_result_window(
        window, {}, valid_pages=list(range(1, 11))
    ) is True
    assert bulk.result_window_saturated({}, valid_pages=list(range(1, 11))) is True


def test_uyap_ui_date_format_matches_observed():
    assert bulk.format_uyap_ui_date("2026-06-10") == "10/06/2026"
    assert bulk.format_uyap_ui_date("2026-06-17") == "17/06/2026"


# --------------------------------------------------------------------------- #
# 2) Geçerli sayfalama süzme — GÖZLENEN page 0 asla seçilmez.
# --------------------------------------------------------------------------- #
def test_valid_pages_excludes_page_zero():
    assert bulk.valid_result_pages(["0", "1", "2", "Sonraki"]) == [1, 2]
    assert bulk.valid_result_pages(["Önceki", "0", "1", "2"]) == [1, 2]


def test_valid_pages_page_zero_alone_yields_nothing():
    assert bulk.valid_result_pages(["0"]) == []


def test_valid_pages_ignores_non_numeric_and_dedupes():
    assert bulk.valid_result_pages(["Önceki", "1", "2", "2", "3", "Sonraki", "..."]) == [1, 2, 3]


def test_valid_pages_never_contains_zero():
    for labels in (["0", "1"], ["0", "0", "2"], ["Sonraki", "0"]):
        assert 0 not in bulk.valid_result_pages(labels)


def test_live_page_enumeration_fills_metadata_and_count_gaps():
    class Item:
        def __init__(self, label):
            self.label = label

        def inner_text(self):
            return self.label

    class Locator:
        def __init__(self, labels):
            self.labels = labels

        def count(self):
            return len(self.labels)

        def nth(self, index):
            return Item(self.labels[index])

    class Page:
        def __init__(self, labels):
            self.labels = labels

        def locator(self, selector):
            return Locator(self.labels)

    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222")
    assert collector._valid_pages(
        Page(["1"]), {"total_pages": 2, "result_count": 40, "per_page": 20}
    ) == [1, 2]
    assert collector._valid_pages(
        Page(["2"]), {"total_pages": None, "result_count": 40, "per_page": 20}
    ) == [1, 2]


# --------------------------------------------------------------------------- #
# 3) Satıldı-kart süzme — POZİTİF "Satıldı"; diğerleri atlanır; çıkarım YOK.
# --------------------------------------------------------------------------- #
def test_sold_status_positive_only():
    assert bulk.is_sold_status("Satıldı") is True
    assert bulk.is_sold_status("Satıldı Satış İşlemleri Tamamlandı") is True
    assert bulk.is_sold_status("İhale Sonucu Girilmemiştir") is False
    assert bulk.is_sold_status("Malın Satışının Düşmesi") is False
    assert bulk.is_sold_status("") is False


def test_classify_card_status_categories():
    assert bulk.classify_card_status("Satıldı")["category"] == "SOLD"
    assert bulk.classify_card_status("İhale Sonucu Girilmemiştir")["category"] == "RESULT_NOT_ENTERED"
    assert bulk.classify_card_status("Malın Satışının Düşmesi")["category"] == "SALE_DROPPED"
    # bilinmeyen kaynak durumu → satıldı ÇIKARILMAZ
    unknown = bulk.classify_card_status("Beklemede")
    assert unknown["sold"] is False and unknown["category"] == "OTHER"


def test_sold_not_inferred_from_price_or_incele():
    # fiyat/İncele/yeşil tutar varlığı satıldı YAPMAZ (yalnız durum metni belirler)
    assert bulk.is_sold_status("1.234.567 TL İncele") is False


# --------------------------------------------------------------------------- #
# 4) Kart-yerel eşleme — kimlik + durum AYNI karttan (A'nın durumu B ile eşleşmez).
# --------------------------------------------------------------------------- #
_TWO_CARDS_HTML = """
<div class="results">
  <div class="card">
    <span>KAYIT NO: 16701234</span>
    <span>Ankara 12. İcra Dairesi</span>
    <span>2026/263 Esas</span>
    <span class="status">Satıldı</span><span>Satış İşlemleri Tamamlandı</span>
    <button>İncele</button><button>İhale Evrak Listesi</button>
  </div>
  <div class="card">
    <span>KAYIT NO: 16709999</span>
    <span>Ankara 5. İcra Dairesi</span>
    <span>2026/999 Esas</span>
    <span class="status">İhale Sonucu Girilmemiştir</span>
    <button>İncele</button><button>İhale Evrak Listesi</button>
  </div>
</div>
"""


def test_card_local_status_identity_pairing():
    cards = bulk.parse_result_cards(_TWO_CARDS_HTML)
    assert len(cards) == 2
    by_fid = {c["file_id"]: c for c in cards}
    # SATILDI durumu SADECE 2026/263 kartına bağlanır; 2026/999 satılmamış
    assert by_fid["2026/263"]["sold"] is True
    assert by_fid["2026/999"]["sold"] is False
    assert by_fid["2026/263"]["kayit_no"] == "16701234"
    assert by_fid["2026/999"]["kayit_no"] == "16709999"
    # kaynak durum metni AYNEN korunur
    assert "Satıldı" in (by_fid["2026/263"]["source_status_raw"] or "")


def test_only_sold_cards_are_targets():
    cards = bulk.parse_result_cards(_TWO_CARDS_HTML)
    sold = [c for c in cards if c["sold"]]
    assert [c["file_id"] for c in sold] == ["2026/263"]


def test_multi_identity_container_not_treated_as_card():
    # tüm-liste container (birden çok distinkt dosya) TEK kart sayılmaz
    cards = bulk.parse_result_cards(_TWO_CARDS_HTML)
    assert all(len(_distinct_file_ids(c["card_text"])) == 1 for c in cards)


def _distinct_file_ids(text: str) -> set:
    import re

    return {re.sub(r"\s+", "", n) for n in re.findall(r"\b\d{3,4}\s*/\s*\d+\b", text)}


# gerçek UYAP sonuç kartı: <li class="incelenen-li {KAYIT_NO}"> ... KAYIT NO class'ta
_INCELENEN_HTML = """
<ul>
  <li class="incelenen-li 16760761856"><i class="incelenen-icon fa fa-asterisk"></i>
    <article class="box"><figure class="col-sm-5"></figure>
      <div class="details">2026/263 Esas · Ankara 12. İcra Dairesi · Satıldı · Satış İşlemleri Tamamlandı</div>
    </article></li>
  <li class="incelenen-li 16760761999"><i class="incelenen-icon fa fa-asterisk"></i>
    <article class="box"><div class="details">2026/999 Esas · Ankara 5. İcra Dairesi · İhale Sonucu Girilmemiştir</div></article></li>
</ul>
"""


def test_parse_result_cards_incelenen_li_kayit_no_from_class():
    cards = bulk.parse_result_cards(_INCELENEN_HTML)
    by_fid = {c["file_id"]: c for c in cards}
    assert set(by_fid) == {"2026/263", "2026/999"}
    # KAYIT NO metinde yok → kart elementinin class'ından alınır
    assert by_fid["2026/263"]["kayit_no"] == "16760761856"
    assert by_fid["2026/999"]["kayit_no"] == "16760761999"
    # kart-yerel durum: yalnız 2026/263 Satıldı
    assert by_fid["2026/263"]["sold"] is True
    assert by_fid["2026/999"]["sold"] is False



# --------------------------------------------------------------------------- #
# 5) Uzun sonuç sayfası — görünür alanın altındaki kartlar da işlenir (koordinat YOK).
# --------------------------------------------------------------------------- #
def test_long_result_page_all_cards_parsed():
    rows = []
    for i in range(25):
        status = "Satıldı" if i % 2 == 0 else "İhale Sonucu Girilmemiştir"
        rows.append(
            f'<div class="card"><span>KAYIT NO: 1670{i:04d}</span>'
            f"<span>Ankara {i}. İcra Dairesi</span><span>2026/{100 + i} Esas</span>"
            f'<span>{status}</span><button>İhale Evrak Listesi</button></div>'
        )
    html = '<div class="results">' + "".join(rows) + "</div>"
    cards = bulk.parse_result_cards(html)
    assert len(cards) == 25                                  # üstteki+ortadaki+alttaki hepsi
    assert sum(1 for c in cards if c["sold"]) == 13          # 0,2,...,24


# --------------------------------------------------------------------------- #
# 6) Sayfalama durum doğrulaması — kontrol noktası yalnız açıkça işaretlenince ilerler.
# --------------------------------------------------------------------------- #
def test_page_marked_complete_only_when_explicit(tmp_path):
    rec = bulk.new_window_record("ANKARA", "2026-06-10", "2026-06-16")
    valid = [1, 2]
    assert bulk.pages_remaining(rec, valid) == [1, 2]
    bulk.mark_page_complete(rec, 1)
    # sayfa 2 tıklandı ama GEÇİŞ doğrulanmadı → işaretlenMEZ → hâlâ kalan
    assert bulk.pages_remaining(rec, valid) == [2]
    bulk.mark_page_complete(rec, 2)
    assert bulk.pages_remaining(rec, valid) == []


def test_bulk_state_checkpoint_roundtrip(tmp_path):
    state = bulk.load_bulk_state(tmp_path)
    rec = bulk.new_window_record("ANKARA", "2026-06-10", "2026-06-16")
    bulk.mark_page_complete(rec, 1)
    bulk.upsert_window_record(state, rec)
    bulk.save_bulk_state(state, tmp_path)
    reloaded = bulk.load_bulk_state(tmp_path)
    got = bulk.get_window_record(reloaded, "ANKARA", "2026-06-10", "2026-06-16")
    assert got is not None and got["pages_completed"] == [1]
    # idempotent upsert (kopya oluşturmaz)
    bulk.upsert_window_record(reloaded, got)
    assert len(reloaded["windows"]) == 1


def test_legacy_unphased_checkpoint_migrates_to_discovery(tmp_path):
    legacy = {
        "windows": [{
            "key": "ankara|2026-06-01|2026-06-07",
            "province": "ANKARA",
            "window_start": "2026-06-01",
            "window_end": "2026-06-07",
            "status": "COMPLETE",
            "pages_completed": [1],
        }]
    }
    bulk.bulk_state_path(tmp_path).write_text(json.dumps(legacy), encoding="utf-8")

    state = bulk.load_bulk_state(tmp_path)

    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_DISCOVERY
    )["status"] == "LEGACY_CARDINALITY_RECHECK"
    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    ) is None
    assert state["legacy_windows_migrated_to_discovery"] == 1


def test_legacy_complete_window_at_source_cap_is_reopened(tmp_path):
    legacy = {
        "windows": [{
            "key": "ankara|2026-06-01|2026-06-07",
            "province": "ANKARA",
            "window_start": "2026-06-01",
            "window_end": "2026-06-07",
            "status": "COMPLETE",
            "result_count": 200,
            "total_pages": 10,
            "pages_completed": list(range(1, 11)),
        }]
    }
    bulk.bulk_state_path(tmp_path).write_text(json.dumps(legacy), encoding="utf-8")

    state = bulk.load_bulk_state(tmp_path)
    record = bulk.get_window_record(
        state, "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_DISCOVERY
    )

    assert record["status"] == "LEGACY_SATURATION_RECHECK"
    assert record["pages_completed"] == []
    assert bulk.build_discovery_campaign_plan(
        ["ANKARA"], "2026-06-01", "2026-06-07", state=state
    )


def test_legacy_complete_window_with_sparse_pages_is_reopened(tmp_path):
    legacy = {
        "windows": [{
            "key": "ankara|2026-06-01|2026-06-07",
            "province": "ANKARA",
            "window_start": "2026-06-01",
            "window_end": "2026-06-07",
            "status": "COMPLETE",
            "result_count": 60,
            "per_page": 20,
            "pages_completed": [1, 3],
        }]
    }
    bulk.bulk_state_path(tmp_path).write_text(json.dumps(legacy), encoding="utf-8")

    state = bulk.load_bulk_state(tmp_path)
    record = bulk.get_window_record(
        state, "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_DISCOVERY
    )

    assert record["status"] == "LEGACY_CARDINALITY_RECHECK"
    assert record["legacy_pages_completed"] == [1, 3]
    assert record["pages_completed"] == []


def test_discovery_and_acquisition_checkpoints_are_independent(tmp_path):
    state = bulk.load_bulk_state(tmp_path)
    discovery = bulk.new_window_record(
        "ANKARA", "2026-06-10", "2026-06-16", bulk.PHASE_DISCOVERY
    )
    discovery["status"] = "COMPLETE"
    bulk.upsert_window_record(state, discovery)

    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-10", "2026-06-16", bulk.PHASE_DISCOVERY
    )["status"] == "COMPLETE"
    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-10", "2026-06-16", bulk.PHASE_ACQUISITION
    ) is None

    acquisition = bulk.new_window_record("ANKARA", "2026-06-10", "2026-06-16")
    bulk.upsert_window_record(state, acquisition)
    assert len(state["windows"]) == 2


def test_result_metadata_parsing():
    meta = bulk.extract_result_metadata(
        "32 sonuç bulundu. Toplam 2 sayfa içerisinde 1. sayfayı görmektesiniz. "
        "Her sayfada 20 kayıt gösterilir."
    )
    assert meta == {"result_count": 32, "total_pages": 2, "current_page": 1, "per_page": 20}


def test_diagnostic_target_selection_is_exact_when_selectors_are_supplied():
    cards = [
        {"file_id": "2026/1", "kayit_no": "1001", "sold": True},
        {"file_id": "2026/2", "kayit_no": "1002", "sold": True},
    ]
    assert bulk.select_diagnostic_result_card(cards)["kayit_no"] == "1001"
    assert bulk.select_diagnostic_result_card(
        cards, target_file_id="2026/2", target_kayit_no="1002"
    )["kayit_no"] == "1002"
    assert bulk.select_diagnostic_result_card(
        cards, target_file_id="2026/1", target_kayit_no="1002"
    ) is None
    assert bulk.select_diagnostic_result_card(
        cards, target_kayit_no="missing"
    ) is None


def test_result_metadata_parses_fragmented_visible_html():
    html = """
    <div><strong>28</strong> sonuç bulundu.</div>
    <div>Toplam <strong>2</strong> sayfa içerisinde <span>1</span> . sayfayı görmektesiniz.</div>
    <div>Her sayfada <strong>20</strong> kayıt gösterilir.</div>
    """
    assert bulk.extract_result_metadata(html) == {
        "result_count": 28,
        "total_pages": 2,
        "current_page": 1,
        "per_page": 20,
    }


def test_result_metadata_ignores_hidden_stale_panels():
    html = """
    <section id="searchResults" style="display: none"><div>99 sonuç bulundu.</div></section>
    <section id="historyResults" style="display: block"><div>0 sonuç bulundu.</div></section>
    <section id="dosyaResults" aria-hidden="true"><div>77 sonuç bulundu.</div></section>
    """
    assert bulk.extract_result_metadata(html)["result_count"] == 0
    assert bulk.zero_results(html) is True


def test_result_payload_evidence_parses_live_history_json_shape():
    payload = json.dumps([
        [{
            "kayitID": 16975383463,
            "dosyaNoTurKod": "2026/123 Talimat",
            "birimAdi": "Beypazarı İcra Dairesi",
            "ihaleSirasi": 2,
        }],
        20,
        28,
        1,
    ], ensure_ascii=False)
    evidence = bulk.result_payload_evidence(payload)
    assert evidence["cards"] == ((
        "16975383463",
        "2026/123",
        "2. ihale beypazari icra dairesi",
    ),)
    assert evidence["result_count"] == 28
    assert evidence["zero"] is False
    assert bulk.result_payload_evidence("[[], 20, 0, 1]")["zero"] is True


def test_result_metadata_ignores_hidden_zero_banner():
    text = (
        "0 sonuç bulundu. 40 sonuç bulundu. Toplam 2 sayfa içerisinde 1. sayfayı "
        "görmektesiniz. Her sayfada 20 kayıt gösterilir."
    )
    assert bulk.extract_result_metadata(text)["result_count"] == 40
    assert bulk.zero_results(text) is False
    assert bulk.zero_results("sonuç bulunamadı · 40 sonuç bulundu") is False

    evidence = bulk.result_payload_evidence(
        '{"totalCount": 40, "empty": "Sonuç bulunamadı"}'
    )
    assert evidence["result_count"] == 40
    assert evidence["zero"] is False


def test_all_province_campaign_plan_is_newest_first_and_resumable():
    assert len(bulk.UYAP_PROVINCES) == 81
    state = {"windows": []}
    completed = bulk.new_window_record(
        "ANKARA", "2026-06-08", "2026-06-14", bulk.PHASE_DISCOVERY
    )
    completed["status"] = "COMPLETE"
    bulk.upsert_window_record(state, completed)

    plan = bulk.build_discovery_campaign_plan(
        ["ANKARA", "İSTANBUL"],
        "2026-06-01",
        "2026-06-14",
        state=state,
        newest_first=True,
    )

    assert plan == [
        {"phase": "discovery", "province": "ANKARA", "start": "2026-06-01", "end": "2026-06-07"},
        {"phase": "discovery", "province": "İSTANBUL", "start": "2026-06-08", "end": "2026-06-14"},
        {"phase": "discovery", "province": "İSTANBUL", "start": "2026-06-01", "end": "2026-06-07"},
    ]

    limited = bulk.build_discovery_campaign_plan(
        ["ANKARA"], "2026-06-01", "2026-06-14", state=state,
        newest_first=True, max_windows_per_province=1,
    )
    assert limited == [
        {"phase": "discovery", "province": "ANKARA", "start": "2026-06-01", "end": "2026-06-07"}
    ]


def test_saturated_newest_day_is_deferred_behind_normal_backlog():
    state = {"windows": []}
    saturated = bulk.new_window_record(
        "ANKARA", "2026-06-08", "2026-06-08", bulk.PHASE_DISCOVERY
    )
    saturated["status"] = "SATURATED_UNRESOLVED"
    bulk.upsert_window_record(state, saturated)

    plan = bulk.build_discovery_campaign_plan(
        ["ANKARA"], "2026-06-01", "2026-06-08", state=state,
        newest_first=True, max_windows_per_province=1,
    )

    assert plan == [{
        "phase": "discovery", "province": "ANKARA",
        "start": "2026-06-01", "end": "2026-06-07",
    }]

    completed = bulk.new_window_record(
        "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_DISCOVERY
    )
    completed["status"] = "COMPLETE"
    bulk.upsert_window_record(state, completed)
    pending = bulk.prioritize_pending_windows(
        state,
        "ANKARA",
        [
            {"start": "2026-06-01", "end": "2026-06-07"},
            {"start": "2026-06-08", "end": "2026-06-08"},
        ],
        bulk.PHASE_DISCOVERY,
    )
    assert pending == [{"start": "2026-06-08", "end": "2026-06-08"}]


def test_nested_split_tree_with_only_saturated_leaf_is_deferred():
    state = {"windows": []}

    def add(start, end, status):
        record = bulk.new_window_record(
            "ANKARA", start, end, bulk.PHASE_DISCOVERY
        )
        record["status"] = status
        bulk.upsert_window_record(state, record)

    add("2026-06-08", "2026-06-14", "SPLIT")
    add("2026-06-08", "2026-06-11", "COMPLETE")
    add("2026-06-12", "2026-06-14", "SPLIT")
    add("2026-06-12", "2026-06-13", "COMPLETE")
    add("2026-06-14", "2026-06-14", "SATURATED_UNRESOLVED")

    assert bulk.window_tree_saturated_only(
        state, "ANKARA", {"start": "2026-06-08", "end": "2026-06-14"}
    ) is True
    plan = bulk.build_discovery_campaign_plan(
        ["ANKARA"], "2026-06-01", "2026-06-14", state=state,
        newest_first=True, max_windows_per_province=1,
    )
    assert plan[0]["start"] == "2026-06-01"


def test_campaign_province_priority_uses_yield_then_cold_start():
    cold = bulk.prioritize_campaign_provinces(list(bulk.UYAP_PROVINCES), {"windows": []})
    assert cold[:3] == ["İSTANBUL", "ANKARA", "İZMİR"]

    measured_state = {
        "windows": [{
            "province": "SİVAS",
            "sold_discovered": 5,
            "result_cards_inspected": 10,
        }]
    }
    measured = bulk.prioritize_campaign_provinces(
        ["İSTANBUL", "ANKARA", "SİVAS"], measured_state
    )
    assert measured == ["SİVAS", "İSTANBUL", "ANKARA"]

    measured_state["windows"][0]["status"] = "SATURATED_UNRESOLVED"
    fair = bulk.prioritize_campaign_provinces(
        ["ANKARA", "SİVAS"], measured_state
    )
    assert fair == ["ANKARA", "SİVAS"]

    turkish_state = {
        "windows": [{
            "province": "istanbul",
            "status": "SATURATED_UNRESOLVED",
            "sold_discovered": 10,
            "result_cards_inspected": 20,
        }]
    }
    assert bulk.prioritize_campaign_provinces(
        ["İSTANBUL", "ANKARA"], turkish_state
    ) == ["ANKARA", "İSTANBUL"]

    tasks = [
        {"province": "SİVAS", "start": "2026-06-01", "end": "2026-06-07"},
        {"province": "İSTANBUL", "start": "2026-06-01", "end": "2026-06-07"},
        {"province": "ANKARA", "start": "2026-06-01", "end": "2026-06-07"},
    ]
    assert bulk.limit_campaign_tasks(tasks, 2) == tasks[:2]
    per_province = tasks + [{
        "province": "SİVAS", "start": "2026-05-01", "end": "2026-05-07"
    }]
    assert bulk.limit_campaign_windows_per_province(
        per_province, 1
    ) == tasks
    with pytest.raises(ValueError):
        bulk.limit_campaign_tasks(tasks, 0)


def test_acquisition_queue_contains_only_incomplete_discovered_windows(tmp_path):
    gp = tmp_path / "genuine.json"
    discovered = bulk.process_sold_auction(
        _sold_card(fid="2026/1", kayit="1001"),
        acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path,
        genuine_path=gp,
        discovery_only=True,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    acquired = bulk.process_sold_auction(
        _sold_card(fid="2026/2", kayit="1002"),
        acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path,
        genuine_path=gp,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    admitted_candidate = store.get_candidate(discovered["candidate_id"], tmp_path)
    assert admitted_candidate["state"] == STATE_DISCOVERED
    completed_candidate = store.get_candidate(acquired["candidate_id"], tmp_path)
    assert completed_candidate["state"] in (STATE_AUDITED, STATE_PENDING_REVIEW)

    queue = bulk.build_acquisition_queue(tmp_path)

    assert len(queue) == 1
    assert queue[0]["province"] == "ANKARA"
    assert queue[0]["candidate_ids"] == [discovered["candidate_id"]]
    assert queue[0]["record_refs"] == ["1001"]
    assert queue[0]["candidate_ids_by_ref"] == {
        "1001": [discovered["candidate_id"]]
    }
    overlapping = bulk.build_acquisition_queue(
        tmp_path, ["ANKARA"], date_from="2026-06-05", date_to="2026-06-05"
    )
    assert overlapping[0]["record_refs"] == ["1001"]


def test_targeted_acquisition_rejects_card_with_different_candidate_identity(
    tmp_path, monkeypatch
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    original = _sold_card(fid="2026/31", kayit="3101")
    original["institution_text"] = "Ankara 1. İcra Dairesi"
    discovered = bulk.process_sold_auction(
        original,
        acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine.json",
        discovery_only=True,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    changed = dict(original, institution_text="Ankara 2. İcra Dairesi")
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("1 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [changed])
    acquired = []

    def acquire(file_id, institution, record_ref=None):
        acquired.append((file_id, institution, record_ref))
        return _fake_acquire_ok(file_id, institution, record_ref)

    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)
    stop = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"3101"},
        target_candidate_ids_by_ref={"3101": {discovered["candidate_id"]}},
    )

    assert stop == "ACQUISITION_INCOMPLETE"
    assert acquired == []
    assert rec["pending_record_refs"] == ["3101"]


def test_one_matching_candidate_does_not_clear_sibling_with_same_record_ref(
    tmp_path, monkeypatch
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    card = _sold_card(fid="2026/32", kayit="3201")
    card["institution_text"] = "Ankara 1. İcra Dairesi"
    matching_id = bulk.deterministic_candidate_id(
        card["institution_text"], card["file_id"], card["kayit_no"]
    )
    sibling_id = bulk.deterministic_candidate_id(
        "Ankara 2. İcra Dairesi", card["file_id"], card["kayit_no"]
    )
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("1 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [card])
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"3201"},
        target_candidate_ids_by_ref={"3201": {matching_id, sibling_id}},
    )

    assert stop == "ACQUISITION_INCOMPLETE"
    assert summary["acquisitions_completed"] == 1
    assert rec["pending_record_refs"] == ["3201"]


def test_failed_candidate_does_not_starve_later_page_sibling_with_same_ref(
    tmp_path, monkeypatch
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1, 2])
    first = _sold_card(fid="2026/34", kayit="3401")
    first["institution_text"] = "Ankara 1. İcra Dairesi"
    second = _sold_card(fid="2026/34", kayit="3401")
    second["institution_text"] = "Ankara 2. İcra Dairesi"
    monkeypatch.setattr(
        bulk, "parse_result_cards",
        lambda html: [first] if page.current_page == 1 else [second],
    )
    first_id = bulk.deterministic_candidate_id(
        first["institution_text"], first["file_id"], first["kayit_no"]
    )
    second_id = bulk.deterministic_candidate_id(
        second["institution_text"], second["file_id"], second["kayit_no"]
    )
    calls = []

    def acquire(file_id, institution, record_ref=None):
        calls.append(institution)
        return (
            _fake_acquire_fail(file_id, institution, record_ref)
            if "1. İcra" in institution
            else _fake_acquire_ok(file_id, institution, record_ref)
        )

    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)
    stop = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"3401"},
        target_candidate_ids_by_ref={"3401": {first_id, second_id}},
    )

    assert stop == "ACQUISITION_INCOMPLETE"
    assert calls == [first["institution_text"], second["institution_text"]]
    assert summary["acquisitions_completed"] == 1
    assert rec["pending_record_refs"] == ["3401"]
    assert set(rec["attempted_candidate_ids"]) == {first_id, second_id}


def test_acquisition_queue_preserves_province_priority_and_blocks_missing_kayit(tmp_path):
    for province, kayit in (("SİVAS", "2001"), ("ZONGULDAK", "2002")):
        bulk.process_sold_auction(
            _sold_card(fid=f"2026/{kayit}", kayit=kayit),
            acquire_documents=_fake_acquire_fail,
            store_dir=tmp_path,
            genuine_path=tmp_path / "genuine.json",
            discovery_only=True,
            province_label=province,
            window={"start": "2026-06-01", "end": "2026-06-07"},
        )
    orphan = store.new_candidate("Sivas İcra", "2026/999")
    orphan["bulk"] = {
        "province_label": "SİVAS",
        "window_start": "2026-06-01",
        "window_end": "2026-06-07",
    }
    store.upsert(orphan, tmp_path)

    queue = bulk.build_acquisition_queue(
        tmp_path, ["SİVAS", "ZONGULDAK"]
    )
    blockers = bulk.acquisition_queue_blockers(tmp_path)

    assert [task["province"] for task in queue] == ["SİVAS", "ZONGULDAK"]
    assert all(task["record_refs"] for task in queue)
    assert blockers == [{
        "candidate_id": orphan["candidate_id"],
        "province": "SİVAS",
        "reasons": ["missing_kayit_no"],
    }]
    assert bulk.acquisition_queue_blockers(tmp_path, ["ZONGULDAK"]) == []
    assert bulk.acquisition_queue_blockers(
        tmp_path, ["SİVAS"], date_from="2027-01-01", date_to="2027-01-07"
    ) == []

    with pytest.raises(ValueError, match="date_to must be >= date_from"):
        bulk.build_acquisition_queue(
            tmp_path, ["SİVAS"], date_from="2026-06-08", date_to="2026-06-01"
        )


def test_acquisition_queue_prioritizes_fresh_tasks_then_lower_retry_round(tmp_path):
    for province, kayit in (("SİVAS", "2101"), ("ZONGULDAK", "2102")):
        bulk.process_sold_auction(
            _sold_card(fid=f"2026/{kayit}", kayit=kayit),
            acquire_documents=_fake_acquire_fail,
            store_dir=tmp_path,
            genuine_path=tmp_path / "genuine.json",
            discovery_only=True,
            province_label=province,
            window={"start": "2026-06-01", "end": "2026-06-07"},
        )
    state = bulk.load_bulk_state(tmp_path)
    sivas = bulk.new_window_record(
        "SİVAS", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    )
    sivas["attempted_record_refs"] = ["2101"]
    sivas["retry_round"] = 1
    bulk.upsert_window_record(state, sivas)
    bulk.save_bulk_state(state, tmp_path)

    queue = bulk.build_acquisition_queue(tmp_path, ["SİVAS", "ZONGULDAK"])
    assert [task["province"] for task in queue] == ["ZONGULDAK", "SİVAS"]
    assert bulk.limit_campaign_tasks(queue, 1)[0]["province"] == "ZONGULDAK"

    zonguldak = bulk.new_window_record(
        "ZONGULDAK", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    )
    zonguldak["attempted_record_refs"] = ["2102"]
    bulk.upsert_window_record(state, zonguldak)
    bulk.save_bulk_state(state, tmp_path)
    retried = bulk.build_acquisition_queue(tmp_path, ["SİVAS", "ZONGULDAK"])
    assert [task["retry_round"] for task in retried] == [0, 1]


def test_acquisition_queue_canonicalizes_turkish_province_and_reports_missing_metadata(tmp_path):
    candidate = store.new_candidate("İstanbul İcra", "2026/1", record_ref="8001")
    candidate["bulk"] = {
        "province_label": "istanbul",
        "window_start": "2026-06-01",
        "window_end": "2026-06-07",
        "kayit_no": "8001",
    }
    store.upsert(candidate, tmp_path)
    missing = store.new_candidate("Bilinmeyen İcra", "2026/2", record_ref="8002")
    missing["bulk"] = {"kayit_no": "8002"}
    store.upsert(missing, tmp_path)

    queue = bulk.build_acquisition_queue(tmp_path, ["İSTANBUL"])
    blockers = bulk.acquisition_queue_blockers(tmp_path, ["İSTANBUL"])

    assert queue[0]["province"] == "İSTANBUL"
    assert queue[0]["record_refs"] == ["8001"]
    assert blockers == []
    assert bulk.acquisition_queue_blockers(tmp_path) == [{
        "candidate_id": missing["candidate_id"],
        "province": None,
        "reasons": ["missing_province", "missing_window"],
    }]


def test_live_runner_adaptively_splits_and_uses_discovery_phase(tmp_path, monkeypatch):
    import threading
    from sold.ingestion.uyap.collect import BrowserCollector

    class FakeContext:
        pages = []

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, endpoint):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        BrowserCollector,
        "_sync_playwright",
        staticmethod(lambda: lambda: FakeManager()),
    )
    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222", store_dir=tmp_path)
    monkeypatch.setattr(collector, "_find_gecmis_page", lambda context: object())
    monkeypatch.setattr(collector, "_start_watchdog", lambda: threading.Event())
    monkeypatch.setattr(collector, "_print_summary", lambda summary: None)
    calls = []

    def fake_run_window(page, context, acquire, province, window, rec, state, summary,
                        max_records, discovery_only, acquired_total, target_kayit_no=None,
                        target_kayit_nos=None, target_candidate_ids_by_ref=None,
                        force=False):
        calls.append(window.copy())
        if window == {"start": "2026-06-01", "end": "2026-06-07"}:
            rec["status"] = "SPLIT"
            bulk.upsert_window_record(state, rec)
            bulk.save_bulk_state(state, tmp_path)
            return "SPLIT_WINDOW"
        rec["status"] = "COMPLETE"
        bulk.upsert_window_record(state, rec)
        bulk.save_bulk_state(state, tmp_path)
        return None

    monkeypatch.setattr(collector, "_run_window", fake_run_window)

    summary = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        discovery_only=True,
        newest_first=True,
        max_windows=3,
    )

    assert calls == [
        {"start": "2026-06-01", "end": "2026-06-07"},
        {"start": "2026-06-05", "end": "2026-06-07"},
        {"start": "2026-06-01", "end": "2026-06-04"},
    ]
    assert summary["phase"] == bulk.PHASE_DISCOVERY
    assert summary["windows_processed"] == 3
    assert summary["dense_windows_split"] == 1
    assert summary["elapsed_seconds"] >= 0
    state = bulk.load_bulk_state(tmp_path)
    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-05", "2026-06-07", bulk.PHASE_DISCOVERY
    )["status"] == "COMPLETE"
    assert bulk.get_window_record(
        state, "ANKARA", "2026-06-05", "2026-06-07", bulk.PHASE_ACQUISITION
    ) is None

    assert bulk.window_tree_complete(
        state,
        "ANKARA",
        {"start": "2026-06-01", "end": "2026-06-07"},
        bulk.PHASE_DISCOVERY,
    ) is True
    assert bulk.build_discovery_campaign_plan(
        ["ANKARA"], "2026-06-01", "2026-06-07", state=state
    ) == []

    bounded = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        discovery_only=True,
        newest_first=True,
        max_windows=1,
        force=True,
    )
    assert bounded["stopped_reason"] == "DISCOVERY_INCOMPLETE"
    assert bounded["discovery_windows_incomplete"] == 2

    acquisition_parent = bulk.new_window_record(
        "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    )
    acquisition_parent["status"] = "SPLIT"
    bulk.upsert_window_record(state, acquisition_parent)
    bulk.save_bulk_state(state, tmp_path)
    calls.clear()

    def fake_targeted_window(page, context, acquire, province, window, rec, state, summary,
                             max_records, discovery_only, acquired_total, target_kayit_no=None,
                             target_kayit_nos=None, target_candidate_ids_by_ref=None,
                             force=False):
        calls.append(window.copy())
        assert rec["status"] == "IN_PROGRESS"
        bulk.mark_acquisition_incomplete(rec, set(target_kayit_nos or ()))
        return "ACQUISITION_INCOMPLETE"

    monkeypatch.setattr(collector, "_run_window", fake_targeted_window)
    targeted = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        kayit_nos={"9001"},
        max_windows=1,
    )

    assert calls == [{"start": "2026-06-01", "end": "2026-06-07"}]
    assert targeted["acquisition_windows_incomplete"] == 1

    observed_targets = []

    def fake_singular_target(page, context, acquire, province, window, rec, state, summary,
                             max_records, discovery_only, acquired_total, target_kayit_no=None,
                             target_kayit_nos=None, target_candidate_ids_by_ref=None,
                             force=False):
        observed_targets.append(set(target_kayit_nos or ()))
        bulk.mark_acquisition_incomplete(rec, set(target_kayit_nos or ()))
        return "ACQUISITION_INCOMPLETE"

    monkeypatch.setattr(collector, "_run_window", fake_singular_target)
    singular = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        kayit_no="9003",
        max_windows=1,
    )
    assert observed_targets == [{"9003"}]
    assert singular["acquisition_windows_incomplete"] == 1

    def fake_capped_window(page, context, acquire, province, window, rec, state, summary,
                           max_records, discovery_only, acquired_total, target_kayit_no=None,
                           target_kayit_nos=None, target_candidate_ids_by_ref=None,
                           force=False):
        bulk.mark_acquisition_incomplete(rec, set(target_kayit_nos or ()))
        return "MAX_RECORDS"

    monkeypatch.setattr(collector, "_run_window", fake_capped_window)
    capped = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        kayit_nos={"9001", "9002"},
        max_windows=1,
        max_records=1,
    )
    assert capped["stopped_reason"] == "max_records"
    assert capped["acquisition_windows_incomplete"] == 1

    def fake_discovery_capped(page, context, acquire, province, window, rec, state, summary,
                              max_records, discovery_only, acquired_total,
                              target_kayit_no=None, target_kayit_nos=None,
                              target_candidate_ids_by_ref=None, force=False):
        rec["status"] = "DISCOVERY_INCOMPLETE"
        return "MAX_RECORDS"

    monkeypatch.setattr(collector, "_run_window", fake_discovery_capped)
    discovery_capped = collector.run(
        province="ANKARA",
        date_from="2026-06-01",
        date_to="2026-06-07",
        discovery_only=True,
        max_records=1,
        force=True,
    )
    assert discovery_capped["stopped_reason"] == "DISCOVERY_INCOMPLETE"
    assert discovery_capped["discovery_windows_incomplete"] == 1

    def fake_complete_window(page, context, acquire, province, window, rec, state, summary,
                             max_records, discovery_only, acquired_total,
                             target_kayit_no=None, target_kayit_nos=None,
                             target_candidate_ids_by_ref=None, force=False):
        rec["status"] = "COMPLETE"
        bulk.upsert_window_record(state, rec)
        bulk.save_bulk_state(state, tmp_path)
        return None

    monkeypatch.setattr(collector, "_run_window", fake_complete_window)
    acquisition_capped = collector.run(
        province="SİVAS",
        date_from="2026-06-01",
        date_to="2026-06-14",
        max_windows=1,
    )
    assert acquisition_capped["stopped_reason"] == "ACQUISITION_INCOMPLETE"
    assert acquisition_capped["acquisition_windows_incomplete"] == 1


def test_campaign_cli_dry_run_needs_no_browser(tmp_path):
    from typer.testing import CliRunner
    from sold.cli import app

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--all-provinces", "--phase", "discover",
        "--max-provinces", "2", "--store-dir", str(tmp_path), "--dry-run",
    ])

    assert result.exit_code == 0, result.output
    assert "faz=discovery" in result.output
    assert "İSTANBUL" in result.output and "ANKARA" in result.output

    bad_cap = CliRunner().invoke(app, [
        "uyap", "campaign", "--all-provinces", "--phase", "discover",
        "--max-provinces", "0", "--dry-run",
    ])
    assert bad_cap.exit_code != 0


def test_campaign_cli_reports_unverified_refresh_as_blocker(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "windows_processed": 1,
            "result_cards_inspected": 0,
            "saturated_windows_unresolved": 0,
            "acquisitions_completed": 0,
            "acquisition_failures": 0,
            "records_processed": 0,
            "stopped_reason": "RESULT_REFRESH_UNVERIFIED",
        },
    )

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "discover", "--provinces", "ANKARA",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--store-dir", str(tmp_path),
    ])

    assert result.exit_code == 1
    assert "Campaign durduruldu: RESULT_REFRESH_UNVERIFIED" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_campaign_cli_reports_bounded_split_as_incomplete(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "windows_processed": 1,
            "result_cards_inspected": 0,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "discovery_windows_incomplete": 2,
            "acquisitions_completed": 0,
            "acquisition_failures": 0,
            "records_processed": 0,
            "stopped_reason": "DISCOVERY_INCOMPLETE",
        },
    )
    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "discover", "--provinces", "ANKARA",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--store-dir", str(tmp_path),
    ])
    assert result.exit_code == 2
    assert "'discovery_incomplete': 2" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_discovery_max_provinces_reports_deferred_scope(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    calls = []

    def run_one(self, *args, **kwargs):
        calls.append(args[0])
        return {
            "windows_processed": 1,
            "result_cards_inspected": 0,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "discovery_windows_incomplete": 0,
            "acquisitions_completed": 0,
            "acquisition_failures": 0,
            "records_processed": 0,
            "stopped_reason": None,
        }

    monkeypatch.setattr(bulk.UyapBulkCollector, "run", run_one)
    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "discover",
        "--provinces", "ANKARA,SİVAS,İZMİR", "--max-provinces", "1",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--store-dir", str(tmp_path),
    ])

    assert calls == ["ANKARA"]
    assert result.exit_code == 2
    assert "ertelenen keşif görevi: 2" in result.output
    assert "'discovery_incomplete': 2" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_campaign_cli_exits_nonzero_for_incomplete_acquisition(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    bulk.process_sold_auction(
        _sold_card(fid="2026/2201", kayit="2201"),
        acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine.json",
        discovery_only=True,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "windows_processed": 1,
            "result_cards_inspected": 1,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 1,
            "acquisitions_completed": 0,
            "acquisition_failures": 1,
            "records_processed": 1,
            "stopped_reason": "ACQUISITION_INCOMPLETE",
        },
    )

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire", "--provinces", "ANKARA",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--store-dir", str(tmp_path),
    ])

    assert result.exit_code == 2
    assert "çözülmemiş işler var" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_campaign_budget_exhaustion_between_tasks_is_incomplete(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    for kayit, start, end in (
        ("2251", "2026-06-08", "2026-06-14"),
        ("2252", "2026-06-01", "2026-06-07"),
    ):
        bulk.process_sold_auction(
            _sold_card(fid=f"2026/{kayit}", kayit=kayit),
            acquire_documents=_fake_acquire_fail,
            store_dir=tmp_path,
            genuine_path=tmp_path / "genuine.json",
            discovery_only=True,
            province_label="ANKARA",
            window={"start": start, "end": end},
        )
    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "windows_processed": 1,
            "result_cards_inspected": 1,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "acquisitions_completed": 1,
            "acquisition_failures": 0,
            "records_processed": 1,
            "stopped_reason": None,
        },
    )

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire", "--provinces", "ANKARA",
        "--max-records", "1", "--cdp-endpoint", "http://127.0.0.1:9222",
        "--store-dir", str(tmp_path),
    ])

    assert result.exit_code == 2
    assert "'incomplete': 1" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_acquisition_per_province_bound_defers_extra_tasks(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    for kayit, start, end in (
        ("2261", "2026-06-08", "2026-06-14"),
        ("2262", "2026-06-01", "2026-06-07"),
    ):
        bulk.process_sold_auction(
            _sold_card(fid=f"2026/{kayit}", kayit=kayit),
            acquire_documents=_fake_acquire_fail,
            store_dir=tmp_path,
            genuine_path=tmp_path / "genuine.json",
            discovery_only=True,
            province_label="ANKARA",
            window={"start": start, "end": end},
        )
    calls = []

    def run_one(self, *args, **kwargs):
        calls.append((args, kwargs))
        return {
            "windows_processed": 1,
            "result_cards_inspected": 1,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "acquisitions_completed": 1,
            "acquisition_failures": 0,
            "records_processed": 1,
            "stopped_reason": None,
        }

    monkeypatch.setattr(bulk.UyapBulkCollector, "run", run_one)
    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire", "--provinces", "ANKARA",
        "--max-windows-per-province", "1",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--store-dir", str(tmp_path),
    ])

    assert len(calls) == 1
    assert result.exit_code == 2
    assert "ertelenen edinim görevi: 1" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_max_provinces_selects_first_province_with_actual_work(tmp_path):
    from typer.testing import CliRunner
    from sold.cli import app

    bulk.process_sold_auction(
        _sold_card(fid="2026/2271", kayit="2271"),
        acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine.json",
        discovery_only=True,
        province_label="SİVAS",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire",
        "--provinces", "ANKARA,SİVAS", "--max-provinces", "1",
        "--store-dir", str(tmp_path), "--dry-run",
    ])

    assert result.exit_code == 0
    assert "il/görev: {'SİVAS': 1}" in result.output
    assert "ANKARA ·" not in result.output


def test_campaign_cli_exits_nonzero_when_all_candidates_are_unqueueable(tmp_path):
    from typer.testing import CliRunner
    from sold.cli import app

    orphan = store.new_candidate("Ankara İcra", "2026/2301")
    orphan["bulk"] = {
        "province_label": "ANKARA",
        "window_start": "2026-06-01",
        "window_end": "2026-06-07",
    }
    store.upsert(orphan, tmp_path)

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire", "--provinces", "ANKARA",
        "--store-dir", str(tmp_path),
    ])

    assert result.exit_code == 2
    assert "Campaign bloke" in result.output
    assert "Campaign tamamlandı" not in result.output


def test_campaign_blockers_follow_max_province_scope(tmp_path):
    from typer.testing import CliRunner
    from sold.cli import app

    bulk.process_sold_auction(
        _sold_card(fid="2026/2351", kayit="2351"),
        acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine.json",
        discovery_only=True,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    sivas_orphan = store.new_candidate("Sivas İcra", "2026/2352")
    sivas_orphan["bulk"] = {
        "province_label": "SİVAS",
        "window_start": "2026-06-01",
        "window_end": "2026-06-07",
    }
    store.upsert(sivas_orphan, tmp_path)

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire",
        "--provinces", "ANKARA,SİVAS", "--max-provinces", "1",
        "--store-dir", str(tmp_path), "--dry-run",
    ])

    assert result.exit_code == 0
    assert "ANKARA" in result.output
    assert "kuyruğa alınamayan" not in result.output

    two_provinces = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire",
        "--provinces", "ANKARA,SİVAS", "--max-provinces", "2",
        "--store-dir", str(tmp_path), "--dry-run",
    ])
    assert two_provinces.exit_code == 0
    assert "kuyruğa alınamayan aday=1" in two_provinces.output

    unbounded = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire",
        "--provinces", "ANKARA,SİVAS", "--store-dir", str(tmp_path), "--dry-run",
    ])
    assert unbounded.exit_code == 0
    assert "kuyruğa alınamayan aday=1" in unbounded.output


def test_all_province_acquisition_reports_unattributable_blockers(tmp_path):
    from typer.testing import CliRunner
    from sold.cli import app

    orphan = store.new_candidate("Bilinmeyen İcra", "2026/2353")
    orphan["bulk"] = {"kayit_no": "2353"}
    store.upsert(orphan, tmp_path)

    result = CliRunner().invoke(app, [
        "uyap", "campaign", "--phase", "acquire", "--all-provinces",
        "--store-dir", str(tmp_path),
    ])

    assert result.exit_code == 2
    assert "kuyruğa alınamayan aday=1" in result.output
    assert "Campaign bloke" in result.output


def test_bulk_cli_exits_nonzero_for_incomplete_or_saturated_run(monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "category": "Taşınmaz",
            "province": "ANKARA",
            "date_from": "2026-06-01",
            "date_to": "2026-06-07",
            "windows_processed": 1,
            "windows_total": 1,
            "result_cards_inspected": 0,
            "sold_discovered": 0,
            "acquisitions_completed": 0,
            "sold_skipped_known": 0,
            "acquisition_failures": 0,
            "audit_decisions": {},
            "elapsed_seconds": 1.0,
            "windows_per_minute": 60.0,
            "acquisitions_per_minute": 0.0,
            "dense_windows_split": 0,
            "saturated_windows_unresolved": 1,
            "acquisition_windows_incomplete": 1,
            "stopped_reason": "ACQUISITION_INCOMPLETE",
        },
    )

    result = CliRunner().invoke(app, [
        "uyap", "bulk", "--province", "ANKARA",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222",
    ])

    assert result.exit_code == 2
    assert "TOPLU KOŞU TAMAMLANMADI" in result.output
    assert "admiyor" not in result.output.lower()

    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "category": "Taşınmaz", "province": "ANKARA",
            "date_from": "2026-06-01", "date_to": "2026-06-07",
            "windows_processed": 1, "windows_total": 1,
            "result_cards_inspected": 1, "sold_discovered": 1,
            "acquisitions_completed": 0, "sold_skipped_known": 0,
            "acquisition_failures": 0, "audit_decisions": {},
            "elapsed_seconds": 1.0, "windows_per_minute": 60.0,
            "acquisitions_per_minute": 0.0, "dense_windows_split": 0,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "discovery_windows_incomplete": 1,
            "stopped_reason": "DISCOVERY_INCOMPLETE",
        },
    )
    discovery = CliRunner().invoke(app, [
        "uyap", "bulk", "--province", "ANKARA",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222", "--discovery-only",
    ])
    assert discovery.exit_code == 2
    assert "TOPLU KOŞU TAMAMLANMADI" in discovery.output


def test_bulk_cli_exits_nonzero_on_session_expiration(monkeypatch):
    from typer.testing import CliRunner
    from sold.cli import app

    monkeypatch.setattr(
        bulk.UyapBulkCollector,
        "run",
        lambda self, *args, **kwargs: {
            "category": "Taşınmaz", "province": "ANKARA",
            "date_from": "2026-06-01", "date_to": "2026-06-07",
            "windows_processed": 0, "windows_total": 1,
            "result_cards_inspected": 0, "sold_discovered": 0,
            "acquisitions_completed": 0, "sold_skipped_known": 0,
            "acquisition_failures": 0, "audit_decisions": {},
            "elapsed_seconds": 1.0, "windows_per_minute": 0.0,
            "acquisitions_per_minute": 0.0, "dense_windows_split": 0,
            "saturated_windows_unresolved": 0,
            "acquisition_windows_incomplete": 0,
            "stopped_reason": "SESSION_EXPIRED",
        },
    )
    result = CliRunner().invoke(app, [
        "uyap", "bulk", "--province", "ANKARA",
        "--date-from", "2026-06-01", "--date-to", "2026-06-07",
        "--cdp-endpoint", "http://127.0.0.1:9222",
    ])
    assert result.exit_code == 1
    assert "OTURUM SONA ERDİ" in result.output


@pytest.mark.parametrize("scenario", ["failure", "absent"])
def test_targeted_acquisition_failure_or_absence_remains_retryable(
    tmp_path, monkeypatch, scenario
):
    class FakePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def content(self):
            return "results"

        def wait_for_timeout(self, milliseconds):
            return None

    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222", store_dir=tmp_path)
    cards = [
        _sold_card(
            fid="2026/1" if scenario == "failure" else "2026/2",
            kayit="3001" if scenario == "failure" else "9999",
        )
    ]
    acquirer = _fake_acquire_fail if scenario == "failure" else _fake_acquire_ok
    monkeypatch.setattr(collector, "_dismiss_notices", lambda page: None)
    monkeypatch.setattr(collector, "_select_category_tasinmaz", lambda page: True)
    monkeypatch.setattr(collector, "_select_province", lambda page, province: True)
    monkeypatch.setattr(collector, "_set_and_verify_dates", lambda page, start, end: True)
    monkeypatch.setattr(collector, "_click_ara", lambda page: True)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("1 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1])
    monkeypatch.setattr(collector, "_goto_page", lambda page, number: True)
    monkeypatch.setattr(collector, "_close_modal", lambda page: None)
    monkeypatch.setattr(collector, "_safe_ref", lambda url: url)
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: cards)
    state = {"windows": []}
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    rec = bulk.new_window_record("ANKARA", window["start"], window["end"])
    bulk.upsert_window_record(state, rec)
    summary = {
        "records_processed": 0,
        "result_cards_inspected": 0,
        "sold_discovered": 0,
        "sold_skipped_known": 0,
        "acquisitions_completed": 0,
        "acquisition_failures": 0,
    }

    stop = collector._run_window(
        FakePage(), object(), acquirer, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"3001"},
    )

    assert stop == "ACQUISITION_INCOMPLETE"
    assert rec["status"] == "ACQUISITION_INCOMPLETE"
    assert rec["pending_record_refs"] == ["3001"]
    assert rec["pages_completed"] == []


def _runner_fixture(tmp_path, monkeypatch):
    class FakePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"
        current_page = 1

        def content(self):
            return "results"

        def wait_for_timeout(self, milliseconds):
            return None

    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222", store_dir=tmp_path)
    monkeypatch.setattr(collector, "_dismiss_notices", lambda page: None)
    monkeypatch.setattr(collector, "_select_category_tasinmaz", lambda page: True)
    monkeypatch.setattr(collector, "_select_province", lambda page, province: True)
    monkeypatch.setattr(collector, "_set_and_verify_dates", lambda page, start, end: True)
    monkeypatch.setattr(collector, "_click_ara", lambda page: True)
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1])
    monkeypatch.setattr(collector, "_goto_page", lambda page, number: setattr(page, "current_page", number) or True)
    monkeypatch.setattr(collector, "_close_modal", lambda page: None)
    monkeypatch.setattr(collector, "_safe_ref", lambda url: url)
    return collector, FakePage()


def _runner_state(window, phase=bulk.PHASE_ACQUISITION):
    state = {"windows": []}
    rec = bulk.new_window_record("ANKARA", window["start"], window["end"], phase)
    bulk.upsert_window_record(state, rec)
    summary = {
        "records_processed": 0,
        "result_cards_inspected": 0,
        "sold_discovered": 0,
        "sold_skipped_known": 0,
        "acquisitions_completed": 0,
        "acquisition_failures": 0,
    }
    return state, rec, summary


def test_unverified_province_stops_before_search(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(collector, "_select_province", lambda page, province: False)
    clicked = {"value": False}
    monkeypatch.setattr(collector, "_click_ara", lambda page: clicked.update(value=True) or True)
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop == "SELECTION_UNVERIFIED"
    assert clicked["value"] is False
    assert rec["status"] == "PROVINCE_SELECTION_UNVERIFIED"


def test_hidden_empty_marker_does_not_hide_rendered_cards(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("sonuç bulunamadı", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [_sold_card(kayit="4001")])
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop is None
    assert rec["status"] == "COMPLETE"
    assert summary["sold_discovered"] == 1


@pytest.mark.parametrize(
    ("result_html", "expected_stop", "expected_status"),
    [
        ("sonuç bulunamadı", None, "COMPLETE"),
        ("arama yanıtı geldi", "RESULT_STATE_UNCONFIRMED", "RESULT_STATE_UNCONFIRMED"),
    ],
)
def test_runner_distinguishes_confirmed_zero_from_unconfirmed_state(
    tmp_path, monkeypatch, result_html, expected_stop, expected_status
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: (result_html, True),
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop == expected_stop
    assert rec["status"] == expected_status


def test_fresh_zero_result_clears_stale_window_telemetry(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("sonuç bulunamadı", True),
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)
    rec.update({
        "pages_completed": [1, 2],
        "result_cards_inspected": 40,
        "sold_discovered": 5,
        "sold_skipped_known": 1,
        "acquisitions_complete": 4,
        "acquisitions_failed": 1,
        "parsed_unique_count": 39,
        "total_pages": 2,
    })

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop is None
    assert rec["status"] == "COMPLETE"
    assert rec["result_count"] == 0
    assert rec["total_pages"] is None
    assert rec["pages_completed"] == []
    assert rec["result_cards_inspected"] == 0
    assert rec["sold_discovered"] == 0
    assert rec["acquisitions_complete"] == 0
    assert rec["acquisitions_failed"] == 0
    assert "parsed_unique_count" not in rec


def test_targeted_zero_result_remains_acquisition_incomplete(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("sonuç bulunamadı", True),
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"9201"},
    )

    assert stop == "ACQUISITION_INCOMPLETE"
    assert rec["status"] == "ACQUISITION_INCOMPLETE"
    assert rec["pending_record_refs"] == ["9201"]
    assert rec["attempted_record_refs"] == ["9201"]


def test_wait_result_state_requires_transition_from_baseline():
    old = (
        '<div class="card"><span>KAYIT NO: 1111</span><span>2026/1 Esas</span>'
        '<span>Satıldı</span></div>'
    )
    new = (
        '<div class="card"><span>KAYIT NO: 2222</span><span>2026/2 Esas</span>'
        '<span>Satıldı</span></div>'
    )

    class SequencePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, values):
            self.values = values
            self.index = 0

        def content(self):
            return self.values[min(self.index, len(self.values) - 1)]

        def wait_for_timeout(self, milliseconds):
            self.index += 1

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=2400
    )
    stale_html, stale_transition = collector._wait_result_state(
        SequencePage([old, old, old, old]), bulk.result_state_signature(old)
    )
    fresh_html, fresh_transition = collector._wait_result_state(
        SequencePage([old, "yükleniyor", new, new, new]), bulk.result_state_signature(old)
    )

    assert stale_transition is False
    assert bulk.result_card_signature(stale_html) == (("1111", "2026/1", ""),)
    assert fresh_transition is True
    assert bulk.result_card_signature(fresh_html) == (("2222", "2026/2", ""),)


def test_wait_result_state_rejects_metadata_only_change_with_stale_cards():
    baseline = (
        "1 sonuç bulundu. Toplam 2 sayfa içerisinde 2. sayfayı görmektesiniz. "
        '<div class="card"><span>KAYIT NO: 1111</span><span>2026/1 Esas</span>'
        '<span>Satıldı</span></div>'
    )
    stale = baseline.replace("2. sayfayı", "1. sayfayı")

    class SequencePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, values):
            self.values = values
            self.index = 0

        def content(self):
            return self.values[min(self.index, len(self.values) - 1)]

        def wait_for_timeout(self, milliseconds):
            self.index += 1

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=2000
    )
    _, metadata_only_verified = collector._wait_result_state(
        SequencePage([stale] * 5), bulk.result_state_signature(baseline)
    )
    same_html, request_verified = collector._wait_result_state(
        SequencePage([baseline] * 5), bulk.result_state_signature(baseline),
        request_probe={"evidence": [bulk.result_payload_evidence(baseline)]},
    )

    assert metadata_only_verified is False
    assert request_verified is True
    assert bulk.result_card_signature(same_html) == (("1111", "2026/1", ""),)


def test_waiter_does_not_accept_dom_that_conflicts_with_response_evidence():
    old_zero = '<div class="alert">Sonuç bulunamadı</div>'
    fresh = (
        '1 sonuç bulundu. <div class="card"><span>KAYIT NO: 2222</span><span>2026/2 Esas</span>'
        '<span>Satıldı</span></div>'
    )

    class SequencePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, values):
            self.values = values
            self.index = 0

        def content(self):
            return self.values[min(self.index, len(self.values) - 1)]

        def wait_for_timeout(self, milliseconds):
            self.index += 1

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=2400
    )
    evidence = {"evidence": [bulk.result_payload_evidence(fresh)]}
    stale_html, stale_verified = collector._wait_result_state(
        SequencePage([old_zero] * 6), bulk.result_state_signature(old_zero), evidence
    )
    fresh_html, fresh_verified = collector._wait_result_state(
        SequencePage([old_zero, old_zero, fresh, fresh, fresh]),
        bulk.result_state_signature(old_zero), evidence,
    )

    assert stale_verified is False
    assert bulk.zero_results(stale_html) is True
    assert fresh_verified is True
    assert bulk.result_card_signature(fresh_html) == (("2222", "2026/2", ""),)


def test_waiter_rejects_intermediate_dom_while_search_request_is_pending():
    old = (
        '<div class="card"><span>KAYIT NO: 1111</span><span>2026/1 Esas</span>'
        '<span>Satıldı</span></div>'
    )
    fresh = (
        '1 sonuç bulundu. <div class="card"><span>KAYIT NO: 2222</span><span>2026/2 Esas</span>'
        '<span>Satıldı</span></div>'
    )

    class StaticPage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def content(self):
            return fresh

        def wait_for_timeout(self, milliseconds):
            return None

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=1600
    )
    evidence = bulk.result_payload_evidence(fresh)
    _, pending_verified = collector._wait_result_state(
        StaticPage(), bulk.result_state_signature(old),
        {"evidence": [evidence], "pending": {1}},
    )
    settled_html, settled_verified = collector._wait_result_state(
        StaticPage(), bulk.result_state_signature(old),
        {"evidence": [evidence], "pending": set()},
    )

    assert pending_verified is False
    assert settled_verified is True
    assert bulk.result_card_signature(settled_html) == (("2222", "2026/2", ""),)


def test_waiter_rejects_same_cards_when_response_count_conflicts_with_dom():
    old = (
        '1 sonuç bulundu. <div class="card"><span>KAYIT NO: 1111</span>'
        '<span>2026/1 Esas</span><span>Satıldı</span></div>'
    )
    response = old.replace("1 sonuç", "2 sonuç")

    class StaticPage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, html):
            self.html = html

        def content(self):
            return self.html

        def wait_for_timeout(self, milliseconds):
            return None

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=1600
    )
    probe = {"evidence": [bulk.result_payload_evidence(response)], "pending": set()}
    _, conflicting_verified = collector._wait_result_state(
        StaticPage(old), bulk.result_state_signature(old), probe
    )
    matched_html, matched_verified = collector._wait_result_state(
        StaticPage(response), bulk.result_state_signature(old), probe
    )

    assert conflicting_verified is False
    assert matched_verified is True
    assert bulk.extract_result_metadata(matched_html)["result_count"] == 2


def test_waiter_accepts_only_latest_date_qualified_response():
    first = (
        '1 sonuç bulundu. <div class="card"><span>KAYIT NO: 1111</span>'
        '<span>2026/1 Esas</span><span>Satıldı</span></div>'
    )
    latest = first.replace("1111", "2222").replace("2026/1", "2026/2")

    class StaticPage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, html):
            self.html = html

        def content(self):
            return self.html

        def wait_for_timeout(self, milliseconds):
            return None

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=1600
    )
    probe = {
        "evidence": [
            bulk.result_payload_evidence(first),
            bulk.result_payload_evidence(latest),
        ],
        "latest_evidence": bulk.result_payload_evidence(latest),
        "latest_started_sequence": 1,
        "latest_evidence_sequence": 1,
        "pending": set(),
    }
    _, stale_verified = collector._wait_result_state(
        StaticPage(first), bulk.result_state_signature(first), probe
    )
    latest_html, latest_verified = collector._wait_result_state(
        StaticPage(latest), bulk.result_state_signature(first), probe
    )
    assert stale_verified is False
    assert latest_verified is True
    assert bulk.result_card_signature(latest_html) == (("2222", "2026/2", ""),)


def test_parser_preserves_candidate_siblings_with_same_record_ref():
        html = """
        <div class="card">
            <span>KAYIT NO: 3301</span><span>2026/33 Esas</span>
            <span>Ankara 1. İcra Dairesi</span><span>Satıldı</span>
        </div>
        <div class="card">
            <span>KAYIT NO: 3301</span><span>2026/33 Esas</span>
            <span>Ankara 2. İcra Dairesi</span><span>Satıldı</span>
        </div>
        """
        cards = bulk.parse_result_cards(html)
        assert len(cards) == 2
        institutions = {card["institution_text"] for card in cards}
        assert len(institutions) == 2
        assert all("İcra Dairesi" in institution for institution in institutions)

        payload = "2 sonuç bulundu. " + html
        evidence = bulk.result_payload_evidence(payload)
        assert len(evidence["cards"]) == 2
        assert {identity[0] for identity in evidence["cards"]} == {"3301"}
        assert len({identity[2] for identity in evidence["cards"]}) == 2
        assert bulk.dom_matches_result_evidence(payload, evidence) is True


def test_parser_keeps_stable_card_with_additional_file_reference():
        html = """
        <li class="incelenen-li 16792885364">
            <article class="box">
                <span>KAYIT NO: 16792885364</span>
                <h4 id="ilanTitle" class="box-title">2026/1612 Esas</h4>
                <span>2. İhale Ankara İcra Dairesi</span>
                <span>Açıklama içinde ilgili 2025/44 dosyasına atıf vardır.</span>
            </article>
        </li>
        """
        cards = bulk.parse_result_cards(html)
        assert len(cards) == 1
        assert cards[0]["kayit_no"] == "16792885364"
        assert cards[0]["file_id"] == "2026/1612"


def test_parser_prefers_official_title_when_reference_appears_first():
        html = """
        <li class="incelenen-li 16792885364">
            <article class="box">
                <p><span id="ihaleAciklamaSpan">İlgili 2025/44 dosyasına atıf.</span></p>
                <h4 id="ilanTitle" class="box-title">2026/1612 Esas</h4>
                <span>2. İhale Ankara İcra Dairesi</span>
            </article>
        </li>
        """
        cards = bulk.parse_result_cards(html)
        assert len(cards) == 1
        assert cards[0]["file_id"] == "2026/1612"

        no_title = html.replace(
            '<h4 id="ilanTitle" class="box-title">2026/1612 Esas</h4>',
            '<span>2026/1612 Esas</span>',
        )
        assert bulk.parse_result_cards(no_title) == []

        from sold.ingestion.uyap.collect import find_target_record_card

        selected = find_target_record_card(html, "2026/1612", target_record_ref="16792885364")
        assert selected is not None
        assert "record_ref" in selected["match_fields"]
        assert find_target_record_card(
            no_title, "2026/1612", target_record_ref="16792885364"
        ) is None

        hidden = f'<div style="display:none">{html}</div>'
        assert bulk.parse_result_cards(hidden) == []
        assert find_target_record_card(
            hidden, "2026/1612", target_record_ref="16792885364"
        ) is None

        hidden_class = f'<div class="d-none">{html}</div>'
        assert bulk.parse_result_cards(hidden_class) == []
        assert find_target_record_card(
            hidden_class, "2026/1612", target_record_ref="16792885364"
        ) is None

        hidden_descendant = """
        <li class="incelenen-li 16792885364">
            <h4 id="ilanTitle">2026/1612 Esas</h4>
            <span>Sonuç girilmemiştir</span>
            <span class="d-none">Satıldı · 2025/44 Esas</span>
        </li>
        """
        visible_cards = bulk.parse_result_cards(hidden_descendant)
        assert len(visible_cards) == 1
        assert visible_cards[0]["file_id"] == "2026/1612"
        assert visible_cards[0]["sold"] is False

        from sold.ingestion.uyap.collect import find_target_record_card

        hidden_target = """
        <div class="result-wrapper">
            <button>İhale Evrak Listesi</button>
            <div class="d-none">
                <li class="incelenen-li 16792885364">
                    <h4 id="ilanTitle">2026/1612 Esas</h4>
                </li>
            </div>
        </div>
        """
        assert find_target_record_card(
                hidden_target, "2026/1612", target_record_ref="16792885364"
        ) is None


def test_target_card_selection_rejects_multi_record_wrapper():
        from sold.ingestion.uyap.collect import find_target_record_card

        html = """
        <div class="result-wrapper">
            <li class="incelenen-li 11111111">
                <h4 id="ilanTitle">2026/77 Esas</h4><button>İhale Evrak Listesi</button>
            </li>
            <li class="incelenen-li 22222222">
                <h4 id="ilanTitle">2026/77 Esas</h4><button>İhale Evrak Listesi</button>
            </li>
        </div>
        """
        selected = find_target_record_card(
                html, "2026/77", target_record_ref="22222222"
        )
        assert selected is not None
        assert "22222222" in selected["html"]
        assert "11111111" not in selected["html"]
        parsed = bulk.parse_result_cards(html)
        assert {card["kayit_no"] for card in parsed} == {"11111111", "22222222"}

        same_ref = html.replace(
            '<h4 id="ilanTitle">2026/77 Esas</h4><button>İhale Evrak Listesi</button>',
            '<h4 id="ilanTitle">2026/77 Esas</h4><span>Ankara 1. İcra Dairesi</span><button>İhale Evrak Listesi</button>',
            1,
        ).replace(
            '<h4 id="ilanTitle">2026/77 Esas</h4><button>İhale Evrak Listesi</button>',
            '<h4 id="ilanTitle">2026/77 Esas</h4><span>Ankara 2. İcra Dairesi</span><button>İhale Evrak Listesi</button>',
            1,
        ).replace("11111111", "22222222")
        same_ref_parsed = bulk.parse_result_cards(same_ref)
        assert len(same_ref_parsed) == 2


def test_page_transition_distinguishes_same_ref_sibling_institution():
    first = """
    <div class="card"><span>KAYIT NO: 3301</span><span>2026/33 Esas</span>
    <span>Ankara 1. İcra Dairesi</span><span>Satıldı</span></div>
    """
    second = first.replace("1. İcra", "2. İcra")

    class StaticPage:
        def wait_for_timeout(self, milliseconds):
            return None

        def content(self):
            return second

    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222")
    assert collector._wait_page_loaded(
        StaticPage(), bulk.result_card_signature(first)
    ) is True


def test_page_transition_never_accepts_unstable_timeout():
    class ChangingPage:
        def __init__(self):
            self.index = 0

        def wait_for_timeout(self, milliseconds):
            self.index += 1

        def content(self):
            return (
                '<div class="card"><span>KAYIT NO: '
                f'{4000 + self.index}</span><span>2026/{self.index + 1} Esas</span>'
                '<span>Satıldı</span></div>'
            )

    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222")
    assert collector._wait_page_loaded(ChangingPage(), ()) is False


@pytest.mark.parametrize("mode", ["max_records", "acquisition_failure"])
def test_untargeted_bulk_never_completes_unresolved_page(tmp_path, monkeypatch, mode):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    cards = [_sold_card(fid="2026/41", kayit="4101")]
    if mode == "max_records":
        cards.append(_sold_card(fid="2026/42", kayit="4102"))
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: (f"{len(cards)} sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: cards)
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)
    stop = collector._run_window(
        page, object(),
        _fake_acquire_ok if mode == "max_records" else _fake_acquire_fail,
        "ANKARA", window, rec, state, summary,
        max_records=1 if mode == "max_records" else None,
        discovery_only=False, acquired_total=0,
    )
    assert stop == ("MAX_RECORDS" if mode == "max_records" else "ACQUISITION_INCOMPLETE")
    assert rec["status"] == "ACQUISITION_INCOMPLETE"
    assert rec["pages_completed"] == []


@pytest.mark.parametrize("discovery_only", [False, True])
def test_bounded_retry_advances_past_known_prefix(
    tmp_path, monkeypatch, discovery_only
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    cards = [
        _sold_card(fid="2026/51", kayit="5101"),
        _sold_card(fid="2026/52", kayit="5102"),
    ]
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: cards)
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    phase = bulk.PHASE_DISCOVERY if discovery_only else bulk.PHASE_ACQUISITION
    state, rec, first_summary = _runner_state(window, phase)

    first = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, first_summary,
        max_records=1, discovery_only=discovery_only, acquired_total=0,
    )
    if discovery_only:
        assert rec["status"] == "DISCOVERY_INCOMPLETE"
    second_summary = _runner_state(window, phase)[2]
    second = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, second_summary,
        max_records=1, discovery_only=discovery_only, acquired_total=0,
    )

    assert first == "MAX_RECORDS"
    assert second is None
    assert second_summary["records_processed"] == 1
    assert rec["status"] == "COMPLETE"
    assert len(store.load_candidates(tmp_path)) == 2


def test_bounded_untargeted_failure_does_not_starve_later_card(
    tmp_path, monkeypatch
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    cards = [
        _sold_card(fid="2026/61", kayit="6101"),
        _sold_card(fid="2026/62", kayit="6102"),
    ]
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: cards)
    calls = []

    def acquire(file_id, institution, record_ref=None):
        calls.append(record_ref)
        return (
            _fake_acquire_fail(file_id, institution, record_ref)
            if record_ref == "6101"
            else _fake_acquire_ok(file_id, institution, record_ref)
        )

    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, first_summary = _runner_state(window)
    first = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, first_summary,
        max_records=1, discovery_only=False, acquired_total=0,
    )
    second_summary = _runner_state(window)[2]
    second = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, second_summary,
        max_records=1, discovery_only=False, acquired_total=0,
    )

    assert first == "MAX_RECORDS"
    assert second == "ACQUISITION_INCOMPLETE"
    assert calls == ["6101", "6102"]
    assert second_summary["acquisitions_completed"] == 1
    assert rec["attempted_untargeted_candidate_ids"] == []
    assert rec["pages_completed"] == []

    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("sonuç bulunamadı", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [])
    third_summary = _runner_state(window)[2]
    third = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, third_summary,
        max_records=1, discovery_only=False, acquired_total=0,
    )

    assert third == "ACQUISITION_INCOMPLETE"
    assert rec["status"] == "ACQUISITION_INCOMPLETE"
    assert rec["unresolved_untargeted_candidate_ids"]
    assert rec["pages_completed"] == []


def test_resolved_failure_is_persistently_removed_before_max_records_return(
    tmp_path, monkeypatch
):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    first = _sold_card(fid="2026/63", kayit="6301")
    second = _sold_card(fid="2026/64", kayit="6302")
    first_id = bulk.deterministic_candidate_id(
        first["institution_text"], first["file_id"], first["kayit_no"]
    )
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [first, second])
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)
    rec["unresolved_untargeted_candidate_ids"] = [first_id]

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=1, discovery_only=False, acquired_total=0,
    )

    assert stop == "MAX_RECORDS"
    persisted = bulk.get_window_record(
        state, "ANKARA", window["start"], window["end"], bulk.PHASE_ACQUISITION
    )
    assert persisted["unresolved_untargeted_candidate_ids"] == []


def test_force_preserves_unresolved_failure_and_zero_stays_incomplete(
    tmp_path, monkeypatch
):
    import threading
    from sold.ingestion.uyap.collect import BrowserCollector

    class Context:
        pages = []

    class Browser:
        contexts = [Context()]

    class Playwright:
        class Chromium:
            def connect_over_cdp(self, endpoint):
                return Browser()

        chromium = Chromium()

    class Manager:
        def __enter__(self):
            return Playwright()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        BrowserCollector, "_sync_playwright",
        staticmethod(lambda: lambda: Manager()),
    )
    collector = bulk.UyapBulkCollector("http://127.0.0.1:9222", store_dir=tmp_path)
    monkeypatch.setattr(collector, "_find_gecmis_page", lambda context: object())
    monkeypatch.setattr(collector, "_start_watchdog", lambda: threading.Event())
    monkeypatch.setattr(collector, "_print_summary", lambda summary: None)
    state = bulk.load_bulk_state(tmp_path)
    rec = bulk.new_window_record(
        "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    )
    rec["status"] = "ACQUISITION_INCOMPLETE"
    rec["unresolved_untargeted_candidate_ids"] = ["candidate-A"]
    bulk.upsert_window_record(state, rec)
    bulk.save_bulk_state(state, tmp_path)

    def fake_zero(page, context, acquire, province, window, rec, state, summary,
                  max_records, discovery_only, acquired_total, target_kayit_no=None,
                  target_kayit_nos=None, target_candidate_ids_by_ref=None, force=False):
        assert rec["unresolved_untargeted_candidate_ids"] == ["candidate-A"]
        rec["status"] = "ACQUISITION_INCOMPLETE"
        return "ACQUISITION_INCOMPLETE"

    monkeypatch.setattr(collector, "_run_window", fake_zero)
    result = collector.run(
        "ANKARA", "2026-06-01", "2026-06-07", force=True, max_windows=1
    )

    assert result["stopped_reason"] == "ACQUISITION_INCOMPLETE"
    persisted = bulk.load_bulk_state(tmp_path)
    record = bulk.get_window_record(
        persisted, "ANKARA", "2026-06-01", "2026-06-07", bulk.PHASE_ACQUISITION
    )
    assert record["unresolved_untargeted_candidate_ids"] == ["candidate-A"]


def test_click_and_wait_result_tracks_and_removes_request_probe(monkeypatch):
    html = (
        '1 sonuç bulundu. <div class="card"><span>KAYIT NO: 1111</span><span>2026/1 Esas</span>'
        '<span>Satıldı</span></div>'
    )

    class Response:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status = status

        def text(self):
            return self.payload

    class Request:
        resource_type = "xhr"

        def __init__(self, payload, status=200):
            self.payload = payload
            self.status = status
            self.url = "https://esatis.uyap.gov.tr/pp/search"
            self.post_data = "start=01%2F06%2F2026&end=07%2F06%2F2026"

        def response(self):
            return Response(self.payload, self.status)

    class EventPage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

        def remove_listener(self, event, handler):
            assert self.handlers[event] is handler
            del self.handlers[event]

        def content(self):
            return html

        def wait_for_timeout(self, milliseconds):
            return None

    page = EventPage()
    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=1600
    )

    payload = {"value": html, "status": 200}

    def click(_page, before_click=None):
        pre_click = Request(html)
        page.handlers["request"](pre_click)
        page.handlers["requestfinished"](pre_click)
        if before_click is not None:
            before_click()
        request = Request(payload["value"], payload["status"])
        page.handlers["request"](request)
        page.handlers["requestfinished"](request)
        return True

    monkeypatch.setattr(collector, "_click_ara", click)
    filters = (("01/06/2026", "2026-06-01"), ("07/06/2026", "2026-06-07"))
    clicked, result_html, verified = collector._click_and_wait_result(
        page, bulk.result_state_signature(html), filters
    )

    assert clicked is verified is True
    assert bulk.result_card_signature(result_html) == (("1111", "2026/1", ""),)
    assert page.handlers == {}

    payload["value"] = '{"heartbeat":"ok"}'
    _, _, heartbeat_verified = collector._click_and_wait_result(
        page, bulk.result_state_signature(html), filters
    )
    assert heartbeat_verified is False
    assert page.handlers == {}

    payload.update(value=html, status=500)
    _, _, failed_verified = collector._click_and_wait_result(
        page, bulk.result_state_signature(html), filters
    )
    assert failed_verified is False
    assert page.handlers == {}


def test_wait_result_state_rejects_stale_zero_and_accepts_stable_new_zero():
    old_zero = '<div class="alert">Sonuç bulunamadı</div>'
    old_card = (
        '<div class="card"><span>KAYIT NO: 3333</span><span>2026/3 Esas</span>'
        '<span>Satıldı</span></div>'
    )

    class SequencePage:
        url = "https://esatis.uyap.gov.tr/pp/index.jsp"

        def __init__(self, values):
            self.values = values
            self.index = 0

        def content(self):
            return self.values[min(self.index, len(self.values) - 1)]

        def wait_for_timeout(self, milliseconds):
            self.index += 1

    collector = bulk.UyapBulkCollector(
        "http://127.0.0.1:9222", result_timeout_ms=2800
    )
    _, stale_confirmed = collector._wait_result_state(
        SequencePage([old_zero] * 7), bulk.result_state_signature(old_zero)
    )
    zero_html, zero_confirmed = collector._wait_result_state(
        SequencePage([old_card, "yükleniyor", old_zero, old_zero, old_zero, old_zero]),
        bulk.result_state_signature(old_card),
    )

    assert stale_confirmed is False
    assert zero_confirmed is True
    assert bulk.zero_results(zero_html) is True


def test_targeted_dense_window_is_not_split(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: (
            "200 sonuç bulundu. Toplam 10 sayfa. Her sayfada 20 kayıt", True
        ),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [_sold_card(kayit="5001")])
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"5001"},
    )

    assert stop is None
    assert rec["status"] == "COMPLETE"
    assert summary["acquisitions_completed"] == 1


def test_unsplittable_saturated_day_is_never_complete(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: (
            "Toplam 10 sayfa. Her sayfada 20 kayıt", True
        ),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: list(range(1, 11)))
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [_sold_card(kayit="5101")])
    window = {"start": "2026-06-01", "end": "2026-06-01"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop == "SATURATED_UNRESOLVED"
    assert rec["status"] == "SATURATED_UNRESOLVED"
    assert rec["pages_completed"] == []
    assert summary["sold_discovered"] == 1


def test_pagination_failure_resets_target_page_checkpoints(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1, 2])
    monkeypatch.setattr(
        collector, "_goto_page",
        lambda page, number: (setattr(page, "current_page", number) or True) if number == 1 else False,
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [_sold_card(kayit="6001")])
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window)

    stop = collector._run_window(
        page, object(), _fake_acquire_fail, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"6001"},
    )

    assert stop == "PAGINATION_INCOMPLETE"
    assert rec["status"] == "ACQUISITION_INCOMPLETE"
    assert rec["pages_completed"] == []
    assert rec["pending_record_refs"] == ["6001"]


def test_fresh_search_revalidates_prior_page_checkpoints(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1, 2])
    visited = []
    monkeypatch.setattr(
        collector,
        "_goto_page",
        lambda page, number: (
            visited.append(number)
            or setattr(page, "current_page", number)
            or True
        ),
    )
    monkeypatch.setattr(
        bulk,
        "parse_result_cards",
        lambda html: [
            _sold_card(kayit="6051" if page.current_page == 1 else "6052")
        ],
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)
    rec["pages_completed"] = [1]
    rec["result_cards_inspected"] = 99
    rec["parsed_unique_count"] = 1

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop is None
    assert visited == [1, 2]
    assert rec["page_checkpoint_reset_reason"] == "fresh_search_revalidation"
    assert rec["result_cards_inspected"] == 2
    assert "parsed_unique_count" not in rec
    assert rec["status"] == "COMPLETE"


def test_known_result_count_mismatch_never_completes_window(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1])
    monkeypatch.setattr(
        bulk, "parse_result_cards", lambda html: [_sold_card(kayit="6061")]
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop == "RESULT_COUNT_MISMATCH"
    assert rec["status"] == "RESULT_COUNT_MISMATCH"
    assert rec["parsed_unique_count"] == 1
    assert rec["pages_completed"] == []


def test_known_result_count_overmatch_never_completes_window(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("1 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1])
    monkeypatch.setattr(
        bulk,
        "parse_result_cards",
        lambda html: [_sold_card(kayit="6071"), _sold_card(kayit="6072")],
    )
    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, summary = _runner_state(window, bulk.PHASE_DISCOVERY)

    stop = collector._run_window(
        page, object(), _fake_acquire_ok, "ANKARA", window, rec, state, summary,
        max_records=None, discovery_only=True, acquired_total=0,
    )

    assert stop == "RESULT_COUNT_MISMATCH"
    assert rec["parsed_unique_count"] == 2
    assert rec["pages_completed"] == []


def test_retry_cursor_allows_later_page_target_with_max_one(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("2 sonuç bulundu", True),
    )
    monkeypatch.setattr(collector, "_valid_pages", lambda page, meta: [1, 2])
    monkeypatch.setattr(
        bulk, "parse_result_cards",
        lambda html: [
            _sold_card(kayit="7001") if page.current_page == 1 else _sold_card(kayit="7002")
        ],
    )

    def acquire(file_id, institution, record_ref=None):
        return (
            _fake_acquire_fail(file_id, institution, record_ref)
            if record_ref == "7001"
            else _fake_acquire_ok(file_id, institution, record_ref)
        )

    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, first_summary = _runner_state(window)
    first = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, first_summary,
        max_records=1, discovery_only=False, acquired_total=0,
        target_kayit_nos={"7001", "7002"},
    )
    assert first == "MAX_RECORDS"
    assert rec["attempted_record_refs"] == ["7001"]

    page.current_page = 1
    second_summary = _runner_state(window)[2]
    second = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, second_summary,
        max_records=1, discovery_only=False, acquired_total=0,
        target_kayit_nos={"7001", "7002"},
    )

    assert second == "ACQUISITION_INCOMPLETE"
    assert second_summary["acquisitions_completed"] == 1
    assert rec["pending_record_refs"] == ["7001"]


def test_absent_target_does_not_permanently_suppress_failed_target(tmp_path, monkeypatch):
    collector, page = _runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector, "_wait_result_state",
        lambda page, baseline=None: ("1 sonuç bulundu", True),
    )
    monkeypatch.setattr(bulk, "parse_result_cards", lambda html: [_sold_card(kayit="7101")])
    calls = []

    def acquire(file_id, institution, record_ref=None):
        calls.append(record_ref)
        return (
            _fake_acquire_fail(file_id, institution, record_ref)
            if len(calls) == 1 else _fake_acquire_ok(file_id, institution, record_ref)
        )

    window = {"start": "2026-06-01", "end": "2026-06-07"}
    state, rec, first_summary = _runner_state(window)
    first = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, first_summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"7101", "7102"},
    )
    second_summary = _runner_state(window)[2]
    second = collector._run_window(
        page, object(), acquire, "ANKARA", window, rec, state, second_summary,
        max_records=None, discovery_only=False, acquired_total=0,
        target_kayit_nos={"7101", "7102"},
    )

    assert first == second == "ACQUISITION_INCOMPLETE"
    assert calls == ["7101", "7101"]
    assert second_summary["acquisitions_completed"] == 1
    assert rec["pending_record_refs"] == ["7102"]


# --------------------------------------------------------------------------- #
# 7) Keşif dayanıklılığı — satılan açık artırma belge ediniminden ÖNCE kalıcılaşır.
# --------------------------------------------------------------------------- #
def _sold_card(fid="2026/263", kayit="16701234"):
    return {
        "kayit_no": kayit,
        "file_id": fid,
        "institution_text": "Ankara 12. İcra Dairesi",
        "source_status_raw": "Satıldı · Satış İşlemleri Tamamlandı",
        "category": "SOLD",
        "sold": True,
        "card_html": "<div/>",
        "card_text": f"KAYIT NO {kayit} {fid} Esas Satıldı Satış İşlemleri Tamamlandı",
    }


def _fake_acquire_ok(file_id, institution, record_ref=None):
    text = (
        "Artırma Sonuç Tutanağı İhale Bedeli 5.715.000,00 TL "
        "Muhammen Bedel 6.800.000,00 TL Satıldı Satış İşlemleri Tamamlandı "
        "50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm"
    )
    arts = [{"artifact_type": "auction_result", "text": text, "source_ref": "path"}]
    return arts, [{"label": "auction_result", "pattern": "native_udf"}], {"ok": True}


def _fake_acquire_fail(file_id, institution, record_ref=None):
    raise RuntimeError("same_row_download_control_not_located_uniquely")


def test_discovery_persisted_before_acquisition(tmp_path):
    gp = tmp_path / "genuine_uyap.json"  # yok → DUPLICATE yok
    res = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_ok, store_dir=tmp_path,
        genuine_path=gp, discovery_only=True, province_label="ANKARA",
    )
    assert res["discovered"] is True and res["acquired"] is False
    cand = store.get_candidate(res["candidate_id"], tmp_path)
    assert cand is not None and cand["state"] == STATE_DISCOVERED
    assert cand["file_id"] == "2026/263" and cand["bulk"]["kayit_no"] == "16701234"


def test_batch_discovery_writes_candidate_store_once_per_page(tmp_path, monkeypatch):
    cards = [
        _sold_card(fid=f"2026/{index + 1}", kayit=f"17{index:09d}")
        for index in range(100)
    ]
    original = store.save_candidates
    writes = {"count": 0}

    def counted_save(candidates, store_dir=None):
        writes["count"] += 1
        return original(candidates, store_dir)

    monkeypatch.setattr(store, "save_candidates", counted_save)

    first = bulk.persist_discovered_cards(
        cards,
        store_dir=tmp_path,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    assert len(first) == 100
    assert writes["count"] == 1
    assert len(store.load_candidates(tmp_path)) == 100

    bulk.persist_discovered_cards(
        cards,
        store_dir=tmp_path,
        province_label="ANKARA",
        window={"start": "2026-06-01", "end": "2026-06-07"},
    )
    assert writes["count"] == 2
    assert len(store.load_candidates(tmp_path)) == 100


def test_discovery_survives_acquisition_failure(tmp_path):
    gp = tmp_path / "genuine_uyap.json"
    res = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_fail, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    assert res["outcome"] == "acquisition_failed"
    cand = store.get_candidate(res["candidate_id"], tmp_path)
    # keşif korunur (kayıt bilinir kalır), hata yapısal olarak kaydedilir
    assert cand is not None and cand["file_id"] == "2026/263"
    assert cand["bulk"]["last_acquisition_error"]


def test_empty_native_collection_is_retryable_acquisition_failure(tmp_path):
    res = bulk.process_sold_auction(
        _sold_card(),
        acquire_documents=lambda *args, **kwargs: (
            [], [], {"blocking_reason": "target_document_list_not_found"}
        ),
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine_uyap.json",
        province_label="ANKARA",
    )
    candidate = store.get_candidate(res["candidate_id"], tmp_path)

    assert res["outcome"] == "acquisition_failed"
    assert res["error"] == "native_document_collection_empty"
    assert candidate["bulk"]["collection_diagnostics"]["blocking_reason"]
    assert candidate.get("audit") is None
    assert bulk.should_acquire(candidate) is True

    recovered = bulk.process_sold_auction(
        _sold_card(),
        acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path,
        genuine_path=tmp_path / "genuine_uyap.json",
        province_label="ANKARA",
        force=True,
    )
    recovered_candidate = store.get_candidate(recovered["candidate_id"], tmp_path)
    assert recovered["outcome"] == "acquired"
    assert "last_acquisition_error" not in recovered_candidate["bulk"]


def test_same_esas_different_kayit_no_are_distinct_candidates(tmp_path):
    # Bir Esas'ın (2026/12) birden çok açık artırması (farklı KAYIT NO) AYRI aday olmalı —
    # yoksa distinkt açık artırmalar kaybolur (canlı Ankara koşusunda 13 → 8 çökmesi).
    gp = tmp_path / "genuine_uyap.json"
    r1 = bulk.process_sold_auction(
        _sold_card(fid="2026/12", kayit="16760703976"), acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path, genuine_path=gp, discovery_only=True, province_label="ANKARA",
    )
    r2 = bulk.process_sold_auction(
        _sold_card(fid="2026/12", kayit="16760703981"), acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path, genuine_path=gp, discovery_only=True, province_label="ANKARA",
    )
    assert r1["candidate_id"] != r2["candidate_id"]
    assert len(store.load_candidates(tmp_path)) == 2
    # aynı KAYIT NO tekrar → idempotent (kopya yok)
    r1b = bulk.process_sold_auction(
        _sold_card(fid="2026/12", kayit="16760703976"), acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path, genuine_path=gp, discovery_only=True, province_label="ANKARA",
    )
    assert r1b["candidate_id"] == r1["candidate_id"]
    assert len(store.load_candidates(tmp_path)) == 2



# --------------------------------------------------------------------------- #
# 8) Yeniden başlama — edinilmiş açık artırma tekrar işlenmez; kısmi olan devam eder.
# --------------------------------------------------------------------------- #
def test_resume_skips_completed_and_resumes_incomplete(tmp_path):
    gp = tmp_path / "genuine_uyap.json"
    first = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_ok, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    assert first["outcome"] == "acquired" and first["audit_decision"] in AUDIT_DECISIONS
    # ikinci kez → zaten edinilmiş → atlanır (aynı belgeler tekrar indirilmez)
    second = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_fail, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    assert second["outcome"] == "skipped_already_acquired"

    # durum-tabanlı yeniden başlama sözleşmesi
    assert bulk.acquisition_state(None) == "new"
    assert bulk.should_acquire({"state": STATE_DISCOVERED}) is True          # keşfedildi → edin
    assert bulk.should_acquire({"state": STATE_COLLECTED}) is True           # kısmi → devam
    assert bulk.should_acquire({"state": STATE_AUDITED}) is False            # tamam → atla
    assert bulk.should_acquire({"state": STATE_PENDING_REVIEW}) is False
    assert bulk.should_acquire({"state": STATE_EXCLUDED}) is False           # terminal-red ≠ çekme hatası


def test_force_reacquires_known_candidate_but_not_admitted(tmp_path):
    # --force: bilinen (denetlenmiş) adayı GÜNCEL toplama koduyla YENİDEN edinir. Canlı vaka:
    # 2026/23 önceki BOZUK koşudan MISSING_APPRAISAL cache'lenmişti; force olmadan atlanıyordu.
    gp = tmp_path / "genuine_uyap.json"
    first = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_ok, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    assert first["outcome"] == "acquired"
    # force olmadan → bilinen aday atlanır (mevcut sözleşme korunur)
    skip = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_fail, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    assert skip["outcome"] == "skipped_already_acquired"
    # force ile → edinici GERÇEKTEN yeniden çağrılır ve yeniden edinilir
    calls = {"n": 0}

    def _counting_acquire(file_id, institution, record_ref=None):
        calls["n"] += 1
        return _fake_acquire_ok(file_id, institution)

    forced = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_counting_acquire, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA", force=True,
    )
    assert forced["outcome"] == "acquired" and calls["n"] == 1
    # ADMİSYON yapılmış aday force ile BİLE yeniden edinilmez (açık insan admisyonu korunur)
    cand = store.get_candidate(forced["candidate_id"], tmp_path)
    cand["admitted_public_record_id"] = "UYAP-ADMITTED-1"
    store.upsert(cand, tmp_path)
    guarded = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_fail, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA", force=True,
    )
    assert guarded["outcome"] == "skipped_already_acquired"


def test_genuine_public_record_id_uses_kayit_no_not_esas():
    # PAYLAŞILAN-Esas: genuine kimlik KAYIT NO olmalı (mevcut 7 kayıt 11-haneli KAYIT NO kullanır);
    # public_record_id=Esas olsaydı aynı Esas'ın farklı açık artırmaları admitte TEK kayda ÇÖKERdi.
    from sold.ingestion.uyap.admit import build_genuine_record
    from sold.ingestion.uyap.models import ADMISSIBLE_COMPLETED_SALE
    cand = {
        "file_id": "2026/12", "kayit_no": "16760703981",
        "extracted": {"property_type": "konut", "completion_datetime": "16.06.2026"},
        "audit": {"decision": ADMISSIBLE_COMPLETED_SALE, "appraisal_value": 2000000.0, "auction_price": 1800000.0},
        "bulk": {"province_label": "ANKARA", "kayit_no": "16760703981"},
    }
    rec = build_genuine_record(cand)
    assert rec["public_record_id"] == "16760703981"          # KAYIT NO, Esas değil
    assert rec["province"] == "Ankara"
    cand2 = dict(cand, kayit_no="16760703976", bulk={"province_label": "ANKARA", "kayit_no": "16760703976"})
    assert build_genuine_record(cand2)["public_record_id"] == "16760703976"   # aynı Esas, farklı kayıt → çökmez


# --------------------------------------------------------------------------- #
# 9) Modal hatası — bir açık artırmanın hatası sonrakinin kimliğini/durumunu bozmaz.
# --------------------------------------------------------------------------- #
def test_modal_failure_isolated_from_next_auction(tmp_path):
    gp = tmp_path / "genuine_uyap.json"
    bad = bulk.process_sold_auction(
        _sold_card(fid="2026/263", kayit="16700001"), acquire_documents=_fake_acquire_fail,
        store_dir=tmp_path, genuine_path=gp, province_label="ANKARA",
    )
    good = bulk.process_sold_auction(
        _sold_card(fid="2026/500", kayit="16700002"), acquire_documents=_fake_acquire_ok,
        store_dir=tmp_path, genuine_path=gp, province_label="ANKARA",
    )
    assert bad["outcome"] == "acquisition_failed" and bad["file_id"] == "2026/263"
    assert good["outcome"] == "acquired" and good["file_id"] == "2026/500"
    # iki ayrı aday, kimlikleri karışmadı
    assert bad["candidate_id"] != good["candidate_id"]
    cg = store.get_candidate(good["candidate_id"], tmp_path)
    assert cg["file_id"] == "2026/500" and cg["audit"] is not None


# --------------------------------------------------------------------------- #
# 10) Mevcut kanıt-yolu yeniden kullanımı — YENİ izin-gevşek parser/audit YOK.
# --------------------------------------------------------------------------- #
def test_reuses_existing_extract_audit_pipeline_no_admission(tmp_path):
    from sold.ingestion.uyap.pilot import genuine_fingerprint

    gp = tmp_path / "genuine_uyap.json"
    before = genuine_fingerprint()  # GERÇEK genuine set (yalnız okur; değişmemeli)
    res = bulk.process_sold_auction(
        _sold_card(), acquire_documents=_fake_acquire_ok, store_dir=tmp_path,
        genuine_path=gp, province_label="ANKARA",
    )
    cand = store.get_candidate(res["candidate_id"], tmp_path)
    # mevcut boru hattı çalıştı: extracted + audit dolduruldu (yeni parser değil)
    assert cand["extracted"] is not None
    assert cand["audit"] is not None and cand["audit"]["decision"] in AUDIT_DECISIONS
    assert cand["state"] in (STATE_AUDITED, STATE_PENDING_REVIEW)
    # ADMİSYON YAPILMADI: aday genuine sete yazılmadı, genuine set değişmedi
    assert cand["admitted_public_record_id"] is None
    assert not gp.exists()
    after = genuine_fingerprint()
    assert after["sha256"] == before["sha256"]
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"]  # process_sold_auction genuine'i DEĞİŞTİRMEZ (mutlak sayıdan bağımsız)


# --------------------------------------------------------------------------- #
# 11) Oturum sona ermesi (giriş sayfası ≠ sıfır sonuç) + sıfır-sonuç.
# --------------------------------------------------------------------------- #
def test_session_expiration_vs_zero_results():
    login_html = "<html><body>e-Devlet ile Giriş Yap · T.C. Kimlik No · Güvenli Giriş</body></html>"
    exp = bulk.detect_session_expiration(login_html, "https://giris.turkiye.gov.tr/Giris/")
    assert exp["expired"] is True and exp["reason"] == "login_or_authentication_page_detected"

    zero_html = "<html><body>Geçmiş İlanlar · 0 sonuç bulundu.</body></html>"
    exp2 = bulk.detect_session_expiration(zero_html, "https://esatis.uyap.gov.tr/")
    assert exp2["expired"] is False                       # sıfır sonuç oturum kaybı DEĞİL
    assert bulk.zero_results(zero_html) is True

    results_html = "<html><body>Geçmiş İlanlar · 32 sonuç bulundu.</body></html>"
    assert bulk.detect_session_expiration(results_html, "https://esatis.uyap.gov.tr/")["expired"] is False
    assert bulk.zero_results(results_html) is False


# --------------------------------------------------------------------------- #
# 12) DONMUŞ ÇEKİRDEK regresyonu — 4 SMM momenti, TOKİ 0-SMM, conditional_on_trade.
# --------------------------------------------------------------------------- #
def test_structural_freeze_four_moments_no_sale_prob():
    from sold.structural import build_observed_moments, load_genuine_datasets

    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    smm = {k for k in built["moments"] if k.startswith(("uyap_win", "kap_log"))}
    assert smm == {"uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
                   "kap_log_ratio_mean", "kap_log_ratio_sd"}
    assert "uyap_sale_prob" not in built["moments"]


def test_toki_external_zero_moments_and_conditional_on_trade():
    from sold.structural import (
        DEFAULT_FREE,
        PRICE_ESTIMATE_CONDITION,
        StructuralParams,
        build_observed_moments,
        context_from_datasets,
        load_genuine_datasets,
        source_jacobian_ranks,
    )

    assert PRICE_ESTIMATE_CONDITION == "conditional_on_trade"
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    sj = source_jacobian_ranks(StructuralParams(), ctx, DEFAULT_FREE, built["moments"], built["provenance"])
    assert sj["J_TOKI"]["rank"] == 0 and sj["J_TOKI"]["n_moments"] == 0


def test_bulk_layer_defines_no_new_admission_path():
    # toplu katman yalnızca discover + run_extract + run_audit'i yeniden kullanır;
    # kendi admit/genuine-yazma işlevi YOKTUR (admisyon açık insan adımı olarak kalır).
    import inspect

    src = inspect.getsource(bulk)
    assert "admit_candidate" not in src and "build_genuine_record" not in src
    assert "def admit" not in src


# --------------------------------------------------------------------------- #
# 13) Form-yapısı tanısı (read-only) — canlı seçicileri gerçek DOM'a eşlemek için.
# --------------------------------------------------------------------------- #
_FORM_HTML = """
<form>
  <label>Kategori</label>
  <input type="radio" name="kategori" value="tasinir"> Taşınır
  <input type="radio" name="kategori" value="tasinmaz"> Taşınmaz
  <input type="radio" name="kategori" value="tasit"> Taşıt
  <label for="ilId">İl</label>
  <select id="ilId" name="il"><option>Seçiniz</option><option>ANKARA</option></select>
  <label>Birim</label>
  <select id="birimId" name="birim"><option>Seçiniz</option></select>
  <label>Dosya No</label>
  <input type="text" id="dosyaNo" name="dosyaNo" placeholder="2026/263">
  <label>İhale Bitiş Tarih Aralıklarını Seçiniz</label>
  <input type="text" id="baslangicTarih" name="baslangicTarih" placeholder="gg/aa/yyyy" readonly value="">
  <input type="text" id="bitisTarih" name="bitisTarih" placeholder="gg/aa/yyyy" readonly value="">
  <a id="araLink" class="btn btn-primary" onclick="doSearch()">Ara</a>
  <button type="button" id="araBtn" class="btn-ara">ARA</button>
</form>
"""


def test_summarize_form_controls_exposes_structure_privacy_safe():
    d = bulk.summarize_form_controls(_FORM_HTML)
    assert d["markers"]["has_tasinmaz"] is True
    assert d["markers"]["has_date_label"] is True
    assert d["markers"]["has_ara_button"] is True
    ids = {i["id"] for i in d["inputs"]}
    assert {"baslangicTarih", "bitisTarih", "dosyaNo"} <= ids
    # readonly tarih kutuları POZİTİF raporlanır (düz fill() çalışmaz → takvim gerekebilir)
    date_inputs = [i for i in d["inputs"] if i["id"] in ("baslangicTarih", "bitisTarih")]
    assert len(date_inputs) == 2 and all(i["readonly"] for i in date_inputs)
    assert {s["id"] for s in d["selects"]} >= {"ilId", "birimId"}
    assert any(b["text"] == "ARA" for b in d["buttons"])
    # gizlilik-güvenli: alan DEĞERLERİ yok, yalnızca varlık bayrağı
    assert all("value" not in i for i in d["inputs"])
    assert all(i["value_present"] is False for i in date_inputs)


def test_summarize_captures_label_and_action_candidates():
    d = bulk.summarize_form_controls(_FORM_HTML)
    # etiket ilişkilendirme (for=)
    il_select = next(s for s in d["selects"] if s["id"] == "ilId")
    assert il_select["label"] == "İl"
    # ARA çoğu zaman <a class=btn> — aksiyon adayı olarak yakalanır (buton olmasa da)
    acts = d.get("action_candidates", [])
    assert any(a["id"] == "araLink" and a["tag"] == "a" for a in acts)
    assert any((a.get("text") or "").strip().lower() == "ara" for a in acts)


_RESULTS_HTML = """
<div class="sonucListe">
  <div class="ilan-card"><span>KAYIT NO: 16701234</span><span>2026/263 Esas</span>
    <span>Ankara 12. İcra Dairesi</span><span>Satıldı</span>
    <a class="btn">İncele</a><a class="btn" id="detailButton">İhale Evrak Listesi</a></div>
  <div class="ilan-card"><span>KAYIT NO: 16709999</span><span>2026/999 Esas</span>
    <span>Ankara 5. İcra Dairesi</span><span>Satıldı</span>
    <a class="btn">İncele</a></div>
</div>
<div>32 sonuç bulundu.</div>
<ul class="pagination"><li><a>0</a></li><li><a>1</a></li>
  <li><a href="#">2</a></li><li><a onclick="go()">Sonraki</a></li></ul>
"""


def test_summarize_result_structure_reports_cards_and_skeleton():
    d = bulk.summarize_result_structure(_RESULTS_HTML)
    assert d["result_count"] == 32
    assert d["parsed_card_count"] == 2
    # tekrarlı kart yapısı: tek-dosya-kimlikli + durumlu elemanlar tespit edilir
    assert any(c["single_file_id"] >= 2 and c["with_status"] >= 2 for c in d["candidates"])
    # iskelet metin İÇERMEZ, yalnız tag/class
    sk = d["first_card_skeleton"]
    assert sk is not None and sk["tag"] == "div"
    assert "text" not in sk
    # sayfalama kontrolleri + sayım banner (canlı seçicileri eşlemek için)
    assert any(p["text"] == "2" for p in d["pagination"])
    assert any(p["text"] == "Sonraki" for p in d["pagination"])
    assert any("32" in b for b in d["count_banners"])


def test_result_card_signature_detects_page_change():
    # aynı kartlar → aynı imza; farklı kartlar → farklı imza (sayfa 2 = sayfa 1 tuzağını yakalar)
    sig1 = bulk.result_card_signature(_INCELENEN_HTML)
    assert sig1 == bulk.result_card_signature(_INCELENEN_HTML) and len(sig1) == 2
    other = _INCELENEN_HTML.replace("16760761856", "19999999999").replace("2026/263", "2026/777")
    assert bulk.result_card_signature(other) != sig1


def test_summarize_document_area_detects_evrak_modal():
    html = ('<div class="modal in" id="evrakModal"><div class="modal-body">'
            'Satış İlanı · 1- Bilirkişi Raporu · Artırma Sonuç Tutanağı</div></div>')
    areas = bulk.summarize_document_area(html)
    m = next((a for a in areas if a["id"] == "evrakModal"), None)
    assert m is not None
    assert "satis ilani" in m["doc_tokens"] and "bilirkisi" in m["doc_tokens"]
    assert "artirma sonuc" in m["doc_tokens"]
    assert "text" not in m  # kişisel metin dönmez, yalnız token varlığı


def test_document_modal_skeleton_reveals_rows():
    html = ('<div id="ihale_evraklari_modal" class="modal fade in"><div class="modal-body">'
            '<table><tr class="evrak-row"><td>Satış İlanı</td>'
            '<td><a href="javascript:;" onclick="dl()" class="indir">indir</a>'
            '<a href="javascript:;" onclick="gor()" class="goruntule">gör</a></td></tr>'
            '<tr class="evrak-row"><td>1- Bilirkişi Raporu</td>'
            '<td><a href="javascript:;" onclick="dl2()">indir</a></td></tr></table></div></div>')
    sk = bulk.document_modal_skeleton(html)
    assert sk is not None and sk["id"] == "ihale_evraklari_modal"
    flat = str(sk)
    assert "evrak-row" in flat            # satır yapısı görünür
    assert "onclick" in flat and "indir" in flat  # indirme/göz kontrolleri + onclick görünür



def test_digits_tolerates_date_mask_format():
    assert bulk._digits("10/06/2026") == bulk._digits("10.06.2026") == "10062026"


def test_scope_open_document_modal_excludes_stray_labels():
    from sold.ingestion.uyap.collect import detect_document_list, scope_open_document_modal

    # sayfada modal DIŞINDA başıboş bir belge etiketi (Bilirkişi Raporu) + açık modal (Satış İlanı + Artırma Sonuç)
    html = (
        '<div class="card">Bilirkişi Raporu</div>'
        '<div id="ihale_evraklari_modal" class="modal fade bs-modal-lg in">'
        '<div class="modal-header"><h4 class="modal-title">İhale Evrak Listesi</h4></div>'
        '<div class="modal-body"><div id="ihaleEvrakListesiResult">'
        '<div class="margin-top-15"><button onclick="d()">Satış İlanı<i class="fa fa-arrow-down"></i></button>'
        '<button onclick="v()"><i class="fa fa-eye"></i></button></div>'
        '<div class="margin-top-15"><button onclick="d()">1- Artırma Sonuç / Uzatma Tutanağı<i class="fa fa-arrow-down"></i></button>'
        '<button onclick="v()"><i class="fa fa-eye"></i></button></div>'
        '</div></div></div>'
    )
    # Tüm-sayfa algılama başıboş etiket yüzünden BAŞARISIZ (ortak-ata body'ye genişler)
    assert detect_document_list(html)["detected"] is False
    # Modala scope edilince BAŞARILI + belge türleri tanınır
    scoped = scope_open_document_modal(html)
    assert scoped is not None
    det = detect_document_list(scoped)
    assert det["detected"] is True
    assert set(det["recognized_types"]) >= {"sale_notice", "auction_result"}

