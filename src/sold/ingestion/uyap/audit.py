"""Kural-tabanlı TAMAMLANMIŞ-SATIŞ denetimi + AÇIK ihale-fiyatı semantiği.

Mevcut 7 genuine UYAP gözleminin ve UYAP Evidence Expansion Batch 1'in kurduğu admisyon
semantiğini yeniden kullanır ve BİÇİMSELLEŞTİRİR. Bir aday genuine UYAP tamamlanmış-satış
momentlerine YALNIZCA şu üç koşul sağlandığında admitte edilebilir:

  A. aynı varlık için ekspertiz (Q) denetlenebilir,
  B. AÇIK resmî ihale fiyatı / İhale Bedeli denetlenebilir,
  C. terminal tamamlanmış-satış kanıtı denetlenebilir (Satıldı / Satış İşlemleri Tamamlandı).

FİYAT SEMANTİĞİ (moment tanımı KORUNUR): pay = açık İhale Bedeli; ASLA Ödenmesi Gereken
Bedel / depozito-ayarlı bakiye / hisse-uzlaşı bakiyesi / alacağa-mahsup tutarı / KDV-ayarlı
fiyat DEĞİL. KDV, P ya da Q'yu değiştirmez. ALACAĞA MAHSUBEN, açık İhale Bedeli'ni GEÇERSİZ
KILMAZ. Terminal-olmayan (ör. Birinci Alıcıya Süre Verildi) → EXCLUDED_NON_TERMINAL (no-sale/
negatif sale-probability gözlemine ÇEVRİLMEZ). Bu denetim ADMİSYON DEĞİLdİr.
"""

from __future__ import annotations

from .models import (
    ADMISSIBLE_COMPLETED_SALE,
    EXCLUDED_NON_TERMINAL,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
    PENDING_REVIEW,
    RECONCILIATION_FAILED,
    TERMINAL_SALE_TOKENS,
    AuditResult,
    ExtractedEvidence,
    ReconciliationResult,
)


def audit_candidate(
    evidence: ExtractedEvidence,
    reconciliation: ReconciliationResult | None = None,
) -> AuditResult:
    """Bir adayı denetler ve ``AuditResult`` döndürür (admisyon DEĞİL)."""
    res = AuditResult()
    trace = res.rule_trace
    blocking = res.blocking_reasons
    confirm = res.fields_to_confirm

    terminal = evidence.terminal_status_text in TERMINAL_SALE_TOKENS
    # Terminal-olmayan bilinen durumlar (Satıldı DEĞİL): açıkça non-terminal kabul edilir.
    known_non_terminal = (
        evidence.terminal_status_text is not None and not terminal
    )

    # --- AÇIK ihale fiyatı seçimi (fiyat semantiği koruması) ---
    auction_price = evidence.ihale_bedeli  # yalnızca açık İhale Bedeli
    if auction_price is not None:
        trace.append(f"auction_price = explicit Ihale Bedeli {auction_price:,.0f}")
    if evidence.odenmesi_gereken_bedel is not None:
        trace.append(
            f"Odenmesi Gereken Bedel {evidence.odenmesi_gereken_bedel:,.0f} detected — NOT used as auction price"
        )
    if evidence.deposit_amount is not None:
        trace.append(f"Teminat {evidence.deposit_amount:,.0f} detected — NOT used as auction price")
    if evidence.share_settlement:
        trace.append("ownership/share settlement detected — payable balance NOT used; explicit Ihale Bedeli used")
    if evidence.alacaga_mahsuben:
        trace.append("ALACAGA MAHSUBEN present — no cash amount inferred; explicit Ihale Bedeli used if available")
    if evidence.kdv_rate is not None:
        trace.append(f"KDV {evidence.kdv_rate:g}% present — NOT applied to P or Q (UYAP moment definition preserved)")

    appraisal = evidence.appraisal_value
    res.auction_price = auction_price
    res.appraisal_value = appraisal
    if auction_price is not None and appraisal not in (None, 0):
        res.win_over_appraisal = auction_price / appraisal  # yalnızca RAPOR için (yön admisyonda KULLANILMAZ)

    # --- Karar sırası ---
    if reconciliation is not None and reconciliation.status == "failed":
        res.decision = RECONCILIATION_FAILED
        blocking.append("same-asset reconciliation failed: " + "; ".join(reconciliation.conflicts))
        confirm.append("confirm appraisal and auction price refer to the same asset")
        return res

    if known_non_terminal:
        # 2026/316 Talimat semantiği — terminal tamamlanmış-satış kanıtı YOK.
        res.decision = EXCLUDED_NON_TERMINAL
        blocking.append(
            f"terminal completed-sale evidence missing (status: {evidence.terminal_status_text}); "
            "not admissible, not a no-sale / negative sale-probability observation"
        )
        return res

    if not terminal:
        res.decision = MISSING_TERMINAL_EVIDENCE
        blocking.append("terminal completed-sale evidence missing (no Satildi / Satis Islemleri Tamamlandi)")
        confirm.append("confirm terminal completed-sale status")
        return res

    # Terminal doğrulandı → A ve B koşulları
    if appraisal in (None, 0):
        if len(evidence.appraisal_candidates or []) > 1:
            res.decision = PENDING_REVIEW
            blocking.append(f"two possible appraisal values found: {evidence.appraisal_candidates}")
            confirm.append("confirm the correct appraisal (muhammen/kiymet/takdir) value")
        else:
            res.decision = MISSING_APPRAISAL
            blocking.append("missing auditable appraisal (muhammen bedel / kiymeti / takdir olunan deger)")
            confirm.append("provide auditable appraisal value for the same asset")
        return res

    if auction_price is None:
        if evidence.result_card_amount is not None or evidence.odenmesi_gereken_bedel is not None:
            res.decision = PENDING_REVIEW
            blocking.append(
                "Odenmesi Gereken Bedel / status-card amount present but explicit Ihale Bedeli missing"
            )
            confirm.append("provide the explicit official Ihale Bedeli (do not use Odenmesi Gereken Bedel)")
        else:
            res.decision = MISSING_AUCTION_PRICE
            blocking.append("missing explicit official Ihale Bedeli")
            confirm.append("provide the explicit official Ihale Bedeli")
        return res

    if reconciliation is not None and reconciliation.status == "ambiguous":
        res.decision = PENDING_REVIEW
        blocking.append("same-asset reconciliation ambiguous: " + "; ".join(reconciliation.conflicts))
        confirm.append("confirm appraisal and auction price refer to the same asset")
        return res

    # A + B + C sağlandı → admissible (bu bir DENETİM sonucudur; admisyon değil).
    res.decision = ADMISSIBLE_COMPLETED_SALE
    trace.append("A(appraisal) + B(explicit Ihale Bedeli) + C(terminal completed sale) satisfied")
    if evidence.alacaga_mahsuben:
        trace.append("ALACAGA MAHSUBEN present; explicit Ihale Bedeli exists and is admitted under current price semantics")
    return res
