def test_registry_exposes_supported_adapters_and_detects_urls():
    from sites.registry import detect_adapter, get_adapter, supported_sites

    assert supported_sites() == ["sciencedirect", "springer", "nature", "wiley"]
    assert get_adapter("sciencedirect").name == "ScienceDirect (Elsevier)"
    assert get_adapter("springer").name == "SpringerLink"
    assert get_adapter("nature").supports_search is True
    assert get_adapter("wiley").name == "Wiley Online Library"

    assert detect_adapter("https://www.sciencedirect.com/science/article/pii/S123").key == "sciencedirect"
    assert detect_adapter("https://link.springer.com/article/10.1007/s10854-025-12345").key == "springer"
    assert detect_adapter("https://www.nature.com/articles/s41586-025-00001").key == "nature"
    assert detect_adapter("https://onlinelibrary.wiley.com/doi/full/10.1002/adma.202500001").key == "wiley"


def test_registry_rejects_unknown_site_and_url():
    import pytest
    from sites.registry import detect_adapter, get_adapter

    with pytest.raises(KeyError):
        get_adapter("unknown")
    with pytest.raises(ValueError):
        detect_adapter("https://example.test/article")


def test_sciencedirect_adapter_preserves_result_extraction_and_filters_pdf_links():
    from sites.registry import get_adapter

    adapter = get_adapter("sciencedirect")
    html = """
    <html><body>
      <a class="result-list-title-link" href="/science/article/pii/S123">First Article</a>
      <a class="result-list-title-link" href="/science/article/pii/S123/pdfft?pid=main.pdf">PDF Preview</a>
      <div class="result-item-content">
        <a href="/science/article/pii/S456">Second Article</a>
      </div>
    </body></html>
    """

    results = adapter.extract_results(html)

    assert [r.url for r in results] == [
        "https://www.sciencedirect.com/science/article/pii/S123",
        "https://www.sciencedirect.com/science/article/pii/S456",
    ]
    assert adapter.normalize_url("https://www.sciencedirect.com/science/article/pii/S123/pdf") == (
        "https://www.sciencedirect.com/science/article/pii/S123"
    )


def test_sciencedirect_search_uses_offset_pagination(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from sites.base import SearchFilters
    from sites.registry import get_adapter

    class FakeEngine:
        def __init__(self):
            self.gotos = []
            self.current_html = ""

        def goto(self, url):
            self.gotos.append(url)
            offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
            start = offset + 1
            count = 25 if offset < 50 else 5
            self.current_html = "<html><body>" + "".join(
                f'<a class="result-list-title-link" href="/science/article/pii/S{i:03d}">Article {i}</a>'
                for i in range(start, start + count)
            ) + "</body></html>"
            return self.current_html

        def html(self):
            return self.current_html

        def scroll_to_bottom(self):
            return None

        def wait_for_user(self, _message):
            raise AssertionError("wait_for_user should not be called")

        def click_next(self, _selectors):
            raise AssertionError("ScienceDirect search should use offset pagination")

    adapter = get_adapter("sciencedirect")
    monkeypatch.setattr(adapter, "_wait_before_next_page", lambda _page: None)
    engine = FakeEngine()

    results = adapter.search(engine, "membrane", 2025, 2025, max_results=55)

    assert len(results) == 55
    assert [parse_qs(urlparse(url).query).get("offset", ["0"])[0] for url in engine.gotos] == [
        "0",
        "25",
        "50",
    ]


def test_sciencedirect_search_can_start_from_resume_offset(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from sites.base import SearchFilters
    from sites.registry import get_adapter

    class FakeEngine:
        def __init__(self):
            self.gotos = []
            self.current_html = ""

        def goto(self, url):
            self.gotos.append(url)
            offset = int(parse_qs(urlparse(url).query).get("offset", ["0"])[0])
            self.current_html = (
                "<html><body>"
                f'<a class="result-list-title-link" href="/science/article/pii/S{offset + 1}">Article</a>'
                "</body></html>"
            )
            return self.current_html

        def html(self):
            return self.current_html

        def scroll_to_bottom(self):
            return None

        def wait_for_user(self, _message):
            raise AssertionError("wait_for_user should not be called")

        def click_next(self, _selectors):
            raise AssertionError("ScienceDirect search should use offset pagination")

    adapter = get_adapter("sciencedirect")
    monkeypatch.setattr(adapter, "_wait_before_next_page", lambda _page: None)
    engine = FakeEngine()

    results = adapter.search(
        engine,
        "membrane",
        2025,
        2025,
        max_results=1,
        filters=SearchFilters(start_offset=500),
    )

    assert [result.url for result in results] == [
        "https://www.sciencedirect.com/science/article/pii/S501"
    ]
    assert parse_qs(urlparse(engine.gotos[0]).query)["offset"] == ["500"]
    assert adapter.last_search_next_offset == 525


def test_springer_adapter_extracts_results_and_normalizes_urls():
    from sites.registry import get_adapter

    adapter = get_adapter("springer")
    html = """
    <html><body>
      <li class="app-card-open">
        <h3><a href="/article/10.1007/s10854-025-12345?error=cookies_not_supported">
          Transparent conductive oxides
        </a></h3>
        <span>Published: 2025</span>
      </li>
      <li>
        <a href="https://link.springer.com/chapter/10.1007/978-3-031-00000-1_2">Book Chapter</a>
        <span>2024</span>
      </li>
      <li><a href="/content/pdf/10.1007/s10854-025-12345.pdf">PDF</a></li>
      <li><a href="/article/10.1007/s10854-025-12345">Duplicate</a></li>
    </body></html>
    """

    results = adapter.extract_results(html)

    assert [r.url for r in results] == [
        "https://link.springer.com/article/10.1007/s10854-025-12345",
        "https://link.springer.com/chapter/10.1007/978-3-031-00000-1_2",
    ]
    assert results[0].title == "Transparent conductive oxides"
    assert results[0].year == "2025"
    assert adapter.normalize_url("https://link.springer.com/article/10.1007/x?foo=bar#citeas") == (
        "https://link.springer.com/article/10.1007/x"
    )


def test_nature_adapter_normalizes_article_urls():
    from sites.registry import get_adapter

    adapter = get_adapter("nature")
    assert adapter.normalize_url("https://www.nature.com/articles/s41586-025-00001?proof=t") == (
        "https://www.nature.com/articles/s41586-025-00001"
    )


def test_nature_search_falls_back_to_page_parameter_when_next_click_fails():
    from urllib.parse import parse_qs, urlparse

    from sites.registry import get_adapter

    class FakeEngine:
        def __init__(self):
            self.gotos = []
            self.current_html = ""

        def goto(self, url):
            self.gotos.append(url)
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            count = 50 if page < 3 else 10
            start = (page - 1) * 50 + 1
            self.current_html = "<html><body>" + "".join(
                f"""
                <article>
                  <a href="/articles/s41586-025-{i:05d}">Nature Article {i}</a>
                  <time datetime="2025-01-01">2025</time>
                </article>
                """
                for i in range(start, start + count)
            ) + "</body></html>"
            return self.current_html

        def html(self):
            return self.current_html

        def scroll_to_bottom(self):
            return None

        def click_next(self, _selectors):
            return False

    adapter = get_adapter("nature")
    engine = FakeEngine()
    results = adapter.search(engine=engine, query="membrane", year_from=2021, year_to=2025, max_results=110)

    assert len(results) == 110
    assert [parse_qs(urlparse(url).query).get("page", ["1"])[0] for url in engine.gotos] == [
        "1",
        "2",
        "3",
    ]


def test_nature_search_can_resume_from_offset_with_journal_filter():
    from urllib.parse import parse_qs, urlparse

    from sites.base import SearchFilters
    from sites.registry import get_adapter

    class FakeEngine:
        def __init__(self):
            self.gotos = []
            self.current_html = ""

        def goto(self, url):
            self.gotos.append(url)
            parsed = urlparse(url)
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            start = (page - 1) * 50 + 1
            self.current_html = "<html><body>" + "".join(
                f"""
                <article>
                  <a href="/articles/s41467-025-{i:05d}">Nature Communications Article {i}</a>
                  <time datetime="2025-01-01">2025</time>
                </article>
                """
                for i in range(start, start + 50)
            ) + "</body></html>"
            return self.current_html

        def html(self):
            return self.current_html

        def scroll_to_bottom(self):
            return None

        def click_next(self, _selectors):
            return False

    adapter = get_adapter("nature")
    engine = FakeEngine()
    results = adapter.search(
        engine=engine,
        query="membrane",
        year_from=2021,
        year_to=2025,
        max_results=50,
        filters=SearchFilters(start_offset=100, journals=["nc"]),
    )

    first_query = parse_qs(urlparse(engine.gotos[0]).query)
    assert first_query["page"] == ["3"]
    assert first_query["journal"] == ["ncomms"]
    assert len(results) == 50
    assert results[0].url == "https://www.nature.com/articles/s41467-025-00101"
    assert adapter.last_search_next_offset == 150
    assert adapter.last_search_page_size == 50


def test_wiley_adapter_extracts_results_and_filters_non_articles():
    from sites.registry import get_adapter

    adapter = get_adapter("wiley")
    html = """
    <html><body>
      <li class="search__item">
        <h3><a href="/doi/full/10.1002/adma.202500001">Advanced material article</a></h3>
        <span>2025</span>
      </li>
      <li class="search__item">
        <h3><a href="/doi/pdf/10.1002/adma.202500001">PDF</a></h3>
      </li>
      <li class="search__item">
        <h3><a href="/doi/full/10.1002/adma.202500002">Back Cover</a></h3>
      </li>
    </body></html>
    """

    results = adapter.extract_results(html)

    assert [result.url for result in results] == [
        "https://onlinelibrary.wiley.com/doi/full/10.1002/adma.202500001"
    ]
    assert results[0].title == "Advanced material article"
    assert adapter.normalize_url("https://onlinelibrary.wiley.com/doi/abs/10.1002/adfm.202500003") == (
        "https://onlinelibrary.wiley.com/doi/full/10.1002/adfm.202500003"
    )
