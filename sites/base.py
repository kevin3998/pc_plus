"""Base types for site-specific search and article rules."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from core.assets import AssetCandidate


@dataclass
class SearchResult:
    url: str
    title: str
    year: str = ""


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
    ) -> list[SearchResult]:
        raise NotImplementedError(f"{self.key} adapter search is not implemented yet")

    def extract_results(self, html: str) -> list[SearchResult]:
        raise NotImplementedError(f"{self.key} adapter result extraction is not implemented yet")

    def find_pdf_url(self, page_url: str, soup: BeautifulSoup) -> str:
        return ""

    def pdf_candidates(self, page_url: str, soup: BeautifulSoup) -> list[AssetCandidate]:
        candidates = []
        generic = self.generic_pdf_url(page_url, soup)
        if generic:
            candidates.append(AssetCandidate(type="pdf", url=generic, source="generic_pdf", priority=10))
        fallback = self.find_pdf_url(page_url, soup)
        if fallback and fallback not in {candidate.url for candidate in candidates}:
            candidates.append(AssetCandidate(type="pdf", url=fallback, source=f"{self.key}_pdf", priority=20))
        return candidates

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
    def generic_pdf_url(page_url: str, soup: BeautifulSoup) -> str:
        el = soup.find("meta", attrs={"name": "citation_pdf_url"})
        if el:
            return el.get("content", "")

        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            text = anchor.get_text(strip=True).lower()
            classes = " ".join(anchor.get("class", [])).lower()
            if ".pdf" in href.lower() or "pdf" in classes or text in {
                "pdf",
                "download pdf",
                "full pdf",
                "view pdf",
            }:
                return urljoin(page_url, href)
        return ""

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
