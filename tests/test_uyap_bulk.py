"""UYAP TOPLU (bulk) keşif + iterasyon katmanı — OFFLINE testler (canlı UYAP oturumu GEREKMEZ).

Kaydırma/tıklama/canlı DOM yolları ``# pragma: no cover``dır; burada SAF orkestrasyon çekirdeği
(tarih pencereleri, page-0-hariç sayfalama, Satıldı-yalnız süzme, kart-yerel eşleme, uzun sayfa,
kalıcılık/yeniden-başlama, oturum-sona-ermesi, mevcut boru hattını yeniden kullanım) fixture/mock
ile test edilir. Yapısal ekonometrik çekirdeğin donmuş olduğu ayrıca doğrulanır.
"""

from __future__ import annotations

import datetime as dt

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


def test_result_metadata_parsing():
    meta = bulk.extract_result_metadata(
        "32 sonuç bulundu. Toplam 2 sayfa içerisinde 1. sayfayı görmektesiniz. "
        "Her sayfada 20 kayıt gösterilir."
    )
    assert meta == {"result_count": 32, "total_pages": 2, "current_page": 1, "per_page": 20}


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


def _fake_acquire_ok(file_id, institution):
    text = (
        "Artırma Sonuç Tutanağı İhale Bedeli 5.715.000,00 TL "
        "Muhammen Bedel 6.800.000,00 TL Satıldı Satış İşlemleri Tamamlandı "
        "50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm"
    )
    arts = [{"artifact_type": "auction_result", "text": text, "source_ref": "path"}]
    return arts, [{"label": "auction_result", "pattern": "native_udf"}], {"ok": True}


def _fake_acquire_fail(file_id, institution):
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
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] == 7


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
  <label>İl</label>
  <select id="ilId" name="il"><option>Seçiniz</option><option>ANKARA</option></select>
  <label>Birim</label>
  <select id="birimId" name="birim"><option>Seçiniz</option></select>
  <label>Dosya No</label>
  <input type="text" id="dosyaNo" name="dosyaNo" placeholder="2026/263">
  <label>İhale Bitiş Tarih Aralıklarını Seçiniz</label>
  <input type="text" id="baslangicTarih" name="baslangicTarih" placeholder="gg/aa/yyyy" readonly value="">
  <input type="text" id="bitisTarih" name="bitisTarih" placeholder="gg/aa/yyyy" readonly value="">
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


def test_digits_tolerates_date_mask_format():
    assert bulk._digits("10/06/2026") == bulk._digits("10.06.2026") == "10062026"

