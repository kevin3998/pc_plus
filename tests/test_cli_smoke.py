import json
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

    assert list(JOURNAL_CONFIGS) == ["sciencedirect", "springer", "nature", "wiley"]
    assert BrowserEngine
    assert CookieManager
    assert BinaryDownloadSession
    assert StorageManager
    assert ArticleParser
    assert BrowserJournalSearcher
    assert CrawlState


def test_browser_challenge_detection_avoids_generic_verification_false_positive():
    from core.browser import BrowserEngine

    normal_article_text = (
        "This article discusses experimental verification of membrane performance "
        "and validation of the filtration model in water treatment."
    )
    assert BrowserEngine._challenge_match(normal_article_text, "ScienceDirect article") is False

    challenge_text = "Are you a robot? Please complete the security check to continue."
    assert BrowserEngine._challenge_match(challenge_text, "Security check") is True


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
    assert "springer" in result.stdout
    assert "SpringerLink" in result.stdout
    assert "nature" in result.stdout
    assert "wiley" in result.stdout


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


def test_search_accepts_springer_site():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "search",
        "--site", "springer",
        "--query", "transparent conductive oxide",
        "--max", "5",
    ])

    assert args.site == "springer"


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


def test_crawl_accepts_optional_site_override():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "crawl",
        "--site", "springer",
        "--url", "https://link.springer.com/article/10.1007/s10854-025-12345",
    ])

    assert args.site == "springer"


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
      <article><h1>Browser Article</h1><div id="body"><h2>1. Introduction</h2><p>Long enough browser supplied full text.</p></div></article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/S1", html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True
    assert (
        tmp_path
        / "articles"
        / "sciencedirect"
        / "_library"
        / "10.1016-j.example.2025.1"
        / "meta.json"
    ).exists()


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
    html = """
    <html><head>
      <meta name="citation_title" content="Options Article">
      <meta name="citation_doi" content="10.1016/j.options.2025.1">
    </head><body><article><p>Body text.</p></article></body></html>
    """

    assert parser.parse_html("https://example.test/article", html, options={
        "html": False,
        "figures": False,
        "tables": False,
        "fulltext": False,
    }) is True

    assert calls == []
    adir = tmp_path / "articles" / "sciencedirect" / "_library" / "10.1016-j.options.2025.1"
    assert (adir / "meta.json").exists()
    assert not (adir / "raw" / "article.html").exists()
    assert not (adir / "parsed" / "fulltext.md").exists()


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
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True

    fulltext = (
        tmp_path
        / "articles"
        / "sciencedirect"
        / "_library"
        / "10.1016-j.sd-body.2025.1"
        / "parsed"
        / "fulltext.md"
    ).read_text()
    assert "Perovskite solar cells are rapidly becoming" in fulltext
    assert "Transparent conductive oxides contribute" in fulltext
    assert "1. Introduction" in fulltext


def test_sciencedirect_browser_search_extracts_rendered_results():
    from sites.registry import get_adapter

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

    results = get_adapter("sciencedirect").extract_results(html)

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

    urls = main._collect_crawl_urls(types.SimpleNamespace(file=str(urls_file), url=None, site=None))

    assert urls == [
        "https://www.sciencedirect.com/science/article/pii/S123",
        "https://www.sciencedirect.com/science/article/pii/S456",
    ]


def test_crawl_url_collection_detects_mixed_site_urls(tmp_path):
    import main

    urls_file = tmp_path / "urls.txt"
    urls_file.write_text(
        "\n".join([
            "https://www.sciencedirect.com/science/article/pii/S123/pdfft?pid=main.pdf",
            "https://link.springer.com/article/10.1007/s10854-025-12345?foo=bar",
            "https://www.nature.com/articles/s41586-025-00001?proof=t",
        ]),
        encoding="utf-8",
    )

    items = main._collect_crawl_items(types.SimpleNamespace(file=str(urls_file), url=None, site=None))

    assert [(item.site, item.url) for item in items] == [
        ("sciencedirect", "https://www.sciencedirect.com/science/article/pii/S123"),
        ("springer", "https://link.springer.com/article/10.1007/s10854-025-12345"),
        ("nature", "https://www.nature.com/articles/s41586-025-00001"),
    ]


def test_site_profile_dir_is_isolated_by_site():
    import main

    assert main._profile_dir_for_site("springer").as_posix().endswith("browser_profiles/springer")


def test_unknown_crawl_urls_are_recorded_as_failed(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    item = main.CrawlItem(site="unknown", url="https://example.test/article", error="无法识别站点")

    main._do_browser_crawl_items(cm=None, items=[item], args=types.SimpleNamespace())

    storage = main.StorageManager(tmp_path / "data", site="unknown")
    run_id = storage.latest_run_id()
    assert storage.run_summary(run_id)["failed"] == 1


def test_search_result_handler_writes_default_urls_inside_run_dir(tmp_path, monkeypatch):
    import main
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    monkeypatch.chdir(tmp_path)
    storage = StorageManager(tmp_path / "data", site="sciencedirect")
    run_id = storage.create_run(site="sciencedirect", query="q", run_type="search")
    args = types.SimpleNamespace(output_urls=None, crawl=False)

    main._handle_search_results([
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S123", title="First", year="2025")
    ], cm=None, args=args, storage=storage, run_id=run_id)

    run_urls = tmp_path / "data" / "runs" / run_id / "urls.txt"
    assert run_urls.read_text() == "https://www.sciencedirect.com/science/article/pii/S123"
    assert not (tmp_path / "search_results.txt").exists()
    jsonl = tmp_path / "data" / "runs" / run_id / "search_results.jsonl"
    assert '"title": "First"' in jsonl.read_text()
    collection_dir = tmp_path / "data" / "articles" / "sciencedirect" / "searches" / "q_yany-any"
    assert (collection_dir / "urls.txt").read_text() == "https://www.sciencedirect.com/science/article/pii/S123"
    assert '"title": "First"' in (collection_dir / "articles.jsonl").read_text()
    run_json = json.loads((tmp_path / "data" / "runs" / run_id / "run.json").read_text())
    assert run_json["options"]["collection_slug"] == "q_yany-any"


def test_search_result_handler_writes_topic_collection_when_requested(tmp_path, monkeypatch):
    import main
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    monkeypatch.chdir(tmp_path)
    storage = StorageManager(tmp_path / "data", site="sciencedirect")
    run_id = storage.create_run(site="sciencedirect", query="nanofiltration", run_type="search")
    args = types.SimpleNamespace(
        output_urls=None,
        crawl=False,
        collection="nanofiltration-membrane",
        collection_title="Nanofiltration Membrane",
        site="sciencedirect",
    )

    main._handle_search_results([
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S123", title="First", year="2025")
    ], cm=None, args=args, storage=storage, run_id=run_id)

    topic_dir = tmp_path / "data" / "collections" / "nanofiltration-membrane"
    assert (topic_dir / "urls.txt").read_text() == "https://www.sciencedirect.com/science/article/pii/S123"
    assert '"title": "First"' in (topic_dir / "articles.jsonl").read_text()
    run_json = json.loads((tmp_path / "data" / "runs" / run_id / "run.json").read_text())
    assert run_json["options"]["topic_collection_slug"] == "nanofiltration-membrane"


def test_collections_command_parses_subcommands():
    import main

    parser = main.build_parser()

    show = parser.parse_args(["collections", "show", "--collection", "nanofiltration-membrane"])
    assert show.command == "collections"
    assert show.collections_command == "show"
    assert show.collection == "nanofiltration-membrane"

    imported = parser.parse_args([
        "collections", "import-search",
        "--site", "nature",
        "--search", "water-membrane_y2021-2025",
        "--collection", "nanofiltration-membrane",
    ])
    assert imported.collections_command == "import-search"
    assert imported.site == "nature"


def test_search_result_handler_writes_explicit_compat_urls_file(tmp_path, monkeypatch):
    import main
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    monkeypatch.chdir(tmp_path)
    storage = StorageManager(tmp_path / "data", site="sciencedirect")
    run_id = storage.create_run(site="sciencedirect", query="q", run_type="search")
    args = types.SimpleNamespace(output_urls="custom_urls.txt", crawl=False)

    main._handle_search_results([
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S123", title="First", year="2025")
    ], cm=None, args=args, storage=storage, run_id=run_id)

    assert (tmp_path / "custom_urls.txt").read_text() == "https://www.sciencedirect.com/science/article/pii/S123"
    assert (tmp_path / "data" / "runs" / run_id / "urls.txt").exists()


def test_crawl_file_from_run_reuses_existing_collection(tmp_path, monkeypatch):
    import main
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    storage = StorageManager(tmp_path / "data", site="sciencedirect")
    run_id = storage.create_run(site="sciencedirect", query="q", run_type="search")
    collection_id = storage.create_or_get_collection(site="sciencedirect", query="q")
    storage.attach_run_to_collection(run_id, collection_id)
    storage.save_run_urls(run_id, ["https://www.sciencedirect.com/science/article/pii/S123"])
    storage.add_collection_search_results(collection_id, [
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S123", title="First", year="2025")
    ])

    args = types.SimpleNamespace(
        file=str(tmp_path / "data" / "runs" / run_id / "urls.txt"),
        url=None,
        site="sciencedirect",
        query="",
        year_from=None,
        year_to=None,
        max=1,
        html=True,
        figures=False,
        tables=False,
        fulltext=True,
        asset_browser_fallback=True,
        max_figure_candidates_per_figure=4,
        min_image_bytes=1000,
        asset_timeout=30,
        inject_browser_cookies=False,
    )

    reused = main._collection_for_crawl(storage, args, site="sciencedirect", urls=[
        "https://www.sciencedirect.com/science/article/pii/S123"
    ])

    assert reused == collection_id
