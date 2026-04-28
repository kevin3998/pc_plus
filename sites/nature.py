"""Nature site adapter."""

import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from core.assets import AssetCandidate
from sites.base import SearchResult, SiteAdapter, first_year

log = logging.getLogger("sites.nature")


class NatureAdapter(SiteAdapter):
    key = "nature"
    name = "Nature"
    domains = ("nature.com", "www.nature.com")
    search_base = "https://www.nature.com/search"
    login_url = "https://www.nature.com"
    article_domain = "www.nature.com"
    supports_search = True

    result_selectors = (
        "a[href^='/articles/'], "
        "a[href*='nature.com/articles/']"
    )
    next_selectors = [
        "a[rel='next']",
        "a[aria-label='Next']",
        "a[aria-label='Next page']",
        "li.next a",
        "a.c-pagination__link[aria-label*='Next']",
    ]

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(urljoin("https://www.nature.com", url))
        host = parsed.netloc.lower()
        if host not in {"nature.com", "www.nature.com"} and not host.endswith(".nature.com"):
            return ""
        path = parsed.path.rstrip("/")
        match = re.match(r"^/articles/([^/?#]+)", path)
        if not match:
            return ""
        article_id = match.group(1)
        if not article_id:
            return ""
        article_id = re.sub(r"\.pdf$", "", article_id, flags=re.I)
        if article_id.lower() in {"figures", "metrics", "references"}:
            return ""
        return f"https://www.nature.com/articles/{article_id}"

    def search(self, engine, query: str, year_from: int = 2024, year_to: int = 2025, max_results: int = 200):
        url = f"{self.search_base}?{urlencode({
            'q': query,
            'date_range': f'{year_from}-{year_to}',
        })}"
        html = engine.goto(url)
        results: list[SearchResult] = []
        seen: set[str] = set()
        page = 1

        while len(results) < max_results:
            engine.scroll_to_bottom()
            html = engine.html() or html
            page_results = [
                result
                for result in self.extract_results(html)
                if result.url not in seen and self._year_in_range(result.year, year_from, year_to)
            ]
            for result in page_results:
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    break

            log.info("  [Nature/browser] 第 %s 页 | 新增 %s 篇 | 共 %s 篇", page, len(page_results), len(results))
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
            if not href or self._is_non_article_href(href):
                continue
            url = self.normalize_url(urljoin("https://www.nature.com", href))
            if not url or url in seen:
                continue

            card = (
                anchor.find_parent("article")
                or anchor.find_parent("li")
                or anchor.find_parent(attrs={"class": re.compile("result|card|item|listing", re.I)})
                or anchor.find_parent("div")
                or anchor
            )
            title = self._result_title(anchor, card)
            if not title:
                continue
            seen.add(url)
            results.append(SearchResult(
                url=url,
                title=title,
                year=first_year(card.get_text(" ", strip=True)),
            ))
        return results

    def figure_candidates(self, page_url: str, soup: BeautifulSoup, max_per_figure: int = 4) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        candidates.extend(self._structured_image_candidates(page_url, soup))

        figures = soup.select(
            "figure, "
            "div[class*='figure'], "
            "div[class*='Figure'], "
            "section[class*='figure'], "
            "section[class*='Figure']"
        )
        if not figures:
            body = soup.select_one("article, main, div.c-article-body, div[class*='article-body']") or soup
            figures = [body]

        for fig in figures:
            label, caption = self._figure_text(fig)
            figure_urls: list[tuple[int, str, str]] = []

            for anchor in fig.select("a[href]"):
                href = anchor.get("href", "")
                text = anchor.get_text(" ", strip=True).lower()
                title = " ".join(str(anchor.get(attr, "")) for attr in ("title", "aria-label")).lower()
                if "full size" not in f"{text} {title}":
                    continue
                url = urljoin(page_url, href)
                if self._is_article_image_url(url, page_url):
                    figure_urls.append((0, url, "nature_fullsize_link"))

            for element in fig.select("picture source[srcset], source[srcset], img[srcset]"):
                for rank, url in enumerate(self._image_urls_from_srcset(page_url, element.get("srcset", ""))):
                    if self._is_article_image_url(url, page_url):
                        figure_urls.append((10 + rank, url, "nature_srcset"))

            for img in fig.select("img"):
                for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                    src = img.get(attr, "")
                    if not src or src.startswith("data:"):
                        continue
                    url = urljoin(page_url, src)
                    if self._is_article_image_url(url, page_url):
                        figure_urls.append((30, url, "nature_image"))

            for priority, url, source in figure_urls[:max_per_figure]:
                candidates.extend(self._candidate_with_upgrades(url, source, label, caption, priority, page_url))

        return self._dedupe_candidates(candidates)

    def _structured_image_candidates(self, page_url: str, soup: BeautifulSoup) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        seen_urls: set[str] = set()

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text() or "")
            except json.JSONDecodeError:
                continue
            for url in self._walk_image_urls(data):
                url = urljoin(page_url, url)
                if url in seen_urls or not self._is_article_image_url(url, page_url):
                    continue
                seen_urls.add(url)
                candidates.append(AssetCandidate(
                    type="figure",
                    url=url,
                    source="nature_jsonld_image",
                    label=self._label_from_image_url(url),
                    priority=self._image_quality_rank(url),
                ))

        for meta_name in ("twitter:image", "og:image"):
            el = (
                soup.find("meta", attrs={"name": meta_name})
                or soup.find("meta", attrs={"property": meta_name})
            )
            url = el.get("content", "") if el else ""
            url = urljoin(page_url, url)
            if url and url not in seen_urls and self._is_article_image_url(url, page_url):
                seen_urls.add(url)
                candidates.append(AssetCandidate(
                    type="figure",
                    url=url,
                    source=f"nature_meta_{meta_name}",
                    label=self._label_from_image_url(url),
                    priority=self._image_quality_rank(url),
                ))

        return candidates

    def _candidate_with_upgrades(
        self,
        url: str,
        source: str,
        label: str,
        caption: str,
        priority: int,
        page_url: str,
    ) -> list[AssetCandidate]:
        candidates = []
        for upgraded in self._upgraded_image_urls(url):
            if upgraded == url or not self._is_article_image_url(upgraded, page_url):
                continue
            candidates.append(AssetCandidate(
                type="figure",
                url=upgraded,
                source="nature_highres_upgrade",
                label=label,
                caption=caption,
                priority=self._image_quality_rank(upgraded),
            ))
        candidates.append(AssetCandidate(
            type="figure",
            url=url,
            source=source,
            label=label,
            caption=caption,
            priority=priority + self._image_quality_rank(url),
        ))
        return candidates

    @staticmethod
    def _walk_image_urls(obj) -> list[str]:
        urls = []
        if isinstance(obj, dict):
            image = obj.get("image")
            if isinstance(image, str):
                urls.append(image)
            elif isinstance(image, list):
                urls.extend(item for item in image if isinstance(item, str))
            elif isinstance(image, dict):
                url = image.get("url")
                if isinstance(url, str):
                    urls.append(url)
            for value in obj.values():
                urls.extend(NatureAdapter._walk_image_urls(value))
        elif isinstance(obj, list):
            for item in obj:
                urls.extend(NatureAdapter._walk_image_urls(item))
        return urls

    @staticmethod
    def _result_title(anchor, card) -> str:
        title = anchor.get_text(" ", strip=True)
        if title and len(title) > 6 and title.lower() not in {"view article", "full text", "pdf"}:
            return title
        heading = card.select_one("h1, h2, h3, [class*='title']")
        return heading.get_text(" ", strip=True) if heading else ""

    @staticmethod
    def _is_non_article_href(href: str) -> bool:
        href_lower = href.lower()
        return any(token in href_lower for token in (
            "/figures/",
            "/metrics",
            "/references",
            "/citeas",
            "#",
        ))

    @staticmethod
    def _year_in_range(year: str, year_from: int, year_to: int) -> bool:
        if not year:
            return True
        try:
            value = int(year)
        except ValueError:
            return True
        return year_from <= value <= year_to

    @staticmethod
    def _image_urls_from_srcset(page_url: str, srcset: str) -> list[str]:
        parts = []
        for raw_part in (srcset or "").split(","):
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
        return [url for _, url in sorted(parts, reverse=True)]

    @staticmethod
    def _upgrade_image_url(url: str) -> str:
        upgraded = NatureAdapter._upgraded_image_urls(url)
        return upgraded[0] if upgraded else url

    @staticmethod
    def _upgraded_image_urls(url: str) -> list[str]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        candidates = []

        if "media.springernature.com" in host:
            for replacement in ("/full/", "/lw1200/"):
                upgraded_path = re.sub(r"/(?:lw|m)\d+/", replacement, path, count=1)
                upgraded_path = re.sub(r"/w\d+h\d+/", replacement, upgraded_path, count=1)
                if upgraded_path != path:
                    candidates.append(urlunparse(parsed._replace(path=upgraded_path, query=urlencode(query))))

        if "w" in query:
            try:
                width = int(query.get("w") or 0)
            except ValueError:
                width = 0
            if width < 1800:
                highres_query = dict(query)
                highres_query["w"] = "1800"
                candidates.append(urlunparse(parsed._replace(query=urlencode(highres_query))))

        return list(dict.fromkeys(candidates))

    @staticmethod
    def _is_article_image_url(url: str, page_url: str) -> bool:
        parsed = urlparse(urljoin(page_url, url))
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        url_lower = url.lower()
        if any(token in url_lower for token in (
            "logo",
            "avatar",
            "profile",
            "placeholder",
            "icon",
            "banner",
            "advert",
            "cover",
            "/collections/",
        )):
            return False
        if "/articles/" in path and "/figures/" in path:
            return False
        image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff")
        looks_like_image = (
            any(path.endswith(ext) for ext in image_exts)
            or any(f"{ext}?" in url_lower for ext in image_exts)
            or "media.springernature.com" in host
        )
        if not looks_like_image:
            return False
        if "nature.com" in host or "springernature.com" in host:
            return True
        return False

    @staticmethod
    def _figure_text(figure) -> tuple[str, str]:
        caption_el = figure.select_one(
            "figcaption, "
            "[class*='caption'], "
            "[class*='Caption'], "
            "[class*='figure-title'], "
            "[class*='Figure-title']"
        )
        caption = caption_el.get_text(" ", strip=True) if caption_el else ""
        label = ""
        label_el = figure.select_one("[class*='label'], [class*='Label'], [class*='figure-number'], [class*='Figure-number']")
        if label_el:
            label = label_el.get_text(" ", strip=True)
        if not label:
            match = re.search(r"\b(?:Fig\.?|Figure)\s*\d+[a-z]?\b", caption, re.I)
            label = match.group(0) if match else ""
        return label, caption

    @staticmethod
    def _dedupe_candidates(candidates: list[AssetCandidate]) -> list[AssetCandidate]:
        seen = set()
        result = []
        for candidate in sorted(candidates, key=lambda item: item.priority):
            key = NatureAdapter._image_identity_key(candidate.url)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    @staticmethod
    def _label_from_image_url(url: str) -> str:
        match = re.search(r"_Fig(\d+[A-Za-z]?)_", url)
        return f"Fig. {match.group(1)}" if match else ""

    @staticmethod
    def _image_quality_rank(url: str) -> int:
        path = urlparse(url).path.lower()
        if "/full/" in path:
            return 0
        if "/lw1200/" in path:
            return 1
        if re.search(r"/(?:lw|m)685/", path):
            return 20
        if re.search(r"/(?:lw|m)\d+/", path):
            return 30
        return 10

    @staticmethod
    def _image_identity_key(url: str) -> str:
        parsed = urlparse(url)
        path = re.sub(r"/(?:full|lw\d+|m\d+|w\d+h\d+)/", "/", parsed.path.lower(), count=1)
        return path
