"""ScienceDirect site adapter."""

import json
import logging
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from core.assets import AssetCandidate
from sites.base import SearchResult, SiteAdapter, first_year

log = logging.getLogger("sites.sciencedirect")


class ScienceDirectAdapter(SiteAdapter):
    key = "sciencedirect"
    name = "ScienceDirect (Elsevier)"
    domains = ("sciencedirect.com", "www.sciencedirect.com")
    search_base = "https://www.sciencedirect.com/search"
    login_url = "https://www.sciencedirect.com/search"
    article_domain = "www.sciencedirect.com"
    supports_search = True
    requires_login = True

    result_selectors = (
        "a.result-list-title-link, "
        "h2.article-title a, "
        "div.result-item-content a[href*='/science/article/']"
    )
    next_selectors = [
        "li.pagination-next button",
        "button[aria-label='Next page']",
        "a[aria-label='Go to next page']",
        "a[aria-label='Next page']",
    ]

    def search(self, engine, query: str, year_from: int = 2024, year_to: int = 2025, max_results: int = 200):
        page_size = 25
        url = f"{self.search_base}?{urlencode({
            'qs': query,
            'date': f'{year_from}-{year_to}',
            'show': page_size,
            'sortBy': 'relevance',
        })}"
        html = engine.goto(url)
        results: list[SearchResult] = []
        seen: set[str] = set()
        page = 1

        while len(results) < max_results:
            engine.scroll_to_bottom()
            html = engine.html() or html
            page_results = [result for result in self.extract_results(html) if result.url not in seen]
            if not page_results and not results:
                engine.wait_for_user(
                    "\n当前页面没有解析到 ScienceDirect 搜索结果。"
                    "\n如果页面停在登录、机构认证或验证流程，请在浏览器中完成后回到终端按 Enter。"
                )
                html = engine.html() or html
                page_results = [result for result in self.extract_results(html) if result.url not in seen]
            for result in page_results:
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    break

            log.info("  [SD/browser] 第 %s 页 | 新增 %s 篇 | 共 %s 篇", page, len(page_results), len(results))
            if len(results) >= max_results or not page_results:
                break
            if not engine.click_next(self.next_selectors):
                next_url = self._search_url(query, year_from, year_to, page_size, page * page_size)
                log.info("  [SD/browser] 下一页按钮不可用，改用 offset 翻页: %s", page * page_size)
                html = engine.goto(next_url)
            page += 1

        return results[:max_results]

    def _search_url(
        self,
        query: str,
        year_from: int,
        year_to: int,
        page_size: int = 25,
        offset: int = 0,
    ) -> str:
        params = {
            "qs": query,
            "date": f"{year_from}-{year_to}",
            "show": page_size,
            "sortBy": "relevance",
        }
        if offset:
            params["offset"] = offset
        return f"{self.search_base}?{urlencode(params)}"

    def extract_results(self, html: str) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[SearchResult] = []
        seen: set[str] = set()

        for anchor in soup.select(self.result_selectors):
            href = anchor.get("href", "")
            url = self.normalize_url(urljoin("https://www.sciencedirect.com", href))
            if not url or url in seen:
                continue
            seen.add(url)
            card = (
                anchor.find_parent(attrs={"class": re.compile("result|Result")})
                or anchor.find_parent("li")
                or anchor.find_parent("article")
                or anchor
            )
            results.append(SearchResult(
                url=url,
                title=anchor.get_text(" ", strip=True),
                year=first_year(card.get_text(" ", strip=True)),
            ))
        return results

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if "sciencedirect.com" not in parsed.netloc:
            return url
        path = parsed.path.rstrip("/")
        if "/science/article/pii/" not in path:
            return ""
        for suffix in ("/pdfft", "/pdf"):
            if suffix in path:
                path = path.split(suffix, 1)[0]
                break
        return f"https://www.sciencedirect.com{path}"

    def find_pdf_url(self, page_url: str, soup: BeautifulSoup) -> str:
        return page_url.rstrip("/") + "/pdf"

    def figure_candidates(self, page_url: str, soup: BeautifulSoup, max_per_figure: int = 4) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []

        for url, source, label, caption in self._figure_download_links(page_url, soup):
            if not self._is_article_figure_url(url, page_url):
                continue
            priority = 0 if source == "sciencedirect_highres_link" else 10
            candidates.append(AssetCandidate(
                type="figure",
                url=url,
                source=source,
                label=label,
                caption=caption,
                priority=priority,
            ))

        candidates.extend(
            candidate for candidate in self._preloaded_image_candidates(page_url, soup)
            if self._is_article_figure_url(candidate.url, page_url)
        )

        fallback_candidates = []
        for candidate in super().figure_candidates(page_url, soup, max_per_figure=max_per_figure):
            if not self._is_article_figure_url(candidate.url, page_url):
                continue
            normalized_candidate = self._with_sciencedirect_figure_key(candidate)
            fallback_candidates.append(normalized_candidate)
            upgraded = self._upgrade_image_url(candidate.url)
            if upgraded != candidate.url and self._is_article_figure_url(upgraded, page_url):
                fallback_candidates.append(AssetCandidate(
                    type="figure",
                    url=upgraded,
                    source="sciencedirect_url_upgrade",
                    label=normalized_candidate.label,
                    caption=normalized_candidate.caption,
                    priority=20,
                ))

        candidates.extend(fallback_candidates)
        seen = set()
        result = []
        for candidate in sorted(candidates, key=lambda item: item.priority):
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            result.append(candidate)
        return result

    @staticmethod
    def _article_pii(page_url: str) -> str:
        match = re.search(r"/pii/([^/?#]+)", page_url)
        return match.group(1) if match else ""

    @staticmethod
    def _is_article_figure_url(url: str, page_url: str) -> bool:
        url_lower = url.lower()
        if any(token in url_lower for token in ("cover", "cov150", "cov200", "logo", "non-solus", "dwoodhead")):
            return False
        image_key = ScienceDirectAdapter._image_key(url)
        if not image_key:
            return False
        pii = ScienceDirectAdapter._article_pii(page_url).lower()
        if pii and pii not in url_lower:
            return False
        return True

    @staticmethod
    def _with_sciencedirect_figure_key(candidate: AssetCandidate) -> AssetCandidate:
        label = candidate.label or ScienceDirectAdapter._image_key(candidate.url)
        if label == candidate.label:
            return candidate
        return AssetCandidate(
            type=candidate.type,
            url=candidate.url,
            source=candidate.source,
            label=label,
            caption=candidate.caption,
            priority=candidate.priority,
            content_type_hint=candidate.content_type_hint,
        )

    @staticmethod
    def _figure_download_links(page_url: str, soup: BeautifulSoup) -> list[tuple[str, str, str, str]]:
        """Extract ScienceDirect figure download links from rendered article HTML."""
        results: list[tuple[str, str, str, str]] = []
        for anchor in soup.find_all("a"):
            href = anchor.get("href", "")
            if not href:
                continue
            text = anchor.get_text(" ", strip=True).lower()
            title = " ".join(
                str(anchor.get(attr, "")) for attr in ("title", "aria-label", "download")
            ).lower()
            combined = f"{text} {title}"
            if not ScienceDirectAdapter._is_image_url(href):
                continue

            url = urljoin(page_url, href)
            label, caption = ScienceDirectAdapter._figure_text(anchor, url)
            if "high-res" in combined or "highres" in combined or "high res" in combined or "_lrg" in href:
                results.append((url, "sciencedirect_highres_link", label, caption))
            elif "full-size" in combined or "full size" in combined:
                results.append((url, "sciencedirect_fullsize_link", label, caption))
        return results

    @staticmethod
    def _figure_text(anchor, url: str) -> tuple[str, str]:
        figure = anchor.find_parent("figure") or anchor.find_parent(attrs={"class": re.compile("figure|Fig", re.I)})
        if not figure:
            return ScienceDirectAdapter._image_key(url), ""
        caption_el = figure.select_one("figcaption, [class*='caption'], [class*='legend'], [class*='fig-caption']")
        label_el = figure.select_one("[class*='label'], [class*='fig-num']")
        caption = caption_el.get_text(" ", strip=True) if caption_el else ""
        label = label_el.get_text(" ", strip=True) if label_el else ""
        return label or ScienceDirectAdapter._image_key(url), caption

    @staticmethod
    def _image_key(url: str) -> str:
        """Return ScienceDirect image id, e.g. gr1, fx2, ga1."""
        match = re.search(r"(?:^|[/\-_])(gr[0-9a-z]+|fx[0-9a-z]+|ga[0-9a-z]+)", url, re.IGNORECASE)
        return match.group(1).lower() if match else ""

    @staticmethod
    def _preloaded_state(soup: BeautifulSoup) -> dict:
        """Parse window.__PRELOADED_STATE__ from ScienceDirect article HTML."""
        marker = "window.__PRELOADED_STATE__"
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            start = text.find(marker)
            if start < 0:
                continue
            equals = text.find("=", start)
            brace_start = text.find("{", equals)
            if equals < 0 or brace_start < 0:
                continue
            payload = ScienceDirectAdapter._balanced_json_object(text, brace_start)
            if not payload:
                continue
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _balanced_json_object(text: str, start: int) -> str:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return ""

    @staticmethod
    def _preloaded_image_candidates(page_url: str, soup: BeautifulSoup) -> list[AssetCandidate]:
        state = ScienceDirectAdapter._preloaded_state(soup)
        if not state:
            return []

        candidates: list[AssetCandidate] = []
        visited: set[int] = set()

        def scan(obj):
            if id(obj) in visited:
                return
            visited.add(id(obj))
            if isinstance(obj, dict):
                url = obj.get("ucs-locator") or obj.get("href") or obj.get("url") or obj.get("src") or ""
                if isinstance(url, str) and url and ScienceDirectAdapter._is_image_url(url):
                    attachment_type = str(obj.get("attachment-type", "")).upper()
                    attachment_eid = str(obj.get("attachment-eid", ""))
                    width = ScienceDirectAdapter._safe_int(obj.get("pixel-width"))
                    is_highres = (
                        "_lrg" in url.lower()
                        or "HIGHRES" in attachment_type
                        or "HIGH-RES" in attachment_type
                        or "ORIGINAL" in attachment_type
                        or width >= 500
                    )
                    source = "sciencedirect_preloaded_highres" if is_highres else "sciencedirect_preloaded"
                    candidates.append(AssetCandidate(
                        type="figure",
                        url=urljoin(page_url, url),
                        source=source,
                        label=ScienceDirectAdapter._image_key(url) or attachment_eid[:20],
                        caption="",
                        priority=5 if is_highres else 15,
                    ))
                for value in obj.values():
                    scan(value)
            elif isinstance(obj, list):
                for item in obj:
                    scan(item)

        scan(state)
        return candidates

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_image_url(url: str) -> bool:
        url_lower = url.lower()
        image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp")
        return (
            any(url_lower.endswith(ext) or f"{ext}?" in url_lower for ext in image_extensions)
            or "/content/image/" in url_lower
        )

    @staticmethod
    def _upgrade_image_url(url: str) -> str:
        url = re.sub(r"/(sml|sm|thumb)(_|$)", "/lrg_", url)
        url = re.sub(r"(?<=_)(sml|sm|thumb)(_|$)", "lrg", url)
        url = re.sub(r"\.(sml|sm|thumb)\.", ".lrg.", url)
        url = re.sub(r"/gr([0-9a-z]+)\\.sml", r"/gr\1", url)
        return url
