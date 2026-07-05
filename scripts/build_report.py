"""Otomatik rapor üreteci — datasets/ground_truth.csv + datasets/kfe.csv -> datasets/report.md

GitHub Actions tarafından çalıştırılır (bilgisayar gerekmez). Etiket/KFE durumuna göre:
- gerçek satış indirimi (asking -> sold) özeti,
- yeterli etiket varsa k-fold CV ile model vs naive doğruluğu üretir.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402


def build(labels_path: Path, kfe_path: Path, folds: int) -> tuple[str, int]:
    now = dt.datetime.now(dt.timezone.utc)
    out: list[str] = [
        "# sold — otomatik rapor",
        "",
        f"_Son güncelleme: {now:%Y-%m-%d %H:%M} UTC · GitHub Actions (bilgisayar gerekmez)_",
        "",
    ]

    # --- KFE (otomatik akan veri) ---
    if kfe_path.exists():
        kfe = pd.read_csv(kfe_path)
        if len(kfe):
            value_cols = [c for c in kfe.columns if c not in ("date", "Tarih")]
            last = kfe.iloc[-1]
            v = last[value_cols[0]] if value_cols else "?"
            out += [
                "## KFE — TCMB konut fiyat endeksi (ekspertiz tabanlı)",
                f"- Gözlem: **{len(kfe)} ay** · son ay **{last.get('Tarih', '?')} = {v}**",
                "- _Haftalık Action ile otomatik güncellenir — senin bir şey yapmana gerek yok._",
                "",
            ]

    # --- Gerçek satış etiketleri (senin eklediğin veri) ---
    n = 0
    frame = None
    if labels_path.exists():
        raw = pd.read_csv(labels_path)
        if {"asking_price", "sold_price"}.issubset(raw.columns):
            raw = raw.dropna(subset=["asking_price", "sold_price"])
            n = len(raw)
            if n:
                from sold.groundtruth.loader import to_feature_frame

                frame = to_feature_frame(raw)

    out += ["## Gerçek satış etiketleri (asking → sold)", f"- Toplam: **{n}**", ""]

    if n == 0:
        out += [
            "> Henüz gerçek satış yok. **`datasets/ground_truth.csv`** dosyasına (GitHub web",
            "> arayüzünden, telefondan bile) gerçek satışları ekledikçe bu rapor ve model",
            "> **kendiliğinden** güncellenir. Kazıma yoktur; etiketleri **sen** eklersin.",
            "",
        ]
    else:
        from sold.groundtruth.analyze import discount_summary

        summary = discount_summary(frame)
        overall = summary["overall"]
        out += [
            f"- Ortalama indirim: **%{overall['mean_pct']:.1f}** · "
            f"medyan **%{overall['median_pct']:.1f}** · "
            f"IQR %{overall['p25_pct']:.1f}–%{overall['p75_pct']:.1f}",
            "",
        ]
        by_district = summary["by_district"]
        if not by_district.empty:
            out += ["### İlçeye göre ortalama indirim", "", "| İlçe | İndirim | n |", "|---|---|---|"]
            for _, r in by_district.iterrows():
                out.append(f"| {r['district']} | %{r['mean']:.1f} | {int(r['count'])} |")
            out.append("")

        if n >= folds * 2:
            from sold.model.evaluate import cross_validate

            res = cross_validate(frame, folds=folds, seed=42)
            out += [
                f"### Model doğruluğu — {res['folds']}-kat CV ({res['n']} örnek)",
                "",
                f"- Naive (son ilan fiyatı): MAPE **%{res['naive_mape_mean']:.1f}** ± {res['naive_mape_std']:.1f}",
                f"- **Model (2 aşamalı): MAPE %{res['model_mape_mean']:.1f}** ± {res['model_mape_std']:.1f}",
                f"- İyileşme: **%{res['improvement_pct']:.0f}**",
                "",
            ]
        else:
            need = folds * 2
            out += [
                f"> Model doğrulaması (CV) için en az **{need}** etiket gerekir; şu an **{n}**.",
                "> Ekledikçe otomatik hesaplanır.",
                "",
            ]

    return "\n".join(out), n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="datasets/ground_truth.csv")
    parser.add_argument("--kfe", default="datasets/kfe.csv")
    parser.add_argument("--out", default="datasets/report.md")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    text, n = build(Path(args.labels), Path(args.kfe), args.folds)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Rapor yazildi: {out} (etiket={n})")

    # GitHub Actions çalıştırma özetine de yaz (Actions sekmesinde görünür)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
