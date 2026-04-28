def test_registry_exposes_supported_adapters_and_detects_urls():
    from sites.registry import detect_adapter, get_adapter, supported_sites

    assert supported_sites() == ["sciencedirect", "springer", "nature"]
    assert get_adapter("sciencedirect").name == "ScienceDirect (Elsevier)"
    assert get_adapter("springer").name == "SpringerLink"
    assert get_adapter("nature").supports_search is True

    assert detect_adapter("https://www.sciencedirect.com/science/article/pii/S123").key == "sciencedirect"
    assert detect_adapter("https://link.springer.com/article/10.1007/s10854-025-12345").key == "springer"
    assert detect_adapter("https://www.nature.com/articles/s41586-025-00001").key == "nature"


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
