"""Faz 0 hızlı başlangıç: KFE'yi çekip data/kfe.csv'e yazar.

Kullanım:
    python scripts/fetch_kfe.py

Önkoşul: .env içinde EVDS_API_KEY tanımlı olmalı
(ücretsiz anahtar: https://evds2.tcmb.gov.tr).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

# src layout'u içe aktarılabilir yap (paket kurulmadan da çalışsın)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sold.evds.client import EvdsAuthError, EvdsClient  # noqa: E402
from sold.evds.kfe import fetch_kfe  # noqa: E402


def main() -> int:
    try:
        with EvdsClient() as client:
            df = fetch_kfe(client, start_date="01-01-2010", end_date=dt.date.today())
    except EvdsAuthError as exc:
        print(f"[HATA] {exc}")
        print("→ .env dosyanıza EVDS_API_KEY ekleyin (https://evds2.tcmb.gov.tr).")
        return 1

    if df.empty:
        print("[UYARI] Veri gelmedi. Kodları doğrulayın: EvdsClient.list_series('bie_kfe')")
        return 1

    out = Path("data/kfe.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[OK] {len(df)} satır -> {out}")
    print(df.tail(6).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
