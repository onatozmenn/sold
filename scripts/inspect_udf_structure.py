"""OFFLINE UDF yapı tanısı (GİZLİLİK-GÜVENLİ).

Bir ``.udf`` (ZIP) içindeki ``content.xml``'in ELEMAN yapısını ve para-literali KONUMLARINI raporlar.
Amaç: İhale Bedeli değerinin content.xml'de NEREDE olduğunu görmek — ``<content>`` CDATA metninde mi
(mevcut ``extract_udf_source_text`` bunu okur), yoksa ``<elements>``/``<field>`` gibi başka elemanlarda mı.

ÇIKTI YALNIZCA yapısaldır: tag adları, metin UZUNLUKLARI ve para-literali SAYILARI. Belge metni ya da
parasal DEĞERLER ASLA yazdırılmaz. Kişisel içerik ekrana basılmaz.

Kullanım (projelerim dizininden):
    python sold\\scripts\\inspect_udf_structure.py
    python sold\\scripts\\inspect_udf_structure.py <klasör-veya-.udf-yolu>
"""
from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# models.MONEY_LITERAL_RE ile aynı: Türk parasal literali (gruplama '.'/ondalık ',') — çıplak tamsayı DEĞİL.
MONEY_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{2}")


def _tag(el: ET.Element) -> str:
    return str(el.tag).split("}")[-1]


def inspect(udf_path: Path) -> None:
    print(f"\n=== {udf_path.name} ({udf_path.stat().st_size} B) ===")
    try:
        with zipfile.ZipFile(udf_path) as z:
            names = z.namelist()
            if "content.xml" not in names:
                print("  content.xml YOK; üyeler:", names)
                return
            raw = z.read("content.xml")
    except Exception as exc:  # noqa: BLE001
        print("  zip okunamadı:", exc)
        return

    xml_text = raw.decode("utf-8", errors="replace")
    total_money = len(MONEY_RE.findall(xml_text))
    print(f"  content.xml: {len(raw)} B · ham XML'deki TOPLAM para-literali: {total_money}")

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print("  XML ayrıştırılamadı:", exc)
        return

    # Mevcut çıkarımın gördüğü metin: İLK <content> elemanının itertext'i
    content_el = next((el for el in root.iter() if _tag(el).lower() == "content"), None)
    if content_el is not None:
        ctext = "".join(content_el.itertext())
        print(f"  <content> itertext: uzunluk={len(ctext)} · para={len(MONEY_RE.findall(ctext))}  "
              f"(<-- extract_udf_source_text SADECE bunu okur)")
    else:
        print("  <content> elemanı YOK")

    # Kök altındaki top-level çocuk elemanlar
    print("  top-level çocuk elemanlar (tag · itertext_len · para_sayısı):")
    for ch in list(root):
        txt = "".join(ch.itertext())
        print(f"    - <{_tag(ch)}> len={len(txt)} para={len(MONEY_RE.findall(txt))}")

    # Para-literali İÇEREN eleman tag'lerinin özeti (derin) — DEĞER YOK, yalnız KONUM (text/tail/attr) + tag + adet.
    text_holders: dict[str, int] = {}
    tail_holders: dict[str, int] = {}
    attr_holders: dict[str, int] = {}
    for el in root.iter():
        t = _tag(el)
        n_text = len(MONEY_RE.findall(el.text or ""))
        if n_text:
            text_holders[t] = text_holders.get(t, 0) + n_text
        n_tail = len(MONEY_RE.findall(el.tail or ""))
        if n_tail:
            tail_holders[t] = tail_holders.get(t, 0) + n_tail
        for an, av in el.attrib.items():
            n_attr = len(MONEY_RE.findall(av or ""))
            if n_attr:
                key = f"{t}@{an.split('}')[-1]}"
                attr_holders[key] = attr_holders.get(key, 0) + n_attr
    print("  para .text içinde (tag: adet):", text_holders or "YOK")
    print("  para .tail içinde (tag: adet):", tail_holders or "YOK")
    print("  para ATTRIBUTE içinde (tag@attr: adet):", attr_holders or "YOK")

    # Eleman tag histogramı (yapıyı görmek için; en sık 12 tag)
    hist: dict[str, int] = {}
    for el in root.iter():
        hist[_tag(el)] = hist.get(_tag(el), 0) + 1
    top = sorted(hist.items(), key=lambda kv: kv[1], reverse=True)[:12]
    print("  tag histogramı (en sık):", top)


def main() -> None:
    args = sys.argv[1:]
    target = Path(args[0]) if args else Path("data/ingestion/uyap/artifacts/downloads")
    if target.is_file():
        inspect(target)
    elif target.is_dir():
        udfs = sorted(target.glob("*.udf"))
        if not udfs:
            print("Hiç .udf bulunamadı:", target.resolve())
            return
        print(f"{len(udfs)} .udf bulundu: {target.resolve()}")
        for p in udfs:
            inspect(p)
    else:
        print("Yol yok:", target.resolve())


if __name__ == "__main__":
    main()
