"""UYAP kanıt-ingestion veri modelleri + denetim sözlüğü (DATA-ACQUISITION; yapısal çekirdek DEĞİL).

Bu alt sistem KANIT TOPLAMA içindir: keşif → toplama → çıkarım → aynı-varlık mutabakatı →
kural-tabanlı tamamlanmış-satış denetimi → insan incelemesi → AÇIK admisyon. Yapısal
ekonometrik çekirdeği (moment tanımları, θ, Nash mekanizması, SMM, Θ_A, conditional_on_trade,
sayısal-arama düzeni) DEĞİŞTİRMEZ. Admisyon, MEVCUT genuine UYAP şemasına
(``sold.structural.auction``) yazar ve UYAP P/Q moment tanımını KORUR.

KVKK / gizlilik: yalnızca KİŞİSEL-OLMAYAN kurum / resmî dosya / taşınmaz / kaynak-sağlayıcı
alanları tutulur. Alıcı/borçlu/alacaklı/avukat adları, TC kimlik/telefon/IBAN/hesap numarası
ve taşınmazla ilgisiz kişisel adresler normalize kayıtlara TAŞINMAZ.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Denetim kararı sözlüğü — mevcut EXCLUDED_NON_TERMINAL semantiğini KORUR.
# --------------------------------------------------------------------------- #
ADMISSIBLE_COMPLETED_SALE = "ADMISSIBLE_COMPLETED_SALE"
EXCLUDED_NON_TERMINAL = "EXCLUDED_NON_TERMINAL"
PENDING_REVIEW = "PENDING_REVIEW"
DUPLICATE = "DUPLICATE"
RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
MISSING_APPRAISAL = "MISSING_APPRAISAL"
MISSING_AUCTION_PRICE = "MISSING_AUCTION_PRICE"
MISSING_TERMINAL_EVIDENCE = "MISSING_TERMINAL_EVIDENCE"

AUDIT_DECISIONS = (
    ADMISSIBLE_COMPLETED_SALE,
    EXCLUDED_NON_TERMINAL,
    PENDING_REVIEW,
    DUPLICATE,
    RECONCILIATION_FAILED,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
)

# İş akışı durumları (candidate trail).
STATE_DISCOVERED = "discovered"
STATE_COLLECTED = "collected"
STATE_EXTRACTED = "extracted"
STATE_AUDITED = "audited"
STATE_PENDING_REVIEW = "pending_review"
STATE_ADMITTED = "admitted"
STATE_EXCLUDED = "excluded"

# Kaynak-artifact türleri (analitik veri kümesine GİRMEZ; provenans için tutulur).
ARTIFACT_SALE_NOTICE = "sale_notice"            # Satış İlanı
ARTIFACT_APPRAISAL_REPORT = "appraisal_report"  # Bilirkişi / ekspertiz raporu
ARTIFACT_AUCTION_RESULT = "auction_result"      # (İhale) Artırma Sonuç Tutanağı
ARTIFACT_STATUS_CARD = "status_card"            # sonuç/liste durum kartı (terminal-durum kanıtı)
ARTIFACT_SALE_SPEC = "sale_spec"                # şartname (yalnızca destekleyici kanıt)

# Terminal TAMAMLANMIŞ-satış kanıt jetonları (mevcut 7 genuine gözlemin semantiği).
TERMINAL_SALE_TOKENS = (
    "satis islemleri tamamlandi",
    "satildi",
)
# Terminal-OLMAYAN durumlar (admisyona KAPALI) — 2026/316 Talimat semantiği.
NON_TERMINAL_STATUS_TOKENS = (
    "birinci aliciya sure verildi",
    "ihale sonucu girilmemistir",
    "satis islemleri devam",
)

# Auction price OLARAK ASLA kullanılmayacak etiketler (fiyat semantiği koruması).
NON_AUCTION_PRICE_LABELS = (
    "odenmesi gereken bedel",
    "teminat",
    "alacaga mahsuben",
)

# Ekspertiz/Q etiketleri (payda).
APPRAISAL_LABELS = ("muhammen bedel", "muhammen kiymet", "kiymeti", "takdir olunan deger", "tasinmazin degeri")
# Açık resmî ihale fiyatı etiketleri (pay).
IHALE_LABELS = ("ihale bedeli", "satis tutari")


def _ascii_lower(text: object) -> str:
    """Türkçe-duyarsız karşılaştırma için kaba ASCII-fold + küçük harf (yalnızca eşleştirme)."""
    s = str(text or "")
    table = str.maketrans("İıŞşĞğÜüÖöÇçÂâÎîÛû", "IiSsGgUuOoCcAaIiUu")
    return s.translate(table).lower()


def _looks_mojibake(text: object) -> bool:
    """Bilinen UTF-8-as-Latin-1/cp1252 mojibake öncü karakterleri (Ã/Ä/Å) var mı."""
    return any(lead in str(text or "") for lead in ("Ã", "Ä", "Å"))


def demojibake(text: object) -> str:
    """Bilinen UTF-8-as-Latin-1/cp1252 mojibake'i onarır (İhale/Satış/Bilirkişi/Alacağa/Kıymet...).

    YALNIZCA bilinen imza (Ã/Ä/Å) varsa uygulanır; doğru Türkçe Unicode latin-1/cp1252'ye
    kodlanamaz → olduğu gibi döner. collect.py'deki ``_demojibake`` ile AYNI mantık (paylaşımlı).
    Uzunluk değişebilir → çağıran, folding'i ONARILMIŞ metin üzerinde yapmalı (ofset hizası korunur).
    """
    s = str(text or "")
    if not s or not _looks_mojibake(s):
        return s
    for enc in ("latin-1", "cp1252"):
        try:
            repaired = s.encode(enc, "strict").decode("utf-8", "strict")
        except UnicodeError:
            continue
        if repaired and repaired != s:
            return repaired
    return s


# Türk PARASAL literal: gruplama noktası ve/veya ondalık virgül GEREKİR — çıplak tamsayı (ada/parsel/
# sıra/bölüm no) para DEĞİLdir. Alan-sınırlı (label-bounded) para çıkarımında kullanılır (Fix 10).
MONEY_LITERAL_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{2}")


def parse_tl_amount(text: object) -> float | None:
    """Türk sayı biçimini (ör. ``4.238.000,00 TL``) float'a çevirir (deterministik).

    ``ALACAĞA MAHSUBEN`` gibi sayısal-olmayan uzlaşı ifadeleri ``None`` döndürür
    (nakit tutar UYDURULMAZ). Binlik ``.`` ve ondalık ``,`` desteklenir; ``TL``/boşluk atılır.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if float(text) != 0 else None
    s = str(text).strip()
    if not s:
        return None
    low = _ascii_lower(s)
    # İlk sayısal token ile ``mahsuben`` konumunu karşılaştır: sayı mahsuben'den ÖNCE ise
    # gerçek tutardır (ör. "İhale Bedeli: 5.715.000 ... Ödenmesi Gereken Bedel: ALACAĞA
    # MAHSUBEN"); ``mahsuben`` sayıdan önce/tek başına ise değer YOKtur (nakit uydurulmaz).
    num = re.search(r"[-+]?\d[\d.,]*", s)
    mahs = low.find("mahsuben")
    if num is None or (mahs >= 0 and mahs < num.start()):
        return None
    token = num.group(0).strip(".,")
    # Binlik "." ve ondalık "," → float
    token = token.replace(".", "").replace(",", ".")
    try:
        val = float(token)
    except ValueError:
        return None
    return val if val != 0 else None


def deterministic_candidate_id(institution: object, file_id: object) -> str:
    """Kurum + resmî dosya kimliğinden DETERMİNİSTİK candidate_id (yeniden-keşif idempotent).

    Kişisel veri içermez; yalnızca kamu kurum + dosya kimliği kullanılır.
    """
    key = f"{_ascii_lower(institution).strip()}|{_ascii_lower(file_id).strip()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    fid = re.sub(r"[^A-Za-z0-9]+", "-", str(file_id or "").strip()).strip("-") or "NA"
    return f"UYAP-{fid}-{digest}"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceArtifact:
    """Toplanan bir kaynak belgesi (analitik veri kümesine GİRMEZ; provenans için tutulur)."""

    artifact_type: str
    local_path: str | None = None       # data/ altında (repo'ya COMMİT EDİLMEZ)
    sha256: str | None = None
    captured_at: str = field(default_factory=_utcnow_iso)
    source_ref: str | None = None       # repo-safe kaynak referansı (token/cookie DEĞİL)
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "artifact_type": self.artifact_type,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "captured_at": self.captured_at,
            "source_ref": self.source_ref,
            "note": self.note,
        }


@dataclass
class ExtractedEvidence:
    """Kaynak artifact'lardan DETERMİNİSTİK çıkarılan alanlar (admisyon DEĞİL).

    Her ekonomik alan, çıkarıldığı artifact türüne (provenans) izlenebilir. ML/güven-skoru
    YOKTUR; yalnızca deterministik ayrıştırma + belirsizlik (``ambiguities``) raporlanır.
    """

    institution: str | None = None
    file_id: str | None = None
    # aynı-varlık mutabakatı için taşınmaz tanımlayıcıları
    province: str | None = None
    district: str | None = None
    ada: str | None = None
    parsel: str | None = None
    block: str | None = None
    section_no: str | None = None
    floor: str | None = None
    property_type: str | None = None
    address_text: str | None = None
    tapu_text: str | None = None
    # ekonomik alanlar + provenans
    appraisal_value: float | None = None
    appraisal_source: str | None = None
    ihale_bedeli: float | None = None
    ihale_bedeli_source: str | None = None
    terminal_status_text: str | None = None
    result_card_amount: float | None = None
    result_document_type: str | None = None
    completion_datetime: str | None = None
    odenmesi_gereken_bedel: float | None = None
    deposit_amount: float | None = None
    share_settlement: bool = False
    alacaga_mahsuben: bool = False
    kdv_rate: float | None = None
    # deterministik çıkarım durumu (ML güven skoru DEĞİL)
    extraction_status: str = "deterministic"   # "deterministic" | "ambiguous"
    ambiguities: list = field(default_factory=list)
    appraisal_candidates: list = field(default_factory=list)
    # Fix 10: gizlilik-güvenli ALAN-DÜZEYİ çıkarım provenansı (tam kaynak metin ASLA)
    auction_price_field_label_found: bool = False
    auction_price_candidate_count: int = 0
    auction_price_value_relation_strategy: str | None = None
    appraisal_field_label_found: bool = False
    appraisal_candidate_count: int = 0
    appraisal_value_relation_strategies: list = field(default_factory=list)
    settlement_field_label_found: bool = False
    alacaga_mahsuben_detected: bool = False
    settlement_value_relation_strategy: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ReconciliationResult:
    """Ekspertiz kanıtı ile ihale-fiyat kanıtının AYNI VARLIĞA ait olup olmadığı."""

    status: str = "reconciled"       # "reconciled" | "ambiguous" | "failed"
    same_asset: bool = True
    matched_on: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class AuditResult:
    """Kural-tabanlı tamamlanmış-satış denetimi sonucu (admisyon DEĞİL)."""

    decision: str = PENDING_REVIEW
    auction_price: float | None = None     # SEÇİLEN = açık İhale Bedeli (payable/deposit DEĞİL)
    appraisal_value: float | None = None   # payda = ekspertiz Q
    win_over_appraisal: float | None = None
    blocking_reasons: list = field(default_factory=list)
    fields_to_confirm: list = field(default_factory=list)
    rule_trace: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)
