import subprocess
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def run_main(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_project_packages_import():
    from config.settings import JOURNAL_CONFIGS
    from core.browser import BrowserEngine
    from core.cookie_manager import CookieManager
    from core.downloader import BinaryDownloadSession
    from core.parser import ArticleParser
    from core.storage import StorageManager
    from search.browser_search import BrowserJournalSearcher
    from utils.state import CrawlState

    assert list(JOURNAL_CONFIGS) == ["sciencedirect"]
    assert BrowserEngine
    assert CookieManager
    assert BinaryDownloadSession
    assert StorageManager
    assert ArticleParser
    assert BrowserJournalSearcher
    assert CrawlState


def test_main_without_args_prints_browser_mainline_help():
    result = run_main()

    assert result.returncode == 0
    assert "usage: python main.py" in result.stdout
    assert "login --site sciencedirect" in result.stdout
    assert "init --site sciencedirect" not in result.stdout


def test_main_sites_lists_only_supported_mainline_site():
    result = run_main("sites")

    assert result.returncode == 0
    assert "sciencedirect" in result.stdout
    assert "SpringerLink" not in result.stdout


def test_login_command_accepts_sciencedirect_site():
    import main

    parser = main.build_parser()
    args = parser.parse_args(["login", "--site", "sciencedirect"])

    assert args.command == "login"
    assert args.site == "sciencedirect"


def test_search_uses_browser_by_default_and_keeps_browser_flag_compatible():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "search",
        "--site", "sciencedirect",
        "--query", "transparent conductive oxide",
        "--max", "5",
    ])
    args_with_flag = parser.parse_args([
        "search",
        "--site", "sciencedirect",
        "--query", "transparent conductive oxide",
        "--max", "5",
        "--browser",
    ])

    assert args.command == "search"
    assert args_with_flag.browser is True


def test_removed_http_curl_flags_are_not_exposed():
    result = run_main("search", "--help")

    assert result.returncode == 0
    assert "--no-curl" not in result.stdout


def test_browser_mode_defaults_to_clean_profile_cookie_handling():
    from core.cookie_manager import CookieManager
    from core.browser import BrowserEngine

    manager = CookieManager(Path("/dev/null"))
    manager.cookies = {"EUID": "elsevier-login-cookie", "sd_session_id": "sd"}

    engine = BrowserEngine(manager)

    assert engine.inject_cookies is False


def test_search_accepts_fresh_browser_profile_flag():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "search",
        "--site", "sciencedirect",
        "--query", "transparent conductive oxide",
        "--fresh-browser-profile",
    ])

    assert args.fresh_browser_profile is True


def test_crawl_accepts_browser_flag_for_compatibility():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "crawl",
        "--file", "search_results.txt",
        "--browser",
    ])

    assert args.browser is True


def test_article_parser_can_parse_supplied_html(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path))
    html = """
    <html><head>
      <meta name="citation_title" content="Browser Article">
      <meta name="citation_doi" content="10.1016/j.example.2025.1">
    </head><body>
      <article><h1>Browser Article</h1><p>Long enough browser supplied full text.</p></article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/S1", html, options={
        "html": True,
        "pdf": False,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True
    assert (tmp_path / "10.1016-j.example.2025.1" / "meta.json").exists()


def test_article_parser_respects_disabled_content_options(tmp_path, monkeypatch):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            raise AssertionError("download_binary should not be called")

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path))
    calls = []
    monkeypatch.setattr(parser, "_extract_figures", lambda *args: calls.append("figures"))
    monkeypatch.setattr(parser, "_extract_tables", lambda *args: calls.append("tables"))
    monkeypatch.setattr(parser, "_try_download_pdf", lambda *args: calls.append("pdf"))
    html = """
    <html><head>
      <meta name="citation_title" content="Options Article">
      <meta name="citation_doi" content="10.1016/j.options.2025.1">
    </head><body><article><p>Body text.</p></article></body></html>
    """

    assert parser.parse_html("https://example.test/article", html, options={
        "html": False,
        "pdf": False,
        "figures": False,
        "tables": False,
        "fulltext": False,
    }) is True

    assert calls == []
    adir = tmp_path / "10.1016-j.options.2025.1"
    assert (adir / "meta.json").exists()
    assert not (adir / "article.html").exists()
    assert not (adir / "fulltext.md").exists()


def test_article_parser_extracts_sciencedirect_div_paragraphs(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    parser = ArticleParser(NoNetworkSession(), StorageManager(tmp_path))
    html = """
    <html><head>
      <meta name="citation_title" content="ScienceDirect Body">
      <meta name="citation_doi" content="10.1016/j.sd-body.2025.1">
    </head><body>
      <article>
        <nav><a>1. Introduction</a></nav>
        <div id="body" class="Body u-font-serif">
          <section id="s0005">
            <h2>1. Introduction</h2>
            <div class="u-margin-s-bottom" id="p0001">
              Perovskite solar cells are rapidly becoming a fast-evolving photovoltaic technology.
            </div>
            <div class="u-margin-s-bottom" id="p0002">
              Transparent conductive oxides contribute to optical transparency and electrical conductivity.
            </div>
          </section>
        </div>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://example.test/article", html, options={
        "html": False,
        "pdf": False,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True

    fulltext = (tmp_path / "10.1016-j.sd-body.2025.1" / "fulltext.md").read_text()
    assert "Perovskite solar cells are rapidly becoming" in fulltext
    assert "Transparent conductive oxides contribute" in fulltext
    assert "1. Introduction" in fulltext


def test_sciencedirect_browser_search_extracts_rendered_results():
    from search.browser_search import ScienceDirectBrowserSearch

    html = """
    <html><body>
      <a class="result-list-title-link" href="/science/article/pii/S123">First Article</a>
      <a class="result-list-title-link" href="/science/article/pii/S123/pdfft?pid=main.pdf">PDF Preview</a>
      <div class="result-item-content">
        <a href="/science/article/pii/S456">Second Article</a>
      </div>
      <a class="result-list-title-link" href="/science/article/pii/S123">Duplicate</a>
    </body></html>
    """

    results = ScienceDirectBrowserSearch.extract_results(html)

    assert [r.url for r in results] == [
        "https://www.sciencedirect.com/science/article/pii/S123",
        "https://www.sciencedirect.com/science/article/pii/S456",
    ]
    assert results[0].title == "First Article"


def test_crawl_url_collection_filters_sciencedirect_pdf_links(tmp_path):
    import main

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text(
        "\n".join([
            "https://www.sciencedirect.com/science/article/pii/S123",
            "https://www.sciencedirect.com/science/article/pii/S123/pdfft?pid=main.pdf",
            "https://www.sciencedirect.com/science/article/pii/S456/pdfft?pid=main.pdf",
            "https://www.sciencedirect.com/science/article/pii/S456",
        ]),
        encoding="utf-8",
    )

    urls = main._collect_crawl_urls(types.SimpleNamespace(file=str(urls_file), url=None))

    assert urls == [
        "https://www.sciencedirect.com/science/article/pii/S123",
        "https://www.sciencedirect.com/science/article/pii/S456",
    ]
