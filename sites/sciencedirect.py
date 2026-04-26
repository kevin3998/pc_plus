"""ScienceDirect site adapter."""

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
        url = f"{self.search_base}?{urlencode({
            'qs': query,
            'date': f'{year_from}-{year_to}',
            'show': 25,
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
                break
            page += 1

        return results[:max_results]

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
        candidates = []
        for candidate in super().figure_candidates(page_url, soup, max_per_figure=max_per_figure):
            upgraded = self._upgrade_image_url(candidate.url)
            if upgraded != candidate.url:
                candidates.append(AssetCandidate(
                    type="figure",
                    url=upgraded,
                    source="sciencedirect_highres",
                    label=candidate.label,
                    caption=candidate.caption,
                    priority=max(candidate.priority - 1, 0),
                ))
            candidates.append(candidate)
        seen = set()
        result = []
        for candidate in sorted(candidates, key=lambda item: item.priority):
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            result.append(candidate)
        return result

    @staticmethod
    def _upgrade_image_url(url: str) -> str:
        url = re.sub(r"/(sml|sm|thumb)_", "/lrg_", url)
        url = re.sub(r"\.(sml|sm|thumb)\.", ".lrg.", url)
        url = re.sub(r"/gr([0-9a-z]+)\\.sml", r"/gr\1", url)
        return url
