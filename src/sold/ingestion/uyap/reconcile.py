"""Aynı-varlık mutabakatı (same-asset reconciliation).

Ekspertiz kanıtı (Q) ile ihale-fiyatı kanıtı (P) AYNI TAŞINMAZA mı ait? Resmî tanımlayıcılar
(kurum, dosya no, ada, parsel, blok, bağımsız-bölüm no, kat, tür) karşılaştırılır. Fiyat/adres
BENZER görünüyor diye kayıtlar SESSİZCE birleştirilmez. Resmî adres ve tapu tanımları,
kaynak belgeler açıkça aynı varlığa BAĞLADIĞINDA farklı olabilir. Belirsiz mutabakat İNSAN
İNCELEMESİNE gider (sessizce admisyon YOK).
"""

from __future__ import annotations

import re

from .models import (
    ARTIFACT_APPRAISAL_REPORT,
    ARTIFACT_AUCTION_RESULT,
    ARTIFACT_SALE_NOTICE,
    ARTIFACT_STATUS_CARD,
    ReconciliationResult,
    _ascii_lower,
)

_APPRAISAL_BEARING = (ARTIFACT_APPRAISAL_REPORT, ARTIFACT_SALE_NOTICE)
_AUCTION_BEARING = (ARTIFACT_AUCTION_RESULT, ARTIFACT_STATUS_CARD)


def _artifact_text(artifact: dict) -> str:
    from .extract import _artifact_text as _t

    return _t(artifact)


def _descriptors(text: str) -> dict:
    """Aynı hardened ``asset_descriptors``'ı kullanır (extraction ile mutabakat TUTARLI)."""
    from .extract import asset_descriptors

    return {k: v for k, v in asset_descriptors(_ascii_lower(text)).items() if v}


def reconcile(
    artifacts: list[dict],
    institution: str | None = None,
    file_id: str | None = None,
) -> ReconciliationResult:
    """Ekspertiz-taşıyan ve ihale-taşıyan artifact'ların AYNI VARLIĞA işaret ettiğini doğrular."""
    appr_text = " ".join(_artifact_text(a) for a in artifacts if a.get("artifact_type") in _APPRAISAL_BEARING)
    auc_text = " ".join(_artifact_text(a) for a in artifacts if a.get("artifact_type") in _AUCTION_BEARING)
    appr = _descriptors(appr_text)
    auc = _descriptors(auc_text)

    matched: list[str] = []
    conflicts: list[str] = []
    shared_keys = set(appr) & set(auc)
    for k in sorted(shared_keys):
        if appr[k] == auc[k]:
            matched.append(f"{k}={appr[k]}")
        else:
            conflicts.append(f"{k}: appraisal={appr[k]} vs auction={auc[k]}")

    if conflicts:
        return ReconciliationResult(status="failed", same_asset=False, matched_on=matched, conflicts=conflicts)

    # En az bir güçlü tanımlayıcı (ada+parsel ya da bağımsız-bölüm) her iki tarafta eşleşmeli
    strong = {"ada", "parsel", "section_no"}
    if shared_keys & strong and matched:
        return ReconciliationResult(status="reconciled", same_asset=True, matched_on=matched, conflicts=[])

    # Tanımlayıcılar yalnızca tek grupta ya da yetersiz → belirsiz (insan incelemesi)
    reasons = []
    if not appr:
        reasons.append("no asset descriptors in appraisal-bearing artifacts")
    if not auc:
        reasons.append("no asset descriptors in auction-bearing artifacts")
    if appr and auc and not (shared_keys & strong):
        reasons.append("no shared strong descriptor (ada/parsel/section) across appraisal and auction evidence")
    return ReconciliationResult(
        status="ambiguous",
        same_asset=False,
        matched_on=matched,
        conflicts=reasons or ["insufficient descriptors to confirm same asset"],
    )
