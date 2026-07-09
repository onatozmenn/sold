"""GERÇEK denetlenmiş yapısal veri kümesi yükleyici + durum raporu — fixture'lardan AYRI.

Gerçek denetlenmiş kamu gözlemleri ``validation/structural/`` altında tutulur
(``source_audited=true``) ve doğrulanmış Level-2 kayıtlarından (KAP 963554, TOKİ PMVR3,
UYAP 16766356960) türetilmiştir. Fixture / illustratif kayıtlar testlerde kalır ve GERÇEK
sayıma KATILMAZ. Operatör-denetimli batch içe aktarma: JSON liste (veya CSV) — ham
UYAP/KAP/TOKİ kazıma YOKTUR. Her kayıt audit durumunu ve kamu kayıt kimliğini korur.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from .auction import load_auctions
from .kap import load_kap_disposals
from .toki import difference_disclosures

_REPO_GENUINE_DIR = Path(__file__).resolve().parents[3] / "validation" / "structural"
_PACKAGED_GENUINE_DIR = Path(__file__).resolve().parents[1] / "evidence"


class StructuralEvidenceError(RuntimeError):
    """Required audited structural evidence is absent or internally inconsistent."""


def _default_genuine_dir() -> Path:
    configured = os.environ.get("SOLD_EVIDENCE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if _REPO_GENUINE_DIR.is_dir():
        return _REPO_GENUINE_DIR
    return _PACKAGED_GENUINE_DIR


GENUINE_DIR = _default_genuine_dir()


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
    return [r for r in records if r.get("source_audited") is True]


def _require_audited(records: list[dict], source: str, path: Path) -> list[dict]:
    audited = _audited(records)
    if not audited:
        raise StructuralEvidenceError(
            f"Required audited {source.upper()} evidence is missing or empty: {path}"
        )

    if source == "uyap":
        identities = [str(r.get("public_record_id") or "").strip() for r in audited]
    elif source == "kap":
        identities = [str(r.get("official_record_id") or "").strip() for r in audited]
    else:
        identities = [
            f"{r.get('project_id') or ''}|{r.get('as_of_date') or ''}" for r in audited
        ]
    if any(not identity.strip("|") for identity in identities):
        raise StructuralEvidenceError(f"Audited {source.upper()} evidence has a missing identity: {path}")
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise StructuralEvidenceError(
            f"Audited {source.upper()} evidence has duplicate identities: {', '.join(duplicates)}"
        )
    return audited


def load_uyap_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "uyap.json")


def load_kap_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "kap.json")


def load_toki_records(directory: Path | None = None) -> list[dict]:
    return _load_records(Path(directory or GENUINE_DIR) / "toki.json")


def load_kap_candidates(directory: Path | None = None) -> list[dict]:
    """PENDING_AUDIT KAP adayları (kap_candidates.json). Bunlar genuine KAP setine GİRMEZ
    ve ``kap_log_ratio``'ya KATILMAZ; yalnızca 8 audit koşulu doğrulanırsa admitte edilir."""
    return _load_records(Path(directory or GENUINE_DIR) / "kap_candidates.json")


def load_uyap_candidates(directory: Path | None = None) -> list[dict]:
    """Denetlenmiş ama GENUINE tamamlanmış-satış setine GİRMEYEN UYAP adayları
    (uyap_candidates.json — ör. terminal-olmayan / EXCLUDED_NON_TERMINAL). Bunlar
    ``uyap_win_over_appraisal``'a KATILMAZ ve negatif sale-probability gözlemine
    ÇEVRİLMEZ (``uyap_sale_prob`` YOK). kap_candidates.json audit-manifest kuralını yansıtır."""
    return _load_records(Path(directory or GENUINE_DIR) / "uyap_candidates.json")


def load_genuine_datasets(directory: Path | None = None) -> dict:
    """Yalnızca ``source_audited=true`` kayıtlardan gerçek yapısal veri kümeleri kurar."""
    root = Path(directory or GENUINE_DIR)
    uyap_records = _require_audited(load_uyap_records(root), "uyap", root / "uyap.json")
    kap_records = _require_audited(load_kap_records(root), "kap", root / "kap.json")
    toki_disclosures = _require_audited(load_toki_records(root), "toki", root / "toki.json")
    uyap_df = load_auctions(uyap_records)
    kap_df = load_kap_disposals(kap_records)
    if uyap_df.empty or kap_df.empty:
        raise StructuralEvidenceError(
            "Audited structural evidence exists but no UYAP/KAP records survived normalization"
        )
    toki_result = difference_disclosures(toki_disclosures)
    return {
        "uyap": uyap_df,
        "kap": kap_df,
        "toki_disclosures": toki_disclosures,
        "toki_result": toki_result,
    }


def _uyap_status(df: pd.DataFrame) -> dict:
    keys0 = (
        "total_audited_auctions", "sold", "unsold", "censored_outcomes", "genuine_no_trade",
        "winning_bids_observed", "offer_counts_observed", "bidder_counts_observed",
        "exact_legal_floors_observed",
    )
    if df is None or len(df) == 0:
        return {k: 0 for k in keys0}
    sold = df["sold"].astype(bool)
    cls = df["trade_outcome_class"].astype(str) if "trade_outcome_class" in df.columns else pd.Series([""] * len(df))
    _CENSORED = ("settlement_pending", "missing_result", "dropped_administrative", "dropped_unspecified", "unknown")
    no_trade = int((cls == "dropped_no_trade").sum())
    return {
        "total_audited_auctions": int(len(df)),
        "sold": int(sold.sum()),
        # unsold = yalnızca AÇIK ekonomik no-trade; censored (bekleyen/eksik/idari) DEĞİL
        "unsold": no_trade,
        "censored_outcomes": int(cls.isin(_CENSORED).sum()),
        "genuine_no_trade": no_trade,
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
    candidates = load_kap_candidates(directory)
    uyap_candidates = load_uyap_candidates(directory)

    genuine = load_genuine_datasets(directory)
    non_audited = {
        "uyap": int(len(uyap_recs) - len(_audited(uyap_recs))),
        "kap": int(len(kap_recs) - len(_audited(kap_recs))),
        "toki": int(len(toki_recs) - len(_audited(toki_recs))),
    }
    pending = [
        {
            "candidate_id": c.get("candidate_id"),
            "source_record_ids": c.get("source_record_ids"),
            "audit_status": c.get("audit_status"),
            "blocking_conditions": [
                f"#{b.get('condition')} {b.get('name')} [{b.get('status')}]"
                for b in c.get("blocking_conditions", [])
            ],
        }
        for c in candidates
        if str(c.get("audit_status")) == "PENDING_AUDIT"
    ]
    uyap_excluded = [
        {
            "candidate_id": c.get("candidate_id"),
            "batch": c.get("batch"),
            "file": c.get("file"),
            "audit_status": c.get("audit_status"),
            "enters_genuine_uyap": bool(c.get("enters_genuine_uyap", False)),
            "enters_smm": bool(c.get("enters_smm", False)),
            "exclusion_reason": c.get("exclusion_reason"),
        }
        for c in uyap_candidates
        if str(c.get("audit_status")) != "ADMITTED"
    ]
    return {
        "genuine": {
            "uyap": _uyap_status(genuine["uyap"]),
            "kap": _kap_status(genuine["kap"]),
            "toki": _toki_status(genuine["toki_disclosures"], genuine["toki_result"]),
        },
        "kap_pending_candidates": pending,  # audit geçene dek genuine sete GİRMEZ
        "uyap_excluded_candidates": uyap_excluded,  # terminal-olmayan/dışlanan (genuine sete GİRMEZ)
        "non_audited_records": non_audited,  # fixture/illustratif (GERÇEK sayıma katılmaz)
    }
