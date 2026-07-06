"""PublicLabelMiner — kamuya açık işlem domain'lerinden gerçekleşen-fiyat etiketi.

Adapter'lar RESMÎ/AÇIK KAYITLARI (operatörün sağladığı yapısal kayıtlar: UYAP icra
sonuç tutanağı, KAP maddi duran varlık satış bildirimi, TOKİ/GYO açık artırma &
birincil satış duyurusu) normalize eder. **Canlı kazıma BU MODÜLDE YOKTUR**; her
kaynağın kendi ToS/robots kuralları ayrıdır, canlı çekim ToS incelemesi sonrası
ayrı bir operatör adımıdır (mevcut scraper/adapters desenindeki gibi).

Bu bir PARSER katmanıdır — sürekli ingestion DEĞİL: hiçbir kayıt otomatik keşfedilmez
ya da çekilmez, etiketler "kendiliğinden akmaz". Operatör bir kaydı verir, adapter
normalize eder. Üç doğrulama düzeyi ayrıdır: (1) parser/adapter doğrulaması (alan
eşleme testleri, örnek fixture'larla), (2) GERÇEK-KAYIT doğrulaması (indirilmiş resmî
kayıtlarla — henüz yapılmadı), (3) canlı kaynak ingestion (kurulmadı).

Her adapter etiketin domain'ini + mekanizmasını + referans fiyat türünü açıkça
etiketler; böylece registry domain'leri karıştırmaz (UYAP/KAP/TOKİ asla
asking→closing head'ine girmez).
"""

from __future__ import annotations

import json
from pathlib import Path

from .registry import LabelError, normalize_label


class PublicSourceAdapter:
    """Bir kamu kaynağının yapısal kaydını birleşik etiket şemasına çevirir."""

    source: str = ""

    def parse(self, record: dict) -> dict | None:
        """Tek kaydı ham etiket dict'ine çevirir; uygun değilse None."""
        raise NotImplementedError


class UYAPAdapter(PublicSourceAdapter):
    """UYAP e-satış (icra) — muhammen (ekspertiz) değer → ihale bedeli (gözlenen).

    sale_mechanism=auction. Sıradan ikinci-el satış DEĞİLDİR; asking→closing
    head'ine girmez, yalnızca FairValue kalibrasyonuna.
    """

    source = "uyap"

    def parse(self, record: dict) -> dict | None:
        result = str(record.get("ihale_sonucu") or record.get("result") or "").lower()
        bid = record.get("ihale_bedeli", record.get("winning_bid"))
        if bid in (None, "", 0) or ("sat" in result and "satılma" in result):
            return None  # ihale gerçekleşmedi / bedel yok
        return {
            "domain": "uyap",
            "label_source": self.source,
            "sale_mechanism": "auction",
            "reference_price_type": "appraisal",
            "reference_price": record.get("muhammen_bedel", record.get("appraised_value")),
            "realized_price": bid,
            "related_party": False,
            "province": record.get("il", record.get("province")),
            "district": record.get("ilce", record.get("district")),
            "property_type": record.get("tasinmaz_turu", record.get("property_type")),
            "gross_m2": record.get("brut_m2", record.get("gross_m2")),
            "transaction_date": record.get("ihale_tarihi", record.get("date")),
            "external_ref": record.get("dosya_no", record.get("ref")),
        }


class KAPAdapter(PublicSourceAdapter):
    """KAP — maddi duran varlık (gayrimenkul) satış bildirimi.

    SINIR (dürüstlük): GİRDİ YAPISALDIR. Operatör, denetlenmiş resmî bildirim
    temsilinden alanları (toplam_satis_bedeli, degerleme_tutari, prior_appraisal_value,
    iliskili_taraf, …) ELLE ÇIKARIR. Bu adapter HAM KAP SERBEST METNİNİ AYRIŞTIRMAZ;
    yalnızca yapısal alanları alıp normalized reference_price_type'ı belirler ve
    provenance'ı KORUR: güncel yapısal değerleme (degerleme_raporu_hazirlandi +
    degerleme_tutari) → appraisal; yoksa operatörün metinden çıkarıp prior_appraisal_value
    alanına yazdığı önceki ekspertiz → prior_appraisal; ikisi de yoksa none.
    İlişkili taraf → corporate_related_party; aksi halde corporate_negotiated_non_related
    (related_party=false tek başına arm_length yapmaz).
    """

    source = "kap"

    def parse(self, record: dict) -> dict | None:
        sale = record.get("toplam_satis_bedeli", record.get("sale_value"))
        if sale in (None, "", 0):
            return None

        # Referans fiyat PROVENANCE'ı YAPISAL alanlardan belirlenir (serbest metin
        # AYRIŞTIRILMAZ): o işlem için hazırlanmış GÜNCEL yapısal değerleme mi
        # (degerleme_tutari), yoksa operatörün açıklama metninden çıkarıp
        # prior_appraisal_value'ya yazdığı ÖNCEKİ/emsal ekspertiz mi? Bu ikisi
        # FARKLI referans türüdür; adapter yalnızca hangisinin dolu olduğuna bakar.
        report_prepared = bool(
            record.get(
                "degerleme_raporu_hazirlandi",
                record.get("valuation_report_prepared", False),
            )
        )
        current_val = record.get("degerleme_tutari", record.get("valuation_amount"))
        prior_val = record.get(
            "prior_appraisal_value", record.get("emsal_degerleme_tutari")
        )
        if report_prepared and current_val not in (None, "", 0):
            ref_price, ref_type = current_val, "appraisal"
        elif prior_val not in (None, "", 0):
            ref_price, ref_type = prior_val, "prior_appraisal"
        else:
            ref_price, ref_type = None, "none"

        related = bool(record.get("iliskili_taraf", record.get("related_party", False)))
        # related_party=false TEK BAŞINA arm's-length KANITI DEĞİLDİR.
        mechanism = (
            "corporate_related_party" if related else "corporate_negotiated_non_related"
        )
        return {
            "domain": "kap",
            "label_source": self.source,
            "sale_mechanism": mechanism,
            "reference_price_type": ref_type,
            "reference_price": ref_price,
            "realized_price": sale,
            "related_party": related,
            "value_method": record.get(
                "deger_belirleme_yontemi", record.get("value_method")
            ),
            "province": record.get("il", record.get("province")),
            "district": record.get("ilce", record.get("district")),
            "property_type": record.get("tasinmaz_turu", record.get("property_type")),
            "gross_m2": record.get("brut_m2", record.get("gross_m2")),
            "transaction_date": record.get("islem_tarihi", record.get("date")),
            "external_ref": record.get(
                "kap_id", record.get("record_id", record.get("ref"))
            ),
        }


class TOKIAdapter(PublicSourceAdapter):
    """TOKİ / GYO — iki tür: (1) açık artırma (reserve → teklif),
    (2) proje birincil satış agregatı (offered_avg → realized_avg).

    ``record['kind']`` = 'auction' | 'project_avg' ile ayrışır.
    """

    source = "toki"

    def parse(self, record: dict) -> dict | None:
        kind = str(record.get("kind") or "auction").lower()
        if kind == "project_avg":
            realized = record.get("realized_avg")
            if realized in (None, "", 0):
                return None
            return {
                "domain": "toki",
                "label_source": self.source,
                "sale_mechanism": "primary_market",
                "reference_price_type": "offered_avg",
                "reference_price": record.get("offered_avg"),
                "realized_price": realized,
                "related_party": False,
                "province": record.get("il", record.get("province")),
                "district": record.get("ilce", record.get("district")),
                "property_type": record.get("property_type", "konut"),
                "transaction_date": record.get("tarih", record.get("date")),
                "external_ref": record.get("proje", record.get("ref")),
            }
        bid = record.get("teklif_toplam", record.get("winning_bid"))
        if bid in (None, "", 0):
            return None
        return {
            "domain": "toki",
            "label_source": self.source,
            "sale_mechanism": "public_auction",
            "reference_price_type": "reserve",
            "reference_price": record.get("muhammen_bedel_toplam", record.get("reserve")),
            "realized_price": bid,
            "related_party": False,
            "province": record.get("il", record.get("province")),
            "district": record.get("ilce", record.get("district")),
            "property_type": record.get("property_type", "konut"),
            "transaction_date": record.get("tarih", record.get("date")),
            "external_ref": record.get("proje", record.get("ref")),
        }


_ADAPTERS: dict[str, PublicSourceAdapter] = {
    a.source: a for a in (UYAPAdapter(), KAPAdapter(), TOKIAdapter())
}


class PublicLabelMiner:
    """Kaynak adı + yapısal kayıtlardan normalize edilmiş etiketler üretir."""

    def __init__(self, adapters: dict[str, PublicSourceAdapter] | None = None) -> None:
        self.adapters = adapters or dict(_ADAPTERS)

    def sources(self) -> list[str]:
        return sorted(self.adapters)

    def mine(self, source: str, records: list[dict]) -> list[dict]:
        """Kayıtları etiketlere çevirir (uygun olmayan/hatalı kayıtları atlar)."""
        adapter = self.adapters.get(source)
        if adapter is None:
            raise LabelError(
                f"Bilinmeyen kaynak: {source!r}. Geçerli: {', '.join(self.sources())}"
            )
        out: list[dict] = []
        for rec in records:
            raw = adapter.parse(rec)
            if raw is None:
                continue
            try:
                out.append(normalize_label(raw))
            except LabelError:
                continue  # eksik/geçersiz kayıt — sessizce atla
        return out

    def mine_file(self, source: str, path: str | Path) -> list[dict]:
        """JSON (kayıt listesi) veya CSV dosyasından etiket üretir."""
        path = Path(path)
        if path.suffix.lower() == ".json":
            records = json.loads(path.read_text(encoding="utf-8"))
        else:
            import pandas as pd

            records = pd.read_csv(path).to_dict("records")
        if isinstance(records, dict):
            records = [records]
        return self.mine(source, records)
