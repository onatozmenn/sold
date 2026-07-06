"""Tüketici (öz-beyan) satış toplayıcı — sıradan konutta DOĞRUDAN asking→closing edinimi.

Projenin çekirdek çözülmemiş sorunu buradan çözülür: ürünün kendi edinim yolundan
(ev satmış kişilerin formu) provenance-aware doğrudan etiketler toplanır
(domain=consumer, seller_self_reported, ordinary_resale, asking, güven=B) ve bunlar
kalite kapısından geçerek ``asking_to_closing_labels()``'e girer. Kamu (UYAP/KAP/TOKİ)
gözlemleri HARİÇ kalır.

KALİTE KAPISI (ML öncesi): origin (consumer_submission/test_fixture/demo_seed/
manual_import) GERÇEK gönderimi fixture/demo'dan ayırır; quality_status
(accepted/flagged/rejected) yapısal-imkânsızı reddeder, olağandışını bayraklar.
GERÇEK doğrudan-etiket sayısı ancak gerçek bir satıcı gönderiminde artar.
"""

from __future__ import annotations

from ..labels.registry import (
    GENUINE_ORIGIN,
    NON_PRODUCTION_ORIGINS,
    ORIGIN_CONSUMER_SUBMISSION,
    ORIGIN_DEMO_SEED,
    ORIGIN_MANUAL_IMPORT,
    ORIGIN_TEST_FIXTURE,
    ORIGINS,
    QUALITY_ACCEPTED,
    QUALITY_FLAGGED,
    QUALITY_REJECTED,
    QUALITY_STATUSES,
)
from .analytics import (
    MIN_SEGMENT_OBSERVATIONS,
    sale_analytics,
    segment_benchmark,
    segment_key,
)
from .collector import (
    COLLECTED_FIELDS,
    CONSUMER_CONFIDENCE,
    CONSUMER_DOMAIN,
    CONSUMER_LABEL_SOURCE,
    CONSUMER_REFERENCE_TYPE,
    CONSUMER_SALE_MECHANISM,
    FORBIDDEN_PERSONAL_KEYS,
    ConsumerSaleError,
    direct_label_counts,
    load_consumer_sales,
    record_consumer_sale,
    sale_as_dict,
    sale_label_dict,
    validate_consumer_sale,
)
from .quality import (
    FLAG_DUPLICATE,
    FLAG_EXTREME_RATIO,
    FLAG_FAST_CLOSE,
    FLAG_FINAL_ABOVE_INITIAL,
    FLAG_FUTURE_DATE,
    FLAG_LONG_CLOSE,
    assess_quality,
    fingerprint,
    quality_flags,
    structural_rejection_reason,
)

__all__ = [
    # provenance zinciri
    "CONSUMER_DOMAIN",
    "CONSUMER_LABEL_SOURCE",
    "CONSUMER_SALE_MECHANISM",
    "CONSUMER_REFERENCE_TYPE",
    "CONSUMER_CONFIDENCE",
    "COLLECTED_FIELDS",
    "FORBIDDEN_PERSONAL_KEYS",
    # köken + kalite
    "ORIGINS",
    "ORIGIN_CONSUMER_SUBMISSION",
    "ORIGIN_TEST_FIXTURE",
    "ORIGIN_DEMO_SEED",
    "ORIGIN_MANUAL_IMPORT",
    "GENUINE_ORIGIN",
    "NON_PRODUCTION_ORIGINS",
    "QUALITY_STATUSES",
    "QUALITY_ACCEPTED",
    "QUALITY_FLAGGED",
    "QUALITY_REJECTED",
    # toplayıcı
    "ConsumerSaleError",
    "validate_consumer_sale",
    "sale_label_dict",
    "record_consumer_sale",
    "sale_as_dict",
    "load_consumer_sales",
    "direct_label_counts",
    # kalite kapısı
    "structural_rejection_reason",
    "quality_flags",
    "assess_quality",
    "fingerprint",
    "FLAG_EXTREME_RATIO",
    "FLAG_FINAL_ABOVE_INITIAL",
    "FLAG_FAST_CLOSE",
    "FLAG_LONG_CLOSE",
    "FLAG_FUTURE_DATE",
    "FLAG_DUPLICATE",
    # analitik
    "sale_analytics",
    "segment_benchmark",
    "segment_key",
    "MIN_SEGMENT_OBSERVATIONS",
]
