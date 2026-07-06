"""Tüketici doğrudan-etiket KALİTE KAPISI — ML ÖNCESİ minimal doğrulama.

Üç seviye:
1. YAPISAL RED (``rejected``) — yalnızca yapısal olarak İMKÂNSIZ değerler: fiyat ≤ 0
   veya kapanış tarihi < ilan tarihi. Bunlar sınırda reddedilir (kayıt oluşmaz).
2. İNCELEME BAYRAĞI (``flagged``) — olağandışı ama mümkün örüntüler: aşırı
   closing/asking oranı, şüpheli tarih/süre, duplicate adayı. Kayıt SAKLANIR (orijinal
   öz-beyan değerleri KORUNUR) ama model eğitimine varsayılan GİRMEZ.
3. KABUL (``accepted``) — bayrak yoksa.

KRİTİK: closing > asking (kızgın piyasada olur) VEYA final_asking > initial_asking
(fiyat artışı) TEK BAŞINA RED SEBEBİ DEĞİLDİR — olağandışı oranlar yalnızca BAYRAKLANIR.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from ..labels.registry import QUALITY_ACCEPTED, QUALITY_FLAGGED, QUALITY_REJECTED

# --- İnceleme bayrak adları ---
FLAG_EXTREME_RATIO = "extreme_close_to_ask_ratio"
FLAG_FINAL_ABOVE_INITIAL = "final_ask_above_initial"
FLAG_FAST_CLOSE = "suspiciously_fast_close"
FLAG_LONG_CLOSE = "suspiciously_long_close"
FLAG_FUTURE_DATE = "future_dated"
FLAG_DUPLICATE = "duplicate_candidate"

# --- Bayrak eşikleri (RED değil, yalnızca inceleme) ---
CLOSE_TO_ASK_LOW = 0.5    # closing/final_asking < 0.5 → aşırı (>%50 altında kapandı)
CLOSE_TO_ASK_HIGH = 1.25  # closing/final_asking > 1.25 → aşırı (>%25 ÜSTÜNDE kapandı)
FINAL_OVER_INITIAL = 1.10  # final/initial > 1.10 → ilan fiyatı belirgin ARTTI (olağandışı)
FAST_CLOSE_DAYS = 3       # < 3 gün → şüpheli hızlı
LONG_CLOSE_DAYS = 1095    # > ~3 yıl → şüpheli uzun


def structural_rejection_reason(sale: dict) -> str | None:
    """Yapısal olarak İMKÂNSIZ değer varsa red sebebini döndürür; yoksa None.

    Yalnızca: fiyat ≤ 0 (final/closing zorunlu, initial verilmişse) veya kapanış
    tarihi ilan tarihinden ÖNCE. Olağandışı oranlar (closing>asking vb.) RED DEĞİL.
    """
    for key in ("final_asking_price", "closing_price"):
        v = sale.get(key)
        if v is None or float(v) <= 0:
            return f"{key} yapısal olarak geçersiz (> 0 olmalı)."
    initial = sale.get("initial_asking_price")
    if initial is not None and float(initial) <= 0:
        return "initial_asking_price yapısal olarak geçersiz (> 0 olmalı)."
    listing_date, closing_date = sale.get("listing_date"), sale.get("closing_date")
    if listing_date and closing_date and closing_date < listing_date:
        return "closing_date, listing_date'ten önce olamaz (yapısal olarak imkânsız)."
    return None


def quality_flags(sale: dict, duplicate: bool = False) -> list[str]:
    """İnceleme bayraklarını hesaplar (RED etmez; kayıt saklanır)."""
    flags: list[str] = []
    final_ask = sale.get("final_asking_price")
    closing = sale.get("closing_price")
    if final_ask and closing:
        ratio = float(closing) / float(final_ask)
        if ratio < CLOSE_TO_ASK_LOW or ratio > CLOSE_TO_ASK_HIGH:
            flags.append(FLAG_EXTREME_RATIO)
    initial = sale.get("initial_asking_price")
    if initial and final_ask and float(final_ask) / float(initial) > FINAL_OVER_INITIAL:
        flags.append(FLAG_FINAL_ABOVE_INITIAL)

    days = sale.get("days_to_close")
    if days is not None:
        if days < FAST_CLOSE_DAYS:
            flags.append(FLAG_FAST_CLOSE)
        elif days > LONG_CLOSE_DAYS:
            flags.append(FLAG_LONG_CLOSE)

    today = dt.date.today()
    for key in ("listing_date", "closing_date"):
        d = sale.get(key)
        if d and d > today:
            flags.append(FLAG_FUTURE_DATE)
            break

    if duplicate:
        flags.append(FLAG_DUPLICATE)
    return flags


def assess_quality(sale: dict, duplicate: bool = False) -> tuple[str, list[str]]:
    """(status, flags) döndürür. Yapısal imkânsız → rejected; bayrak varsa flagged; yoksa accepted."""
    reason = structural_rejection_reason(sale)
    if reason:
        return QUALITY_REJECTED, [reason]
    flags = quality_flags(sale, duplicate=duplicate)
    return (QUALITY_FLAGGED if flags else QUALITY_ACCEPTED), flags


# --------------------------------------------------------------------------- #
# Gizlilik-korumalı duplicate-aday parmak izi
# --------------------------------------------------------------------------- #
def _bucket_price(v: object, bucket: float = 50_000.0) -> str:
    """Fiyatı kaba kovaya yuvarlar (yakın-tekrar toleransı); yoksa 'na'."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "na"
    if f <= 0:
        return "na"
    return str(int(round(f / bucket)))


def _bucket_m2(v: object, bucket: float = 5.0) -> str:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "na"
    if f <= 0:
        return "na"
    return str(int(round(f / bucket)))


def fingerprint(sale: dict) -> str:
    """Kanonik NON-personal işlem alanlarından gizlilik-korumalı parmak izi (SHA-256).

    Mülkü veya kişiyi TANIMLAMAZ ve öyle olduğu İDDİA EDİLMEZ. Fiyat/m² kaba kovalara
    yuvarlandığından birebir VEYA yakın-tekrar gönderimler AYNI parmak izini üretir;
    yalnızca duplicate adayını incelemeye işaretlemek için kullanılır (tek yönlü hash,
    kişisel veri içermez).
    """
    parts = [
        str(sale.get("province") or "").strip().lower(),
        str(sale.get("district") or "").strip().lower(),
        str(sale.get("property_type") or "").strip().lower(),
        str(sale.get("room_count") or "").strip().lower(),
        _bucket_m2(sale.get("gross_m2")),
        _bucket_price(sale.get("final_asking_price")),
        _bucket_price(sale.get("closing_price")),
        str(sale.get("listing_date") or ""),
        str(sale.get("closing_date") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
