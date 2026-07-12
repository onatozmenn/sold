from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(7):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(min(0.05 * (2 ** attempt), 0.5))


@contextmanager
def locked(path: Path | str):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target) + ".lock"):
        yield


def atomic_write_json(path: Path | str, value: object) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        delete=False,
        prefix=f".{target.name}.",
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    try:
        _replace_with_retry(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return target


def atomic_write_bytes(path: Path | str, value: bytes) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=target.parent,
        delete=False,
        prefix=f".{target.name}.",
        suffix=".tmp",
    ) as handle:
        handle.write(value)
        temp_path = Path(handle.name)
    try:
        _replace_with_retry(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return target