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


def classify_document_label(text: str) -> str | None:
    """Bir belge etiketini artifact türüne eşler (jeton-tabanlı; GERÇEK DOM metnine karşı).

    ``BLR_BILIRKISI_RAPORU`` → appraisal_report; ``Artırma Sonuç / Uzatma Tutanağı`` →
    auction_result; ``Satış Şartnamesi Ve Tutanağı`` → sale_spec. Uydurma selector YOK.
    """
    fold = re.sub(r"\s+", " ", _ascii_lower(text or "").replace("_", " ")).strip()
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


def _distinct_doc_types(text: str) -> set:
    fold = _ascii_lower(text or "").replace("_", " ")
    return {atype for kw, atype in _DOC_KEYWORDS.items() if kw in fold}


def _row_action_specs(el) -> list[dict]:
    """Bir satır elemanının eylem kontrollerini (a/button/i/img) çıkarır (satır-yerel)."""
    specs: list[dict] = []
    try:
        controls = el.select("a, button, [role=button], i, span, img")
    except Exception:
        return specs
    for a in controls:
        title = a.get("title") or ""
        try:
            txt = a.get_text(" ", strip=True)
        except Exception:
            txt = ""
        cls = " ".join(a.get("class") or [])
        alt = a.get("alt") or ""
        blob = _ascii_lower(" ".join([title, txt, cls, alt]))
        kind = "eye" if any(k in blob for k in _VIEW_ACTION_TOKENS) or "goz" in blob else "control"
        specs.append({"kind": kind, "text": (title or txt or alt), "href": a.get("href"), "css": cls})
    return specs


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
    """Görüntüleyici belge temsili: dom_text / iframe / embed_object / canvas_image_only / unknown."""
    if counts.get("text_available"):
        return "dom_text"
    if counts.get("iframe", 0) > 0:
        return "iframe"
    if counts.get("embed", 0) > 0 or counts.get("object", 0) > 0:
        return "embed_object"
    if counts.get("canvas", 0) > 0 or counts.get("image", 0) > 0:
        return "canvas_image_only"
    return "unknown"


def classify_view_access_pattern(container_kind: str, observed: dict) -> str:
    """Belge-listesi konteyneri + görüntüleme olayından erişim desenini adlandırır (uydurma YOK)."""
    prefix = "same_page_tab" if container_kind == "same_page_tab_panel" else (
        "modal" if container_kind in ("modal", "dialog") else (container_kind or "unknown"))
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
                "document_collection_attempts": [],
                "document_collection_failures": 0,
                "viewer_pages_opened": 0,
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
            "document_collection_attempts": [],
            "document_collection_failures": 0,
            "viewer_pages_opened": 0,
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

        try:
            control.click(timeout=5000)
        except Exception as exc:
            diag["document_collection_attempts"].append({"stage": "control_click", "blocking_reason": str(exc)[:120]})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        # Konteyner state-transition'ını bekle (listeleme→modal; detay→aynı-sayfa panel).
        observed = {"panel_labels_visible": False, "modal_visible": False, "dialog_visible": False, "new_page": False}
        scope = page
        for _ in range(12):  # ~6s
            try:
                if extract_panel_document_rows(page.content()):
                    observed["panel_labels_visible"] = True
            except Exception:
                pass
            for key, sel in (("dialog_visible", "[role=dialog]"), ("modal_visible", ".modal")):
                try:
                    m = page.locator(sel)
                    if m.count() > 0 and m.first.is_visible():
                        observed[key] = True
                        scope = m.first
                except Exception:
                    pass
            if observed["modal_visible"] or observed["dialog_visible"] or observed["panel_labels_visible"]:
                break
            page.wait_for_timeout(500)

        container_kind = classify_document_list_container(observed)
        diag["document_list_container_kind"] = container_kind
        diag["document_list_opened"] = container_kind not in ("not_opened", "unsupported")
        diag["document_modal_opened"] = container_kind in ("modal", "dialog")
        if not diag["document_list_opened"]:
            diag["document_collection_attempts"].append({"stage": "document_list_wait", "blocking_reason": "document list did not become visible after control click"})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        d2, p2 = self._collect_from_container(page, context, scope, container_kind, diag)
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

    def _collect_from_container(self, page, context, scope, container_kind, diag) -> tuple:  # pragma: no cover - canlı DOM
        """İki yolun BİRLEŞTİĞİ ortak belge-satırı toplayıcı: konteynerden satırlar → satır-yerel
        eye (indirme oku DEĞİL) → YENİ-SEKME UDF görüntüleyici. ``diag`` yerinde güncellenir."""
        documents: list[dict] = []
        patterns: list[dict] = []
        try:
            panel_html = scope.inner_html(timeout=2000) if scope is not page else page.content()
        except Exception:
            panel_html = page.content()
        rows = extract_panel_document_rows(panel_html)
        diag["document_labels_observed"] = [_ascii_lower(r["label"])[:60] for r in rows]
        diag["document_actions_observed"] = sum(len(r["actions"]) for r in rows)

        for sel in select_row_document_actions(rows):
            label = sel["label"]
            attempt = {
                "artifact_type": sel["artifact_type"],
                "normalized_document_label": _ascii_lower(label or "")[:60],
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
            }
            eye = self._locate_row_eye(page, label)
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
                    attempt["access_pattern"] = classify_view_access_pattern(
                        container_kind,
                        {"new_page": True, "is_udf": attempt["viewer_url_kind"] == "udf_viewer",
                         "is_pdf": attempt["viewer_url_kind"] == "pdf_viewer"},
                    )
                    content = self._viewer_source_text(newp, representation)
                    if content:
                        documents.append({"artifact_type": sel["artifact_type"], "text": content,
                                          "source_ref": f"viewer:{attempt['viewer_url_kind']}"})
                        attempt["artifact_collected"] = True
                    else:
                        attempt["blocking_reason"] = f"viewer_representation_unsupported:{representation}"
                        diag["document_collection_failures"] += 1
                    try:
                        newp.close()  # operatörün orijinal sekmesi KAPATILMAZ
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
        return documents, patterns

    def _locate_row_eye(self, page, label):  # pragma: no cover - canlı DOM
        import re as _re

        key = _re.escape((label or "").split("/")[0].strip()[:24])
        if not key:
            return None
        for rsel in ("tr", "li", "[class*=evrak]", "[class*=row]"):
            try:
                row = page.locator(rsel).filter(has_text=_re.compile(key, _re.I))
                if row.count() == 0:
                    continue
                r = row.first
                for asel in ("[title*=Görüntüle i]", "[title*=Göster i]", "button", "a", "i", "img"):
                    try:
                        act = r.locator(asel)
                        if act.count() > 0:
                            return act.last  # sağdaki eye/görüntüle eylemi (indirme DEĞİL)
                    except Exception:
                        continue
            except Exception:
                continue
        return None

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


