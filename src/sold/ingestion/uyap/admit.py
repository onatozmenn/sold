"""Açık admisyon (explicit admission) — denetimden AYRI, kasıtlı bir operatör eylemi.

Bir ayrıştırıcı/çıkarıcı genuine ``uyap.json``'u DOĞRUDAN değiştirmez. Akış:
aday → çıkarılan kanıt → denetim → (gerekirse) insan incelemesi → AÇIK admisyon → mevcut
UYAP kanıt şeması. Admisyon, yazmadan ÖNCE mevcut genuine UYAP şemasını (``normalize_auction``)
DOĞRULAR. Yıkıcı yeniden-yazma YOK; ``public_record_id`` ile IDEMPOTENT (tekrar çalıştırma
kopya genuine gözlem oluşturmaz). Admisyon yapısal tanıları OTOMATİK yeniden hesaplamaz;
mevcut yapısal boru hattı admisyondan sonra kendi kod yoluyla çalıştırılabilir.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import store
from .models import (
    ADMISSIBLE_COMPLETED_SALE,
    EXCLUDED_NON_TERMINAL,
    STATE_ADMITTED,
    STATE_EXCLUDED,
)

INGESTION_BATCH = "UYAP Evidence Ingestion Pipeline V1"


def public_record_identity_map(records: list[dict]) -> dict[str, str]:
    identities: dict[str, str] = {}
    for record in records:
        public_id = record.get("public_record_id")
        if public_id in (None, ""):
            continue
        canonical = str(public_id)
        for identity in [canonical, *(record.get("public_record_aliases") or [])]:
            if identity in (None, ""):
                continue
            key = str(identity)
            existing = identities.get(key)
            if existing is not None and existing != canonical:
                raise ValueError(f"public record identity {key!r} maps to multiple primary records")
            identities[key] = canonical
    return identities


def known_public_record_ids(records: list[dict]) -> set[str]:
    return set(public_record_identity_map(records))


def _genuine_dir() -> Path:
    from ...structural.datasets import GENUINE_DIR

    return GENUINE_DIR


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value.strip()[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _bulk_province(candidate: dict) -> str | None:
    """Kanıtta il yoksa toplu-arama ilinden KABA il (kişisel değil; ör. 'ANKARA' → 'Ankara')."""
    label = str((candidate.get("bulk") or {}).get("province_label") or "").strip()
    return label.title() or None


def build_genuine_record(candidate: dict) -> dict:
    """Admitte edilecek adaydan HAM genuine UYAP kaydı kurar (uyap.json şeması; normalize sonra)."""
    ev = candidate.get("extracted") or {}
    audit = candidate.get("audit") or {}
    prov = []
    if ev.get("appraisal_source"):
        prov.append(f"appraisal<-{ev['appraisal_source']}")
    if ev.get("ihale_bedeli_source"):
        prov.append(f"ihale_bedeli<-{ev['ihale_bedeli_source']}")
    if ev.get("terminal_status_text"):
        prov.append(f"terminal<-{ev['terminal_status_text']}")
    note = (
        f"GENUINE audited UYAP completed-sale auction admitted via {INGESTION_BATCH}. "
        f"winning_bid=official Ihale Bedeli (NOT Odenmesi Gereken Bedel/deposit/share/setoff/KDV). "
        f"appraised_value=Q (muhammen/kiymet/takdir). provenance: {', '.join(prov) or 'n/a'}. "
        f"PRIVACY: only non-personal institution/file/property/economic/public-record fields. "
        f"domain=uyap, sale_mechanism=auction, reference_price_type=appraisal -> excluded from asking_to_closing_labels()."
    )
    return {
        "public_record_id": (candidate.get("kayit_no") or (candidate.get("bulk") or {}).get("kayit_no")
                             or candidate.get("file_id")),
        "auction_date": _iso_date(ev.get("completion_datetime")),
        "province": ev.get("province") or _bulk_province(candidate),
        "district": ev.get("district"),
        "property_type": ev.get("property_type"),
        "appraised_value": audit.get("appraisal_value"),
        "sold": True,
        "outcome_status": "Satıldı",
        "outcome_reason": None,
        "winning_bid": audit.get("auction_price"),
        "offer_count": None,
        "bidder_count": None,
        "priority_claims": None,
        "realization_costs": None,
        "parcel_area_m2": None,
        "unit_net_m2": None,
        "unit_gross_m2": None,
        "source_audited": True,
        "_batch": INGESTION_BATCH,
        "_source_institution": candidate.get("institution"),
        "_ingestion_candidate_id": candidate.get("candidate_id"),
        "_note": note,
    }


def admit(candidate: dict, genuine_path: Path | str | None = None, store_dir: Path | str | None = None) -> dict:
    """ADMISSIBLE_COMPLETED_SALE adayını genuine uyap.json'a IDEMPOTENT admitte eder.

    Yalnızca denetim kararı ADMISSIBLE_COMPLETED_SALE ise yazar. ``normalize_auction`` ile
    şema doğrulaması yapar; ``public_record_id`` zaten varsa KOPYA OLUŞTURMAZ.
    """
    public_record_id = (
        candidate.get("kayit_no")
        or (candidate.get("bulk") or {}).get("kayit_no")
        or candidate.get("file_id")
    )
    if public_record_id in (None, ""):
        return {"status": "error", "reason": "missing public_record_id (file_id)"}

    path = Path(genuine_path) if genuine_path else _genuine_dir() / "uyap.json"
    from .io import atomic_write_json, locked

    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    identity_map = public_record_identity_map(records)
    canonical_id = identity_map.get(str(public_record_id))
    if canonical_id is not None:
        candidate["state"] = STATE_ADMITTED
        candidate["admitted_public_record_id"] = canonical_id
        store.log_event(candidate, "admit_idempotent", f"already present: {canonical_id}")
        store.upsert(candidate, store_dir)
        return {"status": "already_admitted", "public_record_id": canonical_id,
                "genuine_uyap_total": len(records)}

    if not candidate.get("artifacts"):
        return {"status": "error", "reason": "source artifacts are required for admission"}

    from .pipeline import run_audit

    try:
        candidate = run_audit(candidate, store_dir=store_dir, genuine_path=path)
    except (OSError, ValueError) as exc:
        return {"status": "error", "reason": f"artifact verification failed: {exc}"}
    audit = candidate.get("audit") or {}
    if audit.get("decision") != ADMISSIBLE_COMPLETED_SALE:
        return {"status": "not_admissible", "decision": audit.get("decision"),
                "reason": "fresh artifact audit did not establish ADMISSIBLE_COMPLETED_SALE"}

    rec = build_genuine_record(candidate)
    if rec["appraised_value"] in (None, 0) or rec["winning_bid"] in (None, 0):
        return {"status": "error", "reason": "missing appraisal or auction price for genuine schema"}

    # MEVCUT genuine şema doğrulaması (yazmadan önce) — hata olursa admisyon reddedilir.
    from ...structural.auction import normalize_auction

    normalized = normalize_auction(rec)
    if not bool(normalized["sold"]) or normalized["winning_bid"] is None:
        return {"status": "error", "reason": "record failed genuine UYAP schema validation"}

    with locked(path):
        records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        identity_map = public_record_identity_map(records)
        canonical_id = identity_map.get(str(rec["public_record_id"]))
        if canonical_id is not None:
            candidate["state"] = STATE_ADMITTED
            candidate["admitted_public_record_id"] = canonical_id
            store.log_event(candidate, "admit_idempotent", f"already present: {canonical_id}")
            store.upsert(candidate, store_dir)
            return {"status": "already_admitted", "public_record_id": canonical_id,
                    "genuine_uyap_total": len(records)}
        records.append(rec)
        atomic_write_json(path, records)

    candidate["state"] = STATE_ADMITTED
    candidate["admitted_public_record_id"] = rec["public_record_id"]
    candidate["admitted_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    store.log_event(candidate, "admitted", f"{rec['public_record_id']} -> uyap.json")
    store.upsert(candidate, store_dir)
    return {"status": "admitted", "public_record_id": rec["public_record_id"],
            "win_over_appraisal": audit.get("win_over_appraisal"), "genuine_uyap_total": len(records)}


def record_exclusion(candidate: dict, candidates_path: Path | str | None = None, store_dir: Path | str | None = None) -> dict:
    """EXCLUDED_NON_TERMINAL adayını MEVCUT uyap_candidates.json manifestine IDEMPOTENT ekler.

    Genuine sete / SMM momentlerine GİRMEZ; negatif sale-probability gözlemine ÇEVRİLMEZ.
    """
    audit = candidate.get("audit") or {}
    if audit.get("decision") != EXCLUDED_NON_TERMINAL:
        return {"status": "skip", "reason": "only EXCLUDED_NON_TERMINAL candidates are recorded here"}
    ev = candidate.get("extracted") or {}
    path = Path(candidates_path) if candidates_path else _genuine_dir() / "uyap_candidates.json"
    from .io import atomic_write_json, locked

    cid = candidate.get("candidate_id")
    entry = {
        "candidate_id": cid,
        "batch": INGESTION_BATCH,
        "domain": "uyap",
        "audit_status": EXCLUDED_NON_TERMINAL,
        "source_institution": candidate.get("institution"),
        "file": candidate.get("file_id"),
        "appraisal_value_tl": ev.get("appraisal_value"),
        "auction_result_price_shown_tl": ev.get("ihale_bedeli") or ev.get("result_card_amount"),
        "status_shown": ev.get("terminal_status_text") or candidate.get("status_text"),
        "enters_genuine_uyap": False,
        "enters_smm": False,
        "exclusion_reason": "; ".join(audit.get("blocking_reasons", []) or []),
        "_note": "Recorded by the UYAP Evidence Ingestion Pipeline V1. Not admitted, not used in "
                 "uyap_win_over_appraisal, not converted into a negative sale-probability observation; "
                 "uyap_sale_prob is not created.",
    }
    with locked(path):
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if any(c.get("candidate_id") == cid for c in manifest):
            return {"status": "already_recorded", "candidate_id": cid}
        manifest.append(entry)
        atomic_write_json(path, manifest)
    candidate["state"] = STATE_EXCLUDED
    store.log_event(candidate, "excluded_recorded", f"{cid} -> uyap_candidates.json")
    store.upsert(candidate, store_dir)
    return {"status": "recorded", "candidate_id": cid}
