# Nature Adapter Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Nature adapter from "implemented but lightly validated" to a stable production adapter for search, article parsing, figures, and documentation.

**Architecture:** Keep `sites/nature.py` as the site-specific adapter and avoid changing the registry contract. Add Nature-specific validation hooks through small adapter methods consumed by `core/parser.py`, mirroring the current ScienceDirect-only validation without hardcoding every site in parser logic.

**Tech Stack:** Python, BeautifulSoup, Patchright browser engine, pytest, SQLite-backed storage.

---

## Current Gaps

1. README says Nature search is incomplete, while `NatureAdapter.search()` already exists. The documentation is stale and can mislead users.
2. Search extraction is only indirectly tested through URL normalization. There is no fixture proving that Nature search result cards produce correct titles, years, de-duplicated URLs, and filtered non-article links.
3. `core/parser.py` validates incomplete full text only for ScienceDirect. Nature pages can currently pass even when the saved body is only abstract, references, recommendations, figures, or an access/login page.
4. Nature body extraction relies on generic selectors. It should explicitly prefer Nature article body containers such as `div.c-article-body`, `section[data-title]`, and related SpringerNature classes.
5. Figure extraction is relatively mature in code, but tests do not lock behavior for JSON-LD images, full-size links, srcset ranking, `media.springernature.com` high-resolution upgrades, and de-duplication by normalized image path.
6. Browser article wait logic has special ScienceDirect behavior only. Nature crawl may parse too early on slow pages because `BrowserEngine._wait_for_article_content()` only scrolls for non-ScienceDirect sites.
7. Nature-specific access/no-access signals are not modeled, so failures are harder to distinguish from parser bugs.

## File Structure

- Modify `sites/nature.py`: improve result filtering, expose Nature body and access-signal helpers, strengthen image URL logic if tests reveal gaps.
- Modify `core/parser.py`: add adapter-driven validation/body selection extension points while preserving current ScienceDirect behavior.
- Modify `core/browser.py`: add generic adapter-neutral wait behavior or Nature-specific article content readiness check.
- Modify `tests/test_site_adapters.py`: add Nature search extraction and image candidate tests.
- Modify `tests/test_cli_smoke.py` or add `tests/test_nature_parser.py`: add Nature full-text success/failure parser tests.
- Modify `README.md`: align Nature capability description with actual behavior and document remaining limitations.

---

### Task 1: Lock Nature Search Extraction Behavior

**Files:**
- Modify: `tests/test_site_adapters.py`
- Modify: `sites/nature.py`

- [ ] **Step 1: Add failing search extraction test**

Add a test that includes a realistic Nature search card, a duplicate URL, a figures link, and an out-of-card link.

```python
def test_nature_adapter_extracts_search_results_and_filters_noise():
    from sites.registry import get_adapter

    adapter = get_adapter("nature")
    html = """
    <html><body>
      <article class="c-card">
        <h3><a href="/articles/s41586-025-00001">A stable Nature article title</a></h3>
        <time datetime="2025-02-12">12 February 2025</time>
      </article>
      <article class="c-card">
        <h3><a href="/articles/s41586-025-00002?utm_source=search">Second Nature article title</a></h3>
        <span>Published: 2024</span>
      </article>
      <a href="/articles/s41586-025-00001">Duplicate title</a>
      <a href="/articles/s41586-025-00001/figures/1">Fig. 1</a>
      <a href="/articles/s41586-025-00001/metrics">Metrics</a>
      <a href="/articles/s41586-025-00001.pdf">PDF</a>
    </body></html>
    """

    results = adapter.extract_results(html)

    assert [result.url for result in results] == [
        "https://www.nature.com/articles/s41586-025-00001",
        "https://www.nature.com/articles/s41586-025-00002",
    ]
    assert results[0].title == "A stable Nature article title"
    assert results[0].year == "2025"
    assert results[1].year == "2024"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_site_adapters.py::test_nature_adapter_extracts_search_results_and_filters_noise -q
```

Expected: fail if PDF or duplicate/noise filtering is incomplete.

- [ ] **Step 3: Harden Nature URL filtering**

Update `NatureAdapter._is_non_article_href()` to reject PDF paths and explicit article subresources.

```python
@staticmethod
def _is_non_article_href(href: str) -> bool:
    href_lower = href.lower()
    return (
        href_lower.endswith(".pdf")
        or any(token in href_lower for token in (
            "/figures/",
            "/metrics",
            "/references",
            "/citeas",
            "#",
        ))
    )
```

- [ ] **Step 4: Verify**

Run:

```bash
pytest tests/test_site_adapters.py::test_nature_adapter_extracts_search_results_and_filters_noise -q
```

Expected: pass.

---

### Task 2: Add Nature Full-Text Validation

**Files:**
- Modify: `core/parser.py`
- Modify or create: `tests/test_nature_parser.py`

- [ ] **Step 1: Add failing parser tests**

Create `tests/test_nature_parser.py` with one complete Nature page and one access-only page.

```python
def test_nature_parser_accepts_article_body(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager
    from sites.registry import get_adapter

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path, site="nature"), adapter=get_adapter("nature"))
    html = """
    <html><head>
      <meta name="citation_title" content="Nature Body Article">
      <meta name="citation_doi" content="10.1038/s41586-025-00001-1">
    </head><body>
      <article>
        <div class="c-article-body">
          <h2>Introduction</h2>
          <p>Nature article body paragraph with enough signal to be accepted.</p>
          <h2>Results</h2>
          <p>Results paragraph that should be saved into markdown output.</p>
        </div>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.nature.com/articles/s41586-025-00001", html, options={
        "html": False,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True

    fulltext = (
        tmp_path / "articles" / "nature" / "_library" / "10.1038-s41586-025-00001-1" /
        "parsed" / "fulltext.md"
    ).read_text()
    assert "## Introduction" in fulltext
    assert "Results paragraph" in fulltext


def test_nature_parser_rejects_access_only_page(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager
    from sites.registry import get_adapter

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path, site="nature"), adapter=get_adapter("nature"))
    html = """
    <html><head>
      <meta name="citation_title" content="Nature Access Page">
      <meta name="citation_doi" content="10.1038/s41586-025-00002-1">
    </head><body>
      <article>
        <h1>Nature Access Page</h1>
        <p>Access through your institution</p>
        <p>Subscribe to journal</p>
        <section><h2>References</h2><p>Reference list content only.</p></section>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.nature.com/articles/s41586-025-00002", html, options={
        "html": False,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_nature_parser.py -q
```

Expected: the access-only test fails because non-ScienceDirect full text is currently always accepted.

- [ ] **Step 3: Add adapter-driven validation hook**

Modify `ArticleParser._validate_fulltext()` to delegate to the adapter when available before falling back to the existing ScienceDirect branch.

```python
def _validate_fulltext(self, soup: BeautifulSoup, markdown: str, url: str) -> tuple[bool, str]:
    if self.adapter and hasattr(self.adapter, "validate_fulltext"):
        return self.adapter.validate_fulltext(soup, markdown, url)

    if not _is_sciencedirect_url(url):
        return True, ""

    page_text = soup.get_text(" ", strip=True).lower()
    has_no_access = any(signal in page_text for signal in NO_ACCESS_SIGNALS)
    if has_no_access and not _has_sciencedirect_body_signal(soup):
        return False, "fulltext_not_available_or_no_access"

    if _has_sciencedirect_body_signal(soup):
        return True, ""

    md_text = re.sub(r"\s+", " ", markdown or "").strip()
    if len(md_text) < SCIENCEDIRECT_FULLTEXT_MIN_CHARS:
        return False, "fulltext_incomplete_too_short"
    return False, "fulltext_incomplete_no_body_headings"
```

- [ ] **Step 4: Implement Nature validation**

Add to `NatureAdapter`:

```python
NATURE_ACCESS_SIGNALS = (
    "access through your institution",
    "subscribe to journal",
    "rent or buy",
    "get access",
    "sign in",
)

NATURE_CONTENT_HEADINGS = (
    "introduction",
    "results",
    "discussion",
    "methods",
    "materials and methods",
    "conclusion",
)

def validate_fulltext(self, soup: BeautifulSoup, markdown: str, url: str) -> tuple[bool, str]:
    page_text = soup.get_text(" ", strip=True).lower()
    has_body = self._has_article_body_signal(soup)
    has_no_access = any(signal in page_text for signal in self.NATURE_ACCESS_SIGNALS)
    if has_no_access and not has_body:
        return False, "nature_fulltext_not_available_or_no_access"
    if has_body:
        return True, ""
    md_text = re.sub(r"\s+", " ", markdown or "").strip()
    if len(md_text) < 1500:
        return False, "nature_fulltext_incomplete_too_short"
    return False, "nature_fulltext_incomplete_no_body_signal"

def _has_article_body_signal(self, soup: BeautifulSoup) -> bool:
    if soup.select_one("div.c-article-body, section.c-article-section, div[class*='article-body']"):
        return True
    headings = [
        re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip().lower()
        for el in soup.select("article h2, article h3, div.c-article-body h2, div.c-article-body h3")
    ]
    return any(any(token in heading for token in self.NATURE_CONTENT_HEADINGS) for heading in headings)
```

- [ ] **Step 5: Verify**

Run:

```bash
pytest tests/test_nature_parser.py -q
pytest tests/test_cli_smoke.py tests/test_site_adapters.py -q
```

Expected: all pass.

---

### Task 3: Prefer Nature Article Body Containers

**Files:**
- Modify: `core/parser.py`
- Modify: `sites/nature.py`
- Test: `tests/test_nature_parser.py`

- [ ] **Step 1: Add failing test for noisy page**

Add a Nature page where `main` contains navigation and recommendations before the article body.

```python
def test_nature_parser_prefers_nature_article_body_over_main_noise(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager
    from sites.registry import get_adapter

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path, site="nature"), adapter=get_adapter("nature"))
    html = """
    <html><head>
      <meta name="citation_title" content="Noisy Nature Article">
      <meta name="citation_doi" content="10.1038/s41586-025-00003-1">
    </head><body>
      <main>
        <section class="recommended"><h2>Recommended articles</h2><p>Noise item</p></section>
        <article>
          <div class="c-article-body">
            <h2>Introduction</h2>
            <p>The true Nature article body should be selected first.</p>
            <h2>Discussion</h2>
            <p>The discussion paragraph should remain.</p>
          </div>
        </article>
      </main>
    </body></html>
    """

    assert parser.parse_html("https://www.nature.com/articles/s41586-025-00003", html, options={
        "html": False,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True

    fulltext = (
        tmp_path / "articles" / "nature" / "_library" / "10.1038-s41586-025-00003-1" /
        "parsed" / "fulltext.md"
    ).read_text()
    assert "The true Nature article body" in fulltext
    assert "Noise item" not in fulltext
```

- [ ] **Step 2: Add adapter body selector hook**

In `ArticleParser._extract_fulltext()`, before iterating `BODY_SELECTORS`, ask the adapter for preferred body selectors.

```python
body_selectors = []
if self.adapter and hasattr(self.adapter, "body_selectors"):
    body_selectors.extend(self.adapter.body_selectors())
body_selectors.extend(self.BODY_SELECTORS)

for sel in body_selectors:
    body = soup.select_one(sel)
    if body:
        break
```

- [ ] **Step 3: Add Nature body selectors**

Add to `NatureAdapter`:

```python
def body_selectors(self) -> list[str]:
    return [
        "div.c-article-body",
        "section.c-article-section",
        "div[class*='article-body']",
        "article div[class*='body']",
    ]
```

- [ ] **Step 4: Verify**

Run:

```bash
pytest tests/test_nature_parser.py -q
```

Expected: pass.

---

### Task 4: Lock Nature Figure Candidate Quality

**Files:**
- Modify: `tests/test_site_adapters.py`
- Modify: `sites/nature.py`

- [ ] **Step 1: Add figure candidate tests**

Add tests for JSON-LD images, full-size links, srcset sorting, high-resolution upgrades, and de-duplication.

```python
def test_nature_adapter_prefers_high_resolution_figure_candidates():
    from bs4 import BeautifulSoup
    from sites.registry import get_adapter

    adapter = get_adapter("nature")
    html = """
    <html><head>
      <script type="application/ld+json">
        {"@type": "ScholarlyArticle", "image": "https://media.springernature.com/lw685/s41586_025_00001_Fig1_HTML.jpg"}
      </script>
      <meta property="og:image" content="https://media.springernature.com/lw685/s41586_025_00001_Fig1_HTML.jpg" />
    </head><body>
      <figure>
        <figcaption>Fig. 1 Main result</figcaption>
        <a href="https://media.springernature.com/full/s41586_025_00001_Fig1_HTML.jpg">Full size image</a>
        <picture>
          <source srcset="https://media.springernature.com/lw400/s41586_025_00001_Fig1_HTML.jpg 400w,
                          https://media.springernature.com/lw1200/s41586_025_00001_Fig1_HTML.jpg 1200w">
          <img src="https://media.springernature.com/lw685/s41586_025_00001_Fig1_HTML.jpg">
        </picture>
      </figure>
    </body></html>
    """

    candidates = adapter.figure_candidates(
        "https://www.nature.com/articles/s41586-025-00001",
        BeautifulSoup(html, "lxml"),
    )

    assert candidates[0].url == "https://media.springernature.com/full/s41586_025_00001_Fig1_HTML.jpg"
    assert candidates[0].source in {"nature_fullsize_link", "nature_highres_upgrade", "nature_jsonld_image"}
    assert len([candidate for candidate in candidates if "Fig1" in candidate.url]) == 1
```

- [ ] **Step 2: Run test**

Run:

```bash
pytest tests/test_site_adapters.py::test_nature_adapter_prefers_high_resolution_figure_candidates -q
```

Expected: pass or expose ordering/de-duplication gaps.

- [ ] **Step 3: Fix only exposed gaps**

If the test fails due to duplicate identities, adjust `_image_identity_key()` so query width and `/full|lw|m|w*h/` variants collapse to the same identity.

```python
@staticmethod
def _image_identity_key(url: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"/(?:full|lw\d+|m\d+|w\d+h\d+)/", "/", parsed.path.lower(), count=1)
    return path
```

- [ ] **Step 4: Verify**

Run:

```bash
pytest tests/test_site_adapters.py -q
```

Expected: pass.

---

### Task 5: Improve Nature Browser Readiness

**Files:**
- Modify: `core/browser.py`
- Test: existing tests plus manual smoke command

- [ ] **Step 1: Refactor article readiness detection**

Make `_wait_for_article_content()` use a generic readiness script for non-ScienceDirect pages, rather than only scrolling once.

```python
def _wait_for_article_content(self, url: str):
    host = urlparse(url).netloc.lower()
    if not self._page:
        return
    if "sciencedirect.com" in host:
        self._wait_for_sciencedirect_article_content()
        return
    if "nature.com" in host:
        self._wait_for_generic_article_content(
            body_selector="div.c-article-body, section.c-article-section, article",
            no_access_pattern="access through your institution|subscribe to journal|rent or buy|get access|sign in",
        )
        return
    self.scroll_to_bottom()
```

- [ ] **Step 2: Add helper**

```python
def _wait_for_generic_article_content(self, body_selector: str, no_access_pattern: str = ""):
    deadline = time.time() + ARTICLE_WAIT_SECONDS
    last_status = {}
    while time.time() < deadline:
        self.scroll_to_bottom()
        last_status = self._generic_article_status(body_selector, no_access_pattern)
        if last_status.get("has_body") or last_status.get("no_access"):
            break
        time.sleep(ARTICLE_WAIT_POLL_SECONDS)
    self._sync_cookies_out()
```

- [ ] **Step 3: Add status helper**

```python
def _generic_article_status(self, body_selector: str, no_access_pattern: str = "") -> dict:
    try:
        return self._page.evaluate(
            """
            ({bodySelector, noAccessPattern}) => {
              const text = (document.body && document.body.innerText || "").replace(/\\s+/g, " ");
              const hasBodySelector = !!document.querySelector(bodySelector);
              const headings = Array.from(document.querySelectorAll("article h2, article h3"))
                .map(el => (el.innerText || "").replace(/\\s+/g, " ").trim())
                .filter(Boolean);
              const hasContentHeading = headings.some(h =>
                /\\b(introduction|results|discussion|methods?|materials? and methods?|conclusion)\\b/i.test(h)
              );
              const noAccess = noAccessPattern ? new RegExp(noAccessPattern, "i").test(text) : false;
              return {
                chars: text.length,
                headings: headings.length,
                has_body: hasBodySelector || hasContentHeading || text.length > 8000,
                no_access: noAccess && !hasBodySelector && !hasContentHeading,
              };
            }
            """,
            {"bodySelector": body_selector, "noAccessPattern": no_access_pattern},
        )
    except Exception as exc:
        log.debug("generic article status failed: %s", exc)
        return {}
```

- [ ] **Step 4: Keep current ScienceDirect behavior**

Rename the existing ScienceDirect block to `_wait_for_sciencedirect_article_content()` without changing its logic.

- [ ] **Step 5: Verify**

Run:

```bash
pytest -q
```

Expected: all tests pass.

Manual smoke command when network/login context is available:

```bash
python main.py crawl --site nature --url "https://www.nature.com/articles/s41586-025-00001" --no-figures --no-tables
```

Expected: article either saves complete full text or fails with a clear no-access/incomplete reason.

---

### Task 6: Align Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update supported site list**

Replace the Nature bullet with:

```markdown
- `nature`：支持站点适配、检索、爬取、正文解析和 SpringerNature 图片候选提取；需继续用真实样本扩展回归覆盖。
```

- [ ] **Step 2: Add Nature examples**

Add after Springer login example:

```bash
python main.py login --site nature
```

Add a Nature search example:

```bash
python main.py search \
  --site nature \
  --query "perovskite solar cells" \
  --year-from 2024 \
  --year-to 2025 \
  --max 20
```

- [ ] **Step 3: Correct unsupported flags**

Remove or clearly mark `--pdf`, `--no-pdf`, `--supplementary`, and `--no-supplementary` as planned capabilities unless those CLI flags are implemented in the same branch.

- [ ] **Step 4: Verify docs and tests**

Run:

```bash
pytest -q
python main.py sites
python main.py search --help
```

Expected: tests pass; README capability statements match actual CLI flags.

---

## Recommended Execution Order

1. Task 1: search extraction tests and minor filtering.
2. Task 2: full-text validation hook and Nature validation.
3. Task 3: Nature body selector preference.
4. Task 4: figure candidate regression tests.
5. Task 5: browser readiness hardening.
6. Task 6: README cleanup.

This order keeps behavior locked by tests before broadening parser/browser internals.
