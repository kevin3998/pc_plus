"""SpringerLink site adapter."""

import logging
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from sites.base import SearchResult, SiteAdapter, first_year

log = logging.getLogger("sites.springer")


class SpringerAdapter(SiteAdapter):
    key = "springer"
    name = "SpringerLink"
    domains = ("link.springer.com",)
    search_base = "https://link.springer.com/search"
    login_url = "https://link.springer.com"
    article_domain = "link.springer.com"
    supports_search = True

    result_selectors = (
        "a[href^='/article/'], a[href^='/chapter/'], "
        "a[href*='link.springer.com/article/'], a[href*='link.springer.com/chapter/']"
    )
    next_selectors = [
        "a[rel='next']",
        "a[aria-label='Next']",
        "a[aria-label='Next page']",
        "li.next a",
    ]

    def search(self, engine, query: str, year_from: int = 2024, year_to: int = 2025, max_results: int = 200):
        url = f"{self.search_base}?{urlencode({
            'query': query,
            'date-facet-mode': 'between',
            'facet-start-year': year_from,
            'facet-end-year': year_to,
        })}"
        html = engine.goto(url)
        results: list[SearchResult] = []
        seen: set[str] = set()
        page = 1

        while len(results) < max_results:
            engine.scroll_to_bottom()
            html = engine.html() or html
            page_results = [result for result in self.extract_results(html) if result.url not in seen]
            for result in page_results:
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    break

            log.info("  [Springer/browser] 第 %s 页 | 新增 %s 篇 | 共 %s 篇", page, len(page_results), len(results))
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
            url = self.normalize_url(urljoin("https://link.springer.com", href))
            if not url or url in seen:
                continue
            seen.add(url)
            card = anchor.find_parent("li") or anchor.find_parent("article") or anchor.find_parent("div") or anchor
            title = anchor.get_text(" ", strip=True)
            if title.lower() == "pdf":
                continue
            results.append(SearchResult(
                url=url,
                title=title,
                year=first_year(card.get_text(" ", strip=True)),
            ))
        return results

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if "link.springer.com" not in parsed.netloc:
            return url
        path = parsed.path.rstrip("/")
        if not (path.startswith("/article/") or path.startswith("/chapter/")):
            return ""
        return f"https://link.springer.com{path}"
