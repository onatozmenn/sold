"""Ham HTML arşivleme (yeniden üretilebilirlik + tekrar ayrıştırma için)."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import re
from pathlib import Path


def _safe_name(url: str) -> str:
    stem = url.replace("\\", "/").rstrip("/").split("/")[-1]
    stem = re.sub(r"\.html?$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    return stem or hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def save_html(
    source: str,
    url: str,
    html: str,
    captured_at: dt.datetime | None = None,
    base_dir: str | Path = "data/raw",
) -> Path:
    """HTML'i ``data/raw/<source>/<YYYY-MM-DD>/<ad>.html.gz`` olarak sıkıştırır."""
    captured_at = captured_at or dt.datetime.now(dt.timezone.utc)
    day = captured_at.strftime("%Y-%m-%d")
    directory = Path(base_dir) / source / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_name(url)}.html.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(html)
    return path
