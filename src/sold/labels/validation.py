"""Gerçek-kayıt (level-2) doğrulama harness'i — illüstratif fixture'lardan AYRI.

İllüstratif fixture testleri (``tests/test_labels.py``, ``samples/labels/illustrative_*``)
parser'ı UYDURMA veriyle sınar → **level-1**. Bu harness ise OPERATÖRÜN indirdiği
GERÇEK resmî kayıtları (KAP bildirimi, UYAP ihale sonucu, TOKİ satış/açık artırma)
parser'dan geçirip elle DENETLENMİŞ beklenen çıktıyla karşılaştırır → **level-2**.

Ham resmî artefakt (PDF/HTML) COMMIT EDİLMEZ (yeniden dağıtım / gizlilik / lisans
belirsizliği). Yalnızca commit edilir:
  (a) manifest (kaynak kimliği, çekim tarihi, kayıt türü, parser sürümü,
      gerçek-resmî-kayıt mı, elle denetlendi mi, yeniden-dağıtım uygun mu),
  (b) parser girdisi (KİŞİSEL VERİ İÇERMEYEN yapısal alanlar),
  (c) elle denetlenmiş beklenen normalize çıktı.

Bir kayıt YALNIZCA ``is_real_official_record`` ve ``manually_audited`` İKİSİ de
True iken doğrulama testinde zorlanır; aksi halde PENDING olarak atlanır (skip).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# repo_kökü/validation/real_records/*.json
REAL_RECORDS_DIR = (
    Path(__file__).resolve().parents[3] / "validation" / "real_records"
)

# Manifestte bulunması zorunlu alanlar (audit isteği ile birebir)
MANIFEST_FIELDS = (
    "source",
    "domain",
    "record_id",
    "record_type",
    "retrieval_date",
    "parser_version",
    "is_real_official_record",
    "manually_audited",
    "redistribution_ok",
    "notes",
)

# Karşılaştırılan normalize çıktı alanları (transaction_date test'te str olarak kıyaslanır)
COMPARED_FIELDS = (
    "domain",
    "label_source",
    "sale_mechanism",
    "reference_price_type",
    "reference_price",
    "realized_price",
    "related_party",
    "value_method",
    "transaction_date",
)


@dataclass
class ValidationRecord:
    path: Path
    manifest: dict
    parser_input: dict
    expected_output: dict | list

    @property
    def source(self) -> str:
        return str(self.manifest.get("source", ""))

    @property
    def observation_model(self) -> str:
        """Kayıt modeli: 'realized_label' (paired) veya 'aggregate_observation'.

        Varsayılan 'realized_label' — mevcut KAP/UYAP manifestleri bu alanı
        taşımaz ve DEĞİŞMEDEN kalır (yalnızca toplu-gözlem kayıtları işaretlenir).
        """
        return str(self.manifest.get("observation_model", "realized_label"))

    @property
    def ready(self) -> bool:
        """Gerçek + elle denetlenmiş mi? (yalnızca o zaman doğrulama zorlanır)."""
        m = self.manifest
        return bool(m.get("is_real_official_record")) and bool(m.get("manually_audited"))


def load_validation_records(directory: Path | None = None) -> list[ValidationRecord]:
    """validation/real_records altındaki tüm doğrulama kayıtlarını yükler."""
    directory = directory or REAL_RECORDS_DIR
    out: list[ValidationRecord] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            ValidationRecord(
                path=path,
                manifest=data.get("manifest", {}),
                parser_input=data.get("parser_input", {}),
                expected_output=data.get("expected_output", {}),
            )
        )
    return out
