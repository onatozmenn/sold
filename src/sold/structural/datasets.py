"""GERÇEK denetlenmiş yapısal veri kümesi yükleyici + durum raporu — fixture'lardan AYRI.

Gerçek denetlenmiş kamu gözlemleri ``validation/structural/`` altında tutulur
(``source_audited=true``) ve doğrulanmış Level-2 kayıtlarından (KAP 963554, TOKİ PMVR3,
UYAP 16766356960) türetilmiştir. Fixture / illustratif kayıtlar testlerde kalır ve GERÇEK
sayıma KATILMAZ. Operatör-denetimli batch içe aktarma: JSON liste (veya CSV) — ham
UYAP/KAP/TOKİ kazıma YOKTUR. Her kayıt audit durumunu ve kamu kayıt kimliğini korur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .auction import load_auctions
from .kap import load_kap_disposals
from .toki import difference_disclosures

# repo_kökü/validation/structural
GENUINE_DIR = Path(__file__).resolve().parents[3] / "validation" / "structural"


def _load_records(path: Path) -> list[dict]:
    """JSON (liste) veya CSV batch dosyasından ham kayıtları okur; yoksa []"""
    path = Path(path)
    if not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path).to_dict("records")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def _audited(records: list[dict]) -> list[dict]:
    return [r for r in records if bool(r.get("source_audited"))]


def load_uyap_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "uyap.json")


def load_kap_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "kap.json")


def load_toki_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "toki.json")


def load_genuine_datasets(directory: Path | None = None) -> dict:
    """Yalnızca ``source_audited=true`` kayıtlardan gerçek yapısal veri kümeleri kurar."""
    uyap_df = load_auctions(_audited(load_uyap_records(directory)))
    kap_df = load_kap_disposals(_audited(load_kap_records(directory)))
    toki_disclosures = _audited(load_toki_records(directory))
    toki_result = difference_disclosures(toki_disclosures)
    return {
        "uyap": uyap_df,
        "kap": kap_df,
        "toki_disclosures": toki_disclosures,
        "toki_result": toki_result,
    }


def _uyap_status(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {k: 0 for k in (
            "total_audited_auctions", "sold", "unsold", "winning_bids_observed",
            "offer_counts_observed", "bidder_counts_observed", "exact_legal_floors_observed")}
    sold = df["sold"].astype(bool)
    return {
        "total_audited_auctions": int(len(df)),
        "sold": int(sold.sum()),
        "unsold": int((~sold).sum()),
        "winning_bids_observed": int(pd.to_numeric(df["winning_bid"], errors="coerce").notna().sum()),
        "offer_counts_observed": int(pd.to_numeric(df["offer_count"], errors="coerce").notna().sum()),
        "bidder_counts_observed": int(pd.to_numeric(df["bidder_count"], errors="coerce").notna().sum()),
        "exact_legal_floors_observed": int(df["legal_floor_exact"].astype(bool).sum()),
    }


def _kap_status(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {k: 0 for k in (
            "audited_eligible_disposals", "negotiated_calibration_observations",
            "appraisal_observations", "prior_appraisal_observations")}
    ref = df["reference_price_type"].astype(str)
    return {
        "audited_eligible_disposals": int(len(df)),
        "negotiated_calibration_observations": int(df["negotiated"].astype(bool).sum()),
        "appraisal_observations": int((ref == "appraisal").sum()),
        "prior_appraisal_observations": int((ref == "prior_appraisal").sum()),
    }


def _toki_status(disclosures: list[dict], toki_result: dict) -> dict:
    projects = {str(d.get("project_id")) for d in disclosures if d.get("project_id")}
    strata = sum(len(d.get("strata", []) or []) for d in disclosures)
    return {
        "audited_disclosures": int(len(disclosures)),
        "projects_represented": int(len(projects)),
        "room_type_cumulative_strata": int(strata),
        "valid_derived_period_cohorts": int(len(toki_result.get("cohorts", []))),
        "revision_blocked_cohorts": int(len(toki_result.get("revisions", []))),
        "reconciliation_blocked_strata": int(len(toki_result.get("reconciliation", []))),
    }


def dataset_status(directory: Path | None = None) -> dict:
    """GERÇEK denetlenmiş yapısal gözlem durumunu (fixture'lardan AYRI) raporlar."""
    uyap_recs = load_uyap_records(directory)
    kap_recs = load_kap_records(directory)
    toki_recs = load_toki_records(directory)

    genuine = load_genuine_datasets(directory)
    non_audited = {
        "uyap": int(len(uyap_recs) - len(_audited(uyap_recs))),
        "kap": int(len(kap_recs) - len(_audited(kap_recs))),
        "toki": int(len(toki_recs) - len(_audited(toki_recs))),
    }
    return {
        "genuine": {
            "uyap": _uyap_status(genuine["uyap"]),
            "kap": _kap_status(genuine["kap"]),
            "toki": _toki_status(genuine["toki_disclosures"], genuine["toki_result"]),
        },
        "non_audited_records": non_audited,  # fixture/illustratif (GERÇEK sayıma katılmaz)
    }
