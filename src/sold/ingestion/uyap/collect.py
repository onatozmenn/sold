"""Toplama (collection) — tarayıcı-DESTEKLİ toplama + elle artifact içe aktarma (offline yedek).

GÜVENLİK / ERİŞİM (KESİN):
- e-Devlet kimlik doğrulaması OTOMATİKLEŞTİRİLMEZ; parola/MFA istenmez/saklanmaz.
- CAPTCHA / hız sınırı / teknik erişim kontrolü AŞILMAZ; kimlik/oturum ele geçirilmez.
- Çerez/oturum/token repoya GÖMÜLMEZ; tarayıcı profili/çerezleri COMMİT EDİLMEZ.

İŞLETİM MODELİ: kullanıcı yerel bir tarayıcı oturumunu NORMAL biçimde açar/oturum açar →
toplayıcı YALNIZCA kullanıcı-kontrollü (kimlik doğrulanmış ya da kamuya açık) oturum içinde
çalışır → UYAP sonuç/liste sayfalarını inceler ve İZİN VERİLEN kaynak artifact'larını toplar.

Ajan geliştirme ortamı canlı UYAP'a erişemezse başarılı canlı erişim UYDURULMAZ: tarayıcı
adaptörü + yerel fixture'lara karşı deterministik ayrıştırıcılar + elle kaydedilmiş HTML/PDF
içe aktarma yolu sağlanır ve canlı-tarayıcı önkoşulları DÜRÜSTÇE belgelenir. Otomatik test
paketi AĞ ERİŞİMİ GEREKTİRMEZ.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from . import store
from .models import (
    ARTIFACT_APPRAISAL_REPORT,
    ARTIFACT_AUCTION_RESULT,
    ARTIFACT_SALE_NOTICE,
    ARTIFACT_SALE_SPEC,
    STATE_COLLECTED,
    SourceArtifact,
    _ascii_lower,
)
from .extract import corroborate_native_document_type
from .udf import extract_udf_source_text, native_udf_supported

BROWSER_PREREQUISITES = (
    "Browser-assisted collection requires the optional 'browser' extra (Playwright) AND a "
    "user-controlled, already-authenticated or public UYAP session. Install: "
    "pip install -e '.[browser]' then 'python -m playwright install chromium'. The collector "
    "NEVER automates e-Devlet login, MFA or CAPTCHA and stores no credentials/cookies in the repo. "
    "Attach to a session you launched yourself (CDP endpoint) or a local persistent profile you "
    "authenticated manually. If you cannot use a live browser, import saved HTML/PDF artifacts."
)

# Gerçek UYAP belge-listesi kontrolü metinleri ("İhale Evrak Listesi") — normalize eşleşme.
DOCUMENT_LIST_CONTROL_TEXTS = ("ihale evrak listesi", "evrak listesi")

# Satır-yerel görüntüle/eye eylemini tanıyan jetonlar (global Nth-eye KULLANILMAZ).
_VIEW_ACTION_TOKENS = ("goruntule", "goster", "incele", "evrak", "eye", "view", "gor", "goz", "ac")
_DOWNLOAD_TOKENS = ("indir", "download", "dosya", "kaydet")


def _is_download_action(a: dict) -> bool:
    """Bir eylemin indirme oku/kontrolü olup olmadığını belirler (eye/görüntüle DEĞİL)."""
    blob = _ascii_lower(" ".join([str(a.get("text", "")), str(a.get("css", ""))]))
    return a.get("kind") == "download" or any(k in blob for k in _DOWNLOAD_TOKENS)


# --- Fix 5: kısıtlı mojibake (UTF-8-as-Latin-1/cp1252) onarımı — YALNIZCA sınıflandırma girdisi --- #
def _looks_mojibake(text: str) -> bool:
    """Bilinen UTF-8-as-Latin-1/cp1252 mojibake öncü karakterleri (Ã/Ä/Å) var mı."""
    return any(lead in (text or "") for lead in ("Ã", "Ä", "Å"))


def _demojibake(text: str) -> str:
    """Tarayıcı/HTML anlık görüntüsündeki bilinen mojibake'i onarır (İhale/Satış/Bilirkişi/Artırma).

    YALNIZCA bilinen imza varsa uygulanır; doğru Türkçe Unicode (İ/ş/ğ...) latin-1/cp1252'ye
    kodlanamaz → olduğu gibi döner. Kaynak artifact'ları MUTASYONA UĞRATMAZ; yalnız normalize girdisi.
    """
    s = str(text or "")
    if not s or not _looks_mojibake(s):
        return s
    for enc in ("latin-1", "cp1252"):
        try:
            repaired = s.encode(enc, "strict").decode("utf-8", "strict")
        except UnicodeError:
            continue
        if repaired and repaired != s:
            return repaired
    return s


def classify_document_label(text: str) -> str | None:
    """Bir belge etiketini artifact türüne eşler (jeton-tabanlı; GERÇEK DOM metnine karşı).

    ``BLR_BILIRKISI_RAPORU`` → appraisal_report; ``Artırma Sonuç / Uzatma Tutanağı`` →
    auction_result; ``Satış Şartnamesi Ve Tutanağı`` → sale_spec. Uydurma selector YOK.
    """
    fold = re.sub(r"\s+", " ", _ascii_lower(_demojibake(text or "")).replace("_", " ")).strip()
    if not fold:
        return None
    if "satis sartnamesi" in fold:
        return ARTIFACT_SALE_SPEC
    if "satis ilani" in fold:
        return ARTIFACT_SALE_NOTICE
    if "bilirkisi" in fold or "kiymet takdir" in fold:
        return ARTIFACT_APPRAISAL_REPORT
    if "artirma sonuc" in fold or "uzatma tutanagi" in fold or "ihale artirma" in fold:
        return ARTIFACT_AUCTION_RESULT
    return None


def has_document_list_control(html: str) -> bool:
    """HTML metninde "İhale Evrak Listesi" benzeri bir kontrolün metni var mı (testable, offline)."""
    fold = _ascii_lower(html or "")
    return any(t in fold for t in DOCUMENT_LIST_CONTROL_TEXTS)


def select_row_document_actions(rows: list[dict]) -> list[dict]:
    """Modal satırlarından belge etiketini sınıflandırıp O SATIRA AİT görüntüle/eye eylemini seçer.

    ``rows``: ``[{"label": str, "actions": [{"kind", "text", "href", ...}]}]``. GLOBAL Nth-eye
    KULLANILMAZ; belge sırası SABİT VARSAYILMAZ; ilgisiz eylemler seçilmez. OFFLINE test edilebilir.
    """
    selected: list[dict] = []
    for i, row in enumerate(rows):
        atype = classify_document_label(row.get("label", ""))
        if not atype:
            continue
        actions = row.get("actions") or []
        view = None
        for a in actions:
            if _is_download_action(a):
                continue  # indirme oku EYE/görüntüle DEĞİL
            t = _ascii_lower(a.get("text", "") or "")
            if a.get("kind") == "eye" or any(k in t for k in _VIEW_ACTION_TOKENS):
                view = a
                break
        if view is None:
            non_dl = [a for a in actions if not _is_download_action(a)]
            if non_dl:
                view = non_dl[0]  # satır-yerel eylem (indirme HARİÇ)
        if view is not None:
            selected.append({"row_index": i, "label": row.get("label"), "artifact_type": atype, "action": view})
    return selected


def classify_access_pattern(observed: dict) -> str:
    """GÖZLENEN (runtime) tarayıcı olayından belge-erişim desenini sınıflandırır (uydurma YOK)."""
    if observed.get("download"):
        return "button_modal_download"
    if observed.get("opened_popup") or observed.get("opened_new_tab"):
        return "button_modal_new_tab_pdf" if observed.get("is_pdf") else "button_modal_popup_html"
    if observed.get("same_page_nav") or observed.get("viewer_visible"):
        return "button_modal_same_page_viewer"
    return "button_modal_unsupported"


# --- Fix 2: gerçek gözlenen aynı-sayfa belge-listesi paneli + yeni-sekme UDF görüntüleyici --- #
_DOC_KEYWORDS = {
    "satis ilani": ARTIFACT_SALE_NOTICE,
    "bilirkisi": ARTIFACT_APPRAISAL_REPORT,
    "kiymet takdir": ARTIFACT_APPRAISAL_REPORT,
    "artirma sonuc": ARTIFACT_AUCTION_RESULT,
    "uzatma tutanagi": ARTIFACT_AUCTION_RESULT,
    "ihale artirma": ARTIFACT_AUCTION_RESULT,
    "satis sartnamesi": ARTIFACT_SALE_SPEC,
}
_PANEL_ROW_SELECTORS = ("tr", "li", "[class*=evrak]", "[class*=row]")
# Fix 5: belge toplama önceliği (auction_result > appraisal_report > sale_notice > sale_spec).
_DOC_PRIORITY = {
    ARTIFACT_AUCTION_RESULT: 0,
    ARTIFACT_APPRAISAL_REPORT: 1,
    ARTIFACT_SALE_NOTICE: 2,
    ARTIFACT_SALE_SPEC: 3,
}


def _distinct_doc_types(text: str) -> set:
    fold = _ascii_lower(_demojibake(text or "")).replace("_", " ")
    return {atype for kw, atype in _DOC_KEYWORDS.items() if kw in fold}


# --- Fix 6: satır-yerel İKON eylem introspeksiyonu + deterministik download/view çözümü --- #
# Genel ikon/erişilebilirlik token aileleri (gerçek UYAP ikon çerçevesi ÖNCEDEN varsayılmaz).
_ACTION_VIEW_TOKENS = (
    "goruntule", "goster", "incele", "onizleme", "onizle", "preview", "view", "eye",
    "visibility", "goz", "buyutec", "search", "detay",
)
_ACTION_DOWNLOAD_TOKENS = (
    "indir", "download", "kaydet", "arrow-down", "arrowdown", "file-download", "filedownload",
    "cloud-download", "clouddownload", "save",
)


def _text_has_view(s: str) -> bool:
    s = _ascii_lower(_demojibake(str(s or "")))
    return any(tok in s for tok in _ACTION_VIEW_TOKENS)


def _text_has_download(s: str) -> bool:
    s = _ascii_lower(_demojibake(str(s or "")))
    if any(tok in s for tok in _ACTION_DOWNLOAD_TOKENS):
        return True
    return "down" in s.split()  # yalın 'down' YALNIZCA tam-token (dropdown vb. yanlış-eşleşmez)


def _safe_class_tokens(classlist) -> list[str]:
    """Kısıtlı, gizlilik-güvenli class token'ları (uzun/opak/sayısal hash'ler atılır)."""
    out: list[str] = []
    for c in (classlist or []):
        t = _ascii_lower(str(c)).strip()
        if not t or len(t) > 32 or t.isdigit():
            continue
        out.append(t)
        if len(out) >= 12:
            break
    return out


def _bounded_tokens(tokens, limit: int = 16) -> list[str]:
    seen: set = set()
    out: list[str] = []
    for t in tokens:
        t = (str(t) if t is not None else "").strip()[:24]
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _href_kind(el) -> str | None:
    """Href/download-attr'dan GÜVENLİ tür (opak sorgu/evrakId ASLA saklanmaz): javascript/viewer/download/same_page/unknown."""
    if hasattr(el, "has_attr") and el.has_attr("download"):
        return "download"
    href = _ascii_lower(str(el.get("href") or "")).strip()
    if not href:
        return None
    if href.startswith("javascript:") or href in ("#", "#/"):
        return "javascript"
    path = href.split("?", 1)[0]  # opak sorgu (evrakId vb.) DÜŞÜR
    if "viewer.jsp" in path or "goruntule" in path or "/view" in path:
        return "viewer"
    if "indir" in path or "download" in path or path.endswith((".pdf", ".udf", ".zip", ".doc", ".docx", ".xls", ".xlsx")):
        return "download"
    if href.startswith("#"):
        return "same_page"
    return "unknown"


def _icon_tokens(el) -> list[str]:
    """Eylem elemanının KENDİ + torun ikon metadata token'ları (class / svg <title> / <use href=#frag> / alt/aria).

    Ham SVG/markup SAKLANMAZ; yalnız kısa, güvenli semantik token'lar. Renk ASLA kullanılmaz.
    """
    toks: list[str] = list(_safe_class_tokens(el.get("class")))
    try:
        descendants = el.find_all(True)
    except Exception:
        descendants = []
    for d in descendants[:24]:
        try:
            toks += _safe_class_tokens(d.get("class"))
            for k in ("title", "aria-label", "alt"):
                v = d.get(k)
                if v:
                    toks.append(_ascii_lower(_demojibake(str(v)))[:24])
            for hk in ("href", "xlink:href"):
                hv = d.get(hk)
                if hv and str(hv).startswith("#"):
                    toks.append(_ascii_lower(str(hv).lstrip("#"))[:24])
            if d.name == "title":  # SVG <title>Görüntüle</title>
                tt = d.get_text(" ", strip=True)
                if tt:
                    toks.append(_ascii_lower(_demojibake(tt))[:24])
        except Exception:
            continue
    return _bounded_tokens(toks)


def _handler_tokens(el) -> list[str]:
    """onclick gövdesinden/href YOLUNDAN yalnız GÜVENLİ semantik token'lar (gövde/sorgu ASLA saklanmaz)."""
    toks: list[str] = []
    blob = _ascii_lower(_demojibake(str(el.get("onclick") or "")))
    href_path = _ascii_lower(str(el.get("href") or "")).split("?", 1)[0]
    for w in ("goruntule", "goster", "onizle", "preview", "view", "viewer", "indir", "download", "kaydet"):
        if w in blob or w in href_path:
            toks.append(w)
    return sorted(set(toks))


def _row_actionable_controls(el) -> list:
    """Fix 7: satır içindeki GERÇEK tıklanabilir kontroller (button / a / [role=button] / [onclick]),
    iç içe düzleştirilmiş. Bare ``<i>``/``<svg>`` ikon descendant'ları DAHİL EDİLMEZ — bunlar sahip
    (owning) actionable kontrolün metadata'sıdır, bağımsız ActionSpec DEĞİLdİr."""
    try:
        primary = el.select("a, button, [role=button], [onclick]")
    except Exception:
        return []
    pid = {id(x) for x in primary}
    tops: list = []
    for c in primary:
        p = getattr(c, "parent", None)
        nested = False
        while p is not None and p is not el:
            if id(p) in pid:
                nested = True
                break
            p = getattr(p, "parent", None)
        if not nested:
            tops.append(c)
    return _dedupe_by_id(tops)


def _row_action_elements(el) -> list:
    """Satır içindeki eylem kontrolleri: GERÇEK tıklanabilirler; yoksa ikon-yalnız kontroller (Fix 6)."""
    tops = _row_actionable_controls(el)
    if tops:
        return tops
    try:  # ikon-yalnız satır (a/button/onclick yok): i/img/svg tıklanabilir kabul
        icons = [c for c in el.select("i, img, svg")
                 if (c.get("title") or c.get("aria-label") or c.get("class") or c.has_attr("onclick"))]
    except Exception:
        icons = []
    return _dedupe_by_id(icons)


def _dedupe_by_id(items: list) -> list:
    seen: set = set()
    out: list = []
    for x in items:
        if id(x) in seen:
            continue
        seen.add(id(x))
        out.append(x)
    return out


def _action_spec(el) -> dict:
    """Bir tıklanabilir eylem için zengin, gizlilik-güvenli metadata + deterministik semantik."""
    name = getattr(el, "name", "") or ""
    role = _ascii_lower(el.get("role") or "") or None
    title = (el.get("title") or "").strip()
    aria = (el.get("aria-label") or "").strip()
    alt = (el.get("alt") or "").strip()
    try:
        txt = el.get_text(" ", strip=True)
    except Exception:
        txt = ""
    accessible = _demojibake(title or aria or alt or txt or "")
    spec = {
        "tag": name,
        "role": role,
        "title": (title[:40] or None),
        "aria_label": (aria[:40] or None),
        "text": ((title or aria or alt or txt)[:40] or None),
        "accessible_name": (accessible[:40] or None),
        "href": el.get("href"),  # İÇSEL (canlı locate için); tanı ÖZETİNE ham girmez
        "href_kind": _href_kind(el),
        "download_attr": bool(hasattr(el, "has_attr") and el.has_attr("download")),
        "onclick_present": bool(hasattr(el, "has_attr") and el.has_attr("onclick")),
        "class_tokens": _safe_class_tokens(el.get("class")),
        "icon_tokens": _icon_tokens(el),
        "handler_tokens": _handler_tokens(el),
        "css": " ".join(el.get("class") or []),
    }
    spec["semantic"] = classify_action_semantic(spec)
    spec["kind"] = ("eye" if spec["semantic"] == "view"
                    else "download" if spec["semantic"] == "download" else "control")
    return spec


def classify_action_semantic(spec: dict) -> str:
    """Bir eylem kontrolünü deterministik ÖNCELİKLE sınıflar: download / view / ambiguous / unknown.

    Öncelik: (1) erişilebilirlik/title semantiği; (2) torun ikon token semantiği; (3) href/download
    attribute semantiği; (4) güvenli onclick/handler token semantiği; aksi halde unknown. KONUM/Nth
    KULLANMAZ; 'diğeri download olduğu için bu view' ÇIKARIMI YOK; yalnız POZİTİF kanıtla (Fix 6).
    """
    # (1) erişilebilirlik/title
    name = spec.get("accessible_name") or spec.get("title") or spec.get("aria_label") or spec.get("text") or ""
    v, d = _text_has_view(name), _text_has_download(name)
    if v and d:
        return "ambiguous"
    if v:
        return "view"
    if d:
        return "download"
    # (2) torun ikon token'ları (kendi class + torun ikon metadata)
    icon_blob = " ".join(list(spec.get("icon_tokens") or []) + list(spec.get("class_tokens") or [])
                         + [_ascii_lower(str(spec.get("css") or ""))])
    v, d = _text_has_view(icon_blob), _text_has_download(icon_blob)
    if v and d:
        return "ambiguous"
    if v:
        return "view"
    if d:
        return "download"
    # (3) href / download attribute
    if spec.get("download_attr"):
        return "download"
    hk = spec.get("href_kind")
    if hk == "viewer":
        return "view"
    if hk == "download":
        return "download"
    # (4) güvenli onclick/handler token'ları
    hblob = " ".join(spec.get("handler_tokens") or [])
    v, d = _text_has_view(hblob), _text_has_download(hblob)
    if v and d:
        return "ambiguous"
    if v:
        return "view"
    if d:
        return "download"
    return "unknown"


def _semantic_candidates(spec: dict) -> list[str]:
    """Tanı için: bu eylemde gözlenen (view/download) POZİTİF sinyaller (kesin çözüm değil)."""
    blob = " ".join([
        str(spec.get("accessible_name") or ""),
        " ".join(spec.get("icon_tokens") or []),
        " ".join(spec.get("class_tokens") or []),
        " ".join(spec.get("handler_tokens") or []),
        _ascii_lower(str(spec.get("css") or "")),
    ])
    cands: list[str] = []
    if _text_has_view(blob) or spec.get("href_kind") == "viewer":
        cands.append("view")
    if _text_has_download(blob) or spec.get("download_attr") or spec.get("href_kind") == "download":
        cands.append("download")
    return cands


def _action_summary(spec: dict, index: int) -> dict:
    """Gizlilik-güvenli, sınırlı per-action tanı özeti (ham href/onclick/evrakId/DOM ASLA)."""
    nm = spec.get("accessible_name")
    return {
        "local_index": index,
        "tag": spec.get("tag"),
        "role": spec.get("role"),
        "accessible_name_present": bool(nm),
        "accessible_name": (nm if nm and len(nm) <= 40 else None),
        "title": spec.get("title"),
        "aria_label": spec.get("aria_label"),
        "href_kind": spec.get("href_kind"),
        "download_attribute_present": bool(spec.get("download_attr")),
        "onclick_present": bool(spec.get("onclick_present")),
        "safe_class_tokens": (spec.get("class_tokens") or [])[:12],
        "descendant_icon_tokens": (spec.get("icon_tokens") or [])[:16],
        "semantic_candidates": _semantic_candidates(spec),
        "resolved_semantic": spec.get("semantic"),
    }


def _row_action_specs(el) -> list[dict]:
    """Bir satır elemanının EYLEM kontrollerini (tıklanabilir + ikon metadata) çıkarır (satır-yerel).

    İç içe tıklanabilirler DÜZLEŞTİRİLİR (``<a><i></i></a>`` → tek eylem; ``<i>`` ikon-metadata olur).
    Düz METİN etiketi EYLEM SAYILMAZ. Her spec zengin, gizlilik-güvenli aksiyon metadata taşır ve
    ``classify_action_semantic`` ile download/view/ambiguous/unknown olarak sınıflanır (Fix 6).
    """
    return [_action_spec(c) for c in _row_action_elements(el)]


def extract_panel_document_rows(html: str) -> list[dict]:
    """Aynı-sayfa belge panelinden satırları (etiket + satır-yerel eylemler) çıkarır (OFFLINE testable).

    Global Nth-eye KULLANILMAZ; her satır KENDİ eylemlerini taşır. Birden çok DİSTİNKT belge türü
    içeren elemanlar (tüm-panel container'ı) satır SAYILMAZ. Row-container seçicileri sırayla
    denenir; ilk ≥1 belge-etiketli satır üreteni kullanılır.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return []
    for sel in _PANEL_ROW_SELECTORS:
        rows: list[dict] = []
        seen: set = set()
        for el in soup.select(sel):
            label_text = el.get_text(" ", strip=True)
            if classify_document_label(label_text) is None:
                continue
            if len(_distinct_doc_types(label_text)) > 1:
                continue  # tüm-panel container'ı — satır değil
            key = re.sub(r"\s+", " ", _ascii_lower(label_text))[:80]
            if key in seen:
                continue
            seen.add(key)
            rows.append({"label": label_text, "actions": _row_action_specs(el)})
        if rows:
            return rows
    return []


def panel_has_documents(html: str) -> bool:
    """Aynı-sayfa panel HTML'inde ilgili belge etiketleri görünür mü (testable)."""
    return bool(extract_panel_document_rows(html))


# --- Fix 5: canlı GÖRÜNÜR modal/overlay algısı — semantik belge-listesi + ortak-ata konteyner --- #
def _has_doc_list_title(html: str) -> bool:
    """Belge-listesi başlığı/kontrol semantiği var mı (İhale Evrak Listesi / Evrak Listesi)."""
    fold = _ascii_lower(_demojibake(html or ""))
    return any(t in fold for t in DOCUMENT_LIST_CONTROL_TEXTS)


def _bs_soup(html: str):
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html or "", "html.parser")
    except Exception:
        return None


def _is_hidden(el) -> bool:
    """Eleman (ya da bir atası) görünür-değil mi: display:none / visibility:hidden / hidden / aria-hidden.

    Canlı Playwright görünürlüğü yerine OFFLINE HTML için kaba bir güvenlik: gizli şablon/kopya
    işaretlemesini açık-belge-listesi kanıtı SAYMAMAK için (run-4 ham-HTML yanlış-pozitifi dersi).
    """
    cur = el
    while cur is not None and getattr(cur, "name", None):
        if hasattr(cur, "get"):
            style = _ascii_lower(cur.get("style") or "").replace(" ", "")
            if "display:none" in style or "visibility:hidden" in style:
                return True
            if cur.has_attr("hidden"):
                return True
            if _ascii_lower(cur.get("aria-hidden") or "") == "true":
                return True
        cur = cur.parent
    return False


def _single_doc_type(text: str):
    """Metin TAM OLARAK bir belge türü içeriyorsa onu döner; aksi halde None (container/ilgisiz)."""
    atype = classify_document_label(text)
    if atype is None or len(_distinct_doc_types(text)) != 1:
        return None
    return atype


def _semantic_label_elements(soup) -> list:
    """GÖRÜNÜR, tek-belge-türü etiketini taşıyan EN KÜÇÜK elemanlar: ``[(el, atype, text)]``.

    Sabit satır seçicisi (tr/li/class*=row) KULLANMAZ; div/section/li fark etmez. Gizli
    (display:none/hidden/aria-hidden) şablon/kopya işaretleme YOK sayılır (yalnız görünür kanıt).
    """
    if soup is None:
        return []
    out: list = []
    seen: set = set()
    for el in soup.find_all(True):
        if el.name in ("script", "style"):
            continue
        text = el.get_text(" ", strip=True)
        atype = _single_doc_type(text)
        if atype is None or _is_hidden(el):
            continue
        # EN KÜÇÜK: aynı türe sınıflanan bir alt-eleman varsa bu eleman etiket değil (üst) — atla
        if any(_single_doc_type(d.get_text(" ", strip=True)) == atype for d in el.find_all(True)):
            continue
        key = (atype, re.sub(r"\s+", " ", _ascii_lower(_demojibake(text)))[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append((el, atype, text))
    return out


def _ancestor_chain(el) -> list:
    chain = []
    cur = el
    while cur is not None:
        chain.append(cur)
        cur = getattr(cur, "parent", None)
    return chain


def _nearest_common_ancestor(elements: list):
    """Verilen elemanların EN YAKIN ortak atası (kök-öncelikli hizalama)."""
    chains = [list(reversed(_ancestor_chain(e))) for e in elements if e is not None]
    if not chains:
        return None
    common = None
    for level in zip(*chains):
        if all(n is level[0] for n in level):
            common = level[0]
        else:
            break
    return common


def _container_strategy(anc) -> str:
    """Konteynerin (ya da yakın atasının) semantik türü: dialog / modal-class / ortak-ata."""
    cur = anc
    for _ in range(6):
        if cur is None or not getattr(cur, "name", None):
            break
        if cur.name == "dialog" or (hasattr(cur, "get") and _ascii_lower(cur.get("role") or "") == "dialog"):
            return "semantic_dialog"
        cls = _ascii_lower(" ".join(cur.get("class") or [])) if hasattr(cur, "get") else ""
        if "modal" in cls or "overlay" in cls or "dialog" in cls:
            return "semantic_modal_class"
        cur = cur.parent
    return "semantic_common_ancestor"


def detect_document_container(html: str) -> dict:
    """Görünür belge etiketlerinden ORTAK-ATA belge konteynerini bulur (OFFLINE testable).

    ``html``/``body``/tüm-sayfa ya da BİRDEN ÇOK kayıt içeren arama-sonuç container'ı SEÇİLMEZ.
    Konteyner ≥2 distinkt tanınan belge türü içermelidir. ``.modal``/``role=dialog`` GEREKMEZ
    (div/portal/overlay da olur); strateji buna göre raporlanır.
    """
    label_els = _semantic_label_elements(_bs_soup(html))
    types = sorted({atype for _, atype, _ in label_els})
    if len(types) < 2:
        return {"found": False, "strategy": "not_found", "recognized_types": types,
                "reason": "insufficient_document_types"}
    anc = _nearest_common_ancestor([el for el, _, _ in label_els])
    if anc is None or getattr(anc, "name", None) in ("html", "body", "[document]", None):
        return {"found": False, "strategy": "not_found", "recognized_types": types,
                "reason": "ancestor_too_broad"}
    anc_text = anc.get_text(" ", strip=True)
    file_nums = {re.sub(r"\s+", "", n) for n in re.findall(r"\d{3,4}\s*/\s*\d+", _ascii_lower(_demojibake(anc_text)))}
    if len(file_nums) > 1:
        return {"found": False, "strategy": "not_found", "recognized_types": types,
                "reason": "spans_multiple_records"}
    return {"found": True, "strategy": _container_strategy(anc),
            "recognized_types": sorted(_distinct_doc_types(anc_text)), "container_tag": anc.name}


def _semantic_row_for_label(label_el):
    """Fix 7: etiketten MANTIKSAL belge-satırı atasını bulur (tr/li VARSAYMAZ).

    GERÇEK tıklanabilir kontrol (button/a) içeren, TEK-belge-etiketli EN KÜÇÜK atayı seçer ve
    kardeş-actionable genişlemesiyle AYRI eye/view kontrolünü de kapsar (ikon descendant'lar ActionSpec
    DEĞİL, sahip actionable kontrolün metadata'sıdır). Modal/body/html ya da birden çok belge etiketi
    içeren ata SATIR SAYILMAZ. Döner: ``(row_el, [action_spec...], meta)``. OFFLINE testable.
    """
    label_kind = getattr(label_el, "name", None)
    # tek-belge-etiketli ata zinciri (birden çok belge → çok geniş, dur)
    chain: list = []
    cur = label_el
    for _ in range(8):
        if cur is None or not getattr(cur, "name", None):
            break
        if len(_distinct_doc_types(cur.get_text(" ", strip=True))) > 1:
            break
        chain.append(cur)
        cur = getattr(cur, "parent", None)

    def _meta(strategy, row_el, controls, tags):
        return {
            "row_boundary_strategy": strategy,
            "label_element_kind": label_kind,
            "logical_row_ancestor_kind": (getattr(row_el, "name", None)),
            "logical_row_recognized_type_count": len(_distinct_doc_types(row_el.get_text(" ", strip=True))),
            "logical_row_actionable_control_count": controls,
            "actionable_control_tags": tags[:8],
        }

    # (1) GERÇEK tıklanabilir kontrol içeren atalar; kardeş genişlemesiyle en uygun satır.
    best_el = None
    best_controls: list = []
    expanded = False
    for c in chain:
        controls = _row_actionable_controls(c)
        if not controls:
            continue
        if best_el is None:
            best_el, best_controls = c, controls
        elif len(controls) > len(best_controls):
            best_el, best_controls, expanded = c, controls, True  # actionable-sibling expansion
        else:
            break  # daha fazla actionable kontrol eklemiyor → durma (fazla genişleme YOK)
    if best_el is not None:
        strategy = "actionable_sibling_expansion" if expanded else "label_actionable_ancestor"
        specs = [_action_spec(x) for x in best_controls]
        return best_el, specs, _meta(strategy, best_el, len(best_controls), [getattr(x, "name", "") or "" for x in best_controls])

    # (2) tıklanabilir yoksa ikon-yalnız satır (Fix 6): eyleme sahip en küçük ata.
    for c in chain:
        specs = _row_action_specs(c)
        if specs:
            return c, specs, _meta("icon_only_ancestor", c, len(specs), [s.get("tag") or "" for s in specs])

    fallback = _row_action_specs(label_el)
    return label_el, fallback, _meta("unresolved", label_el, len(fallback), [s.get("tag") or "" for s in fallback])


def extract_document_rows_semantic(html: str) -> list[dict]:
    """Sabit satır seçicisi OLMADAN, semantik etiket-anchoring ile belge satırlarını çıkarır.

    Her tanınan GÖRÜNÜR etiket → MANTIKSAL satır atası (Fix 7: kardeş eye kontrolünü kapsar) → birleşik
    DocumentRow (``{"label", "actions", "row_boundary"}``). Gerçek UYAP overlay'i div/portal olabilir;
    tr/li/class*=row GEREKMEZ. ``extract_panel_document_rows`` ile aynı soyutlamayı üretir. OFFLINE testable.
    """
    rows: list[dict] = []
    seen: set = set()
    for el, _atype, text in _semantic_label_elements(_bs_soup(html)):
        key = re.sub(r"\s+", " ", _ascii_lower(_demojibake(text)))[:80]
        if key in seen:
            continue
        seen.add(key)
        _row_el, actions, meta = _semantic_row_for_label(el)
        rows.append({"label": text, "actions": actions, "row_boundary": meta})
    return rows


def visible_document_types(html: str) -> list[str]:
    """Yalnızca GÖRÜNÜR etiketlerden tanınan belge türleri (pre/post-click imzası için)."""
    return sorted({atype for _, atype, _ in _semantic_label_elements(_bs_soup(html))})


def detect_document_list(html: str) -> dict:
    """Açık belge listesini GÖRÜNÜR semantikten algılar: başlık + ≥2 distinkt tür (başlık TEK BAŞINA yetmez)."""
    rows = extract_document_rows_semantic(html)
    types = sorted({t for t in (classify_document_label(r["label"]) for r in rows) if t})
    title = _has_doc_list_title(html)
    cont = detect_document_container(html)
    return {
        "detected": bool(title and len(types) >= 2 and cont["found"]),
        "title_present": title,
        "recognized_types": types,
        "labels": [r["label"] for r in rows],
        "n_rows": len(rows),
        "container_strategy": cont.get("strategy", "not_found"),
    }


def document_list_semantic_transition(before_html: str, after_html: str) -> dict:
    """Tıklama öncesi/sonrası GÖRÜNÜR belge-türü kümesindeki materyal geçişi algılar."""
    before = set(visible_document_types(before_html))
    after = set(visible_document_types(after_html))
    return {
        "pre_click_visible_document_types": sorted(before),
        "post_click_visible_document_types": sorted(after),
        "transition_detected": len(after) >= 2 and len(after - before) >= 1,
        "new_document_types": sorted(after - before),
    }


def document_container_kind_for_entry(page_state: str) -> str:
    """Giriş yolundan konteyner türü (CSS class'ından DEĞİL): search_listing → listing_modal,
    record_detail → same_page_tab_panel. Operatör görsel overlay'i gördü; DOM ``.modal`` gerekmez."""
    if page_state == "search_listing":
        return "listing_modal"
    if page_state == "record_detail":
        return "same_page_tab_panel"
    return "not_opened"


def preopened_document_list_reusable(html: str, url: str | None = None, target_file_id: str | None = None) -> bool:
    """Fix 7: Kart-kontrolüne TIKLAMADAN önce, GEÇERLİ görünür bir belge-listesi zaten açık mı?

    Katı koşullar (mevcut guard'lar zorunlu): (1) desteklenen belge-giriş sayfası (search_listing /
    record_detail); (2) hedef dosya kimliği sayfada görünür (candidate scoping — stale/ilgisiz liste
    yeniden KULLANILMAZ); (3) ``detect_document_list`` geçerli (başlık + ≥2 distinkt tür + sınırlı
    ortak-ata konteyner; gizli şablon / ham-HTML-yalnız etiketler görünürlük guard'larıyla ELENİR).
    Bağlanamıyorsa yeniden kullanılmaz. OFFLINE testable.
    """
    ps = classify_page_state(html, url)
    if ps not in ("search_listing", "record_detail"):
        return False
    if target_file_id and not file_identity_matches(html, target_file_id):
        return False  # bu sayfa hedef adaya ait değil → stale/ilgisiz liste kullanılmaz
    return bool(detect_document_list(html).get("detected"))


def resolve_row_view_action(actions: list[dict]) -> dict:
    """Satır-yerel GÖRÜNTÜLE/view eylemini POZİTİF semantikle çözer (Fix 6). KEYFİ tıklama YOK.

    Her eylem ``classify_action_semantic`` ile download/view/ambiguous/unknown olur. YALNIZCA tam
    olarak BİR eylem pozitif 'view' ise çözülür. 'Diğeri download olduğu için bu view' ÇIKARIMI YOK;
    konum/Nth/sağdaki KULLANILMAZ. Sıfır/çok view adayı ya da belirsizlik → çözülemez. OFFLINE testable.
    """
    acts = list(actions or [])
    classified = [(a, a.get("semantic") or classify_action_semantic(a)) for a in acts]
    views = [a for a, s in classified if s == "view"]
    downloads = [a for a, s in classified if s == "download"]
    ambiguous = [a for a, s in classified if s == "ambiguous"]
    dl_detected = bool(downloads)
    dl_action = downloads[0] if len(downloads) == 1 else None  # POZİTİF tek download (Fix 6.1 fallback için)
    base = {"download_action": dl_action, "download_action_resolved": dl_action is not None,
            "download_action_detected": dl_detected}
    if len(views) == 1:
        return {"view_action": views[0], "resolved": True, "reason": "positive_view", "view_semantic": "view", **base}
    if len(views) > 1:
        return {"view_action": None, "resolved": False, "reason": "ambiguous_multiple_view_candidates", **base}
    if acts and downloads and len(downloads) == len(acts):
        return {"view_action": None, "resolved": False, "reason": "download_only", **base}
    if ambiguous:
        return {"view_action": None, "resolved": False, "reason": "ambiguous_action_semantics", **base}
    return {"view_action": None, "resolved": False, "reason": "no_view_action", **base}


def classify_document_list_container(observed: dict) -> str:
    """Belge listesi nasıl açıldı: modal / dialog / aynı-sayfa tab paneli / yeni sayfa / açılmadı."""
    if observed.get("modal_visible"):
        return "modal"
    if observed.get("dialog_visible"):
        return "dialog"
    if observed.get("panel_labels_visible"):
        return "same_page_tab_panel"
    if observed.get("new_page"):
        return "new_page"
    return "not_opened"


def classify_viewer_url(url: str) -> str:
    """UYAP görüntüleyici URL desenini sınıflandırır (viewer.jsp?mimeType=Udf → udf_viewer)."""
    low = _ascii_lower(url or "").replace(" ", "")
    if "mimetype=udf" in low:
        return "udf_viewer"
    if low.endswith(".pdf") or "mimetype=pdf" in low:
        return "pdf_viewer"
    if "viewer.jsp" in low or "goruntule" in low:
        return "document_viewer"
    return "html"


def viewer_mime_hint(url: str) -> str | None:
    m = re.search(r"mimetype=([a-z0-9]+)", _ascii_lower(url or "").replace(" ", ""))
    return m.group(1) if m else None


def classify_viewer_representation(counts: dict) -> str:
    """Görüntüleyici belge temsili (Fix 8: kesin isimlendirme — canvas ve image AYRILIR).

    dom_text / iframe / embed_object / canvas_and_image / canvas_only / image_only / unknown.
    Canvas gözlenmediyse ``canvas_*`` RAPORLANMAZ (Run-8: canvas=0, image=1 → image_only).
    """
    if counts.get("text_available"):
        return "dom_text"
    if counts.get("iframe", 0) > 0:
        return "iframe"
    if counts.get("embed", 0) > 0 or counts.get("object", 0) > 0:
        return "embed_object"
    canvas = counts.get("canvas", 0) > 0
    image = counts.get("image", 0) > 0
    if canvas and image:
        return "canvas_and_image"
    if canvas:
        return "canvas_only"
    if image:
        return "image_only"
    return "unknown"


# Fix 8: bir belge-render GÖRÜNTÜsü içeren (dolayısıyla kaynak-yakalama denenebilecek) temsiller.
IMAGE_VIEWER_REPRESENTATIONS = ("image_only", "canvas_and_image")



# --- Fix 6.1: görüntüleyici SONUÇ sınıflandırması + indirme-gerekli semantiği + çıkarım-desteği --- #
def viewer_download_instruction_detected(text: str) -> bool:
    """GERÇEK gözlenen 'Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz.' semantiği (KATI).

    Görüntüleme-başarısızlığı VE indirerek-görüntüleme yönergesi BİRLİKTE gerekir. Yalın ``indir`` ya
    da genel program-indirme yönergesi YETERSİZ. Noktalama/büyük-küçük harf zorunlu değil; Türkçe fold +
    kısıtlı mojibake onarımı uygulanır.
    """
    fold = _ascii_lower(_demojibake(text or ""))
    fold = re.sub(r"\s+", " ", fold)
    failed = "goruntulenemedi" in fold or "goruntulenemiyor" in fold or "goruntulenememektedir" in fold
    instruct = ("indirerek goruntule" in fold or "indirerek inceleyebilir" in fold
                or "indirerek acabilir" in fold)
    return bool(failed and instruct)


def classify_viewer_outcome(text: str, representation: str | None = None) -> str:
    """Görüntüleyici sonucunu deterministik sınıflar (Fix 6.1 + Fix 8).

    content_available / download_required / image_backed / unsupported_representation / viewer_error /
    unknown. ``download_required`` YALNIZCA görüntüleme-başarısızlığı + indirme yönergesi birlikteyken;
    viewer-hata/indirme-gerekli semantiği görüntü-varlığından ÖNCE gelir (Fix 6.1 tetikleyicisi katı kalır).
    Görüntü-destekli temsil (image_only / canvas_and_image) → ``image_backed`` (Fix 8 kaynak-yakalama).
    """
    if viewer_download_instruction_detected(text):
        return "download_required"
    fold = re.sub(r"\s+", " ", _ascii_lower(_demojibake(text or "")))
    if representation == "dom_text" or re.search(r"ihale bedeli|artirma sonuc|muhammen|alacaga mahsuben", fold):
        return "content_available"
    if "goruntulenemedi" in fold or "goruntulenemiyor" in fold or ("evrak" in fold and "hata" in fold):
        return "viewer_error"
    if representation in IMAGE_VIEWER_REPRESENTATIONS:
        return "image_backed"
    if representation in ("iframe", "embed_object", "canvas_only", "unknown"):
        return "unsupported_representation"
    return "unknown"


# Deterministik metin çıkarımı GERÇEKTEN desteklenen formatlar (ham UDF/PDF/ikili/GÖRÜNTÜ DEĞİL).
EXTRACTABLE_ARTIFACT_EXTENSIONS = (".txt", ".html", ".htm")

# Fix 12: satır-yerel resmî .udf indirmenin NATIVE konteyner çıkarımıyla TERCİH EDİLDİĞİ evidence türleri.
# auction_result öncelik-1 (ölçülen native artifact); sale_notice viewer yolu KORUNUR (native opsiyonel/bloklayıcı DEĞİL).
NATIVE_DOWNLOAD_TYPES = (ARTIFACT_AUCTION_RESULT,)
# Native UDF konteyner uzantıları (destek YALNIZCA doğrulanmış yapı + content.xml ile; uzantı tek başına YETMEZ).
NATIVE_UDF_EXTENSIONS = (".udf",)


def select_unique_document_row(recognized_rows: list, artifact_type: str, normalized_label: str | None = None) -> dict | None:
    """Fix 13: recognized satırlar arasından İSTENEN kimlikle EŞSİZ mantıksal DocumentRow'u döner.

    ``artifact_type`` (ve verilirse ``normalized_label``) eşleşen TEK satır; 0 ya da >1 → None (belirsiz →
    indirme YAPILMAZ). Çok-kimlikli satır (``logical_row_recognized_type_count`` > 1) reddedilir. Böylece
    native indirme, çözülen satırın KİMLİĞİNE bağlanır; global/ilk/Nth eşleşmeye DÜŞMEZ.
    """
    def _norm(s: object) -> str:
        return _ascii_lower(_demojibake(str(s or "")))[:60]

    want = _norm(normalized_label) if normalized_label else None
    matches = []
    for r in recognized_rows or []:
        if r.get("artifact_type") != artifact_type:
            continue
        if r.get("logical_row_recognized_type_count") not in (None, 1):
            continue
        if want is not None and _norm(r.get("normalized_label")) != want:
            continue
        matches.append(r)
    return matches[0] if len(matches) == 1 else None


def extraction_supported_for(extension: str | None, mime_hint: str | None = None) -> bool:
    """İndirilen artifact deterministik metin çıkarımı için GERÇEKTEN destekleniyor mu (dürüst).

    Repo yalnız düz-metin/HTML'den deterministik metin çıkarır; ham ``.udf`` / ``.pdf`` / GÖRÜNTÜ
    (png/jpeg) / ikili DESTEKLENMEZ. ``mimeType=Udf`` URL ipucu tek başına destek ANLAMINA GELMEZ.
    """
    ext = (extension or "").strip().lower()
    if not ext.startswith(".") and ext:
        ext = "." + ext
    return ext in EXTRACTABLE_ARTIFACT_EXTENSIONS


# --- Fix 8: görüntü-destekli görüntüleyici temsili — kaynak introspeksiyonu + kesin bayt yakalama (OCR YOK) --- #
_IMAGE_MIME_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/tiff": ".tiff", "image/bmp": ".bmp", "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}


def image_mime_to_extension(mime: str | None) -> str | None:
    """Güvenli MIME → uzantı eşlemesi (png/jpeg/... ; bilinmiyorsa None)."""
    return _IMAGE_MIME_EXT.get((mime or "").split(";")[0].strip().lower())


def classify_image_src_kind(src: str | None) -> str:
    """Bir görüntü src'sinin KAYNAK TÜRÜ (ham URL DÖNMEZ): data_url / blob_url / http_resource /
    relative_resource / empty / unknown. Yalnız yakalama-stratejisi kararı için."""
    s = str(src or "").strip()
    if not s:
        return "empty"
    low = s.lower()
    if low.startswith("data:"):
        return "data_url"
    if low.startswith("blob:"):
        return "blob_url"
    if low.startswith(("http://", "https://", "//")):
        return "http_resource"
    if low.startswith("javascript:"):
        return "unknown"
    return "relative_resource"


def image_source_capture_supported(src_kind: str | None, same_origin: bool = True) -> dict:
    """Bu kaynak türü deterministik tarayıcı-erişimli bayt yakalamayı destekliyor mu (OCR YOK)."""
    if src_kind == "data_url":
        return {"supported": True, "strategy": "data_url_decode"}
    if src_kind == "blob_url":
        return {"supported": True, "strategy": "blob_scoped_fetch"}
    if src_kind == "relative_resource":
        return {"supported": True, "strategy": "same_origin_page_fetch"}
    if src_kind == "http_resource":
        if same_origin:
            return {"supported": True, "strategy": "same_origin_page_fetch"}
        return {"supported": False, "strategy": None, "reason": "cross_origin_source_not_captured"}
    return {"supported": False, "strategy": None, "reason": f"unsupported_source_kind:{src_kind}"}


def classify_document_image_candidate(meta: dict, min_dimension: int = 200) -> dict:
    """Bir görüntünün belge-RENDER adayı mı yoksa dekoratif (logo/ikon/loading) mi olduğunu belirler.

    Deterministik kanıt: görünür + materyal boyut (naturel ya da render ≥ ``min_dimension``) + görüntüleyici
    içerik kapsamında (header/nav/footer/logo DEĞİL). GÖRSEL METİN / OCR KULLANILMAZ. OFFLINE testable.
    """
    if not meta.get("visible"):
        return {"document_image_candidate": False, "candidate_reason": "not_visible"}
    src_kind = meta.get("src_kind") or classify_image_src_kind(meta.get("src") or meta.get("current_src"))
    if src_kind == "empty":
        return {"document_image_candidate": False, "candidate_reason": "empty_src"}
    nw, nh = int(meta.get("natural_width") or 0), int(meta.get("natural_height") or 0)
    rw, rh = int(meta.get("rendered_width") or 0), int(meta.get("rendered_height") or 0)
    if max(nw, nh, rw, rh) < min_dimension:
        return {"document_image_candidate": False, "candidate_reason": "too_small_icon_or_logo"}
    if not meta.get("viewer_content_scoped", True):
        return {"document_image_candidate": False, "candidate_reason": "outside_viewer_content_scope"}
    return {"document_image_candidate": True, "candidate_reason": "material_visible_scoped_image"}


def select_viewer_image_candidate(candidates: list[dict]) -> int | None:
    """Belge-render adayları arasından EN BÜYÜK materyal olanı seçer (global ilk görüntü DEĞİL).

    Yalnız ``document_image_candidate`` True olanlar arasından; aday yoksa None. OFFLINE testable.
    """
    docs = [(i, c) for i, c in enumerate(candidates or []) if c.get("document_image_candidate")]
    if not docs:
        return None

    def _area(c: dict) -> int:
        return max(int(c.get("natural_width") or 0) * int(c.get("natural_height") or 0),
                   int(c.get("rendered_width") or 0) * int(c.get("rendered_height") or 0))

    docs.sort(key=lambda ic: (_area(ic[1]), -ic[0]), reverse=True)
    return docs[0][0]


def decode_data_url(data_url: str) -> tuple | None:
    """``data:`` URL'yi KESİN baytlara çözer → ``(bytes, mime, ext)``; geçersizse None. OFFLINE testable.

    Bayt gövdesi tanılara ASLA yazılmaz (yalnız yakalama içindir).
    """
    m = re.match(r"data:([^;,]*)((?:;[^,]*)*)?,(.*)", str(data_url or ""), re.DOTALL)
    if not m:
        return None
    mime = (m.group(1) or "application/octet-stream").strip().lower() or "application/octet-stream"
    params = (m.group(2) or "").lower()
    body = m.group(3) or ""
    try:
        if ";base64" in params:
            import base64
            data = base64.b64decode(body)
        else:
            from urllib.parse import unquote_to_bytes
            data = unquote_to_bytes(body)
    except Exception:
        return None
    return data, mime, (image_mime_to_extension(mime) or ".bin")


def _safe_image_ext_hint(src: str | None) -> str | None:
    """Ham URL/sorgu OLMADAN, src'den güvenli uzantı ipucu (data: → MIME; blob: → None)."""
    s = str(src or "")
    low = s.lower()
    if low.startswith("data:"):
        mm = re.match(r"data:([^;,]+)", s)
        return image_mime_to_extension(mm.group(1)) if mm else None
    if low.startswith("blob:") or not s:
        return None
    path = s.split("?", 1)[0].split("#", 1)[0]
    suf = Path(path).suffix.lower()
    return suf if suf and len(suf) <= 8 else None


def viewer_image_candidate_summary(meta: dict, index: int, mime_hint: str | None = None) -> dict:
    """Gizlilik-güvenli görüntü-aday özeti (ham src/blob/data-gövde/evrakId ASLA). OFFLINE testable."""
    src = meta.get("src") or meta.get("current_src")
    src_kind = classify_image_src_kind(src)
    cand = classify_document_image_candidate({**meta, "src_kind": src_kind})
    cap = image_source_capture_supported(src_kind, bool(meta.get("same_origin", True)))
    return {
        "local_index": index,
        "visible": bool(meta.get("visible")),
        "natural_width": int(meta.get("natural_width") or 0),
        "natural_height": int(meta.get("natural_height") or 0),
        "rendered_width": int(meta.get("rendered_width") or 0),
        "rendered_height": int(meta.get("rendered_height") or 0),
        "src_kind": src_kind,
        "current_src_kind": classify_image_src_kind(meta.get("current_src")),
        "same_origin": bool(meta.get("same_origin", True)),
        "extension_hint": _safe_image_ext_hint(src),
        "mime_hint": mime_hint,
        "viewer_content_scoped": bool(meta.get("viewer_content_scoped", True)),
        "document_image_candidate": bool(cand.get("document_image_candidate")),
        "candidate_reason": cand.get("candidate_reason"),
        "source_capture_supported": bool(cap.get("supported")),
        "source_capture_strategy": cap.get("strategy"),
    }


# --- Fix 9: görüntüleyici hazır-durum gözlemi + belge-render kimliği + cross-document generic-asset guard --- #
# Sınırlı (bounded) kararlılık gözlemi — SONSUZ bekleme YOK, keyfi uzun sleep YOK.
VIEWER_STABILIZATION_MIN_OBSERVATIONS = 2   # ard arda aynı imza sayısı → kararlı
VIEWER_STABILIZATION_MAX_OBSERVATIONS = 3   # üst sınır (bounded)
VIEWER_STABILIZATION_POLL_MS = 400          # gözlemler arası KISA sınırlı bekleme


def viewer_image_fingerprint(meta: dict) -> str | None:
    """Fix 9: gizlilik-güvenli DOM kaynak parmak izi (ham URL/BAYT YOK) — kararlılık gözlemi için.

    Yalnız ``(naturalW, naturalH, src_kind, güvenli-uzantı-ipucu)`` üzerinden kısa hash. İçerik/piksel/
    bayt DEĞİL; iki farklı görüntüleyicide aynı boyutlu asset AYNI parmak izini verebilir — cross-document
    BAYT kimliği ayrı (tam SHA) ile yakalanır. Placeholder → belge-render geçişini ucuza saptar.
    """
    if not meta:
        return None
    src = meta.get("src") or meta.get("current_src")
    sk = meta.get("src_kind") or classify_image_src_kind(src)
    basis = (f"{int(meta.get('natural_width') or 0)}x{int(meta.get('natural_height') or 0)}"
             f"|{sk}|{_safe_image_ext_hint(src) or ''}")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def viewer_observation_signature(obs: dict) -> tuple:
    """Tek görüntüleyici gözleminin deterministik, gizlilik-güvenli imzası (ham URL/bayt YOK)."""
    return (
        obs.get("representation"),
        int(obs.get("candidate_count") or 0),
        obs.get("selected_dimension"),
        obs.get("selected_src_kind"),
        obs.get("selected_fingerprint"),
    )


def classify_viewer_ready_state(observations: list, min_stable: int = VIEWER_STABILIZATION_MIN_OBSERVATIONS) -> dict:
    """Fix 9: sınırlı görüntüleyici kararlılık kararı (SAF, offline-testable).

    ready_state ∈ {stable_image_representation, stable_text_representation, download_required,
    viewer_error, timeout_unstable}. İndirme-gerekli / viewer-hata semantiği kararlılığı KESER ve
    ÖNCELİKLİDİR (Fix 6.1 katı önceliği korunur). İmza son ``min_stable`` gözlemde aynıysa kararlı;
    değişmeye devam ediyorsa DÜRÜSTÇE ``timeout_unstable``. İlk uygun görüntü TEK BAŞINA kararlı sayılmaz.
    """
    obs = list(observations or [])
    count = len(obs)
    if count == 0:
        return {"ready_state": "timeout_unstable", "observation_count": 0,
                "transition_detected": False, "blocking_reason": "no_viewer_observation"}
    transition = len({viewer_observation_signature(o) for o in obs}) > 1
    # Fix 6.1 önceliği: indirme-gerekli / viewer-hata kararlılığı ANINDA keser.
    for o in obs:
        if o.get("download_required"):
            return {"ready_state": "download_required", "observation_count": count,
                    "transition_detected": transition, "blocking_reason": None}
        if o.get("viewer_error"):
            return {"ready_state": "viewer_error", "observation_count": count,
                    "transition_detected": transition, "blocking_reason": None}
    sigs = [viewer_observation_signature(o) for o in obs]
    tail = sigs[-min_stable:]
    stable = len(tail) >= min_stable and len(set(tail)) == 1
    rep = obs[-1].get("representation")
    if stable and rep == "dom_text":
        state, reason = "stable_text_representation", None
    elif stable and rep in IMAGE_VIEWER_REPRESENTATIONS:
        state, reason = "stable_image_representation", None
    elif stable:
        state, reason = "timeout_unstable", f"stable_but_unsupported_representation:{rep}"
    else:
        state, reason = "timeout_unstable", "viewer_representation_did_not_stabilize"
    return {"ready_state": state, "observation_count": count,
            "transition_detected": transition, "blocking_reason": reason}


def detect_cross_document_image_duplicates(captures: list) -> dict:
    """Fix 9: TEK pilot toplamada, EXACT full-SHA256 görüntü kimliği ≥2 AYRI artifact türünde paylaşılıyor mu.

    Run-9: auction_result görüntüsü == sale_notice görüntüsü (aynı bayt). SAF; TAM SHA karşılaştırması
    (kısa prefix DEĞİL). Aynı türün kendini tekrarı cross-document sayılmaz (ayrık türler gerekir).
    """
    by_sha: dict = {}
    for c in captures or []:
        sha = c.get("sha256")
        atype = c.get("artifact_type")
        if not sha or not atype:
            continue
        types = by_sha.setdefault(sha, [])
        if atype not in types:
            types.append(atype)
    duplicate_shas = {sha: types for sha, types in by_sha.items() if len(types) >= 2}
    all_types = sorted({t for types in duplicate_shas.values() for t in types})
    return {"duplicate": bool(duplicate_shas), "duplicate_shas": duplicate_shas,
            "duplicate_artifact_types": all_types}


def classify_viewer_image_document_identity(capture: dict, duplicates: dict | None = None,
                                            association_supported: bool = False) -> str:
    """Fix 9: yakalanan görüntüleyici görüntüsünün belge-render KİMLİĞİ (deterministik; ML YOK).

    not_document_candidate / shared_cross_document_asset / generic_viewer_asset / document_specific /
    renderer_asset_unresolved. Cross-document tam-SHA paylaşımı → ``shared_cross_document_asset``. Pozitif
    belge-render ilişkisi KANITLANMADAN (association) tek yakalama ``renderer_asset_unresolved`` kalır —
    görünür + materyal + kapsamlı + bayt-yakalandı YETMEZ (yalnız bir görüntüleyici-asset yakalandığını kanıtlar).
    """
    if not capture or not capture.get("sha256"):
        return "not_document_candidate"
    sha = capture.get("sha256")
    if sha in (duplicates or {}).get("duplicate_shas", {}):
        return "shared_cross_document_asset"
    if capture.get("generic_viewer_asset"):
        return "generic_viewer_asset"
    if association_supported or capture.get("document_render_association_supported"):
        return "document_specific"
    return "renderer_asset_unresolved"


def resolve_viewer_image_identities(captures: list) -> list:
    """Fix 9: cross-document tam-SHA guard + kayıt-başı belge kimliği + promosyon kararı (SAF).

    Girdi ``captures``: ``[{"artifact_type", "sha256"(TAM), "extension", ...}]``. Döner paralel liste:
    her kayıt için ``viewer_image_document_identity`` + cross_document_duplicate + duplicate_artifact_types
    + ``promote_as_document_source`` (YALNIZCA ``document_specific`` iken True). Paylaşılan/çözülmemiş asset
    ASLA iki satırın belge kaynağı olarak bağımsızca PROMOTE EDİLMEZ.
    """
    dup = detect_cross_document_image_duplicates(captures)
    out = []
    for c in captures or []:
        sha = c.get("sha256")
        identity = classify_viewer_image_document_identity(c, dup)
        out.append({
            "artifact_type": c.get("artifact_type"),
            "viewer_image_document_identity": identity,
            "cross_document_duplicate": sha in dup["duplicate_shas"],
            "duplicate_artifact_types": dup["duplicate_shas"].get(sha, []),
            "promote_as_document_source": identity == "document_specific",
        })
    return out


def classify_view_access_pattern(container_kind: str, observed: dict) -> str:
    """Belge-listesi konteyneri + görüntüleme olayından erişim desenini adlandırır (uydurma YOK)."""
    prefix = "same_page_tab" if container_kind == "same_page_tab_panel" else (
        "modal" if container_kind in ("modal", "dialog", "listing_modal") else (container_kind or "unknown"))
    if observed.get("new_page"):
        if observed.get("is_udf"):
            return f"{prefix}_new_tab_udf_viewer"
        if observed.get("is_pdf"):
            return f"{prefix}_new_tab_pdf"
        return f"{prefix}_new_tab_html"
    if observed.get("same_page_nav") or observed.get("viewer_visible"):
        return f"{prefix}_same_page_viewer"
    if observed.get("download"):
        return f"{prefix}_download"
    return f"{prefix}_unsupported"


# --- Fix 3: sayfa-durumu farkında, HEDEF-KAYIT-kapsamlı belge girişi (iki gerçek yol) --- #
# --- Fix 4: SAĞLAM AKTİF-SAYFA durum sınıflandırması + yanlış-pozitif önleme (kanıt önceliği) --- #
def _active_viewer_url(url: str | None) -> str | None:
    """YALNIZCA AKTİF sayfa URL'sinin yol/sorgu bileşeninden görüntüleyici türünü çıkarır.

    HTML içindeki gömülü/gezinti URL'leri DEĞİL — yalnızca canlı Playwright ``page.url``. Aktif yol
    ``viewer.jsp`` (veya ``/viewer``) ise ve sorgu ``mimeType=Udf`` içeriyorsa ``udf_viewer``.
    """
    try:
        p = urlparse(url or "")
    except Exception:
        return None
    path = _ascii_lower(p.path)
    query = _ascii_lower(p.query).replace(" ", "")
    is_viewer_path = path.endswith("viewer.jsp") or path.rstrip("/").endswith("/viewer")
    if not is_viewer_path:
        return None
    if "mimetype=udf" in query:
        return "udf_viewer"
    if "mimetype=pdf" in query:
        return "pdf_viewer"
    return "document_viewer"


def _safe_url_kind(url: str | None) -> str:
    """Gizlilik-güvenli KABA URL türü (sorgu/kişisel veri YOK) — tanı özetleri için."""
    v = _active_viewer_url(url)
    if v:
        return v
    try:
        path = _ascii_lower(urlparse(url or "").path)
    except Exception:
        path = ""
    if path.endswith("index.jsp"):
        return "listing_or_search"
    if "detay" in path:
        return "detail"
    if path.endswith(".jsp"):
        return "uyap_jsp"
    return "other" if path else "unknown"


def _page_state_evidence(html: str, url: str | None) -> tuple[str, list[str]]:
    """AKTİF sayfa durumunu deterministik KANIT ÖNCELİĞİYLE sınıflandırır (Fix 4).

    Öncelik: (1) güçlü aktif-URL görüntüleyici kanıtı → udf_viewer; (2) güçlü görünür DETAY
    semantiği → record_detail; (3) güçlü görünür LİSTELEME semantiği → search_listing; (4)
    kalan detay semantiği → record_detail; (5) yalnızca listeleme/detay YOKKEN görüntüleyici
    semantiği → udf_viewer; aksi halde unknown. HAM HTML içindeki zayıf ``viewer.jsp`` /
    ``mimeType=Udf`` referansları AKTİF sayfa durumunu ASLA geçersiz kılmaz (kayda geçer, yok sayılır).
    """
    fold = _ascii_lower(html or "")
    despaced = fold.replace(" ", "")
    evidence: list[str] = []
    weak_viewer_ref = ("viewer.jsp" in fold) or ("mimetype=udf" in despaced)
    url_kind = _active_viewer_url(url)
    # 1) GÜÇLÜ aktif-URL görüntüleyici kanıtı (yalnız aktif URL)
    if url_kind == "udf_viewer":
        evidence.append("active_url_udf_viewer")
        return "udf_viewer", evidence
    detail_unique = "ihaleye ait tekliflerim" in fold
    detail_semantics = detail_unique or ("detayli inceleme" in fold)
    listing_semantics = bool(re.search(r"\bincele\b", fold)) and ("evrak listesi" in fold)
    # 2) DETAY önceliği yalnızca benzersiz teklif-sekmesi ifadesiyle
    if detail_unique and not listing_semantics:
        evidence.append("visible_detail_semantics")
        if weak_viewer_ref:
            evidence.append("weak_embedded_viewer_reference_ignored")
        return "record_detail", evidence
    # 3) GÜÇLÜ listeleme semantiği (zayıf görüntüleyici referansını geçersiz kılmaz)
    if listing_semantics:
        evidence.append("visible_listing_semantics")
        if weak_viewer_ref:
            evidence.append("weak_embedded_viewer_reference_ignored")
        return "search_listing", evidence
    # 4) kalan detay semantiği
    if detail_semantics:
        evidence.append("visible_detail_semantics")
        if weak_viewer_ref:
            evidence.append("weak_embedded_viewer_reference_ignored")
        return "record_detail", evidence
    # 5) görüntüleyici semantiği YALNIZCA listeleme/detay yokken
    if "evrak goruntuleme" in fold:
        evidence.append("visible_viewer_semantics")
        return "udf_viewer", evidence
    if url_kind in ("document_viewer", "pdf_viewer"):
        evidence.append("active_url_viewer")
        return "udf_viewer", evidence
    return "unknown", evidence


def classify_page_state(html: str, url: str | None = None) -> str:
    """UYAP AKTİF sayfa durumu: search_listing / record_detail / udf_viewer / unknown.

    Fix 4: AKTİF sayfa URL/state kanıtı önce; görünür sayfa semantiği sonra; HAM HTML'deki zayıf
    görüntüleyici referansları AKTİF durumu geçersiz KILMAZ (üçüncü/dördüncü canlı hata önlenir).
    """
    return _page_state_evidence(html, url)[0]


def page_state_evidence(html: str, url: str | None = None) -> dict:
    """Sınıflandırma + gizlilik-güvenli kanıt etiketleri (tanı için; ham HTML DÖNDÜRMEZ)."""
    state, evidence = _page_state_evidence(html, url)
    return {"page_state": state, "evidence": evidence}


def normalize_file_identity(file_id: str) -> str:
    """'2026/263 Esas' / '2026/263 Icra' → '2026/263' (Esas/İcra listeleme ALIAS'ı normalize)."""
    fold = _ascii_lower(file_id or "")
    m = re.search(r"(\d{3,4})\s*/\s*(\d+)", fold)
    return f"{m.group(1)}/{m.group(2)}" if m else fold.strip()


def file_identity_matches(text: str, target_file_id: str) -> bool:
    """Metin, hedef resmî dosya kimliğini içeriyor mu (Esas/İcra alias kabul; fiyat/P/Q DEĞİL)."""
    tnum = normalize_file_identity(target_file_id)
    if not tnum:
        return False
    fold = _ascii_lower(text or "")
    pat = re.escape(tnum).replace("/", r"\s*/\s*")
    return bool(re.search(pat, fold))


def classify_document_entry_path(page_state: str) -> str:
    """Sayfa durumundan belge-giriş yolunu türetir (listing_card_modal / detail_tab_panel)."""
    if page_state == "search_listing":
        return "listing_card_modal"
    if page_state == "record_detail":
        return "detail_tab_panel"
    return "unsupported"


def _card_control_labels(card_html: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(card_html or "", "html.parser")
        out = []
        for c in soup.select("a, button, [role=button]"):
            t = c.get_text(" ", strip=True)
            if t:
                out.append(t[:40])
        return out[:10]
    except Exception:
        return []


def find_target_record_card(html: str, target_file_id: str, institution: str | None = None) -> dict | None:
    """Listeleme sayfasında HEDEF kaydı resmî dosya kimliğiyle bulur (fiyat/P/Q/nth DEĞİL).

    Aynı dosya kimliğine sahip TEK kartı döndürür; birden çok distinkt dosya no içeren container
    (tüm-liste) satır/kart SAYILMAZ. İlk-global-kart ya da nth-kart seçilmez. OFFLINE testable.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:
        return None
    tnum = normalize_file_identity(target_file_id)
    for sel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "li", "tr", "div"):
        matches = []
        for c in soup.select(sel):
            text = c.get_text(" ", strip=True)
            if not file_identity_matches(text, target_file_id):
                continue
            nums = set(re.findall(r"\d{3,4}\s*/\s*\d+", _ascii_lower(text)))
            if len({re.sub(r"\s+", "", n) for n in nums}) != 1:
                continue  # birden çok kayıt → container, kart değil
            match_fields = ["file_id"]
            if institution:
                inst_tok = _ascii_lower(institution).split()
                if inst_tok and inst_tok[0] in _ascii_lower(text):
                    match_fields.append("institution")
            matches.append((c, re.sub(r"\s+", " ", text), match_fields))
        if len(matches) >= 1:
            c, text, match_fields = matches[0]
            return {
                "html": str(c),
                "file_text": text[:120],
                "match_fields": match_fields,
                "control_labels": _card_control_labels(str(c)),
                "target_file": tnum,
            }
    return None


def card_document_list_control(card_html: str) -> dict:
    """Kart-yerel AKSİYONE EDİLEBİLİR 'İhale Evrak Listesi' kontrolü (metin-yalnız YETERSİZ)."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(card_html or "", "html.parser")
    except Exception:
        return {"found": False, "actionable": False, "kind": None}
    for kind, sel in (("button", "button, [role=button]"), ("link", "a")):
        for el in soup.select(sel):
            if "evrak listesi" in _ascii_lower(el.get_text(" ", strip=True)):
                return {"found": True, "actionable": True, "kind": kind, "text": el.get_text(" ", strip=True)[:40]}
    if "evrak listesi" in _ascii_lower(soup.get_text(" ", strip=True)):
        return {"found": False, "actionable": False, "kind": "non_actionable_text_only"}
    return {"found": False, "actionable": False, "kind": None}


def select_target_page_index(candidates: list[dict], target_file_id: str | None = None) -> dict:
    """Birden çok açık sekme arasından HEDEF sayfayı deterministik/güvenli seçer (Fix 4).

    ``candidates``: her biri ``{"url":..., "html":...}``. Sıralama önceliği: desteklenen belge-giriş
    durumu (search_listing/record_detail) + hedef-kimlik eşleşmesi > desteklenen durum > kimlik
    eşleşmesi > search_listing > düşük indeks (operatör-aktif). Bir UDF görüntüleyici sekmesi
    YALNIZCA UYAP olduğu için TERCİH EDİLMEZ. Gizlilik-güvenli özetler döner (ham URL/DOM YOK).
    """
    seen: list[dict] = []
    for c in candidates:
        url = c.get("url", "") or ""
        html = c.get("html", "") or ""
        state = classify_page_state(html, url)
        seen.append({
            "url_kind": _safe_url_kind(url),
            "state": state,
            "target_identity_match": bool(target_file_id and file_identity_matches(html, target_file_id)),
        })

    def _rank(i: int):
        s = seen[i]
        supported = s["state"] in ("search_listing", "record_detail")
        return (
            supported and s["target_identity_match"],
            supported,
            s["target_identity_match"],
            s["state"] == "search_listing",
            -i,
        )

    idx = max(range(len(seen)), key=_rank) if seen else -1
    chosen = seen[idx] if idx >= 0 else {"url_kind": "unknown", "state": "unknown", "target_identity_match": False}
    return {
        "index": idx,
        "page_candidates_seen": seen,
        "selected_page_url_kind": chosen["url_kind"],
        "selected_page_state": chosen["state"],
        "selected_page_target_identity_match": chosen["target_identity_match"],
    }


def discover_document_links(html: str) -> list[dict]:
    """GERÇEK DOM'daki ``<a>`` bağlantılarından bilinen UYAP belge etiketlerini eşler.

    Uydurma DOM selector YOK: yalnızca gerçek bağlantı metni + ``href`` okunur. ``usable_href``
    False ise (``javascript:`` / href yok) canlı erişim deseni DESTEKLENMİYOR olarak raporlanır.
    Bu işlev OFFLINE test edilebilir (fixture HTML ile).
    """
    try:
        from bs4 import BeautifulSoup

        anchors = BeautifulSoup(html or "", "html.parser").find_all("a")
    except Exception:  # bs4 yoksa kaba <a href> taraması
        anchors = []
    out: list[dict] = []
    if not anchors:
        for m in re.finditer(r"<a[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>", html or "", re.IGNORECASE | re.DOTALL):
            href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
            _match_doc_link(text, href, out)
        return out
    for a in anchors:
        _match_doc_link(a.get_text(" ", strip=True), a.get("href"), out)
    return out


def _match_doc_link(text: str, href: str | None, out: list[dict]) -> None:
    atype = classify_document_label(text)
    if atype:
        usable = bool(href) and not str(href).strip().lower().startswith("javascript:")
        out.append({"text": text, "href": href, "artifact_type": atype, "usable_href": usable})



def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", str(name))
    return stem or "artifact"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_artifact(
    candidate: dict,
    artifact_type: str,
    source_path: str | Path | None = None,
    text: str | None = None,
    source_ref: str | None = None,
    note: str | None = None,
    store_dir: Path | str | None = None,
    persist: bool = True,
) -> dict:
    """Elle kaydedilmiş bir kaynak artifact'ını (HTML/PDF/metin) adaya ekler (offline yedek).

    Ham artifact ``<store_dir>/artifacts/<candidate_id>/`` altında tutulur (data/ .gitignore'da;
    analitik veri kümesine GİRMEZ). Yalnızca provenans (tür, sha256, yol, kaynak-ref) saklanır;
    kişisel veri normalize edilmez. ``text`` verilirse (fixture) inline saklanır ve isteğe bağlı
    diske yazılır. İçe aktarma ADMİSYON DEĞİLdİr.
    """
    cid = candidate["candidate_id"]
    sha = None
    local_path = None
    if source_path is not None:
        src = Path(source_path)
        data = src.read_bytes()
        sha = _sha256_bytes(data)
        if persist:
            dest_dir = Path(store_dir or store.DEFAULT_STORE_DIR) / "artifacts" / _safe_name(cid)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / _safe_name(src.name)
            shutil.copyfile(src, dest)
            local_path = str(dest)
    elif text is not None:
        sha = _sha256_bytes(text.encode("utf-8"))
        if persist:
            dest_dir = Path(store_dir or store.DEFAULT_STORE_DIR) / "artifacts" / _safe_name(cid)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{_safe_name(artifact_type)}_{sha[:8]}.txt"
            dest.write_text(text, encoding="utf-8")
            local_path = str(dest)

    artifact = SourceArtifact(
        artifact_type=artifact_type,
        local_path=local_path,
        sha256=sha,
        source_ref=source_ref,
        note=note,
    ).to_dict()
    # inline metni ayrıştırma aşaması için taşı (diske yazılmadıysa da çıkarım çalışsın)
    if text is not None:
        artifact["text"] = text
    candidate.setdefault("artifacts", []).append(artifact)
    candidate["state"] = STATE_COLLECTED
    store.log_event(candidate, "artifact_imported", f"{artifact_type} sha256={sha}")
    return candidate


class BrowserCollector:
    """Playwright tabanlı, KULLANICI-KONTROLLÜ oturuma bağlanan minimal toplayıcı.

    Kimlik doğrulaması OTOMATİKLEŞTİRİLMEZ. İki mod: (1) ``cdp_endpoint`` — kullanıcının
    ``--remote-debugging-port`` ile başlattığı ve kendisinin oturum açtığı tarayıcıya BAĞLANIR;
    (2) ``user_data_dir`` — kullanıcının elle oturum açtığı yerel kalıcı profil (data/ altında;
    COMMİT EDİLMEZ). Hiçbir parola/MFA/CAPTCHA işlenmez; hiçbir çerez/token repoya yazılmaz.
    """

    def __init__(self, cdp_endpoint: str | None = None, user_data_dir: str | Path | None = None, headless: bool = False):
        self.cdp_endpoint = cdp_endpoint
        self.user_data_dir = user_data_dir
        self.headless = headless

    @staticmethod
    def _sync_playwright():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - ortam-bağımlı
            raise RuntimeError(BROWSER_PREREQUISITES) from exc
        return sync_playwright

    def collect_page_html(self, url: str) -> str:  # pragma: no cover - canlı tarayıcı gerektirir
        """Kullanıcı-kontrollü oturumda verilen UYAP sonuç/liste sayfasının HTML'ini okur.

        Yalnızca zaten kimlik-doğrulanmış ya da kamuya açık bir sayfayı OKUR; oturum AÇMAZ.
        Test paketi bu yolu çağırmaz (ağ gerektirmez).
        """
        sync_playwright = self._sync_playwright()
        with sync_playwright() as pw:
            if self.cdp_endpoint:
                browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
            elif self.user_data_dir:
                context = pw.chromium.launch_persistent_context(str(self.user_data_dir), headless=self.headless)
                page = context.new_page()
            else:
                raise RuntimeError(
                    "No user-controlled session provided. Supply cdp_endpoint (attach to a browser "
                    "you launched and authenticated) or user_data_dir (a local profile you signed into). "
                    + BROWSER_PREREQUISITES
                )
            page.goto(url, wait_until="domcontentloaded")
            html = page.content()
            page.close()
            return html

    def collect_record(self, url: str | None = None, follow_documents: bool = True,
                       target_file_id: str | None = None, target_institution: str | None = None) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        """Kullanıcının açtığı GERÇEK UYAP kaydını (arama/listeleme YA DA detay sayfası) toplar.

        SAYFA-DURUMU FARKINDA: ``search_listing`` ise HEDEF kayıt kartı (``target_file_id`` /
        listeleme alias'ı) bulunur, kart-yerel "İhale Evrak Listesi" → modal; ``record_detail``
        ise detay-sekmesi → aynı-sayfa panel. İki yol aynı belge-satırı soyutlamasında birleşir.
        Test paketi bu yolu ÇAĞIRMAZ (ağ gerektirmez).
        """
        sync_playwright = self._sync_playwright()
        with sync_playwright() as pw:
            page_selection: dict | None = None
            if self.cdp_endpoint:
                browser = pw.chromium.connect_over_cdp(self.cdp_endpoint)
                if not browser.contexts:
                    raise RuntimeError("no_usable_browser_context: CDP oturumunda kullanılabilir bağlam yok.")
                context = browser.contexts[0]
                if url:
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded")
                else:
                    if not context.pages:
                        raise RuntimeError(
                            "no_matching_uyap_page: açık sekme yok. Önce 2026/263 UYAP sayfasını açın."
                        )
                    # Fix 4: birden çok sekme varsa bayat UDF görüntüleyici DEĞİL, desteklenen HEDEF
                    # sayfa (search_listing/record_detail + kimlik) deterministik seçilir.
                    page, page_selection = self._select_target_page(context, target_file_id)
            elif self.user_data_dir:
                context = pw.chromium.launch_persistent_context(str(self.user_data_dir), headless=self.headless)
                page = context.new_page()
                if url:
                    page.goto(url, wait_until="domcontentloaded")
            else:
                raise RuntimeError("no_user_controlled_session: cdp_endpoint ya da user_data_dir gerekir. " + BROWSER_PREREQUISITES)

            html = page.content()
            title = page.title()
            page_url = page.url
            pse = page_state_evidence(html, page_url)
            sel = page_selection or {}
            links = discover_document_links(html)
            documents: list[dict] = []
            access_patterns: list[dict] = []
            collection_diagnostics: dict = {
                "page_state": pse["page_state"],
                "page_state_evidence": pse["evidence"],
                "page_candidates_seen": sel.get("page_candidates_seen", []),
                "selected_page_url_kind": sel.get("selected_page_url_kind", _safe_url_kind(page_url)),
                "selected_page_state": sel.get("selected_page_state", pse["page_state"]),
                "selected_page_target_identity_match": sel.get(
                    "selected_page_target_identity_match",
                    bool(target_file_id and file_identity_matches(html, target_file_id)),
                ),
                "document_entry_path": "unsupported",
                "target_record_card_found": False,
                "target_record_card_match_fields": [],
                "target_record_card_file_text": None,
                "target_record_card_control_labels": [],
                "document_list_control_found": False,
                "document_list_control_kind": None,
                "document_list_opened": False,
                "document_list_container_kind": "not_opened",
                "document_modal_opened": False,
                "document_labels_observed": [],
                "document_actions_observed": 0,
                "pre_click_visible_document_types": [],
                "post_click_visible_document_types": [],
                "document_list_semantic_transition_detected": False,
                "document_container_strategy": "not_found",
                "document_container_recognized_types": [],
                "document_row_detection_strategy": None,
                "document_entry_state": None,
                "row_boundary_strategy": None,
                "recognized_document_rows": [],
                "action_resolution_strategy": None,
                "document_collection_attempts": [],
                "document_collection_failures": 0,
                "viewer_pages_opened": 0,
                "viewer_image_cross_document_duplicate": False,
                "viewer_image_duplicate_artifact_types": [],
            }
            if follow_documents:
                for lk in links:
                    if not lk.get("usable_href"):
                        access_patterns.append({"label": lk["text"], "pattern": "unsupported_javascript_or_handler"})
                        continue
                    href = lk["href"]
                    try:
                        dp = context.new_page()
                        dp.goto(href, wait_until="domcontentloaded")
                        content = dp.content()
                        pattern = "pdf_or_download" if str(href).lower().endswith(".pdf") else "normal_link_new_page"
                        documents.append({"artifact_type": lk["artifact_type"], "text": content, "source_ref": href})
                        access_patterns.append({"label": lk["text"], "pattern": pattern})
                        dp.close()
                    except Exception as exc:
                        access_patterns.append({"label": lk["text"], "pattern": "navigation_failed", "detail": str(exc)[:120]})
                # SAYFA-DURUMU FARKINDA belge girişi (listeleme-kartı-modal VEYA detay-tab-panel).
                m_docs, m_patterns, m_diag = self._collect_documents(page, context, target_file_id, target_institution)
                documents += m_docs
                access_patterns += m_patterns
                # Fix 4: sayfa-seçim/kanıt tanılarını KORU (belge-giriş tanılarıyla birleştir).
                collection_diagnostics.update(m_diag)
            return {
                "html": html,
                "title": title,
                "url": page_url,
                "document_links": links,
                "documents": documents,
                "document_access_patterns": access_patterns,
                "collection_diagnostics": collection_diagnostics,
            }

    def _select_target_page(self, context, target_file_id=None):  # pragma: no cover - canlı çoklu-sekme
        """Fix 4: açık sekmeler arasından desteklenen HEDEF sayfayı güvenle seçer.

        Bayat bir UDF görüntüleyici / ilgisiz UYAP sekmesi YALNIZCA UYAP olduğu için seçilmez;
        ``select_target_page_index`` desteklenen belge-giriş durumunu + hedef kimliğini önceler.
        Seçim başarısızsa operatör-aktif ilk sekmeye düşer (dürüst tanı ile).
        """
        candidates: list[dict] = []
        for p in context.pages:
            try:
                u = p.url or ""
            except Exception:
                u = ""
            try:
                h = p.content()
            except Exception:
                h = ""
            candidates.append({"url": u, "html": h})
        sel = select_target_page_index(candidates, target_file_id)
        idx = sel.get("index", -1)
        page = context.pages[idx] if 0 <= idx < len(context.pages) else context.pages[0]
        return page, sel

    def _collect_documents(self, page, context, target_file_id=None, target_institution=None) -> tuple:  # pragma: no cover - canlı DOM/olay gerektirir
        """SAYFA-DURUMU FARKINDA, HEDEF-KAYIT-KAPSAMLI belge girişi (iki gerçek gözlenen yol).

        ``search_listing`` → HEDEF kayıt kartı dosya kimliğiyle bulunur (fiyat/nth DEĞİL) →
        kart-yerel "İhale Evrak Listesi" → MODAL. ``record_detail`` → detay "İhale Evrak Listesi"
        sekmesi → AYNI-SAYFA panel. İki yol aynı belge-satırı soyutlamasında birleşir
        (``extract_panel_document_rows`` + ``select_row_document_actions``) → satır-yerel eye →
        YENİ-SEKME UDF görüntüleyici. Global metin locator / global Nth-eye KULLANILMAZ.
        """
        import re as _re

        html = page.content()
        page_url = page.url or ""
        page_state = classify_page_state(html, page_url)
        diag = {
            "page_state": page_state,
            "document_entry_path": classify_document_entry_path(page_state),
            "target_record_card_found": False,
            "target_record_card_match_fields": [],
            "target_record_card_file_text": None,
            "target_record_card_control_labels": [],
            "document_list_control_found": False,
            "document_list_control_kind": None,
            "document_list_opened": False,
            "document_list_container_kind": "not_opened",
            "document_modal_opened": False,
            "document_labels_observed": [],
            "document_actions_observed": 0,
            "pre_click_visible_document_types": [],
            "post_click_visible_document_types": [],
            "document_list_semantic_transition_detected": False,
            "document_container_strategy": "not_found",
            "document_container_recognized_types": [],
            "document_row_detection_strategy": None,
            "document_entry_state": None,
            "row_boundary_strategy": None,
            "recognized_document_rows": [],
            "action_resolution_strategy": None,
            "document_collection_attempts": [],
            "document_collection_failures": 0,
            "viewer_pages_opened": 0,
            "viewer_image_cross_document_duplicate": False,
            "viewer_image_duplicate_artifact_types": [],
        }
        documents: list[dict] = []
        patterns: list[dict] = []

        control = None
        if page_state == "search_listing":
            if not target_file_id:
                diag["document_collection_attempts"].append({"stage": "target_card", "blocking_reason": "no_target_file_id_provided"})
                diag["document_collection_failures"] += 1
                return documents, patterns, diag
            card = find_target_record_card(html, target_file_id, target_institution)
            if card is None:
                diag["document_collection_attempts"].append({"stage": "target_card", "blocking_reason": "target_record_card_not_found_on_listing"})
                diag["document_collection_failures"] += 1
                return documents, patterns, diag
            diag["target_record_card_found"] = True
            diag["target_record_card_match_fields"] = card["match_fields"]
            diag["target_record_card_file_text"] = card["file_text"]
            diag["target_record_card_control_labels"] = card["control_labels"]
            ctrl_info = card_document_list_control(card["html"])
            diag["document_list_control_found"] = ctrl_info.get("found", False)
            diag["document_list_control_kind"] = (f"card_{ctrl_info['kind']}" if ctrl_info.get("actionable") else ctrl_info.get("kind"))
            if not ctrl_info.get("actionable"):
                diag["document_collection_attempts"].append({"stage": "card_control", "blocking_reason": f"card_local_control_not_actionable:{ctrl_info.get('kind')}"})
                diag["document_collection_failures"] += 1
                return documents, patterns, diag
            # Kart-yerel kontrolü CANLI DOM'da HEDEF kart kapsamında bul (global text locator DEĞİL —
            # üçüncü canlı hatanın kök nedeni buydu).
            control = self._locate_card_control(page, target_file_id)
        elif page_state == "record_detail":
            if target_file_id and not file_identity_matches(html, target_file_id):
                diag["document_collection_attempts"].append({"stage": "detail_identity", "blocking_reason": "detail_page_does_not_match_target_file_id"})
                diag["document_collection_failures"] += 1
                return documents, patterns, diag
            for kind, loc in (
                ("tab", page.get_by_role("tab", name=_re.compile("evrak listesi", _re.I))),
                ("link", page.get_by_role("link", name=_re.compile("evrak listesi", _re.I))),
                ("button", page.get_by_role("button", name=_re.compile("evrak listesi", _re.I))),
            ):
                try:
                    if loc.count() > 0:
                        control = loc.first
                        diag["document_list_control_found"] = True
                        diag["document_list_control_kind"] = f"detail_{kind}"
                        break
                except Exception:
                    continue
        else:
            diag["document_collection_attempts"].append({"stage": "page_state", "blocking_reason": f"unsupported_page_state:{page_state}"})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        if control is None:
            diag["document_collection_attempts"].append({"stage": "control_locate", "blocking_reason": "document_list_control_not_located_in_live_dom"})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        # Fix 5: tıklama ÖNCESİ görünür belge-türü imzası (semantic transition kanıtı için).
        try:
            pre_html = page.content()
        except Exception:
            pre_html = ""
        diag["pre_click_visible_document_types"] = visible_document_types(pre_html)

        # Fix 7: kart-kontrolüne TIKLAMADAN önce — GEÇERLİ görünür belge-listesi zaten açık mı? (pre-opened/
        # stale UI). Aynı-bağlam (hedef kimlik + desteklenen sayfa + sınırlı konteyner) geçerli liste açıksa
        # tekrar tıklanmaz; doğrudan satır toplamaya geçilir (gizli şablon / ham-HTML pre-opened SAYILMAZ).
        if preopened_document_list_reusable(pre_html, page_url, target_file_id):
            preopen_rows = extract_document_rows_semantic(pre_html) or extract_panel_document_rows(pre_html)
            if preopen_rows:
                det0 = detect_document_list(pre_html)
                diag["document_entry_state"] = "preopened_document_list_reused"
                diag["post_click_visible_document_types"] = det0.get("recognized_types", [])
                diag["document_list_semantic_transition_detected"] = False  # zaten açıktı — geçiş GEREKMEZ
                diag["document_container_strategy"] = det0.get("container_strategy", "not_found")
                diag["document_container_recognized_types"] = det0.get("recognized_types", [])
                diag["document_row_detection_strategy"] = "semantic_label_ancestor"
                container_kind = document_container_kind_for_entry(page_state)
                diag["document_list_container_kind"] = container_kind
                diag["document_list_opened"] = True
                diag["document_modal_opened"] = (container_kind == "listing_modal")
                d0, p0 = self._collect_from_container(page, context, preopen_rows, container_kind, diag)
                documents += d0
                patterns += p0
                return documents, patterns, diag
        diag["document_entry_state"] = "entry_control_click"

        try:
            control.click(timeout=5000)
        except Exception as exc:
            diag["document_collection_attempts"].append({"stage": "control_click", "blocking_reason": str(exc)[:120]})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        # Fix 5: AÇILAN belge listesini GÖRÜNÜR SEMANTİK içerikten algıla (gerçek UYAP overlay'i
        # div/portal/custom olabilir; .modal / role=dialog / <tr> GEREKMEZ). Ortak-ata konteyner +
        # etiket-anchoring ile satırlar; başlık TEK BAŞINA yetmez (≥2 distinkt belge türü gerekir).
        rows: list[dict] = []
        det = {"detected": False, "recognized_types": [], "container_strategy": "not_found"}
        for _ in range(12):  # ~6s
            try:
                cur_html = page.content()
            except Exception:
                cur_html = ""
            det = detect_document_list(cur_html)
            if det["detected"]:
                rows = extract_document_rows_semantic(cur_html) or extract_panel_document_rows(cur_html)
                if rows:
                    break
            page.wait_for_timeout(500)

        pre_types = diag.get("pre_click_visible_document_types", [])
        post_types = det.get("recognized_types", [])
        diag["post_click_visible_document_types"] = post_types
        diag["document_list_semantic_transition_detected"] = (
            len(set(post_types)) >= 2 and len(set(post_types) - set(pre_types)) >= 1
        )
        diag["document_container_strategy"] = det.get("container_strategy", "not_found")
        diag["document_container_recognized_types"] = post_types
        diag["document_row_detection_strategy"] = "semantic_label_ancestor"

        if not rows:
            diag["document_collection_attempts"].append({"stage": "document_list_wait", "blocking_reason": "document list did not become visible after control click"})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        # Fix 5: giriş yolu + gözlenen geçiş → konteyner türü (CSS class'ından türetilmez).
        container_kind = document_container_kind_for_entry(page_state)
        diag["document_list_container_kind"] = container_kind
        diag["document_list_opened"] = True
        diag["document_modal_opened"] = (container_kind == "listing_modal")

        d2, p2 = self._collect_from_container(page, context, rows, container_kind, diag)
        documents += d2
        patterns += p2
        return documents, patterns, diag

    def _locate_card_control(self, page, target_file_id):  # pragma: no cover - canlı DOM
        """HEDEF kaydın CANLI kartını dosya kimliğiyle bulur; kart-yerel "İhale Evrak Listesi"
        kontrolünü döner (global metin locator DEĞİL — kart kapsamı zorunlu)."""
        import re as _re

        tnum = normalize_file_identity(target_file_id)
        key = _re.escape(tnum).replace("/", r"\s*/\s*")
        for csel in ("[class*=card]", "[class*=sonuc]", "[class*=result]", "[class*=ilan]", "li", "tr"):
            try:
                cards = page.locator(csel).filter(has_text=_re.compile(key, _re.I))
                if cards.count() == 0:
                    continue
                card = cards.first
                for name in ("button", "link"):
                    try:
                        act = card.get_by_role(name, name=_re.compile("evrak listesi", _re.I))
                        if act.count() > 0:
                            return act.first
                    except Exception:
                        continue
                try:
                    act = card.get_by_text(_re.compile("evrak listesi", _re.I))
                    if act.count() > 0:
                        return act.first
                except Exception:
                    pass
            except Exception:
                continue
        return None

    def _collect_from_container(self, page, context, rows, container_kind, diag) -> tuple:  # pragma: no cover - canlı DOM
        """İki yolun BİRLEŞTİĞİ ortak belge-satırı toplayıcı: verilen (semantik) satırlar → satır-yerel
        eye (indirme oku DEĞİL; belirsizse KEYFİ tıklama YOK) → YENİ-SEKME UDF görüntüleyici.

        Satırlar önceliğe göre denenir (auction_result > appraisal_report > sale_notice > sale_spec).
        ``diag`` yerinde güncellenir (recognized_document_rows dahil)."""
        documents: list[dict] = []
        patterns: list[dict] = []
        rows = rows or []
        diag["document_labels_observed"] = [_ascii_lower(_demojibake(r.get("label") or ""))[:60] for r in rows]
        diag["document_actions_observed"] = sum(len(r.get("actions") or []) for r in rows)

        selected: list[dict] = []
        recognized: list[dict] = []
        pending_image_captures: list = []  # Fix 9: (attempt, capture) — döngü SONRASI kimlik/promosyon
        for i, row in enumerate(rows):
            atype = classify_document_label(row.get("label", ""))
            if not atype:
                continue
            row_actions = row.get("actions") or []
            res = resolve_row_view_action(row_actions)
            rb = row.get("row_boundary") or {}
            recognized.append({
                "artifact_type": atype,
                "normalized_label": _ascii_lower(_demojibake(row.get("label") or ""))[:60],
                "action_count": len(row_actions),
                "view_action_resolved": bool(res["resolved"]),
                "download_action_detected": bool(res["download_action_detected"]),
                "resolved_semantic": ("view" if res["resolved"] else None),
                "action_resolution_reason": res.get("reason"),
                "row_boundary_strategy": rb.get("row_boundary_strategy"),
                "logical_row_ancestor_kind": rb.get("logical_row_ancestor_kind"),
                "logical_row_recognized_type_count": rb.get("logical_row_recognized_type_count"),
                "logical_row_actionable_control_count": rb.get("logical_row_actionable_control_count"),
                "actionable_control_tags": rb.get("actionable_control_tags"),
                "action_summaries": [_action_summary(a, k) for k, a in enumerate(row_actions)],
            })
            selected.append({"row_index": i, "label": row.get("label"), "artifact_type": atype, "resolution": res})
        diag["recognized_document_rows"] = recognized
        diag["action_resolution_strategy"] = "icon_accessibility_href_precedence"
        diag["row_boundary_strategy"] = next((r.get("row_boundary_strategy") for r in recognized if r.get("row_boundary_strategy")), None)
        selected.sort(key=lambda s: _DOC_PRIORITY.get(s["artifact_type"], 9))  # öncelik: auction_result önce

        for sel in selected:
            label = sel["label"]
            resolution = sel["resolution"]
            attempt = {
                "artifact_type": sel["artifact_type"],
                "normalized_document_label": _ascii_lower(_demojibake(label or ""))[:60],
                "new_page_detected": False,
                "viewer_url_kind": None,
                "viewer_mime_type_hint": None,
                "viewer_text_available": False,
                "viewer_iframe_count": 0,
                "viewer_canvas_count": 0,
                "viewer_image_count": 0,
                "viewer_embed_count": 0,
                "viewer_object_count": 0,
                "access_pattern": None,
                "artifact_collected": False,
                "blocking_reason": None,
                "viewer_outcome": None,
                "initial_viewer_representation": None,
                "final_viewer_representation": None,
                "initial_viewer_text_available": False,
                "final_viewer_text_available": False,
                "initial_viewer_outcome": None,
                "final_viewer_outcome": None,
                "viewer_download_instruction_detected": False,
                "download_fallback_attempted": False,
                "download_fallback_resolved_same_row": False,
                "download_action_resolved": bool(resolution.get("download_action_resolved")),
                "download_event_detected": False,
                "downloaded_artifact_extension": None,
                "downloaded_artifact_mime_hint": None,
                "downloaded_artifact_size": None,
                "downloaded_artifact_collected": False,
                "downloaded_artifact_extraction_supported": None,
                "download_fallback_blocking_reason": None,
                "viewer_representation": None,
                "viewer_image_candidate_count": 0,
                "viewer_document_image_candidate_count": 0,
                "viewer_image_candidates": [],
                "selected_viewer_image_candidate_index": None,
                "viewer_image_source_kind": None,
                "viewer_image_source_capture_supported": None,
                "viewer_image_source_capture_strategy": None,
                "viewer_image_source_bytes_captured": False,
                "viewer_image_artifact_collected": False,
                "viewer_image_artifact_extension": None,
                "viewer_image_artifact_mime_hint": None,
                "viewer_image_artifact_size": None,
                "viewer_image_artifact_sha256": None,
                "viewer_image_source_sha256": None,
                "viewer_image_text_extraction_supported": None,
                "viewer_image_capture_blocking_reason": None,
                "viewer_asset_captured": False,
                "document_source_artifact_collected": False,
                "source_text_persisted": False,
                "source_text_artifact_sha256": None,
                "source_text_artifact_size": None,
                "native_download_attempted": False,
                "native_download_action_resolved": None,
                "native_download_event_detected": False,
                "native_requested_artifact_type": None,
                "native_requested_normalized_label": None,
                "native_row_reacquired": None,
                "native_row_reacquired_artifact_type": None,
                "native_row_reacquired_label_match": None,
                "native_action_owner_same_row": None,
                "native_action_owner_semantic_revalidated": None,
                "native_action_owner_fingerprint_match": None,
                "native_artifact_collected": False,
                "native_artifact_extension": None,
                "native_artifact_size": None,
                "native_artifact_sha256": None,
                "native_container_kind": None,
                "native_udf_zip_valid": None,
                "native_udf_member_names_safe_summary": None,
                "native_udf_content_xml_found": None,
                "native_udf_content_xml_size": None,
                "native_udf_xml_parse_succeeded": None,
                "native_udf_content_element_found": None,
                "native_udf_source_text_available": None,
                "native_udf_text_extraction_supported": None,
                "native_detected_document_type": None,
                "native_document_type_corroborated": None,
                "native_document_type_mismatch": None,
                "native_document_type_corroboration_reason": None,
                "native_udf_source_relation": None,
                "native_udf_blocking_reason": None,
                "viewer_image_document_identity": None,
                "viewer_image_cross_document_duplicate": False,
                "viewer_image_duplicate_artifact_types": [],
                "viewer_asset_only": False,
                "viewer_asset_identity_blocking_reason": None,
                "viewer_ready_state": None,
                "viewer_stabilization_observation_count": 0,
                "viewer_stabilization_transition_detected": False,
                "viewer_representation_sequence": [],
                "viewer_image_candidate_count_sequence": [],
                "viewer_selected_image_dimension_sequence": [],
                "viewer_selected_image_src_kind_sequence": [],
                "viewer_image_fingerprint_changed": False,
                "viewer_stabilization_blocking_reason": None,
            }
            # Fix 12: auction_result için ÖNCE satır-yerel resmî .udf indirme (NATIVE konteyner çıkarımı).
            # Tetikleyici: POZİTİF çözülmüş AYNI-SATIR download eylemi + evidence DocumentRow + gerçek indirme
            # olayı (viewer'ın image-backed/hata olması GEREKMEZ). Global/Nth download YOK. Başarılıysa
            # viewer'a gerek kalmaz; başarısızsa mevcut viewer yoluna DÜŞER (KORUNUR).
            if sel["artifact_type"] in NATIVE_DOWNLOAD_TYPES and resolution.get("download_action_resolved"):
                if self._collect_native_udf_download(page, label, sel, resolution, attempt, diag, documents):
                    diag["document_collection_attempts"].append(attempt)
                    continue
            if not resolution.get("resolved") or resolution.get("view_action") is None:
                attempt["blocking_reason"] = f"row_action_unresolved:{resolution.get('reason')}"
                diag["document_collection_failures"] += 1
                diag["document_collection_attempts"].append(attempt)
                continue
            # Fix 6: POZİTİF çözülmüş view eylemini canlı DOM'da bul (satır-yerel; global Nth DEĞİL).
            eye = self._locate_row_view_action(page, label, resolution.get("view_action")) or self._locate_row_eye(page, label)
            if eye is None:
                attempt["blocking_reason"] = "row_local_eye_control_not_located"
                diag["document_collection_failures"] += 1
                diag["document_collection_attempts"].append(attempt)
                continue
            before = set(context.pages)
            newp = None
            try:
                try:
                    with context.expect_page(timeout=5000) as pinfo:
                        eye.click(timeout=4000)
                    newp = pinfo.value
                except Exception:
                    for _ in range(10):
                        page.wait_for_timeout(300)
                        delta = [p for p in context.pages if p not in before]
                        if delta:
                            newp = delta[0]
                            break
                if newp is not None:
                    diag["viewer_pages_opened"] += 1
                    attempt["new_page_detected"] = True
                    try:
                        newp.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        pass
                    vurl = newp.url or ""
                    attempt["viewer_url_kind"] = classify_viewer_url(vurl)
                    attempt["viewer_mime_type_hint"] = viewer_mime_hint(vurl)
                    counts = self._viewer_counts(newp)
                    attempt.update({
                        "viewer_iframe_count": counts.get("iframe", 0),
                        "viewer_canvas_count": counts.get("canvas", 0),
                        "viewer_image_count": counts.get("image", 0),
                        "viewer_embed_count": counts.get("embed", 0),
                        "viewer_object_count": counts.get("object", 0),
                        "viewer_text_available": bool(counts.get("text_available")),
                    })
                    representation = classify_viewer_representation(counts)
                    attempt["viewer_representation"] = representation
                    # Fix 10: ilk (kararlılık-öncesi) anlık gözlem — SONRAKİ kararlı durumla KARIŞTIRILMAZ.
                    attempt["initial_viewer_representation"] = representation
                    attempt["initial_viewer_text_available"] = bool(counts.get("text_available"))
                    attempt["final_viewer_representation"] = representation
                    attempt["final_viewer_text_available"] = bool(counts.get("text_available"))
                    attempt["access_pattern"] = classify_view_access_pattern(
                        container_kind,
                        {"new_page": True, "is_udf": attempt["viewer_url_kind"] == "udf_viewer",
                         "is_pdf": attempt["viewer_url_kind"] == "pdf_viewer"},
                    )
                    # Fix 6.1: görüntüleyici SONUCUNU sınıfla (içerik var mı / indirme-gerekli / hata).
                    vtext = self._viewer_body_text(newp)
                    outcome = classify_viewer_outcome(vtext, representation)
                    attempt["viewer_outcome"] = outcome
                    attempt["initial_viewer_outcome"] = outcome
                    attempt["final_viewer_outcome"] = outcome
                    attempt["viewer_download_instruction_detected"] = viewer_download_instruction_detected(vtext)
                    if outcome == "download_required":
                        attempt["final_viewer_outcome"] = "download_required"
                        try:
                            newp.close()  # başarısız görüntüleyici sekmesi kapatılır (orijinal sayfa KORUNUR)
                        except Exception:
                            pass
                        self._same_row_download_fallback(page, label, sel, resolution, attempt, diag, documents)
                    elif outcome == "content_available":
                        content = self._viewer_source_text(newp, representation)
                        if content:
                            documents.append({"artifact_type": sel["artifact_type"], "text": content,
                                              "source_ref": f"viewer:{attempt['viewer_url_kind']}"})
                            attempt["artifact_collected"] = True
                            attempt["document_source_artifact_collected"] = True  # dom_text = belge-özgü kaynak
                            attempt["final_viewer_text_available"] = True
                            self._persist_viewer_source(sel["artifact_type"], content, attempt)  # Fix 11: gitignored yerel kalıcılık
                        else:
                            attempt["blocking_reason"] = f"viewer_representation_unsupported:{representation}"
                            diag["document_collection_failures"] += 1
                        try:
                            newp.close()  # operatörün orijinal sekmesi KAPATILMAZ
                        except Exception:
                            pass
                    elif outcome == "image_backed":
                        # Fix 8: görüntü-destekli görüntüleyici. Fix 9: KESİN yakalamadan ÖNCE SINIRLI
                        # kararlılık gözlemi (ilk uygun görüntüyü anında yakalama); indirme-gerekli/hata
                        # kararlılığı KESER (Fix 6.1 önceliği korunur). Promosyon döngü SONRASI kimliğe bağlı.
                        ready = self._observe_viewer_stabilization(newp)
                        attempt["viewer_ready_state"] = ready.get("ready_state")
                        attempt["viewer_stabilization_observation_count"] = ready.get("observation_count", 0)
                        attempt["viewer_stabilization_transition_detected"] = bool(ready.get("transition_detected"))
                        attempt["viewer_representation_sequence"] = ready.get("representation_sequence") or []
                        attempt["viewer_image_candidate_count_sequence"] = ready.get("candidate_count_sequence") or []
                        attempt["viewer_selected_image_dimension_sequence"] = ready.get("selected_dimension_sequence") or []
                        attempt["viewer_selected_image_src_kind_sequence"] = ready.get("selected_src_kind_sequence") or []
                        attempt["viewer_image_fingerprint_changed"] = bool(ready.get("fingerprint_changed"))
                        attempt["viewer_stabilization_blocking_reason"] = ready.get("blocking_reason")
                        rs = ready.get("ready_state")
                        if rs == "download_required":
                            # Fix 6.1 önceliği: kararlılık sırasında indirme-gerekli belirdi → aynı-satır fallback.
                            attempt["viewer_download_instruction_detected"] = True
                            attempt["final_viewer_outcome"] = "download_required"
                            try:
                                newp.close()
                            except Exception:
                                pass
                            self._same_row_download_fallback(page, label, sel, resolution, attempt, diag, documents)
                        elif rs == "stable_text_representation":
                            # Fix 10: görüntüleyici image_only → dom_text KARARLILAŞTI. Sıradan alanlar SONUÇ
                            # (kararlı) durumu yansıtır; ilk anlık gözlem initial_* olarak KORUNUR.
                            attempt["viewer_representation"] = "dom_text"
                            attempt["viewer_text_available"] = True
                            attempt["viewer_outcome"] = "content_available"
                            attempt["final_viewer_representation"] = "dom_text"
                            attempt["final_viewer_text_available"] = True
                            attempt["final_viewer_outcome"] = "content_available"
                            content = self._viewer_source_text(newp, "dom_text")
                            if content:
                                documents.append({"artifact_type": sel["artifact_type"], "text": content,
                                                  "source_ref": f"viewer:{attempt['viewer_url_kind']}"})
                                attempt["artifact_collected"] = True
                                attempt["document_source_artifact_collected"] = True
                                self._persist_viewer_source(sel["artifact_type"], content, attempt)  # Fix 11: gitignored yerel kalıcılık
                            else:
                                attempt["blocking_reason"] = "viewer_stable_text_but_no_source_text"
                                diag["document_collection_failures"] += 1
                            try:
                                newp.close()
                            except Exception:
                                pass
                        elif rs == "stable_image_representation":
                            # KESİN kaynak baytlarını yakala + görüntüleyici-asset olarak sakla (Fix 8).
                            # Promosyon YAPILMAZ: cross-document kimlik döngü sonrası çözülür (Fix 9).
                            attempt["final_viewer_representation"] = attempt.get("initial_viewer_representation")
                            attempt["final_viewer_outcome"] = "image_backed"
                            capture = self._collect_viewer_image(newp, attempt, sel, diag)
                            if capture is not None:
                                pending_image_captures.append((attempt, capture))
                            try:
                                newp.close()  # operatörün orijinal sekmesi KAPATILMAZ
                            except Exception:
                                pass
                        elif rs == "viewer_error":
                            attempt["blocking_reason"] = "viewer_error"
                            attempt["final_viewer_outcome"] = "viewer_error"
                            diag["document_collection_failures"] += 1
                            try:
                                newp.close()
                            except Exception:
                                pass
                        else:  # timeout_unstable — ilk görüntüyü belge kanıtı sayma (dürüst)
                            attempt["blocking_reason"] = ready.get("blocking_reason") or "viewer_stabilization_timeout_unstable"
                            attempt["final_viewer_outcome"] = "timeout_unstable"
                            diag["document_collection_failures"] += 1
                            try:
                                newp.close()
                            except Exception:
                                pass
                    else:
                        attempt["blocking_reason"] = ("viewer_error" if outcome == "viewer_error"
                                                      else f"viewer_representation_unsupported:{representation}")
                        diag["document_collection_failures"] += 1
                        try:
                            newp.close()
                        except Exception:
                            pass
                else:
                    attempt["access_pattern"] = classify_view_access_pattern(container_kind, {"same_page_nav": True})
                    attempt["blocking_reason"] = "no_new_viewer_page_detected"
                    diag["document_collection_failures"] += 1
                patterns.append({"label": attempt["normalized_document_label"], "pattern": attempt["access_pattern"]})
            except Exception as exc:
                attempt["blocking_reason"] = str(exc)[:120]
                diag["document_collection_failures"] += 1
            diag["document_collection_attempts"].append(attempt)
        # Fix 9: cross-document tam-SHA duplicate guard + belge-render kimliği + KOŞULLU promosyon.
        # Byte-identical bir görüntü ≥2 AYRI resmî DocumentRow'da paylaşılıyorsa (Run-9: auction_result ==
        # sale_notice) → shared_cross_document_asset → İKİSİ DE belge kaynağı olarak PROMOTE EDİLMEZ.
        # Yalnız document_specific kimlik promote edilir; diğerleri görüntüleyici-asset (tanı) olarak kalır.
        if pending_image_captures:
            captures = [c for (_a, c) in pending_image_captures]
            dup = detect_cross_document_image_duplicates(captures)
            diag["viewer_image_cross_document_duplicate"] = dup["duplicate"]
            diag["viewer_image_duplicate_artifact_types"] = dup["duplicate_artifact_types"]
            resolutions = resolve_viewer_image_identities(captures)
            for (att, cap), rez in zip(pending_image_captures, resolutions):
                att["viewer_image_document_identity"] = rez["viewer_image_document_identity"]
                att["viewer_image_cross_document_duplicate"] = bool(rez["cross_document_duplicate"])
                att["viewer_image_duplicate_artifact_types"] = rez["duplicate_artifact_types"]
                if rez["promote_as_document_source"]:
                    documents.append({"artifact_type": cap["artifact_type"],
                                      "source_ref": f"viewer_image:{cap['extension'] or '.bin'}",
                                      "extraction_supported": False})
                    att["document_source_artifact_collected"] = True
                    att["artifact_collected"] = True
                else:
                    att["document_source_artifact_collected"] = False
                    att["artifact_collected"] = False
                    att["viewer_asset_only"] = True
                    att["viewer_asset_identity_blocking_reason"] = (
                        f"viewer_image_not_document_specific:{rez['viewer_image_document_identity']}")
        return documents, patterns

    def _locate_row_view_action(self, page, label, view_spec):  # pragma: no cover - canlı DOM
        """Fix 6: POZİTİF çözülmüş view eylemini, çözümü üreten semantikle CANLI DOM'da bulur.

        Ayırt edici seçiciler yalnız çözülen view metadata'sından türetilir (erişilebilir ad /
        view ikon token'ı / viewer href). Bulunamazsa None döner (keyfi/sağdaki eylem TIKLANMAZ).
        """
        import re as _re

        if not view_spec:
            return None
        key = _re.escape(_demojibake(label or "").split("/")[0].strip()[:24])
        if not key:
            return None
        selectors: list[str] = []
        nm = view_spec.get("accessible_name")
        if nm:
            safe = _re.sub(r'["\\]', "", str(nm))[:20]
            if safe:
                selectors.append(f'[title*="{safe}" i]')
                selectors.append(f'[aria-label*="{safe}" i]')
        for tok in (view_spec.get("icon_tokens") or [])[:8]:
            if _text_has_view(tok):
                safe = _re.sub(r'["\\]', "", str(tok))[:24]
                if safe:
                    selectors.append(f'[class*="{safe}" i]')
                    selectors.append(f'a:has([class*="{safe}" i])')
                    selectors.append(f'button:has([class*="{safe}" i])')
        if view_spec.get("href_kind") == "viewer":
            selectors.append('a[href*="viewer.jsp" i]')
            selectors.append('a[href*="goruntule" i]')
        if not selectors:
            return None
        for rsel in ("[class*=doc]", "tr", "li", "[class*=evrak]", "[class*=row]", "div", "section"):
            try:
                row = page.locator(rsel).filter(has_text=_re.compile(key, _re.I))
                if row.count() == 0:
                    continue
                r = row.last if rsel in ("div", "section") else row.first
                for s in selectors:
                    try:
                        act = r.locator(s)
                        if act.count() > 0:
                            return act.first  # satır-yerel, POZİTİF view eylemi
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _locate_row_eye(self, page, label):  # pragma: no cover - canlı DOM
        import re as _re

        key = _re.escape(_demojibake(label or "").split("/")[0].strip()[:24])
        if not key:
            return None
        # Fix 5: sabit tr/li VARSAYMAZ — div/section overlay satırı da olabilir (en içteki eşleşme).
        for rsel in ("tr", "li", "[class*=evrak]", "[class*=row]", "[class*=doc]", "div", "section"):
            try:
                row = page.locator(rsel).filter(has_text=_re.compile(key, _re.I))
                if row.count() == 0:
                    continue
                r = row.last if rsel in ("div", "section") else row.first
                for asel in ("[title*=Görüntüle i]", "[title*=Göster i]", "[aria-label*=Görüntüle i]",
                             "button", "a", "[role=button]", "i", "img"):
                    try:
                        act = r.locator(asel)
                        if act.count() > 0:
                            return act.last  # sağdaki eye/görüntüle eylemi (indirme DEĞİL)
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _viewer_body_text(self, newp) -> str:  # pragma: no cover - canlı DOM
        """Görüntüleyici gövde metnini sınırlı biçimde döndürür (sonuç sınıflandırması için; ham DOM saklanmaz)."""
        try:
            return (newp.inner_text("body", timeout=2000) or "")[:2000]
        except Exception:
            return ""

    def _locate_row_download_action(self, page, label, download_spec):  # pragma: no cover - canlı DOM
        """Fix 6.1: AYNI satırın POZİTİF çözülmüş download eylemini, çözümü üreten semantikle bulur.

        Seçiciler yalnız çözülen download metadata'sından türetilir (download erişilebilir ad / download
        ikon token'ı / download attribute / download href). Global/Nth/başka-satır download KULLANILMAZ.
        """
        import re as _re

        if not download_spec:
            return None
        key = _re.escape(_demojibake(label or "").split("/")[0].strip()[:24])
        if not key:
            return None
        selectors: list[str] = []
        nm = download_spec.get("accessible_name")
        if nm and _text_has_download(nm):
            safe = _re.sub(r'["\\]', "", str(nm))[:20]
            if safe:
                selectors += [f'[title*="{safe}" i]', f'[aria-label*="{safe}" i]']
        for tok in (download_spec.get("icon_tokens") or [])[:8]:
            if _text_has_download(tok):
                safe = _re.sub(r'["\\]', "", str(tok))[:24]
                if safe:
                    selectors += [f'[class*="{safe}" i]', f'a:has([class*="{safe}" i])', f'button:has([class*="{safe}" i])']
        if download_spec.get("download_attr"):
            selectors.append("a[download]")
        if download_spec.get("href_kind") == "download":
            selectors += ['a[href*="indir" i]', 'a[href$=".udf" i]', 'a[href$=".pdf" i]']
        if not selectors:
            return None
        for rsel in ("[class*=doc]", "tr", "li", "[class*=evrak]", "[class*=row]", "div", "section"):
            try:
                row = page.locator(rsel).filter(has_text=_re.compile(key, _re.I))
                if row.count() == 0:
                    continue
                r = row.last if rsel in ("div", "section") else row.first
                for s in selectors:
                    try:
                        act = r.locator(s)
                        # Fix 13: satır-yerel kontrol TEKİL olmalı — >1 ise 'satır' aslında bir konteyner
                        # (birden çok satırın download'ı) demektir; ilk eşleşmeyi (çapraz-satır) ALMA, ATLA.
                        if act.count() == 1:
                            return act.first  # satır-yerel, POZİTİF, TEKİL download eylemi
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _collect_native_udf_download(self, page, label, sel, resolution, attempt, diag, documents):  # pragma: no cover - canlı DOM
        """Fix 12+13: AYNI-SATIR resmî ``.udf`` indirme → NATIVE çıkarım → belge-türü KORROBORASYONU → kaynak metin.

        Fix 13: DocumentRow KİMLİĞİ indirme tıklaması boyunca KORUNUR — istenen (artifact_type + normalized_label)
        EŞSİZ mantıksal satıra reacquire edilir (belirsizse indirme YOK); download kontrolü satır-yerel TEKİL
        olmalı (çapraz-satır/ilk/global/Nth YOK). İndirilen native kaynak metninin SEMANTİK belge-türü istenen
        türle KORROBORE edilir; uyuşmazsa (Run-13: sale_notice indirildi) artifact PROMOTE EDİLMEZ (baytlar
        tanı için KORUNUR ama auction_result kanıtı sayılmaz). Döner True (korrobore native kaynak) ya da False.
        """
        attempt["native_download_attempted"] = True
        requested_type = sel["artifact_type"]
        requested_label = _ascii_lower(_demojibake(label or ""))[:60]
        attempt["native_requested_artifact_type"] = requested_type
        attempt["native_requested_normalized_label"] = requested_label
        # Fix 13: istenen kimlikle EŞSİZ mantıksal satır reacquire edilir (belirsiz/çok-kimlikli → indirme YOK).
        unique_row = select_unique_document_row(diag.get("recognized_document_rows"), requested_type, requested_label)
        attempt["native_row_reacquired"] = unique_row is not None
        attempt["native_row_reacquired_artifact_type"] = (unique_row or {}).get("artifact_type")
        attempt["native_row_reacquired_label_match"] = bool(
            unique_row and _ascii_lower(_demojibake((unique_row or {}).get("normalized_label") or ""))[:60] == requested_label)
        if unique_row is None:
            attempt["native_udf_blocking_reason"] = "ambiguous_or_unresolved_row_reacquisition"
            return False
        dl_spec = resolution.get("download_action")
        if not resolution.get("download_action_resolved") or dl_spec is None:
            attempt["native_download_action_resolved"] = False
            attempt["native_udf_blocking_reason"] = "no_resolved_same_row_download_action"
            return False
        attempt["native_download_action_resolved"] = True
        dl = self._locate_row_download_action(page, label, dl_spec)
        if dl is None:
            # TEKİL satır-yerel download bulunamadı (çapraz-satır grab'ı önlemek için katı; dürüst blocker).
            attempt["native_action_owner_same_row"] = False
            attempt["native_udf_blocking_reason"] = "same_row_download_control_not_located_uniquely"
            return False
        attempt["native_action_owner_same_row"] = True
        attempt["native_action_owner_semantic_revalidated"] = True
        attempt["native_action_owner_fingerprint_match"] = True
        try:
            with page.expect_download(timeout=8000) as dinfo:
                dl.click(timeout=4000)
            download = dinfo.value
        except Exception as exc:
            attempt["native_download_event_detected"] = False
            attempt["native_udf_blocking_reason"] = "no_download_event_detected"
            attempt["blocking_reason"] = str(exc)[:120] or "no_download_event_detected"
            return False
        attempt["native_download_event_detected"] = True
        try:
            fname = download.suggested_filename or ""
        except Exception:
            fname = ""
        ext = (Path(fname).suffix.lower() or None) if fname else None
        attempt["native_artifact_extension"] = ext
        # KESİN baytlar gitignored artifact deposuna yazılır (mutasyona uğratılmaz; provenans için).
        try:
            dest_dir = Path(store.DEFAULT_STORE_DIR) / "artifacts" / "downloads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / _safe_name(fname or "uyap_download.udf")
            download.save_as(str(dest))
            data = dest.read_bytes()
        except Exception as exc:
            attempt["native_udf_blocking_reason"] = "download_save_failed"
            attempt["blocking_reason"] = str(exc)[:120]
            return False
        full_sha = _sha256_bytes(data)
        attempt["native_artifact_collected"] = True
        attempt["native_artifact_size"] = len(data)
        attempt["native_artifact_sha256"] = full_sha[:16]
        # NATIVE UDF konteyner çıkarımı (Fix 12; DEĞİŞTİRİLMEZ): ZIP doğrula → content.xml → güvenli XML → metin.
        text, udiag = extract_udf_source_text(data)
        attempt["native_container_kind"] = udiag.get("container_kind")
        attempt["native_udf_zip_valid"] = udiag.get("zip_valid")
        attempt["native_udf_member_names_safe_summary"] = udiag.get("member_names_safe_summary")
        attempt["native_udf_content_xml_found"] = udiag.get("content_xml_found")
        attempt["native_udf_content_xml_size"] = udiag.get("content_xml_size")
        attempt["native_udf_xml_parse_succeeded"] = udiag.get("xml_parse_succeeded")
        attempt["native_udf_content_element_found"] = udiag.get("content_element_found")
        attempt["native_udf_source_text_available"] = udiag.get("source_text_available")
        attempt["native_udf_text_extraction_supported"] = udiag.get("text_extraction_supported")
        if not (text and native_udf_supported(udiag)):
            attempt["native_udf_blocking_reason"] = udiag.get("blocking_reason") or "native_udf_source_unavailable"
            return False
        # Fix 13: SEMANTİK belge-türü KORROBORASYONU (değerden DEĞİL) — promosyondan ÖNCE.
        corr = corroborate_native_document_type(text, requested_type)
        attempt["native_detected_document_type"] = corr["native_detected_document_type"]
        attempt["native_document_type_corroborated"] = corr["native_document_type_corroborated"]
        attempt["native_document_type_mismatch"] = corr["native_document_type_mismatch"]
        attempt["native_document_type_corroboration_reason"] = corr["native_document_type_corroboration_reason"]
        if not corr["native_document_type_corroborated"]:
            # Yanlış-satır/uyuşmayan belge (Run-13: sale_notice) → auction_result kanıtı olarak EKLENMEZ.
            # Baytlar diskte KORUNUR (tanı) ama document_source_artifact_collected/artifact_types_collected'e GİRMEZ.
            attempt["native_udf_source_relation"] = "uncorroborated_document_type_not_promoted"
            attempt["native_udf_blocking_reason"] = f"native_document_type_mismatch:{corr['native_document_type_corroboration_reason']}"
            return False
        # Korrobore edilmiş resmî satır-yerel native kaynak → MEVCUT alan çıkarıcıya (inline text) verilir.
        documents.append({"artifact_type": requested_type, "text": text,
                          "source_ref": f"native_udf:{ext or '.udf'}", "local_path": str(dest)})
        attempt["artifact_collected"] = True
        attempt["document_source_artifact_collected"] = True
        attempt["native_udf_source_relation"] = "official_same_row_native_udf"
        return True

    def _same_row_download_fallback(self, page, label, sel, resolution, attempt, diag, documents):  # pragma: no cover - canlı DOM
        """Fix 6.1: görüntüleyici 'indirme-gerekli' dediğinde, AYNI satırın çözülmüş download eylemiyle
        resmî artifact'ı indirir. Global/Nth/başka-satır download YOK; keyfi tıklama YOK; UYDURMA YOK."""
        attempt["download_fallback_attempted"] = True
        dl_spec = resolution.get("download_action")
        if not resolution.get("download_action_resolved") or dl_spec is None:
            attempt["download_action_resolved"] = False
            attempt["download_fallback_blocking_reason"] = "download_required_but_download_action_unresolved"
            attempt["blocking_reason"] = "download_required_but_download_action_unresolved"
            diag["document_collection_failures"] += 1
            return
        attempt["download_action_resolved"] = True
        dl = self._locate_row_download_action(page, label, dl_spec)
        if dl is None:
            attempt["download_fallback_resolved_same_row"] = False
            attempt["download_fallback_blocking_reason"] = "same_row_download_control_not_located"
            attempt["blocking_reason"] = "same_row_download_control_not_located"
            diag["document_collection_failures"] += 1
            return
        attempt["download_fallback_resolved_same_row"] = True
        try:
            with page.expect_download(timeout=8000) as dinfo:
                dl.click(timeout=4000)
            download = dinfo.value
        except Exception as exc:
            attempt["download_event_detected"] = False
            attempt["download_fallback_blocking_reason"] = "no_download_event_detected"
            attempt["blocking_reason"] = str(exc)[:120] or "no_download_event_detected"
            diag["document_collection_failures"] += 1
            return
        attempt["download_event_detected"] = True
        fname = ""
        try:
            fname = download.suggested_filename or ""
        except Exception:
            fname = ""
        ext = (Path(fname).suffix.lower() or None) if fname else None
        attempt["downloaded_artifact_extension"] = ext
        attempt["downloaded_artifact_mime_hint"] = attempt.get("viewer_mime_type_hint")
        # GİTİGNORE'lı artifact deposu (analitik veri kümesine GİRMEZ; yalnız provenans).
        try:
            dest_dir = Path(store.DEFAULT_STORE_DIR) / "artifacts" / "downloads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / _safe_name(fname or "uyap_download.bin")
            download.save_as(str(dest))
            data = dest.read_bytes()
            attempt["downloaded_artifact_size"] = len(data)
            attempt["downloaded_artifact_sha256"] = _sha256_bytes(data)[:16]
            attempt["downloaded_artifact_collected"] = True
        except Exception as exc:
            attempt["download_fallback_blocking_reason"] = "download_save_failed"
            attempt["blocking_reason"] = str(exc)[:120]
            diag["document_collection_failures"] += 1
            return
        supported = extraction_supported_for(ext, attempt.get("viewer_mime_type_hint"))
        attempt["downloaded_artifact_extraction_supported"] = supported
        if supported:
            try:
                text = dest.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            if text:
                documents.append({"artifact_type": sel["artifact_type"], "text": text,
                                  "source_ref": f"download:{ext}", "local_path": str(dest)})
                attempt["artifact_collected"] = True
            else:
                attempt["download_fallback_blocking_reason"] = "downloaded_artifact_empty"
                attempt["blocking_reason"] = "downloaded_artifact_empty"
                diag["document_collection_failures"] += 1
        else:
            # Resmî artifact diskte KORUNUR (provenans: type+ext+size+sha) ama deterministik çıkarım
            # DESTEKLENMEZ → ikili görüntüleyiciye/çıkarıma VERİLMEZ (dürüst; UYDURMA/çöp-metin YOK).
            documents.append({"artifact_type": sel["artifact_type"], "source_ref": f"download:{ext}",
                              "extraction_supported": False})
            attempt["download_fallback_blocking_reason"] = f"downloaded_artifact_extraction_unsupported:{ext or 'binary'}"
            attempt["blocking_reason"] = f"downloaded_artifact_extraction_unsupported:{ext or 'binary'}"

    def _viewer_image_candidates(self, newp) -> list:  # pragma: no cover - canlı DOM
        """Görüntüleyicideki ``<img>`` elemanlarının GÜVENLİ yapısal metadata'sını döner (ham DOM saklanmaz).

        naturalWidth/Height, render boyutu, görünürlük, görüntüleyici-içerik kapsamı (header/nav/footer/
        logo DIŞI), same-origin ve src (yalnız KAYNAK-YAKALAMA için içsel; tanıya SIZMAZ). GÖRSEL/OCR YOK.
        """
        try:
            raw = newp.eval_on_selector_all("img", """(imgs) => imgs.map((im) => {
                const r = im.getBoundingClientRect();
                const st = window.getComputedStyle(im);
                const visible = st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                let scoped = true, p = im.parentElement, depth = 0;
                while (p && depth < 8) {
                    const c = (p.className || '').toString().toLowerCase();
                    const t = (p.tagName || '').toLowerCase();
                    if (t === 'header' || t === 'nav' || t === 'footer' ||
                        c.indexOf('logo') >= 0 || c.indexOf('header') >= 0 || c.indexOf('nav') >= 0 ||
                        c.indexOf('footer') >= 0 || c.indexOf('brand') >= 0) { scoped = false; break; }
                    p = p.parentElement; depth++;
                }
                let same = true;
                try {
                    const u = new URL(im.currentSrc || im.src || '', location.href);
                    if (u.protocol === 'http:' || u.protocol === 'https:') same = (u.origin === location.origin);
                } catch (e) {}
                return {
                    natural_width: im.naturalWidth || 0, natural_height: im.naturalHeight || 0,
                    rendered_width: Math.round(r.width), rendered_height: Math.round(r.height),
                    visible: visible, viewer_content_scoped: scoped, same_origin: same,
                    src: im.src || '', current_src: im.currentSrc || ''
                };
            })""")
            return raw or []
        except Exception:
            return []

    def _capture_image_source(self, newp, src, src_kind):  # pragma: no cover - canlı DOM
        """Belge-render görüntüsünün KESİN kaynak baytlarını yakalar (OCR YOK). Döner ``(bytes, mime, ext)``.

        data: → doğrudan çöz; blob:/same-origin → görüntüleyici sayfasında KISITLI tarayıcı-içi fetch
        (mevcut kimlik-doğrulamalı bağlam; elle çerez/token KOPYALANMAZ). Ekran görüntüsü ALINMAZ.
        """
        if src_kind == "data_url":
            return decode_data_url(src)
        if src_kind in ("blob_url", "http_resource", "relative_resource"):
            try:
                res = newp.evaluate("""async (u) => {
                    try {
                        const r = await fetch(u);
                        const b = await r.arrayBuffer();
                        const bytes = new Uint8Array(b);
                        let s = ''; const CH = 0x8000;
                        for (let i = 0; i < bytes.length; i += CH) { s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH)); }
                        return { ok: true, b64: btoa(s), mime: (r.headers.get('content-type') || '') };
                    } catch (e) { return { ok: false }; }
                }""", src)
            except Exception:
                return None
            if not res or not res.get("ok") or not res.get("b64"):
                return None
            import base64
            try:
                data = base64.b64decode(res["b64"])
            except Exception:
                return None
            mime = (res.get("mime") or "").split(";")[0].strip().lower() or "application/octet-stream"
            ext = image_mime_to_extension(mime) or _safe_image_ext_hint(src) or ".bin"
            return data, mime, ext
        return None

    def _collect_viewer_image(self, newp, attempt, sel, diag):  # pragma: no cover - canlı DOM
        """Fix 8+9: görüntü-destekli görüntüleyicide belge-render adayının KESİN kaynağını yakalar ve
        görüntüleyici-ASSET olarak saklar (Fix 8 bayt yakalama KORUNUR: data/blob/same-origin, tam
        bayt/size/sha/store).

        Fix 9: TEK yakalama TEK BAŞINA satırın belge-kaynak artifact'ını PROMOTE ETMEZ — kimlik (cross-
        document tam-SHA guard + belge-render ilişkisi) döngü SONRASI çözülür. Görüntü artifact'ı metin
        çıkarımını DESTEKLEMEZ (OCR YOK); İhale Bedeli UYDURULMAZ. Döner: capture kaydı ya da None.
        """
        raw = self._viewer_image_candidates(newp)
        attempt["viewer_image_candidate_count"] = len(raw)
        classified = []
        for m in raw:
            sk = classify_image_src_kind(m.get("src") or m.get("current_src"))
            cand = classify_document_image_candidate({**m, "src_kind": sk})
            classified.append({**m, "src_kind": sk, "document_image_candidate": cand["document_image_candidate"]})
        attempt["viewer_image_candidates"] = [viewer_image_candidate_summary(m, i) for i, m in enumerate(raw)][:8]
        attempt["viewer_document_image_candidate_count"] = sum(1 for c in classified if c["document_image_candidate"])

        idx = select_viewer_image_candidate(classified)
        if idx is None:
            attempt["viewer_image_capture_blocking_reason"] = "no_document_image_candidate"
            attempt["viewer_image_document_identity"] = "not_document_candidate"
            diag["document_collection_failures"] += 1
            return None
        chosen = raw[idx]
        src = chosen.get("src") or chosen.get("current_src")
        src_kind = classify_image_src_kind(src)
        attempt["selected_viewer_image_candidate_index"] = idx
        attempt["viewer_image_source_kind"] = src_kind
        cap = image_source_capture_supported(src_kind, bool(chosen.get("same_origin", True)))
        attempt["viewer_image_source_capture_supported"] = bool(cap.get("supported"))
        attempt["viewer_image_source_capture_strategy"] = cap.get("strategy")
        if not cap.get("supported"):
            attempt["viewer_image_capture_blocking_reason"] = cap.get("reason") or "source_capture_unsupported"
            diag["document_collection_failures"] += 1
            return None
        got = self._capture_image_source(newp, src, src_kind)
        if not got:
            attempt["viewer_image_source_bytes_captured"] = False
            attempt["viewer_image_capture_blocking_reason"] = "source_bytes_not_captured"
            diag["document_collection_failures"] += 1
            return None
        data, mime, ext = got
        full_sha = _sha256_bytes(data)  # Fix 9: TAM sha (cross-document guard tam-SHA kullanır)
        attempt["viewer_image_source_bytes_captured"] = True
        attempt["viewer_image_artifact_mime_hint"] = mime
        attempt["viewer_image_artifact_extension"] = ext
        attempt["viewer_image_artifact_size"] = len(data)
        attempt["viewer_image_artifact_sha256"] = full_sha[:16]   # kısa (tanı gösterimi)
        attempt["viewer_image_source_sha256"] = full_sha          # TAM (kimlik/guard karşılaştırması)
        try:
            dest_dir = Path(store.DEFAULT_STORE_DIR) / "artifacts" / "viewer_images"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / _safe_name(f"{sel['artifact_type']}_{full_sha[:16]}{ext or '.bin'}")
            dest.write_bytes(data)
        except Exception as exc:
            attempt["viewer_image_capture_blocking_reason"] = "image_artifact_save_failed"
            attempt["blocking_reason"] = str(exc)[:120]
            diag["document_collection_failures"] += 1
            return None
        # Fix 9: görüntüleyici-asset KESİN baytlarıyla KORUNDU (mühendislik/provenans) ama belge-KAYNAK
        # İDDİA EDİLMEZ; document_source_artifact_collected kimlik çözülene kadar False. Metin çıkarımı YOK (OCR YOK).
        attempt["viewer_image_artifact_collected"] = True   # görüntüleyici-asset yakalandı (metin/İhale Bedeli DEĞİL)
        attempt["viewer_asset_captured"] = True
        attempt["viewer_image_text_extraction_supported"] = extraction_supported_for(ext, mime)  # → False (görüntü)
        attempt["document_source_artifact_collected"] = False  # promosyon post-loop kimliğe bağlı
        return {"artifact_type": sel["artifact_type"], "sha256": full_sha, "extension": ext,
                "size": len(data), "mime_hint": mime, "stored_path": str(dest)}

    def _observe_viewer_stabilization(self, newp):  # pragma: no cover - canlı DOM
        """Fix 9: SINIRLI (bounded) görüntüleyici kararlılık gözlemi — ilk uygun görüntüyü ANINDA yakalama.

        En çok ``VIEWER_STABILIZATION_MAX_OBSERVATIONS`` kez ucuz güvenli imza gözlenir; gözlemler arası
        ``wait_for_timeout`` ile KISA SINIRLI bekleme (SONSUZ sleep YOK, keyfi uzun sleep YOK). İndirme-
        gerekli / viewer-hata semantiği ANINDA keser (Fix 6.1 önceliği). Son ``MIN_OBSERVATIONS`` imza
        aynıysa erken çıkar (kararlı). ``classify_viewer_ready_state`` + gizlilik-güvenli diziler döner.
        """
        observations: list = []
        rep_seq: list = []
        cnt_seq: list = []
        dim_seq: list = []
        kind_seq: list = []
        last_fp = None
        fp_changed = False
        for i in range(VIEWER_STABILIZATION_MAX_OBSERVATIONS):
            counts = self._viewer_counts(newp)
            representation = classify_viewer_representation(counts)
            vtext = self._viewer_body_text(newp)
            raw = self._viewer_image_candidates(newp)
            classified = []
            for m in raw:
                sk = classify_image_src_kind(m.get("src") or m.get("current_src"))
                cand = classify_document_image_candidate({**m, "src_kind": sk})
                classified.append({**m, "src_kind": sk, "document_image_candidate": cand["document_image_candidate"]})
            idx = select_viewer_image_candidate(classified)
            sel_dim = sel_kind = sel_fp = None
            if idx is not None:
                ch = classified[idx]
                sel_dim = f"{int(ch.get('natural_width') or 0)}x{int(ch.get('natural_height') or 0)}"
                sel_kind = ch.get("src_kind")
                sel_fp = viewer_image_fingerprint(ch)
            obs = {
                "representation": representation,
                "candidate_count": len(raw),
                "selected_dimension": sel_dim,
                "selected_src_kind": sel_kind,
                "selected_fingerprint": sel_fp,
                "download_required": viewer_download_instruction_detected(vtext),
                "viewer_error": classify_viewer_outcome(vtext, representation) == "viewer_error",
                "text_available": bool(counts.get("text_available")),
            }
            observations.append(obs)
            rep_seq.append(representation)
            cnt_seq.append(len(raw))
            dim_seq.append(sel_dim)
            kind_seq.append(sel_kind)
            if last_fp is not None and sel_fp != last_fp:
                fp_changed = True
            last_fp = sel_fp
            if obs["download_required"] or obs["viewer_error"]:
                break
            sigs = [viewer_observation_signature(o) for o in observations]
            if (len(sigs) >= VIEWER_STABILIZATION_MIN_OBSERVATIONS
                    and len(set(sigs[-VIEWER_STABILIZATION_MIN_OBSERVATIONS:])) == 1):
                break  # kararlı → erken çık
            if i < VIEWER_STABILIZATION_MAX_OBSERVATIONS - 1:
                try:
                    newp.wait_for_timeout(VIEWER_STABILIZATION_POLL_MS)  # SINIRLI; unbounded sleep YOK
                except Exception:
                    pass
        ready = classify_viewer_ready_state(observations)
        ready.update({
            "representation_sequence": rep_seq,
            "candidate_count_sequence": cnt_seq,
            "selected_dimension_sequence": dim_seq,
            "selected_src_kind_sequence": kind_seq,
            "fingerprint_changed": fp_changed,
        })
        return ready

    def _viewer_counts(self, newp) -> dict:  # pragma: no cover - canlı DOM
        counts: dict = {}
        for key, sel in (("iframe", "iframe"), ("canvas", "canvas"), ("image", "img"), ("embed", "embed"), ("object", "object")):
            try:
                counts[key] = newp.locator(sel).count()
            except Exception:
                counts[key] = 0
        text = ""
        try:
            text = newp.inner_text("body", timeout=2000) or ""
        except Exception:
            text = ""
        counts["text_available"] = bool(re.search(r"ihale bedeli|artirma sonuc|muhammen|alacaga mahsuben", _ascii_lower(text)))
        return counts

    def _viewer_source_text(self, newp, representation) -> str | None:  # pragma: no cover - canlı DOM
        """Deterministik erişilebilir kaynak metni döndürür; canvas/görüntü-yalnız → None (UYDURMA YOK)."""
        markers = r"ihale bedeli|artirma sonuc|muhammen"
        if representation == "dom_text":
            try:
                t = newp.inner_text("body", timeout=2000)
                if t and re.search(markers, _ascii_lower(t)):
                    return t
            except Exception:
                return None
        if representation == "iframe":
            try:
                for fr in newp.frames:
                    try:
                        t = fr.locator("body").inner_text(timeout=1500)
                    except Exception:
                        continue
                    if t and re.search(markers, _ascii_lower(t)):
                        return t
            except Exception:
                return None
        return None  # embed/object/canvas/image/unknown → deterministik kaynak yok

    def _persist_viewer_source(self, artifact_type, content, attempt):
        """Fix 11: kararlı DOM-metin kaynağını YALNIZCA gitignored yerel artifact deposuna kalıcılaştırır.

        Ham resmî belge içeriği HASSAS olabilir → repoya COMMİT EDİLMEZ, README/DEVELOPMENT_HISTORY/pilot-JSON/
        log/test'e KOPYALANMAZ. Yalnız gizlilik-güvenli provenans (tür/boyut/sha256) attempt tanısına yazılır;
        GÖVDE (kaynak metni) hiçbir tanıya/JSON'a YAZILMAZ. Sonraki gerçek çalıştırmada operatör gerçek
        düzeni bu yerel dosyadan güvenle inceleyebilir. Döner: yerel yol (str) ya da None.
        """
        if not content:
            attempt["source_text_persisted"] = False
            return None
        try:
            data = content.encode("utf-8")
            sha = _sha256_bytes(data)
            dest_dir = Path(store.DEFAULT_STORE_DIR) / "artifacts" / "viewer_sources"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / _safe_name(f"{artifact_type}_{sha[:16]}.txt")
            dest.write_bytes(data)
        except Exception:
            attempt["source_text_persisted"] = False
            return None
        # Yalnız provenans (GÖVDE değil) tanıya yazılır.
        attempt["source_text_persisted"] = True
        attempt["source_text_artifact_sha256"] = sha[:16]
        attempt["source_text_artifact_size"] = len(data)
        return str(dest)


