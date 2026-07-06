"""sold komut satırı arayüzü (Typer)."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    help="sold — gerçekleşen konut fiyatı tahmini araç seti",
    no_args_is_help=True,
)
evds_app = typer.Typer(help="TCMB EVDS (Konut Fiyat Endeksi vb.)", no_args_is_help=True)
db_app = typer.Typer(help="Veritabanı işlemleri", no_args_is_help=True)
app.add_typer(evds_app, name="evds")
app.add_typer(db_app, name="db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# Güvenlik: httpx/httpcore INFO logları istek URL'sini (EVDS anahtarını) sızdırabilir.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# EVDS
# --------------------------------------------------------------------------- #
@evds_app.command("kfe")
def evds_kfe(
    start: str = typer.Option("01-01-2010", help="Başlangıç (DD-MM-YYYY)"),
    end: str = typer.Option("", help="Bitiş (DD-MM-YYYY); boşsa bugün"),
    out: Path = typer.Option(Path("data/kfe.csv"), help="Çıktı CSV yolu"),
    to_db: bool = typer.Option(False, "--to-db", help="Sonuçları veritabanına da yaz"),
) -> None:
    """Konut Fiyat Endeksi'ni (ekspertiz tabanlı) çekip CSV'ye kaydeder."""
    from .evds.client import EvdsAuthError, EvdsClient
    from .evds.kfe import fetch_kfe

    try:
        with EvdsClient() as client:
            df = fetch_kfe(client, start_date=start, end_date=end or dt.date.today())
    except EvdsAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if df.empty:
        typer.secho(
            "Veri gelmedi. Seri kodlarını `sold evds series bie_kfe` ile kontrol edin.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.secho(f"{len(df)} satır -> {out}", fg=typer.colors.GREEN)
    typer.echo(df.tail(6).to_string(index=False))

    if to_db:
        _store_evds(df)


@evds_app.command("house-sales")
def evds_house_sales(
    start: str = typer.Option("01-01-2013", help="Başlangıç (DD-MM-YYYY)"),
    end: str = typer.Option("", help="Bitiş (DD-MM-YYYY); boşsa bugün"),
    out: Path = typer.Option(Path("data/house_sales.csv"), help="Çıktı CSV yolu"),
    to_db: bool = typer.Option(False, "--to-db", help="Sonuçları veritabanına da yaz"),
) -> None:
    """TÜİK konut satış adetlerini (talep/likidite sinyali) çekip CSV'ye kaydeder."""
    from .evds.client import EvdsAuthError, EvdsClient
    from .evds.house_sales import build_name_map, fetch_house_sales, to_long
    from .evds.series import DEFAULT_HOUSE_SALES_SERIES

    try:
        with EvdsClient() as client:
            df = fetch_house_sales(
                client, start_date=start, end_date=end or dt.date.today()
            )
            name_map = build_name_map(client, list(DEFAULT_HOUSE_SALES_SERIES))
    except EvdsAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if df.empty:
        typer.secho(
            "Veri gelmedi. Kodları `sold evds series bie_akonutsat1` ile kontrol edin.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    long_df = to_long(df, name_map)
    out.parent.mkdir(parents=True, exist_ok=True)
    long_df.to_csv(out, index=False)
    typer.secho(f"{len(long_df)} satır -> {out}", fg=typer.colors.GREEN)
    typer.echo(long_df.tail(8).to_string(index=False))

    if to_db:
        _store_house_sales(long_df)


@evds_app.command("unit-prices")
def evds_unit_prices(
    start: str = typer.Option("01-01-2013", help="Başlangıç (DD-MM-YYYY)"),
    end: str = typer.Option("", help="Bitiş (DD-MM-YYYY); boşsa bugün"),
    out: Path = typer.Option(Path("data/unit_prices.csv"), help="Çıktı CSV yolu"),
) -> None:
    """TCMB ekspertiz TL/m² konut birim fiyatlarını (il bazında, GERÇEK) çeker."""
    from .evds.client import EvdsAuthError, EvdsClient
    from .evds.unit_prices import fetch_unit_prices

    try:
        with EvdsClient() as client:
            df = fetch_unit_prices(
                client, start_date=start, end_date=end or dt.date.today()
            )
    except EvdsAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if df.empty:
        typer.secho(
            "Veri gelmedi. Kodları `sold evds series bie_birimfiyat` ile kontrol edin.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    n_prov = df["province"].nunique()
    typer.secho(f"{len(df)} satır ({n_prov} il) -> {out}", fg=typer.colors.GREEN)
    typer.echo(df.tail(8).to_string(index=False))


@evds_app.command("series")
def evds_series(
    datagroup: str = typer.Argument("bie_kfe", help="Veri grubu kodu"),
    keyword: str = typer.Option("", help="İsim filtresi"),
) -> None:
    """Bir veri grubundaki serileri listeler (kesin KFE kodlarını doğrulamak için)."""
    from .evds.client import EvdsAuthError, EvdsClient

    try:
        with EvdsClient() as client:
            df = (
                client.search_series(datagroup, keyword)
                if keyword
                else client.list_series(datagroup)
            )
    except EvdsAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if df.empty:
        typer.secho("Seri bulunamadı.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.echo(df.to_string(index=False))


@evds_app.command("datagroups")
def evds_datagroups(keyword: str = typer.Option("", help="Metin filtresi")) -> None:
    """Tüm EVDS veri gruplarını listeler."""
    from .evds.client import EvdsAuthError, EvdsClient

    try:
        with EvdsClient() as client:
            df = client.list_datagroups()
    except EvdsAuthError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if keyword and not df.empty:
        mask = df.apply(
            lambda row: row.astype(str).str.contains(keyword, case=False, na=False).any(),
            axis=1,
        )
        df = df[mask]
    typer.echo(df.head(60).to_string(index=False))


def _store_evds(df) -> None:
    from sqlalchemy import select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import EvdsObservation

    engine = get_engine()
    init_db(engine)
    session_factory = get_sessionmaker(engine)
    value_cols = [c for c in df.columns if c not in ("date", "Tarih")]
    written = 0
    with session_factory() as session:
        existing = {
            (row.series_code, row.obs_date)
            for row in session.execute(
                select(EvdsObservation.series_code, EvdsObservation.obs_date)
            ).all()
        }
        for _, row in df.iterrows():
            obs_date = row["date"].date()
            for col in value_cols:
                value = row[col]
                if value != value or (col, obs_date) in existing:  # NaN veya var
                    continue
                session.add(
                    EvdsObservation(
                        series_code=col, obs_date=obs_date, value=float(value)
                    )
                )
                existing.add((col, obs_date))
                written += 1
        session.commit()
    typer.secho(f"{written} EVDS gözlemi veritabanına yazıldı.", fg=typer.colors.GREEN)


def _store_house_sales(long_df) -> None:
    import pandas as pd
    from sqlalchemy import select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import TuikHouseSale

    engine = get_engine()
    init_db(engine)
    session_factory = get_sessionmaker(engine)
    written = 0
    with session_factory() as session:
        existing = {
            (row.province, row.period, row.sale_type)
            for row in session.execute(
                select(
                    TuikHouseSale.province,
                    TuikHouseSale.period,
                    TuikHouseSale.sale_type,
                )
            ).all()
        }
        for _, row in long_df.iterrows():
            key = (row["province"], row["period"], row["sale_type"])
            count = row["sales_count"]
            if key in existing or pd.isna(count):
                continue
            session.add(
                TuikHouseSale(
                    province=row["province"],
                    period=row["period"],
                    sales_count=int(count),
                    sale_type=row["sale_type"],
                )
            )
            existing.add(key)
            written += 1
        session.commit()
    typer.secho(
        f"{written} konut satış gözlemi veritabanına yazıldı.", fg=typer.colors.GREEN
    )


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
@db_app.command("init")
def db_init() -> None:
    """Veritabanı tablolarını oluşturur (Postgres'te PostGIS şeması)."""
    from .db import get_engine, init_db

    engine = get_engine()
    init_db(engine)
    typer.secho(f"Veritabanı hazır: {engine.url}", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
# Scraper demo (yerel fixture; hiçbir siteye istek atmaz)
# --------------------------------------------------------------------------- #
@app.command("scrape-demo")
def scrape_demo(
    sample: Path = typer.Option(
        Path("samples/example_listing.html"), help="Örnek HTML dosyası"
    ),
) -> None:
    """Yerel örnek HTML ile scraper->pipeline hattını uçtan uca çalıştırır."""
    from .db import get_engine, get_sessionmaker, init_db
    from .scraper.example_parser import ExampleParser
    from .scraper.pipeline import ingest_records, mark_delisted

    html = sample.read_text(encoding="utf-8")
    with ExampleParser() as parser:
        record = parser.parse(html, url=str(sample))
    if record is None:
        typer.secho("Örnek ayrıştırılamadı.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        seen = ingest_records(session, [record])
        delisted = mark_delisted(session, ExampleParser.source_name, seen)
        session.commit()

    typer.secho(
        f"İşlendi: {record.source_listing_id} @ {record.price:,.0f} "
        f"{record.currency} | bu turda delisted={delisted}",
        fg=typer.colors.GREEN,
    )


# --------------------------------------------------------------------------- #
# Crawl (Faz 1) — longitudinal tarama
# --------------------------------------------------------------------------- #
crawl_app = typer.Typer(help="Longitudinal tarama (Faz 1)", no_args_is_help=True)
app.add_typer(crawl_app, name="crawl")


def _parse_iso_date(text: str) -> dt.datetime | None:
    if not text:
        return None
    return dt.datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)


def _adapter_kwargs(adapter: str, path: Path) -> dict:
    return {"path": path} if adapter == "local-example" else {}


@crawl_app.command("run")
def crawl_run_cmd(
    adapter: str = typer.Option("local-example", help="Adapter adı"),
    path: Path = typer.Option(Path("samples/site/day1"), help="local-example için dizin"),
    date: str = typer.Option("", help="Yakalama tarihi (YYYY-MM-DD); boşsa şimdi"),
    archive: bool = typer.Option(False, "--archive", help="Ham HTML'i data/raw altına gzip'le"),
) -> None:
    """Tek bir tarama turu çalıştırır."""
    from .db import get_engine, get_sessionmaker, init_db
    from .scraper.adapters import get_adapter
    from .scraper.crawler import crawl_once

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        with get_adapter(adapter, **_adapter_kwargs(adapter, path)) as site:
            run = crawl_once(session, site, captured_at=_parse_iso_date(date), archive=archive)
        session.commit()
        typer.secho(
            f"[{run.source}] görülen={run.listings_seen} yeni={run.new_listings} "
            f"fiyat_değişim={run.price_changes} delisted={run.delisted} durum={run.status}",
            fg=typer.colors.GREEN,
        )


@crawl_app.command("loop")
def crawl_loop_cmd(
    adapter: str = typer.Option("local-example", help="Adapter adı"),
    path: Path = typer.Option(Path("samples/site/day1"), help="local-example için dizin"),
    interval_hours: float = typer.Option(24.0, help="Turlar arası bekleme (saat)"),
    max_runs: int = typer.Option(0, help="0 = sonsuz"),
) -> None:
    """Belirli aralıkla tekrar eden tarama (bloklar).

    Üretimde bunun yerine Windows Task Scheduler / cron ile `sold crawl run`
    zamanlamanız önerilir.
    """
    import time

    from .db import get_engine, get_sessionmaker, init_db
    from .scraper.adapters import get_adapter
    from .scraper.crawler import crawl_once

    engine = get_engine()
    init_db(engine)
    runs = 0
    while True:
        with get_sessionmaker(engine)() as session:
            with get_adapter(adapter, **_adapter_kwargs(adapter, path)) as site:
                run = crawl_once(session, site)
            session.commit()
            typer.secho(
                f"[tur {runs + 1}] görülen={run.listings_seen} yeni={run.new_listings} "
                f"fiyat_değişim={run.price_changes} delisted={run.delisted}",
                fg=typer.colors.GREEN,
            )
        runs += 1
        if max_runs and runs >= max_runs:
            break
        time.sleep(interval_hours * 3600)


@crawl_app.command("stats")
def crawl_stats_cmd() -> None:
    """Tarama turlarını ve biriken longitudinal sinyalleri özetler."""
    from sqlalchemy import func, select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import CrawlRun, Listing, PriceChange

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        runs = session.scalars(select(CrawlRun).order_by(CrawlRun.started_at)).all()
        if not runs:
            typer.secho("Henüz tarama turu yok. `sold crawl run` deneyin.", fg=typer.colors.YELLOW)
            raise typer.Exit()

        typer.echo("Tarama turları:")
        for r in runs:
            stamp = r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "-"
            typer.echo(
                f"  {stamp} [{r.source}] görülen={r.listings_seen} yeni={r.new_listings} "
                f"fiyat_değişim={r.price_changes} delisted={r.delisted} ({r.status})"
            )

        total = session.scalar(select(func.count()).select_from(Listing)) or 0
        active = (
            session.scalar(
                select(func.count()).select_from(Listing).where(Listing.status == "active")
            )
            or 0
        )
        delisted = (
            session.scalar(
                select(func.count()).select_from(Listing).where(Listing.status == "delisted")
            )
            or 0
        )
        drops = session.scalars(
            select(PriceChange.pct_change).where(PriceChange.pct_change < 0)
        ).all()
        avg_drop = sum(drops) / len(drops) if drops else 0.0

        typer.echo("")
        typer.secho(
            f"İlan: toplam={total} aktif={active} delisted={delisted}",
            fg=typer.colors.CYAN,
        )
        typer.secho(
            f"Fiyat düşüşü gözlemi: {len(drops)} adet, ort. {avg_drop:.1f}% "
            f"(asking-side indirim sinyali)",
            fg=typer.colors.CYAN,
        )


@crawl_app.command("clear")
def crawl_clear_cmd(
    source: str = typer.Option("", help="Sadece bu kaynağı sil (boşsa TÜM ilanlar)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onay sorma"),
) -> None:
    """İlan (crawl) verisini siler: listings + snapshots + price_changes + crawl_runs. Geri alınamaz."""
    from sqlalchemy import delete, select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import CrawlRun, Listing, ListingSnapshot, PriceChange

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        id_stmt = select(Listing.id)
        if source:
            id_stmt = id_stmt.where(Listing.source == source)
        ids = [row[0] for row in session.execute(id_stmt).all()]
        n = len(ids)
        if n == 0:
            typer.secho("Silinecek ilan yok.", fg=typer.colors.YELLOW)
            raise typer.Exit()

        target = f"kaynak='{source}'" if source else "TÜM kaynaklar"
        if not yes:
            typer.confirm(
                f"{n} ilan (+snapshot/fiyat değişimi) silinecek ({target}). Emin misin?",
                abort=True,
            )

        session.execute(delete(PriceChange).where(PriceChange.listing_id.in_(ids)))
        session.execute(delete(ListingSnapshot).where(ListingSnapshot.listing_id.in_(ids)))
        session.execute(delete(Listing).where(Listing.id.in_(ids)))
        run_stmt = delete(CrawlRun)
        if source:
            run_stmt = run_stmt.where(CrawlRun.source == source)
        session.execute(run_stmt)
        session.commit()

    typer.secho(f"{n} ilan silindi ({target}).", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------- #
# Model (Faz 2-3) — realized fiyat tahmin motoru
# --------------------------------------------------------------------------- #
model_app = typer.Typer(
    help="Realized fiyat tahmini (hedonik + indirim modeli)", no_args_is_help=True
)
app.add_typer(model_app, name="model")


@model_app.command("demo")
def model_demo_cmd(
    n: int = typer.Option(2500, help="Sentetik ilan sayısı"),
    seed: int = typer.Option(42, help="Rastgelelik tohumu"),
    test_size: float = typer.Option(0.25, help="Test oranı"),
) -> None:
    """ML YÖNTEMİNİ simüle veriyle doğrular (gerçek tahmin DEĞİL; onun için `sold serve`)."""
    import numpy as np

    from .model.calibrate import mape, median_ape
    from .model.estimator import RealizedValuator
    from .model.synthetic import generate_market

    df = generate_market(n, seed)
    rng = np.random.default_rng(seed)
    is_train = rng.random(len(df)) < (1 - test_size)
    train, test = df[is_train].copy(), df[~is_train].copy()

    valuator = RealizedValuator.train(train)
    estimate = valuator.estimate(test)

    t = test.reset_index(drop=True)
    y_true = t["true_realized_price"].to_numpy(float)
    naive_initial = t["initial_price"].to_numpy(float)
    naive_last = t["last_price"].to_numpy(float)

    typer.secho(
        "\nML yöntem doğrulaması — SİMÜLE veri (gerçek tahmin DEĞİL)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo("  Gerçek tahmin için: `sold serve` ya da `sold model value` (gerçek TCMB/pazarlık verisi)")
    typer.echo(f"  Eğitim/Test: {len(train)}/{len(t)} ilan\n")
    typer.echo(f"  {'Yöntem':<26}{'MAPE':>8}{'MedAPE':>9}")
    typer.echo("  " + "-" * 43)
    typer.echo(
        f"  {'Naive (ilk ilan fiyatı)':<26}"
        f"{mape(y_true, naive_initial):>7.1f}%{median_ape(y_true, naive_initial):>8.1f}%"
    )
    typer.echo(
        f"  {'Naive (son ilan fiyatı)':<26}"
        f"{mape(y_true, naive_last):>7.1f}%{median_ape(y_true, naive_last):>8.1f}%"
    )
    typer.secho(
        f"  {'Model (2 aşamalı)':<26}"
        f"{mape(y_true, estimate):>7.1f}%{median_ape(y_true, estimate):>8.1f}%",
        fg=typer.colors.GREEN,
    )

    typer.echo("\n  Örnek (son ilan fiyatı → tahmini gerçekleşen | gerçek):")
    for i in range(min(5, len(t))):
        last = float(t.loc[i, "last_price"])
        true = float(t.loc[i, "true_realized_price"])
        pred = float(estimate[i])
        disc = (1 - pred / last) * 100 if last else 0.0
        typer.echo(
            f"    {last:>12,.0f} → {pred:>12,.0f}  (%{disc:4.1f} indirim) | gerçek {true:>12,.0f}"
        )


@model_app.command("train")
def model_train_cmd(
    source: str = typer.Option("synthetic", help="'synthetic' veya 'db'"),
    n: int = typer.Option(4000, help="synthetic için ilan sayısı"),
    seed: int = typer.Option(42, help="Rastgelelik tohumu"),
    out: Path = typer.Option(Path("data/models/valuator.joblib"), help="Model çıktısı"),
) -> None:
    """Modeli eğitip diske kaydeder."""
    from .model.estimator import RealizedValuator

    if source == "db":
        from .db import get_engine, get_sessionmaker, init_db
        from .features.build import build_feature_frame

        engine = get_engine()
        init_db(engine)
        with get_sessionmaker(engine)() as session:
            df = build_feature_frame(session)
        if df.empty:
            typer.secho(
                "Veritabanında ilan yok. Önce `sold crawl run`.", fg=typer.colors.YELLOW
            )
            raise typer.Exit(code=1)
        if "true_realized_price" not in df.columns or df["true_realized_price"].isna().all():
            typer.secho(
                "Etiket (gerçek satış) yok → yalnızca hedonik + sabit indirim (%5). "
                "İndirim modeli için etiketli veri (broker/ekspertiz) gerekir.",
                fg=typer.colors.YELLOW,
            )
            valuator = RealizedValuator.train_hedonic_only(df, residual_bargain=0.05)
        else:
            valuator = RealizedValuator.train(df)
    else:
        from .model.synthetic import generate_market

        valuator = RealizedValuator.train(generate_market(n, seed))

    saved = valuator.save(out)
    typer.secho(f"Model kaydedildi: {saved}", fg=typer.colors.GREEN)


@model_app.command("estimate")
def model_estimate_cmd(
    model: Path = typer.Option(Path("data/models/valuator.joblib"), help="Kayıtlı model"),
    out: Path = typer.Option(Path("data/estimates.csv"), help="Çıktı CSV"),
) -> None:
    """Kayıtlı modelle veritabanındaki ilanların gerçekleşen fiyatını tahmin eder."""
    from .db import get_engine, get_sessionmaker, init_db
    from .features.build import build_feature_frame
    from .model.estimator import RealizedValuator

    if not model.exists():
        typer.secho(
            f"Model bulunamadı: {model}. Önce `sold model train`.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)

    valuator = RealizedValuator.load(model)
    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        df = build_feature_frame(session)
    if df.empty:
        typer.secho("İlan yok. Önce `sold crawl run`.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    df["realized_estimate"] = valuator.estimate(df)
    df["implied_discount_pct"] = (1 - df["realized_estimate"] / df["last_price"]) * 100
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    typer.secho(f"{len(df)} tahmin -> {out}", fg=typer.colors.GREEN)
    cols = [
        c
        for c in (
            "source_listing_id",
            "district",
            "gross_m2",
            "last_price",
            "realized_estimate",
            "implied_discount_pct",
            "days_on_market",
            "is_delisted",
        )
        if c in df.columns
    ]
    typer.echo(df[cols].head(10).to_string(index=False))


@model_app.command("value")
def model_value_cmd(
    asking: float = typer.Argument(..., help="İlan (istenen) fiyat, TL"),
    province: str = typer.Option("İstanbul", help="İl"),
    gross_m2: float = typer.Option(0.0, help="Brüt m² (0=bilinmiyor)"),
) -> None:
    """GERÇEK veriyle satış tahmini (yayınlı pazarlık payı + TCMB TL/m²; MOCK yok)."""
    import pandas as pd

    from .model.valuation import RealValuator, effective_discount

    rv = RealValuator()
    frame = pd.DataFrame(
        [{"province": province, "last_price": asking, "gross_m2": gross_m2 or None}]
    )
    sold = float(rv.estimate(frame)[0])
    disc = effective_discount(province)
    avg = rv.province_ppm2(province)
    suffix = f", {gross_m2:.0f} m²" if gross_m2 else ""
    typer.secho(f"İlan: {asking:,.0f} TL  ({province}{suffix})", fg=typer.colors.CYAN)
    typer.secho(
        f"Tahmini satış: {sold:,.0f} TL   (yayınlı pazarlık ~%{disc * 100:.0f})",
        fg=typer.colors.GREEN,
        bold=True,
    )
    if gross_m2 and avg:
        listing_ppm2 = asking / gross_m2
        rel = (listing_ppm2 / avg - 1) * 100
        yon = "üstünde" if rel >= 0 else "altında"
        typer.echo(
            f"Bu ilan: {listing_ppm2:,.0f} TL/m²  ·  {province} ort. (TCMB): {avg:,.0f} TL/m²"
        )
        typer.echo(f"→ İlan, il ortalamasının %{abs(rel):.0f} {yon}.")
    typer.echo("Kaynak: TCMB TL/m² + yayınlı pazarlık + TÜİK talep — uydurma yok.")


@model_app.command("features")
def model_features_cmd(
    out: Path = typer.Option(Path("data/features.csv"), help="Çıktı CSV"),
) -> None:
    """Veritabanından özellik (feature) tablosunu üretir."""
    from .db import get_engine, get_sessionmaker, init_db
    from .features.build import build_feature_frame

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        df = build_feature_frame(session)
    if df.empty:
        typer.secho("İlan yok. Önce `sold crawl run`.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.secho(f"{len(df)} satır x {df.shape[1]} sütun -> {out}", fg=typer.colors.GREEN)
    typer.echo(df.head().to_string(index=False))


# --------------------------------------------------------------------------- #
# Ground-truth (Faz 4) — gerçek etiketler
# --------------------------------------------------------------------------- #
gt_app = typer.Typer(
    help="Gerçek (broker/ekspertiz) etiketleri (Faz 4)", no_args_is_help=True
)
app.add_typer(gt_app, name="gt")


@gt_app.command("template")
def gt_template_cmd(
    out: Path = typer.Option(
        Path("data/ground_truth_template.csv"), help="Şablon çıktısı"
    ),
) -> None:
    """Beklenen ground-truth CSV şablonunu yazar."""
    from .groundtruth import write_template

    path = write_template(out)
    typer.secho(f"Şablon yazıldı: {path}", fg=typer.colors.GREEN)


@gt_app.command("demo")
def gt_demo_cmd(
    out: Path = typer.Option(Path("data/ground_truth_demo.csv"), help="Çıktı CSV"),
    n: int = typer.Option(500, help="Kayıt sayısı"),
    seed: int = typer.Option(123, help="Rastgelelik tohumu"),
) -> None:
    """Gerçekçi bir DEMO ground-truth CSV üretir (gerçek broker verisi taklidi)."""
    from .groundtruth import make_demo

    df = make_demo(n, seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    typer.secho(f"{len(df)} satır demo ground-truth -> {out}", fg=typer.colors.GREEN)


@gt_app.command("import")
def gt_import_cmd(
    csv: Path = typer.Option(..., help="Broker/ekspertiz CSV yolu"),
    source: str = typer.Option("", help="Kaynak etiketi (opsiyonel)"),
) -> None:
    """Broker/ekspertiz CSV'sini veritabanına aktarır."""
    from .db import get_engine, get_sessionmaker, init_db
    from .groundtruth import persist_to_db, read_csv

    df = read_csv(csv)
    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        added = persist_to_db(session, df, source=source or None)
        session.commit()
    typer.secho(f"{added} gerçek satış içe aktarıldı.", fg=typer.colors.GREEN)


@gt_app.command("list")
def gt_list_cmd() -> None:
    """Veritabanındaki ground-truth kayıtlarını (kaynağa göre) özetler."""
    from sqlalchemy import func, select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import GroundTruthSale

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        total = session.scalar(select(func.count()).select_from(GroundTruthSale)) or 0
        by_source = session.execute(
            select(GroundTruthSale.source, func.count())
            .group_by(GroundTruthSale.source)
            .order_by(func.count().desc())
        ).all()
    typer.secho(f"Ground-truth kayıt sayısı: {total}", fg=typer.colors.CYAN)
    for src, cnt in by_source:
        typer.echo(f"  {src or '(kaynaksız)'}: {cnt}")


@gt_app.command("clear")
def gt_clear_cmd(
    source: str = typer.Option("", help="Sadece bu kaynağı sil (boşsa TÜM etiketler)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onay sorma"),
) -> None:
    """Ground-truth etiketlerini siler (kaynağa göre veya tümü). DİKKAT: geri alınamaz."""
    from sqlalchemy import delete, func, select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import GroundTruthSale

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        count_stmt = select(func.count()).select_from(GroundTruthSale)
        if source:
            count_stmt = count_stmt.where(GroundTruthSale.source == source)
        n = session.scalar(count_stmt) or 0
        if n == 0:
            typer.secho("Silinecek kayıt yok.", fg=typer.colors.YELLOW)
            raise typer.Exit()

        target = f"kaynak='{source}'" if source else "TÜM kaynaklar"
        if not yes:
            typer.confirm(f"{n} etiket silinecek ({target}). Emin misin?", abort=True)

        delete_stmt = delete(GroundTruthSale)
        if source:
            delete_stmt = delete_stmt.where(GroundTruthSale.source == source)
        session.execute(delete_stmt)
        session.commit()

    typer.secho(f"{n} etiket silindi ({target}).", fg=typer.colors.GREEN)


@gt_app.command("analyze")
def gt_analyze_cmd() -> None:
    """Gerçek etiketlerden asking→sold indirim (gerçekleşen fark) dağılımını çıkarır."""
    from .db import get_engine, get_sessionmaker, init_db
    from .groundtruth import discount_summary, load_frame_from_db

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        frame = load_frame_from_db(session)
    if frame.empty:
        typer.secho("Ground-truth yok. Önce `sold gt import`.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    summary = discount_summary(frame)
    overall = summary["overall"]
    typer.secho(
        f"\nGerçek indirim (asking→sold) — {overall['count']} etiketli satış",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(
        f"  Ortalama %{overall['mean_pct']:.1f} | Medyan %{overall['median_pct']:.1f} "
        f"| IQR %{overall['p25_pct']:.1f}–%{overall['p75_pct']:.1f}"
    )

    by_district = summary["by_district"]
    if not by_district.empty:
        typer.secho("\n  İlçeye göre ortalama indirim:", fg=typer.colors.CYAN)
        for _, row in by_district.iterrows():
            typer.echo(
                f"    {str(row['district']):<18} %{row['mean']:5.1f}  (n={int(row['count'])})"
            )

    by_band = summary["by_price_band"]
    if not by_band.empty:
        typer.secho("\n  Fiyat bandına göre ortalama indirim:", fg=typer.colors.CYAN)
        for _, row in by_band.iterrows():
            if int(row["count"]) == 0:
                continue
            typer.echo(
                f"    {str(row['price_band']):<10} %{row['mean']:5.1f}  (n={int(row['count'])})"
            )


@gt_app.command("add")
def gt_add_cmd(
    asking: float = typer.Option(..., help="İlan (asking) fiyatı, TL"),
    sold: float = typer.Option(..., help="Gerçekleşen satış fiyatı, TL"),
    district: str = typer.Option("", help="İlçe"),
    gross_m2: Optional[float] = typer.Option(None, help="Brüt m²"),
    room_count: str = typer.Option("", help="Oda (ör. 2+1)"),
    building_age: Optional[int] = typer.Option(None, help="Bina yaşı"),
    floor: Optional[int] = typer.Option(None, help="Kat"),
    heating: str = typer.Option("", help="Isıtma"),
    province: str = typer.Option("İstanbul", help="İl"),
    neighborhood: str = typer.Option("", help="Mahalle"),
    source: str = typer.Option("manual", help="Kaynak etiketi"),
    sale_date: str = typer.Option("", help="Satış tarihi (YYYY-MM-DD)"),
) -> None:
    """Tek bir GERÇEK satışı (etiket) veritabanına ekler."""
    import pandas as pd
    from sqlalchemy import func, select

    from .db import get_engine, get_sessionmaker, init_db
    from .db.models import GroundTruthSale
    from .groundtruth import persist_to_db

    row = {
        "source": source,
        "listing_type": "sale",
        "province": province or None,
        "district": district or None,
        "neighborhood": neighborhood or None,
        "gross_m2": gross_m2,
        "room_count": room_count or None,
        "building_age": building_age,
        "floor": floor,
        "heating": heating or None,
        "asking_price": asking,
        "sold_price": sold,
        "sale_date": sale_date or None,
    }
    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        persist_to_db(session, pd.DataFrame([row]), source=source)
        session.commit()
        total = session.scalar(select(func.count()).select_from(GroundTruthSale)) or 0

    discount = (1 - sold / asking) * 100 if asking else 0.0
    typer.secho(
        f"Eklendi: {district or '-'} asking {asking:,.0f} → sold {sold:,.0f} "
        f"(%{discount:.1f} indirim). Toplam etiket: {total}",
        fg=typer.colors.GREEN,
    )


@model_app.command("evaluate")
def model_evaluate_cmd(
    source: str = typer.Option("gt", help="'gt' (DB etiketleri) veya 'synthetic'"),
    folds: int = typer.Option(5, help="k-fold CV kat sayısı"),
    n: int = typer.Option(2000, help="synthetic için ilan sayısı"),
    seed: int = typer.Option(42, help="Rastgelelik tohumu"),
) -> None:
    """k-fold çapraz doğrulama ile model vs naive doğruluğunu raporlar."""
    from .model.evaluate import cross_validate

    if source == "synthetic":
        from .model.synthetic import generate_market

        frame = generate_market(n, seed)
    else:
        from .db import get_engine, get_sessionmaker, init_db
        from .groundtruth import load_frame_from_db

        engine = get_engine()
        init_db(engine)
        with get_sessionmaker(engine)() as session:
            frame = load_frame_from_db(session)
        if frame.empty:
            typer.secho(
                "Ground-truth yok. Önce `sold gt demo` + `sold gt import`.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=1)

    if len(frame) < folds * 2:
        typer.secho(
            f"Çok az örnek ({len(frame)}); --folds değerini azaltın.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    result = cross_validate(frame, folds=folds, seed=seed)
    typer.secho(
        f"\n{result['folds']}-kat CV — {result['n']} etiketli örnek",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(
        f"  Naive (son ilan):  MAPE {result['naive_mape_mean']:.1f}% "
        f"± {result['naive_mape_std']:.1f}   (MedAPE {result['naive_medape_mean']:.1f}%)"
    )
    typer.secho(
        f"  Model (2 aşamalı): MAPE {result['model_mape_mean']:.1f}% "
        f"± {result['model_mape_std']:.1f}   (MedAPE {result['model_medape_mean']:.1f}%)",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  İyileşme: %{result['improvement_pct']:.0f}")


@model_app.command("calibrate")
def model_calibrate_cmd(
    estimates: Path = typer.Option(..., help="period + value sütunlu CSV"),
    anchor: str = typer.Option(..., help="Çapa dönem (ör. 2026-01)"),
    kfe: Path = typer.Option(None, help="period + index sütunlu KFE CSV (opsiyonel)"),
    kfe_from_db: bool = typer.Option(
        False, "--kfe-from-db", help="KFE'yi veritabanından al (Faz 0'da çekilen GERÇEK)"
    ),
    kfe_series: str = typer.Option("TP.KFE.TR", help="DB'den alınacak KFE seri kodu"),
    period_col: str = typer.Option("period", help="Tahmin CSV'sinde dönem sütunu"),
    value_col: str = typer.Option("value", help="Tahmin CSV'sinde değer sütunu"),
    kfe_period_col: str = typer.Option("period", help="KFE CSV dönem sütunu"),
    kfe_value_col: str = typer.Option("index", help="KFE CSV endeks sütunu"),
    out: Path = typer.Option(Path("data/calibrated.csv"), help="Çıktı CSV"),
) -> None:
    """Dönemsel realized ortalamalarını KFE (ekspertiz) büyümesine oturtur."""
    import pandas as pd

    from .model.calibrate import align_growth_to_kfe, load_kfe_from_db, period_means

    est_df = pd.read_csv(estimates)
    means = period_means(est_df, period_col, value_col)

    if kfe_from_db:
        from .db import get_engine, get_sessionmaker, init_db

        engine = get_engine()
        init_db(engine)
        with get_sessionmaker(engine)() as session:
            kfe_map = load_kfe_from_db(session, kfe_series)
        if not kfe_map:
            typer.secho(
                f"DB'de '{kfe_series}' KFE gözlemi yok. Önce `sold evds kfe --to-db`.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=1)
    elif kfe is not None:
        kfe_df = pd.read_csv(kfe)
        kfe_map = {
            str(p): float(v)
            for p, v in zip(kfe_df[kfe_period_col].astype(str), kfe_df[kfe_value_col])
        }
    else:
        typer.secho("--kfe VEYA --kfe-from-db verin.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    aligned = align_growth_to_kfe(means, kfe_map, anchor=str(anchor))
    result = pd.DataFrame(
        {
            "period": list(aligned),
            "raw_mean": [means[p] for p in aligned],
            "kfe_aligned": [aligned[p] for p in aligned],
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    source_label = f"DB-KFE {kfe_series}" if kfe_from_db else "CSV-KFE"
    typer.secho(f"Kalibre edildi ({source_label}) -> {out}", fg=typer.colors.GREEN)
    typer.echo(result.to_string(index=False))


# --------------------------------------------------------------------------- #
# Broker veri flywheel (ilan sonucu + müzakere analitiği)
# --------------------------------------------------------------------------- #
flywheel_app = typer.Typer(
    help="Broker veri flywheel: ilan sonucu toplama + müzakere analitiği",
    no_args_is_help=True,
)
app.add_typer(flywheel_app, name="flywheel")


@flywheel_app.command("record")
def flywheel_record_cmd(
    outcome: str = typer.Argument(
        ..., help="sold/withdrawn/expired/active/lost_to_other/unknown"
    ),
    province: str = typer.Option("İstanbul", help="İl"),
    district: str = typer.Option("", help="İlçe"),
    gross_m2: float = typer.Option(0.0, help="Brüt m²"),
    initial_asking: float = typer.Option(0.0, help="İlk ilan fiyatı, TL"),
    last_asking: float = typer.Option(0.0, help="Son ilan fiyatı, TL"),
    price_cuts: int = typer.Option(0, help="Fiyat düşürme sayısı"),
    days_on_market: int = typer.Option(0, help="İlanda kalma (gün)"),
    sold_price: float = typer.Option(0.0, help="Satış fiyatı (YALNIZCA outcome=sold)"),
    days_to_close: int = typer.Option(0, help="Kapanışa kadar gün (yalnızca sold)"),
    sale_mode: str = typer.Option("arm_length", help="arm_length/auction/related_party/unknown"),
    source: str = typer.Option("broker", help="Broker kimliği"),
    label_source: str = typer.Option("", help="broker_closing/bank_transfer_observed/deed_declared/..."),
    evidence_type: str = typer.Option("none", help="none/screenshot/contract/bank_receipt/deed"),
    verified: bool = typer.Option(False, "--verified", help="Bağımsız doğrulandı (güven A)"),
) -> None:
    """Bir ilan SONUCU kaydeder (sadece satış değil). Kapanış alanları yalnızca 'sold'da geçerli."""
    from .db import get_engine, get_sessionmaker, init_db
    from .flywheel import OutcomeError, record_outcome

    rec = {
        "outcome": outcome,
        "province": province,
        "district": district or None,
        "gross_m2": gross_m2 or None,
        "initial_asking_price": initial_asking or None,
        "last_asking_price": last_asking or None,
        "price_cut_count": price_cuts,
        "days_on_market": days_on_market or None,
        "sold_price": sold_price or None,
        "days_to_close": days_to_close or None,
        "sale_mode": sale_mode,
        "source": source,
        "label_source": label_source or None,
        "evidence_type": evidence_type,
        "evidence_verified": verified,
    }
    engine = get_engine()
    init_db(engine)
    try:
        with get_sessionmaker(engine)() as session:
            row = record_outcome(session, rec)
            session.commit()
            conf = row.label_confidence
    except OutcomeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Kaydedildi: outcome={outcome} · güven={conf}", fg=typer.colors.GREEN)
    if outcome == "sold" and conf != "A":
        typer.echo("Not: öz-beyan güveni B; bağımsız doğrulamada --verified ile A olur.")


@flywheel_app.command("analytics")
def flywheel_analytics_cmd(
    source: str = typer.Option("", help="Sadece bu broker (boş = tümü)"),
) -> None:
    """Müzakere analitiği (NON-ML): indirim, kapanış süresi, fiyat-kesinti, işlem sayısı."""
    from .db import get_engine, get_sessionmaker, init_db
    from .flywheel import load_outcomes, negotiation_analytics

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        df = load_outcomes(session, source or None)

    a = negotiation_analytics(df)
    if a["transaction_count"] == 0:
        typer.secho(
            f"arm_length satış yok (toplam sonuç: {a['total_outcomes']}).",
            fg=typer.colors.YELLOW,
        )
        if a["outcome_counts"]:
            typer.echo(f"  Sonuç dağılımı: {a['outcome_counts']}")
        raise typer.Exit()

    typer.secho("Müzakere analitiği — GERÇEK, NON-ML", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"  İşlem (arm_length satış): {a['transaction_count']} / toplam sonuç {a['total_outcomes']}"
    )
    typer.echo(
        f"  İlan→satış indirimi: medyan %{a['median_discount_pct']:.1f} · "
        f"ortalama %{a['mean_discount_pct']:.1f}"
    )
    typer.echo(
        f"  Kapanış süresi: medyan {a['median_days_to_close']:.0f} gün · "
        f"ortalama {a['mean_days_to_close']:.0f} gün"
    )
    typer.echo(
        f"  Fiyat düşürme: medyan {a['median_price_cuts']:.1f} · "
        f"ortalama {a['mean_price_cuts']:.1f}"
    )
    for key, label in (("no_cut", "fiyat düşürmeyen"), ("with_cut", "fiyat düşüren")):
        d = a["discount_by_price_cut"].get(key)
        if d:
            typer.echo(
                f"    {label}: n={d['count']} · ort. indirim %{d['mean_discount_pct']:.1f}"
            )
    typer.echo(f"  Sonuç dağılımı: {a['outcome_counts']}")


# --------------------------------------------------------------------------- #
# Public Label Miner (kamu domain'lerinden gerçekleşen-fiyat etiketi)
# --------------------------------------------------------------------------- #
labels_app = typer.Typer(
    help="Public Label Miner: UYAP/KAP/TOKİ resmî kayıtlarından etiket + domain ayrımı",
    no_args_is_help=True,
)
app.add_typer(labels_app, name="labels")


@labels_app.command("mine")
def labels_mine_cmd(
    source: str = typer.Argument(..., help="uyap / kap / toki"),
    file: Path = typer.Option(..., "--file", help="JSON (kayıt listesi) veya CSV"),
    to_db: bool = typer.Option(False, "--to-db", help="Etiketleri veritabanına yaz"),
) -> None:
    """Bir kamu kaynağının RESMÎ kayıtlarını etikete çevirir (canlı kazıma YOK)."""
    from .labels import LabelError, PublicLabelMiner, persist_labels

    try:
        labels = PublicLabelMiner().mine_file(source, file)
    except LabelError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if not labels:
        typer.secho("Uygun etiket bulunamadı.", fg=typer.colors.YELLOW)
        raise typer.Exit()

    typer.secho(f"{len(labels)} etiket üretildi ({source}).", fg=typer.colors.GREEN)
    for lab in labels[:5]:
        ref = lab.get("reference_price") or 0
        typer.echo(
            f"  {lab['sale_mechanism']:<20} {lab['reference_price_type']:<10} "
            f"ref={ref:>14,.0f} → gerçekleşen={lab['realized_price']:>14,.0f} "
            f"[güven {lab['label_confidence']}]"
        )
    if to_db:
        from .db import get_engine, get_sessionmaker, init_db

        engine = get_engine()
        init_db(engine)
        with get_sessionmaker(engine)() as session:
            n = persist_labels(session, labels)
            session.commit()
        typer.secho(f"{n} etiket veritabanına yazıldı.", fg=typer.colors.GREEN)


@labels_app.command("stats")
def labels_stats_cmd() -> None:
    """Etiketleri özetler + DOMAIN AYRIMINI gösterir (asking→closing vs FairValue)."""
    from .db import get_engine, get_sessionmaker, init_db
    from .labels import asking_to_closing_labels, fair_value_labels, load_labels

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        df = load_labels(session)

    if df.empty:
        typer.secho(
            "Etiket yok. `sold labels mine <kaynak> --file ... --to-db`",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit()

    typer.secho(f"Toplam gerçekleşen-fiyat etiketi: {len(df)}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Domain: {df['domain'].value_counts().to_dict()}")
    typer.echo(f"  Mekanizma: {df['sale_mechanism'].value_counts().to_dict()}")
    typer.echo(f"  Kaynak: {df['label_source'].value_counts().to_dict()}")
    typer.echo(f"  Güven: {df['label_confidence'].value_counts().to_dict()}")
    a2c = asking_to_closing_labels(df)
    fv = fair_value_labels(df)
    typer.secho("  Domain ayrımı (KARIŞTIRILMAZ):", fg=typer.colors.CYAN)
    typer.echo(f"    asking→closing head (yalnızca doğrudan closing): {len(a2c)}")
    typer.echo(f"    FairValue→realized kalibrasyonu (appraisal/reserve): {len(fv)}")


# --------------------------------------------------------------------------- #
# Tüketici (öz-beyan) satış toplayıcı — sıradan konutta DOĞRUDAN asking→closing
# --------------------------------------------------------------------------- #
consumer_app = typer.Typer(
    help="Tüketici satış toplayıcı: ev satmış kişiden DOĞRUDAN asking→closing etiketi",
    no_args_is_help=True,
)
app.add_typer(consumer_app, name="consumer")


@consumer_app.command("record")
def consumer_record_cmd(
    final_asking: float = typer.Option(..., "--final-asking", help="SON ilan fiyatı, TL (zorunlu)"),
    closing: float = typer.Option(..., "--closing", help="Gerçekleşen satış fiyatı, TL (zorunlu)"),
    initial_asking: float = typer.Option(0.0, "--initial-asking", help="İLK ilan fiyatı, TL"),
    province: str = typer.Option("İstanbul", help="İl"),
    district: str = typer.Option("", help="İlçe"),
    property_type: str = typer.Option("konut", help="Taşınmaz türü"),
    gross_m2: float = typer.Option(0.0, help="Brüt m² (biliniyorsa)"),
    room_count: str = typer.Option("", help="Oda sayısı (örn. 2+1)"),
    price_cuts: int = typer.Option(0, help="Fiyat düşürme sayısı"),
    listing_date: str = typer.Option("", help="İlan tarihi (YYYY-MM-DD)"),
    closing_date: str = typer.Option("", help="Kapanış tarihi (YYYY-MM-DD)"),
    origin: str = typer.Option(
        "consumer_submission",
        "--origin",
        help=(
            "Köken: consumer_submission (YALNIZCA gerçek satıcının kendi gönderimi) / "
            "test_fixture / demo_seed / manual_import (geliştirici-aktarımı geçmiş veri). "
            "Fixture/demo/aktarım ASLA consumer_submission olarak etiketlenmez."
        ),
    ),
) -> None:
    """Ev satmış bir kişinin satışını kaydeder; anında analitik + segment benchmark döner.

    KVKK: yalnızca nesnel taşınmaz + fiyat/tarih toplanır (ad/adres/tapu/dekont YOK).
    Kayıt provenance-aware DOĞRUDAN etikete çevrilir ve asking→closing head'ine girer.
    """
    from .consumer import (
        ConsumerSaleError,
        record_consumer_sale,
        sale_analytics,
        sale_as_dict,
        segment_benchmark,
    )
    from .db import get_engine, get_sessionmaker, init_db

    raw = {
        "final_asking_price": final_asking,
        "closing_price": closing,
        "initial_asking_price": initial_asking or None,
        "province": province,
        "district": district or None,
        "property_type": property_type,
        "gross_m2": gross_m2 or None,
        "room_count": room_count or None,
        "price_cut_count": price_cuts,
        "listing_date": listing_date or None,
        "closing_date": closing_date or None,
    }
    engine = get_engine()
    init_db(engine)
    try:
        with get_sessionmaker(engine)() as session:
            row = record_consumer_sale(session, raw, origin=origin)
            session.commit()
            sale = sale_as_dict(row)
            analytics = sale_analytics(sale)
            bench = segment_benchmark(session, sale)
            status = row.quality_status
            flags = list(row.quality_flags or [])
    except ConsumerSaleError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    is_genuine = origin == "consumer_submission"
    enters_head = is_genuine and status == "accepted"
    typer.secho(
        "Kaydedildi — DOĞRUDAN etiket üretildi "
        "(domain=consumer · seller_self_reported · ordinary_resale · güven B).",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  Köken: {origin}  ·  Kalite: {status}")
    if flags:
        typer.secho(f"  İnceleme bayrakları: {', '.join(flags)}", fg=typer.colors.YELLOW)
    if enters_head:
        typer.secho(
            "  → asking→closing head'ine GİRDİ (GERÇEK, accepted).", fg=typer.colors.GREEN
        )
    elif not is_genuine:
        typer.secho(
            "  → test/demo/manuel köken: varsayılan a2c'ye GİRMEZ (genuine sayısını şişirmez).",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            "  → flagged: incelemeye kadar model eğitimine GİRMEZ (saklanır).",
            fg=typer.colors.YELLOW,
        )
    typer.secho("Anlık analitik (NON-ML):", fg=typer.colors.CYAN, bold=True)

    def _pct(v: object) -> str:
        return f"%{v:.1f}" if isinstance(v, (int, float)) else "—"

    typer.echo(f"  İlk ilan → kapanış farkı: {_pct(analytics['initial_ask_to_close_gap_pct'])}")
    typer.echo(f"  Son ilan → kapanış farkı: {_pct(analytics['final_ask_to_close_gap_pct'])}")
    dtc = analytics["days_to_close"]
    typer.echo(f"  Kapanış süresi: {dtc if dtc is not None else '—'} gün")
    typer.echo(f"  Fiyat düşürme: {analytics['price_cut_count']}")

    seg = bench["segment"]
    if bench["enough_observations"]:
        typer.secho(
            f"Segment benchmark ({seg['province']} · {seg['property_type']}, "
            f"n={bench['observations']}):",
            fg=typer.colors.CYAN,
            bold=True,
        )
        typer.echo(
            f"  Son ilan → kapanış: medyan {_pct(bench['median_final_ask_to_close_gap_pct'])} · "
            f"ortalama {_pct(bench['mean_final_ask_to_close_gap_pct'])}"
        )
        typer.echo(f"  Kapanış süresi: medyan {bench['median_days_to_close']} gün")
    else:
        typer.secho(bench["message"], fg=typer.colors.YELLOW)


@consumer_app.command("stats")
def consumer_stats_cmd() -> None:
    """Doğrudan etiket durumu — GERÇEK (genuine) vs test/demo AYRI raporlanır."""
    from .consumer import direct_label_counts, load_consumer_sales
    from .db import get_engine, get_sessionmaker, init_db

    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        sales = load_consumer_sales(session)
        counts = direct_label_counts(session)

    typer.secho("Tüketici doğrudan-etiket durumu", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Kayıtlı tüketici satışı (tüm kökenler): {len(sales)}")
    typer.secho(
        f"  GERÇEK (genuine) doğrudan etiket [consumer_submission + accepted]: "
        f"{counts['genuine_accepted']}",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"    GERÇEK ama flagged (incelemede, eğitime girmez): {counts['genuine_flagged']}")
    typer.echo(f"    Test/demo etiketi (a2c'ye GİRMEZ): {counts['test_demo']}")
    typer.echo(f"    Manuel import: {counts['manual_import']}")
    typer.echo(
        f"  asking→closing head (varsayılan — üretim + accepted): "
        f"{counts['asking_to_closing_default']}"
    )
    if counts["genuine_accepted"] == 0:
        typer.secho(
            "  Not: Henüz GERÇEK bir satıcı gönderimi YOK → genuine = 0. "
            "E2E testi edinim YOLUNU kanıtlar, gerçek dünya etiketini DEĞİL.",
            fg=typer.colors.YELLOW,
        )


# --------------------------------------------------------------------------- #
# Yapısal ekonometrik motor (Nash pazarlık + SMM) — yeni çekirdek
# --------------------------------------------------------------------------- #
structural_app = typer.Typer(
    help="Yapısal ekonometrik motor: Nash pazarlık + TCMB-çıpalı hedonik + SMM",
    no_args_is_help=True,
)
app.add_typer(structural_app, name="structural")


@structural_app.command("value")
def structural_value_cmd(
    asking: float = typer.Argument(..., help="İlan (asking) fiyatı, TL"),
    province: str = typer.Option("İstanbul", help="İl"),
    gross_m2: float = typer.Option(100.0, help="Brüt m²"),
    tightness: float = typer.Option(0.0, help="Piyasa sıkılığı (TÜİK hacminden; 0=nötr)"),
) -> None:
    """Sıradan ilan için YAPISAL-YÖNTEM PROTOTİPİ closing dağılımı — gözlenen fiyat DEĞİL.

    asking, satıcı rezervasyonuna GÜRÜLTÜLÜ sinyaldir (tavan değil). Fair value TCMB
    çağdaş ekspertiz TL/m²'ye çıpalıdır (EK KFE çarpanı YOK — çift-sayım önlenir). θ şu an
    PROVİZYONEL'dir (kimliklendirme raporu desteklemeden ölçülen model DEĞİL); çıktı
    sensitivity modunda bir yapısal-yöntem prototipidir.
    """
    from .model.synthetic import load_province_ppm2
    from .structural import (
        StructuralClosingPredictor,
        StructuralParams,
        tcmb_fair_value,
    )

    ppm2 = load_province_ppm2().get(province)
    fv = tcmb_fair_value(ppm2, gross_m2)  # çağdaş çıpa; EK KFE ÇARPANI YOK
    if fv is None:
        typer.secho(
            f"{province} için TCMB ekspertiz TL/m² bulunamadı (gross_m2 > 0 olmalı).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    # identified=False → sensitivity mode (kimliklendirilmiş SMM tahmini yok)
    out = StructuralClosingPredictor(StructuralParams(), identified=False).predict(
        asking, fv, tightness=tightness
    )
    typer.secho(
        "YAPISAL-YÖNTEM PROTOTİPİ (θ PROVİZYONEL — kimliklendirilmemiş; sensitivity mode)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  İlan: {asking:,.0f} TL · Fair value (TCMB çağdaş ekspertiz çıpası): {fv:,.0f} TL")
    med = out["inferred_closing_median"]
    if med is None:
        typer.secho("  Bu senaryoda ticaret olasılığı ~0.", fg=typer.colors.YELLOW)
    else:
        lo, hi = out["interval_80"]
        typer.echo(f"  Çıkarımsal closing: medyan {med:,.0f} · ortalama {out['inferred_closing_mean']:,.0f} TL")
        typer.echo(f"  %80 yapısal aralık: {lo:,.0f} – {hi:,.0f} TL")
    typer.echo(f"  Ticaret olasılığı: {out['trade_probability']:.2f}  ·  mod: {out['mode']}")
    band = out["mechanism_transfer_sensitivity"].get("median_band")
    if band and band[0] is not None:
        typer.echo(f"  Mekanizma-transfer duyarlılığı (medyan bandı): {band[0]:,.0f} – {band[1]:,.0f} TL")
    typer.secho(f"  {out['note']}", fg=typer.colors.YELLOW)


@structural_app.command("estimate")
def structural_estimate_cmd(
    eta_true: float = typer.Option(0.60, help="Demo: bilinen (gerçek) eta"),
) -> None:
    """SMM DOĞRULAMA demosu: bilinen θ'dan sentetik moment üretip eta'yı geri kazanır.

    Bu bir YÖNTEM doğrulamasıdır (gerçek tahmin değil) — `model demo` gibi. Gerçek
    tahmin, kamu yapısal veri kümesi genişletildikçe (UYAP/KAP/TOKİ) yapılacaktır.
    """
    import numpy as np

    from .structural import (
        MomentContext,
        StructuralParams,
        estimate_smm,
        observed_moments,
        simulate_negotiations,
    )

    theta0 = StructuralParams(eta=eta_true)
    rng = np.random.default_rng(2024)
    V = np.ones(40000)
    neg = simulate_negotiations(rng, V, theta0, 40000, mechanism="kap")
    traded = neg["traded"]
    m_obs = observed_moments(kap_realized=neg["price"][traded], kap_appraisal=V[traded])
    ctx = MomentContext(
        auction_appraised=np.array([]),
        auction_floors=np.array([]),
        kap_appraisal=np.ones(60),
        reps=400,
    )
    res = estimate_smm(m_obs, ctx, free_names=("eta",), start=StructuralParams(eta=0.40), seed=777)
    typer.secho("SMM doğrulama demosu (yöntem doğrulaması)", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Gerçek eta: {eta_true:.3f}  ·  Geri kazanılan eta: {res.params.eta:.3f}")
    typer.echo(f"  SMM hedefi: {res.objective:.3e}  ·  iterasyon: {res.n_iter}")
    typer.echo(f"  Eşleşen momentler: {res.moment_keys}")
    typer.secho(
        "  Not: eta HARD-CODE değildir; SMM tahmin eder. Sonraki iş: kamu yapısal "
        "veri kümesi genişletmesi + gerçek SMM tahmini.",
        fg=typer.colors.YELLOW,
    )


@structural_app.command("dataset")
def structural_dataset_cmd() -> None:
    """GERÇEK denetlenmiş yapısal gözlem durumu — fixture/illustratif kayıtlardan AYRI."""
    from .structural import dataset_status

    st = dataset_status()
    g = st["genuine"]
    typer.secho("Yapısal veri kümesi durumu (GERÇEK denetlenmiş)", fg=typer.colors.CYAN, bold=True)
    u = g["uyap"]
    typer.echo(
        f"  UYAP: denetlenmiş açık artırma {u['total_audited_auctions']} "
        f"(satılan {u['sold']} · satılmayan {u['unsold']})"
    )
    typer.echo(
        f"        kazanan teklif {u['winning_bids_observed']} · teklif sayısı {u['offer_counts_observed']} "
        f"· artıran {u['bidder_counts_observed']} · tam yasal-taban {u['exact_legal_floors_observed']}"
    )
    k = g["kap"]
    typer.echo(
        f"  KAP: uygun elden çıkarma {k['audited_eligible_disposals']} "
        f"(müzakere-kalibrasyon {k['negotiated_calibration_observations']}) · "
        f"appraisal {k['appraisal_observations']} · prior-appraisal {k['prior_appraisal_observations']}"
    )
    t = g["toki"]
    typer.echo(
        f"  TOKİ: denetlenmiş açıklama {t['audited_disclosures']} · proje {t['projects_represented']} "
        f"· oda-tipi kümülatif strata {t['room_type_cumulative_strata']}"
    )
    typer.echo(
        f"        geçerli dönem kohortu {t['valid_derived_period_cohorts']} "
        f"· revizyonla-bloklanan {t['revision_blocked_cohorts']}"
    )
    na = st["non_audited_records"]
    typer.secho(
        f"  Denetlenmemiş (fixture/illustratif, GERÇEK sayıma KATILMAZ): "
        f"UYAP {na['uyap']} · KAP {na['kap']} · TOKİ {na['toki']}",
        fg=typer.colors.YELLOW,
    )


@structural_app.command("identify")
def structural_identify_cmd(
    auctions_file: Optional[Path] = typer.Option(None, "--auctions", help="UYAP kayıtları (JSON) — verilirse gerçek seti geçersiz kılar"),
    kap_file: Optional[Path] = typer.Option(None, "--kap", help="KAP kayıtları (JSON)"),
    toki_file: Optional[Path] = typer.Option(None, "--toki", help="TOKİ açıklamaları (JSON)"),
    demo: bool = typer.Option(False, "--demo", help="Sentetik veriyle diagnostiği göster (gerçek değil)"),
) -> None:
    """Yapısal KİMLİKLENDİRME raporu — VARSAYILAN olarak GERÇEK denetlenmiş veri kümesinden.

    Dataset sayıları + kullanılabilir/eksik momentler (+ neden) + moment provenance +
    Jacobian rank/SVD/koşul + zayıf yönler + eta/kayma profilleri + 3'lü durum. Optimizer
    yakınsaması KİMLİKLENDİRME DEĞİLDİR. rank(J)<dim → NOT_IDENTIFIED; tam rank ama ağır
    kondisyon bozukluğu/düz profil → WEAKLY_IDENTIFIED; ikisi de değilse IDENTIFIED.
    """
    import json

    import numpy as np

    from .structural import (
        DEFAULT_FREE,
        MomentContext,
        StructuralParams,
        build_observed_moments,
        context_from_datasets,
        difference_disclosures,
        identification_report,
        load_auctions,
        load_genuine_datasets,
        load_kap_disposals,
        observed_moments,
        simulate_negotiations,
    )

    auctions_df = kap_df = None
    toki_res = None
    m_obs: dict = {}
    provenance: dict = {}
    unavailable: list = []

    if demo:
        theta0 = StructuralParams(eta=0.6)
        rng = np.random.default_rng(7)
        V = np.ones(20000)
        neg = simulate_negotiations(rng, V, theta0, 20000, mechanism="kap")
        tr = neg["traded"]
        m_obs = observed_moments(kap_realized=neg["price"][tr], kap_appraisal=V[tr])
        provenance = {k: "synthetic" for k in m_obs}
        ctx = MomentContext(
            auction_appraised=np.array([]), auction_floors=np.array([]),
            kap_appraisal=np.ones(60), reps=300,
        )
    else:
        if auctions_file or kap_file or toki_file:
            if auctions_file:
                auctions_df = load_auctions(json.loads(Path(auctions_file).read_text(encoding="utf-8")))
            if kap_file:
                kap_df = load_kap_disposals(json.loads(Path(kap_file).read_text(encoding="utf-8")))
            if toki_file:
                toki_res = difference_disclosures(json.loads(Path(toki_file).read_text(encoding="utf-8")))
        else:
            genuine = load_genuine_datasets()  # VARSAYILAN: gerçek denetlenmiş veri
            auctions_df, kap_df, toki_res = genuine["uyap"], genuine["kap"], genuine["toki_result"]
        built = build_observed_moments(auctions_df, kap_df, toki_res)
        m_obs, provenance, unavailable = built["moments"], built["provenance"], built["unavailable"]
        ctx = context_from_datasets(auctions_df, kap_df)

    rep = identification_report(
        ctx, StructuralParams(), DEFAULT_FREE, m_obs=m_obs,
        auctions=auctions_df, kap=kap_df, toki_result=toki_res,
        provenance=provenance, unavailable=unavailable,
    )
    ds = rep["dataset"]
    typer.secho("Yapısal kimliklendirme raporu", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        f"  UYAP: toplam {ds['uyap_total']} · satılan {ds['uyap_sold']} · satılmayan {ds['uyap_unsold']} "
        f"· teklif göz. {ds['uyap_offer_count_observed']} · artıran {ds['uyap_bidder_count_observed']} "
        f"· tam-taban {ds['uyap_exact_legal_floor_observed']}"
    )
    typer.echo(f"  KAP müzakereli elden çıkarma: {ds['kap_negotiated_disposals']}")
    typer.echo(f"  TOKİ geçerli proje-dönem kohortu: {ds['toki_valid_project_period_strata']}")
    typer.echo(
        f"  dim(θ): {rep['n_structural_parameters']} · kullanılabilir moment: {rep['n_observed_moments']}"
    )
    if rep.get("moment_provenance"):
        typer.echo(f"  Moment provenance: {rep['moment_provenance']}")
    for un in rep.get("unavailable_moments", []):
        typer.echo(f"  Eksik moment: {un['moment']} [{un['source']}] — {un['reason']}")
    color = {
        "IDENTIFIED": typer.colors.GREEN,
        "WEAKLY_IDENTIFIED": typer.colors.YELLOW,
        "NOT_IDENTIFIED": typer.colors.RED,
    }.get(rep["status"], typer.colors.RED)
    typer.secho(
        f"  DURUM: {rep['status']}  (rank {rep['rank']} / dim {rep['n_structural_parameters']}) "
        f"· mod: {rep['prediction_mode']}",
        fg=color,
        bold=True,
    )
    if rep.get("singular_values"):
        sv = ", ".join(f"{x:.2e}" for x in rep["singular_values"])
        typer.echo(f"  Tekil değerler: [{sv}]  ·  koşul sayısı: {rep['condition_number']:.2e}")
    for wd in rep.get("weakly_identified_directions", []):
        typer.echo(f"  Zayıf yön (s={wd['singular_value']:.2e}): {wd['direction']}")
    for pname, pr in (rep.get("profiles") or {}).items():
        rr = pr.get("relative_range")
        flag = "ZAYIF" if pr.get("weakly_identified") else "belirgin"
        rr_txt = f"{rr:.2e}" if isinstance(rr, (int, float)) else "—"
        typer.echo(f"  Profil {pname}: göreli hedef aralığı {rr_txt} → {flag}")
    if rep["status"] != "IDENTIFIED":
        typer.secho(
            "  Not: rank < dim veya zayıf kimliklendirme → optimizer sonucu NOKTA TAHMİNİ olarak "
            "SUNULMAZ; sensitivity/prototip mode. (Optimizer yakınsaması ≠ kimliklendirme.) "
            "Sonraki iş: gerçek UYAP/KAP/tekrarlı TOKİ gözlem genişletmesi.",
            fg=typer.colors.YELLOW,
        )


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Dinlenecek adres"),
    port: int = typer.Option(8000, help="Port"),
    reload: bool = typer.Option(False, "--reload", help="Geliştirme için otomatik yeniden yükle"),
) -> None:
    """Değerleme API'sini + web arayüzünü başlatır (FastAPI/uvicorn)."""
    try:
        import uvicorn
    except ImportError:
        typer.secho(
            'FastAPI/uvicorn kurulu değil. `pip install -e ".[api]"` çalıştırın.',
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.secho(f"Değerleme servisi: http://{host}:{port}", fg=typer.colors.GREEN)
    uvicorn.run("sold.api.app:app", host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
