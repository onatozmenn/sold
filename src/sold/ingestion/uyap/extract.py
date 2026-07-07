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
    NON_TERMINAL_STATUS_TOKENS,
    TERMINAL_SALE_TOKENS,
    ExtractedEvidence,
    _ascii_lower,
    parse_tl_amount,
)


def _artifact_text(artifact: dict) -> str:
    """Artifact'ın metnini döndürür: inline ``text`` ya da ``local_path`` (HTML → düz metin)."""
    text = artifact.get("text")
    if text is None and artifact.get("local_path"):
        p = Path(artifact["local_path"])
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
    text = text or ""
    if "<" in text and ">" in text:  # HTML → düz metin (bs4 varsa)
        try:
            from bs4 import BeautifulSoup

            text = BeautifulSoup(text, "html.parser").get_text(separator=" ")
        except Exception:  # bs4 yoksa kaba etiket temizliği
            text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _amount_after(text: str, folded: str, label: str, window: int = 60) -> tuple[float | None, str | None]:
    """``label``'dan sonraki pencerede ilk Türk-sayı tutarını döndürür (deterministik)."""
    idx = folded.find(label)
    if idx < 0:
        return None, None
    seg = text[idx + len(label): idx + len(label) + window]
    return parse_tl_amount(seg), seg.strip()


def _all_amounts_after(text: str, folded: str, label: str, window: int = 60) -> list[float]:
    """Metindeki TÜM ``label`` konumlarından tutarları toplar (çoklu/çelişkili değer tespiti)."""
    out: list[float] = []
    start = 0
    while True:
        idx = folded.find(label, start)
        if idx < 0:
            break
        val = parse_tl_amount(text[idx + len(label): idx + len(label) + window])
        if val is not None:
            out.append(val)
        start = idx + len(label)
    return out


def extract_evidence(
    artifacts: list[dict],
    institution: str | None = None,
    file_id: str | None = None,
) -> ExtractedEvidence:
    """Toplanan artifact'lardan ``ExtractedEvidence`` üretir (deterministik; admisyon DEĞİL)."""
    ev = ExtractedEvidence(institution=institution, file_id=file_id)
    ambiguities: list[str] = []

    # Artifact türüne göre metinler + tümü
    per_type: dict[str, str] = {}
    for a in artifacts:
        t = _artifact_text(a)
        per_type[a.get("artifact_type", "unknown")] = per_type.get(a.get("artifact_type", "unknown"), "") + " " + t
    all_text = " ".join(per_type.values())
    all_fold = _ascii_lower(all_text)

    # --- Ekspertiz (Q) — birden çok etiket; çelişki → belirsizlik ---
    appraisal_vals: list[float] = []
    appraisal_src: str | None = None
    for atype, txt in per_type.items():
        fold = _ascii_lower(txt)
        for lbl in APPRAISAL_LABELS:
            for v in _all_amounts_after(txt, fold, lbl):
                appraisal_vals.append(v)
                if appraisal_src is None:
                    appraisal_src = atype
    distinct_appraisal = sorted({round(v, 2) for v in appraisal_vals})
    ev.appraisal_candidates = distinct_appraisal
    if len(distinct_appraisal) == 1:
        ev.appraisal_value = distinct_appraisal[0]
        ev.appraisal_source = appraisal_src or ARTIFACT_APPRAISAL_REPORT
    elif len(distinct_appraisal) > 1:
        ambiguities.append(f"two possible appraisal values found: {distinct_appraisal}")
        ev.appraisal_value = None  # belirsiz → admisyon değil, insan incelemesi

    # --- İhale Bedeli (pay) — açık resmî ihale fiyatı; auction result / status card ---
    ihale_val: float | None = None
    ihale_src: str | None = None
    for atype in (ARTIFACT_AUCTION_RESULT, ARTIFACT_STATUS_CARD, ARTIFACT_SALE_NOTICE):
        txt = per_type.get(atype)
        if not txt:
            continue
        fold = _ascii_lower(txt)
        v, _seg = _amount_after(txt, fold, "ihale bedeli")
        if v is not None:
            ihale_val, ihale_src = v, atype
            break
    ev.ihale_bedeli = ihale_val
    ev.ihale_bedeli_source = ihale_src

    # Sonuç kartı "Satış Tutarı" (İhale Bedeli ile mutabakat/corroboration)
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

    # --- Uzlaşı desenleri: Ödenmesi Gereken Bedel / Teminat / hisse / ALACAĞA MAHSUBEN / KDV ---
    og_val, og_seg = _amount_after(all_text, all_fold, "odenmesi gereken bedel", window=48)
    ev.odenmesi_gereken_bedel = og_val
    if og_seg is not None and "mahsuben" in _ascii_lower(og_seg):
        ev.alacaga_mahsuben = True
    if "alacaga mahsuben" in all_fold:
        ev.alacaga_mahsuben = True
    dep_val, _ = _amount_after(all_text, all_fold, "teminat", window=40)
    ev.deposit_amount = dep_val
    ev.share_settlement = ("hisse orani" in all_fold) or ("satilan hisse" in all_fold) or ("hisse" in all_fold and "orani" in all_fold)
    m_kdv = re.search(r"kdv[^%\d]{0,8}%?\s*(\d{1,2})", all_fold)
    ev.kdv_rate = float(m_kdv.group(1)) if m_kdv else None
    ev.result_document_type = ARTIFACT_AUCTION_RESULT if ARTIFACT_AUCTION_RESULT in per_type else None
    m_dt = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", all_text)
    ev.completion_datetime = m_dt.group(1) if m_dt else None

    # --- Taşınmaz tanımlayıcıları (aynı-varlık mutabakatı) ---
    def _first(pat: str) -> str | None:
        m = re.search(pat, all_fold)
        return m.group(1) if m else None

    ev.ada = _first(r"(\d+)\s*ada")
    ev.parsel = _first(r"(\d+)\s*parsel")
    ev.block = _first(r"([a-z])\s*blok")
    ev.section_no = _first(r"(\d+)\s*no\.?\s*lu")
    ev.floor = _first(r"(\d+)\.\s*kat") or ("zemin" if "zemin kat" in all_fold else None)
    for pt in ("mesken", "konut", "dukkan", "isyeri", "arsa", "bagimsiz bolum"):
        if pt in all_fold:
            ev.property_type = pt
            break
    ev.address_text = None  # kişisel adres taşınmaz; ham adres analitik kayda GEÇMEZ

    ev.ambiguities = ambiguities
    ev.extraction_status = "ambiguous" if ambiguities else "deterministic"
    return ev
