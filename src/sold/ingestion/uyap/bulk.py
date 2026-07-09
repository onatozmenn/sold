"""UYAP TOPLU (bulk) keşif + iterasyon katmanı — ÇALIŞAN tek-açık-artırma yolunun ETRAFINDA.

Bu katman YENİ bir kazıyıcı/parser DEĞİLdir. Canlı-kanıtlanmış tek-kayıt edinim yolunu
(``find_target_record_card`` → "İhale Evrak Listesi" modalı → satır → native UDF) ve mevcut
``extract → reconcile → audit`` boru hattını YENİDEN KULLANIR. Yeni bir izin-gevşek toplu
admisyon yolu KURMAZ (admisyon, açık insan adımı ``sold uyap admit`` OLARAK KALIR). Yapısal
ekonometrik çekirdeği DEĞİŞTİRMEZ.

Güvenlik duruşu: kullanıcı Chrome'a ELLE oturum açar; toplayıcı yalnızca CDP ile BAĞLANIR.
Hiçbir parola/MFA/CAPTCHA otomatikleştirilmez, hiçbir erişim kontrolü aşılmaz. Kaynağa karşı
davranış SERİ ve TUTUCUdur (paralel sel YOK). Tüm durum gitignored ``data/ingestion`` altındadır.

Kapsam: yalnızca ``Taşınmaz`` kategorisi; görünür UYAP durumu POZİTİF olarak ``Satıldı`` olan
açık artırmalar edinim hedefidir. Fiyat/İncele/eksik-metin ASLA "satıldı" çıkarımı için kullanılmaz.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import store
from .discovery import discover
from .models import (
    STATE_ADMITTED,
    STATE_AUDITED,
    STATE_COLLECTED,
    STATE_EXCLUDED,
    STATE_EXTRACTED,
    STATE_PENDING_REVIEW,
    _ascii_lower,
    demojibake,
)
from .pipeline import run_audit, run_extract

CATEGORY_TASINMAZ = "Taşınmaz"
MAX_WINDOW_DAYS = 7          # UYAP: "Tarih aralığı en fazla 1 hafta olabilir."
DEFAULT_PER_PAGE = 20
BULK_STATE_FILE = "bulk_state.json"

# Görünür UYAP kaynak-durum jetonları (POZİTİF eşleşme; ASCII-fold + demojibake sonrası).
SOLD_TOKEN = "satildi"
SALE_COMPLETED_TOKEN = "satis islemleri tamamlandi"
RESULT_NOT_ENTERED_TOKEN = "ihale sonucu girilmemistir"
SALE_DROPPED_TOKENS = ("malin satisinin dusmesi", "malin satisi dustu", "satisin dusmesi")

# Kartlarda görünen ham durum ifadeleri (kaynak metni AYNEN korunur).
_STATUS_PHRASES = (
    "Satıldı",
    "Satış İşlemleri Tamamlandı",
    "İhale Sonucu Girilmemiştir",
    "Malın Satışının Düşmesi",
    "Satışın Düşmesi",
)

# Oturum-kaybı / yeniden-yönlendirme kanıtı (giriş sayfası ≠ sıfır sonuç).
_LOGIN_TOKENS = (
    "e-devlet", "e-government", "giris yap", "giris yapiniz", "kimlik dogrulama",
    "oturum acma", "turkiye.gov.tr", "tc kimlik", "guvenli giris",
)
_RESULT_CONTEXT_TOKENS = (
    "sonuc bulundu", "gecmis ilanlar", "ihale", "tasinmaz", "evrak listesi", "kayit no",
)

_FILE_ID_RE = re.compile(r"\b\d{3,4}\s*/\s*\d+\b")
_KAYIT_NO_RE = re.compile(r"kayit\s*no\s*[:\-]?\s*(\d{3,})")
_INSTITUTION_RE = re.compile(
    r"([\wÇĞİÖŞÜçğıöşü\.]+(?:\s+[\wÇĞİÖŞÜçğıöşü\.]+){0,6}?\s+"
    r"(?:İcra Dairesi|Satış Memurluğu|İcra Müdürlüğü|Satış İcra Dairesi))",
    re.IGNORECASE,
)


def _fold(text: object) -> str:
    """Türkçe-duyarsız karşılaştırma için demojibake + ASCII-fold (yalnızca eşleştirme)."""
    return _ascii_lower(demojibake(str(text or "")))


def _digits(text: object) -> str:
    """Yalnızca rakamlar (tarih maske/biçim farklarını tolere eden karşılaştırma için)."""
    return re.sub(r"\D", "", str(text or ""))


# --------------------------------------------------------------------------- #
# 1) Tarih pencereleri (SAF, deterministik) — UYAP en fazla 1 haftalık arama.
# --------------------------------------------------------------------------- #
def _parse_iso(d: object) -> dt.date:
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d).strip())


def generate_date_windows(date_from: object, date_to: object, max_days: int = MAX_WINDOW_DAYS) -> list[dict]:
    """İstenen tarih aralığını deterministik, boşluksuz, örtüşmesiz ≤max_days'lik pencerelere böler.

    Pencereler KAPSAYICIdır (start..start+max_days-1). Ör. 2025-01-01..2025-01-21 →
    [01-01,01-07],[01-08,01-14],[01-15,01-21]. Son pencere kısa olabilir. Ay/yıl/artık-yıl
    geçişleri tarih aritmetiğiyle doğrudan çalışır.
    """
    start = _parse_iso(date_from)
    end = _parse_iso(date_to)
    if end < start:
        raise ValueError("date_to must be >= date_from")
    if max_days < 1:
        raise ValueError("max_days must be >= 1")
    span = dt.timedelta(days=max_days - 1)
    windows: list[dict] = []
    cur = start
    while cur <= end:
        w_end = min(cur + span, end)
        windows.append({"start": cur.isoformat(), "end": w_end.isoformat()})
        cur = w_end + dt.timedelta(days=1)
    return windows


def format_uyap_ui_date(iso_date: object) -> str:
    """Gözlenen UYAP arayüz tarih biçimi (``DD/MM/YYYY``, ör. ``10/06/2026``).

    Canlı katman ARA'dan ÖNCE girilen değerleri bu biçimle DOĞRULAR; biçim gerçek UI'dan
    gözlemlenmelidir (varsayılan burada gözlenen örnekle uyumludur).
    """
    return _parse_iso(iso_date).strftime("%d/%m/%Y")


# --------------------------------------------------------------------------- #
# 2) Sayfalama (SAF) — GÖZLENEN "0" kontrolü GEÇERLİ sayfa DEĞİLdir.
# --------------------------------------------------------------------------- #
def valid_result_pages(labels: list) -> list[int]:
    """Görünür sayfalama etiketlerinden GEÇERLİ numerik sayfaları döndürür (0 HARİÇ, sıralı).

    UYAP'ta gözlenen ``0`` kontrolü geçerli bir sonuç sayfası açmaz → asla seçilmez.
    ``Sonraki``/``Önceki``/``...`` gibi numerik-olmayan etiketler atlanır. ``["0","1","2","Sonraki"]``
    → ``[1,2]``. Sıfır-tabanlı genel bir tıklama döngüsü KURULMAZ.
    """
    pages: set[int] = set()
    for lb in labels or []:
        s = str(lb).strip()
        if not s.isdigit():
            continue
        n = int(s)
        if n <= 0:                     # UYAP page 0 geçerli değil
            continue
        pages.add(n)
    return sorted(pages)


# --------------------------------------------------------------------------- #
# 3) Kart durum sınıflandırma (SAF) — POZİTİF "Satıldı" tespiti.
# --------------------------------------------------------------------------- #
def is_sold_status(status_text: object) -> bool:
    """Görünür UYAP durumu POZİTİF olarak ``Satıldı`` mı? (fiyat/İncele/eksik-metin KULLANILMAZ)."""
    return SOLD_TOKEN in _fold(status_text)


def classify_card_status(status_text: object) -> dict:
    """Kart kaynak-durumunu sınıflandırır (ham metin AYNEN korunur; satıldı yalnız pozitif)."""
    raw = str(status_text or "").strip()
    fold = _fold(raw)
    sold = SOLD_TOKEN in fold
    if sold:
        category = "SOLD"
    elif RESULT_NOT_ENTERED_TOKEN in fold:
        category = "RESULT_NOT_ENTERED"
    elif any(t in fold for t in SALE_DROPPED_TOKENS):
        category = "SALE_DROPPED"
    else:
        category = "OTHER"
    return {"source_status_raw": raw, "normalized": fold, "sold": sold, "category": category}


# --------------------------------------------------------------------------- #
# 4) Sonuç metaverisi (SAF) — kaynak gezinme metaverisi olarak ayrıştırılır.
# --------------------------------------------------------------------------- #
def extract_result_metadata(text: object) -> dict:
    """Görünür ``N sonuç bulundu`` / ``Toplam M sayfa`` / ``P. sayfa`` / ``her sayfada K kayıt``.

    Yalnızca güvenilir biçimde bulunanlar döndürülür (yoksa ``None``). ``20`` sonsuza dek
    varsayılmaz — her sayfada gerçek metaveri okunur.
    """
    fold = _fold(text)
    out = {"result_count": None, "total_pages": None, "current_page": None, "per_page": None}
    m = re.search(r"(\d+)\s*sonuc\s+bulundu", fold)
    if m:
        out["result_count"] = int(m.group(1))
    m = re.search(r"toplam\s+(\d+)\s+sayfa", fold)
    if m:
        out["total_pages"] = int(m.group(1))
    m = re.search(r"(\d+)\s*\.?\s*sayfayi\s+gormektesiniz", fold)
    if m:
        out["current_page"] = int(m.group(1))
    m = re.search(r"her\s+sayfada\s+(\d+)\s+kayit", fold)
    if m:
        out["per_page"] = int(m.group(1))
    return out


def zero_results(text: object) -> bool:
    """Pozitif sıfır-sonuç tespiti (giriş sayfası / oturum kaybı ile KARIŞTIRILMAZ)."""
    fold = _fold(text)
    if "sonuc bulunamadi" in fold or "kayit bulunamadi" in fold:
        return True
    m = re.search(r"(\d+)\s*sonuc\s+bulundu", fold)
    return bool(m and int(m.group(1)) == 0)


# --------------------------------------------------------------------------- #
# 5) Oturum sona ermesi (SAF) — giriş sayfası ≠ sıfır sonuç.
# --------------------------------------------------------------------------- #
def detect_session_expiration(html: object, url: object = "") -> dict:
    """Oturum kaybı / beklenmeyen yönlendirmeyi POZİTİF giriş kanıtından tespit eder.

    Giriş sayfası ``sıfır sonuç`` OLARAK sınıflandırılmaz; sonuç-bağlamı olmadan giriş jetonları
    → ``expired``. Sonuç bağlamı da varsa belirsiz sayılır (durdurma yerine dürüst tanı).
    """
    fold = _fold(html)
    ufold = _fold(url)
    login = any(t in fold for t in _LOGIN_TOKENS) or "login" in ufold or "girisyap" in ufold.replace(" ", "")
    result_context = any(t in fold for t in _RESULT_CONTEXT_TOKENS)
    expired = bool(login and not result_context)
    reason = None
    if expired:
        reason = "login_or_authentication_page_detected"
    elif login and result_context:
        reason = "ambiguous_login_tokens_but_result_context_present"
    return {"expired": expired, "reason": reason, "login_evidence": bool(login), "result_context": result_context}


# --------------------------------------------------------------------------- #
# 6) Sonuç kartı ayrıştırma (SAF, kart-yerel) — kimlik + durum AYNI karttan.
# --------------------------------------------------------------------------- #
def _card_file_id(text: str) -> str | None:
    m = _FILE_ID_RE.search(text)
    if not m:
        return None
    return re.sub(r"\s*/\s*", "/", m.group(0)).strip()


def _card_status_raw(text: str) -> str | None:
    fold = _fold(text)
    hits = [p for p in _STATUS_PHRASES if _fold(p) in fold]
    return " · ".join(dict.fromkeys(hits)) if hits else None


def _card_institution(text: str) -> str | None:
    m = _INSTITUTION_RE.search(text)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def _card_kayit_no(text: str) -> str | None:
    m = _KAYIT_NO_RE.search(_fold(text))
    return m.group(1) if m else None


def _kayit_no_from_el(el) -> str | None:
    """KAYIT NO'yu kart elementinin class'ından alır (ör. ``incelenen-li 16760761856``) — metinde yoksa."""
    try:
        for c in (el.get("class") or []):
            if c.isdigit() and len(c) >= 6:
                return c
    except Exception:
        pass
    return None


def parse_result_cards(html: object) -> list[dict]:
    """Sonuç sayfasındaki açık-artırma kartlarını KART-YEREL olarak ayrıştırır.

    Her kart TEK distinkt dosya kimliği içermelidir (çok-kayıtlı container = tüm-liste, kart DEĞİL).
    Kimlik + görünür durum AYNI kart elementinden alınır (A'nın durumu B'nin butonuyla EŞLEŞTİRİLMEZ).
    ``kayit_no`` kaynak kanıtı olarak korunur. Kaydırma gerektirmez (statik DOM'dan). OFFLINE testable.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return []
    for sel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "[class*=incelenen]", "li", "tr"):
        candidates: list = []
        for el in soup.select(sel):
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            fids = {re.sub(r"\s+", "", n) for n in _FILE_ID_RE.findall(text)}
            if len(fids) != 1:              # 0 → kimlik yok; >1 → container
                continue
            candidates.append((el, text))
        if not candidates:
            continue
        cards: list[dict] = []
        seen: set = set()
        for el, text in candidates:
            fid = _card_file_id(text)
            if not fid:
                continue
            kayit = _card_kayit_no(text) or _kayit_no_from_el(el)
            dedup_key = kayit or fid        # KAYIT NO auction-özel; yoksa Esas No (dış kart önce)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            status_raw = _card_status_raw(text)
            cls = classify_card_status(status_raw or "")
            cards.append({
                "kayit_no": kayit,
                "file_id": fid,
                "institution_text": _card_institution(text),
                "source_status_raw": status_raw,
                "category": cls["category"],
                "sold": cls["sold"],
                "card_html": str(el),
                "card_text": re.sub(r"\s+", " ", text).strip(),
            })
        return cards                        # kart üreten İLK selector kazanır
    return []


def result_card_signature(html: object) -> tuple:
    """Sonuç sayfasındaki kartların kimlik imzası (KAYIT NO/Esas kümesi) — sayfa GEÇİŞİ doğrulaması.

    Sayfalama tıklaması sonrası kart kümesi GERÇEKTEN değişti mi anlamak için kullanılır (yalnızca
    'içerik değişti' yeterli değil: spinner/DOM oynaması yanıltabilir). OFFLINE test edilebilir.
    """
    return tuple(sorted((c.get("kayit_no") or c.get("file_id") or "") for c in parse_result_cards(html)))


def summarize_form_controls(html: object) -> dict:
    """'Geçmiş İlanlar' arama formunun GERÇEK kontrol yapısını KİŞİSEL-OLMAYAN özetler.

    input/select/button için yapısal öznitelikler (tag/type/id/name/class/placeholder/aria/readonly/
    maxlength/role) + değer VARLIĞI (içerik DEĞİL) döndürür. Canlı seçicileri gerçek DOM'a eşlemek
    için tanı amaçlıdır (tahmin yerine gözlem). OFFLINE test edilebilir.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return {"input_count": 0, "select_count": 0, "inputs": [], "selects": [], "buttons": [], "markers": {}}

    def _label_text(el) -> str | None:
        lid = el.get("id")
        if lid:
            lab = soup.find("label", attrs={"for": lid})
            if lab:
                return lab.get_text(" ", strip=True)[:48] or None
        par = el.find_parent("label")
        if par:
            return par.get_text(" ", strip=True)[:48] or None
        return None

    def _attrs(el) -> dict:
        return {
            "tag": el.name,
            "type": el.get("type"),
            "id": el.get("id"),
            "name": el.get("name"),
            "class": " ".join(el.get("class") or []) or None,
            "placeholder": el.get("placeholder"),
            "aria_label": el.get("aria-label"),
            "label": _label_text(el),
            "role": el.get("role"),
            "readonly": el.has_attr("readonly") or el.get("aria-readonly") == "true",
            "maxlength": el.get("maxlength"),
            "value_present": bool((el.get("value") or "").strip()),
        }

    inputs = [_attrs(e) for e in soup.find_all("input")]
    selects = []
    for e in soup.find_all("select"):
        d = _attrs(e)
        d["option_count"] = len(e.find_all("option"))
        selects.append(d)
    buttons: list[dict] = []
    for e in soup.find_all("button"):
        buttons.append({"tag": "button", "id": e.get("id"),
                        "class": " ".join(e.get("class") or []) or None,
                        "text": e.get_text(" ", strip=True)[:40]})
    for e in soup.find_all("input", {"type": ["submit", "button"]}):
        buttons.append({"tag": "input", "id": e.get("id"),
                        "class": " ".join(e.get("class") or []) or None,
                        "text": (e.get("value") or "")[:40]})

    # Aksiyon adayları (ara/sorgu/listele) — ARA kontrolü çoğu zaman <a class=btn> olabilir.
    action_re = re.compile(r"\b(ara|sorgula|sorgu|listele|arama)\b")
    action_candidates: list[dict] = []
    seen_act: set = set()
    for el in soup.find_all(["a", "button", "input"]):
        if el.name == "input" and (el.get("type") or "text") not in ("submit", "button"):
            continue
        txt = (el.get("value") if el.name == "input" else el.get_text(" ", strip=True)) or ""
        cls = " ".join(el.get("class") or [])
        idc = _fold(f"{el.get('id') or ''} {el.get('name') or ''} {cls}")
        btn_like = "btn" in cls.lower()
        if action_re.search(_fold(txt)) or "ara" in idc.split() or "sorgu" in idc or (btn_like and el.name == "a"):
            key = (el.name, el.get("id"), txt[:24])
            if key in seen_act:
                continue
            seen_act.add(key)
            action_candidates.append({
                "tag": el.name, "type": el.get("type"), "id": el.get("id"),
                "class": cls or None, "text": txt[:32], "onclick": el.has_attr("onclick"),
            })

    fold = _fold(html)
    return {
        "input_count": len(inputs),
        "select_count": len(selects),
        "inputs": inputs,
        "selects": selects,
        "buttons": buttons[:40],
        "action_candidates": action_candidates[:50],
        "markers": {
            "has_tasinmaz": "tasinmaz" in fold,
            "has_tasinir": "tasinir" in fold,
            "has_date_label": ("ihale bitis tarih" in fold or "tarih aralik" in fold),
            "has_il_label": bool(re.search(r"\bil\b", fold)),
            "has_ara_button": any((b.get("text") or "").strip().lower() == "ara" for b in buttons)
            or any((a.get("text") or "").strip().lower() == "ara" for a in action_candidates),
        },
    }


def summarize_result_structure(html: object) -> dict:
    """Sonuç sayfasındaki tekrarlı kart yapısını KİŞİSEL-OLMAYAN özetler (parse_result_cards ayarı için).

    Her aday selector için: tek-dosya-kimlikli eleman sayısı + durum-metinli eleman sayısı; ayrıca
    ilk kartın iskeleti (tag + class'lar; METİN DEĞİL). OFFLINE test edilebilir.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return {"parsed_card_count": 0, "result_count": None, "candidates": [], "first_card_skeleton": None}
    out: dict = {
        "parsed_card_count": len(parse_result_cards(html)),
        "result_count": extract_result_metadata(html).get("result_count"),
        "candidates": [],
        "first_card_skeleton": None,
    }
    best_el = None
    for sel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "[class*=incelenen]", "li", "tr", "div"):
        els = soup.select(sel)
        single = 0
        with_status = 0
        first = None
        for el in els:
            t = el.get_text(" ", strip=True)
            fids = {re.sub(r"\s+", "", n) for n in _FILE_ID_RE.findall(t)}
            if len(fids) == 1:
                single += 1
                if _card_status_raw(t):
                    with_status += 1
                if first is None:
                    first = el
        if single:
            out["candidates"].append({"selector": sel, "elements": len(els),
                                      "single_file_id": single, "with_status": with_status})
            if best_el is None:
                best_el = first

    def _skel(el, depth=0):
        if depth > 2 or getattr(el, "name", None) is None:
            return None
        kids = [c for c in el.find_all(recursive=False) if getattr(c, "name", None)]
        return {
            "tag": el.name,
            "class": " ".join(el.get("class") or []) or None,
            "children": [s for c in kids[:10] if (s := _skel(c, depth + 1))],
        }

    if best_el is not None:
        out["first_card_skeleton"] = _skel(best_el)

    # Sayfalama kontrolleri (numara / Sonraki / Önceki) — gerçek page-nav DOM'unu görmek için.
    pagination: list[dict] = []
    seen_pg: set = set()
    for el in soup.find_all(["a", "button", "li"]):
        t = el.get_text(" ", strip=True)
        if not t or len(t) > 16:
            continue
        ft = _fold(t)
        if re.fullmatch(r"\d+", t) or ft in ("sonraki", "onceki", "ileri", "geri", "next", "prev", "previous", "...", "\u00bb", "\u00ab"):
            key = (el.name, el.get("id"), t, " ".join(el.get("class") or []))
            if key in seen_pg:
                continue
            seen_pg.add(key)
            pagination.append({
                "tag": el.name, "id": el.get("id"),
                "class": " ".join(el.get("class") or []) or None,
                "text": t, "onclick": el.has_attr("onclick"), "href": el.get("href") or None,
            })
    out["pagination"] = pagination[:40]

    # Sayım/sayfa banner (kişisel-olmayan: sonuç/toplam/sayfa/kayıt içeren KISA metin; KAYIT NO hariç).
    banners: list[str] = []
    for el in soup.find_all(["div", "span", "p", "small", "label", "strong", "b"]):
        t = el.get_text(" ", strip=True)
        if not t or len(t) > 80 or not re.search(r"\d", t):
            continue
        ft = _fold(t)
        if "kayit no" in ft:
            continue
        if re.search(r"(sonuc bulundu|toplam|her sayfada|sayfa)", ft):
            banners.append(t[:80])
    out["count_banners"] = list(dict.fromkeys(banners))[:12]
    return out


def summarize_document_area(html: object) -> list[dict]:
    """Post-click document-list containers (modal/dialog/panel/evrak) - privacy-safe summary.

    Reports tag/class/id + document-type token presence + a visibility hint for elements whose
    class contains modal/dialog/panel/popup/evrak or role=dialog, to see whether clicking the
    'evrak listesi' control actually opens a list and in what structure. No personal text returned.
    OFFLINE testable.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return []
    out: list[dict] = []
    seen: set = set()
    _doc_tokens = ("evrak listesi", "satis ilani", "bilirkisi", "sartname",
                   "artirma sonuc", "tutanagi", "muhammen", "kiymeti", "uzatma")
    for sel in ("[class*=modal i]", "[class*=dialog i]", "[class*=panel i]",
                "[class*=popup i]", "[role=dialog]", "[class*=evrak i]"):
        for el in soup.select(sel):
            key = (el.name, " ".join(el.get("class") or []), el.get("id"))
            if key in seen:
                continue
            seen.add(key)
            ft = _fold(el.get_text(" ", strip=True))
            style = (el.get("style") or "").lower()
            classes = el.get("class") or []
            out.append({
                "tag": el.name,
                "id": el.get("id"),
                "class": " ".join(classes) or None,
                "visible_hint": ("display:none" not in style and "hidden" not in classes and "hide" not in classes),
                "doc_tokens": [t for t in _doc_tokens if t in ft],
                "text_len": len(ft),
            })
    return out[:30]


def document_modal_skeleton(html: object, modal_id: str = "ihale_evraklari_modal", max_depth: int = 8) -> dict | None:
    """Inner element tree of the document-list modal (default id 'ihale_evraklari_modal').

    Reveals the real row structure + download/eye control tags/class/href/onclick so the row
    extraction can be bound to it. Document names (Satis Ilani, Bilirkisi...) are non-personal,
    so short own-text is included; no personal data. OFFLINE testable.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return None
    modal = soup.find(id=modal_id)
    if modal is None:
        for m in soup.select("[class*=modal i]"):
            if "evrak" in _fold(m.get_text(" ", strip=True)) and len(m.get_text(" ", strip=True)) > 60:
                modal = m
                break
    if modal is None:
        return None

    def _node(el, depth=0):
        if depth > max_depth or getattr(el, "name", None) is None:
            return None
        node: dict = {"tag": el.name}
        if el.get("id"):
            node["id"] = el.get("id")
        cls = " ".join(el.get("class") or [])
        if cls:
            node["class"] = cls[:60]
        if el.name in ("a", "button"):
            node["href"] = (el.get("href") or "")[:48] or None
            node["onclick"] = bool(el.has_attr("onclick"))
        own = el.find(string=True, recursive=False)
        txt = str(own).strip()[:40] if own and str(own).strip() else None
        if txt:
            node["text"] = txt
        kids = [c for c in el.find_all(recursive=False) if getattr(c, "name", None)]
        children = [n for c in kids[:14] if (n := _node(c, depth + 1))]
        if children:
            node["children"] = children
        return node

    return _node(modal)


# --------------------------------------------------------------------------- #
# 7) Yeniden başlama / tekilleştirme (SAF).
# --------------------------------------------------------------------------- #
_COMPLETE_STATES = {STATE_AUDITED, STATE_PENDING_REVIEW, STATE_ADMITTED, STATE_EXCLUDED}
_INCOMPLETE_STATES = {STATE_COLLECTED, STATE_EXTRACTED}


def acquisition_state(candidate: dict | None) -> str:
    """Bir adayın edinim durumunu döndürür: ``new`` | ``acquire`` | ``resume`` | ``complete``.

    Denetim tamamlanmışsa (audited/pending_review/admitted/excluded) ``complete`` — varsayılan
    olarak yeniden edinilmez. Denetimle terminal-reddedilmiş (excluded) bir kayıt, BAŞARISIZ bir
    çekme ile KARIŞTIRILMAZ. Kısmi (collected/extracted) → ``resume``.
    """
    if candidate is None:
        return "new"
    st = candidate.get("state")
    if st in _COMPLETE_STATES:
        return "complete"
    if st in _INCOMPLETE_STATES:
        return "resume"
    return "acquire"


def should_acquire(candidate: dict | None) -> bool:
    """Edinim yapılmalı mı? (tamamlanmış olanlar varsayılan olarak atlanır)."""
    return acquisition_state(candidate) != "complete"


# --------------------------------------------------------------------------- #
# 8) Toplu durum kontrol noktası (kalıcı) — pencere/sayfa ilerlemesi.
# --------------------------------------------------------------------------- #
def bulk_state_path(store_dir: Path | str | None = None) -> Path:
    return Path(store_dir or store.DEFAULT_STORE_DIR) / BULK_STATE_FILE


def load_bulk_state(store_dir: Path | str | None = None) -> dict:
    p = bulk_state_path(store_dir)
    if not p.exists():
        return {"windows": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"windows": []}
    return data if isinstance(data, dict) else {"windows": []}


def save_bulk_state(state: dict, store_dir: Path | str | None = None) -> Path:
    p = bulk_state_path(store_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def window_key(province: object, start: object, end: object) -> str:
    return f"{_ascii_lower(province).strip()}|{start}|{end}"


def new_window_record(province: str, start: str, end: str) -> dict:
    return {
        "key": window_key(province, start, end),
        "province": province,
        "window_start": start,
        "window_end": end,
        "result_count": None,
        "total_pages": None,
        "pages_completed": [],
        "result_cards_inspected": 0,
        "sold_discovered": 0,
        "sold_skipped_known": 0,
        "acquisitions_complete": 0,
        "acquisitions_failed": 0,
        "status": "IN_PROGRESS",
    }


def get_window_record(state: dict, province: object, start: object, end: object) -> dict | None:
    key = window_key(province, start, end)
    for w in state.get("windows", []):
        if w.get("key") == key:
            return w
    return None


def upsert_window_record(state: dict, rec: dict) -> dict:
    windows = state.setdefault("windows", [])
    for i, w in enumerate(windows):
        if w.get("key") == rec.get("key"):
            windows[i] = rec
            return rec
    windows.append(rec)
    return rec


def mark_page_complete(rec: dict, page: int) -> dict:
    done = rec.setdefault("pages_completed", [])
    if page not in done:
        done.append(page)
        done.sort()
    return rec


def pages_remaining(rec: dict, valid_pages: list[int]) -> list[int]:
    done = set(rec.get("pages_completed") or [])
    return [p for p in valid_pages if p not in done]


# --------------------------------------------------------------------------- #
# 9) Tek satılan-açık-artırma işleme (ORKESTRASYON) — çalışan yolu YENİDEN KULLANIR.
# --------------------------------------------------------------------------- #
def process_sold_auction(
    card: dict,
    *,
    acquire_documents,
    store_dir: Path | str | None = None,
    genuine_path: Path | str | None = None,
    discovery_only: bool = False,
    force: bool = False,
    source_page_ref: str | None = None,
    province_label: str | None = None,
    window: dict | None = None,
) -> dict:
    """Keşif (KALICI, edinimden ÖNCE) → çalışan edinim yolunu yeniden kullan → mevcut boru hattı.

    ``acquire_documents(file_id, institution) -> (artifacts, patterns, diag)`` ENJEKTE edilir;
    canlı koşu gerçek ``BrowserCollector._collect_documents`` sarmalayıcısını verir, testler
    fixture döndüren sahte bir edinici verir (canlı tarayıcı GEREKMEZ). ASLA admit çağırmaz
    (admisyon açık insan adımı olarak KALIR). Yapısal çekirdek çağrılmaz/değiştirilmez.
    """
    file_id = card.get("file_id")
    institution = card.get("institution_text") or province_label or "UYAP e-Satış"
    kayit_no = card.get("kayit_no")
    status_raw = card.get("source_status_raw")

    outcome = {
        "candidate_id": None,
        "file_id": file_id,
        "kayit_no": kayit_no,
        "discovered": False,
        "acquired": False,
        "skipped": False,
        "audit_decision": None,
        "outcome": "not_processed",
    }
    if not file_id:
        outcome["outcome"] = "no_stable_file_identity"
        return outcome

    # 1) KALICI keşif — belge ediniminden ÖNCE (kesinti olsa bile kayıt bilinir kalır).
    #    record_ref=KAYIT NO → aynı Esas'ın farklı açık artırmaları AYRI aday (birleşmez/kaybolmaz).
    cand = discover(
        institution, file_id, listing_ref=kayit_no, status_text=status_raw,
        source_page_ref=source_page_ref, store_dir=store_dir, record_ref=kayit_no,
    )
    bulk_meta = cand.setdefault("bulk", {})
    bulk_meta.update({
        "kayit_no": kayit_no,
        "province_label": province_label,
        "window_start": (window or {}).get("start"),
        "window_end": (window or {}).get("end"),
        "source_status_raw": status_raw,
    })
    cand = store.upsert(cand, store_dir)
    outcome.update({"candidate_id": cand["candidate_id"], "discovered": True, "outcome": "discovered"})

    if discovery_only:
        return outcome

    # 2) Yeniden başlama / tekilleştirme: zaten edinilmiş olanı varsayılan olarak atla.
    #    --force: bilinen (denetlenmiş) adayı da YENİDEN edin (güncel toplama koduyla tekrar dene).
    #    ANCAK açıkça ADMİSYON yapılmış (admitted_public_record_id) adaya force ile bile DOKUNMA.
    existing = store.get_candidate(cand["candidate_id"], store_dir) or cand
    already_admitted = bool(existing.get("admitted_public_record_id"))
    if already_admitted or (not force and not should_acquire(existing)):
        outcome.update({
            "skipped": True,
            "outcome": "skipped_already_acquired",
            "audit_decision": (existing.get("audit") or {}).get("decision"),
        })
        return outcome

    # 3) ÇALIŞAN belge-edinim yolunu yeniden kullan (yeni bir parser DEĞİL).
    try:
        artifacts, patterns, diag = acquire_documents(file_id, institution)
    except Exception as exc:  # edinim hatası kaydı; sonraki açık artırmanın kimliğini BOZMAZ
        existing.setdefault("bulk", {})["last_acquisition_error"] = str(exc)[:160]
        store.log_event(existing, "bulk_acquisition_failed", str(exc)[:160])
        store.upsert(existing, store_dir)
        outcome.update({"outcome": "acquisition_failed", "error": str(exc)[:160]})
        return outcome

    # status_card (terminal-durum kanıtı) + toplanan belgeler.
    status_card = {
        "artifact_type": "status_card",
        "text": card.get("card_text") or status_raw or "",
        "source_ref": source_page_ref,
    }
    existing["artifacts"] = [status_card] + list(artifacts or [])
    existing["state"] = STATE_COLLECTED
    bm = existing.setdefault("bulk", {})
    bm["document_access_patterns"] = patterns or []
    bm["collection_diagnostics"] = diag or {}
    store.log_event(existing, "bulk_collected", f"docs={len(artifacts or [])}")
    store.upsert(existing, store_dir)

    # 4) MEVCUT boru hattı: çıkarım → mutabakat + denetim. ADMİSYON YOK.
    existing = run_extract(existing, store_dir)
    existing = run_audit(existing, store_dir, genuine_path)
    decision = (existing.get("audit") or {}).get("decision")
    outcome.update({"acquired": True, "audit_decision": decision, "outcome": "acquired"})
    return outcome


def summarize_candidates(store_dir: Path | str | None = None) -> dict:
    """Toplu koşu için aday deposundan denetim-kararı dağılımını özetler (admisyon sayısı dahil)."""
    by_decision: dict[str, int] = {}
    admitted = 0
    for c in store.load_candidates(store_dir):
        dec = (c.get("audit") or {}).get("decision")
        if dec:
            by_decision[dec] = by_decision.get(dec, 0) + 1
        if c.get("admitted_public_record_id"):
            admitted += 1
    return {"by_audit_decision": by_decision, "admitted": admitted}


# --------------------------------------------------------------------------- #
# 10) Canlı orkestratör (KULLANICI-KONTROLLÜ oturuma CDP ile bağlanır).
# --------------------------------------------------------------------------- #
class UyapBulkCollector:
    """UYAP "Geçmiş İlanlar" için SERİ, kontrol-noktalı toplu orkestratör.

    Kimlik doğrulama OTOMATİKLEŞTİRİLMEZ: kullanıcı Chrome'u ``--remote-debugging-port`` ile
    başlatıp ELLE oturum açar ve ``e-Satış → İhaleler → Geçmiş İlanlar`` sayfasına gelir; bu sınıf
    yalnızca CDP ile BAĞLANIR. Kaynağa karşı davranış seri/tutucudur. Her satılan açık artırma,
    çalışan tek-kayıt edinim yoluna (``BrowserCollector._collect_documents``) ve mevcut
    ``extract/audit`` boru hattına beslenir; ADMİSYON YAPILMAZ.
    """

    def __init__(
        self,
        cdp_endpoint: str,
        store_dir: Path | str | None = None,
        genuine_path: Path | str | None = None,
        request_delay_ms: int = 900,
        result_timeout_ms: int = 20000,
        stall_seconds: int = 120,
    ) -> None:
        self.cdp_endpoint = cdp_endpoint
        self.store_dir = store_dir
        self.genuine_path = genuine_path
        self.request_delay_ms = max(0, int(request_delay_ms))
        self.result_timeout_ms = max(1000, int(result_timeout_ms))
        # Canlı adım gözcüsü (watchdog): hiçbir adım stall_seconds'ı aşarsa net tanı ile GÜVENLE sonlandır
        # (önceki koşumdan kalan TAKILI görüntüleyici sekmesi page.content()'i sonsuza dek bloklayabilir).
        self.stall_seconds = max(15, int(stall_seconds))
        self._hb_step = "başlatılıyor"
        self._hb_ts = time.monotonic()
        self._tab_count = -1
        self._live_active = False

    def diagnose_form(self) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        """READ-ONLY tanı: oturuma bağlanıp 'Geçmiş İlanlar' formunun GERÇEK kontrol yapısını döndürür.

        ARA'ya TIKLAMAZ, ARAMA/İNDİRME yapmaz, kişisel metin/alan değeri okumaz. Canlı seçicileri
        (kategori/İl/tarih/ARA) gerçek DOM'a eşlemek için kullanılır — tahmin yerine gözlem.
        """
        from .collect import BrowserCollector

        sync_playwright = BrowserCollector._sync_playwright()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            page = self._find_gecmis_page(browser.contexts[0])
            if page is None:
                raise RuntimeError(
                    "no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sekmesi bulunamadı. "
                    "Önce UYAP e-Satış → İhaleler → Geçmiş İlanlar sayfasını elle açın."
                )
            html = page.content()
            summary = summarize_form_controls(html)
            summary["page"] = {"url_path": self._safe_ref(page.url), "title": (page.title() or "")[:80]}
            summary["session"] = detect_session_expiration(html, page.url)
            return summary

    def diagnose_results(self, province, date_from, date_to):  # pragma: no cover - canlı tarayıcı gerektirir
        """READ-ONLY sonuç-yapısı tanısı: gerçek aramayı ÇALIŞTIRIR (indirme/mutasyon YOK) ve ilk
        pencerenin sonuç DOM iskeletini döndürür (parse_result_cards'ı gerçek sonuç kartına göre
        ayarlamak için). Kart metni/kişisel veri DÖNMEZ; yalnız tag/class iskeleti + sayımlar.
        """
        from .collect import BrowserCollector

        w = generate_date_windows(date_from, date_to)[0]
        start_ui, end_ui = format_uyap_ui_date(w["start"]), format_uyap_ui_date(w["end"])
        sync_playwright = BrowserCollector._sync_playwright()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            page = self._find_gecmis_page(browser.contexts[0])
            if page is None:
                raise RuntimeError("no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sayfasını elle açın.")
            self._dismiss_notices(page)
            cat = self._select_category_tasinmaz(page)
            prov = self._select_province(page, province)
            dates = self._set_and_verify_dates(page, start_ui, end_ui)
            ara = self._click_ara(page) if dates else False
            result_html = self._wait_result_state(page) if ara else page.content()
            summary = summarize_result_structure(result_html)
            summary["steps"] = {
                "window": w, "category_selected": cat, "province_selected": prov,
                "dates_verified": dates, "ara_clicked": ara,
                "session": detect_session_expiration(result_html, page.url),
            }
            return summary

    def diagnose_documents(self, province, date_from, date_to,
                           target_file_id=None, target_kayit_no=None):  # pragma: no cover - canlı tarayıcı
        """Hedef kartın 'İhale Evrak Listesi' kontrolünü çalıştırıp belge-listesinin NE yaptığını raporlar.

        Gerçek ``_collect_documents``'ı bir kart için çalıştırır (aynı edinim yolu) ve tıklama-öncesi/
        sonrası sekme sayısı + URL, toplanan belge sayısı, toplama tanıları ve tıklama-sonrası olası
        modal/panel iskeletini döndürür — 'evrak listesi' tıklanınca liste modal mı / yeni sekme mi /
        yönlendirme mi açıyor görmek için. ADMİSYON YOK; genuine'e dokunmaz.
        """
        from .collect import BrowserCollector

        w = generate_date_windows(date_from, date_to)[0]
        start_ui, end_ui = format_uyap_ui_date(w["start"]), format_uyap_ui_date(w["end"])
        sync_playwright = BrowserCollector._sync_playwright()
        collector = BrowserCollector(cdp_endpoint=self.cdp_endpoint)
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            context = browser.contexts[0]
            page = self._find_gecmis_page(context)
            if page is None:
                raise RuntimeError("no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sayfasını elle açın.")
            self._dismiss_notices(page)
            self._select_category_tasinmaz(page)
            self._select_province(page, province)
            dates = self._set_and_verify_dates(page, start_ui, end_ui)
            ara = self._click_ara(page) if dates else False
            self._wait_result_state(page)
            cards = parse_result_cards(page.content())
            target = None
            for c in cards:
                if target_kayit_no and c.get("kayit_no") == target_kayit_no:
                    target = c
                    break
                if target_file_id and str(c.get("file_id")) == str(target_file_id):
                    target = c
                    break
            if target is None:
                target = next((c for c in cards if c.get("sold")), None)
            fid = target.get("file_id") if target else None
            pre_tabs = [self._safe_ref(p.url) for p in context.pages]
            docs, patterns, diag = collector._collect_documents(page, context, fid, province, native_only=True)
            post_tabs = [self._safe_ref(p.url) for p in context.pages]
            keys = ("page_state", "document_entry_path", "target_record_card_found",
                    "document_list_control_found", "document_list_control_kind", "document_list_opened",
                    "document_list_container_kind", "pre_click_visible_document_types",
                    "post_click_visible_document_types", "document_container_strategy",
                    "recognized_document_rows", "document_collection_attempts", "viewer_pages_opened")
            return {
                "steps": {"dates_verified": dates, "ara_clicked": ara, "window": w},
                "target": {"file_id": fid, "kayit_no": target and target.get("kayit_no")},
                "pre_tab_count": len(pre_tabs),
                "post_tab_count": len(post_tabs),
                "new_tab_urls": post_tabs[len(pre_tabs):] if len(post_tabs) > len(pre_tabs) else [],
                "documents_collected": len(docs),
                "diag": {k: diag.get(k) for k in keys},
                "post_click_area": summarize_document_area(page.content()),
                "document_modal_skeleton": document_modal_skeleton(page.content()),
            }

    # -- Canlı koşu (pragma: canlı tarayıcı/DOM gerektirir; ağ testlerinde çalışmaz) ------- #
    def run(
        self,
        province: str,
        date_from: str,
        date_to: str,
        max_records: int | None = None,
        max_windows: int | None = None,
        dry_run: bool = False,
        discovery_only: bool = False,
        resume: bool = False,
        force: bool = False,
        kayit_no: str | None = None,
    ) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        from .collect import BrowserCollector

        windows = generate_date_windows(date_from, date_to)
        if max_windows:
            windows = windows[:max_windows]
        summary = {
            "category": CATEGORY_TASINMAZ,
            "province": province,
            "date_from": date_from,
            "date_to": date_to,
            "windows_total": len(windows),
            "windows_processed": 0,
            "result_cards_inspected": 0,
            "sold_discovered": 0,
            "sold_skipped_known": 0,
            "acquisitions_completed": 0,
            "acquisition_failures": 0,
            "records_processed": 0,
            "audit_decisions": {},
            "session_interruptions": 0,
            "stopped_reason": None,
            "dry_run": bool(dry_run),
            "discovery_only": bool(discovery_only),
        }
        if dry_run:
            summary["planned_windows"] = windows
            summary["stopped_reason"] = "dry_run"
            self._print(f"[UYAP BULK] DRY-RUN · {province} · {date_from}→{date_to} · {len(windows)} pencere")
            for w in windows:
                self._print(f"  pencere {w['start']} → {w['end']} "
                            f"({format_uyap_ui_date(w['start'])} - {format_uyap_ui_date(w['end'])})")
            return summary

        sync_playwright = BrowserCollector._sync_playwright()
        collector = BrowserCollector(cdp_endpoint=self.cdp_endpoint)
        acquired_total = 0
        with sync_playwright() as pw:
            self._live_active = True
            watchdog_stop = self._start_watchdog()
            self._beat("CDP oturumuna bağlanılıyor")
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                self._live_active = False
                watchdog_stop.set()
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            context = browser.contexts[0]
            try:
                self._tab_count = len(context.pages)
            except Exception:
                self._tab_count = -1
            self._beat(f"'Geçmiş İlanlar' sekmesi aranıyor ({self._tab_count} açık sekme)")
            page = self._find_gecmis_page(context)
            if page is None:
                self._live_active = False
                watchdog_stop.set()
                raise RuntimeError(
                    "no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sekmesi bulunamadı. "
                    "Önce UYAP e-Satış → İhaleler → Geçmiş İlanlar sayfasını elle açın."
                )

            def _acquire(file_id, institution):
                return collector._collect_documents(page, context, file_id, institution, native_only=True)

            state = load_bulk_state(self.store_dir)
            for w in windows:
                rec = get_window_record(state, province, w["start"], w["end"])
                if rec and rec.get("status") == "COMPLETE" and not force:
                    self._print(f"[UYAP BULK] pencere atlandı (tamamlanmış; yeniden çalıştırmak için --force): {w['start']}→{w['end']}")
                    continue
                if rec is None or force:
                    rec = new_window_record(province, w["start"], w["end"])  # force → sıfırdan (pages_completed sıfırlanır)
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)

                stop = self._run_window(page, context, _acquire, province, w, rec, state,
                                        summary, max_records, discovery_only, acquired_total, kayit_no,
                                        force=force)
                acquired_total = summary["acquisitions_completed"] + summary["sold_skipped_known"]
                summary["windows_processed"] += 1
                if stop == "SESSION_EXPIRED":
                    summary["stopped_reason"] = "SESSION_EXPIRED"
                    summary["session_interruptions"] += 1
                    break
                if stop == "MAX_RECORDS":
                    summary["stopped_reason"] = "max_records"
                    break
            self._live_active = False
            watchdog_stop.set()

        for c in store.load_candidates(self.store_dir):
            dec = (c.get("audit") or {}).get("decision")
            if dec:
                summary["audit_decisions"][dec] = summary["audit_decisions"].get(dec, 0) + 1
        self._print_summary(summary)
        return summary

    def _run_window(self, page, context, acquire, province, w, rec, state, summary,
                    max_records, discovery_only, acquired_total, target_kayit_no=None,
                    force: bool = False) -> str | None:  # pragma: no cover
        start_ui = format_uyap_ui_date(w["start"])
        end_ui = format_uyap_ui_date(w["end"])
        self._print(f"[UYAP BULK] pencere {w['start']}→{w['end']} · {CATEGORY_TASINMAZ} · {province}")
        self._beat(f"pencere {w['start']}→{w['end']}: form dolduruluyor (kategori/il/tarih)")

        self._dismiss_notices(page)
        cat_ok = self._select_category_tasinmaz(page)
        prov_ok = self._select_province(page, province)
        if not self._set_and_verify_dates(page, start_ui, end_ui):
            rec["status"] = "DATE_INPUT_UNVERIFIED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  tarih girişi doğrulanamadı (kategori={cat_ok} il={prov_ok}) — pencere atlandı (uydurma yok).")
            return None
        if not self._click_ara(page):
            rec["status"] = "ARA_BUTTON_NOT_LOCATED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  ARA (arama) kontrolü bulunamadı — pencere atlandı. `--diagnose` çıktısındaki aksiyon adaylarını paylaşın.")
            return None
        self._beat("ARA sonrası sonuç durumu bekleniyor")
        result_html = self._wait_result_state(page)

        exp = detect_session_expiration(result_html, page.url)
        if exp["expired"]:
            rec["status"] = "SESSION_EXPIRED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  OTURUM SONA ERDİ ({exp['reason']}). Durum kaydedildi; yeniden giriş sonrası devam edilebilir.")
            return "SESSION_EXPIRED"
        if zero_results(result_html):
            rec["result_count"] = 0
            rec["status"] = "COMPLETE"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  0 sonuç. Pencere tamamlandı.")
            return None

        meta = extract_result_metadata(result_html)
        cards_present = bool(parse_result_cards(result_html))
        if meta.get("result_count") is None and not cards_present:
            rec["status"] = "RESULT_STATE_UNCONFIRMED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  sonuç durumu doğrulanamadı: ARA sonrası numaralı 'N sonuç bulundu' ya da sonuç "
                        "kartı görünmedi (zaman aşımı / ARA tetiklenmedi / sonuçlar farklı yükleniyor olabilir). "
                        "Pencere COMPLETE İŞARETLENMEDİ — `--diagnose-results` ile sonuç yapısını paylaşın.")
            return None

        rec["result_count"] = meta.get("result_count")
        rec["total_pages"] = meta.get("total_pages")
        upsert_window_record(state, rec)
        save_bulk_state(state, self.store_dir)

        valid_pages = self._valid_pages(page, meta)
        self._print(f"  sonuç={meta.get('result_count')} · geçerli sayfalar={valid_pages} "
                    f"(page 0 hariç) · tamamlanan={rec.get('pages_completed')}")

        processed_ids: set = set()
        pages_failed: list = []
        for pnum in pages_remaining(rec, valid_pages):
            self._beat(f"sayfa {pnum} yükleniyor")
            if not self._goto_page(page, pnum):
                pages_failed.append(pnum)
                self._print(f"  sayfa {pnum} yüklenemedi/kart kümesi değişmedi — atlanıyor (pencere COMPLETE değil, tekrar denenir).")
                continue
            page_html = page.content()
            exp = detect_session_expiration(page_html, page.url)
            if exp["expired"]:
                rec["status"] = "SESSION_EXPIRED"
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
                return "SESSION_EXPIRED"

            cards = parse_result_cards(page_html)
            # Sayfalar arası KAYIT NO tekilleştirme: aynı kart iki sayfada görünürse tekrar işlenmez.
            new_cards = []
            for c in cards:
                cid = c.get("kayit_no") or c.get("file_id")
                if cid in processed_ids:
                    continue
                processed_ids.add(cid)
                new_cards.append(c)
            rec["result_cards_inspected"] += len(new_cards)
            summary["result_cards_inspected"] += len(new_cards)
            sold = [c for c in new_cards if c["sold"]]
            self._print(f"  sayfa {pnum}/{valid_pages[-1] if valid_pages else pnum} · kart={len(new_cards)} · Satıldı={len(sold)}")

            for card in sold:
                if target_kayit_no and (card.get("kayit_no") or "") != target_kayit_no:
                    continue                # hedefli edinim: yalnız istenen KAYIT NO işlenir
                if max_records and summary["records_processed"] >= max_records:
                    return "MAX_RECORDS"
                self._beat(f"KAYIT NO {card.get('kayit_no')}: belge ediniliyor (native)")
                res = process_sold_auction(
                    card, acquire_documents=acquire, store_dir=self.store_dir,
                    genuine_path=self.genuine_path, discovery_only=discovery_only,
                    source_page_ref=self._safe_ref(page.url), province_label=province, window=w,
                    force=force,
                )
                summary["records_processed"] += 1
                rec["sold_discovered"] += 1
                summary["sold_discovered"] += 1
                self._report_auction(res)
                if res["outcome"] == "skipped_already_acquired":
                    rec["sold_skipped_known"] += 1
                    summary["sold_skipped_known"] += 1
                elif res["outcome"] == "acquired":
                    rec["acquisitions_complete"] += 1
                    summary["acquisitions_completed"] += 1
                elif res["outcome"] == "acquisition_failed":
                    rec["acquisitions_failed"] += 1
                    summary["acquisition_failures"] += 1
                self._close_modal(page)
                if not discovery_only:
                    page.wait_for_timeout(self.request_delay_ms)  # seri/tutucu kaynak davranışı
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)

            mark_page_complete(rec, pnum)
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  sayfa {pnum} kontrol noktası kaydedildi.")

        if pages_failed:
            rec["status"] = "PAGINATION_INCOMPLETE"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  sayfa(lar) {pages_failed} yüklenemedi — pencere COMPLETE İŞARETLENMEDİ, sonraki koşuda tekrar denenir.")
            return None
        rec["status"] = "COMPLETE"
        upsert_window_record(state, rec)
        save_bulk_state(state, self.store_dir)
        return None

    # -- Canlı DOM yardımcıları (pragma) — gerçek gözlenen UYAP DOM'una uyarlanır ---------- #
    def _find_gecmis_page(self, context):  # pragma: no cover - canlı DOM
        # ÖNCE URL'e göre ayıkla (ucuz, ASILMAZ): önceki koşumdan kalan TAKILI görüntüleyici/indirme
        # sekmeleri (blob:/data:/file:/about:blank) ölü renderer'a sahip olabilir; page.content() ZAMAN
        # AŞIMSIZ bloklar → o sekmelerin content()'ine HİÇ dokunma (bir saatlik asılmanın kök nedeni).
        _skip_prefix = ("blob:", "data:", "file:", "chrome:", "about:", "devtools:", "view-source:")
        app_pages = []
        for p in context.pages:
            try:
                url = _fold(p.url or "")
            except Exception:
                continue
            if not url or any(url.startswith(s) for s in _skip_prefix):
                continue
            app_pages.append(p)
        best = None
        for p in app_pages:
            try:
                fold = _fold(p.content())
            except Exception:
                continue
            if "gecmis ilanlar" in fold or ("ihale bitis tarih" in fold and "tasinmaz" in fold):
                return p
            if best is None and ("ilan" in fold or "ihale" in fold):
                best = p
        return best or (app_pages[0] if app_pages else None)

    def _dismiss_notices(self, page):  # pragma: no cover - canlı DOM
        """Bilgilendirme/duyuru pop-up'larını + açık kalmış Bootstrap modallarını KAPATIR.

        Yasal 'Kabul Et' TIKLANMAZ; yalnız Tamam/Kapat/X/Escape. Açık kalan bir modal (ör. önceki
        koşudan ihale_evraklari_modal) bir sonraki aramanın tarih alanlarını bloklar → temizlenir.
        """
        for sel in ("#closeBtnDuyuru", "#btnCloseTrialOk"):
            try:
                el = page.locator(sel)
                if el.count() and el.first.is_visible():
                    el.first.click()
                    page.wait_for_timeout(200)
            except Exception:
                continue
        for _ in range(3):
            try:
                if page.locator(".modal.in, .modal.show").count() == 0:
                    break
                closes = page.locator(".modal.in .close, .modal.show .close, "
                                      ".modal.in [data-dismiss=modal], .modal.show [data-dismiss=modal]")
                clicked = False
                for i in range(min(closes.count(), 5)):
                    try:
                        if closes.nth(i).is_visible():
                            closes.nth(i).click()
                            clicked = True
                            page.wait_for_timeout(200)
                    except Exception:
                        continue
                if not clicked:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
            except Exception:
                break

    def _select_category_tasinmaz(self, page):  # pragma: no cover - canlı DOM
        """Geçmiş İlanlar (history) formunda Taşınmaz kategorisini seçer.

        Kategori radyoları ``radioHistory1/2/3`` (Taşınır/Taşınmaz/Taşıt). Sıra TAHMİN EDİLMEZ —
        ilişkili ETİKET metni 'taşınmaz' olan radyo bulunur ve işaretlenir (md-radiobtn gizli input
        ise etikete tıklanır).
        """
        try:
            radios = page.locator("input[id^=radioHistory]")
            for i in range(radios.count()):
                r = radios.nth(i)
                rid = r.get_attribute("id") or ""
                text = ""
                try:
                    lab = page.locator(f"label[for='{rid}']")
                    if lab.count():
                        text = lab.first.inner_text()
                    else:
                        text = r.evaluate("el => (el.closest('label') || el.parentElement || {}).innerText || ''")
                except Exception:
                    text = ""
                if "tasinmaz" in _fold(text):
                    try:
                        r.check()
                    except Exception:
                        lab = page.locator(f"label[for='{rid}']")
                        (lab.first if lab.count() else r).click(force=True)
                    return True
        except Exception:
            pass
        return False

    def _select_province(self, page, province):  # pragma: no cover - canlı DOM
        """Geçmiş İlanlar İl seçicisini (``#historySearchIller``, native <select>) ayarlar."""
        try:
            sel = page.locator("#historySearchIller")
            if not sel.count():
                return False
            for how in ("label", "value"):
                try:
                    sel.first.select_option(**{how: province})
                    return True
                except Exception:
                    continue
            try:  # görünen seçenek metnine göre (Türkçe büyük/küçük harf toleransı)
                val = sel.first.evaluate(
                    "(el,p)=>{const t=p.toLocaleUpperCase('tr-TR');"
                    "const o=[...el.options].find(o=>(o.text||'').trim().toLocaleUpperCase('tr-TR')===t);"
                    "return o?o.value:null;}",
                    province,
                )
                if val is not None:
                    sel.first.select_option(value=val)
                    return True
            except Exception:
                pass
        except Exception:
            pass
        return False

    def _set_and_verify_dates(self, page, start_ui, end_ui):  # pragma: no cover - canlı DOM
        """Başlangıç/bitiş tarih kutularını çoklu strateji ile doldurur; ARA'dan ÖNCE doğrular.

        Doğrulama maske/biçim farklarına TOLERANSLIDIR (yalnız rakamlar: 10/06/2026 == 10.06.2026).
        readonly (yalnız-takvim) alan tespit edilirse False döner (uydurma yok) → `--diagnose` ile
        gerçek DOM görülüp takvim-tıklama uygulanır.
        """
        import re as _re

        strategies = (
            lambda: page.locator("#historyBaslangicTarihi, #historyBitisTarihi"),  # Geçmiş İlanlar (birebir)
            lambda: page.get_by_role("textbox", name=_re.compile("tarih|bitis|baslang", _re.I)),
            lambda: page.locator("input[id*=tarih i], input[id*=bitis i]"),
            lambda: page.locator("input[type=date]"),
        )
        for strat in strategies:
            try:
                loc = strat()
                if loc.count() >= 2 and self._fill_verify(loc.nth(0), start_ui) and self._fill_verify(loc.nth(1), end_ui):
                    return True
            except Exception:
                continue
        return False

    def _fill_verify(self, box, ui):  # pragma: no cover - canlı DOM
        """Bir tarih kutusunu doldurur ve maske/biçim toleranslı doğrular; readonly ise False."""
        try:
            if box.get_attribute("readonly") is not None:
                return False  # yalnız-takvim alan: fill() geçersiz — diagnose ile ele alınır
            box.click()
            box.fill("")
            box.fill(ui)
            try:
                box.press("Escape")           # açılan takvim overlay'ini kapat
                box.evaluate("el => el.blur()")
            except Exception:
                pass
            return _digits(box.input_value()) == _digits(ui)
        except Exception:
            return False

    def _click_ara(self, page):  # pragma: no cover - canlı DOM
        """Geçmiş İlanlar ARA kontrolünü bulur ve tıklar (button VEYA anchor/[role=button]).

        ARA çoğu zaman <a class="btn">Ara</a> olabilir; GÖRÜNÜR olan ilk eşleşme tıklanır."""
        import re as _re

        ara_exact = _re.compile(r"^\s*ara\s*$", _re.I)
        ara_word = _re.compile(r"\bara\b", _re.I)
        getters = (
            lambda: page.get_by_role("button", name=ara_exact),
            lambda: page.get_by_role("link", name=ara_exact),
            lambda: page.locator("a[class*=btn i], button, [role=button], input[type=submit], input[type=button]").filter(has_text=ara_word),
            lambda: page.locator("[id*=ara i]").filter(has_text=ara_word),
        )
        for getter in getters:
            try:
                loc = getter()
                for i in range(min(loc.count(), 20)):
                    el = loc.nth(i)
                    try:
                        if el.is_visible():
                            el.click()
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _wait_result_state(self, page):  # pragma: no cover - canlı DOM
        """ARA sonrası sonuçların TAM yüklenmesini bekler: kart imzası STABİLLEŞENE (art arda aynı)
        kadar — AJAX kartları kademeli render edebilir (önce 8 görünüp sonra 20 olması gibi). Gerçek
        0 sonuç / oturum kaybı hemen döner.
        """
        deadline = self.result_timeout_ms
        step = 400
        waited = 0
        html = ""
        last_sig = None
        stable = 0
        while waited < deadline:
            try:
                html = page.content()
            except Exception:
                html = ""
            if detect_session_expiration(html, page.url)["expired"] or zero_results(html):
                return html
            sig = result_card_signature(html)
            if sig and sig == last_sig:
                stable += 1
                if stable >= 2:            # ~0.8s değişmedi → tam yüklendi
                    return html
            else:
                last_sig = sig
                stable = 0
            page.wait_for_timeout(step)
            waited += step
        return html

    def _result_ready(self, html):  # pragma: no cover - canlı DOM
        """Gerçek sonuç durumu: NUMARALI 'N sonuç bulundu' YA DA en az bir sonuç kartı (şablon metni değil)."""
        if extract_result_metadata(html).get("result_count") is not None:
            return True
        return bool(parse_result_cards(html))

    def _valid_pages(self, page, meta):  # pragma: no cover - canlı DOM
        """Geçerli sayfa numaraları — gerçek UYAP pagination ``<a id="item-N">N</a>`` (item-0 = geçersiz
        '0', task gereği atlanır). Yedek: rol-tabanlı numerik linkler; son çare meta total_pages."""
        import re as _re

        labels: list[str] = []
        try:
            loc = page.locator("a[id^=item-]")
            for i in range(min(loc.count(), 60)):
                labels.append(loc.nth(i).inner_text().strip())
        except Exception:
            pass
        if not labels:
            for role in ("link", "button"):
                try:
                    l2 = page.get_by_role(role, name=_re.compile(r"^\s*\d+\s*$"))
                    for i in range(min(l2.count(), 60)):
                        labels.append(l2.nth(i).inner_text().strip())
                except Exception:
                    continue
        pages = valid_result_pages(labels)   # 0 ASLA dahil edilmez
        if not pages and meta.get("total_pages"):
            pages = list(range(1, int(meta["total_pages"]) + 1))
        return pages or [1]

    def _goto_page(self, page, pnum):  # pragma: no cover - canlı DOM
        """Geçerli sayfaya (pnum>0) gider; kart imzası GERÇEKTEN değişip STABİLLEŞENE kadar bekler.

        Gerçek UYAP pagination: ``<a id="item-N">N</a>`` (javascript:; onclick). page 0 asla tıklanmaz.
        'İçerik değişti' yeterli değil (AJAX kademeli): yeni kart kümesi görünüp sabitlenmeli.
        """
        import re as _re

        if pnum <= 0:                       # page 0 ASLA tıklanmaz
            return False
        try:
            before = page.content()
        except Exception:
            before = ""
        before_sig = result_card_signature(before)
        # İlk sayfa ARA sonrası zaten aktiftir; kart varsa tekrar tıklama.
        if pnum == 1 and before_sig:
            return True
        num_re = _re.compile(rf"^\s*{pnum}\s*$")
        for getter in (
            lambda: page.locator(f"a#item-{pnum}"),
            lambda: page.locator("a[id^=item-]").filter(has_text=num_re),
            lambda: page.get_by_role("link", name=num_re),
            lambda: page.get_by_role("button", name=num_re),
        ):
            try:
                loc = getter()
                for i in range(min(loc.count(), 10)):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                        el.scroll_into_view_if_needed()
                        el.click()
                    except Exception:
                        continue
                    if self._wait_page_loaded(page, before_sig):
                        return True
            except Exception:
                continue
        return False

    def _wait_page_loaded(self, page, before_sig):  # pragma: no cover - canlı DOM
        """Tıklama sonrası kart kümesi ÖNCE (before_sig'den) değişsin SONRA stabillessin (tam yüklensin)."""
        last = None
        stable = 0
        changed = False
        for _ in range(40):                # ~12s
            page.wait_for_timeout(300)
            try:
                s = result_card_signature(page.content())
            except Exception:
                s = ()
            if s and s != before_sig:
                changed = True
            if changed and s and s == last:
                stable += 1
                if stable >= 2:
                    return True
            else:
                last = s
                stable = 0
        return changed                     # değişti ama stabilize olmadıysa da kabul; hiç değişmediyse False

    def _close_modal(self, page):  # pragma: no cover - canlı DOM
        import re as _re

        for getter in (
            lambda: page.get_by_role("button", name=_re.compile("kapat|close", _re.I)),
            lambda: page.locator("[aria-label*=close i], [class*=close], button.close"),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    loc.first.click()
                    page.wait_for_timeout(300)
                    return True
            except Exception:
                continue
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    @staticmethod
    def _safe_ref(url: object) -> str | None:
        """Repo-safe kaynak referansı: yalnızca yol (query/token/çerez DEĞİL)."""
        s = str(url or "")
        if not s:
            return None
        return s.split("?", 1)[0][:200]

    def _report_auction(self, res: dict) -> None:  # pragma: no cover - canlı çıktı
        tag = {
            "acquired": "edinildi",
            "skipped_already_acquired": "atlandı (bilinen)",
            "acquisition_failed": "edinim hatası",
            "discovered": "keşfedildi",
            "no_stable_file_identity": "kimlik yok",
        }.get(res.get("outcome"), res.get("outcome"))
        dec = res.get("audit_decision")
        self._print(f"    KAYIT NO={res.get('kayit_no')} · dosya={res.get('file_id')} · {tag}"
                    + (f" · denetim={dec}" if dec else ""))

    def _print(self, msg: str) -> None:  # pragma: no cover - canlı çıktı
        print(msg, flush=True)

    def _beat(self, step: str) -> None:  # pragma: no cover - canlı çıktı/heartbeat
        """Canlı adım nabzı: gözcü zamanlayıcısını sıfırlar + ilerlemeyi stderr'e (flush) yazar."""
        self._hb_step = step
        self._hb_ts = time.monotonic()
        print(f"[UYAP BULK] · {step}", file=sys.stderr, flush=True)

    def _start_watchdog(self):  # pragma: no cover - zamanlayıcı iş parçacığı
        """Arka plan gözcüsü: canlı bir adım stall_seconds'ı aşarsa net tanı ile SERT sonlandır.

        Takılı bir Playwright sync çağrısı (ör. ölü renderer'lı sekmede page.content()) ana iş parçacığını
        kesintisiz bloklar; tek güvenli kurtarma os._exit'tir. Kontrol noktaları zaten sayfa-sayfa kaydedilir.
        """
        stop = threading.Event()

        def _mon():
            while not stop.wait(5.0):
                if not self._live_active:
                    continue
                age = time.monotonic() - self._hb_ts
                if age > self.stall_seconds:
                    sys.stderr.write(
                        f"\n[UYAP BULK] ZAMAN AŞIMI: '{self._hb_step}' adımı {int(age)}sn ilerlemedi "
                        f"(eşik {self.stall_seconds}sn; --stall-timeout ile ayarlanır). Açık sekme≈{self._tab_count}. "
                        f"Olası neden: önceki koşumdan kalan TAKILI görüntüleyici/indirme sekmesi 'page.content()'i "
                        f"blokluyor. ÇÖZÜM: Chrome'da YALNIZ 'Geçmiş İlanlar' sekmesini bırakıp diğer sekmeleri "
                        f"kapatın, sonra komutu tekrar çalıştırın. Kontrol noktaları kaydedildi; güvenle sonlandırılıyor.\n"
                    )
                    sys.stderr.flush()
                    os._exit(4)

        threading.Thread(target=_mon, name="uyap-bulk-watchdog", daemon=True).start()
        return stop

    def _print_summary(self, s: dict) -> None:  # pragma: no cover - canlı çıktı
        self._print("\n[UYAP BULK] ÖZET")
        self._print(f"  kapsam: {s['category']} · {s['province']} · {s['date_from']}→{s['date_to']}")
        self._print(f"  pencere: {s['windows_processed']}/{s['windows_total']} işlendi")
        self._print(f"  incelenen kart: {s['result_cards_inspected']} · Satıldı keşfedilen: {s['sold_discovered']}")
        self._print(f"  edinilen: {s['acquisitions_completed']} · zaten bilinen (atlanan): {s['sold_skipped_known']} · edinim hatası: {s['acquisition_failures']}")
        self._print(f"  denetim kararları: {s['audit_decisions']}")
        self._print(f"  oturum kesintisi: {s['session_interruptions']} · durma nedeni: {s['stopped_reason']}")
        self._print("  NOT: admisyon YAPILMADI — ADMISSIBLE adaylar `sold uyap review`/`admit` ile AÇIKÇA alınır.")
