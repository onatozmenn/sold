"""Native UDF (UYAP Doküman Formatı) kaynak-metin çıkarımı — DETERMİNİSTİK; OCR ve belge-görselleştirme YOK.

Ölçülen gerçek resmî artifact: ZIP-uyumlu (PK) konteyner; üst düzey üyeler ``documentproperties.xml``,
``content.xml``, ``sign.sgn``. ``content.xml`` UTF-8 XML olup belge kaynak metnini ``content`` elemanının
CDATA/metin içeriğinde taşır. Bu adaptör YALNIZCA ``content.xml``'i doğrudan (arşiv topluca açılmaz) okur,
güvenli biçimde XML ayrıştırır (dış entity/DTD/XInclude/ağ YOK) ve ``content`` metnini aynen döndürür.

GÜVENLİK: keyfi arşiv üyesi diske AÇILMAZ; yol-gezinme (``..``/mutlak/sürücü) reddedilir; çözülmüş boyut
sınırlanır (zip-bomb koruması); DOCTYPE/ENTITY içeren XML reddedilir. Harici ofis/görüntüleyici yazılımı /
GUI çağrılmaz. Görsel-metin (OCR) YOK, ML YOK, known-truth ENJEKSİYONU YOK.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Çözülmüş kaynak için makul depo-yerel güvenlik sınırı (zip-bomb koruması). Ölçülen content.xml ≈ 17.9 KB.
MAX_UDF_DECOMPRESSED_BYTES = 8 * 1024 * 1024  # 8 MiB
CONTENT_MEMBER = "content.xml"
# Gizlilik-güvenli özet için bilinen yapısal üye adları (kişisel/opak değil).
_KNOWN_MEMBERS = ("documentproperties.xml", "content.xml", "sign.sgn")


def _new_diag() -> dict:
    return {
        "container_kind": None,
        "zip_valid": False,
        "member_names_safe_summary": {},
        "content_xml_found": False,
        "content_xml_size": 0,
        "xml_parse_succeeded": False,
        "content_element_found": False,
        "source_text_available": False,
        "text_extraction_supported": False,
        "blocking_reason": None,
    }


def _unsafe_member(name: str) -> bool:
    """Yol-gezinme / mutlak / sürücü-öneki gibi güvensiz arşiv üyesi yolu mu."""
    n = str(name or "").replace("\\", "/")
    if not n:
        return True
    if n.startswith("/"):
        return True
    if len(n) >= 2 and n[1] == ":":  # C:\ gibi sürücü öneki
        return True
    return ".." in n.split("/")


def _member_names_safe_summary(names: list) -> dict:
    """Gizlilik-güvenli üye özeti: yalnız sayı + BİLİNEN yapısal adlar + güvensiz-yol bayrağı (ham ad YOK)."""
    known = sorted({n for n in names if n in _KNOWN_MEMBERS})
    return {
        "member_count": len(names),
        "known_members": known,
        "has_unsafe_member_path": any(_unsafe_member(n) for n in names),
        "has_content_xml": CONTENT_MEMBER in names,
    }


# UYAP UDF: doldurulmuş ALAN DEĞERLERİ ``<content>`` CDATA'sında DEĞİL, ayrı ``<data>`` bölümünde SEMANTİK
# tag'lerle tutulabilir (ör. ``<ihaleBedeli>4.654.000,00</ihaleBedeli>``). YALNIZCA bilinen FİNANSAL tag'ler
# (TAM ad eşleşmesi) kanonik Türkçe etiketle metne eklenir → alan-etiketi→değer çıkarımı bunları görür.
# Bileşik/kişisel/bilinmeyen tag'ler EKLENMEZ (yanlış eşleşme + kişisel-veri sızıntısı önlenir).
_UDF_DATA_FINANCIAL_LABELS = {
    "ihalebedeli": "İhale Bedeli",
    "muhammenbedel": "Muhammen Bedel",
    "muhammenbedeli": "Muhammen Bedel",
    "muhammenkiymet": "Muhammen Kıymet",
    "muhammenkiymeti": "Muhammen Kıymet",
    "kiymeti": "Kıymeti",
    "odenmesigerekenbedel": "Ödenmesi Gereken Bedel",
}


def _data_field_lines(root) -> list:
    """UYAP UDF ``<data>`` bölümündeki BİLİNEN finansal alanları ``Kanonik Etiket: değer`` satırlarına çevirir.

    Doldurulmuş değerler ``<content>`` CDATA'sında değil ``<data>``'da semantik tag'lerle tutulur. Yalnız
    bilinen finansal tag'ler (TAM ad eşleşmesi) kanonik etiketle eklenir; değer AÇIK semantik elemandan
    gelir (UYDURMA YOK, kart 'Satış Tutarı' KULLANILMAZ). Bileşik/kişisel/bilinmeyen alanlar EKLENMEZ.
    """
    lines = []
    for data_el in root.iter():
        if str(data_el.tag).split("}")[-1].lower() != "data":
            continue
        for child in list(data_el):
            key = str(child.tag).split("}")[-1].lower().replace("_", "")
            label = _UDF_DATA_FINANCIAL_LABELS.get(key)
            val = (child.text or "").strip()
            if label and val:
                lines.append(f"{label}: {val}")
        break  # ilk ``<data>`` bölümü yeterli
    return lines


def _extract_content_text(xml_bytes: bytes, diag: dict) -> str | None:
    """``content.xml`` baytlarını GÜVENLİ XML ayrıştırıp ``content`` elemanının CDATA/metnini aynen döndürür."""
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError:
        diag["blocking_reason"] = "unsupported_encoding"
        return None
    low = xml_text.lower()
    # Dış entity / DTD / XInclude / stylesheet YOK (güvenlik): bunlardan biri varsa dürüstçe reddet.
    if "<!doctype" in low or "<!entity" in low or "xinclude" in low or "<?xml-stylesheet" in low:
        diag["blocking_reason"] = "doctype_or_entity_not_allowed"
        return None
    try:
        # ElementTree (expat) varsayılan olarak dış entity ÇÖZMEZ ve ağ ERİŞMEZ; DOCTYPE zaten yukarıda elendi.
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        diag["blocking_reason"] = "malformed_xml"
        return None
    diag["xml_parse_succeeded"] = True
    content_el = None
    for el in root.iter():
        if str(el.tag).split("}")[-1].lower() == "content":  # namespace-duyarsız
            content_el = el
            break
    if content_el is None:
        diag["blocking_reason"] = "content_element_missing"
        return None
    diag["content_element_found"] = True
    # ElementTree CDATA'yı METİN olarak birleştirir (.text/itertext); formatlama/koordinat AYRIŞTIRILMAZ.
    text = "".join(content_el.itertext())
    if not text or not text.strip():
        diag["blocking_reason"] = "empty_content_text"
        return None
    # UYAP UDF: doldurulmuş finansal değerler <content> DIŞINDA <data> bölümünde semantik tag'lerde durur
    # (ör. <ihaleBedeli>). Bilinen finansal alanları kanonik etiketle EKLE ki İhale Bedeli/Muhammen çıkarımı
    # görebilsin (değer açık semantik elemandan; UYDURMA YOK). content.xml'de yoksa metin AYNEN kalır.
    data_lines = _data_field_lines(root)
    if data_lines:
        text = text + "\n" + "\n".join(data_lines)
    diag["udf_data_financial_field_count"] = len(data_lines)
    diag["source_text_available"] = True
    diag["text_extraction_supported"] = True
    return text


def extract_udf_source_text(source) -> tuple:
    """Native UDF konteynerinden DETERMİNİSTİK belge kaynak metni çıkarır. Döner ``(text | None, diag)``.

    ``source``: kesin indirilen baytlar (``bytes``) YA DA yerel artifact yolu. ZIP doğrulanır; YALNIZCA
    kök ``content.xml`` doğrudan okunur (arşiv topluca açılmaz); güvenli XML ayrıştırılır; ``content`` metni döner.
    Başarısızlıkta ``text=None`` + ``diag['blocking_reason']`` (dürüst; UYDURMA YOK). Görsel-metin/görselleştirme YOK.
    """
    diag = _new_diag()
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        try:
            data = Path(source).read_bytes()
        except Exception:
            diag["blocking_reason"] = "artifact_unreadable"
            return None, diag
    bio = io.BytesIO(data)
    if not zipfile.is_zipfile(bio):
        diag["blocking_reason"] = "not_a_zip_compatible_container"
        return None, diag
    diag["container_kind"] = "zip_udf"
    diag["zip_valid"] = True
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        diag["blocking_reason"] = "zip_open_failed"
        return None, diag
    with zf:
        names = zf.namelist()
        diag["member_names_safe_summary"] = _member_names_safe_summary(names)
        if diag["member_names_safe_summary"]["has_unsafe_member_path"]:
            # Güvensiz yol → HİÇBİR üye açılmaz (path traversal engeli).
            diag["blocking_reason"] = "unsafe_archive_member_path"
            return None, diag
        content_members = [n for n in names if n == CONTENT_MEMBER]
        if len(content_members) == 0:
            diag["blocking_reason"] = "content_xml_missing"
            return None, diag
        if len(content_members) > 1:
            # Kök content.xml güvenle tekil olarak tanımlanamıyor → dürüstçe reddet.
            diag["blocking_reason"] = "ambiguous_content_xml_members"
            return None, diag
        try:
            info = zf.getinfo(CONTENT_MEMBER)
        except KeyError:
            diag["blocking_reason"] = "content_xml_missing"
            return None, diag
        if getattr(info, "file_size", 0) > MAX_UDF_DECOMPRESSED_BYTES:
            diag["blocking_reason"] = "content_xml_too_large"
            return None, diag
        diag["content_xml_found"] = True
        try:
            with zf.open(CONTENT_MEMBER) as fh:
                raw = fh.read(MAX_UDF_DECOMPRESSED_BYTES + 1)  # sınırlı okuma (zip-bomb koruması)
        except RuntimeError:
            diag["blocking_reason"] = "encrypted_or_unreadable_archive"
            return None, diag
        except Exception:
            diag["blocking_reason"] = "content_xml_unreadable"
            return None, diag
    if len(raw) > MAX_UDF_DECOMPRESSED_BYTES:
        diag["blocking_reason"] = "content_xml_too_large"
        return None, diag
    diag["content_xml_size"] = len(raw)
    text = _extract_content_text(raw, diag)
    return text, diag


def native_udf_supported(diag: dict) -> bool:
    """Native UDF metin çıkarımı DESTEKLENİYOR mu — YALNIZCA doğrulanmış yapı + content.xml + metin
    (uzantı tek başına YETMEZ)."""
    return bool(diag and diag.get("zip_valid") and diag.get("content_xml_found")
                and diag.get("xml_parse_succeeded") and diag.get("content_element_found")
                and diag.get("source_text_available"))
