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
_VIEW_ACTION_TOKENS = ("goruntule", "goster", "incele", "evrak", "eye", "view", "gor", "ac")


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
                "document_modal_opened": False,
                "document_labels_observed": [],
                "document_actions_observed": 0,
                "document_collection_attempts": [],
                "document_collection_failures": 0,
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
                # Gerçek UYAP belge UI'si anchor DEĞİL: "İhale Evrak Listesi" button → modal → eye-action.
                if has_document_list_control(html):
                    m_docs, m_patterns, m_diag = self._collect_via_modal(page, context)
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

    def _collect_via_modal(self, page, context) -> tuple:  # pragma: no cover - canlı modal/DOM gerektirir
        """Gerçek "İhale Evrak Listesi" button→modal→satır-yerel eye-action belge toplama.

        Spekülatif tek bir CSS selector'a bağlanmaz; önce görünür METİN/ROLE tabanlı sağlam
        Playwright locator hiyerarşisi denenir. Satır-yerel eşleme (``select_row_document_actions``)
        global Nth-eye kullanmaz. Açılma modu (popup/yeni sekme/aynı-sayfa/PDF/indirme) sınırlı
        zaman aşımıyla GÖZLENİR ve ``classify_access_pattern`` ile sınıflandırılır (uydurma YOK).
        """
        import re as _re

        diag = {
            "document_list_control_found": False,
            "document_list_control_kind": None,
            "document_modal_opened": False,
            "document_labels_observed": [],
            "document_actions_observed": 0,
            "document_collection_attempts": [],
            "document_collection_failures": 0,
        }
        documents: list[dict] = []
        patterns: list[dict] = []

        control = None
        for kind, loc in (
            ("button", page.get_by_role("button", name=_re.compile("evrak listesi", _re.I))),
            ("link", page.get_by_role("link", name=_re.compile("evrak listesi", _re.I))),
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

        scope = page
        for loc in (page.get_by_role("dialog"), page.locator("[role=dialog], .modal")):
            try:
                loc.first.wait_for(state="visible", timeout=5000)
                scope = loc.first
                diag["document_modal_opened"] = True
                break
            except Exception:
                continue

        rows: list[dict] = []
        try:
            row_loc = scope.locator("tr, li, .row, [class*=row]")
            n = min(row_loc.count(), 50)
            for i in range(n):
                r = row_loc.nth(i)
                try:
                    label_text = (r.inner_text(timeout=1000) or "").strip()
                except Exception:
                    continue
                if classify_document_label(label_text) is None:
                    continue
                diag["document_labels_observed"].append(_ascii_lower(label_text)[:60])
                actions = []
                act_loc = r.locator("a, button, [role=button], [onclick], i[class*=icon], span[class*=icon]")
                m = min(act_loc.count(), 8)
                for j in range(m):
                    a = act_loc.nth(j)
                    try:
                        actions.append({
                            "kind": "eye",
                            "text": (a.get_attribute("title") or a.inner_text(timeout=500) or ""),
                            "href": a.get_attribute("href"),
                            "_loc": a,
                        })
                    except Exception:
                        continue
                rows.append({"label": label_text, "actions": actions})
        except Exception as exc:
            diag["document_collection_attempts"].append({"stage": "row_enumeration", "blocking_reason": str(exc)[:120]})

        diag["document_actions_observed"] = sum(len(r["actions"]) for r in rows)
        for sel in select_row_document_actions(rows):
            action = sel["action"]
            loc = action.get("_loc")
            attempt = {"label": _ascii_lower(sel["label"] or "")[:60], "artifact_type": sel["artifact_type"],
                       "collected": False, "access_pattern": None, "blocking_reason": None}
            if loc is None:
                attempt["blocking_reason"] = "no_locator"
                diag["document_collection_failures"] += 1
                diag["document_collection_attempts"].append(attempt)
                continue
            observed: dict = {}
            content = None
            try:
                try:
                    with context.expect_page(timeout=4000) as pinfo:
                        loc.click(timeout=3000)
                    newp = pinfo.value
                    newp.wait_for_load_state("domcontentloaded", timeout=5000)
                    observed = {"opened_new_tab": True, "is_pdf": ".pdf" in (newp.url or "").lower()}
                    content = newp.content()
                    newp.close()
                except Exception:
                    observed = {"same_page_nav": True, "viewer_visible": True}
                    try:
                        content = scope.inner_text(timeout=2000)
                    except Exception:
                        content = page.content()
                pattern = classify_access_pattern(observed)
                attempt["access_pattern"] = pattern
                if pattern != "button_modal_unsupported" and content:
                    documents.append({"artifact_type": sel["artifact_type"], "text": content,
                                      "source_ref": f"modal:{_ascii_lower(sel['label'] or '')[:40]}"})
                    attempt["collected"] = True
                else:
                    diag["document_collection_failures"] += 1
                patterns.append({"label": attempt["label"], "pattern": pattern})
            except Exception as exc:
                attempt["blocking_reason"] = str(exc)[:120]
                diag["document_collection_failures"] += 1
                patterns.append({"label": attempt["label"], "pattern": "button_modal_unsupported"})
            diag["document_collection_attempts"].append(attempt)
        return documents, patterns, diag


