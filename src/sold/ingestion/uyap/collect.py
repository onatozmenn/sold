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
            t = _ascii_lower(a.get("text", "") or "")
            if a.get("kind") == "eye" or any(k in t for k in _VIEW_ACTION_TOKENS):
                view = a
                break
        if view is None and actions:
            view = actions[0]  # satır-yerel tek eylem
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

    def collect_record(self, url: str | None = None, follow_documents: bool = True) -> dict:  # pragma: no cover - canlı tarayıcı gerektirir
        """Kullanıcının açtığı GERÇEK UYAP kaydını (mevcut sekme ya da verilen URL) toplar.

        CDP ile kullanıcı-kontrollü oturuma bağlanır; ``url`` verilmezse mevcut ÖN sekmeyi
        kullanır (operatörün elle açtığı 2026/263 kaydı). Ana sayfa HTML'i + başlık + URL +
        GERÇEK DOM'dan keşfedilen belge bağlantıları döner; kullanılabilir (normal) belge
        bağlantıları aynı oturumda izlenir. Desteklenmeyen desenler (javascript/popup/PDF-viewer)
        UYDURULMAZ; ``document_access_patterns`` içinde dürüstçe raporlanır. Test paketi bu yolu
        ÇAĞIRMAZ (ağ gerektirmez).
        """
        sync_playwright = self._sync_playwright()
        with sync_playwright() as pw:
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
                            "no_matching_uyap_page: açık sekme yok. Önce 2026/263 UYAP sonuç sayfasını açın."
                        )
                    page = context.pages[0]
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
            links = discover_document_links(html)
            documents: list[dict] = []
            access_patterns: list[dict] = []
            collection_diagnostics: dict = {
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
                # Gerçek UYAP belge UI'si: "İhale Evrak Listesi" AYNI-SAYFA tab/panel (modal DEĞİL);
                # satır-yerel eye → yeni-sekme UDF görüntüleyici. Modal yol yalnızca YEDEK.
                if has_document_list_control(html):
                    m_docs, m_patterns, m_diag = self._collect_documents(page, context)
                    documents += m_docs
                    access_patterns += m_patterns
                    collection_diagnostics = m_diag
            return {
                "html": html,
                "title": title,
                "url": page_url,
                "document_links": links,
                "documents": documents,
                "document_access_patterns": access_patterns,
                "collection_diagnostics": collection_diagnostics,
            }

    def _collect_documents(self, page, context) -> tuple:  # pragma: no cover - canlı DOM/olay gerektirir
        """Gerçek gözlenen UYAP belge iş akışı: "İhale Evrak Listesi" AYNI-SAYFA tab/panel →
        satır-yerel eye → YENİ-SEKME UDF görüntüleyici (viewer.jsp?mimeType=Udf).

        Önce aynı-sayfa panel state-transition'ı algılanır (ilgili belge etiketleri görünür olur);
        modal/dialog YEDEK'tir. Görüntüleyici temsili (dom_text/iframe/embed/canvas-image) runtime'da
        GÖZLENİR; kanıt yalnızca deterministik erişilebilir kaynaktan alınır (UYDURMA/OCR YOK).
        """
        import re as _re

        diag = {
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
        for kind, loc in (
            ("link", page.get_by_role("link", name=_re.compile("evrak listesi", _re.I))),
            ("button", page.get_by_role("button", name=_re.compile("evrak listesi", _re.I))),
            ("tab", page.get_by_role("tab", name=_re.compile("evrak listesi", _re.I))),
            ("text", page.get_by_text(_re.compile("evrak listesi", _re.I))),
        ):
            try:
                if loc.count() > 0:
                    control = loc.first
                    diag["document_list_control_found"] = True
                    diag["document_list_control_kind"] = kind
                    break
            except Exception:
                continue
        if control is None:
            return documents, patterns, diag

        try:
            control.click(timeout=5000)
        except Exception as exc:
            diag["document_collection_attempts"].append({"stage": "control_click", "blocking_reason": str(exc)[:120]})
            diag["document_collection_failures"] += 1
            return documents, patterns, diag

        # AYNI-SAYFA panel / modal / dialog state-transition'ını bekle (sınırlı poll; modal YEDEK).
        observed = {"panel_labels_visible": False, "modal_visible": False, "dialog_visible": False, "new_page": False}
        scope = page
        for _ in range(12):  # ~6s
            try:
                if extract_panel_document_rows(page.content()):
                    observed["panel_labels_visible"] = True
                    break
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
            if observed["modal_visible"] or observed["dialog_visible"]:
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
        return documents, patterns, diag

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


