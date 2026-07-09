"""UYAP ingestion ÇALIŞMA deposu — in-flight aday izini (candidate trail) tutar.

Bu OPERASYONEL bir hazırlık alanıdır; analitik genuine kanıttan (``validation/structural/
uyap.json``) ve dışlanan-aday manifestinden (``uyap_candidates.json``) KAVRAMSAL OLARAK
AYRIDIR. Aynı-kayıt manifest desenini (list[dict] + durum alanı) yansıtır — ikinci bir
kanıt mimarisi kurmaz. Varsayılan konum ``data/ingestion/uyap/`` (repo'ya COMMİT EDİLMEZ;
data/ .gitignore'dadır). Testler ``store_dir`` ile geçici dizin verir (ağ/kalıcılık YOK).
"""

from __future__ import annotations

import datetime as dt
import copy
import hashlib
import json
import re
from pathlib import Path

from .models import STATE_DISCOVERED, deterministic_candidate_id
from .io import atomic_write_json, locked

DEFAULT_STORE_DIR = Path("data/ingestion/uyap")
CANDIDATES_FILE = "candidates.json"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def store_path(store_dir: Path | str | None = None) -> Path:
    return Path(store_dir or DEFAULT_STORE_DIR) / CANDIDATES_FILE


def load_candidates(store_dir: Path | str | None = None) -> list[dict]:
    """Çalışma deposundaki tüm adayları okur (yoksa boş liste)."""
    path = store_path(store_dir)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def save_candidates(candidates: list[dict], store_dir: Path | str | None = None) -> Path:
    """Aday listesini deterministik (sıralı anahtar) JSON olarak yazar."""
    path = store_path(store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    sanitized = copy.deepcopy(candidates)
    for candidate in sanitized:
        candidate_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(candidate.get("candidate_id") or "candidate"))
        for artifact in candidate.get("artifacts", []):
            text = artifact.get("text")
            if text is not None and not artifact.get("local_path"):
                data = str(text).encode("utf-8")
                digest = hashlib.sha256(data).hexdigest()
                artifact_type = re.sub(
                    r"[^A-Za-z0-9._-]", "_", str(artifact.get("artifact_type") or "artifact")
                )
                artifact_dir = path.parent / "artifacts" / candidate_id
                artifact_dir.mkdir(parents=True, exist_ok=True)
                artifact_path = artifact_dir / f"{artifact_type}_{digest[:16]}.txt"
                if not artifact_path.exists():
                    artifact_path.write_bytes(data)
                artifact["local_path"] = str(artifact_path)
                artifact["sha256"] = digest
            artifact.pop("text", None)
    return atomic_write_json(path, sanitized)


def get_candidate(candidate_id: str, store_dir: Path | str | None = None) -> dict | None:
    for c in load_candidates(store_dir):
        if c.get("candidate_id") == candidate_id:
            return c
    return None


def new_candidate(
    institution: str,
    file_id: str,
    listing_ref: str | None = None,
    status_text: str | None = None,
    source_page_ref: str | None = None,
    record_ref: str | None = None,
) -> dict:
    """Kişisel-olmayan keşif metaverisinden yeni bir aday kaydı kurar (P/Q KULLANMAZ)."""
    cid = deterministic_candidate_id(institution, file_id, record_ref)
    now = _utcnow_iso()
    return {
        "candidate_id": cid,
        "state": STATE_DISCOVERED,
        "institution": institution,
        "file_id": file_id,
        "kayit_no": record_ref,
        "listing_ref": listing_ref,
        "status_text": status_text,
        "source_page_ref": source_page_ref,
        "discovered_at": now,
        "artifacts": [],
        "extracted": None,
        "reconciliation": None,
        "audit": None,
        "admitted_public_record_id": None,
        "admitted_at": None,
        "provenance_log": [{"at": now, "event": "discovered", "detail": f"{institution} / {file_id}"}],
    }


def log_event(candidate: dict, event: str, detail: str = "") -> None:
    """Aday provenans günlüğüne yapısal bir olay ekler (tanı SONUCU HARD-CODE edilmez)."""
    candidate.setdefault("provenance_log", []).append(
        {"at": _utcnow_iso(), "event": event, "detail": detail}
    )


def upsert(candidate: dict, store_dir: Path | str | None = None) -> dict:
    """Adayı candidate_id ile IDEMPOTENT ekler/günceller (kopya oluşturmaz)."""
    with locked(store_path(store_dir)):
        candidates = load_candidates(store_dir)
        cid = candidate.get("candidate_id")
        for i, current in enumerate(candidates):
            if current.get("candidate_id") == cid:
                candidates[i] = candidate
                save_candidates(candidates, store_dir)
                return candidate
        candidates.append(candidate)
        save_candidates(candidates, store_dir)
    return candidate
