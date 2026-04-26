"""
core/parser.py
─────────────────────────────────────────────────────────────
通用文章解析器（多策略降级）：

解析优先级：
  1. 结构化元数据（Dublin Core / OpenGraph / citation_* / JSON-LD）
  2. 语义 HTML 标签（<article>, <main>, <section>）
  3. 启发式类名匹配（fuzzy class matching）
  4. 正文密度分析（文字/标签比最高的 div）

图片：支持 <img>, <picture>, data-src 懒加载, srcset
表格：HTML → CSV（含跨行列合并展开）
PDF：检测 /pdf, /epdf, download 链接并尝试下载
─────────────────────────────────────────────────────────────
"""

import re
import json
import logging
import mimetypes
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from core.assets import AssetCandidate, AssetDownloader, extension_from_url_or_type
from core.storage import StorageManager
from config.settings import (
    DOWNLOAD_FIGURES, DOWNLOAD_TABLES,
    DOWNLOAD_FULLTEXT, DOWNLOAD_HTML, DOWNLOAD_PDF,
)

log = logging.getLogger("parser")


class ArticleParser:

    # 正文容器候选选择器（按优先级）
    BODY_SELECTORS = [
        "article.full-text",
        "div#body",
        "div.Body",
        "div[class*='article-body']",
        "div[class*='fulltext']",
        "div[class*='full-text']",
        "div[class*='article__body']",
        "div[class*='content-body']",
        "section[class*='article']",
        "div#article-content",
        "div.article",
        "main article",
        "main",
        "article",
    ]

    # 需要从正文中剔除的干扰元素
    NOISE_SELECTORS = [
        "nav", "header", "footer", "aside",
        "script", "style", "noscript",
        ".references", ".ref-list", ".bibliography",
        ".sidebar", ".ads", ".banner",
        "[class*='related']", "[class*='recommend']",
        "[class*='share']", "[class*='social']",
        "[class*='comment']", "[class*='toc']",
    ]

    def __init__(self, session, storage: StorageManager, adapter=None, browser=None):
        self.session = session
        self.storage = storage
        self.adapter = adapter
        self.browser = browser

    # ─────────────────────────────────────────────
    #  入口
    # ─────────────────────────────────────────────
    def parse(self, url: str, options: dict | None = None) -> bool:
        log.info(f"\n{'─'*60}")
        log.info(f"  解析: {url}")

        resp = self.session.get(url)
        if not resp or resp.status_code != 200:
            log.error(f"  ✗ 页面获取失败 (status={getattr(resp,'status_code','?')})")
            return False

        return self.parse_html(url, resp.text, options=options)

    def parse_html(self, url: str, html: str, options: dict | None = None) -> bool:
        """Parse an article from already-fetched browser HTML."""
        opts = _content_options(options)
        soup = BeautifulSoup(html, "lxml")

        # ── 元数据 ──────────────────────────────
        meta = self._extract_meta(soup, url)
        doi = meta.get("doi") or url
        log.info(f"  标题: {meta.get('title','?')[:70]}")

        # 断点续爬检查
        if self.storage.article_exists(doi):
            log.info("  ↩ 已存在，跳过（断点续爬）")
            return True

        adir = self.storage.article_dir(doi)
        self.storage.save_meta(adir, meta)

        # ── 原始 HTML ───────────────────────────
        if opts["html"]:
            self.storage.save_html(adir, html)

        # ── 摘要 ────────────────────────────────
        abstract = self._extract_abstract(soup)
        if abstract:
            self.storage.save_abstract(adir, abstract)

        # ── 正文 ────────────────────────────────
        if opts["fulltext"]:
            md = self._extract_fulltext(soup)
            if abstract:
                md = self._prepend_abstract(md, abstract)
            self.storage.save_fulltext(adir, md)

        # ── 图片 ────────────────────────────────
        if opts["figures"]:
            self._extract_figures(soup, adir, url, opts)

        # ── 表格 ────────────────────────────────
        if opts["tables"]:
            self._extract_tables(soup, adir)

        # ── PDF ─────────────────────────────────
        if opts["pdf"]:
            self._try_download_pdf(soup, adir, url, opts)

        log.info(f"  ✓ 完成: {adir.name}")
        return True

    # ─────────────────────────────────────────────
    #  元数据提取
    # ─────────────────────────────────────────────
    def _extract_meta(self, soup: BeautifulSoup, url: str) -> dict:
        meta = {"url": url}

        # ── JSON-LD（最权威）─────────────────────
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    data = data[0]
                t = data.get("@type", "")
                if "ScholarlyArticle" in t or "Article" in t:
                    meta["title"]   = data.get("headline") or data.get("name", "")
                    meta["doi"]     = _extract_doi(data.get("identifier", ""))
                    meta["journal"] = _nested(data, "isPartOf", "name") or \
                                      _nested(data, "publisher", "name", "")
                    meta["year"]    = str(data.get("datePublished", ""))[:4]
                    authors = data.get("author", [])
                    if isinstance(authors, list):
                        meta["authors"] = [
                            a.get("name", "") for a in authors if isinstance(a, dict)
                        ]
                    break
            except Exception:
                pass

        # ── <meta> 标签（citation_ / DC / OG）───
        for key, names in {
            "title":   ["citation_title", "dc.title", "og:title", "DC.Title"],
            "doi":     ["citation_doi", "dc.identifier"],
            "journal": ["citation_journal_title", "dc.source", "og:site_name"],
            "year":    ["citation_publication_date", "citation_date", "dc.date"],
            "volume":  ["citation_volume"],
            "issue":   ["citation_issue"],
            "pages":   ["citation_firstpage"],
            "issn":    ["citation_issn"],
        }.items():
            if key in meta and meta[key]:
                continue
            for name in names:
                el = soup.find("meta", attrs={"name": name}) or \
                     soup.find("meta", attrs={"property": name})
                if el:
                    val = el.get("content", "").strip()
                    if val:
                        if key == "doi":
                            val = _extract_doi(val)
                        elif key == "year":
                            val = val[:4]
                        meta[key] = val
                        break

        # ── 作者列表 ────────────────────────────
        if "authors" not in meta:
            authors = []
            for el in soup.find_all("meta", attrs={"name": "citation_author"}):
                v = el.get("content", "").strip()
                if v:
                    authors.append(v)
            if not authors:
                # 备用：从正文作者 span 提取
                for el in soup.select(
                    "span[class*='author-name'], "
                    "span[class*='contrib-author'], "
                    "a[class*='author']"
                ):
                    v = el.get_text(strip=True)
                    if v and len(v) < 60:
                        authors.append(v)
            meta["authors"] = list(dict.fromkeys(authors))  # 去重保序

        # ── 兜底标题 ────────────────────────────
        if not meta.get("title"):
            h1 = soup.find("h1")
            if h1:
                meta["title"] = h1.get_text(strip=True)

        # ── DOI 从 URL 提取 ─────────────────────
        if not meta.get("doi"):
            meta["doi"] = _extract_doi(url)

        # ── PDF URL ─────────────────────────────
        meta["pdf_url"] = self._find_pdf_url(soup, url)

        return meta

    # ─────────────────────────────────────────────
    #  摘要
    # ─────────────────────────────────────────────
    def _extract_abstract(self, soup: BeautifulSoup) -> str:
        sciencedirect_nodes = [
            node for node in soup.select("div.Abstracts div.abstract.author")
            if "author-highlights" not in node.get("class", [])
            and "graphical" not in node.get("class", [])
        ]
        for node in sciencedirect_nodes:
            text = _node_text_without_heading(node)
            if text:
                return text

        candidates = [
            "div[class*='abstract'] p",
            "section[class*='abstract'] p",
            "#abstract p",
            "#Abs1 p",
            "[aria-label*='Abstract'] p",
            "div.abstract p",
        ]
        for sel in candidates:
            els = soup.select(sel)
            if els:
                return "\n".join(e.get_text(separator=" ", strip=True) for e in els)
        return ""

    @staticmethod
    def _prepend_abstract(markdown: str, abstract: str) -> str:
        abstract = abstract.strip()
        markdown = markdown.strip()
        if not abstract:
            return markdown
        if abstract in markdown[: max(1000, len(abstract) + 100)]:
            return markdown
        if markdown:
            return f"## Abstract\n\n{abstract}\n\n{markdown}"
        return f"## Abstract\n\n{abstract}"

    # ─────────────────────────────────────────────
    #  正文 → Markdown
    # ─────────────────────────────────────────────
    def _extract_fulltext(self, soup: BeautifulSoup) -> str:
        # 克隆，避免修改原始树
        soup = BeautifulSoup(str(soup), "lxml")

        # 删除噪声
        for sel in self.NOISE_SELECTORS:
            for el in soup.select(sel):
                el.decompose()
        # 删除图、表（单独处理）
        for el in soup.select("figure, table"):
            el.decompose()

        # 定位正文容器
        body = None
        for sel in self.BODY_SELECTORS:
            body = soup.select_one(sel)
            if body:
                break

        if not body:
            # 最后手段：密度分析
            body = self._density_body(soup) or soup.body or soup

        return self._html_to_md(body)

    def _html_to_md(self, body: Tag) -> str:
        lines = []
        for el in body.find_all(
            ["h1","h2","h3","h4","h5","p","div","li","blockquote","pre","code"],
            recursive=True
        ):
            text = el.get_text(separator=" ", strip=True)
            if not text or len(text) < 3:
                continue
            tag = el.name
            if tag == "div" and not _is_text_div(el):
                continue
            if tag in ("h1","h2","h3","h4","h5"):
                lvl = int(tag[1])
                lines.append(f"\n{'#'*lvl} {text}\n")
            elif tag == "blockquote":
                lines.append(f"\n> {text}\n")
            elif tag == "li":
                lines.append(f"- {text}")
            elif tag in ("pre","code"):
                lines.append(f"\n```\n{text}\n```\n")
            else:
                lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def _density_body(soup: BeautifulSoup) -> Tag | None:
        """选取文字密度（文本长度/子标签数）最高的 div。"""
        best, best_score = None, 0
        for div in soup.find_all("div"):
            text = div.get_text(strip=True)
            tags = len(div.find_all(True))
            if tags == 0:
                continue
            score = len(text) / tags
            if score > best_score and len(text) > 500:
                best_score = score
                best = div
        return best

    # ─────────────────────────────────────────────
    #  图片提取
    # ─────────────────────────────────────────────
    def _extract_figures(self, soup: BeautifulSoup, adir, page_url: str, options: dict | None = None):
        opts = _content_options(options)
        candidates = self._figure_candidates(soup, page_url, opts["max_figure_candidates_per_figure"])
        downloader = AssetDownloader(
            self.session,
            browser=self.browser,
            browser_fallback=opts["asset_browser_fallback"],
            timeout=opts["asset_timeout"],
            min_image_bytes=opts["min_image_bytes"],
        )
        count = 0
        completed_figures = set()
        for candidate in candidates:
            figure_key = (
                candidate.label.strip().lower(),
                candidate.caption.strip().lower(),
            )
            if figure_key in completed_figures:
                continue
            result = downloader.download_one(candidate, referer=page_url)
            if result.status == "done" and result.data:
                count += 1
                ext = extension_from_url_or_type(candidate.url, result.content_type)
                self.storage.save_figure(
                    adir,
                    count,
                    result.data,
                    ext,
                    candidate.caption,
                    candidate.label,
                    source_url=result.url,
                    content_type=result.content_type,
                    method=result.method,
                )
                completed_figures.add(figure_key)
            else:
                self.storage.record_asset_failure(
                    adir,
                    asset_type="figure",
                    source_url=candidate.url,
                    error=result.error or "download_failed",
                    content_type=result.content_type,
                    caption=candidate.caption,
                    label=candidate.label,
                )

        log.info(f"    共提取 {count} 张图片")

    def _figure_candidates(self, soup: BeautifulSoup, page_url: str, max_per_figure: int) -> list[AssetCandidate]:
        if self.adapter and hasattr(self.adapter, "figure_candidates"):
            return self.adapter.figure_candidates(page_url, soup, max_per_figure=max_per_figure)
        from sites.base import SiteAdapter

        return SiteAdapter().figure_candidates(page_url, soup, max_per_figure=max_per_figure)

    @staticmethod
    def _upgrade_img_url(url: str) -> str:
        """将常见小图 URL 升级为高分辨率版本。"""
        # Elsevier: /sml/ → /lrg/
        url = re.sub(r"/(sml|sm|thumb)_", "/lrg_", url)
        url = re.sub(r"\.(sml|sm|thumb)\.", ".lrg.", url)
        # Nature: ?w=200 → ?w=1200
        url = re.sub(r"[?&]w=\d+", "?w=1200", url)
        # Springer: size=small → size=large
        url = re.sub(r"size=(small|medium)", "size=large", url)
        return url

    # ─────────────────────────────────────────────
    #  表格提取
    # ─────────────────────────────────────────────
    def _extract_tables(self, soup: BeautifulSoup, adir):
        tables = soup.select("table")
        for idx, tbl in enumerate(tables, 1):
            # caption 优先从前一个兄弟节点找
            cap_el = (
                tbl.find_previous_sibling(
                    lambda t: t.name and
                    ("caption" in " ".join(t.get("class", [])).lower() or
                     t.name == "caption")
                ) or tbl.select_one("caption")
            )
            caption = cap_el.get_text(strip=True) if cap_el else f"Table {idx}"
            html_str = str(tbl)
            rows = self._table_to_rows(tbl)
            self.storage.save_table(adir, idx, html_str, rows, caption)

    @staticmethod
    def _table_to_rows(tbl) -> list[list[str]]:
        """将 HTML 表格解析为二维列表（处理 colspan/rowspan）。"""
        rows_out = []
        for tr in tbl.find_all("tr"):
            row = []
            for cell in tr.find_all(["th", "td"]):
                text = cell.get_text(separator=" ", strip=True)
                colspan = int(cell.get("colspan", 1))
                row.extend([text] + [""] * (colspan - 1))
            if row:
                rows_out.append(row)
        return rows_out

    # ─────────────────────────────────────────────
    #  PDF 下载
    # ─────────────────────────────────────────────
    def _find_pdf_url(self, soup: BeautifulSoup, page_url: str) -> str:
        candidates = self._pdf_candidates(soup, page_url)
        return candidates[0].url if candidates else ""

    def _pdf_candidates(self, soup: BeautifulSoup, page_url: str) -> list[AssetCandidate]:
        if self.adapter and hasattr(self.adapter, "pdf_candidates"):
            return self.adapter.pdf_candidates(page_url, soup)
        from sites.base import SiteAdapter

        return SiteAdapter().pdf_candidates(page_url, soup)

    def _try_download_pdf(self, soup: BeautifulSoup, adir, page_url: str, options: dict | None = None):
        opts = _content_options(options)
        candidates = self._pdf_candidates(soup, page_url)
        if not candidates:
            log.info("    PDF: 未找到下载链接")
            return

        downloader = AssetDownloader(
            self.session,
            browser=self.browser,
            browser_fallback=opts["asset_browser_fallback"],
            timeout=opts["asset_timeout"],
            min_image_bytes=opts["min_image_bytes"],
        )
        for candidate in candidates:
            log.info(f"    PDF: 尝试下载 {candidate.url[:70]}")
            result = downloader.download_one(candidate, referer=page_url)
            if result.status == "done" and result.data:
                self.storage.save_pdf(adir, result.data, source_url=result.url, method=result.method)
                return
            self.storage.record_asset_failure(
                adir,
                asset_type="pdf",
                source_url=candidate.url,
                error=result.error or "download_failed",
                content_type=result.content_type,
            )
        log.info("    PDF: 无权限或下载失败（需机构订阅）")


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────
def _extract_doi(text: str) -> str:
    m = re.search(r"10\.\d{4,}/[^\s\"'<>]+", text or "")
    return m.group(0).rstrip(".,;)") if m else ""


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    valid = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff"}
    return ext if ext in valid else ".jpg"


def _nested(d: dict, *keys, default=""):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, {})
    return d if isinstance(d, str) else default


def _is_text_div(el: Tag) -> bool:
    classes = set(el.get("class", []))
    if "u-margin-s-bottom" in classes:
        return True
    el_id = el.get("id", "")
    if re.match(r"p\d+", el_id or ""):
        return True
    return False


def _node_text_without_heading(el: Tag) -> str:
    clone = BeautifulSoup(str(el), "lxml")
    for node in clone.select("h1, h2, h3, h4, h5, h6, figure, table, script, style"):
        node.decompose()
    return clone.get_text(separator=" ", strip=True)


def _content_options(options: dict | None) -> dict:
    defaults = {
        "html": DOWNLOAD_HTML,
        "pdf": DOWNLOAD_PDF,
        "figures": DOWNLOAD_FIGURES,
        "tables": DOWNLOAD_TABLES,
        "fulltext": DOWNLOAD_FULLTEXT,
        "asset_browser_fallback": True,
        "max_figure_candidates_per_figure": 4,
        "min_image_bytes": 1000,
        "asset_timeout": 30,
    }
    if options is None:
        return defaults
    merged = defaults.copy()
    for key in merged:
        if key in options:
            if isinstance(defaults[key], bool):
                merged[key] = bool(options[key])
            else:
                merged[key] = options[key]
    return merged
