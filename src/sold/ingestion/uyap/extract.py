"""Çıkarım (extraction) — kaynak artifact'lardan DETERMİNİSTİK alan çıkarımı (admisyon DEĞİL).

Yalnızca deterministik ayrıştırma + açık kanıt kuralları kullanılır. ML güven skoru, zayıf
denetim ya da sınıflandırıcı YOKTUR. Her ekonomik alan, çıkarıldığı artifact türüne
(provenans) izlenir. Çıkarım ADMİSYON DEĞİLdİr; yalnızca ``ExtractedEvidence`` üretir.

Etiket eşleştirme Türkçe-duyarsızdır (uzunluk-koruyan ASCII-fold → ofsetler orijinal metinle
hizalı kalır, tutarlar orijinal Türk sayı biçiminden okunur).
"""

from __future__ import annotations

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
            text = p.read_text(encoding="utf-8", errors="ignore")
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

    # --- İhale Bedeli (pay) — AÇIK resmî ihale fiyatı; LABEL-BOUNDED (Satış Tutarı/Ödenmesi Gereken DEĞİL) ---
    ihale_val: float | None = None
    ihale_src: str | None = None
    ihale_strat: str | None = None
    ihale_label_found = False
    for atype in (ARTIFACT_AUCTION_RESULT, ARTIFACT_STATUS_CARD, ARTIFACT_SALE_NOTICE):
        segs = per_type_segments.get(atype)
        if not segs:
            continue
        if any("ihale bedeli" in _ascii_lower(s) for s in segs):
            ihale_label_found = True
        cands = _field_money_candidates(segs, ("ihale bedeli",))
        if cands:
            ihale_val, _lbl, ihale_strat = cands[0]
            ihale_src = atype
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
    ev.settlement_field_label_found = "odenmesi gereken bedel" in all_fold
    settlement_strategy: str | None = None
    if og_seg is not None and "mahsuben" in _ascii_lower(og_seg):
        ev.alacaga_mahsuben = True
        settlement_strategy = "odenmesi_gereken_bedel_segment"
    # Bölünmüş-blok "ALACAĞA \n MAHSUBEN" → collapsed all_fold tek boşlukla birleştirir (yakalanır).
    if "alacaga mahsuben" in all_fold:
        ev.alacaga_mahsuben = True
        settlement_strategy = settlement_strategy or "alacaga_mahsuben_phrase"
    ev.alacaga_mahsuben_detected = ev.alacaga_mahsuben
    ev.settlement_value_relation_strategy = settlement_strategy
    dep_val, _ = _amount_after(all_text, all_fold, "teminat", window=40)
    ev.deposit_amount = dep_val
    ev.share_settlement = ("hisse orani" in all_fold) or ("satilan hisse" in all_fold) or ("hisse" in all_fold and "orani" in all_fold)
    # KDV: gerçek detay sayfası "KDV Oranı : %20" (etiket/değer AYRI düğümlerde; araya ':' ve
    # boşluk/nbsp girebilir). Önce 'oranı' desenli, sonra %-çıpalı yedek (nbsp \s ile toplanır).
    m_kdv = re.search(r"kdv\s*orani?\s*:?\s*%?\s*(\d{1,3})", all_fold) or re.search(r"kdv[^0-9%]{0,12}%\s*(\d{1,3})", all_fold)
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

    ev.ambiguities = ambiguities
    ev.extraction_status = "ambiguous" if ambiguities else "deterministic"
    return ev
