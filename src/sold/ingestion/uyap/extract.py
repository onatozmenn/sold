"""Çıkarım (extraction) — kaynak artifact'lardan DETERMİNİSTİK alan çıkarımı (admisyon DEĞİL).

Yalnızca deterministik ayrıştırma + açık kanıt kuralları kullanılır. ML güven skoru, zayıf
denetim ya da sınıflandırıcı YOKTUR. Her ekonomik alan, çıkarıldığı artifact türüne
(provenans) izlenir. Çıkarım ADMİSYON DEĞİLdİr; yalnızca ``ExtractedEvidence`` üretir.

Etiket eşleştirme Türkçe-duyarsızdır (uzunluk-koruyan ASCII-fold → ofsetler orijinal metinle
hizalı kalır, tutarlar orijinal Türk sayı biçiminden okunur).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import (
    APPRAISAL_LABELS,
    ARTIFACT_APPRAISAL_REPORT,
    ARTIFACT_AUCTION_RESULT,
    ARTIFACT_SALE_NOTICE,
    ARTIFACT_STATUS_CARD,
    IHALE_LABELS,
    MONEY_LITERAL_RE,
    NON_TERMINAL_STATUS_TOKENS,
    TERMINAL_SALE_TOKENS,
    ExtractedEvidence,
    _ascii_lower,
    demojibake,
    parse_tl_amount,
)


def _artifact_raw(artifact: dict) -> str:
    """Artifact ham metni: inline ``text`` ya da ``local_path`` (dosya)."""
    text = artifact.get("text")
    if text is None and artifact.get("local_path"):
        p = Path(artifact["local_path"])
        if p.exists():
            data = p.read_bytes()
            declared = artifact.get("sha256")
            if declared and hashlib.sha256(data).hexdigest() != str(declared):
                raise ValueError(f"artifact sha256 mismatch: {p}")
            if p.suffix.lower() == ".udf":
                from .udf import extract_udf_source_text

                text, _ = extract_udf_source_text(data)
            else:
                text = data.decode("utf-8", errors="ignore")
    return text or ""


def _html_to_text(raw: str, sep: str) -> str:
    """HTML → düz metin (bs4 varsa ``separator=sep``; yoksa kaba etiket temizliği). Düz metin AYNEN döner."""
    if "<" in raw and ">" in raw:
        try:
            from bs4 import BeautifulSoup

            return BeautifulSoup(raw, "html.parser").get_text(separator=sep)
        except Exception:
            return re.sub(r"<[^>]+>", sep, raw)
    return raw


def _artifact_text(artifact: dict) -> str:
    """Artifact metnini TEK-SATIR (collapsed) döndürür — mojibake onarılır (Fix 10). Geriye-uyumlu."""
    return re.sub(r"\s+", " ", demojibake(_html_to_text(_artifact_raw(artifact), " ")))


def _segments(text: str) -> list[str]:
    """Kaynak metni satır/blok sınırlarına böler (inner_text/HTML newline'ları KORUNUR).

    Alan-etiketi/değer düzenini (aynı-satır / sonraki-satır / bitişik-blok) ayırt edebilmek için
    newline'lar sınır sayılır; satır-içi boşluk (nbsp dahil) sadeleştirilir. Boş segmentler atılır.
    """
    raw = re.split(r"[\r\n]+", str(text or ""))
    return [re.sub(r"[ \t\u00a0]+", " ", s).strip() for s in raw if s and s.strip()]


def _artifact_segments(artifact: dict) -> list[str]:
    """Artifact'ı newline-KORUYAN segmentlere böler (mojibake onarılır). Alan-sınırlı çıkarım için."""
    return _segments(demojibake(_html_to_text(_artifact_raw(artifact), "\n")))


# Fix 10: bir alanın DEĞER bölgesi, bir SONRAKİ alan/kimlik etiketiyle SINIRLANIR (label-bounded).
# Böylece bir alanın penceresi komşu alanın sayısını / taşınmaz kimliğini (ada/parsel/no/kat) YUTMAZ.
_VALUE_BOUNDARY_RE = re.compile(
    r"\b(?:muhammen\s+bedel|muhammen\s+kiymet|kiymeti|takdir\s+olunan\s+deger|tasinmazin\s+degeri|"
    r"ihale\s+bedeli|satis\s+tutari|odenmesi\s+gereken\s+bedel|teminat|kdv|"
    r"ada|parsel|blok|hisse|bagimsiz\s+bolum|no\.?\s*lu|nolu|kat)\b"
)


def _bounded_region(seg: str, fold: str, start: int) -> str:
    """``seg[start:]`` içinde, bir SONRAKİ alan/kimlik sınır etiketine kadar olan DEĞER bölgesi.

    ``fold`` uzunluk-korumalı ASCII-fold'dur (``seg`` ile hizalı) → sınır folded'da bulunur, dilim
    orijinal ``seg``'den alınır. Çıplak sayılar (parsel/sıra/ada) böylece değer bölgesine GİRMEZ.
    """
    rest_fold = fold[start:]
    m = _VALUE_BOUNDARY_RE.search(rest_fold)
    cut = m.start() if m else len(rest_fold)
    return seg[start:start + cut]


def _money_ok(region: str, m: "re.Match") -> bool:
    """``mahsuben`` parasal token'dan ÖNCE ise gerçek nakit değil (ALACAĞA MAHSUBEN) → reddet."""
    mahs = _ascii_lower(region).find("mahsuben")
    return not (0 <= mahs < m.start())


def _field_money_candidates(segments: list[str], label_variants: tuple) -> list[tuple]:
    """LABEL-BOUNDED parasal alan adayları: ``(value, matched_variant, strategy)``.

    YALNIZCA Türk parasal literali (gruplama '.'/ondalık ',') kabul edilir — çıplak tamsayı (ada/
    parsel/sıra/bölüm no) ADMİT EDİLMEZ (Fix 10 yapısal sınır; değer/eşik/max sezgisi YOK). Düzenler:
    aynı-segment (label sonrası bounded bölge) ya da bitişik-segment (``LABEL`` \\n ``VALUE``).
    """
    out: list[tuple] = []
    for i, seg in enumerate(segments):
        fold = _ascii_lower(seg)
        for lbl in label_variants:
            pos = fold.find(lbl)
            if pos < 0:
                continue
            end = pos + len(lbl)
            region = _bounded_region(seg, fold, end)
            m = MONEY_LITERAL_RE.search(region)
            if m and _money_ok(region, m):
                out.append((parse_tl_amount(m.group(0)), lbl, "same_segment"))
                continue
            # ``LABEL`` segment sonunda (yalnız sözcük-tamamlama harfi + ':'/'-' kalıntısı) → değer
            # BİR SONRAKİ segmentte (ör. "Muhammen Bedeli" \n "6.800.000,00 TL"; "bedel"→"bedeli").
            if re.fullmatch(r"[a-z]*[\s:\u2013-]*", fold[end:]) and i + 1 < len(segments):
                nxt = segments[i + 1]
                head = _bounded_region(nxt, _ascii_lower(nxt), 0)
                m2 = MONEY_LITERAL_RE.search(head)
                if m2 and _money_ok(head, m2):
                    out.append((parse_tl_amount(m2.group(0)), lbl, "adjacent_segment"))
    return out


def _tokens_in_order(text: str, tokens: tuple) -> bool:
    """``text`` içinde ``tokens`` SIRAYLA (araya başka içerik girebilir) geçiyor mu (tam kimlik için)."""
    pos = 0
    for t in tokens:
        j = text.find(t, pos)
        if j < 0:
            return False
        pos = j + len(t)
    return True


def _bounded_token_sequence(segments: list, tokens: tuple, max_span: int = 3):
    """Fix 11: ``tokens`` alan-etiketi dizisini EN ÇOK ``max_span`` ARDIŞIK segmentte SIRAYLA bulur.

    Bölünmüş serileştirmeyi (ör. 'Ödenmesi' / 'Gereken' / 'Bedel' ayrı segmentlerde) yakalar; TAM kimlik
    gerekir (eksik dizi eşleşmez; tüm-belge birleştirme YOK). Döner: en küçük ``(start, end)`` ya da None.
    """
    n = len(segments)
    for start in range(n):
        window = ""
        for end in range(start, min(n, start + max_span)):
            window = (window + " " + _ascii_lower(segments[end])).strip()
            if _tokens_in_order(window, tokens):
                return start, end
    return None


def _segment_kind(seg: str) -> str:
    """Gizlilik-güvenli segment TÜRÜ: bilinen alan/kimlik etiketi-TÜRÜ + 'money'/'other' (METİN/DEĞER İÇERİĞİ ASLA)."""
    fold = _ascii_lower(seg)
    parts: list[str] = []
    fm = _VALUE_BOUNDARY_RE.search(fold)
    if fm:
        parts.append(f"label:{fm.group(0).strip()}")
    if MONEY_LITERAL_RE.search(fold):      # segmentin HERHANGI yerinde para (etiket öncesi/sonrası dahil)
        parts.append("money")
    return "+".join(parts) if parts else "other"


def _segment_shape(segments: list, start: int, window: int = 12) -> list:
    """``start``'tan itibaren en çok ``window`` segmentin gizlilik-güvenli TÜR haritası (yerleşim tanısı; METİN YOK)."""
    lo = max(0, start)
    return [_segment_kind(segments[j]) for j in range(lo, min(len(segments), lo + window))]


def _ihale_bedeli_relation(segments: list, label: str = "ihale bedeli", max_following: int = 3):
    """Fix 11: AÇIK İhale Bedeli için LABEL→VALUE ilişkisi + gizlilik-güvenli komşuluk tanısı.

    same_segment → adjacent_segment (i+1) → bounded_following (i+2..i+max_following). Değer bölgesi bir
    SONRAKİ alan/kimlik etiketinde DURUR; yalnız Türk parasal literali kabul edilir; tüm-belge taraması /
    ilk-herhangi-sayı / max / eşik / known-truth YOK. Döner: ``(value, strategy, neighborhood)`` — neighborhood
    yalnız yapısal sayaç + normalize etiket-TÜRÜ (SEGMENT METNİ / DEĞER İÇERİĞİ ASLA).
    """
    nb = {
        "label_segment_found": False, "label_segment_index": None, "label_occurrence_count": 0,
        "same_segment_money_count": 0, "adjacent_segment_money_count": 0,
        "bounded_following_segments_inspected": 0, "bounded_following_money_count": 0,
        "boundary_stop_reason": None, "intervening_field_label_types": [], "relation_candidates": [],
        "segment_shape": [], "document_segment_count": len(segments),
        "label_segment_indexes": [], "money_segment_indexes": [],
    }
    # Fix 13: AÇIK ALAN etiketi tam-sözcük olmalı — çekimli prose ("ihale bedelini/bedelinin yatirmamasi")
    # etiket-bulundu SAYILMAZ (word-boundary). Auction numeratör semantiği değişmez (açık İhale Bedeli alanı).
    label_re = re.compile(r"\b" + re.escape(label) + r"\b")
    # Gizlilik-güvenli belge-geneli KONUM haritası (yalnız segment İNDEKSLERİ; DEĞER/METİN İÇERİĞİ ASLA):
    # etiket bloğu ile değer bloğunun ayrı (sütun/başlık) olduğu düzeni teşhis için — tahmin değil, GÖZLEM.
    nb["label_segment_indexes"] = [j for j, s in enumerate(segments) if label_re.search(_ascii_lower(s))][:20]
    nb["money_segment_indexes"] = [j for j, s in enumerate(segments)
                                   if MONEY_LITERAL_RE.search(_ascii_lower(s))][:24]
    for i, seg in enumerate(segments):
        fold = _ascii_lower(seg)
        lm = label_re.search(fold)
        if lm is None:
            continue
        nb["label_segment_found"] = True
        nb["label_occurrence_count"] += 1
        if nb["label_segment_index"] is None:
            nb["label_segment_index"] = i
            nb["segment_shape"] = _segment_shape(segments, i, window=12)
        end = lm.end()
        # 1. same segment (label sonrası bounded bölge)
        region = _bounded_region(seg, fold, end)
        same_hits = [m for m in MONEY_LITERAL_RE.finditer(region) if _money_ok(region, m)]
        nb["same_segment_money_count"] = len(same_hits)
        if same_hits:
            nb["relation_candidates"].append("same_segment")
            nb["boundary_stop_reason"] = "money_found"
            return parse_tl_amount(same_hits[0].group(0)), "same_segment", nb
        # 2..N sonraki segmentler (bounded; başka alan/kimlik etiketinde DUR)
        for j in range(i + 1, min(len(segments), i + 1 + max_following)):
            nxt_fold = _ascii_lower(segments[j])
            bm = _VALUE_BOUNDARY_RE.search(nxt_fold)
            if bm and bm.start() == 0:      # segment BAŞKA bir alan/kimlik etiketiyle başlıyor → DUR
                nb["boundary_stop_reason"] = f"field_or_identifier_label:{bm.group(0).strip()}"
                nb["intervening_field_label_types"].append(bm.group(0).strip())
                break
            nb["bounded_following_segments_inspected"] += 1
            head = _bounded_region(segments[j], nxt_fold, 0)
            mh = MONEY_LITERAL_RE.search(head)
            if mh and _money_ok(head, mh):
                strat = "adjacent_segment" if j == i + 1 else "bounded_following"
                nb["adjacent_segment_money_count" if j == i + 1 else "bounded_following_money_count"] += 1
                nb["relation_candidates"].append(strat)
                nb["boundary_stop_reason"] = "money_found"
                return parse_tl_amount(mh.group(0)), strat, nb
            if bm:                          # segment ORTASINDA sınır etiketi (değer öncesi) → DUR
                nb["boundary_stop_reason"] = f"field_or_identifier_label:{bm.group(0).strip()}"
                nb["intervening_field_label_types"].append(bm.group(0).strip())
                break
        if nb["boundary_stop_reason"] is None:
            nb["boundary_stop_reason"] = ("max_segments" if nb["bounded_following_segments_inspected"]
                                          else "no_following_segment")
        # Fix: İLK oluşum başarısızsa DURMA — sonraki İhale Bedeli oluşumunu dene (başlık+detay yerleşimi).
    return None, None, nb


def _settlement_relation(segments: list, max_following: int = 3) -> dict:
    """Fix 11: Ödenmesi Gereken Bedel alan-etiketi (bölünmüş dizi dahil) + bounded ALACAĞA MAHSUBEN değeri.

    TAM etiket kimliği (odenmesi→gereken→bedel) gerekir; genel 'bedel'/'gereken' YETMEZ. Değer yalnız
    etiketin bounded takip bölgesinden okunur (fiyat/alacaklı/sıfır/known-truth'tan ÇIKARILMAZ). Yalnız
    yapısal tanı döner (segment metni YOK).
    """
    out = {
        "settlement_label_token_sequence_found": False, "settlement_label_segment_span": 0,
        "settlement_bounded_following_segments_inspected": 0, "settlement_alacaga_token_found": False,
        "settlement_mahsuben_token_found": False, "settlement_value_sequence_found": False,
    }
    span = _bounded_token_sequence(segments, ("odenmesi", "gereken", "bedel"), max_span=3)
    if span is None:
        return out
    start, end = span
    out["settlement_label_token_sequence_found"] = True
    out["settlement_label_segment_span"] = end - start + 1
    stop = min(len(segments), end + 1 + max_following)
    out["settlement_bounded_following_segments_inspected"] = max(0, stop - (end + 1))
    region = " ".join(_ascii_lower(segments[j]) for j in range(start, stop))
    out["settlement_alacaga_token_found"] = "alacaga" in region
    out["settlement_mahsuben_token_found"] = "mahsuben" in region
    out["settlement_value_sequence_found"] = _tokens_in_order(region, ("alacaga", "mahsuben"))
    return out


def _amount_after(text: str, folded: str, label: str, window: int = 60) -> tuple[float | None, str | None]:
    """``label``'dan sonraki pencerede ilk Türk-sayı tutarını döndürür (deterministik)."""
    idx = folded.find(label)
    if idx < 0:
        return None, None
    seg = text[idx + len(label): idx + len(label) + window]
    return parse_tl_amount(seg), seg.strip()


def asset_descriptors(fold: str) -> dict:
    """Taşınmaz tanımlayıcılarını (ada/parsel/blok/bağımsız-bölüm/kat) GERÇEK UYAP yazımından
    çıkarır. İki sıra desteklenir: ``50984 Ada, 1 Parsel`` ve ``Ada 50984, Parsel 1``; ayrıca
    ``60 Nolu B.B.`` / ``60 No.lu Bağımsız Bölüm`` / ``12. Kat``. ``fold`` ASCII-fold+küçük harf
    beklenir. Bilinen pilot değerleri FALLBACK olarak KULLANILMAZ.
    """

    def _cap(pat: str) -> str | None:
        m = re.search(pat, fold)
        if not m:
            return None
        for g in m.groups():
            if g:
                return g
        return None

    return {
        "ada": _cap(r"(?:(\d{2,7})\s*ada\b|\bada[\s:.]+(\d{2,7}))"),
        "parsel": _cap(r"(?:(\d{1,6})\s*parsel\b|\bparsel[\s:.]+(\d{1,6}))"),
        "block": _cap(r"\b([a-z])\s*blok\b"),
        "section_no": _cap(r"(\d{1,5})\s*no\.?\s*lu\b"),
        "floor": _cap(r"(\d{1,3})\.\s*kat\b"),
    }


# --- Fix 13: NATIVE belge-türü korroborasyonu (deterministik SEMANTİK; DEĞERDEN/known-truth'tan DEĞİL) --- #
# Auction-result semantiği: sonuç/uzatma TUTANAĞI (+ açık "İhale Bedeli" tam-sözcük alanı).
_DOC_TYPE_AUCTION_RESULT = ("artirma sonuc tutanagi", "satis sonuc tutanagi", "uzatma tutanagi", "sonuc tutanagi")
# Sale-notice semantiği: elektronik satış ortamında AÇIK ARTIRMA İLANI / satış ilanı.
_DOC_TYPE_SALE_NOTICE = ("elektronik satis ortaminda", "acik artirma ilani", "satis ilani")
_DOC_TYPE_SALE_SPEC = ("satis sartnamesi", "sartname")
_DOC_TYPE_APPRAISAL = ("bilirkisi raporu", "kiymet takdir raporu", "kiymet takdiri raporu")


def classify_udf_document_type(source_text: str | None) -> str:
    """NATIVE UDF kaynak metninden DETERMİNİSTİK belge-türü (semantik başlık/alan; PARASAL DEĞERDEN DEĞİL).

    auction_result / sale_notice / sale_spec / appraisal_report / unknown. Sonuç-tutanağı semantiği en
    ayırt edici olduğundan ÖNCE denenir (satış ilanı 'açık artırma' içerse de 'sonuç/uzatma tutanağı'
    içermez). Yedek: açık ``İhale Bedeli`` TAM-sözcük alanı (çekimli prose 'ihale bedelini' DEĞİL).
    """
    fold = re.sub(r"\s+", " ", _ascii_lower(demojibake(source_text or "")))
    if any(t in fold for t in _DOC_TYPE_AUCTION_RESULT):
        return "auction_result"
    if any(t in fold for t in _DOC_TYPE_SALE_NOTICE):
        return "sale_notice"
    if any(t in fold for t in _DOC_TYPE_SALE_SPEC):
        return "sale_spec"
    if any(t in fold for t in _DOC_TYPE_APPRAISAL):
        return "appraisal_report"
    if re.search(r"\bihale bedeli\b", fold):   # açık resmî sonuç-alanı yapısı (tam-sözcük; prose DEĞİL)
        return "auction_result"
    return "unknown"


def corroborate_native_document_type(source_text: str | None, requested_artifact_type: str | None) -> dict:
    """Fix 13: NATIVE kaynak belge-türü İSTENEN artifact türüyle uyuşuyor mu (promosyon-öncesi guard).

    Yalnız ``detected == requested`` ise korrobore; farklı (ve unknown değil) ise MİSMATCH (Run-13:
    requested auction_result / detected sale_notice → mismatch). Tanı gizlilik-güvenli (metin YOK).
    """
    detected = classify_udf_document_type(source_text)
    corroborated = bool(detected == requested_artifact_type)
    mismatch = bool(detected != "unknown" and detected != requested_artifact_type)
    reason = ("document_type_match" if corroborated
              else (f"detected_{detected}_for_requested_{requested_artifact_type}" if mismatch
                    else "document_type_indeterminate"))
    return {
        "native_detected_document_type": detected,
        "native_requested_artifact_type": requested_artifact_type,
        "native_document_type_corroborated": corroborated,
        "native_document_type_mismatch": mismatch,
        "native_document_type_corroboration_reason": reason,
    }


def extract_evidence(
    artifacts: list[dict],
    institution: str | None = None,
    file_id: str | None = None,
) -> ExtractedEvidence:
    """Toplanan artifact'lardan ``ExtractedEvidence`` üretir (deterministik; admisyon DEĞİL)."""
    ev = ExtractedEvidence(institution=institution, file_id=file_id)
    ambiguities: list[str] = []

    # Artifact türüne göre metinler (collapsed) + segmentler (newline-koruyan) — mojibake onarılır
    per_type: dict[str, str] = {}
    per_type_segments: dict[str, list] = {}
    for a in artifacts:
        atype = a.get("artifact_type", "unknown")
        per_type[atype] = per_type.get(atype, "") + " " + _artifact_text(a)
        per_type_segments.setdefault(atype, []).extend(_artifact_segments(a))
    all_text = " ".join(per_type.values())
    all_fold = _ascii_lower(all_text)

    # --- Ekspertiz (Q) — LABEL-BOUNDED parasal alan (Fix 10): çıplak sayı (parsel/sıra/ada) ADMİT EDİLMEZ ---
    appraisal_cands: list[tuple] = []  # (value, variant, strategy, atype)
    for atype, segs in per_type_segments.items():
        for val, lbl, strat in _field_money_candidates(segs, APPRAISAL_LABELS):
            if val is not None:
                appraisal_cands.append((val, lbl, strat, atype))
    distinct_appraisal = sorted({round(v, 2) for (v, _, _, _) in appraisal_cands})
    ev.appraisal_candidates = distinct_appraisal
    ev.appraisal_field_label_found = bool(appraisal_cands) or any(
        lbl in _ascii_lower(" ".join(segs)) for segs in per_type_segments.values() for lbl in APPRAISAL_LABELS)
    ev.appraisal_candidate_count = len(distinct_appraisal)
    ev.appraisal_value_relation_strategies = sorted({s for (_, _, s, _) in appraisal_cands})
    if len(distinct_appraisal) == 1:
        ev.appraisal_value = distinct_appraisal[0]
        ev.appraisal_source = next((at for (_, _, _, at) in appraisal_cands), ARTIFACT_APPRAISAL_REPORT)
    elif len(distinct_appraisal) > 1:
        ambiguities.append(f"two possible appraisal values found: {distinct_appraisal}")
        ev.appraisal_value = None  # belirsiz → admisyon değil, insan incelemesi

    # --- İhale Bedeli (pay) — AÇIK resmî ihale fiyatı; LABEL-BOUNDED bounded-following (Fix 11) ---
    ihale_val: float | None = None
    ihale_src: str | None = None
    ihale_strat: str | None = None
    ihale_label_found = False
    ihale_nb: dict | None = None
    for atype in (ARTIFACT_AUCTION_RESULT, ARTIFACT_STATUS_CARD, ARTIFACT_SALE_NOTICE):
        segs = per_type_segments.get(atype)
        if not segs:
            continue
        val, strat, nb = _ihale_bedeli_relation(segs)
        if nb.get("label_segment_found"):
            ihale_label_found = True
            if ihale_nb is None:
                ihale_nb = nb
        if val is not None:
            ihale_val, ihale_strat, ihale_nb, ihale_src = val, strat, nb, atype
            break
    ev.ihale_bedeli = ihale_val
    ev.ihale_bedeli_source = ihale_src
    ev.auction_price_field_label_found = ihale_label_found
    ev.auction_price_candidate_count = 1 if ihale_val is not None else 0
    ev.auction_price_value_relation_strategy = ihale_strat

    # Sonuç kartı "Satış Tutarı" (İhale Bedeli ile mutabakat/corroboration) — auction price OLARAK KULLANILMAZ
    card_txt = per_type.get(ARTIFACT_STATUS_CARD) or all_text
    card_amt, _ = _amount_after(card_txt, _ascii_lower(card_txt), "satis tutari")
    ev.result_card_amount = card_amt
    if ihale_val is None and card_amt is not None:
        # açık İhale Bedeli yok ama sonuç kartı satış tutarı var → belirsiz (incelemeye)
        ambiguities.append("Odenmesi Gereken Bedel/status-card amount present but explicit Ihale Bedeli missing")

    # --- Terminal tamamlanmış-satış kanıtı ---
    ev.terminal_status_text = None
    for tok in TERMINAL_SALE_TOKENS:
        if tok in all_fold:
            ev.terminal_status_text = tok
            break
    non_terminal = next((tok for tok in NON_TERMINAL_STATUS_TOKENS if tok in all_fold), None)
    if non_terminal:
        ev.terminal_status_text = ev.terminal_status_text or non_terminal

    # --- Uzlaşı: Ödenmesi Gereken Bedel / ALACAĞA MAHSUBEN (bölünmüş-blok dahil) / Teminat / hisse / KDV ---
    og_val, og_seg = _amount_after(all_text, all_fold, "odenmesi gereken bedel", window=48)
    ev.odenmesi_gereken_bedel = og_val
    # Fix 11: bölünmüş serileştirme için TOKEN-DİZİSİ etiket eşleştirme (auction_result öncelikli).
    settle_nb: dict = _settlement_relation([])
    for atype in (ARTIFACT_AUCTION_RESULT, ARTIFACT_STATUS_CARD, ARTIFACT_SALE_NOTICE):
        segs = per_type_segments.get(atype)
        if not segs:
            continue
        r = _settlement_relation(segs)
        if r["settlement_label_token_sequence_found"]:
            settle_nb = r
            break
    ev.settlement_field_label_found = ("odenmesi gereken bedel" in all_fold) or settle_nb["settlement_label_token_sequence_found"]
    settlement_strategy: str | None = None
    if og_seg is not None and "mahsuben" in _ascii_lower(og_seg):
        ev.alacaga_mahsuben = True
        settlement_strategy = "odenmesi_gereken_bedel_segment"
    # Bölünmüş-blok "ALACAĞA \n MAHSUBEN" → collapsed all_fold tek boşlukla birleştirir (yakalanır).
    if "alacaga mahsuben" in all_fold:
        ev.alacaga_mahsuben = True
        settlement_strategy = settlement_strategy or "alacaga_mahsuben_phrase"
    # Fix 11: settlement etiketinin BOUNDED değer bölgesinde alacaga→mahsuben dizisi (segment-bölünmüş).
    if settle_nb["settlement_value_sequence_found"]:
        ev.alacaga_mahsuben = True
        settlement_strategy = settlement_strategy or "bounded_token_sequence"
    ev.alacaga_mahsuben_detected = ev.alacaga_mahsuben
    ev.settlement_value_relation_strategy = settlement_strategy
    dep_val, _ = _amount_after(all_text, all_fold, "teminat", window=40)
    ev.deposit_amount = dep_val
    ev.share_settlement = ("hisse orani" in all_fold) or ("satilan hisse" in all_fold) or ("hisse" in all_fold and "orani" in all_fold)
    # KDV: gerçek detay sayfası "KDV Oranı : %20" (etiket/değer AYRI düğümlerde; araya ':' ve
    # boşluk/nbsp girebilir). '%' ZORUNLU (yalın 'kdv ... 1' gibi başıboş rakam ORAN sayılmaz) —
    # gerçek '%1' (konut) / '%20' korunur, başıboş rakam reddedilir.
    m_kdv = re.search(r"kdv\s*orani?\s*:?\s*%\s*(\d{1,3})", all_fold) or re.search(r"kdv[^0-9%]{0,12}%\s*(\d{1,3})", all_fold)
    ev.kdv_rate = float(m_kdv.group(1)) if m_kdv else None
    ev.result_document_type = ARTIFACT_AUCTION_RESULT if ARTIFACT_AUCTION_RESULT in per_type else None
    m_dt = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", all_text)
    ev.completion_datetime = m_dt.group(1) if m_dt else None

    # --- Taşınmaz tanımlayıcıları (aynı-varlık mutabakatı) — gerçek UYAP yazımı, iki sıra ---
    desc = asset_descriptors(all_fold)
    ev.ada = desc["ada"]
    ev.parsel = desc["parsel"]
    ev.block = desc["block"]
    ev.section_no = desc["section_no"]
    ev.floor = desc["floor"] or ("zemin" if "zemin kat" in all_fold else None)
    for pt in ("mesken", "konut", "dukkan", "isyeri", "arsa", "bagimsiz bolum"):
        if pt in all_fold:
            ev.property_type = pt
            break
    ev.address_text = None  # kişisel adres taşınmaz; ham adres analitik kayda GEÇMEZ

    # Fix 11: gizlilik-güvenli ALAN-KOMŞULUĞU tanısı (yalnız yapısal sayaç + normalize etiket-TÜRÜ; METİN YOK).
    _inb = ihale_nb or {}
    ev.field_neighborhood = {
        "auction_price": {
            "label_segment_found": bool(_inb.get("label_segment_found", ihale_label_found)),
            "label_segment_index": _inb.get("label_segment_index"),
            "same_segment_money_count": _inb.get("same_segment_money_count", 0),
            "adjacent_segment_money_count": _inb.get("adjacent_segment_money_count", 0),
            "bounded_following_segments_inspected": _inb.get("bounded_following_segments_inspected", 0),
            "bounded_following_money_count": _inb.get("bounded_following_money_count", 0),
            "boundary_stop_reason": _inb.get("boundary_stop_reason"),
            "intervening_field_label_types": _inb.get("intervening_field_label_types", []),
            "relation_candidates": _inb.get("relation_candidates", []),
            "label_occurrence_count": _inb.get("label_occurrence_count", 0),
            "segment_shape": _inb.get("segment_shape", []),
            "document_segment_count": _inb.get("document_segment_count", 0),
            "label_segment_indexes": _inb.get("label_segment_indexes", []),
            "money_segment_indexes": _inb.get("money_segment_indexes", []),
        },
        "settlement": settle_nb,
    }

    ev.ambiguities = ambiguities
    ev.extraction_status = "ambiguous" if ambiguities else "deterministic"
    return ev
