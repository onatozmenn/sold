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
import re
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
    for sel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "li", "tr"):
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
            if not fid or fid in seen:      # dış (en üst) kart önce gelir → ilkini tut
                continue
            seen.add(fid)
            status_raw = _card_status_raw(text)
            cls = classify_card_status(status_raw or "")
            cards.append({
                "kayit_no": _card_kayit_no(text),
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
    cand = discover(
        institution, file_id, listing_ref=kayit_no, status_text=status_raw,
        source_page_ref=source_page_ref, store_dir=store_dir,
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
    existing = store.get_candidate(cand["candidate_id"], store_dir) or cand
    if not should_acquire(existing):
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
    ) -> None:
        self.cdp_endpoint = cdp_endpoint
        self.store_dir = store_dir
        self.genuine_path = genuine_path
        self.request_delay_ms = max(0, int(request_delay_ms))
        self.result_timeout_ms = max(1000, int(result_timeout_ms))

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
            browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
            context = browser.contexts[0]
            page = self._find_gecmis_page(context)
            if page is None:
                raise RuntimeError(
                    "no_gecmis_ilanlar_page: 'Geçmiş İlanlar' sekmesi bulunamadı. "
                    "Önce UYAP e-Satış → İhaleler → Geçmiş İlanlar sayfasını elle açın."
                )

            def _acquire(file_id, institution):
                return collector._collect_documents(page, context, file_id, institution)

            state = load_bulk_state(self.store_dir)
            for w in windows:
                rec = get_window_record(state, province, w["start"], w["end"])
                if rec and rec.get("status") == "COMPLETE" and not resume:
                    self._print(f"[UYAP BULK] pencere atlandı (tamamlanmış): {w['start']}→{w['end']}")
                    continue
                if rec is None:
                    rec = new_window_record(province, w["start"], w["end"])
                    upsert_window_record(state, rec)
                    save_bulk_state(state, self.store_dir)

                stop = self._run_window(page, context, _acquire, province, w, rec, state,
                                        summary, max_records, discovery_only, acquired_total)
                acquired_total = summary["acquisitions_completed"] + summary["sold_skipped_known"]
                summary["windows_processed"] += 1
                if stop == "SESSION_EXPIRED":
                    summary["stopped_reason"] = "SESSION_EXPIRED"
                    summary["session_interruptions"] += 1
                    break
                if stop == "MAX_RECORDS":
                    summary["stopped_reason"] = "max_records"
                    break

        for c in store.load_candidates(self.store_dir):
            dec = (c.get("audit") or {}).get("decision")
            if dec:
                summary["audit_decisions"][dec] = summary["audit_decisions"].get(dec, 0) + 1
        self._print_summary(summary)
        return summary

    def _run_window(self, page, context, acquire, province, w, rec, state, summary,
                    max_records, discovery_only, acquired_total) -> str | None:  # pragma: no cover
        start_ui = format_uyap_ui_date(w["start"])
        end_ui = format_uyap_ui_date(w["end"])
        self._print(f"[UYAP BULK] pencere {w['start']}→{w['end']} · {CATEGORY_TASINMAZ} · {province}")

        self._select_category_tasinmaz(page)
        self._select_province(page, province)
        if not self._set_and_verify_dates(page, start_ui, end_ui):
            rec["status"] = "DATE_INPUT_UNVERIFIED"
            upsert_window_record(state, rec)
            save_bulk_state(state, self.store_dir)
            self._print("  tarih girişi doğrulanamadı — pencere atlandı (uydurma yok).")
            return None
        self._click_ara(page)
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
        rec["result_count"] = meta.get("result_count")
        rec["total_pages"] = meta.get("total_pages")
        upsert_window_record(state, rec)
        save_bulk_state(state, self.store_dir)

        valid_pages = self._valid_pages(page, meta)
        self._print(f"  sonuç={meta.get('result_count')} · geçerli sayfalar={valid_pages} "
                    f"(page 0 hariç) · tamamlanan={rec.get('pages_completed')}")

        for pnum in pages_remaining(rec, valid_pages):
            if not self._goto_page(page, pnum):
                self._print(f"  sayfa {pnum} aktifleştirilemedi/değişmedi — atlanıyor (sessizce geçilmez).")
                continue
            page_html = page.content()
            exp = detect_session_expiration(page_html, page.url)
            if exp["expired"]:
                rec["status"] = "SESSION_EXPIRED"
                upsert_window_record(state, rec)
                save_bulk_state(state, self.store_dir)
                return "SESSION_EXPIRED"

            cards = parse_result_cards(page_html)
            rec["result_cards_inspected"] += len(cards)
            summary["result_cards_inspected"] += len(cards)
            sold = [c for c in cards if c["sold"]]
            self._print(f"  sayfa {pnum}/{valid_pages[-1] if valid_pages else pnum} · kart={len(cards)} · Satıldı={len(sold)}")

            for card in sold:
                if max_records and (summary["acquisitions_completed"] >= max_records):
                    return "MAX_RECORDS"
                res = process_sold_auction(
                    card, acquire_documents=acquire, store_dir=self.store_dir,
                    genuine_path=self.genuine_path, discovery_only=discovery_only,
                    source_page_ref=self._safe_ref(page.url), province_label=province, window=w,
                )
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

        rec["status"] = "COMPLETE"
        upsert_window_record(state, rec)
        save_bulk_state(state, self.store_dir)
        return None

    # -- Canlı DOM yardımcıları (pragma) — gerçek gözlenen UYAP DOM'una uyarlanır ---------- #
    def _find_gecmis_page(self, context):  # pragma: no cover - canlı DOM
        best = None
        for p in context.pages:
            try:
                fold = _fold(p.content())
            except Exception:
                continue
            if "gecmis ilanlar" in fold or ("ihale bitis tarih" in fold and "tasinmaz" in fold):
                return p
            if best is None and ("ilan" in fold or "ihale" in fold):
                best = p
        return best or (context.pages[0] if context.pages else None)

    def _select_category_tasinmaz(self, page):  # pragma: no cover - canlı DOM
        import re as _re

        for getter in (
            lambda: page.get_by_role("radio", name=_re.compile("taşınmaz", _re.I)),
            lambda: page.get_by_label(_re.compile("taşınmaz", _re.I)),
            lambda: page.get_by_text(_re.compile(r"^\s*taşınmaz\s*$", _re.I)),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    loc.first.check() if loc.first.get_attribute("type") == "radio" else loc.first.click()
                    return True
            except Exception:
                continue
        return False

    def _select_province(self, page, province):  # pragma: no cover - canlı DOM
        import re as _re

        for getter in (
            lambda: page.get_by_label(_re.compile(r"^\s*İl\s*$", _re.I)),
            lambda: page.locator("select").filter(has_text=_re.compile("il", _re.I)),
            lambda: page.get_by_role("combobox", name=_re.compile("il", _re.I)),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    try:
                        loc.first.select_option(label=province)
                    except Exception:
                        loc.first.click()
                        page.get_by_role("option", name=_re.compile(province, _re.I)).first.click()
                    return True
            except Exception:
                continue
        return False

    def _set_and_verify_dates(self, page, start_ui, end_ui):  # pragma: no cover - canlı DOM
        import re as _re

        try:
            inputs = page.get_by_role("textbox", name=_re.compile("tarih", _re.I))
            if inputs.count() >= 2:
                start_box, end_box = inputs.nth(0), inputs.nth(1)
            else:
                boxes = page.locator("input[type=text], input:not([type])")
                start_box, end_box = boxes.nth(0), boxes.nth(1)
            start_box.fill(start_ui)
            end_box.fill(end_ui)
            # ARA'dan ÖNCE görünür değerlerin hedef pencereyle EŞLEŞTİĞİNİ doğrula.
            return start_box.input_value().strip() == start_ui and end_box.input_value().strip() == end_ui
        except Exception:
            return False

    def _click_ara(self, page):  # pragma: no cover - canlı DOM
        import re as _re

        for getter in (
            lambda: page.get_by_role("button", name=_re.compile(r"^\s*ara\s*$", _re.I)),
            lambda: page.get_by_text(_re.compile(r"^\s*ara\s*$", _re.I)),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    loc.first.click()
                    return True
            except Exception:
                continue
        return False

    def _wait_result_state(self, page):  # pragma: no cover - canlı DOM
        deadline = self.result_timeout_ms
        step = 500
        waited = 0
        html = ""
        while waited < deadline:
            try:
                html = page.content()
            except Exception:
                html = ""
            fold = _fold(html)
            if ("sonuc bulundu" in fold or "sonuc bulunamadi" in fold
                    or detect_session_expiration(html, page.url)["expired"]):
                return html
            page.wait_for_timeout(step)
            waited += step
        return html

    def _valid_pages(self, page, meta):  # pragma: no cover - canlı DOM
        import re as _re

        labels: list[str] = []
        try:
            loc = page.get_by_role("link", name=_re.compile(r"^\s*\d+\s*$"))
            for i in range(min(loc.count(), 50)):
                labels.append(loc.nth(i).inner_text().strip())
        except Exception:
            pass
        try:
            loc2 = page.get_by_role("button", name=_re.compile(r"^\s*\d+\s*$"))
            for i in range(min(loc2.count(), 50)):
                labels.append(loc2.nth(i).inner_text().strip())
        except Exception:
            pass
        pages = valid_result_pages(labels)
        if not pages and meta.get("total_pages"):
            pages = list(range(1, int(meta["total_pages"]) + 1))  # 0 ASLA dahil edilmez
        return pages or [1]

    def _goto_page(self, page, pnum):  # pragma: no cover - canlı DOM
        import re as _re

        if pnum <= 0:                       # page 0 ASLA tıklanmaz
            return False
        try:
            before = page.content()
        except Exception:
            before = ""
        # Zaten aktifse tekrar tıklama; ilk sayfa çoğu zaman ARA sonrası aktiftir.
        if pnum == 1 and extract_result_metadata(before).get("current_page") in (1, None):
            return True
        for getter in (
            lambda: page.get_by_role("link", name=_re.compile(rf"^\s*{pnum}\s*$")),
            lambda: page.get_by_role("button", name=_re.compile(rf"^\s*{pnum}\s*$")),
        ):
            try:
                loc = getter()
                if loc.count() > 0:
                    loc.first.click()
                    for _ in range(20):
                        page.wait_for_timeout(300)
                        cur = page.content()
                        if extract_result_metadata(cur).get("current_page") == pnum or cur != before:
                            return True
                    return False
            except Exception:
                continue
        return False

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
        print(msg)

    def _print_summary(self, s: dict) -> None:  # pragma: no cover - canlı çıktı
        self._print("\n[UYAP BULK] ÖZET")
        self._print(f"  kapsam: {s['category']} · {s['province']} · {s['date_from']}→{s['date_to']}")
        self._print(f"  pencere: {s['windows_processed']}/{s['windows_total']} işlendi")
        self._print(f"  incelenen kart: {s['result_cards_inspected']} · Satıldı keşfedilen: {s['sold_discovered']}")
        self._print(f"  edinilen: {s['acquisitions_completed']} · zaten bilinen (atlanan): {s['sold_skipped_known']} · edinim hatası: {s['acquisition_failures']}")
        self._print(f"  denetim kararları: {s['audit_decisions']}")
        self._print(f"  oturum kesintisi: {s['session_interruptions']} · durma nedeni: {s['stopped_reason']}")
        self._print("  NOT: admisyon YAPILMADI — ADMISSIBLE adaylar `sold uyap review`/`admit` ile AÇIKÇA alınır.")
