"""
search/browser_search.py
─────────────────────────────────────────────────────────────
Browser-backed search implementations for JS-heavy or bot-protected sites.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from core.browser import BrowserEngine

log = logging.getLogger("browser_search")


@dataclass
class SearchResult:
    url: str
    title: str
    year: str = ""


class ScienceDirectBrowserSearch:
    BASE = "https://www.sciencedirect.com/search"
    RESULT_SELECTORS = (
        "a.result-list-title-link, "
        "h2.article-title a, "
        "div.result-item-content a[href*='/science/article/']"
    )
    NEXT_SELECTORS = [
        "li.pagination-next button",
        "button[aria-label='Next page']",
        "a[aria-label='Go to next page']",
        "a[aria-label='Next page']",
    ]

    def __init__(self, engine: BrowserEngine):
        self.engine = engine

    def search(
        self,
        query: str,
        year_from: int = 2024,
        year_to: int = 2025,
        max_results: int = 200,
    ) -> list[SearchResult]:
        url = f"{self.BASE}?{urlencode({
            'qs': query,
            'date': f'{year_from}-{year_to}',
            'show': 25,
            'sortBy': 'relevance',
        })}"

        html = self.engine.goto(url)
        results: list[SearchResult] = []
        seen: set[str] = set()
        page = 1

        while len(results) < max_results:
            self.engine.scroll_to_bottom()
            html = self.engine.html() or html
            page_results = [
                result for result in self.extract_results(html)
                if result.url not in seen
            ]
            if not page_results and not results:
                self.engine.wait_for_user(
                    "\n当前页面没有解析到 ScienceDirect 搜索结果。"
                    "\n如果页面停在登录、机构认证或验证流程，请在浏览器中完成后回到终端按 Enter。"
                )
                html = self.engine.html() or html
                page_results = [
                    result for result in self.extract_results(html)
                    if result.url not in seen
                ]
            for result in page_results:
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    break

            log.info(f"  [SD/browser] 第 {page} 页 | 新增 {len(page_results)} 篇 | 共 {len(results)} 篇")
            if len(results) >= max_results or not page_results:
                break
            if not self.engine.click_next(self.NEXT_SELECTORS):
                break
            page += 1

        return results[:max_results]

    @classmethod
    def extract_results(cls, html: str) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[SearchResult] = []
        seen: set[str] = set()

        for anchor in soup.select(cls.RESULT_SELECTORS):
            href = anchor.get("href", "")
            url = normalize_sciencedirect_article_url(urljoin("https://www.sciencedirect.com", href))
            if not url:
                continue
            if url in seen:
                continue
            seen.add(url)

            card = (
                anchor.find_parent(attrs={"class": re.compile("result|Result")})
                or anchor.find_parent("li")
                or anchor.find_parent("article")
                or anchor
            )
            card_text = card.get_text(" ", strip=True)
            year_match = re.search(r"\b(20\d{2})\b", card_text)
            results.append(SearchResult(
                url=url,
                title=anchor.get_text(" ", strip=True),
                year=year_match.group(1) if year_match else "",
            ))

        return results


def normalize_sciencedirect_article_url(url: str) -> str:
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


class BrowserJournalSearcher:
    def __init__(self, engine: BrowserEngine):
        self.engine = engine

    def search(
        self,
        site: str,
        query: str,
        year_from: int = 2024,
        year_to: int = 2025,
        max_results: int = 200,
    ) -> list[SearchResult]:
        if site.lower() != "sciencedirect":
            raise ValueError("浏览器搜索目前只支持 sciencedirect")

        log.info(
            f"\n{'═'*60}\n"
            f"  浏览器检索: [{site}] {query}\n"
            f"  时间: {year_from} – {year_to}\n"
            f"  上限: {max_results} 篇\n"
            f"{'═'*60}"
        )
        return ScienceDirectBrowserSearch(self.engine).search(
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )
