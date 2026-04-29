"""Base types for site-specific search and article rules."""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from core.assets import AssetCandidate


@dataclass
class SearchResult:
    url: str
    title: str
    year: str = ""


@dataclass
class SearchFilters:
    journals: list[str] = field(default_factory=list)
    journal_family: str = ""
    sort: str = "relevance"
    start_offset: int = 0


class SiteAdapter:
    key = ""
    name = ""
    domains: tuple[str, ...] = ()
    search_base = ""
    login_url = ""
    article_domain = ""
    supports_search = True
    requires_login = False

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"

    def matches_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(host == domain or host.endswith(f".{domain}") for domain in self.domains)

    def search(
        self,
        engine,
        query: str,
        year_from: int = 2024,
        year_to: int = 2025,
        max_results: int = 200,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError(f"{self.key} adapter search is not implemented yet")

    def extract_results(self, html: str) -> list[SearchResult]:
        raise NotImplementedError(f"{self.key} adapter result extraction is not implemented yet")

    def figure_candidates(self, page_url: str, soup: BeautifulSoup, max_per_figure: int = 4) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        figures = soup.select("figure, div[class*='figure'], div[class*='fig-']")
        if not figures:
            body = soup.select_one("article, main, div[class*='article']") or soup
            figures = [body]

        for fig in figures:
            caption_el = fig.select_one("figcaption, [class*='caption'], [class*='legend'], [class*='fig-caption']")
            label_el = fig.select_one("[class*='label'], [class*='fig-num']")
            caption = caption_el.get_text(strip=True) if caption_el else ""
            label = label_el.get_text(strip=True) if label_el else ""
            for img in fig.select("img, source"):
                urls = self._image_urls_from_element(page_url, img)
                for priority, url in enumerate(urls[:max_per_figure], 1):
                    candidates.append(AssetCandidate(
                        type="figure",
                        url=url,
                        source="html_image",
                        label=label,
                        caption=caption,
                        priority=priority,
                    ))
        return _dedupe_candidates(candidates)

    @staticmethod
    def _image_urls_from_element(page_url: str, element) -> list[str]:
        urls = []
        srcset = element.get("srcset", "")
        if srcset:
            parts = []
            for raw_part in srcset.split(","):
                pieces = raw_part.strip().split()
                if not pieces:
                    continue
                width = 0
                if len(pieces) > 1 and pieces[1].endswith("w"):
                    try:
                        width = int(pieces[1][:-1])
                    except ValueError:
                        width = 0
                parts.append((width, urljoin(page_url, pieces[0])))
            urls.extend(url for _, url in sorted(parts, reverse=True))

        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            src = element.get(attr, "")
            if src and not src.startswith("data:"):
                urls.append(urljoin(page_url, src))
        return list(dict.fromkeys(urls))


def first_year(text: str) -> str:
    match = re.search(r"\b(20\d{2})\b", text or "")
    return match.group(1) if match else ""


def _dedupe_candidates(candidates: list[AssetCandidate]) -> list[AssetCandidate]:
    seen = set()
    result = []
    for candidate in sorted(candidates, key=lambda item: item.priority):
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result
