"""
Backward-compatible browser search imports.

Site-specific implementations now live in ``sites`` adapters.
"""

import logging

from sites.base import SearchResult
from sites.registry import get_adapter
from sites.sciencedirect import ScienceDirectAdapter

log = logging.getLogger("browser_search")


class ScienceDirectBrowserSearch:
    def __init__(self, engine=None):
        self.engine = engine
        self.adapter = ScienceDirectAdapter()

    def search(self, query: str, year_from: int = 2024, year_to: int = 2025, max_results: int = 200):
        return self.adapter.search(self.engine, query, year_from, year_to, max_results)

    @classmethod
    def extract_results(cls, html: str):
        return ScienceDirectAdapter().extract_results(html)


def normalize_sciencedirect_article_url(url: str) -> str:
    return get_adapter("sciencedirect").normalize_url(url)


class BrowserJournalSearcher:
    def __init__(self, engine):
        self.engine = engine

    def search(
        self,
        site: str,
        query: str,
        year_from: int = 2024,
        year_to: int = 2025,
        max_results: int = 200,
    ) -> list[SearchResult]:
        adapter = get_adapter(site)
        log.info(
            f"\n{'═'*60}\n"
            f"  浏览器检索: [{site}] {query}\n"
            f"  时间: {year_from} – {year_to}\n"
            f"  上限: {max_results} 篇\n"
            f"{'═'*60}"
        )
        return adapter.search(
            self.engine,
            query=query,
            year_from=year_from,
            year_to=year_to,
            max_results=max_results,
        )
