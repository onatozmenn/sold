"""UYAP TOPLU (bulk) keşif + iterasyon katmanı — ÇALIŞAN tek-açık-artırma yolunun ETRAFINDA.

Bu katman gözlenen geçmiş-ilan JSON yanıtını yalnız keşif metaverisine uyarlar. Canlı-kanıtlanmış
tek-kayıt edinim yolunu (``find_target_record_card`` → "İhale Evrak Listesi" modalı → satır →
native UDF) ve mevcut ``extract → reconcile → audit`` boru hattını YENİDEN KULLANIR. Yeni bir
izin-gevşek toplu admisyon yolu KURMAZ (admisyon, açık insan adımı ``sold uyap admit`` OLARAK
KALIR). Yapısal ekonometrik çekirdeği DEĞİŞTİRMEZ.

Güvenlik duruşu: kullanıcı Chrome'a ELLE oturum açar; toplayıcı yalnızca CDP ile BAĞLANIR.
Hiçbir parola/MFA/CAPTCHA otomatikleştirilmez, hiçbir erişim kontrolü aşılmaz. Keşif, gözlenen
same-origin JSON uç noktasını sınırlı eşzamanlılıkla kullanır; belge edinimi SERİ kalır. Tüm durum
gitignored ``data/ingestion`` altındadır.

Kapsam: yalnızca ``Taşınmaz`` kategorisi; görünür UYAP durumu POZİTİF olarak ``Satıldı`` olan
açık artırmalar edinim hedefidir. Fiyat/İncele/eksik-metin ASLA "satıldı" çıkarımı için kullanılmaz.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import functools
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import unquote_plus

from . import store
from .discovery import discover
from .models import (
    ARTIFACT_AUCTION_RESULT,
    ARTIFACT_SALE_NOTICE,
    STATE_ADMITTED,
    STATE_AUDITED,
    STATE_COLLECTED,
    STATE_DISCOVERED,
    STATE_EXCLUDED,
    STATE_EXTRACTED,
    STATE_PENDING_REVIEW,
    _ascii_lower,
    demojibake,
    deterministic_candidate_id,
)
from .pipeline import run_audit, run_extract

CATEGORY_TASINMAZ = "Taşınmaz"
CATEGORY_TASINMAZ_ID = "1"
MAX_WINDOW_DAYS = 7          # UYAP: "Tarih aralığı en fazla 1 hafta olabilir."
DEFAULT_PER_PAGE = 20
RESULT_SPLIT_THRESHOLD = 200
BULK_STATE_FILE = "bulk_state.json"
PHASE_DISCOVERY = "discovery"
PHASE_ACQUISITION = "acquisition"
HISTORY_ENDPOINT_PATH = "/pp/gecmisIhaleler_brd.ajx"
DOCUMENT_MANIFEST_ENDPOINT_PATH = "/pp/getIhaleEvrakBilgileri_brd.ajx"
DOCUMENT_DOWNLOAD_ENDPOINT_PATH = "/pp/evrak_indir_brd.uyap"
DOCUMENT_VIEW_ENDPOINT_PATH = "/pp/view_document_brd.uyap"
PROPERTY_HINT_VERSION = "uyap-history-property-v1"
DOCUMENT_MANIFEST_VERSION = "uyap-document-manifest-v1"
INSPECTION_VERSION = "uyap-candidate-inspection-v1"

UYAP_PROVINCES = (
    "ADANA", "ADIYAMAN", "AFYONKARAHİSAR", "AĞRI", "AMASYA", "ANKARA", "ANTALYA",
    "ARTVİN", "AYDIN", "BALIKESİR", "BİLECİK", "BİNGÖL", "BİTLİS", "BOLU", "BURDUR",
    "BURSA", "ÇANAKKALE", "ÇANKIRI", "ÇORUM", "DENİZLİ", "DİYARBAKIR", "EDİRNE",
    "ELAZIĞ", "ERZİNCAN", "ERZURUM", "ESKİŞEHİR", "GAZİANTEP", "GİRESUN",
    "GÜMÜŞHANE", "HAKKARİ", "HATAY", "ISPARTA", "MERSİN", "İSTANBUL", "İZMİR",
    "KARS", "KASTAMONU", "KAYSERİ", "KIRKLARELİ", "KIRŞEHİR", "KOCAELİ", "KONYA",
    "KÜTAHYA", "MALATYA", "MANİSA", "KAHRAMANMARAŞ", "MARDİN", "MUĞLA", "MUŞ",
    "NEVŞEHİR", "NİĞDE", "ORDU", "RİZE", "SAKARYA", "SAMSUN", "SİİRT", "SİNOP",
    "SİVAS", "TEKİRDAĞ", "TOKAT", "TRABZON", "TUNCELİ", "ŞANLIURFA", "UŞAK", "VAN",
    "YOZGAT", "ZONGULDAK", "AKSARAY", "BAYBURT", "KARAMAN", "KIRIKKALE", "BATMAN",
    "ŞIRNAK", "BARTIN", "ARDAHAN", "IĞDIR", "YALOVA", "KARABÜK", "KİLİS",
    "OSMANİYE", "DÜZCE",
)
_COLD_START_PRIORITY = (
    "İSTANBUL", "ANKARA", "İZMİR", "ANTALYA", "BURSA", "KOCAELİ", "MERSİN",
    "ADANA", "KONYA", "GAZİANTEP", "TEKİRDAĞ", "BALIKESİR", "AYDIN", "MUĞLA",
)

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

_HISTORY_STATUS_LABELS = {
    "0": "Satıldı",
    "1": "Birinci Alıcıya Süre Verildi",
    "2": "İkinci Alıcıya Süre Verildi",
    "3": "Satış Düştü",
    "4": "Alacağa Mahsuben",
}

_PROPERTY_HINT_TERMS = {
    "residential": (
        "mesken", "konut", "daire", "apartman", "villa", "yazlik",
        "dubleks", "tripleks", "mustakil ev", "kagir ev", "kargir ev",
    ),
    "land": (
        "arsa", "tarla", "zeytinlik", "bag", "bahce", "findiklik",
        "uzum bagi", "cay bahcesi",
    ),
    "commercial": (
        "dukkan", "isyeri", "is yeri", "buro", "ofis", "depo", "fabrika",
        "atolye", "otel", "pansiyon", "lokanta", "magaza", "ticarethane",
    ),
    "other": (
        "ahir", "samanlik", "garaj", "otopark", "akaryakit", "istasyon",
        "sanayi tesisi",
    ),
}

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


def split_date_window(window: dict) -> list[dict]:
    """Yoğun bir pencereyi boşluksuz iki kapsayıcı alt pencereye böler."""
    start = _parse_iso(window["start"])
    end = _parse_iso(window["end"])
    if start >= end:
        return []
    midpoint = start + dt.timedelta(days=(end - start).days // 2)
    return [
        {"start": start.isoformat(), "end": midpoint.isoformat()},
        {"start": (midpoint + dt.timedelta(days=1)).isoformat(), "end": end.isoformat()},
    ]


def should_split_result_window(
    window: dict,
    metadata: dict,
    valid_pages: list[int] | None = None,
    inspected_count: int = 0,
    threshold: int = RESULT_SPLIT_THRESHOLD,
) -> bool:
    result_count = metadata.get("result_count")
    page_capacity = None
    if metadata.get("total_pages") and metadata.get("per_page"):
        page_capacity = int(metadata["total_pages"]) * int(metadata["per_page"])
    pagination_capacity = (
        max(valid_pages or [], default=0) * int(metadata.get("per_page") or DEFAULT_PER_PAGE)
    )
    observed = max(
        value for value in (result_count, page_capacity, pagination_capacity, inspected_count, 0)
        if value is not None
    )
    return observed >= threshold and bool(split_date_window(window))


def result_window_saturated(
    metadata: dict,
    valid_pages: list[int] | None = None,
    inspected_count: int = 0,
    threshold: int = RESULT_SPLIT_THRESHOLD,
) -> bool:
    result_count = int(metadata.get("result_count") or 0)
    total_pages = int(metadata.get("total_pages") or 0)
    per_page = int(metadata.get("per_page") or DEFAULT_PER_PAGE)
    pagination_capacity = max(valid_pages or [], default=total_pages) * per_page
    return max(result_count, total_pages * per_page, pagination_capacity, inspected_count) >= threshold


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
def _result_marker_text(value: object) -> str:
    source = str(value or "")
    if "<" not in source or ">" not in source:
        return source
    if source.lstrip().startswith(("{", "[")):
        return source
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(source, "html.parser")
        for node in soup.select("script, style, template, [hidden], [aria-hidden=true]"):
            node.decompose()
        for node in list(soup.find_all(True)):
            if node.parent is None:
                continue
            style = re.sub(r"\s+", "", _fold(node.get("style") or ""))
            classes = {_fold(value) for value in (node.get("class") or [])}
            if (
                "display:none" in style
                or "visibility:hidden" in style
                or classes.intersection({"hidden", "hide", "d-none", "ng-hide"})
            ):
                node.decompose()
        visible = soup.get_text(" ", strip=True)
    except Exception:
        return source
    return visible


def extract_result_metadata(text: object) -> dict:
    """Görünür ``N sonuç bulundu`` / ``Toplam M sayfa`` / ``P. sayfa`` / ``her sayfada K kayıt``.

    Yalnızca güvenilir biçimde bulunanlar döndürülür (yoksa ``None``). ``20`` sonsuza dek
    varsayılmaz — her sayfada gerçek metaveri okunur.
    """
    fold = _fold(_result_marker_text(text))
    out = {"result_count": None, "total_pages": None, "current_page": None, "per_page": None}
    result_counts = [int(value) for value in re.findall(r"(\d+)\s*sonuc\s+bulundu", fold)]
    total_pages = [int(value) for value in re.findall(r"toplam\s+(\d+)\s+sayfa", fold)]
    current_pages = [int(value) for value in re.findall(r"(\d+)\s*\.?\s*sayfayi\s+gormektesiniz", fold)]
    per_pages = [int(value) for value in re.findall(r"her\s+sayfada\s+(\d+)\s+kayit", fold)]
    if result_counts:
        out["result_count"] = max(result_counts)
    if total_pages:
        out["total_pages"] = max(total_pages)
    if current_pages:
        out["current_page"] = max(current_pages)
    if per_pages:
        out["per_page"] = max(per_pages)
    return out


def zero_results(text: object) -> bool:
    """Pozitif sıfır-sonuç tespiti (giriş sayfası / oturum kaybı ile KARIŞTIRILMAZ)."""
    fold = _fold(_result_marker_text(text))
    counts = [int(value) for value in re.findall(r"(\d+)\s*sonuc\s+bulundu", fold)]
    if counts:
        return max(counts) == 0
    return "sonuc bulunamadi" in fold or "kayit bulunamadi" in fold


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


def _card_file_id_from_element(element, text: str) -> str | None:
    """Prefer the card-local official title; descriptions may cite unrelated file numbers."""
    try:
        title = element.select_one("h4#ilanTitle.box-title, h4#ilanTitle")
        if title is not None:
            official = _card_file_id(title.get_text(" ", strip=True))
            if official:
                return official
    except Exception:
        pass
    if len({re.sub(r"\s+", "", value) for value in _FILE_ID_RE.findall(text)}) > 1:
        return None
    return _card_file_id(text)


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


def _card_root_record_refs(element) -> set[str]:
    refs = set()
    nodes = [element]
    try:
        nodes.extend(element.select(".incelenen-li"))
    except Exception:
        pass
    for index, node in enumerate(nodes):
        classes = node.get("class") or []
        if isinstance(classes, str):
            classes = [classes]
        if index > 0 and "incelenen-li" not in classes:
            continue
        ref = _kayit_no_from_el(node)
        if ref:
            refs.add(ref)
    return refs


def _card_root_count(element) -> int:
    try:
        roots = element.select(".incelenen-li")
    except Exception:
        roots = []
    own_classes = element.get("class") or []
    own = 1 if "incelenen-li" in own_classes else 0
    return max(own, len(roots))


def _card_element_hidden(element) -> bool:
    current = element
    while current is not None and getattr(current, "name", None):
        try:
            style = re.sub(r"\s+", "", _fold(current.get("style") or ""))
            classes = {_fold(value) for value in (current.get("class") or [])}
            if (
                "display:none" in style
                or "visibility:hidden" in style
                or current.has_attr("hidden")
                or _fold(current.get("aria-hidden") or "") == "true"
                or classes.intersection({"hidden", "hide", "d-none", "ng-hide"})
            ):
                return True
        except Exception:
            pass
        current = current.parent
    return False


def _visible_card_element(element):
    """Clone a card and remove hidden descendants before extracting identity/status text."""
    try:
        from bs4 import BeautifulSoup

        clone = BeautifulSoup(str(element), "html.parser").find()
        if clone is None:
            return element
        for descendant in list(clone.find_all(True)):
            if descendant is clone:
                continue
            if _card_element_hidden(descendant):
                descendant.decompose()
        return clone
    except Exception:
        return element


def parse_result_cards(html: object) -> list[dict]:
    """Sonuç sayfasındaki açık-artırma kartlarını KART-YEREL olarak ayrıştırır.

    Her kart TEK distinkt dosya kimliği içermelidir (çok-kayıtlı container = tüm-liste, kart DEĞİL).
    Kimlik + görünür durum AYNI kart elementinden alınır (A'nın durumu B'nin butonuyla EŞLEŞTİRİLMEZ).
    ``kayit_no`` kaynak kanıtı olarak korunur. Kaydırma gerektirmez (statik DOM'dan). OFFLINE testable.
    """
    source = str(html or "")
    if "<" not in source or ">" not in source:
        return []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(source, "html.parser")
    except Exception:
        return []
    for sel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "[class*=incelenen]", "li", "tr"):
        candidates: list = []
        for el in soup.select(sel):
            if _card_element_hidden(el):
                continue
            visible_el = _visible_card_element(el)
            descendant_record_refs = _card_root_record_refs(visible_el)
            if _card_root_count(visible_el) > 1 or len(descendant_record_refs) > 1:
                continue
            text = visible_el.get_text(" ", strip=True)
            if not text:
                continue
            fids = {re.sub(r"\s+", "", n) for n in _FILE_ID_RE.findall(text)}
            if not fids:
                continue
            if len(fids) > 1 and not _kayit_no_from_el(visible_el):
                # Çoklu dosya numarası büyük bir container göstergesidir; ancak gerçek kart kökü
                # kendi class/attribute'ında stabil KAYIT NO taşıyorsa açıklamadaki ek atıflar meşrudur.
                continue
            candidates.append((visible_el, text))
        if not candidates:
            continue
        cards: list[dict] = []
        seen: set = set()
        for el, text in candidates:
            fid = _card_file_id_from_element(el, text)
            if not fid:
                continue
            kayit = _card_kayit_no(text) or _kayit_no_from_el(el)
            dedup_key = (kayit or "", fid, _fold(_card_institution(text) or ""))
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
    return tuple(sorted(
        (
            str(c.get("kayit_no") or ""),
            str(c.get("file_id") or ""),
            _fold(c.get("institution_text") or ""),
        )
        for c in parse_result_cards(html)
    ))


def result_state_signature(html: object) -> tuple:
    metadata = extract_result_metadata(html)
    return (
        result_card_signature(html),
        metadata.get("result_count"),
        metadata.get("total_pages"),
        metadata.get("current_page"),
        zero_results(html),
    )


def result_payload_evidence(payload: object) -> dict | None:
    text = str(payload or "")
    if not text:
        return None
    direct_signature = result_card_signature(text)
    embedded_signatures: list[tuple] = []
    structured_identities: set[str] = set()
    metadata = extract_result_metadata(text)
    counts = []
    if metadata.get("result_count") is not None:
        counts.append(int(metadata["result_count"]))
    zero = zero_results(text)

    try:
        decoded = json.loads(text)
    except Exception:
        decoded = None
    if (
        isinstance(decoded, list)
        and len(decoded) >= 4
        and isinstance(decoded[0], list)
        and isinstance(decoded[1], (int, float))
        and isinstance(decoded[2], (int, float))
        and isinstance(decoded[3], (int, float))
    ):
        rows = decoded[0]
        cards = []
        valid_rows = True
        for row in rows:
            if not isinstance(row, dict) or "kayitID" not in row or "dosyaNoTurKod" not in row:
                valid_rows = False
                break
            auction_order = row.get("ihaleSirasi")
            institution = str(row.get("birimAdi") or "")
            institution_text = (
                f"{auction_order}. ihale {institution}"
                if auction_order is not None else institution
            )
            cards.append((
                str(row.get("kayitID") or ""),
                _card_file_id(str(row.get("dosyaNoTurKod") or "")) or "",
                _fold(_card_institution(institution_text) or ""),
            ))
        if valid_rows:
            result_count = int(decoded[2])
            return {
                "cards": tuple(sorted(cards)),
                "result_count": result_count,
                "zero": result_count == 0,
            }
    identity_keys = {"kayitno", "recordref", "listingref", "dosyano", "esasno"}
    count_keys = {"resultcount", "totalcount", "totalelements", "toplamkayit", "toplamkayitsayisi"}

    def walk(value, key=""):
        nonlocal zero
        normalized_key = re.sub(r"[^a-z0-9]", "", _fold(key))
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
        elif isinstance(value, list):
            for child_value in value:
                walk(child_value, key)
        elif normalized_key in identity_keys and value not in (None, ""):
            structured_identities.add(str(value).strip())
        elif normalized_key in count_keys:
            try:
                counts.append(int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(value, str):
            signature = (
                result_card_signature(value)
                if "<" in value and ">" in value else ()
            )
            if signature:
                embedded_signatures.append(signature)
            child_metadata = extract_result_metadata(value)
            if child_metadata.get("result_count") is not None:
                counts.append(int(child_metadata["result_count"]))
            zero = zero or zero_results(value)

    if decoded is not None:
        walk(decoded)
    result_count = max(counts) if counts else None
    if result_count is not None and result_count > 0:
        zero = False
    cards = direct_signature
    if not cards and embedded_signatures:
        cards = max(embedded_signatures, key=len)
    if not cards:
        cards = tuple(sorted(structured_identities))
    if not cards and result_count is None and not zero:
        return None
    return {
        "cards": cards,
        "result_count": result_count,
        "zero": bool(zero or result_count == 0),
    }


def history_request_payload(
    province_code: object,
    window: dict,
    page_number: int = 1,
) -> dict[str, str]:
    """Build the observed UYAP history POST body for one Taşınmaz result page."""
    code = str(province_code or "").strip()
    if not code:
        raise ValueError("province_code is required")
    if page_number < 1:
        raise ValueError("page_number must be >= 1")
    return {
        "kategori": CATEGORY_TASINMAZ_ID,
        "ihaleBaslangicZamani": format_uyap_ui_date(window["start"]),
        "ihaleBitisZamani": format_uyap_ui_date(window["end"]),
        "isPilotMu": "false",
        "pageNumber": str(page_number),
        "birimId": "",
        "ilKodu": code,
        "dosyaYil": "",
        "dosyaSiraNo": "",
    }


def parse_history_response(payload: object, expected_page: int | None = None) -> dict:
    """Validate the observed ``[rows, page-size, total, current-page]`` response."""
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except Exception as exc:
            raise ValueError("history response is not valid JSON") from exc
    else:
        decoded = payload
    if not isinstance(decoded, list) or len(decoded) < 4 or not isinstance(decoded[0], list):
        raise ValueError("history response shape is invalid")

    def integer(value: object, field: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"history response {field} is invalid")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"history response {field} is invalid") from exc

    rows = decoded[0]
    returned_page_size = integer(decoded[1], "page_size")
    raw_result_count = integer(decoded[2], "result_count")
    current_page = integer(decoded[3], "current_page")
    if returned_page_size < 0 or raw_result_count < -1:
        raise ValueError("history response count metadata is invalid")
    if raw_result_count == -1 and current_page == 1:
        raise ValueError("history response first page lacks a result count")
    result_count = None if raw_result_count == -1 else raw_result_count
    if current_page < 1 or (expected_page is not None and current_page != expected_page):
        raise ValueError("history response current_page does not match the request")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("history response contains a non-object row")

    per_page = max(DEFAULT_PER_PAGE, returned_page_size)
    total_pages = (
        (result_count + per_page - 1) // per_page
        if result_count is not None and result_count > 0 else 0
    )
    if len(rows) > per_page or (result_count is not None and len(rows) > result_count):
        raise ValueError("history response row count is inconsistent")
    if result_count and current_page > total_pages:
        raise ValueError("history response current_page exceeds total pages")
    return {
        "rows": rows,
        "returned_page_size": returned_page_size,
        "result_count": result_count,
        "current_page": current_page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def classify_property_description(description: object) -> dict:
    """Classify public property text without retaining its raw or normalized contents."""
    folded = _fold(description)
    matched: dict[str, list[str]] = {}
    for property_class, terms in _PROPERTY_HINT_TERMS.items():
        hits = [
            term for term in terms
            if re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                folded,
            )
        ]
        if hits:
            matched[property_class] = hits
    classes = sorted(matched)
    residential = "residential" in matched
    if residential:
        hint = "residential" if len(classes) == 1 else "residential_mixed"
    elif len(classes) == 1:
        hint = classes[0]
    elif classes:
        hint = "mixed_nonresidential"
    else:
        hint = "unknown"
    return {
        "property_type_hint": hint,
        "residential_hint": residential,
        "property_hint_classes": classes,
        "property_hint_terms": sorted({term for terms in matched.values() for term in terms}),
        "property_hint_version": PROPERTY_HINT_VERSION,
    }


def history_rows_to_cards(rows: list[dict]) -> list[dict]:
    """Convert structured history rows using the portal renderer's status mapping."""
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("history row must be an object")
        kayit_no = str(row.get("kayitID") or "").strip()
        file_id = _card_file_id(str(row.get("dosyaNoTurKod") or ""))
        institution = str(row.get("birimAdi") or "").strip()
        if not kayit_no or not file_id or not institution:
            raise ValueError("history row lacks stable candidate identity")
        raw_status = row.get("satisDurumu")
        status_code = str(raw_status).strip() if raw_status is not None else None
        status_raw = _HISTORY_STATUS_LABELS.get(status_code)
        classified = classify_card_status(status_raw or "")
        property_hint = classify_property_description(row.get("malAciklama"))
        cards.append({
            "kayit_no": kayit_no,
            "file_id": file_id,
            "institution_text": institution,
            "source_status_raw": status_raw,
            "category": classified["category"],
            "sold": status_code == "0",
            "card_html": None,
            "card_text": " ".join(
                value for value in (institution, file_id, kayit_no, status_raw) if value
            ),
            **property_hint,
        })
    return cards


def history_province_codes(options: object) -> dict[str, str]:
    """Map native ``#historySearchIller`` option labels to canonical provinces."""
    if not isinstance(options, list):
        raise ValueError("province options must be a list")
    canonical = {_fold(province): province for province in UYAP_PROVINCES}
    result: dict[str, str] = {}
    for option in options:
        if not isinstance(option, dict):
            continue
        province = canonical.get(_fold(option.get("label")).strip())
        code = str(option.get("value") or "").strip()
        if not province or not code:
            continue
        if province in result and result[province] != code:
            raise ValueError(f"conflicting province code for {province}")
        result[province] = code
    return result


def parse_document_manifest(payload: object) -> dict:
    """Parse the observed five-group, candidate-bound UYAP document manifest."""
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except Exception as exc:
            raise ValueError("document manifest is not valid JSON") from exc
    else:
        decoded = payload
    if not isinstance(decoded, dict):
        raise ValueError("document manifest must be an object")
    if (
        set(decoded) == {"errorCode", "error"}
        and all(isinstance(decoded.get(key), str) for key in ("errorCode", "error"))
    ):
        return {
            "version": DOCUMENT_MANIFEST_VERSION,
            "group_counts": {},
            "document_count": 0,
            "response_uri_count": 0,
            "recognized_document_types": [],
            "sale_notice_count": 0,
            "auction_result_count": 0,
            "direct_download_eligible": False,
            "source_error": True,
            "downloads": [],
        }

    groups: dict[int, list[dict]] = {}
    for index in range(5):
        rows = decoded.get(str(index), decoded.get(index))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"document manifest group {index} is invalid")
        groups[index] = rows

    from .collect import classify_document_label

    downloads = []
    recognized_types = set()
    for index, row in enumerate(groups[0]):
        downloads.append({
            "artifact_type": ARTIFACT_SALE_NOTICE,
            "islem_turu": "satis",
            "evrak_sayisi": None,
            "response_uri": str(row.get("evrakUri") or ""),
            "group": 0,
            "index": index,
        })
        recognized_types.add(ARTIFACT_SALE_NOTICE)
    for group_index in (1, 2, 3):
        for index, row in enumerate(groups[group_index]):
            artifact_type = classify_document_label(str(row.get("aciklama") or ""))
            if artifact_type:
                recognized_types.add(artifact_type)
            if group_index == 3 and artifact_type == ARTIFACT_AUCTION_RESULT:
                downloads.append({
                    "artifact_type": artifact_type,
                    "islem_turu": "satisSonu",
                    "evrak_sayisi": index,
                    "response_uri": str(row.get("evrakUri") or ""),
                    "group": group_index,
                    "index": index,
                })
    for index, row in enumerate(groups[4]):
        downloads.append({
            "artifact_type": ARTIFACT_AUCTION_RESULT,
            "islem_turu": "tutanak",
            "evrak_sayisi": index,
            "response_uri": str(row.get("evrakUri") or ""),
            "group": 4,
            "index": index,
        })
        recognized_types.add(ARTIFACT_AUCTION_RESULT)

    sale_notices = [
        item for item in downloads if item["artifact_type"] == ARTIFACT_SALE_NOTICE
    ]
    auction_results = [
        item for item in downloads if item["artifact_type"] == ARTIFACT_AUCTION_RESULT
    ]
    selected = sale_notices + auction_results
    direct_eligible = (
        len(sale_notices) == 1
        and len(auction_results) == 1
        and all(item["response_uri"] for item in selected)
    )
    response_uris = {
        str(row.get("evrakUri"))
        for rows in groups.values()
        for row in rows
        if row.get("evrakUri")
    }
    return {
        "version": DOCUMENT_MANIFEST_VERSION,
        "group_counts": {str(index): len(groups[index]) for index in range(5)},
        "document_count": sum(len(rows) for rows in groups.values()),
        "response_uri_count": len(response_uris),
        "recognized_document_types": sorted(recognized_types),
        "sale_notice_count": len(sale_notices),
        "auction_result_count": len(auction_results),
        "direct_download_eligible": direct_eligible,
        "downloads": selected if direct_eligible else [],
        "review_downloads": selected,
    }


def validate_native_download(payload: object, requested_artifact_type: str) -> dict:
    """Validate one direct native download before it can become a source artifact."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise ValueError("native download request failed")
    if int(payload.get("status") or 0) != 200:
        raise ValueError("native download returned a non-200 status")
    encoded = payload.get("body_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("native download body is missing")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("native download body is not valid base64") from exc
    if len(data) != int(payload.get("size") or -1):
        raise ValueError("native download byte count mismatch")

    from .extract import corroborate_native_document_type
    from .udf import extract_udf_source_text, native_udf_supported

    text, udf_diagnostics = extract_udf_source_text(data)
    if not text or not native_udf_supported(udf_diagnostics):
        raise ValueError(
            str(udf_diagnostics.get("blocking_reason") or "native UDF source unavailable")
        )
    corroboration = corroborate_native_document_type(text, requested_artifact_type)
    if not corroboration["native_document_type_corroborated"]:
        raise ValueError(
            f"native document type mismatch: "
            f"{corroboration['native_document_type_corroboration_reason']}"
        )
    return {
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "container_extension": ".udf",
        "source_transport": "candidate_bound_download",
        "diagnostics": {
            "container_kind": udf_diagnostics.get("container_kind"),
            "zip_valid": bool(udf_diagnostics.get("zip_valid")),
            "content_xml_found": bool(udf_diagnostics.get("content_xml_found")),
            "xml_parse_succeeded": bool(udf_diagnostics.get("xml_parse_succeeded")),
            "detected_document_type": corroboration.get("native_detected_document_type"),
            "document_type_corroborated": True,
        },
    }


def validate_exact_view_document(payload: object, requested_artifact_type: str) -> dict:
    """Validate a manifest-URI-owned ODF transformation before source promotion."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise ValueError("exact view document request failed")
    if int(payload.get("status") or 0) != 200:
        raise ValueError("exact view document returned a non-200 status")
    encoded = payload.get("body_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("exact view document body is missing")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("exact view document body is not valid base64") from exc
    if len(data) != int(payload.get("size") or -1):
        raise ValueError("exact view document byte count mismatch")

    from .extract import corroborate_manifest_document_type
    from .udf import extract_odf_source_text

    text, diagnostics = extract_odf_source_text(data)
    if not text or not diagnostics.get("text_extraction_supported"):
        raise ValueError(
            str(diagnostics.get("blocking_reason") or "exact ODF source unavailable")
        )
    corroboration = corroborate_manifest_document_type(text, requested_artifact_type)
    if not corroboration["native_document_type_corroborated"]:
        raise ValueError(
            f"exact view document type mismatch: "
            f"{corroboration['native_document_type_corroboration_reason']}"
        )
    return {
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "container_extension": ".odt",
        "source_transport": "manifest_uri_view",
        "diagnostics": {
            "container_kind": diagnostics.get("container_kind"),
            "zip_valid": bool(diagnostics.get("zip_valid")),
            "content_xml_found": bool(diagnostics.get("content_xml_found")),
            "xml_parse_succeeded": bool(diagnostics.get("xml_parse_succeeded")),
            "detected_document_type": corroboration.get("native_detected_document_type"),
            "document_type_corroborated": True,
            "document_types_present": corroboration.get("manifest_document_types_present"),
            "mixed_document_type": bool(corroboration.get("manifest_document_mixed_type")),
        },
    }


def _validated_document_text(validated: dict) -> str:
    from .udf import extract_odf_source_text, extract_udf_source_text

    parser = (
        extract_odf_source_text
        if validated.get("container_extension") == ".odt"
        else extract_udf_source_text
    )
    text, diagnostics = parser(validated["data"])
    if not text or not diagnostics.get("text_extraction_supported"):
        raise ValueError(
            str(diagnostics.get("blocking_reason") or "validated document text unavailable")
        )
    return text


def multi_result_documents_agree(documents: list[tuple[dict, dict]]) -> dict:
    """Require all result documents to agree on price and non-personal asset identity."""
    from .extract import extract_evidence

    result_documents = [
        (spec, validated)
        for spec, validated in documents
        if spec.get("artifact_type") == ARTIFACT_AUCTION_RESULT
    ]
    if len(result_documents) < 2:
        return {"agreed": False, "reason": "multiple_result_documents_required"}
    amounts = []
    descriptor_values = {
        field: set()
        for field in ("ada", "parsel", "block", "section_no", "floor", "property_type")
    }
    for _, validated in result_documents:
        text = _validated_document_text(validated)
        evidence = extract_evidence([{
            "artifact_type": ARTIFACT_AUCTION_RESULT,
            "text": text,
        }])
        if evidence.ihale_bedeli is None:
            return {"agreed": False, "reason": "result_document_missing_ihale_bedeli"}
        amounts.append(round(float(evidence.ihale_bedeli), 2))
        for field, values in descriptor_values.items():
            value = getattr(evidence, field, None)
            if value not in (None, ""):
                values.add(str(value))
    distinct_amounts = sorted(set(amounts))
    if len(distinct_amounts) != 1:
        return {
            "agreed": False,
            "reason": "result_document_ihale_bedeli_conflict",
            "distinct_amount_count": len(distinct_amounts),
        }
    conflicts = sorted(
        field for field, values in descriptor_values.items() if len(values) > 1
    )
    if conflicts:
        return {
            "agreed": False,
            "reason": "result_document_asset_identity_conflict",
            "conflicting_fields": conflicts,
        }
    return {
        "agreed": True,
        "reason": "same_ihale_bedeli_and_no_asset_identity_conflict",
        "result_document_count": len(result_documents),
        "agreed_ihale_bedeli": distinct_amounts[0],
        "matched_descriptor_fields": sorted(
            field for field, values in descriptor_values.items() if len(values) == 1
        ),
    }


def dom_matches_result_evidence(html: object, evidence: dict) -> bool:
    dom_cards = result_card_signature(html)
    response_cards = tuple(evidence.get("cards") or ())
    if response_cards:
        response_count = evidence.get("result_count")
        dom_count = extract_result_metadata(html).get("result_count")
        return (
            bool(dom_cards)
            and dom_cards == response_cards
            and response_count is not None
            and dom_count is not None
            and int(dom_count) == int(response_count)
        )
    if evidence.get("zero") or evidence.get("result_count") == 0:
        return zero_results(html) and not dom_cards
    return False


def request_has_filter_groups(request, filter_groups: tuple[tuple[str, ...], ...]) -> bool:
    if not filter_groups:
        return False
    try:
        raw = f"{request.url or ''} {request.post_data or ''}"
    except Exception:
        return False
    decoded = unquote_plus(raw)
    date_tokens = {
        _digits(token)
        for token in re.findall(
            r"(?<!\d)(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}|\d{8})(?!\d)",
            decoded,
        )
    }
    return all(
        any(_digits(variant) in date_tokens for variant in group)
        for group in filter_groups
    )


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


def select_diagnostic_result_card(
    cards: list[dict],
    target_file_id: object = None,
    target_kayit_no: object = None,
) -> dict | None:
    """Select one explicit card; only selector-free diagnosis may fall back to the first sold card."""
    file_value = str(target_file_id or "").strip()
    record_value = str(target_kayit_no or "").strip()
    if file_value or record_value:
        return next((
            card for card in cards
            if (not file_value or str(card.get("file_id") or "") == file_value)
            and (not record_value or str(card.get("kayit_no") or "") == record_value)
        ), None)
    return next((card for card in cards if card.get("sold")), None)


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


def _exclusive_bulk_run(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        if kwargs.get("dry_run", False):
            return method(self, *args, **kwargs)
        from .io import locked

        run_guard = bulk_state_path(self.store_dir).with_suffix(".run")
        with locked(run_guard):
            return method(self, *args, **kwargs)

    return wrapped


def load_bulk_state(store_dir: Path | str | None = None) -> dict:
    p = bulk_state_path(store_dir)
    if not p.exists():
        return {"windows": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"windows": []}
    if not isinstance(data, dict):
        return {"schema_version": 2, "windows": []}
    windows = []
    seen = set()
    migrated = 0
    for record in data.get("windows", []):
        rec = dict(record)
        legacy = not rec.get("phase")
        if legacy:
            # Legacy state cannot prove whether COMPLETE meant discovery or acquisition.
            # Treat it as discovery; incomplete candidates independently drive acquisition.
            rec["phase"] = PHASE_DISCOVERY
            rec["key"] = window_key(
                rec.get("province"), rec.get("window_start"), rec.get("window_end"),
                PHASE_DISCOVERY,
            )
            migrated += 1
        else:
            rec["phase"] = _phase(rec["phase"])
            rec["key"] = window_key(
                rec.get("province"), rec.get("window_start"), rec.get("window_end"),
                rec["phase"],
            )
        if (
            legacy
            and rec.get("status") == "COMPLETE"
            and result_window_saturated(rec, rec.get("pages_completed") or [])
        ):
            rec["legacy_status"] = "COMPLETE"
            rec["legacy_pages_completed"] = list(rec.get("pages_completed") or [])
            rec["pages_completed"] = []
            rec["status"] = "LEGACY_SATURATION_RECHECK"
        elif (
            legacy
            and rec.get("status") == "COMPLETE"
            and (
                rec.get("result_count") is None
                or not rec.get("pages_completed")
                or set(rec.get("pages_completed") or []) != set(range(
                    1,
                    (
                        int(rec.get("result_count") or 0)
                        + int(rec.get("per_page") or DEFAULT_PER_PAGE)
                        - 1
                    ) // int(rec.get("per_page") or DEFAULT_PER_PAGE) + 1,
                ))
            )
        ):
            rec["legacy_status"] = "COMPLETE"
            rec["legacy_pages_completed"] = list(rec.get("pages_completed") or [])
            rec["pages_completed"] = []
            rec["status"] = "LEGACY_CARDINALITY_RECHECK"
        if rec["key"] in seen:
            continue
        seen.add(rec["key"])
        windows.append(rec)
    data["schema_version"] = 2
    data["windows"] = windows
    if migrated:
        data["legacy_windows_migrated_to_discovery"] = int(
            data.get("legacy_windows_migrated_to_discovery") or 0
        ) + migrated
    return data


def save_bulk_state(state: dict, store_dir: Path | str | None = None) -> Path:
    p = bulk_state_path(store_dir)
    from .io import atomic_write_json, locked

    with locked(p):
        return atomic_write_json(p, state)


def _phase(phase: str | None) -> str:
    value = str(phase or PHASE_ACQUISITION).strip().lower()
    if value not in (PHASE_DISCOVERY, PHASE_ACQUISITION):
        raise ValueError(f"unknown bulk phase: {phase!r}")
    return value


def window_key(
    province: object,
    start: object,
    end: object,
    phase: str = PHASE_ACQUISITION,
) -> str:
    base = f"{_ascii_lower(province).strip()}|{start}|{end}"
    normalized = _phase(phase)
    return base if normalized == PHASE_ACQUISITION else f"{normalized}|{base}"


def new_window_record(
    province: str,
    start: str,
    end: str,
    phase: str = PHASE_ACQUISITION,
) -> dict:
    normalized = _phase(phase)
    return {
        "key": window_key(province, start, end, normalized),
        "phase": normalized,
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


def get_window_record(
    state: dict,
    province: object,
    start: object,
    end: object,
    phase: str = PHASE_ACQUISITION,
) -> dict | None:
    key = window_key(province, start, end, phase)
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


def mark_acquisition_incomplete(rec: dict, pending_refs: set[str]) -> dict:
    rec["status"] = "ACQUISITION_INCOMPLETE"
    rec["pending_record_refs"] = sorted(str(value) for value in pending_refs)
    rec["pages_completed"] = []
    return rec


def window_tree_complete(
    state: dict,
    province: str,
    window: dict,
    phase: str = PHASE_DISCOVERY,
) -> bool:
    rec = get_window_record(state, province, window["start"], window["end"], phase)
    if rec is None:
        return False
    if rec.get("status") == "COMPLETE":
        return True
    if rec.get("status") != "SPLIT":
        return False
    children = split_date_window(window)
    return bool(children) and all(
        window_tree_complete(state, province, child, phase) for child in children
    )


def window_tree_saturated_only(
    state: dict,
    province: str,
    window: dict,
    phase: str = PHASE_DISCOVERY,
) -> bool:
    """True when every unresolved leaf is saturated and all other leaves are complete."""
    rec = get_window_record(state, province, window["start"], window["end"], phase)
    if rec is None:
        return False
    if rec.get("status") == "SATURATED_UNRESOLVED":
        return True
    if rec.get("status") != "SPLIT":
        return False
    children = split_date_window(window)
    if not children:
        return False
    has_saturated = False
    for child in children:
        if window_tree_complete(state, province, child, phase):
            continue
        if window_tree_saturated_only(state, province, child, phase):
            has_saturated = True
            continue
        return False
    return has_saturated


def prioritize_pending_windows(
    state: dict,
    province: str,
    windows: list[dict],
    phase: str = PHASE_DISCOVERY,
) -> list[dict]:
    """Çözülemeyen tek-gün kaplarını normal backlog'un arkasına erteler."""
    ready: list[dict] = []
    saturated: list[dict] = []
    for window in windows:
        if phase == PHASE_DISCOVERY and window_tree_complete(
            state, province, window, phase
        ):
            continue
        target = (
            saturated
            if window_tree_saturated_only(state, province, window, phase)
            else ready
        )
        target.append(window)
    return ready + saturated


def normalize_campaign_provinces(provinces: list[str] | tuple[str, ...] | None) -> list[str]:
    if not provinces:
        return list(UYAP_PROVINCES)
    requested = []
    allowed = {_fold(province): province for province in UYAP_PROVINCES}
    for province in provinces:
        canonical = allowed.get(_fold(province).strip())
        if canonical is None:
            raise ValueError(f"unknown UYAP province: {province!r}")
        if canonical not in requested:
            requested.append(canonical)
    return requested


def canonicalize_province_label(value: object) -> str | None:
    folded = _fold(value).strip()
    if not folded:
        return None
    return {_fold(province): province for province in UYAP_PROVINCES}.get(folded)


def prioritize_campaign_provinces(provinces: list[str], state: dict | None = None) -> list[str]:
    """Önce gözlenen Satıldı/kart verimi; veri yoksa yüksek-hacimli cold-start sırası."""
    state = state or {"windows": []}
    stats: dict[str, dict[str, int]] = {}
    for record in state.get("windows", []):
        province = canonicalize_province_label(record.get("province"))
        if not province:
            continue
        row = stats.setdefault(province, {"sold": 0, "cards": 0, "saturated": 0})
        row["sold"] += int(record.get("sold_discovered") or 0)
        row["cards"] += int(record.get("result_cards_inspected") or 0)
        row["saturated"] += int(record.get("status") == "SATURATED_UNRESOLVED")
    cold_rank = {province: index for index, province in enumerate(_COLD_START_PRIORITY)}

    def key(province: str):
        row = stats.get(province, {})
        cards = int(row.get("cards") or 0)
        sold = int(row.get("sold") or 0)
        measured = 1 if cards else 0
        saturated = 1 if row.get("saturated") else 0
        yield_rate = sold / cards if cards else 0.0
        return (
            saturated,
            -measured,
            -yield_rate,
            cold_rank.get(province, len(cold_rank)),
            province,
        )

    return sorted(normalize_campaign_provinces(provinces), key=key)


def build_discovery_campaign_plan(
    provinces: list[str] | tuple[str, ...] | None,
    date_from: object,
    date_to: object,
    state: dict | None = None,
    newest_first: bool = True,
    max_windows_per_province: int | None = None,
) -> list[dict]:
    if max_windows_per_province is not None and max_windows_per_province < 1:
        raise ValueError("max_windows_per_province must be >= 1")
    state = state or {"windows": []}
    windows = generate_date_windows(date_from, date_to)
    if newest_first:
        windows.reverse()
    tasks = []
    for province in normalize_campaign_provinces(provinces):
        pending = [
            window for window in windows
            if not window_tree_complete(state, province, window, PHASE_DISCOVERY)
        ]
        pending = prioritize_pending_windows(
            state, province, pending, PHASE_DISCOVERY
        )
        if max_windows_per_province:
            pending = pending[:max_windows_per_province]
        for window in pending:
            tasks.append({"phase": PHASE_DISCOVERY, "province": province, **window})
    return tasks


def build_acquisition_queue(
    store_dir: Path | str | None = None,
    provinces: list[str] | tuple[str, ...] | None = None,
    date_from: object | None = None,
    date_to: object | None = None,
    residential_only: bool = False,
) -> list[dict]:
    ordered_provinces = normalize_campaign_provinces(provinces) if provinces else list(UYAP_PROVINCES)
    allowed = set(ordered_provinces) if provinces else None
    province_rank = {province: index for index, province in enumerate(ordered_provinces)}
    start = _parse_iso(date_from) if date_from else None
    end = _parse_iso(date_to) if date_to else None
    if start and end and end < start:
        raise ValueError("date_to must be >= date_from")
    bulk_state = load_bulk_state(store_dir)
    grouped: dict[tuple[str, str, str], dict] = {}
    for candidate in store.load_candidates(store_dir):
        if not should_acquire(candidate) or candidate.get("admitted_public_record_id"):
            continue
        metadata = candidate.get("bulk") or {}
        if residential_only and metadata.get("residential_hint") is not True:
            continue
        province = canonicalize_province_label(metadata.get("province_label"))
        window_start = metadata.get("window_start")
        window_end = metadata.get("window_end")
        if not province or not window_start or not window_end:
            continue
        if allowed and province not in allowed:
            continue
        window_start_day = _parse_iso(window_start)
        window_end_day = _parse_iso(window_end)
        if start and window_end_day < start:
            continue
        if end and window_start_day > end:
            continue
        record_ref = candidate.get("kayit_no") or metadata.get("kayit_no")
        if not record_ref:
            continue
        record_ref = str(record_ref)
        candidate_id = str(candidate["candidate_id"])
        group = grouped.setdefault(
            (province, window_start, window_end),
            {"candidate_ids": [], "record_refs": [], "candidate_ids_by_ref": {}},
        )
        group["candidate_ids"].append(candidate_id)
        group["record_refs"].append(record_ref)
        group["candidate_ids_by_ref"].setdefault(record_ref, []).append(candidate_id)
    tasks = []
    for (province, window_start, window_end), group in grouped.items():
        record_refs = sorted(set(group["record_refs"]))
        if not group["candidate_ids"] or not record_refs:
            continue
        rec = get_window_record(
            bulk_state, province, window_start, window_end, PHASE_ACQUISITION
        )
        attempted = set((rec or {}).get("attempted_record_refs") or [])
        tasks.append({
            "phase": PHASE_ACQUISITION,
            "province": province,
            "start": window_start,
            "end": window_end,
            "candidate_ids": sorted(group["candidate_ids"]),
            "candidate_ids_by_ref": {
                ref: sorted(set(candidate_ids))
                for ref, candidate_ids in group["candidate_ids_by_ref"].items()
            },
            "record_refs": record_refs,
            "candidate_count": len(group["candidate_ids"]),
            "all_targets_attempted": set(record_refs).issubset(attempted),
            "retry_round": int((rec or {}).get("retry_round") or 0),
        })
    return sorted(
        tasks,
        key=lambda task: (
            bool(task["all_targets_attempted"]),
            task["retry_round"],
            province_rank.get(task["province"], len(province_rank)),
            -_parse_iso(task["start"]).toordinal(),
        ),
    )


def acquisition_queue_blockers(
    store_dir: Path | str | None = None,
    provinces: list[str] | tuple[str, ...] | None = None,
    date_from: object | None = None,
    date_to: object | None = None,
    residential_only: bool = False,
) -> list[dict]:
    allowed = set(normalize_campaign_provinces(provinces)) if provinces else None
    start = _parse_iso(date_from) if date_from else None
    end = _parse_iso(date_to) if date_to else None
    if start and end and end < start:
        raise ValueError("date_to must be >= date_from")
    blockers = []
    for candidate in store.load_candidates(store_dir):
        if not should_acquire(candidate) or candidate.get("admitted_public_record_id"):
            continue
        metadata = candidate.get("bulk") or {}
        if residential_only and metadata.get("residential_hint") is not True:
            continue
        raw_province = metadata.get("province_label")
        province = canonicalize_province_label(raw_province)
        if allowed:
            if province and province not in allowed:
                continue
            if not province:
                continue
        window_start = metadata.get("window_start")
        window_end = metadata.get("window_end")
        if window_start and window_end:
            window_start_day = _parse_iso(window_start)
            window_end_day = _parse_iso(window_end)
            if start and window_end_day < start:
                continue
            if end and window_start_day > end:
                continue
        missing = []
        if not (candidate.get("kayit_no") or metadata.get("kayit_no")):
            missing.append("missing_kayit_no")
        if not province:
            missing.append("missing_province" if not str(raw_province or "").strip() else "invalid_province")
        if not window_start or not window_end:
            missing.append("missing_window")
        if missing:
            blockers.append({
                "candidate_id": candidate.get("candidate_id"),
                "province": province,
                "reasons": missing,
            })
            continue
    return blockers


def limit_campaign_tasks(tasks: list[dict], max_provinces: int | None) -> list[dict]:
    if max_provinces is None:
        return tasks
    if max_provinces < 1:
        raise ValueError("max_provinces must be >= 1")
    selected = []
    allowed = set()
    for task in tasks:
        province = task["province"]
        if province not in allowed and len(allowed) >= max_provinces:
            continue
        allowed.add(province)
        selected.append(task)
    return selected


def limit_campaign_windows_per_province(
    tasks: list[dict], max_windows_per_province: int | None
) -> list[dict]:
    if max_windows_per_province is None:
        return tasks
    if max_windows_per_province < 1:
        raise ValueError("max_windows_per_province must be >= 1")
    counts: dict[str, int] = {}
    selected = []
    for task in tasks:
        province = task["province"]
        if counts.get(province, 0) >= max_windows_per_province:
            continue
        counts[province] = counts.get(province, 0) + 1
        selected.append(task)
    return selected


# --------------------------------------------------------------------------- #
# 9) Tek satılan-açık-artırma işleme (ORKESTRASYON) — çalışan yolu YENİDEN KULLANIR.
# --------------------------------------------------------------------------- #
def persist_discovered_cards(
    cards: list[dict],
    *,
    store_dir: Path | str | None = None,
    source_page_ref: str | None = None,
    province_label: str | None = None,
    window: dict | None = None,
) -> list[dict]:
    """Bir sonuç sayfasındaki keşifleri tek atomik candidate-store yazımıyla kalıcılaştırır."""
    from .io import locked

    outcomes = []
    path = store.store_path(store_dir)
    with locked(path):
        candidates = store.load_candidates(store_dir)
        by_id = {candidate.get("candidate_id"): candidate for candidate in candidates}
        changed = False
        for card in cards:
            file_id = card.get("file_id")
            kayit_no = card.get("kayit_no")
            institution = card.get("institution_text") or province_label or "UYAP e-Satış"
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
                outcomes.append(outcome)
                continue
            candidate_id = deterministic_candidate_id(institution, file_id, kayit_no)
            candidate = by_id.get(candidate_id)
            created = candidate is None
            if candidate is None:
                candidate = store.new_candidate(
                    institution=institution,
                    file_id=file_id,
                    listing_ref=kayit_no,
                    status_text=card.get("source_status_raw"),
                    source_page_ref=source_page_ref,
                    record_ref=kayit_no,
                )
                candidate["state"] = STATE_DISCOVERED
                candidates.append(candidate)
                by_id[candidate_id] = candidate
                store.log_event(candidate, "discovered_batch", card.get("source_status_raw") or "")
            else:
                candidate["listing_ref"] = kayit_no or candidate.get("listing_ref")
                candidate["status_text"] = (
                    card.get("source_status_raw")
                    if card.get("source_status_raw") is not None
                    else candidate.get("status_text")
                )
                candidate["source_page_ref"] = source_page_ref or candidate.get("source_page_ref")
                store.log_event(candidate, "rediscovered_batch", card.get("source_status_raw") or "")
            bulk_metadata = {
                "kayit_no": kayit_no,
                "province_label": province_label,
                "window_start": (window or {}).get("start"),
                "window_end": (window or {}).get("end"),
                "source_status_raw": card.get("source_status_raw"),
            }
            if card.get("property_hint_version"):
                bulk_metadata.update({
                    "property_type_hint": card.get("property_type_hint"),
                    "residential_hint": bool(card.get("residential_hint")),
                    "property_hint_classes": list(card.get("property_hint_classes") or []),
                    "property_hint_terms": list(card.get("property_hint_terms") or []),
                    "property_hint_version": card.get("property_hint_version"),
                })
            candidate.setdefault("bulk", {}).update(bulk_metadata)
            changed = True
            outcome.update({
                "candidate_id": candidate_id,
                "discovered": True,
                "outcome": "discovered" if created else "rediscovered",
            })
            outcomes.append(outcome)
        if changed:
            store.save_candidates(candidates, store_dir)
    return outcomes


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
        artifacts, patterns, diag = acquire_documents(file_id, institution, record_ref=kayit_no)
    except Exception as exc:  # edinim hatası kaydı; sonraki açık artırmanın kimliğini BOZMAZ
        existing.setdefault("bulk", {})["last_acquisition_error"] = str(exc)[:160]
        store.log_event(existing, "bulk_acquisition_failed", str(exc)[:160])
        store.upsert(existing, store_dir)
        outcome.update({"outcome": "acquisition_failed", "error": str(exc)[:160]})
        return outcome
    if not artifacts:
        error = "native_document_collection_empty"
        existing.setdefault("bulk", {})["last_acquisition_error"] = error
        existing["bulk"]["collection_diagnostics"] = diag or {}
        store.log_event(existing, "bulk_acquisition_failed", error)
        store.upsert(existing, store_dir)
        outcome.update({"outcome": "acquisition_failed", "error": error})
        return outcome

    # status_card (terminal-durum kanıtı) + toplanan belgeler.
    from .collect import import_artifact

    existing["artifacts"] = list(artifacts or [])
    import_artifact(
        existing,
        "status_card",
        text=card.get("card_text") or status_raw or "",
        source_ref=source_page_ref,
        store_dir=store_dir,
        persist=True,
    )
    existing["state"] = STATE_COLLECTED
    bm = existing.setdefault("bulk", {})
    bm.pop("last_acquisition_error", None)
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


def candidate_inspection_result(candidate: dict) -> dict:
    audit_decision = (candidate.get("audit") or {}).get("decision")
    if audit_decision:
        return {
            "version": INSPECTION_VERSION,
            "status": "AUDITED",
            "reason": str(audit_decision),
        }
    metadata = candidate.get("bulk") or {}
    manifest = metadata.get("document_manifest") or {}
    direct = metadata.get("direct_acquisition") or {}
    manifest_status = manifest.get("status")
    direct_status = direct.get("status")
    if manifest_status == "SOURCE_ERROR":
        reason = "source_manifest_error"
    elif direct_status == "REQUIRES_UI_FALLBACK":
        error = str(direct.get("error") or "")
        reason = (
            "multi_result_documents_not_agreed"
            if error.startswith("multi_result:")
            else "manifest_group_content_semantics_mismatch"
        )
    elif manifest_status == "REQUIRES_UI_FALLBACK":
        sale_count = int(manifest.get("sale_notice_count") or 0)
        result_count = int(manifest.get("auction_result_count") or 0)
        if sale_count == 0:
            reason = "sale_notice_missing"
        elif result_count == 0:
            reason = "auction_result_missing"
        else:
            reason = "manifest_document_identity_ambiguous"
    else:
        return {
            "version": INSPECTION_VERSION,
            "status": "INCOMPLETE",
            "reason": "candidate_not_terminally_inspected",
        }
    return {
        "version": INSPECTION_VERSION,
        "status": "MANUAL_REQUIRED",
        "reason": reason,
    }


def finalize_inspection_statuses(
    store_dir: Path | str | None = None,
    date_from: object | None = None,
    date_to: object | None = None,
) -> dict:
    start = _parse_iso(date_from) if date_from else None
    end = _parse_iso(date_to) if date_to else None
    if start and end and end < start:
        raise ValueError("date_to must be >= date_from")
    from .io import locked

    with locked(store.store_path(store_dir)):
        candidates = store.load_candidates(store_dir)
        scoped = 0
        changed = False
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for candidate in candidates:
            metadata = candidate.get("bulk") or {}
            window_start = metadata.get("window_start")
            window_end = metadata.get("window_end")
            if start or end:
                if not window_start or not window_end:
                    continue
                window_start_day = _parse_iso(window_start)
                window_end_day = _parse_iso(window_end)
                if start and window_end_day < start:
                    continue
                if end and window_start_day > end:
                    continue
            scoped += 1
            result = candidate_inspection_result(candidate)
            status = result["status"]
            reason = result["reason"]
            by_status[status] = by_status.get(status, 0) + 1
            by_reason[reason] = by_reason.get(reason, 0) + 1
            if metadata.get("inspection") != result:
                candidate.setdefault("bulk", {})["inspection"] = result
                store.log_event(candidate, "candidate_inspection_finalized", f"{status}:{reason}")
                changed = True
        if changed:
            store.save_candidates(candidates, store_dir)
    return {
        "candidates_scoped": scoped,
        "by_status": by_status,
        "by_reason": by_reason,
        "all_inspected": by_status.get("INCOMPLETE", 0) == 0,
    }


# --------------------------------------------------------------------------- #
# 10) Canlı orkestratör (KULLANICI-KONTROLLÜ oturuma CDP ile bağlanır).
# --------------------------------------------------------------------------- #
class UyapBulkCollector:
    """UYAP "Geçmiş İlanlar" için kontrol-noktalı toplu orkestratör.

    Kimlik doğrulama OTOMATİKLEŞTİRİLMEZ: kullanıcı Chrome'u ``--remote-debugging-port`` ile
    başlatıp ELLE oturum açar ve ``e-Satış → İhaleler → Geçmiş İlanlar`` sayfasına gelir; bu sınıf
    yalnızca CDP ile BAĞLANIR. Keşif istekleri sınırlı bir same-origin havuzunda yürütülebilir;
    her satılan açık artırmanın belge edinimi çalışan seri tek-kayıt yoluna
    (``BrowserCollector._collect_documents``) ve mevcut ``extract/audit`` boru hattına beslenir;
    ADMİSYON YAPILMAZ.
    """

    def __init__(
        self,
        cdp_endpoint: str,
        store_dir: Path | str | None = None,
        genuine_path: Path | str | None = None,
        request_delay_ms: int = 400,
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
            before_html = page.content()
            ara, result_html, transitioned = (
                self._click_and_wait_result(
                    page,
                    result_state_signature(before_html),
                    ((start_ui, w["start"]), (end_ui, w["end"])),
                )
                if dates else (False, page.content(), False)
            )
            summary = summarize_result_structure(result_html)
            summary["steps"] = {
                "window": w, "category_selected": cat, "province_selected": prov,
                "dates_verified": dates, "ara_clicked": ara,
                "result_transition_verified": transitioned,
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
            before_html = page.content()
            ara, result_html, refresh_verified = (
                self._click_and_wait_result(
                    page,
                    result_state_signature(before_html),
                    ((start_ui, w["start"]), (end_ui, w["end"])),
                )
                if dates else (False, page.content(), False)
            )
            cards = parse_result_cards(result_html) if ara and refresh_verified else []
            target = select_diagnostic_result_card(
                cards,
                target_file_id=target_file_id,
                target_kayit_no=target_kayit_no,
            )
            fid = target.get("file_id") if target else target_file_id
            pre_tabs = [self._safe_ref(p.url) for p in context.pages]
            selected_record_ref = target.get("kayit_no") if target else target_kayit_no
            docs, patterns, diag = collector._collect_documents(
                page,
                context,
                fid,
                target.get("institution_text") if target else None,
                native_only=True,
                target_record_ref=selected_record_ref,
            )
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

    def _history_province_codes(self, page) -> dict[str, str]:  # pragma: no cover - canlı DOM
        options = page.evaluate(
            """() => [...document.querySelectorAll('#historySearchIller option')]
                .map(option => ({label: (option.textContent || '').trim(), value: option.value || ''}))"""
        )
        return history_province_codes(options)

    def _fetch_history_windows(
        self,
        page,
        jobs: list[dict],
        concurrency: int,
    ) -> list[dict]:  # pragma: no cover - canlı ağ
        if not jobs:
            return []
        return page.evaluate(
            """async ({jobs, concurrency, endpoint, timeoutMs, threshold, defaultPerPage}) => {
                const output = new Array(jobs.length);
                let cursor = 0;

                async function fetchPage(job, pageNumber) {
                    let last = null;
                    for (let attempt = 1; attempt <= 2; attempt += 1) {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const payload = {...job.payload, pageNumber: String(pageNumber)};
                            const response = await fetch(endpoint, {
                                method: 'POST',
                                credentials: 'same-origin',
                                headers: {
                                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                    'X-Requested-With': 'XMLHttpRequest'
                                },
                                body: new URLSearchParams(payload),
                                signal: controller.signal
                            });
                            const body = await response.text();
                            last = {
                                page: pageNumber,
                                ok: response.ok,
                                status: response.status,
                                url: response.url,
                                body,
                                attempts: attempt,
                                error: null
                            };
                            if (response.ok || response.status === 401 || response.status === 403) {
                                return last;
                            }
                        } catch (error) {
                            last = {
                                page: pageNumber,
                                ok: false,
                                status: 0,
                                url: endpoint,
                                body: '',
                                attempts: attempt,
                                error: String(error && error.message || error)
                            };
                        } finally {
                            clearTimeout(timer);
                        }
                    }
                    return last;
                }

                async function worker() {
                    while (true) {
                        const index = cursor++;
                        if (index >= jobs.length) return;
                        const job = jobs[index];
                        const item = {id: job.id, responses: []};
                        const first = await fetchPage(job, 1);
                        item.responses.push(first);
                        if (first && first.ok) {
                            try {
                                const decoded = JSON.parse(first.body);
                                const returned = Number(decoded && decoded[1]);
                                const total = Number(decoded && decoded[2]);
                                const perPage = Math.max(defaultPerPage, Number.isFinite(returned) ? returned : 0);
                                let totalPages = total > 0 ? Math.ceil(total / perPage) : 0;
                                if (Number.isFinite(total) && !(job.canSplit && total >= threshold)) {
                                    if (total >= threshold) {
                                        totalPages = Math.min(totalPages, Math.ceil(threshold / perPage));
                                    }
                                    for (let pageNumber = 2; pageNumber <= totalPages; pageNumber += 1) {
                                        const response = await fetchPage(job, pageNumber);
                                        item.responses.push(response);
                                        if (!response || !response.ok) break;
                                    }
                                }
                            } catch (_) {
                                // Python validates the full response and records a fail-closed checkpoint.
                            }
                        }
                        output[index] = item;
                    }
                }

                const workerCount = Math.min(Math.max(1, concurrency), jobs.length);
                await Promise.all(Array.from({length: workerCount}, () => worker()));
                return output;
            }""",
            {
                "jobs": jobs,
                "concurrency": concurrency,
                "endpoint": HISTORY_ENDPOINT_PATH,
                "timeoutMs": self.result_timeout_ms,
                "threshold": RESULT_SPLIT_THRESHOLD,
                "defaultPerPage": DEFAULT_PER_PAGE,
            },
        )

    def _fetch_document_manifests(
        self,
        page,
        targets: list[dict],
        concurrency: int,
    ) -> list[dict]:  # pragma: no cover - canlı ağ
        if not targets:
            return []
        return page.evaluate(
            """async ({targets, concurrency, endpoint, timeoutMs}) => {
                const output = new Array(targets.length);
                let cursor = 0;

                async function fetchManifest(target) {
                    let last = null;
                    for (let attempt = 1; attempt <= 2; attempt += 1) {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const response = await fetch(endpoint, {
                                method: 'POST',
                                credentials: 'same-origin',
                                headers: {
                                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                    'X-Requested-With': 'XMLHttpRequest'
                                },
                                body: new URLSearchParams({kayitId: target.record_ref}),
                                signal: controller.signal
                            });
                            last = {
                                candidate_id: target.candidate_id,
                                record_ref: target.record_ref,
                                ok: response.ok,
                                status: response.status,
                                body: await response.text(),
                                attempts: attempt,
                                error: null
                            };
                            if (response.ok || response.status === 401 || response.status === 403) {
                                return last;
                            }
                        } catch (error) {
                            last = {
                                candidate_id: target.candidate_id,
                                record_ref: target.record_ref,
                                ok: false,
                                status: 0,
                                body: '',
                                attempts: attempt,
                                error: String(error && error.message || error)
                            };
                        } finally {
                            clearTimeout(timer);
                        }
                    }
                    return last;
                }

                async function worker() {
                    while (true) {
                        const index = cursor++;
                        if (index >= targets.length) return;
                        output[index] = await fetchManifest(targets[index]);
                    }
                }

                const workerCount = Math.min(Math.max(1, concurrency), targets.length);
                await Promise.all(Array.from({length: workerCount}, () => worker()));
                return output;
            }""",
            {
                "targets": targets,
                "concurrency": concurrency,
                "endpoint": DOCUMENT_MANIFEST_ENDPOINT_PATH,
                "timeoutMs": self.result_timeout_ms,
            },
        )

    def _fetch_native_downloads(
        self,
        page,
        jobs: list[dict],
        concurrency: int,
    ) -> list[dict]:  # pragma: no cover - canlı ağ
        if not jobs:
            return []
        return page.evaluate(
            """async ({jobs, concurrency, endpoint, timeoutMs}) => {
                const output = new Array(jobs.length);
                let cursor = 0;

                function bytesToBase64(bytes) {
                    let binary = '';
                    const chunkSize = 32768;
                    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                        binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
                    }
                    return btoa(binary);
                }

                async function fetchDocument(job) {
                    let last = null;
                    for (let attempt = 1; attempt <= 2; attempt += 1) {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const params = {
                                kayitId: job.record_ref,
                                islemTuru: job.islem_turu
                            };
                            if (job.evrak_sayisi !== null) {
                                params.evrakSayisi = String(job.evrak_sayisi);
                            }
                            const response = await fetch(
                                endpoint + '?' + new URLSearchParams(params),
                                {credentials: 'same-origin', signal: controller.signal}
                            );
                            const bytes = new Uint8Array(await response.arrayBuffer());
                            last = {
                                job_id: job.job_id,
                                candidate_id: job.candidate_id,
                                artifact_type: job.artifact_type,
                                ok: response.ok,
                                status: response.status,
                                content_type: response.headers.get('content-type'),
                                disposition: response.headers.get('content-disposition'),
                                size: bytes.length,
                                body_base64: bytesToBase64(bytes),
                                attempts: attempt,
                                error: null
                            };
                            if (response.ok || response.status === 401 || response.status === 403) {
                                return last;
                            }
                        } catch (error) {
                            last = {
                                job_id: job.job_id,
                                candidate_id: job.candidate_id,
                                artifact_type: job.artifact_type,
                                ok: false,
                                status: 0,
                                content_type: null,
                                disposition: null,
                                size: 0,
                                body_base64: '',
                                attempts: attempt,
                                error: String(error && error.message || error)
                            };
                        } finally {
                            clearTimeout(timer);
                        }
                    }
                    return last;
                }

                async function worker() {
                    while (true) {
                        const index = cursor++;
                        if (index >= jobs.length) return;
                        output[index] = await fetchDocument(jobs[index]);
                    }
                }

                const workerCount = Math.min(Math.max(1, concurrency), jobs.length);
                await Promise.all(Array.from({length: workerCount}, () => worker()));
                return output;
            }""",
            {
                "jobs": jobs,
                "concurrency": concurrency,
                "endpoint": DOCUMENT_DOWNLOAD_ENDPOINT_PATH,
                "timeoutMs": self.result_timeout_ms,
            },
        )

    def _fetch_exact_view_documents(
        self,
        page,
        jobs: list[dict],
        concurrency: int,
    ) -> list[dict]:  # pragma: no cover - canlı ağ
        if not jobs:
            return []
        return page.evaluate(
            """async ({jobs, concurrency, endpoint, timeoutMs}) => {
                const output = new Array(jobs.length);
                let cursor = 0;

                function bytesToBase64(bytes) {
                    let binary = '';
                    for (let offset = 0; offset < bytes.length; offset += 32768) {
                        binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
                    }
                    return btoa(binary);
                }

                async function fetchDocument(job) {
                    let last = null;
                    for (let attempt = 1; attempt <= 2; attempt += 1) {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const query = new URLSearchParams({
                                mimeType: 'Udf', evrakId: job.response_uri
                            });
                            const response = await fetch(endpoint + '?' + query, {
                                credentials: 'same-origin', signal: controller.signal
                            });
                            const bytes = new Uint8Array(await response.arrayBuffer());
                            last = {
                                job_id: job.job_id,
                                candidate_id: job.candidate_id,
                                artifact_type: job.artifact_type,
                                ok: response.ok,
                                status: response.status,
                                size: bytes.length,
                                body_base64: bytesToBase64(bytes),
                                attempts: attempt,
                                error: null
                            };
                            if (response.ok || response.status === 401 || response.status === 403) {
                                return last;
                            }
                        } catch (error) {
                            last = {
                                job_id: job.job_id,
                                candidate_id: job.candidate_id,
                                artifact_type: job.artifact_type,
                                ok: false,
                                status: 0,
                                size: 0,
                                body_base64: '',
                                attempts: attempt,
                                error: String(error && error.message || error)
                            };
                        } finally {
                            clearTimeout(timer);
                        }
                    }
                    return last;
                }

                async function worker() {
                    while (true) {
                        const index = cursor++;
                        if (index >= jobs.length) return;
                        output[index] = await fetchDocument(jobs[index]);
                    }
                }

                const workerCount = Math.min(Math.max(1, concurrency), jobs.length);
                await Promise.all(Array.from({length: workerCount}, () => worker()));
                return output;
            }""",
            {
                "jobs": jobs,
                "concurrency": concurrency,
                "endpoint": DOCUMENT_VIEW_ENDPOINT_PATH,
                "timeoutMs": self.result_timeout_ms,
            },
        )

    def _scan_document_manifest_tasks(
        self,
        page,
        tasks: list[dict],
        concurrency: int,
        max_records: int | None = None,
        force: bool = False,
        batch_size: int = 50,
    ) -> dict:
        candidates = store.load_candidates(self.store_dir)
        by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
        requested_ids = list(dict.fromkeys(
            str(candidate_id)
            for task in tasks
            for candidate_id in (task.get("candidate_ids") or [])
        ))
        targets = []
        for candidate_id in requested_ids:
            candidate = by_id.get(candidate_id)
            if not candidate or not should_acquire(candidate):
                continue
            metadata = candidate.get("bulk") or {}
            existing_manifest = metadata.get("document_manifest") or {}
            if (
                not force
                and existing_manifest.get("version") == DOCUMENT_MANIFEST_VERSION
                and existing_manifest.get("status")
                in ("DIRECT_ELIGIBLE", "REQUIRES_UI_FALLBACK", "SOURCE_ERROR")
            ):
                continue
            record_ref = candidate.get("kayit_no") or metadata.get("kayit_no")
            if not record_ref:
                continue
            targets.append({
                "candidate_id": candidate_id,
                "record_ref": str(record_ref),
            })
        if max_records is not None:
            targets = targets[:max_records]
        summary = {
            "targets_total": len(targets),
            "candidates_scanned": 0,
            "direct_download_eligible": 0,
            "requires_ui_fallback": 0,
            "source_errors": 0,
            "manifest_failures": 0,
            "request_count": 0,
            "stopped_reason": None,
        }
        from .io import locked

        for offset in range(0, len(targets), max(1, batch_size)):
            batch = targets[offset:offset + max(1, batch_size)]
            try:
                responses = self._fetch_document_manifests(page, batch, concurrency)
            except Exception as exc:
                summary["manifest_failures"] += len(batch)
                summary["stopped_reason"] = type(exc).__name__
                break
            responses_by_id = {
                str(response.get("candidate_id")): response
                for response in (responses or [])
                if isinstance(response, dict) and response.get("candidate_id")
            }
            for target in batch:
                candidate = by_id[target["candidate_id"]]
                metadata = candidate.setdefault("bulk", {})
                response = responses_by_id.get(target["candidate_id"])
                if not response:
                    metadata["document_manifest"] = {
                        "version": DOCUMENT_MANIFEST_VERSION,
                        "status": "REQUEST_FAILED",
                        "error": "missing manifest response",
                    }
                    summary["manifest_failures"] += 1
                    continue
                summary["request_count"] += int(response.get("attempts") or 1)
                if not response.get("ok"):
                    status_code = int(response.get("status") or 0)
                    metadata["document_manifest"] = {
                        "version": DOCUMENT_MANIFEST_VERSION,
                        "status": "REQUEST_FAILED",
                        "http_status": status_code,
                        "error": str(response.get("error") or "request failed")[:120],
                    }
                    summary["manifest_failures"] += 1
                    if status_code in (401, 403):
                        summary["stopped_reason"] = "SESSION_EXPIRED"
                    continue
                try:
                    parsed = parse_document_manifest(response.get("body"))
                except ValueError as exc:
                    metadata["document_manifest"] = {
                        "version": DOCUMENT_MANIFEST_VERSION,
                        "status": "INVALID_RESPONSE",
                        "error": str(exc)[:120],
                    }
                    summary["manifest_failures"] += 1
                    continue
                manifest_summary = {
                    key: value for key, value in parsed.items()
                    if key not in ("downloads", "review_downloads")
                }
                if parsed.get("source_error"):
                    manifest_summary["status"] = "SOURCE_ERROR"
                elif parsed["direct_download_eligible"]:
                    manifest_summary["status"] = "DIRECT_ELIGIBLE"
                else:
                    manifest_summary["status"] = "REQUIRES_UI_FALLBACK"
                metadata["document_manifest"] = manifest_summary
                store.log_event(
                    candidate,
                    "document_manifest_scanned",
                    manifest_summary["status"],
                )
                summary["candidates_scanned"] += 1
                if parsed.get("source_error"):
                    summary["source_errors"] += 1
                elif parsed["direct_download_eligible"]:
                    summary["direct_download_eligible"] += 1
                else:
                    summary["requires_ui_fallback"] += 1
            with locked(store.store_path(self.store_dir)):
                store.save_candidates(candidates, self.store_dir)
            if summary["stopped_reason"] == "SESSION_EXPIRED":
                break
        return summary

    def _persist_direct_udf(
        self,
        candidate_id: str,
        record_ref: str,
        spec: dict,
        validated: dict,
    ) -> tuple[dict, dict]:
        from .io import atomic_write_bytes

        safe_candidate = re.sub(r"[^A-Za-z0-9._-]", "_", candidate_id)
        artifact_type = str(spec["artifact_type"])
        digest = str(validated["sha256"])
        extension = str(validated.get("container_extension") or ".udf")
        artifact_dir = Path(self.store_dir or store.DEFAULT_STORE_DIR) / "artifacts" / safe_candidate
        destination = artifact_dir / f"{artifact_type}_{digest[:16]}{extension}"
        if not destination.exists():
            atomic_write_bytes(destination, validated["data"])
        params = {
            "kayitId": record_ref,
            "islemTuru": spec["islem_turu"],
        }
        if spec.get("evrak_sayisi") is not None:
            params["evrakSayisi"] = str(spec["evrak_sayisi"])
        uri_fingerprint = hashlib.sha256(
            str(spec.get("response_uri") or "").encode("utf-8")
        ).hexdigest()
        if validated.get("source_transport") == "manifest_uri_view":
            source_ref = (
                f"{DOCUMENT_VIEW_ENDPOINT_PATH}?mimeType=Udf&"
                f"evrakId_sha256={uri_fingerprint}"
            )
            note = "manifest-URI-owned same-origin ODF transformation"
        else:
            source_ref = (
                f"{DOCUMENT_DOWNLOAD_ENDPOINT_PATH}?"
                + "&".join(f"{key}={value}" for key, value in params.items())
            )
            note = "candidate-bound same-origin native UDF"
        artifact = {
            "artifact_type": artifact_type,
            "local_path": str(destination),
            "sha256": digest,
            "source_ref": source_ref,
            "note": note,
        }
        diagnostics = {
            "artifact_type": artifact_type,
            "size": int(validated["size"]),
            "sha256": digest,
            "manifest_uri_sha256": uri_fingerprint,
            **validated["diagnostics"],
        }
        return artifact, diagnostics

    def _run_fast_direct_acquisition_tasks(
        self,
        page,
        tasks: list[dict],
        concurrency: int,
        max_records: int | None = None,
        batch_size: int = 100,
    ) -> dict:
        candidates = store.load_candidates(self.store_dir)
        by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
        index_by_id = {
            str(candidate.get("candidate_id")): index
            for index, candidate in enumerate(candidates)
        }
        requested_ids = list(dict.fromkeys(
            str(candidate_id)
            for task in tasks
            for candidate_id in (task.get("candidate_ids") or [])
        ))
        targets = []
        for candidate_id in requested_ids:
            candidate = by_id.get(candidate_id)
            if not candidate or not should_acquire(candidate):
                continue
            metadata = candidate.get("bulk") or {}
            manifest_summary = metadata.get("document_manifest") or {}
            multi_result = bool(
                manifest_summary.get("status") == "REQUIRES_UI_FALLBACK"
                and int(manifest_summary.get("sale_notice_count") or 0) == 1
                and int(manifest_summary.get("auction_result_count") or 0) > 1
            )
            if (
                manifest_summary.get("status") != "DIRECT_ELIGIBLE"
                and not multi_result
            ):
                continue
            if (metadata.get("direct_acquisition") or {}).get("status") == "REQUIRES_UI_FALLBACK":
                continue
            record_ref = candidate.get("kayit_no") or metadata.get("kayit_no")
            if record_ref:
                previous_direct = metadata.get("direct_acquisition") or {}
                previous_error = str(previous_direct.get("error") or "")
                targets.append({
                    "candidate_id": candidate_id,
                    "record_ref": str(record_ref),
                    "multi_result": multi_result,
                    "prefer_exact_view": (
                        previous_direct.get("status") == "RETRYABLE_FAILURE"
                        and (
                            "document type mismatch" in previous_error
                            or "not_a_zip_compatible_container" in previous_error
                        )
                    ),
                })
        if max_records is not None:
            targets = targets[:max_records]
        if any(
            target.get("prefer_exact_view") or target.get("multi_result")
            for target in targets
        ):
            batch_size = 1
        checkpoint_every = 25 if batch_size == 1 else batch_size
        summary = {
            "targets_total": len(targets),
            "candidates_processed": 0,
            "acquired": 0,
            "retryable_failures": 0,
            "manual_fallback": 0,
            "multi_result_resolved": 0,
            "manifest_changed": 0,
            "request_count": 0,
            "downloaded_bytes": 0,
            "audit_decisions": {},
            "stopped_reason": None,
        }
        from .collect import import_artifact
        from .io import locked

        for offset in range(0, len(targets), max(1, batch_size)):
            batch = targets[offset:offset + max(1, batch_size)]
            manifest_responses = self._fetch_document_manifests(page, batch, concurrency)
            summary["request_count"] += sum(
                int(response.get("attempts") or 1)
                for response in manifest_responses or [] if isinstance(response, dict)
            )
            manifests_by_id = {
                str(response.get("candidate_id")): response
                for response in manifest_responses or []
                if isinstance(response, dict) and response.get("candidate_id")
            }
            parsed_by_id: dict[str, dict] = {}
            specs_by_job: dict[str, dict] = {}
            download_jobs = []
            for target in batch:
                candidate_id = target["candidate_id"]
                response = manifests_by_id.get(candidate_id)
                try:
                    if not response or not response.get("ok"):
                        raise ValueError("manifest request failed")
                    parsed = parse_document_manifest(response.get("body"))
                    if target.get("multi_result"):
                        eligible = bool(
                            parsed["sale_notice_count"] == 1
                            and parsed["auction_result_count"] > 1
                        )
                        selected_downloads = parsed["review_downloads"]
                    else:
                        eligible = bool(parsed["direct_download_eligible"])
                        selected_downloads = parsed["downloads"]
                    if not eligible:
                        raise ValueError("manifest no longer direct-download eligible")
                except ValueError as exc:
                    candidate = by_id[candidate_id]
                    candidate.setdefault("bulk", {})["direct_acquisition"] = {
                        "status": "MANIFEST_CHANGED",
                        "error": str(exc)[:120],
                    }
                    summary["manifest_changed"] += 1
                    continue
                parsed["selected_downloads"] = selected_downloads
                parsed_by_id[candidate_id] = parsed
                for spec in selected_downloads:
                    job_id = (
                        f"{candidate_id}|{spec['artifact_type']}|"
                        f"{spec['group']}|{spec['index']}"
                    )
                    specs_by_job[job_id] = spec
                    download_jobs.append({
                        "job_id": job_id,
                        "candidate_id": candidate_id,
                        "record_ref": target["record_ref"],
                        "artifact_type": spec["artifact_type"],
                        "islem_turu": spec["islem_turu"],
                        "evrak_sayisi": spec["evrak_sayisi"],
                        "prefer_exact_view": bool(target.get("prefer_exact_view")),
                    })
            validated_by_job: dict[str, dict] = {}
            validation_errors: dict[str, str] = {}
            preferred_view_jobs = [{
                "job_id": job["job_id"],
                "candidate_id": job["candidate_id"],
                "artifact_type": job["artifact_type"],
                "response_uri": specs_by_job[job["job_id"]]["response_uri"],
            } for job in download_jobs if job.get("prefer_exact_view")]
            preferred_responses = self._fetch_exact_view_documents(
                page, preferred_view_jobs, concurrency
            )
            summary["request_count"] += sum(
                int(response.get("attempts") or 1)
                for response in preferred_responses or [] if isinstance(response, dict)
            )
            preferred_by_job = {
                str(response.get("job_id")): response
                for response in preferred_responses or []
                if isinstance(response, dict) and response.get("job_id")
            }
            for job in preferred_view_jobs:
                try:
                    validated_by_job[job["job_id"]] = validate_exact_view_document(
                        preferred_by_job.get(job["job_id"]), job["artifact_type"]
                    )
                except ValueError as exc:
                    validation_errors[job["job_id"]] = str(exc)[:120]

            native_jobs = [
                job for job in download_jobs
                if job["job_id"] not in validated_by_job
                and not job.get("prefer_exact_view")
            ]
            download_responses = self._fetch_native_downloads(
                page, native_jobs, concurrency
            )
            summary["request_count"] += sum(
                int(response.get("attempts") or 1)
                for response in download_responses or [] if isinstance(response, dict)
            )
            downloads_by_job = {
                str(response.get("job_id")): response
                for response in download_responses or []
                if isinstance(response, dict) and response.get("job_id")
            }
            fallback_jobs = []
            for job in native_jobs:
                job_id = job["job_id"]
                spec = specs_by_job[job_id]
                try:
                    validated_by_job[job_id] = validate_native_download(
                        downloads_by_job.get(job_id), spec["artifact_type"]
                    )
                except ValueError as exc:
                    if spec.get("response_uri") and not job.get("prefer_exact_view"):
                        fallback_jobs.append({
                            "job_id": job_id,
                            "candidate_id": job["candidate_id"],
                            "artifact_type": job["artifact_type"],
                            "response_uri": spec["response_uri"],
                        })
                    else:
                        validation_errors[job_id] = validation_errors.get(
                            job_id, str(exc)[:120]
                        )
            fallback_responses = self._fetch_exact_view_documents(
                page, fallback_jobs, concurrency
            )
            summary["request_count"] += sum(
                int(response.get("attempts") or 1)
                for response in fallback_responses or [] if isinstance(response, dict)
            )
            fallback_by_job = {
                str(response.get("job_id")): response
                for response in fallback_responses or []
                if isinstance(response, dict) and response.get("job_id")
            }
            for job in fallback_jobs:
                job_id = job["job_id"]
                try:
                    validated_by_job[job_id] = validate_exact_view_document(
                        fallback_by_job.get(job_id), job["artifact_type"]
                    )
                except ValueError as exc:
                    validation_errors[job_id] = str(exc)[:120]

            for target in batch:
                candidate_id = target["candidate_id"]
                if candidate_id not in parsed_by_id:
                    continue
                original = by_id[candidate_id]
                working = copy.deepcopy(original)
                artifacts = []
                artifact_diagnostics = []
                failure = None
                selected_downloads = parsed_by_id[candidate_id]["selected_downloads"]
                validated_documents = []
                for spec in selected_downloads:
                    job_id = (
                        f"{candidate_id}|{spec['artifact_type']}|"
                        f"{spec['group']}|{spec['index']}"
                    )
                    validated = validated_by_job.get(job_id)
                    if validated is None:
                        failure = (
                            f"{spec['artifact_type']}: "
                            + validation_errors.get(
                                job_id, "required document validation failed"
                            )
                        )
                        break
                    validated_documents.append((spec, validated))
                multi_result_agreement = None
                if not failure and target.get("multi_result"):
                    try:
                        multi_result_agreement = multi_result_documents_agree(
                            validated_documents
                        )
                    except ValueError as exc:
                        multi_result_agreement = {
                            "agreed": False,
                            "reason": str(exc)[:120],
                        }
                    if not multi_result_agreement.get("agreed"):
                        failure = (
                            "multi_result:"
                            + str(multi_result_agreement.get("reason") or "not_agreed")
                        )
                if failure:
                    manual_fallback = bool(
                        "manifest_group_requested_semantics_missing" in failure
                        or failure.startswith("multi_result:")
                    )
                    original.setdefault("bulk", {})["direct_acquisition"] = {
                        "status": (
                            "REQUIRES_UI_FALLBACK"
                            if manual_fallback else "RETRYABLE_FAILURE"
                        ),
                        "error": failure,
                    }
                    store.log_event(original, "fast_direct_acquisition_failed", failure)
                    if manual_fallback:
                        summary["manual_fallback"] += 1
                    else:
                        summary["retryable_failures"] += 1
                    continue
                for spec, validated in validated_documents:
                    try:
                        artifact, diagnostics = self._persist_direct_udf(
                            candidate_id,
                            target["record_ref"],
                            spec,
                            validated,
                        )
                    except (OSError, ValueError) as exc:
                        failure = str(exc)[:120]
                        break
                    artifacts.append(artifact)
                    artifact_diagnostics.append(diagnostics)
                    summary["downloaded_bytes"] += int(validated["size"])
                if failure or len(artifacts) != len(selected_downloads):
                    manual_fallback = bool(
                        failure and "manifest_group_requested_semantics_missing" in failure
                    )
                    original.setdefault("bulk", {})["direct_acquisition"] = {
                        "status": (
                            "REQUIRES_UI_FALLBACK"
                            if manual_fallback else "RETRYABLE_FAILURE"
                        ),
                        "error": failure or "required native documents incomplete",
                    }
                    store.log_event(original, "fast_direct_acquisition_failed", failure or "incomplete")
                    if manual_fallback:
                        summary["manual_fallback"] += 1
                    else:
                        summary["retryable_failures"] += 1
                    continue
                try:
                    working["artifacts"] = artifacts
                    import_artifact(
                        working,
                        "status_card",
                        text=str(working.get("status_text") or "Satıldı"),
                        source_ref=working.get("source_page_ref"),
                        store_dir=self.store_dir,
                        persist=True,
                    )
                    working.setdefault("bulk", {})["direct_acquisition"] = {
                        "status": "COLLECTED",
                        "version": DOCUMENT_MANIFEST_VERSION,
                        "documents": artifact_diagnostics,
                    }
                    if multi_result_agreement:
                        working["bulk"]["direct_acquisition"][
                            "multi_result_agreement"
                        ] = multi_result_agreement
                    store.log_event(
                        working, "fast_direct_collected", f"docs={len(artifacts)}"
                    )
                    working = run_audit(
                        working,
                        self.store_dir,
                        self.genuine_path,
                        persist=False,
                    )
                except Exception as exc:
                    original.setdefault("bulk", {})["direct_acquisition"] = {
                        "status": "RETRYABLE_FAILURE",
                        "error": f"audit pipeline: {type(exc).__name__}",
                    }
                    store.log_event(original, "fast_direct_acquisition_failed", type(exc).__name__)
                    summary["retryable_failures"] += 1
                    continue
                candidates[index_by_id[candidate_id]] = working
                by_id[candidate_id] = working
                decision = (working.get("audit") or {}).get("decision") or "UNKNOWN"
                summary["audit_decisions"][decision] = (
                    summary["audit_decisions"].get(decision, 0) + 1
                )
                summary["acquired"] += 1
                summary["candidates_processed"] += 1
                if target.get("multi_result"):
                    summary["multi_result_resolved"] += 1
            completed = min(offset + len(batch), len(targets))
            checkpoint_due = completed == len(targets) or completed % checkpoint_every == 0
            if checkpoint_due:
                with locked(store.store_path(self.store_dir)):
                    store.save_candidates(candidates, self.store_dir)
                self._print(
                    f"[UYAP DIRECT] {completed}/{len(targets)} · "
                    f"edinilen={summary['acquired']} · retry={summary['retryable_failures']}"
                )
        return summary

    @_exclusive_bulk_run
    def run_fast_manifest_scan(
        self,
        tasks: list[dict],
        concurrency: int = 8,
        max_records: int | None = None,
        force: bool = False,
    ) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        from .collect import BrowserCollector

        if concurrency < 1 or concurrency > 16:
            raise ValueError("concurrency must be between 1 and 16")
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be >= 1")
        started_at = time.monotonic()
        sync_playwright = BrowserCollector._sync_playwright()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            page = self._find_gecmis_page(browser.contexts[0])
            if page is None:
                raise RuntimeError("no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sayfasını elle açın.")
            expiration = detect_session_expiration(page.content(), page.url)
            if expiration["expired"]:
                return {
                    "targets_total": 0,
                    "candidates_scanned": 0,
                    "direct_download_eligible": 0,
                    "requires_ui_fallback": 0,
                    "source_errors": 0,
                    "manifest_failures": 0,
                    "request_count": 0,
                    "stopped_reason": "SESSION_EXPIRED",
                    "elapsed_seconds": 0.0,
                }
            summary = self._scan_document_manifest_tasks(
                page, tasks, concurrency, max_records=max_records, force=force
            )
        elapsed = max(time.monotonic() - started_at, 1e-9)
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["requests_per_minute"] = round(
            summary["request_count"] * 60.0 / elapsed, 2
        )
        return summary

    @_exclusive_bulk_run
    def run_fast_direct_acquisition(
        self,
        tasks: list[dict],
        concurrency: int = 4,
        max_records: int | None = None,
    ) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        from .collect import BrowserCollector

        if concurrency < 1 or concurrency > 8:
            raise ValueError("direct acquisition concurrency must be between 1 and 8")
        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be >= 1")
        started_at = time.monotonic()
        sync_playwright = BrowserCollector._sync_playwright()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            page = self._find_gecmis_page(browser.contexts[0])
            if page is None:
                raise RuntimeError("no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sayfasını elle açın.")
            expiration = detect_session_expiration(page.content(), page.url)
            if expiration["expired"]:
                return {
                    "targets_total": 0,
                    "candidates_processed": 0,
                    "acquired": 0,
                    "retryable_failures": 0,
                    "manual_fallback": 0,
                    "manifest_changed": 0,
                    "request_count": 0,
                    "downloaded_bytes": 0,
                    "audit_decisions": {},
                    "stopped_reason": "SESSION_EXPIRED",
                    "elapsed_seconds": 0.0,
                }
            summary = self._run_fast_direct_acquisition_tasks(
                page, tasks, concurrency, max_records=max_records
            )
        elapsed = max(time.monotonic() - started_at, 1e-9)
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["candidates_per_minute"] = round(
            summary["acquired"] * 60.0 / elapsed, 2
        )
        summary["requests_per_minute"] = round(
            summary["request_count"] * 60.0 / elapsed, 2
        )
        return summary

    @staticmethod
    def _fast_summary(tasks: list[dict], concurrency: int) -> dict:
        provinces = list(dict.fromkeys(task["province"] for task in tasks))
        starts = [task["start"] for task in tasks]
        ends = [task["end"] for task in tasks]
        return {
            "category": CATEGORY_TASINMAZ,
            "province": f"{len(provinces)} il",
            "date_from": min(starts) if starts else None,
            "date_to": max(ends) if ends else None,
            "windows_total": len(tasks),
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
            "dry_run": False,
            "discovery_only": True,
            "phase": PHASE_DISCOVERY,
            "newest_first": True,
            "dense_windows_split": 0,
            "saturated_windows_unresolved": 0,
            "result_refresh_failures": 0,
            "acquisition_windows_incomplete": 0,
            "discovery_windows_incomplete": 0,
            "request_count": 0,
            "concurrency": concurrency,
        }

    def _run_fast_discovery_tasks(
        self,
        page,
        tasks: list[dict],
        province_codes: dict[str, str],
        concurrency: int,
        force: bool = False,
    ) -> dict:
        summary = self._fast_summary(tasks, concurrency)
        state = load_bulk_state(self.store_dir)
        pending = [dict(task) for task in tasks]
        source_page_ref = self._safe_ref(page.url)

        def mark_failure(rec: dict, status: str, detail: str) -> None:
            rec["status"] = status
            rec["last_fast_error"] = detail
            rec["pages_completed"] = []
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            summary["discovery_windows_incomplete"] += 1
            summary["result_refresh_failures"] += 1

        while pending and summary["stopped_reason"] != "SESSION_EXPIRED":
            current, pending = pending, []
            jobs = []
            records: dict[str, tuple[dict, dict]] = {}
            for task in current:
                province = canonicalize_province_label(task.get("province"))
                if not province or province not in province_codes:
                    rec = new_window_record(
                        str(task.get("province") or ""), task["start"], task["end"],
                        PHASE_DISCOVERY,
                    )
                    mark_failure(rec, "PROVINCE_CODE_UNAVAILABLE", "province code unavailable")
                    continue
                task["province"] = province
                rec = get_window_record(
                    state, province, task["start"], task["end"], PHASE_DISCOVERY
                )
                if rec and rec.get("status") == "COMPLETE" and not force:
                    continue
                if rec and rec.get("status") == "SPLIT" and not force:
                    children = prioritize_pending_windows(
                        state, province, split_date_window(task), PHASE_DISCOVERY
                    )
                    pending.extend({**task, **child} for child in reversed(children))
                    summary["windows_total"] += len(children)
                    continue
                rec = new_window_record(
                    province, task["start"], task["end"], PHASE_DISCOVERY
                )
                rec["transport"] = "authenticated_same_origin_fetch"
                rec["endpoint_path"] = HISTORY_ENDPOINT_PATH
                upsert_window_record(state, rec)
                request_id = rec["key"]
                records[request_id] = (task, rec)
                jobs.append({
                    "id": request_id,
                    "payload": history_request_payload(province_codes[province], task),
                    "canSplit": bool(split_date_window(task)),
                })
            if not jobs:
                continue
            save_bulk_state(state, self.store_dir)
            try:
                fetched = self._fetch_history_windows(page, jobs, concurrency)
            except Exception as exc:
                for _, rec in records.values():
                    mark_failure(rec, "FAST_REQUEST_FAILED", type(exc).__name__)
                continue
            fetched_by_id = {
                str(item.get("id")): item
                for item in (fetched or [])
                if isinstance(item, dict) and item.get("id") is not None
            }

            for request_id, (task, rec) in records.items():
                summary["windows_processed"] += 1
                item = fetched_by_id.get(request_id)
                responses = item.get("responses") if isinstance(item, dict) else None
                if not isinstance(responses, list) or not responses:
                    mark_failure(rec, "FAST_REQUEST_FAILED", "missing endpoint response")
                    continue
                summary["request_count"] += sum(
                    int(response.get("attempts") or 1)
                    for response in responses if isinstance(response, dict)
                )
                first = responses[0] if isinstance(responses[0], dict) else {}
                if not first.get("ok"):
                    expired = int(first.get("status") or 0) in (401, 403)
                    if not expired and first.get("body"):
                        expired = detect_session_expiration(
                            first["body"], first.get("url")
                        )["expired"]
                    status = "SESSION_EXPIRED" if expired else "FAST_REQUEST_FAILED"
                    mark_failure(
                        rec, status,
                        f"HTTP {first.get('status') or 0}: {first.get('error') or 'request failed'}",
                    )
                    if expired:
                        summary["stopped_reason"] = "SESSION_EXPIRED"
                        summary["session_interruptions"] += 1
                    continue
                try:
                    first_meta = parse_history_response(first.get("body"), expected_page=1)
                except ValueError as exc:
                    expired = detect_session_expiration(
                        first.get("body"), first.get("url")
                    )["expired"]
                    status = "SESSION_EXPIRED" if expired else "FAST_RESPONSE_INVALID"
                    mark_failure(rec, status, str(exc))
                    if expired:
                        summary["stopped_reason"] = "SESSION_EXPIRED"
                        summary["session_interruptions"] += 1
                    continue

                rec["result_count"] = first_meta["result_count"]
                rec["total_pages"] = first_meta["total_pages"]
                rec["per_page"] = first_meta["per_page"]
                meta = {
                    "result_count": first_meta["result_count"],
                    "total_pages": first_meta["total_pages"],
                    "per_page": first_meta["per_page"],
                }
                valid_pages = list(range(1, first_meta["total_pages"] + 1))
                if should_split_result_window(
                    task, meta, valid_pages, len(first_meta["rows"])
                ) and split_date_window(task):
                    rec["status"] = "SPLIT"
                    rec["pages_completed"] = []
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)
                    children = split_date_window(task)
                    pending.extend({**task, **child} for child in reversed(children))
                    summary["windows_total"] += len(children)
                    summary["dense_windows_split"] += 1
                    continue

                all_rows = []
                successful_pages = []
                page_failure = None
                expected_pages = max(1, first_meta["total_pages"])
                if first_meta["result_count"] >= RESULT_SPLIT_THRESHOLD:
                    expected_pages = min(
                        expected_pages,
                        (RESULT_SPLIT_THRESHOLD + first_meta["per_page"] - 1)
                        // first_meta["per_page"],
                    )
                for expected_page, response in enumerate(responses, start=1):
                    if not isinstance(response, dict) or not response.get("ok"):
                        page_failure = "PAGINATION_INCOMPLETE"
                        break
                    try:
                        page_meta = parse_history_response(
                            response.get("body"), expected_page=expected_page
                        )
                    except ValueError:
                        page_failure = "FAST_RESPONSE_INVALID"
                        break
                    if (
                        page_meta["result_count"] not in (None, first_meta["result_count"])
                        or page_meta["per_page"] != first_meta["per_page"]
                    ):
                        page_failure = "RESULT_COUNT_MISMATCH"
                        break
                    all_rows.extend(page_meta["rows"])
                    successful_pages.append(expected_page)
                if len(successful_pages) != expected_pages:
                    page_failure = page_failure or "PAGINATION_INCOMPLETE"
                try:
                    cards = history_rows_to_cards(all_rows)
                except ValueError as exc:
                    mark_failure(rec, "FAST_RESPONSE_INVALID", str(exc))
                    continue

                unique_cards = []
                identities = set()
                for card in cards:
                    identity = (
                        card["kayit_no"], card["file_id"],
                        _fold(card["institution_text"]),
                    )
                    if identity in identities:
                        continue
                    identities.add(identity)
                    unique_cards.append(card)
                rec["result_cards_inspected"] = len(unique_cards)
                summary["result_cards_inspected"] += len(unique_cards)
                sold_cards = [card for card in unique_cards if card["sold"]]
                outcomes = persist_discovered_cards(
                    sold_cards,
                    store_dir=self.store_dir,
                    source_page_ref=source_page_ref,
                    province_label=task["province"],
                    window=task,
                )
                rec["sold_discovered"] = len(outcomes)
                summary["sold_discovered"] += len(outcomes)
                summary["records_processed"] += sum(
                    outcome.get("outcome") == "discovered" for outcome in outcomes
                )
                rec["pages_completed"] = (
                    successful_pages if first_meta["result_count"] else []
                )

                if page_failure:
                    rec["status"] = page_failure
                    rec["last_fast_error"] = "endpoint pagination could not be fully reconciled"
                    summary["discovery_windows_incomplete"] += 1
                    summary["result_refresh_failures"] += 1
                elif first_meta["result_count"] >= RESULT_SPLIT_THRESHOLD:
                    rec["status"] = "SATURATED_UNRESOLVED"
                    rec["pages_completed"] = []
                    summary["saturated_windows_unresolved"] += 1
                elif len(identities) != first_meta["result_count"]:
                    rec["status"] = "RESULT_COUNT_MISMATCH"
                    rec["parsed_unique_count"] = len(identities)
                    rec["pages_completed"] = []
                    summary["discovery_windows_incomplete"] += 1
                    summary["result_refresh_failures"] += 1
                else:
                    rec["status"] = "COMPLETE"
                    rec.pop("last_fast_error", None)
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
        return summary

    @_exclusive_bulk_run
    def run_fast_discovery(
        self,
        tasks: list[dict],
        concurrency: int = 8,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        from .collect import BrowserCollector

        if concurrency < 1 or concurrency > 16:
            raise ValueError("concurrency must be between 1 and 16")
        normalized_tasks = []
        seen = set()
        for raw_task in tasks:
            province = canonicalize_province_label(raw_task.get("province"))
            if not province:
                raise ValueError(f"unknown UYAP province: {raw_task.get('province')!r}")
            task = {
                "phase": PHASE_DISCOVERY,
                "province": province,
                "start": _parse_iso(raw_task["start"]).isoformat(),
                "end": _parse_iso(raw_task["end"]).isoformat(),
            }
            task_windows = generate_date_windows(task["start"], task["end"])
            if len(task_windows) != 1 or task_windows[0] != {
                "start": task["start"], "end": task["end"]
            }:
                raise ValueError("fast discovery task must fit one UYAP date window")
            key = window_key(province, task["start"], task["end"], PHASE_DISCOVERY)
            if key not in seen:
                seen.add(key)
                normalized_tasks.append(task)
        summary = self._fast_summary(normalized_tasks, concurrency)
        if dry_run or not normalized_tasks:
            summary["dry_run"] = bool(dry_run)
            summary["planned_windows"] = normalized_tasks
            summary["stopped_reason"] = "dry_run" if dry_run else None
            return summary

        started_at = time.monotonic()
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
            page_html = page.content()
            expiration = detect_session_expiration(page_html, page.url)
            if expiration["expired"]:
                summary["stopped_reason"] = "SESSION_EXPIRED"
                summary["session_interruptions"] = 1
                summary["discovery_windows_incomplete"] = len(normalized_tasks)
            else:
                province_codes = self._history_province_codes(page)
                missing = sorted({task["province"] for task in normalized_tasks} - province_codes.keys())
                if missing:
                    self._select_category_tasinmaz(page)
                    page.wait_for_timeout(200)
                    province_codes = self._history_province_codes(page)
                    missing = sorted({task["province"] for task in normalized_tasks} - province_codes.keys())
                if missing:
                    summary["stopped_reason"] = "SELECTION_UNVERIFIED"
                    summary["discovery_windows_incomplete"] = len(normalized_tasks)
                else:
                    summary = self._run_fast_discovery_tasks(
                        page, normalized_tasks, province_codes, concurrency, force=force
                    )

        elapsed = max(time.monotonic() - started_at, 1e-9)
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["windows_per_minute"] = round(
            summary["windows_processed"] * 60.0 / elapsed, 2
        )
        summary["requests_per_minute"] = round(
            summary["request_count"] * 60.0 / elapsed, 2
        )
        summary["acquisitions_per_minute"] = 0.0
        self._print_summary(summary)
        return summary

    # -- Canlı koşu (pragma: canlı tarayıcı/DOM gerektirir; ağ testlerinde çalışmaz) ------- #
    @_exclusive_bulk_run
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
        kayit_nos: set[str] | None = None,
        target_candidate_ids_by_ref: dict[str, set[str]] | None = None,
        newest_first: bool = False,
    ) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        from .collect import BrowserCollector

        if max_records is not None and max_records < 1:
            raise ValueError("max_records must be >= 1")
        if max_windows is not None and max_windows < 1:
            raise ValueError("max_windows must be >= 1")
        if kayit_nos is not None and not kayit_nos:
            raise ValueError("targeted acquisition requires at least one KAYIT NO")
        target_refs = (
            {str(value) for value in kayit_nos}
            if kayit_nos is not None else None
        )
        if kayit_no:
            target_refs = target_refs or set()
            target_refs.add(str(kayit_no))

        windows = generate_date_windows(date_from, date_to)
        if newest_first:
            windows.reverse()
        phase = PHASE_DISCOVERY if discovery_only else PHASE_ACQUISITION
        started_at = time.monotonic()
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
            "phase": phase,
            "newest_first": bool(newest_first),
            "dense_windows_split": 0,
            "saturated_windows_unresolved": 0,
            "result_refresh_failures": 0,
            "acquisition_windows_incomplete": 0,
            "discovery_windows_incomplete": 0,
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

            def _acquire(file_id, institution, record_ref=None):
                return collector._collect_documents(page, context, file_id, institution,
                                                    native_only=True, target_record_ref=record_ref)

            state = load_bulk_state(self.store_dir)
            pending_windows = (
                list(windows)
                if force
                else prioritize_pending_windows(state, province, list(windows), phase)
            )
            while pending_windows:
                if max_windows and summary["windows_processed"] >= max_windows:
                    if phase == PHASE_DISCOVERY:
                        summary["stopped_reason"] = "DISCOVERY_INCOMPLETE"
                        summary["discovery_windows_incomplete"] = len(pending_windows)
                    else:
                        summary["stopped_reason"] = "ACQUISITION_INCOMPLETE"
                        summary["acquisition_windows_incomplete"] = len(pending_windows)
                    break
                w = pending_windows.pop(0)
                rec = get_window_record(state, province, w["start"], w["end"], phase)
                targeted_retry = phase == PHASE_ACQUISITION and target_refs is not None
                if rec and rec.get("status") == "COMPLETE" and not force:
                    if targeted_retry:
                        rec = new_window_record(province, w["start"], w["end"], phase)
                        upsert_window_record(state, rec)
                        save_bulk_state(state, self.store_dir)
                    else:
                        self._print(f"[UYAP BULK] pencere atlandı (tamamlanmış; yeniden çalıştırmak için --force): {w['start']}→{w['end']}")
                        continue
                if rec and rec.get("status") == "SPLIT" and targeted_retry and not force:
                    retry_round = int(rec.get("retry_round") or 0)
                    rec = new_window_record(province, w["start"], w["end"], phase)
                    rec["retry_round"] = retry_round
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)
                if rec and rec.get("status") == "SPLIT" and not force:
                    if not targeted_retry and window_tree_complete(state, province, w, phase):
                        rec["status"] = "COMPLETE"
                        upsert_window_record(state, rec)
                        save_bulk_state(state, self.store_dir)
                        continue
                    children = split_date_window(w)
                    if newest_first:
                        children.reverse()
                    if not force:
                        children = prioritize_pending_windows(
                            state, province, children, phase
                        )
                    pending_windows = children + pending_windows
                    summary["windows_total"] += max(0, len(children) - 1)
                    continue
                if rec is None or force:
                    preserved_failure_state = {
                        field: list(rec.get(field) or [])
                        for field in (
                            "unresolved_untargeted_candidate_ids",
                            "attempted_untargeted_candidate_ids",
                            "attempted_candidate_ids",
                            "attempted_record_refs",
                            "pending_record_refs",
                        )
                        if rec and rec.get(field)
                    }
                    rec = new_window_record(province, w["start"], w["end"], phase)
                    rec.update(preserved_failure_state)
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)

                stop = self._run_window(page, context, _acquire, province, w, rec, state,
                                        summary, max_records, discovery_only, acquired_total,
                                        target_kayit_nos=target_refs,
                                        target_candidate_ids_by_ref=target_candidate_ids_by_ref,
                                        force=force)
                acquired_total = summary["acquisitions_completed"] + summary["sold_skipped_known"]
                summary["windows_processed"] += 1
                if stop == "SPLIT_WINDOW":
                    children = split_date_window(w)
                    if newest_first:
                        children.reverse()
                    if not force:
                        children = prioritize_pending_windows(
                            state, province, children, phase
                        )
                    pending_windows = children + pending_windows
                    summary["windows_total"] += len(children)
                    summary["dense_windows_split"] += 1
                    continue
                if stop == "SATURATED_UNRESOLVED":
                    summary["saturated_windows_unresolved"] += 1
                    continue
                if stop == "ACQUISITION_INCOMPLETE":
                    summary["acquisition_windows_incomplete"] += 1
                    summary["stopped_reason"] = "ACQUISITION_INCOMPLETE"
                    continue
                if stop in {
                    "RESULT_REFRESH_UNVERIFIED",
                    "RESULT_STATE_UNCONFIRMED",
                    "DATE_INPUT_UNVERIFIED",
                    "ARA_BUTTON_NOT_LOCATED",
                    "PAGINATION_INCOMPLETE",
                    "RESULT_COUNT_MISMATCH",
                }:
                    summary["stopped_reason"] = stop
                    summary["result_refresh_failures"] += 1
                    break
                if stop == "SESSION_EXPIRED":
                    summary["stopped_reason"] = "SESSION_EXPIRED"
                    summary["session_interruptions"] += 1
                    break
                if stop == "SELECTION_UNVERIFIED":
                    summary["stopped_reason"] = "SELECTION_UNVERIFIED"
                    break
                if stop == "MAX_RECORDS":
                    if phase == PHASE_DISCOVERY:
                        summary["discovery_windows_incomplete"] = 1 + len(pending_windows)
                        summary["stopped_reason"] = "DISCOVERY_INCOMPLETE"
                    else:
                        if rec.get("status") == "ACQUISITION_INCOMPLETE":
                            summary["acquisition_windows_incomplete"] += 1
                        summary["stopped_reason"] = "max_records"
                    break
            self._live_active = False
            watchdog_stop.set()

        elapsed = max(time.monotonic() - started_at, 1e-9)
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["windows_per_minute"] = round(summary["windows_processed"] * 60.0 / elapsed, 2)
        summary["acquisitions_per_minute"] = round(
            summary["acquisitions_completed"] * 60.0 / elapsed, 2
        )
        for c in store.load_candidates(self.store_dir):
            dec = (c.get("audit") or {}).get("decision")
            if dec:
                summary["audit_decisions"][dec] = summary["audit_decisions"].get(dec, 0) + 1
        self._print_summary(summary)
        return summary

    def _run_window(self, page, context, acquire, province, w, rec, state, summary,
                    max_records, discovery_only, acquired_total, target_kayit_no=None,
                    target_kayit_nos: set[str] | None = None,
                    target_candidate_ids_by_ref: dict[str, set[str]] | None = None,
                    force: bool = False) -> str | None:  # pragma: no cover
        pending_target_refs = (
            set(str(value) for value in target_kayit_nos)
            if target_kayit_nos is not None else None
        )
        pending_candidate_ids_by_ref = {
            str(record_ref): {str(candidate_id) for candidate_id in candidate_ids}
            for record_ref, candidate_ids in (target_candidate_ids_by_ref or {}).items()
        }
        attempted_target_refs = set(rec.get("attempted_record_refs") or [])
        all_target_candidate_ids = {
            candidate_id
            for candidate_ids in pending_candidate_ids_by_ref.values()
            for candidate_id in candidate_ids
        }
        attempted_candidate_ids = set(rec.get("attempted_candidate_ids") or [])
        attempted_untargeted_candidate_ids = set(
            rec.get("attempted_untargeted_candidate_ids") or []
        )
        unresolved_untargeted_candidate_ids = set(
            rec.get("unresolved_untargeted_candidate_ids") or []
        )
        candidate_cursor = bool(all_target_candidate_ids)
        all_attempted = (
            all_target_candidate_ids.issubset(attempted_candidate_ids)
            if candidate_cursor
            else bool(pending_target_refs and pending_target_refs.issubset(attempted_target_refs))
        )
        if all_attempted:
            attempted_target_refs.clear()
            attempted_candidate_ids.clear()
            rec["attempted_record_refs"] = []
            rec["attempted_candidate_ids"] = []
            rec["retry_round"] = int(rec.get("retry_round") or 0) + 1
        start_ui = format_uyap_ui_date(w["start"])
        end_ui = format_uyap_ui_date(w["end"])
        self._print(f"[UYAP BULK] pencere {w['start']}→{w['end']} · {CATEGORY_TASINMAZ} · {province}")
        self._beat(f"pencere {w['start']}→{w['end']}: form dolduruluyor (kategori/il/tarih)")

        self._dismiss_notices(page)
        cat_ok = self._select_category_tasinmaz(page)
        prov_ok = self._select_province(page, province)
        if not cat_ok or not prov_ok:
            rec["status"] = (
                "CATEGORY_SELECTION_UNVERIFIED" if not cat_ok
                else "PROVINCE_SELECTION_UNVERIFIED"
            )
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                f"  kategori/il seçimi doğrulanamadı (kategori={cat_ok} il={prov_ok}) — "
                "arama çalıştırılmadı"
            )
            return "SELECTION_UNVERIFIED"
        if not self._set_and_verify_dates(page, start_ui, end_ui):
            rec["status"] = "DATE_INPUT_UNVERIFIED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  tarih girişi doğrulanamadı (kategori={cat_ok} il={prov_ok}) — pencere atlandı (uydurma yok).")
            return "DATE_INPUT_UNVERIFIED"
        before_search_html = page.content()
        self._beat("ARA sonrası sonuç durumu bekleniyor")
        ara_clicked, result_html, refresh_verified = self._click_and_wait_result(
            page,
            result_state_signature(before_search_html),
            ((start_ui, w["start"]), (end_ui, w["end"])),
        )
        if not ara_clicked:
            rec["status"] = "ARA_BUTTON_NOT_LOCATED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  ARA (arama) kontrolü bulunamadı — pencere atlandı. `--diagnose` çıktısındaki aksiyon adaylarını paylaşın.")
            return "ARA_BUTTON_NOT_LOCATED"
        if not refresh_verified:
            rec["status"] = "RESULT_REFRESH_UNVERIFIED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  ARA isteği/sonuç yenilenmesi doğrulanamadı — pencere tamamlanmadı")
            return "RESULT_REFRESH_UNVERIFIED"

        exp = detect_session_expiration(result_html, page.url)
        if exp["expired"]:
            rec["status"] = "SESSION_EXPIRED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  OTURUM SONA ERDİ ({exp['reason']}). Durum kaydedildi; yeniden giriş sonrası devam edilebilir.")
            return "SESSION_EXPIRED"
        rec["pages_completed"] = []
        rec["result_cards_inspected"] = 0
        rec["sold_discovered"] = 0
        rec["sold_skipped_known"] = 0
        rec["acquisitions_complete"] = 0
        rec["acquisitions_failed"] = 0
        rec.pop("parsed_unique_count", None)
        rec["page_checkpoint_reset_reason"] = "fresh_search_revalidation"
        result_cards = parse_result_cards(result_html)
        if zero_results(result_html) and not result_cards:
            rec["result_count"] = 0
            rec["total_pages"] = None
            if pending_target_refs:
                attempted_target_refs.update(pending_target_refs)
                rec["attempted_record_refs"] = sorted(attempted_target_refs)
                mark_acquisition_incomplete(rec, pending_target_refs)
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
                self._print("  0 sonuç; hedef edinim tamamlanmadı ve tekrar denenebilir kaldı.")
                return "ACQUISITION_INCOMPLETE"
            if unresolved_untargeted_candidate_ids:
                rec["status"] = "ACQUISITION_INCOMPLETE"
                rec["pages_completed"] = []
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
                self._print(
                    "  0 sonuç; önceki edinim hataları çözülmediği için checkpoint COMPLETE yapılmadı"
                )
                return "ACQUISITION_INCOMPLETE"
            rec["status"] = "COMPLETE"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  0 sonuç. Pencere tamamlandı.")
            return None

        meta = extract_result_metadata(result_html)
        cards_present = bool(result_cards)
        if meta.get("result_count") is None and not cards_present:
            rec["status"] = "RESULT_STATE_UNCONFIRMED"
            rec["result_count"] = None
            rec["total_pages"] = None
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  sonuç durumu doğrulanamadı: ARA sonrası numaralı 'N sonuç bulundu' ya da sonuç "
                        "kartı görünmedi (zaman aşımı / ARA tetiklenmedi / sonuçlar farklı yükleniyor olabilir). "
                        "Pencere COMPLETE İŞARETLENMEDİ — `--diagnose-results` ile sonuç yapısını paylaşın.")
            return "RESULT_STATE_UNCONFIRMED"

        rec["result_count"] = meta.get("result_count")
        rec["total_pages"] = meta.get("total_pages")
        valid_pages = self._valid_pages(page, meta)
        if should_split_result_window(w, meta, valid_pages) and pending_target_refs is None:
            rec["status"] = "SPLIT"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                f"  yoğun pencere ({meta.get('result_count')} sonuç / {meta.get('total_pages')} sayfa) "
                "→ iki alt pencereye bölünüyor"
            )
            return "SPLIT_WINDOW"
        upsert_window_record(state, rec)
        save_bulk_state(state, self.store_dir)

        self._print(f"  sonuç={meta.get('result_count')} · geçerli sayfalar={valid_pages} "
                    f"(page 0 hariç) · tamamlanan={rec.get('pages_completed')}")

        processed_ids: set = set()
        pages_failed: list = []
        acquisition_failed_pages: list = []
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
                cid = (
                    str(c.get("kayit_no") or ""),
                    str(c.get("file_id") or ""),
                    _fold(c.get("institution_text") or province or ""),
                )
                if cid in processed_ids:
                    continue
                processed_ids.add(cid)
                new_cards.append(c)
            rec["result_cards_inspected"] += len(new_cards)
            summary["result_cards_inspected"] += len(new_cards)
            sold = [c for c in new_cards if c["sold"]]
            self._print(f"  sayfa {pnum}/{valid_pages[-1] if valid_pages else pnum} · kart={len(new_cards)} · Satıldı={len(sold)}")

            def candidate_identity(card):
                if not target_candidate_ids_by_ref:
                    return None
                record_ref = str(card.get("kayit_no") or "")
                expected_ids = target_candidate_ids_by_ref.get(record_ref)
                if not expected_ids:
                    return None
                file_id = card.get("file_id")
                if not file_id:
                    return None
                institution = card.get("institution_text") or province or "UYAP e-Satış"
                candidate_id = deterministic_candidate_id(institution, file_id, record_ref)
                return candidate_id if candidate_id in expected_ids else None

            def candidate_identity_matches(card):
                return not target_candidate_ids_by_ref or candidate_identity(card) is not None

            def mark_candidate_complete(card, result):
                if pending_target_refs is None:
                    return
                record_ref = str(card.get("kayit_no") or "")
                if not target_candidate_ids_by_ref:
                    pending_target_refs.discard(record_ref)
                    return
                matched_candidate_id = candidate_identity(card)
                if not matched_candidate_id or str(result.get("candidate_id") or "") != matched_candidate_id:
                    return
                pending_ids = pending_candidate_ids_by_ref.get(record_ref, set())
                pending_ids.discard(matched_candidate_id)
                if not pending_ids:
                    pending_target_refs.discard(record_ref)

            selected_sold = [
                card for card in sold
                if (not target_kayit_no or (card.get("kayit_no") or "") == target_kayit_no)
                and (not target_kayit_nos or str(card.get("kayit_no") or "") in target_kayit_nos)
                and (
                    pending_target_refs is None
                    or (
                        candidate_identity(card) not in attempted_candidate_ids
                        if candidate_cursor
                        else str(card.get("kayit_no") or "") not in attempted_target_refs
                    )
                )
                and candidate_identity_matches(card)
                and (
                    pending_target_refs is not None
                    or deterministic_candidate_id(
                        card.get("institution_text") or province or "UYAP e-Satış",
                        card.get("file_id"),
                        card.get("kayit_no"),
                    ) not in attempted_untargeted_candidate_ids
                )
            ]
            if discovery_only:
                remaining = (
                    max_records - summary["records_processed"]
                    if max_records is not None else None
                )
                existing_ids = {
                    str(candidate.get("candidate_id") or "")
                    for candidate in store.load_candidates(self.store_dir)
                }
                discovery_batch = []
                new_in_batch = 0
                page_limit_reached = False
                for card in selected_sold:
                    file_id = card.get("file_id")
                    if not file_id:
                        discovery_batch.append(card)
                        continue
                    institution = card.get("institution_text") or province or "UYAP e-Satış"
                    candidate_id = deterministic_candidate_id(
                        institution, file_id, card.get("kayit_no")
                    )
                    is_new = candidate_id not in existing_ids
                    if is_new and remaining is not None and new_in_batch >= remaining:
                        page_limit_reached = True
                        break
                    discovery_batch.append(card)
                    if is_new:
                        existing_ids.add(candidate_id)
                        new_in_batch += 1
                outcomes = persist_discovered_cards(
                    discovery_batch,
                    store_dir=self.store_dir,
                    source_page_ref=self._safe_ref(page.url),
                    province_label=province,
                    window=w,
                )
                for res in outcomes:
                    if res["outcome"] == "discovered":
                        summary["records_processed"] += 1
                    rec["sold_discovered"] += 1
                    summary["sold_discovered"] += 1
                    self._report_auction(res)
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
                if page_limit_reached:
                    rec["status"] = "DISCOVERY_INCOMPLETE"
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)
                    return "MAX_RECORDS"
            else:
                page_acquisition_failed = False
                for card in selected_sold:
                    if max_records is not None and summary["records_processed"] >= max_records:
                        if pending_target_refs:
                            mark_acquisition_incomplete(rec, pending_target_refs)
                        else:
                            rec["status"] = "ACQUISITION_INCOMPLETE"
                        upsert_window_record(state, rec)
                        save_bulk_state(state, self.store_dir)
                        return "MAX_RECORDS"
                    self._beat(f"KAYIT NO {card.get('kayit_no')}: belge ediniliyor (native)")
                    res = process_sold_auction(
                        card, acquire_documents=acquire, store_dir=self.store_dir,
                        genuine_path=self.genuine_path, discovery_only=False,
                        source_page_ref=self._safe_ref(page.url), province_label=province, window=w,
                        force=force,
                    )
                    if res["outcome"] != "skipped_already_acquired":
                        summary["records_processed"] += 1
                    if pending_target_refs is not None:
                        matched_candidate_id = candidate_identity(card)
                        if candidate_cursor and matched_candidate_id:
                            attempted_candidate_ids.add(matched_candidate_id)
                            rec["attempted_candidate_ids"] = sorted(attempted_candidate_ids)
                        else:
                            attempted_target_refs.add(str(card.get("kayit_no") or ""))
                        rec["attempted_record_refs"] = sorted(attempted_target_refs)
                    rec["sold_discovered"] += 1
                    summary["sold_discovered"] += 1
                    self._report_auction(res)
                    if res["outcome"] == "skipped_already_acquired":
                        rec["sold_skipped_known"] += 1
                        summary["sold_skipped_known"] += 1
                        mark_candidate_complete(card, res)
                        if res.get("candidate_id"):
                            unresolved_untargeted_candidate_ids.discard(res["candidate_id"])
                    elif res["outcome"] == "acquired":
                        rec["acquisitions_complete"] += 1
                        summary["acquisitions_completed"] += 1
                        mark_candidate_complete(card, res)
                        if res.get("candidate_id"):
                            unresolved_untargeted_candidate_ids.discard(res["candidate_id"])
                    elif res["outcome"] == "acquisition_failed":
                        rec["acquisitions_failed"] += 1
                        summary["acquisition_failures"] += 1
                        page_acquisition_failed = True
                        if pending_target_refs is None and res.get("candidate_id"):
                            unresolved_untargeted_candidate_ids.add(res["candidate_id"])
                            rec["unresolved_untargeted_candidate_ids"] = sorted(
                                unresolved_untargeted_candidate_ids
                            )
                            attempted_untargeted_candidate_ids.add(res["candidate_id"])
                            rec["attempted_untargeted_candidate_ids"] = sorted(
                                attempted_untargeted_candidate_ids
                            )
                    rec["unresolved_untargeted_candidate_ids"] = sorted(
                        unresolved_untargeted_candidate_ids
                    )
                    self._close_modal(page)
                    page.wait_for_timeout(self.request_delay_ms)
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)
                if page_acquisition_failed and pending_target_refs is None:
                    acquisition_failed_pages.append(pnum)
                    rec["status"] = "ACQUISITION_INCOMPLETE"
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)
                    continue

            mark_page_complete(rec, pnum)
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  sayfa {pnum} kontrol noktası kaydedildi.")

        if pages_failed:
            if pending_target_refs:
                mark_acquisition_incomplete(rec, pending_target_refs)
            else:
                rec["status"] = "PAGINATION_INCOMPLETE"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(f"  sayfa(lar) {pages_failed} yüklenemedi — pencere COMPLETE İŞARETLENMEDİ, sonraki koşuda tekrar denenir.")
            return "PAGINATION_INCOMPLETE"
        if acquisition_failed_pages:
            rec["status"] = "ACQUISITION_INCOMPLETE"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                f"  edinimi başarısız sayfa(lar) {acquisition_failed_pages} — "
                "checkpoint COMPLETE yapılmadı"
            )
            return "ACQUISITION_INCOMPLETE"
        if pending_target_refs is None and attempted_untargeted_candidate_ids:
            rec["status"] = "ACQUISITION_INCOMPLETE"
            rec["pages_completed"] = []
            rec["attempted_untargeted_candidate_ids"] = []
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                "  önceki başarısız adaylar ertelendi; sonraki kartlar işlendi ve "
                "failure cursor yeni tur için sıfırlandı"
            )
            return "ACQUISITION_INCOMPLETE"
        if pending_target_refs is None and unresolved_untargeted_candidate_ids:
            rec["status"] = "ACQUISITION_INCOMPLETE"
            rec["pages_completed"] = []
            rec["unresolved_untargeted_candidate_ids"] = sorted(
                unresolved_untargeted_candidate_ids
            )
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  önceki edinim hataları çözülmedi — checkpoint COMPLETE yapılmadı")
            return "ACQUISITION_INCOMPLETE"
        expected_result_count = int(meta.get("result_count") or 0)
        if (
            pending_target_refs is None
            and expected_result_count > 0
            and len(processed_ids) != expected_result_count
        ):
            rec["status"] = "RESULT_COUNT_MISMATCH"
            rec["parsed_unique_count"] = len(processed_ids)
            rec["pages_completed"] = []
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                f"  kaynak sonuç sayısı={expected_result_count}, benzersiz parse edilen={len(processed_ids)} — "
                "checkpoint COMPLETE yapılmadı"
            )
            return "RESULT_COUNT_MISMATCH"
        if pending_target_refs:
            if candidate_cursor:
                for record_ref in pending_target_refs:
                    attempted_candidate_ids.update(
                        pending_candidate_ids_by_ref.get(record_ref, set())
                    )
                rec["attempted_candidate_ids"] = sorted(attempted_candidate_ids)
            else:
                attempted_target_refs.update(pending_target_refs)
            rec["attempted_record_refs"] = sorted(attempted_target_refs)
            mark_acquisition_incomplete(rec, pending_target_refs)
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print(
                f"  hedef edinim tamamlanmadı: {sorted(pending_target_refs)} · sonraki campaign tekrar deneyecek"
            )
            return "ACQUISITION_INCOMPLETE"
        if pending_target_refs is None and result_window_saturated(
            meta, valid_pages, len(processed_ids)
        ):
            children = split_date_window(w)
            rec["pages_completed"] = []
            rec["status"] = "SPLIT" if children else "SATURATED_UNRESOLVED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            if not children:
                self._print(
                    "  tek-gün/ayrıştırılamayan pencere kaynak limitinde doygun — "
                    "görünür kartlar kaydedildi, COMPLETE yazılmadı"
                )
            return "SPLIT_WINDOW" if children else "SATURATED_UNRESOLVED"
        rec.pop("pending_record_refs", None)
        rec.pop("attempted_record_refs", None)
        rec.pop("attempted_candidate_ids", None)
        rec.pop("attempted_untargeted_candidate_ids", None)
        rec.pop("unresolved_untargeted_candidate_ids", None)
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

    def _click_ara(self, page, before_click=None):  # pragma: no cover - canlı DOM
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
                            if before_click is not None:
                                before_click()
                            el.click()
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _start_result_request_probe(
        self, page, filter_groups=()
    ):  # pragma: no cover - canlı DOM
        probe = {
            "armed": False,
            "started": set(),
            "pending": set(),
            "completed": 0,
            "evidence": [],
            "latest_started_sequence": -1,
            "latest_evidence_sequence": -1,
            "latest_evidence": None,
            "next_sequence": 0,
            "request_sequences": {},
            "handlers": [],
        }

        def eligible(request):
            try:
                return (
                    request.resource_type in {"xhr", "fetch"}
                    and request_has_filter_groups(request, filter_groups)
                )
            except Exception:
                return False

        def on_request(request):
            if probe["armed"] and eligible(request):
                request_id = id(request)
                sequence = probe["next_sequence"]
                probe["next_sequence"] += 1
                probe["request_sequences"][request_id] = sequence
                probe["latest_started_sequence"] = sequence
                probe["started"].add(request_id)
                probe["pending"].add(request_id)

        def on_finished(request):
            request_id = id(request)
            if request_id in probe["started"]:
                probe["pending"].discard(request_id)
                probe["completed"] += 1
                successful = False
                payload = ""
                try:
                    response = request.response()
                    successful = response is not None and 200 <= int(response.status) < 400
                    if successful:
                        payload = response.text()
                except Exception:
                    successful = False
                evidence = result_payload_evidence(payload) if successful else None
                sequence = probe["request_sequences"].get(request_id, -1)
                if evidence is not None:
                    probe["evidence"].append(evidence)
                    if sequence >= probe["latest_evidence_sequence"]:
                        probe["latest_evidence_sequence"] = sequence
                        probe["latest_evidence"] = evidence

        def on_failed(request):
            probe["pending"].discard(id(request))

        try:
            for event, handler in (
                ("request", on_request),
                ("requestfinished", on_finished),
                ("requestfailed", on_failed),
            ):
                page.on(event, handler)
                probe["handlers"].append((event, handler))
        except Exception:
            self._stop_result_request_probe(page, probe)
            return None
        return probe

    def _stop_result_request_probe(self, page, probe):  # pragma: no cover - canlı DOM
        if not probe:
            return
        for event, handler in probe.get("handlers", []):
            try:
                page.remove_listener(event, handler)
            except Exception:
                continue

    def _click_and_wait_result(
        self, page, baseline_signature, filter_groups=()
    ):  # pragma: no cover - canlı DOM
        probe = self._start_result_request_probe(page, filter_groups)
        try:
            if probe is None:
                clicked = self._click_ara(page)
            else:
                clicked = self._click_ara(
                    page, before_click=lambda: probe.update(armed=True)
                )
            if not clicked:
                return False, page.content(), False
            if probe is None:
                html, verified = self._wait_result_state(page, baseline_signature)
            else:
                html, verified = self._wait_result_state(
                    page, baseline_signature, request_probe=probe
                )
            return True, html, verified
        finally:
            self._stop_result_request_probe(page, probe)

    def _wait_result_state(
        self, page, baseline_signature=None, request_probe=None
    ):  # pragma: no cover - canlı DOM
        """ARA sonrası sonuçların TAM yüklenmesini bekler: eski state'ten geçiş ve yeni state'in
        stabil kalması gerekir. AJAX kartları kademeli render edebilir; 0 sonuç da ara yükleme
        boşluğuyla karışmaması için art arda doğrulanır. Oturum kaybı hemen döner.
        """
        deadline = self.result_timeout_ms
        step = 400
        waited = 0
        html = ""
        last_sig = None
        stable = 0
        last_zero_state = None
        zero_stable = 0
        baseline_cards = baseline_signature[0] if baseline_signature else ()
        baseline_zero = bool(baseline_signature and baseline_signature[4])
        request_state = None
        request_stable = 0
        while waited < deadline:
            try:
                html = page.content()
            except Exception:
                html = ""
            if detect_session_expiration(html, page.url)["expired"]:
                return html, True
            state_signature = result_state_signature(html)
            sig = result_card_signature(html)
            zero_state = bool(zero_results(html) and not sig)
            current_request_state = None
            if request_probe is not None:
                latest_evidence = request_probe.get("latest_evidence")
                evidence_values = (
                    [latest_evidence]
                    if latest_evidence is not None
                    else list(request_probe.get("evidence") or ())
                )
                current_request_state = (
                    request_probe.get("latest_started_sequence", len(evidence_values)),
                    request_probe.get("latest_evidence_sequence", len(evidence_values)),
                    len(request_probe.get("pending") or ()),
                )
                request_complete = (
                    bool(evidence_values)
                    and current_request_state[0] == current_request_state[1]
                    and current_request_state[2] == 0
                )
                if request_complete and current_request_state == request_state:
                    request_stable += 1
                else:
                    request_stable = 0
                request_state = current_request_state
            request_verified = request_stable >= 2 and any(
                dom_matches_result_evidence(html, evidence)
                for evidence in evidence_values
            ) if request_probe is not None else False
            cards_fresh = baseline_signature is None or sig != baseline_cards
            zero_fresh = baseline_signature is None or not baseline_zero
            result_verified = (
                request_verified
                if request_probe is not None
                else (zero_fresh if zero_state else cards_fresh)
            )
            if zero_state and result_verified:
                if state_signature == last_zero_state:
                    zero_stable += 1
                    if zero_stable >= 3:
                        return html, True
                else:
                    last_zero_state = state_signature
                    zero_stable = 0
            else:
                last_zero_state = None
                zero_stable = 0
            if sig and result_verified and sig == last_sig:
                stable += 1
                if stable >= 2:            # ~0.8s değişmedi → tam yüklendi
                    return html, True
            else:
                last_sig = sig
                stable = 0
            page.wait_for_timeout(step)
            waited += step
        return html, False

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
        expected_total = int(meta.get("total_pages") or 0)
        result_count = int(meta.get("result_count") or 0)
        per_page = int(meta.get("per_page") or DEFAULT_PER_PAGE)
        if result_count:
            expected_total = max(
                expected_total,
                (result_count + per_page - 1) // per_page,
            )
        if pages:
            expected_total = max(expected_total, max(pages))
        return list(range(1, max(expected_total, 1) + 1))

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
        return False

    def _close_modal(self, page):  # pragma: no cover - canlı DOM
        import re as _re

        closed = False
        for getter in (
            lambda: page.get_by_role("button", name=_re.compile("kapat|close", _re.I)),
            lambda: page.locator("[aria-label*=close i], [class*=close], button.close"),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    loc.first.click()
                    closed = True
                    break
            except Exception:
                continue
        if not closed:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        # Modal GERÇEKTEN kapanana kadar bekle (bounded ~2s): sonraki kartın kontrol-tıklamasının
        # YARIM-KAPANAN modalla YARIŞMASINI önler → --delay-ms 0'da bile temiz durum, eksik-çekim yok.
        for _ in range(10):
            try:
                if page.locator(".modal.in, .modal.show").count() == 0:
                    break
            except Exception:
                break
            page.wait_for_timeout(200)
        return closed

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
        if s.get("elapsed_seconds") is not None:
            request_speed = (
                f" · {s['request_count']} istek · {s['requests_per_minute']} istek/dk"
                if s.get("request_count") is not None else ""
            )
            self._print(
                f"  hız: {s['elapsed_seconds']}sn · {s['windows_per_minute']} pencere/dk · "
                f"{s['acquisitions_per_minute']} edinim/dk{request_speed} · "
                f"bölünen yoğun pencere={s['dense_windows_split']} · "
                f"çözülemeyen doygun pencere={s['saturated_windows_unresolved']}"
            )
        self._print("  NOT: admisyon YAPILMADI — ADMISSIBLE adaylar `sold uyap review`/`admit` ile AÇIKÇA alınır.")
