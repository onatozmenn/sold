"""Eşlenmemiş TOPLU (cohort) gözlem soyutlaması — paired ``RealizedLabel``'dan AYRI.

NEDEN AYRI: bazı resmî açıklamalar aynı taşınmaz için ``reference → realized`` ÇİFTİ
vermez; FARKLI popülasyonların toplu istatistiğini verir. Örnek (Park Mavera III,
"Projede Benzer Nitelikte Olan Bağımsız Bölümlerin Ortalama Satış Fiyatları,
31 Aralık 2019"): (a) SUNULAN envanter ortalaması (84 birim), (b) KÜMÜLATİF
gerçekleşen satış ortalaması (387 birim). Bunlar AYNI birimler DEĞİLDİR; bu iki
ortalamayı ``reference_price → realized_price`` olarak eşleştirmek SAHTE bir ilişki
ve yanıltıcı bir "closing indirimi" üretirdi.

Bu yüzden bu kayıtlar ``RealizedLabel``'a ZORLANMAZ. Burada eşlenmemiş toplu gözlem
olarak temsil edilir: her gözlemin kendi ``observation_role``'ü (offered_inventory /
cumulative_realized_sales) vardır ve YAPISAL olarak ``realized_price`` /
``reference_price`` alanı YOKTUR — dolayısıyla ``asking_to_closing_labels()``'e
inşaen giremez.

Adapter, operatörün elle DENETLEYİP çıkardığı YAPISAL tablo alanlarını normalize eder;
ham PDF/HTML AYRIŞTIRILMAZ. Oda-tipi strata kaynaktaki gibi KORUNUR (havuzlanmaz).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AggregateObservation as AggregateObservationRow
from .registry import DOMAINS

# Toplama düzeyi (şimdilik yalnızca cohort; ileride site/tip düzeyleri eklenebilir)
AGGREGATION_LEVELS = ("cohort",)
# Karşılaştırma kapsamı — bu gözlemler EŞLENMEMİŞ toplu popülasyonlardır
COMPARISON_SCOPES = ("unpaired_aggregate",)
# Gözlem rolü — sunulan envanter vs kümülatif gerçekleşen satışlar (FARKLI popülasyon)
OBSERVATION_ROLES = ("offered_inventory", "cumulative_realized_sales")

# Toplu-gözlem parser sürümü — paired ``PARSER_VERSION``'dan BAĞIMSIZ versiyonlanır;
# böylece toplu-gözlem parser'ı değişince yalnızca toplu kayıtlar yeniden denetlenir.
AGGREGATE_PARSER_VERSION = "1.0.0"

# Gerçek-kayıt (level-2) doğrulamasında karşılaştırılan alanlar
# (as_of_date test'te str olarak kıyaslanır; external_ref kıyasa girmez)
AGGREGATE_COMPARED_FIELDS = (
    "domain",
    "label_source",
    "aggregation_level",
    "comparison_scope",
    "observation_role",
    "project_id",
    "as_of_date",
    "count",
    "total_price",
    "average_price",
    "strata",
)


class AggregateError(ValueError):
    """Geçersiz toplu gözlem."""


def _f(v: object) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: object) -> int | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _date(v: object) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _s(v: object) -> str | None:
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return None
    return str(v)


# Oda-tipi stratum'unun KANONİK alanları (kaynaktaki gibi; havuzlanmaz, türetilmez)
STRATUM_FIELDS = (
    "room_type",
    "count",
    "total_price",
    "total_gross_m2",
    "average_price",
    "average_m2_price",
)


def _normalize_stratum(s: dict) -> dict:
    """Bir oda-tipi stratum'unu kanonik 6 alanlı forma normalize eder.

    RAPORLANAN değerler KORUNUR (yeniden hesaplanmaz/türetilmez); yalnızca tipler
    zorlanır. Böylece 5+1 (count=0, total=0) gibi satırlar 0/0 bölmesine düşmez.
    """
    return {
        "room_type": _s(s.get("room_type")),
        "count": _i(s.get("count")),
        "total_price": _f(s.get("total_price")),
        "total_gross_m2": _f(s.get("total_gross_m2")),
        "average_price": _f(s.get("average_price")),
        "average_m2_price": _f(s.get("average_m2_price")),
    }


def normalize_aggregate(raw: dict) -> dict:
    """Ham toplu gözlemi doğrular/normalize eder.

    Zorunlu: geçerli ``domain`` + ``aggregation_level`` + ``comparison_scope`` +
    ``observation_role``, ``count`` > 0, ``total_price`` > 0. ``average_price`` yoksa
    ``total_price / count``'tan türetilir (kaynak raporladıysa RAPORLANAN değer korunur;
    yeniden hesaplanıp ÜZERİNE YAZILMAZ). ``strata`` (oda-tipi kırılımı) kanonik 6-alanlı
    forma normalize edilir; RAPORLANAN değerler KORUNUR (havuzlanmaz/türetilmez).
    """
    domain = str(raw.get("domain") or "").strip()
    level = str(raw.get("aggregation_level") or "").strip()
    scope = str(raw.get("comparison_scope") or "").strip()
    role = str(raw.get("observation_role") or "").strip()
    if domain not in DOMAINS:
        raise AggregateError(f"Geçersiz domain: {domain!r}.")
    if level not in AGGREGATION_LEVELS:
        raise AggregateError(f"Geçersiz aggregation_level: {level!r}.")
    if scope not in COMPARISON_SCOPES:
        raise AggregateError(f"Geçersiz comparison_scope: {scope!r}.")
    if role not in OBSERVATION_ROLES:
        raise AggregateError(f"Geçersiz observation_role: {role!r}.")

    count = _i(raw.get("count"))
    total = _f(raw.get("total_price"))
    if count is None or count <= 0:
        raise AggregateError("count (gözlem sayısı) zorunlu ve > 0 olmalı.")
    if total is None or total <= 0:
        raise AggregateError("total_price (toplam bedel) zorunlu ve > 0 olmalı.")
    average = _f(raw.get("average_price"))
    if average is None:
        average = round(total / count, 2)

    strata_raw = raw.get("strata") or []
    strata = [_normalize_stratum(s) for s in strata_raw]

    return {
        "domain": domain,
        "label_source": _s(raw.get("label_source")),
        "aggregation_level": level,
        "comparison_scope": scope,
        "observation_role": role,
        "project_id": _s(raw.get("project_id")),
        "as_of_date": _date(raw.get("as_of_date")),
        "count": count,
        "total_price": total,
        "average_price": average,
        "strata": strata,
        "external_ref": _s(raw.get("external_ref")),
    }


class ProjectDisclosureAdapter:
    """TOKİ/GYO proje ortalama-fiyat açıklamasını EŞLENMEMİŞ toplu gözlemlere çevirir.

    Girdi, operatörün elle denetleyip çıkardığı YAPISAL tablodur (``populations``
    listesi); ham belge AYRIŞTIRILMAZ. Tek açıklama → BİRDEN ÇOK ayrı gözlem
    (sunulan envanter + kümülatif gerçekleşen satışlar). Bunlar FARKLI
    popülasyonlardır; EŞLEŞTİRİLMEZ. ``aggregation_level`` ve ``comparison_scope``
    bu açıklama türü için deterministiktir (cohort / unpaired_aggregate); rol,
    proje ve tarih operatörün yapısal alanlarından gelir.
    """

    source = "toki"

    def parse_disclosure(self, record: dict) -> list[dict]:
        project = record.get("project_id", record.get("proje"))
        as_of = record.get("as_of_date", record.get("tarih"))
        ext = record.get("external_ref", record.get("disclosure_title"))
        populations = record.get("populations") or []
        out: list[dict] = []
        for pop in populations:
            out.append(
                normalize_aggregate(
                    {
                        "domain": "toki",
                        "label_source": "toki",
                        "aggregation_level": "cohort",
                        "comparison_scope": "unpaired_aggregate",
                        "observation_role": pop.get("observation_role"),
                        "project_id": project,
                        "as_of_date": as_of,
                        "count": pop.get("count"),
                        "total_price": pop.get("total_price"),
                        "average_price": pop.get("average_price"),
                        "strata": pop.get("strata"),
                        "external_ref": ext,
                    }
                )
            )
        return out


_AGGREGATE_ADAPTERS: dict[str, ProjectDisclosureAdapter] = {
    ProjectDisclosureAdapter.source: ProjectDisclosureAdapter()
}


def aggregate_sources() -> list[str]:
    return sorted(_AGGREGATE_ADAPTERS)


def mine_aggregates(source: str, records: list[dict]) -> list[dict]:
    """Yapısal açıklama kayıtlarını eşlenmemiş toplu gözlemlere çevirir.

    Bir açıklama BİRDEN ÇOK gözlem üretebilir (her popülasyon = ayrı gözlem).
    """
    adapter = _AGGREGATE_ADAPTERS.get(source)
    if adapter is None:
        raise AggregateError(
            f"Bilinmeyen toplu-gözlem kaynağı: {source!r}. "
            f"Geçerli: {', '.join(aggregate_sources())}"
        )
    out: list[dict] = []
    for rec in records:
        out.extend(adapter.parse_disclosure(rec))
    return out


# --------------------------------------------------------------------------- #
# Kalıcılık — RealizedLabel'dan AYRI tabloda (asla karışmaz)
# --------------------------------------------------------------------------- #
def persist_aggregates(session: Session, observations: list[dict]) -> int:
    """Normalize edilmiş toplu gözlemleri aggregate_observations tablosuna ekler."""
    count = 0
    for raw in observations:
        v = normalize_aggregate(raw)
        session.add(AggregateObservationRow(**v))
        count += 1
    session.flush()
    return count


_AGG_COLUMNS = [
    "domain",
    "label_source",
    "aggregation_level",
    "comparison_scope",
    "observation_role",
    "project_id",
    "as_of_date",
    "count",
    "total_price",
    "average_price",
    "strata",
    "external_ref",
]


def load_aggregates(
    session: Session,
    domain: str | None = None,
    observation_role: str | None = None,
) -> pd.DataFrame:
    """Toplu gözlemleri DataFrame olarak yükler (isteğe bağlı domain/rol filtresi)."""
    stmt = select(AggregateObservationRow)
    if domain:
        stmt = stmt.where(AggregateObservationRow.domain == domain)
    if observation_role:
        stmt = stmt.where(AggregateObservationRow.observation_role == observation_role)
    rows = session.scalars(stmt).all()
    if not rows:
        return pd.DataFrame(columns=_AGG_COLUMNS)
    return pd.DataFrame(
        [
            {
                "domain": r.domain,
                "label_source": r.label_source,
                "aggregation_level": r.aggregation_level,
                "comparison_scope": r.comparison_scope,
                "observation_role": r.observation_role,
                "project_id": r.project_id,
                "as_of_date": r.as_of_date,
                "count": r.count,
                "total_price": _f(r.total_price),
                "average_price": _f(r.average_price),
                "strata": r.strata,
                "external_ref": r.external_ref,
            }
            for r in rows
        ]
    )
