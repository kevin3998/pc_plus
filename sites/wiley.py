"""Wiley Online Library site adapter."""

import json
import logging
import re
from urllib.parse import unquote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from core.assets import AssetCandidate
from sites.base import SearchFilters, SearchResult, SiteAdapter, first_year
from sites.wiley_journals import WileyJournal, resolve_journals

log = logging.getLogger("sites.wiley")

WILEY_PAGE_SIZE = 20
WILEY_MAX_FILTERED_EMPTY_PAGES = 10
WILEY_FULLTEXT_MIN_CHARS = 1200
WILEY_NO_ACCESS_SIGNALS = (
    "access denied",
    "access through your institution",
    "check access",
    "get access",
    "institutional login",
    "log in",
    "purchase access",
    "rent or buy",
    "sign in",
)
WILEY_BODY_HEADINGS = {
    "introduction",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "methods",
    "materials and methods",
    "experimental",
    "experimental section",
    "results and discussion",
}
WILEY_INCOMPLETE_HEADINGS = {
    "abstract",
    "references",
    "cited by",
    "supporting information",
    "related articles",
    "recommended",
    "figures",
}
WILEY_NON_ARTICLE_TITLE_PATTERNS = (
    "back cover",
    "cover picture",
    "front cover",
    "frontispiece",
    "frontispiz",
    "innenrücktitelbild",
    "innentitelbild",
    "inside cover",
    "rücktitelbild",
    "table of contents",
)
WILEY_STATUS_PREFIX_RE = re.compile(
    r"^(?:"
    r"free\s*to\s*read|freetoread|"
    r"full\s*access|fullaccess|"
    r"free\s*access|freeaccess|"
    r"open\s*access|openaccess|"
    r"oa|free|no"
    r")\s+",
    flags=re.I,
)


class WileyAdapter(SiteAdapter):
    key = "wiley"
    name = "Wiley Online Library"
    domains = ("onlinelibrary.wiley.com",)
    search_base = "https://onlinelibrary.wiley.com/action/doSearch"
    login_url = "https://onlinelibrary.wiley.com"
    article_domain = "onlinelibrary.wiley.com"
    supports_search = True
    supports_search_cursor = True

    result_selectors = (
        "a[href*='/doi/']"
    )

    def preferred_body_selectors(self) -> list[str]:
        return [
            "div.article__body",
            "section.article-section",
            "div.article-section__content",
            "div[class*='article-body']",
            "div[class*='articleBody']",
            "section[class*='article-section']",
            "div[class*='ArticleBody']",
            "main article",
            "article",
        ]

    def validate_fulltext(self, soup: BeautifulSoup, markdown: str, url: str) -> tuple[bool, str]:
        if "onlinelibrary.wiley.com" not in urlparse(url).netloc.lower():
            return True, ""
        if self._has_no_access_signal(soup) and not self._has_body_signal(soup):
            return False, "wiley_fulltext_not_available_or_no_access"
        if self._has_body_signal(soup):
            return True, ""
        md_text = re.sub(r"\s+", " ", markdown or "").strip()
        if len(md_text) < WILEY_FULLTEXT_MIN_CHARS:
            return False, "wiley_fulltext_incomplete_too_short"
        headings = self._content_headings(soup)
        if not headings or headings <= WILEY_INCOMPLETE_HEADINGS:
            return False, "wiley_fulltext_incomplete_no_body_signal"
        return True, ""

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(urljoin("https://onlinelibrary.wiley.com", url))
        host = parsed.netloc.lower()
        if host != "onlinelibrary.wiley.com" and not host.endswith(".onlinelibrary.wiley.com"):
            return ""
        match = re.match(r"^/doi/(?:full|abs|pdf|epdf)?/?(.+)$", parsed.path.rstrip("/"), flags=re.I)
        if not match:
            return ""
        doi = unquote(match.group(1)).strip("/")
        if not doi or "/" not in doi:
            return ""
        if self._is_non_article_doi(doi):
            return ""
        doi = re.sub(r"\.pdf$", "", doi, flags=re.I)
        return f"https://onlinelibrary.wiley.com/doi/full/{doi}"

    def search(
        self,
        engine,
        query: str,
        year_from: int = 2024,
        year_to: int = 2025,
        max_results: int = 200,
        filters: SearchFilters | None = None,
    ):
        selected_journals = resolve_journals(
            filters.journals if filters else [],
            filters.journal_family if filters else "",
        )
        if selected_journals:
            summary = ", ".join(
                f"{journal.key}={journal.name}({','.join(journal.doi_codes)})"
                for journal in selected_journals
            )
            log.info("  [Wiley/browser] 启用期刊过滤: %s", summary)

        page_size = WILEY_PAGE_SIZE
        requested_offset = max(0, int(getattr(filters, "start_offset", 0) or 0))
        start_offset = requested_offset - (requested_offset % page_size)
        if start_offset != requested_offset:
            log.info("  [Wiley/browser] offset=%s 非页大小倍数，回退到页起点 offset=%s", requested_offset, start_offset)

        self.last_search_next_offset = start_offset
        self.last_search_finished = False
        self.last_search_page_size = page_size

        html = engine.goto(self._search_url(query, year_from, year_to, page_size, start_offset))
        results: list[SearchResult] = []
        seen: set[str] = set()
        page = start_offset // page_size + 1
        current_offset = start_offset
        filtered_empty_pages = 0

        while len(results) < max_results:
            engine.scroll_to_bottom()
            html = engine.html() or html
            has_raw_candidates = self._has_search_result_candidates(html)
            page_results = [
                result
                for result in self.extract_results(html)
                if result.url not in seen
                and self._result_matches_selected_journals(result, selected_journals)
            ]
            if has_raw_candidates and not page_results:
                self._log_rejection_diagnostics(html)
            if not page_results and not results and not has_raw_candidates:
                engine.wait_for_user(
                    "\n当前页面没有解析到 Wiley 搜索结果。"
                    "\n如果页面停在登录、机构认证或验证流程，请在浏览器中完成后回到终端按 Enter。"
                )
                html = engine.html() or html
                has_raw_candidates = self._has_search_result_candidates(html)
                page_results = [
                    result
                    for result in self.extract_results(html)
                    if result.url not in seen
                    and self._result_matches_selected_journals(result, selected_journals)
                ]
                if has_raw_candidates and not page_results:
                    self._log_rejection_diagnostics(html)

            for result in page_results:
                seen.add(result.url)
                results.append(result)
                if len(results) >= max_results:
                    break

            if page_results:
                self.last_search_next_offset = current_offset + page_size
            log.info(
                "  [Wiley/browser] 第 %s 页 | offset=%s | 新增 %s 篇 | 共 %s 篇 | next_offset=%s",
                page,
                current_offset,
                len(page_results),
                len(results),
                self.last_search_next_offset,
            )
            if not page_results and has_raw_candidates:
                filtered_empty_pages += 1
                log.info(
                    "  [Wiley/browser] 当前页存在 DOI 候选但过滤后无论文结果，继续翻页 (%s/%s)",
                    filtered_empty_pages,
                    WILEY_MAX_FILTERED_EMPTY_PAGES,
                )
            else:
                filtered_empty_pages = 0

            if not page_results and not has_raw_candidates:
                self.last_search_finished = True
            if filtered_empty_pages >= WILEY_MAX_FILTERED_EMPTY_PAGES:
                log.info("  [Wiley/browser] 连续过滤空页达到上限，停止搜索")
                self.last_search_finished = True
            if len(results) >= max_results or self.last_search_finished:
                break

            current_offset += page_size
            page += 1
            next_url = self._search_url(query, year_from, year_to, page_size, current_offset)
            log.info("  [Wiley/browser] 使用 offset 翻页: %s", current_offset)
            html = engine.goto(next_url)

        return results[:max_results]

    def _search_url(
        self,
        query: str,
        year_from: int,
        year_to: int,
        page_size: int = WILEY_PAGE_SIZE,
        offset: int = 0,
    ) -> str:
        params = {
            "AllField": query,
            "AfterYear": str(year_from),
            "BeforeYear": str(year_to),
            "startPage": str(max(0, int(offset)) // page_size),
            "pageSize": str(page_size),
        }
        return f"{self.search_base}?{urlencode(params)}"

    @staticmethod
    def _result_matches_selected_journals(result: SearchResult, journals: list[WileyJournal]) -> bool:
        if not journals:
            return True
        doi = WileyAdapter._doi_from_url(result.url)
        doi_lower = doi.lower()
        return any(
            any(WileyAdapter._doi_matches_code(doi_lower, code) for code in journal.doi_codes)
            for journal in journals
        )

    @staticmethod
    def _doi_from_url(url: str) -> str:
        parsed = urlparse(url)
        match = re.match(r"^/doi/(?:full|abs|pdf|epdf)?/?(.+)$", parsed.path.rstrip("/"), flags=re.I)
        return unquote(match.group(1)).strip("/") if match else ""

    @staticmethod
    def _doi_matches_code(doi: str, code: str) -> bool:
        return bool(re.match(rf"^10\.1002/{re.escape(code)}(?:\.|\\d)", doi, flags=re.I))

    def extract_results(self, html: str) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[SearchResult] = []
        seen: set[str] = set()

        anchors = self._result_anchors(soup)
        for anchor in anchors:
            href = anchor.get("href", "")
            rejected, _ = self._rejection_reason(anchor)
            if rejected:
                continue
            url = self.normalize_url(urljoin("https://onlinelibrary.wiley.com", href))
            if not url or url in seen:
                continue
            card = self._result_card(anchor)
            title = self._clean_result_title(self._result_title(anchor, card))
            if not title or self._is_non_article_title(title):
                continue
            seen.add(url)
            results.append(SearchResult(
                url=url,
                title=title,
                year=first_year(card.get_text(" ", strip=True)),
            ))
        return results

    def _has_search_result_candidates(self, html: str) -> bool:
        soup = BeautifulSoup(html, "lxml")
        return any(
            href and "/doi/" in href.lower()
            for href in (anchor.get("href", "") for anchor in self._result_anchors(soup))
        )

    def _log_rejection_diagnostics(self, html: str):
        soup = BeautifulSoup(html, "lxml")
        anchors = self._result_anchors(soup)
        counts: dict[str, int] = {}
        samples: list[str] = []
        raw_count = 0
        for anchor in anchors:
            href = anchor.get("href", "")
            if not href or "/doi/" not in href.lower():
                continue
            raw_count += 1
            rejected, reason = self._rejection_reason(anchor)
            if not rejected:
                reason = "accepted_before_dedupe"
            counts[reason] = counts.get(reason, 0) + 1
            if rejected and len(samples) < 5:
                title = self._clean_result_title(self._result_title(anchor, self._result_card(anchor)))
                samples.append(f"{reason}: {title[:80]} | {href[:120]}")
        log.info("  [Wiley/browser] DOI候选诊断: raw=%s reasons=%s", raw_count, counts)
        for sample in samples:
            log.info("  [Wiley/browser] 过滤样例: %s", sample)

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
            body = soup.select_one("article, main, div.article__body, div[class*='article-body']") or soup
            figures = [body]

        for fig in figures:
            label, caption = self._figure_text(fig)
            urls: list[tuple[int, str, str]] = []

            for element in fig.select("picture source[srcset], source[srcset], img[srcset]"):
                for rank, url in enumerate(self._image_urls_from_srcset(page_url, element.get("srcset", ""))):
                    if self._is_article_image_url(url):
                        urls.append((20 + rank, url, "wiley_srcset"))

            for img in fig.select("img"):
                for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                    src = img.get(attr, "")
                    if not src or src.startswith("data:"):
                        continue
                    url = urljoin(page_url, src)
                    if self._is_article_image_url(url):
                        urls.append((40, url, "wiley_html_image"))

            for anchor in fig.select("a[href]"):
                href = anchor.get("href", "")
                url = urljoin(page_url, href)
                if self._is_article_image_url(url):
                    urls.append((30, url, "wiley_figure_asset"))

            for priority, url, source in urls[:max_per_figure]:
                candidates.extend(self._candidate_with_upgrades(url, source, label, caption, priority))

        return self._dedupe_candidates(candidates)

    @staticmethod
    def _is_non_article_href(href: str) -> bool:
        href_lower = href.lower()
        if "/doi/" not in href_lower:
            return True
        if any(token in href_lower for token in (
            "/doi/book",
            "/doi/epdf",
            "/doi/full/book",
            "/doi/full/toc",
            "/doi/pdf",
            "/doi/suppl",
            "/doi/toc",
            "/doi/references",
            "/doi/citedby",
            "#",
            "download",
            "citation",
        )):
            return True
        return False

    @staticmethod
    def _is_non_article_doi(doi: str) -> bool:
        doi_lower = doi.lower().strip("/")
        if any(token in doi_lower for token in (
            "reference",
            "references",
            "citedby",
            "suppl",
            "figure",
            "book/",
            "/book",
            "toc/",
            "/toc",
            "(issn)",
            "cover",
        )):
            return True
        if re.search(r"\b978\d{7,}", doi_lower):
            return True
        if re.search(r"\.ch\d+(?:$|[/?#])", doi_lower):
            return True
        if doi_lower.startswith(("book/", "toc/")):
            return True
        return False

    def _rejection_reason(self, anchor) -> tuple[bool, str]:
        href = anchor.get("href", "")
        if not href:
            return True, "empty_href"
        if self._is_non_article_href(href):
            return True, "non_article_href"
        url = self.normalize_url(urljoin("https://onlinelibrary.wiley.com", href))
        if not url:
            return True, "non_article_doi"
        title = self._clean_result_title(self._result_title(anchor, self._result_card(anchor)))
        if not title:
            return True, "empty_title"
        if self._is_non_article_title(title):
            return True, "non_article_title"
        return False, ""

    @staticmethod
    def _result_card(anchor):
        return (
            anchor.find_parent("li")
            or anchor.find_parent("article")
            or anchor.find_parent(attrs={"class": re.compile("search|result|item|card|issue", re.I)})
            or anchor.find_parent("div")
            or anchor
        )

    @staticmethod
    def _result_anchors(soup: BeautifulSoup) -> list:
        card_selectors = (
            "li.search__item",
            "div.search__item",
            "li[class*='search']",
            "div[class*='search-result']",
            "div[class*='SearchResult']",
            "article",
        )
        anchors = []
        for selector in card_selectors:
            for card in soup.select(selector):
                anchors.extend(card.select("a[href*='/doi/']"))
        if not anchors:
            anchors = list(soup.select("a[href*='/doi/']"))
        result = []
        seen = set()
        for anchor in anchors:
            href = anchor.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            result.append(anchor)
        return result

    @staticmethod
    def _result_title(anchor, card) -> str:
        for selector in (
            "h2",
            "h3",
            "[class*='title']",
            "[class*='Title']",
            "[class*='publication_title']",
        ):
            node = card.select_one(selector) if card else None
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return re.sub(r"\s+", " ", text)
        return re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))

    @staticmethod
    def _clean_result_title(title: str) -> str:
        title = re.sub(r"\s+", " ", title or "").strip()
        previous = None
        while title and title != previous:
            previous = title
            title = WILEY_STATUS_PREFIX_RE.sub("", title).strip()
        return title

    @staticmethod
    def _is_non_article_title(title: str) -> bool:
        lowered = title.lower()
        return any(pattern in lowered for pattern in WILEY_NON_ARTICLE_TITLE_PATTERNS)

    @staticmethod
    def _has_no_access_signal(soup: BeautifulSoup) -> bool:
        text = soup.get_text(" ", strip=True).lower()
        return any(signal in text for signal in WILEY_NO_ACCESS_SIGNALS)

    @staticmethod
    def _has_body_signal(soup: BeautifulSoup) -> bool:
        if soup.select_one("div.article__body, section.article-section, div.article-section__content"):
            return True
        headings = WileyAdapter._content_headings(soup)
        return bool(headings & WILEY_BODY_HEADINGS)

    @staticmethod
    def _content_headings(soup: BeautifulSoup) -> set[str]:
        headings = set()
        for node in soup.select("h1, h2, h3, h4, [role='heading']"):
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip().lower()
            text = re.sub(r"^\d+(?:\.\d+)*\s*", "", text)
            text = text.strip(" .:;")
            if text:
                headings.add(text)
        return headings

    @staticmethod
    def _structured_image_candidates(page_url: str, soup: BeautifulSoup) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        seen: set[str] = set()
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            for url in WileyAdapter._json_image_urls(data):
                url = urljoin(page_url, url)
                if not WileyAdapter._is_article_image_url(url) or url in seen:
                    continue
                seen.add(url)
                candidates.append(AssetCandidate(
                    type="figure",
                    url=url,
                    source="wiley_jsonld_image",
                    priority=80 + WileyAdapter._image_quality_rank(url),
                ))

        for selector, source in (
            ("meta[property='og:image']", "wiley_og_image"),
            ("meta[name='twitter:image']", "wiley_twitter_image"),
        ):
            for meta in soup.select(selector):
                url = urljoin(page_url, meta.get("content", ""))
                if not WileyAdapter._is_article_image_url(url) or url in seen:
                    continue
                seen.add(url)
                candidates.append(AssetCandidate(
                    type="figure",
                    url=url,
                    source=source,
                    priority=90 + WileyAdapter._image_quality_rank(url),
                ))
        return candidates

    @staticmethod
    def _json_image_urls(data) -> list[str]:
        urls = []
        if isinstance(data, list):
            for item in data:
                urls.extend(WileyAdapter._json_image_urls(item))
            return urls
        if not isinstance(data, dict):
            return urls
        image = data.get("image")
        if isinstance(image, str):
            urls.append(image)
        elif isinstance(image, dict):
            for key in ("url", "contentUrl"):
                if image.get(key):
                    urls.append(image[key])
        elif isinstance(image, list):
            for item in image:
                urls.extend(WileyAdapter._json_image_urls({"image": item}))
        for value in data.values():
            if isinstance(value, (dict, list)):
                urls.extend(WileyAdapter._json_image_urls(value))
        return urls

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
    def _is_article_image_url(url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if not host:
            return False
        if any(token in f"{host}{path}" for token in (
            "logo",
            "banner",
            "cover",
            "icon",
            "placeholder",
            "profile",
            "avatar",
            "advert",
            "sprite",
        )):
            return False
        if "/cms/asset/" in path:
            return True
        if "wiley" not in host and "onlinelibrary" not in host and "literatumonline" not in host:
            return False
        return bool(re.search(r"\.(?:png|jpe?g|gif|webp|tiff?)(?:$|[?#])", url, flags=re.I))

    @classmethod
    def _candidate_with_upgrades(
        cls,
        url: str,
        source: str,
        label: str,
        caption: str,
        priority: int,
    ) -> list[AssetCandidate]:
        candidates: list[AssetCandidate] = []
        for upgraded in cls._upgraded_image_urls(url):
            if not cls._is_article_image_url(upgraded):
                continue
            candidates.append(AssetCandidate(
                type="figure",
                url=upgraded,
                source="wiley_highres_upgrade" if upgraded != url else source,
                label=label,
                caption=caption,
                priority=cls._image_quality_rank(upgraded),
            ))
        candidates.append(AssetCandidate(
            type="figure",
            url=url,
            source=source,
            label=label,
            caption=caption,
            priority=priority + cls._image_quality_rank(url),
        ))
        return candidates

    @staticmethod
    def _upgraded_image_urls(url: str) -> list[str]:
        parsed = urlparse(url)
        path = parsed.path
        queryless = parsed._replace(query="")
        candidates = []

        match = re.search(r"(?P<stem>.+-(?:fig|scheme|chart|gra|blkfxd|toc)-\d{4})(?P<size>-[a-z])?(?P<ext>\.[a-z0-9]+)$", path, flags=re.I)
        if match:
            stem = match.group("stem")
            ext = match.group("ext")
            base_path = f"{stem}{ext}"
            ext_candidates = []
            for candidate_ext in (".tif", ".tiff", ".jpg", ".png"):
                ext_candidates.append(f"{stem}{candidate_ext}")
            ext_candidates.append(base_path)
            for candidate_path in ext_candidates:
                candidates.append(queryless._replace(path=candidate_path).geturl())

        if not candidates and re.search(r"-(?:m|l|s)(?=\.[a-z0-9]+$)", path, flags=re.I):
            candidates.append(queryless._replace(path=re.sub(r"-(?:m|l|s)(?=\.[a-z0-9]+$)", "", path, flags=re.I)).geturl())

        return list(dict.fromkeys(candidates))

    @staticmethod
    def _image_quality_rank(url: str) -> int:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if path.endswith((".tif", ".tiff")) and not re.search(r"-(?:m|s|thumb|thumbnail)(?=\.)", path):
            return 0
        if not re.search(r"-(?:m|s|thumb|thumbnail)(?=\.)", path) and path.endswith((".jpg", ".jpeg", ".png")):
            return 5
        if re.search(r"-l(?=\.)", path):
            return 10
        if re.search(r"-m(?=\.)", path):
            return 40
        if re.search(r"-(?:s|thumb|thumbnail)(?=\.)", path):
            return 80
        return 20

    @staticmethod
    def _figure_text(fig) -> tuple[str, str]:
        label_el = fig.select_one("[class*='label'], [class*='Label'], [class*='fig-num'], strong")
        caption_el = fig.select_one("figcaption, [class*='caption'], [class*='Caption']")
        label = label_el.get_text(" ", strip=True) if label_el else ""
        caption = caption_el.get_text(" ", strip=True) if caption_el else ""
        return label, caption

    @staticmethod
    def _candidate_key(candidate: AssetCandidate) -> str:
        parsed = urlparse(candidate.url)
        path = re.sub(r"/(?:full|large|medium|small|thumbnail|thumb)/", "/", parsed.path, flags=re.I)
        path = re.sub(r"([_-])(?:full|large|medium|small|thumb|thumbnail)(?=\.)", "", path, flags=re.I)
        path = re.sub(r"-(?:m|l|s)(?=\.)", "", path, flags=re.I)
        path = re.sub(r"\.(?:tiff?|jpe?g|png|webp)$", "", path, flags=re.I)
        return f"{parsed.netloc.lower()}{path.lower()}|{candidate.label.lower()}"

    @classmethod
    def _dedupe_candidates(cls, candidates: list[AssetCandidate]) -> list[AssetCandidate]:
        seen = set()
        result = []
        for candidate in sorted(candidates, key=lambda item: item.priority):
            key = (candidate.url, candidate.label.lower(), candidate.caption.lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result
