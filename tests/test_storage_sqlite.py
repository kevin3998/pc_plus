import json
import sqlite3
from pathlib import Path


def rows(db_path: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"select * from {table}").fetchall()
    finally:
        conn.close()


def test_storage_initializes_sqlite_catalog_and_data_dirs(tmp_path):
    from core.storage import StorageManager

    storage = StorageManager(tmp_path)

    assert storage.db_path == tmp_path / "catalog.sqlite"
    assert storage.db_path.exists()
    assert (tmp_path / "articles").is_dir()
    assert (tmp_path / "articles" / "sciencedirect" / "_library").is_dir()
    assert (tmp_path / "articles" / "sciencedirect" / "_failed").is_dir()
    assert (tmp_path / "articles" / "sciencedirect" / "searches").is_dir()
    assert (tmp_path / "runs").is_dir()
    assert {r["name"] for r in rows(storage.db_path, "sqlite_master") if r["type"] == "table"} >= {
        "runs",
        "articles",
        "run_items",
        "assets",
        "collections",
        "collection_runs",
        "collection_items",
    }


def test_article_parser_saves_article_in_site_scoped_layout_and_indexes_it(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    storage = StorageManager(tmp_path, site="sciencedirect")
    parser = ArticleParser(NoNetworkSession(), storage)
    html = """
    <html><head>
      <meta name="citation_title" content="Indexed Article">
      <meta name="citation_doi" content="10.1016/j.indexed.2025.1">
      <meta name="citation_journal_title" content="Solar Energy">
      <meta name="citation_publication_date" content="2025-01-02">
    </head><body>
      <article><h1>Indexed Article</h1><div id="body"><h2>1. Introduction</h2><p>Body text for the indexed article.</p></div></article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/SINDEX", html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    })

    article_dir = tmp_path / "articles" / "sciencedirect" / "_library" / "10.1016-j.indexed.2025.1"
    assert (article_dir / "meta.json").exists()
    assert (article_dir / "raw" / "article.html").exists()
    assert (article_dir / "parsed" / "fulltext.md").exists()

    article = rows(storage.db_path, "articles")[0]
    assert article["site"] == "sciencedirect"
    assert article["article_key"] == "10.1016-j.indexed.2025.1"
    assert article["doi"] == "10.1016/j.indexed.2025.1"
    assert article["canonical_url"] == "https://www.sciencedirect.com/science/article/pii/SINDEX"


def test_incomplete_sciencedirect_article_is_saved_to_failed_area_without_indexing(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    storage = StorageManager(tmp_path, site="sciencedirect")
    parser = ArticleParser(NoNetworkSession(), storage)
    incomplete_html = """
    <html><head>
      <meta name="citation_title" content="Incomplete Article">
      <meta name="citation_doi" content="10.1016/j.incomplete.2025.1">
    </head><body>
      <article>
        <section class="Abstracts"><h2>Abstract</h2><p>Only an abstract is visible.</p></section>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/SINCOMPLETE", incomplete_html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is False

    failed_dir = tmp_path / "articles" / "sciencedirect" / "_failed" / "10.1016-j.incomplete.2025.1"
    library_dir = tmp_path / "articles" / "sciencedirect" / "_library" / "10.1016-j.incomplete.2025.1"
    meta = json.loads((failed_dir / "meta.json").read_text(encoding="utf-8"))

    assert meta["_status"] == "failed"
    assert meta["_failure_reason"] == "fulltext_incomplete_too_short"
    assert (failed_dir / "raw" / "article.html").exists()
    assert not (failed_dir / "parsed" / "fulltext.md").exists()
    assert not library_dir.exists()
    assert rows(storage.db_path, "articles") == []

    complete_html = """
    <html><head>
      <meta name="citation_title" content="Incomplete Article">
      <meta name="citation_doi" content="10.1016/j.incomplete.2025.1">
    </head><body>
      <article><div id="body"><h2>1. Introduction</h2><p>Now full text is visible.</p></div></article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/SINCOMPLETE", complete_html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True
    assert (library_dir / "meta.json").exists()
    assert (library_dir / "parsed" / "fulltext.md").exists()
    assert len(rows(storage.db_path, "articles")) == 1


def test_incomplete_nature_article_is_saved_to_failed_area_without_indexing(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager
    from sites.registry import get_adapter

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    storage = StorageManager(tmp_path, site="nature")
    parser = ArticleParser(NoNetworkSession(), storage, adapter=get_adapter("nature"))
    incomplete_html = """
    <html><head>
      <meta name="citation_title" content="Nature Incomplete Article">
      <meta name="citation_doi" content="10.1038/s41586-025-00001">
    </head><body>
      <article>
        <section><h2>Abstract</h2><p>Only the abstract is visible.</p></section>
        <p>Access through your institution</p>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.nature.com/articles/s41586-025-00001", incomplete_html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is False

    failed_dir = tmp_path / "articles" / "nature" / "_failed" / "10.1038-s41586-025-00001"
    library_dir = tmp_path / "articles" / "nature" / "_library" / "10.1038-s41586-025-00001"
    meta = json.loads((failed_dir / "meta.json").read_text(encoding="utf-8"))

    assert meta["_status"] == "failed"
    assert meta["_failure_reason"] == "nature_fulltext_not_available_or_no_access"
    assert (failed_dir / "raw" / "article.html").exists()
    assert not library_dir.exists()
    assert rows(storage.db_path, "articles") == []

    complete_html = """
    <html><head>
      <meta name="citation_title" content="Nature Incomplete Article">
      <meta name="citation_doi" content="10.1038/s41586-025-00001">
    </head><body>
      <article>
        <div class="c-article-body">
          <section data-title="Introduction"><h2>Introduction</h2><p>Now full text is visible.</p></section>
        </div>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.nature.com/articles/s41586-025-00001", complete_html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    }) is True
    assert (library_dir / "meta.json").exists()
    assert (library_dir / "parsed" / "fulltext.md").exists()
    assert len(rows(storage.db_path, "articles")) == 1


def test_sciencedirect_abstract_is_saved_and_prepended_to_fulltext(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class NoNetworkSession:
        def download_binary(self, *args, **kwargs):
            return None

    storage = StorageManager(tmp_path, site="sciencedirect")
    parser = ArticleParser(NoNetworkSession(), storage)
    html = """
    <html><head>
      <meta name="citation_title" content="ScienceDirect Abstract Article">
      <meta name="citation_doi" content="10.1016/j.abstract.2025.1">
    </head><body>
      <article>
        <div class="Abstracts u-font-serif">
          <div id="abs0015" class="abstract author-highlights">
            <h2>Highlights</h2>
            <div>Highlights should not become the article abstract.</div>
          </div>
          <div id="abs0010" class="abstract author">
            <h2>Abstract</h2>
            <div id="sp0005">This is the ScienceDirect abstract text.</div>
          </div>
        </div>
        <div id="body">
          <section><h2>1. Introduction</h2><p>Introduction starts here.</p></section>
        </div>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/SABSTRACT", html, options={
        "html": True,
        "figures": False,
        "tables": False,
        "fulltext": True,
    })

    article_dir = tmp_path / "articles" / "sciencedirect" / "_library" / "10.1016-j.abstract.2025.1"
    abstract = (article_dir / "parsed" / "abstract.txt").read_text(encoding="utf-8")
    fulltext = (article_dir / "parsed" / "fulltext.md").read_text(encoding="utf-8")

    assert abstract == "This is the ScienceDirect abstract text."
    assert "## Abstract\n\nThis is the ScienceDirect abstract text." in fulltext
    assert fulltext.index("This is the ScienceDirect abstract text.") < fulltext.index("## 1. Introduction")
    assert "Highlights should not become the article abstract" not in abstract


def test_storage_registers_assets_with_file_metadata(tmp_path):
    from core.storage import StorageManager

    storage = StorageManager(tmp_path, site="sciencedirect")
    adir = storage.article_dir("10.1016/j.assets.2025.1")
    storage.save_meta(adir, {
        "url": "https://www.sciencedirect.com/science/article/pii/SASSET",
        "doi": "10.1016/j.assets.2025.1",
        "title": "Asset Article",
    })

    storage.save_figure(adir, 1, b"x" * 1200, ".jpg", "Caption", "Fig. 1", source_url="https://example.test/fig.jpg")
    storage.save_table(adir, 1, "<table><tr><td>A</td></tr></table>", [["A"]], "Table 1")

    assets = rows(storage.db_path, "assets")
    assert [asset["type"] for asset in assets] == ["figure", "table"]
    assert all(asset["status"] == "done" for asset in assets)
    assert all(asset["path"] for asset in assets)
    assert all(asset["sha256"] for asset in assets)
    assert assets[0]["caption"] == "Caption"
    assert assets[0]["label"] == "Fig. 1"


def test_run_state_tracks_pending_retries_and_report(tmp_path):
    from core.storage import StorageManager

    storage = StorageManager(tmp_path, site="sciencedirect")
    run_id = storage.create_run(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2024,
        year_to=2025,
        max_results=10,
        options={"source": "test"},
    )
    urls = [
        "https://www.sciencedirect.com/science/article/pii/S1",
        "https://www.sciencedirect.com/science/article/pii/S2",
    ]
    storage.add_run_items(run_id, urls)

    assert storage.pending_urls(run_id) == urls
    storage.mark_failed(run_id, urls[0], "temporary")
    storage.mark_done(run_id, urls[1])
    assert storage.pending_urls(run_id) == [urls[0]]

    item_rows = rows(storage.db_path, "run_items")
    failed = [r for r in item_rows if r["url"] == urls[0]][0]
    done = [r for r in item_rows if r["url"] == urls[1]][0]
    assert failed["status"] == "failed"
    assert failed["retries"] == 1
    assert failed["last_error"] == "temporary"
    assert done["status"] == "done"

    report = storage.generate_report(run_id)
    assert "transparent conductive oxide" in (tmp_path / "runs" / run_id / "run.json").read_text()
    assert (tmp_path / "runs" / run_id / "report.md").exists()
    assert "爬取报告" in report


def test_search_results_jsonl_is_written_for_run(tmp_path):
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    run_id = storage.create_run(site="sciencedirect", query="q", year_from=2024, year_to=2025, max_results=2)
    storage.save_search_results(run_id, [
        SearchResult(url="https://example.test/1", title="One", year="2025"),
        SearchResult(url="https://example.test/2", title="Two", year="2024"),
    ])

    path = tmp_path / "runs" / run_id / "search_results.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["title"] for line in lines] == ["One", "Two"]


def test_search_cursor_tracks_resume_offset(tmp_path):
    from core.storage import StorageManager

    storage = StorageManager(tmp_path, site="sciencedirect")
    search_key = storage.search_cursor_key(
        site="sciencedirect",
        query="membrane",
        year_from=2025,
        year_to=2025,
        options={"sort": "relevance"},
    )

    storage.upsert_search_cursor(
        site="sciencedirect",
        search_key=search_key,
        query="membrane",
        year_from=2025,
        year_to=2025,
        options={"sort": "relevance"},
        page_size=25,
        next_offset=500,
        total_seen=500,
        finished=False,
        last_run_id="run-1",
    )

    cursor = storage.get_search_cursor("sciencedirect", search_key)
    assert cursor["next_offset"] == 500
    assert cursor["total_seen"] == 500
    assert cursor["finished"] == 0
    assert cursor["last_run_id"] == "run-1"

    storage.reset_search_cursor("sciencedirect", search_key)
    assert storage.get_search_cursor("sciencedirect", search_key) is None


def test_collection_exports_search_results_and_reuses_slug(tmp_path):
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    first = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2023,
        year_to=2024,
        max_results=5,
        options={"type": "search"},
    )
    second = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2023,
        year_to=2024,
        max_results=5,
        options={"type": "search"},
    )
    assert first == second

    storage.add_collection_search_results(first, [
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S1", title="One", year="2024"),
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S2", title="Two", year="2023"),
    ])

    collection_dir = tmp_path / "articles" / "sciencedirect" / "searches" / "transparent-conductive-oxide_y2023-2024"
    assert (collection_dir / "urls.txt").read_text() == (
        "https://www.sciencedirect.com/science/article/pii/S1\n"
        "https://www.sciencedirect.com/science/article/pii/S2"
    )
    csv_text = (collection_dir / "articles.csv").read_text()
    assert "One" in csv_text
    assert "https://www.sciencedirect.com/science/article/pii/S1" in csv_text
    assert '"title": "Two"' in (collection_dir / "articles.jsonl").read_text()


def test_topic_collection_exports_cross_site_links_and_deduplicates_urls(tmp_path):
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    topic_id = storage.create_or_get_topic_collection(
        slug="nanofiltration-membrane",
        title="Nanofiltration Membrane",
    )
    same_topic = storage.create_or_get_topic_collection(slug="nanofiltration-membrane")
    assert same_topic == topic_id

    url = "https://www.sciencedirect.com/science/article/pii/STOPIC"
    storage.add_topic_collection_search_results(
        topic_id,
        site="sciencedirect",
        results=[
            SearchResult(url=url, title="Search Title", year="2025"),
            SearchResult(url=url, title="Updated Search Title", year="2025"),
        ],
        source_run_id="run-1",
        source_query="nanofiltration",
    )

    adir = storage.article_dir("10.1016/j.topic.2025.1")
    storage.save_meta(adir, {
        "url": url,
        "doi": "10.1016/j.topic.2025.1",
        "title": "Topic Article",
        "journal": "Journal of Membranes",
        "year": "2025",
    })
    storage.save_fulltext(adir, "# Topic Article\n\nBody")
    article_id = storage.last_article_id(adir)
    storage.add_article_to_topic_collection(topic_id, "sciencedirect", article_id, url=url, status="done")

    topic_dir = tmp_path / "collections" / "nanofiltration-membrane"
    assert (topic_dir / "urls.txt").read_text() == url
    csv_text = (topic_dir / "articles.csv").read_text()
    assert "Topic Article" in csv_text
    assert "sciencedirect" in csv_text
    assert "run-1" in (topic_dir / "articles.jsonl").read_text()
    link = topic_dir / "article_links" / "sciencedirect__10.1016-j.topic.2025.1"
    fallback = topic_dir / "article_links" / "sciencedirect__10.1016-j.topic.2025.1.link.json"
    assert link.exists() or fallback.exists()

    topic_rows = rows(storage.db_path, "topic_collection_items")
    assert len(topic_rows) == 1
    assert topic_rows[0]["article_id"] == article_id


def test_topic_collection_can_import_existing_search_collection(tmp_path):
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="nature")
    search_id = storage.create_or_get_collection(
        site="nature",
        query="water membrane",
        year_from=2021,
        year_to=2025,
    )
    storage.add_collection_search_results(search_id, [
        SearchResult(url="https://www.nature.com/articles/s41586-025-00001", title="Nature Result", year="2025"),
    ])

    topic_id = storage.import_search_collection_to_topic(
        topic_slug="nanofiltration-membrane",
        site="nature",
        search_slug="water-membrane_y2021-2025",
        topic_title="Nanofiltration Membrane",
    )

    topic_dir = tmp_path / "collections" / "nanofiltration-membrane"
    assert topic_id
    assert "https://www.nature.com/articles/s41586-025-00001" in (topic_dir / "urls.txt").read_text()
    assert "water membrane" in (topic_dir / "articles.jsonl").read_text()


def test_collection_exports_are_completed_after_article_metadata_is_saved(tmp_path):
    from core.storage import StorageManager
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    collection_id = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2023,
        year_to=2024,
        max_results=5,
    )
    url = "https://www.sciencedirect.com/science/article/pii/SINDEX"
    storage.add_collection_search_results(collection_id, [
        SearchResult(url=url, title="Search Title", year="2024"),
    ])
    adir = storage.article_dir("10.1016/j.indexed.2025.1")
    storage.save_meta(adir, {
        "url": url,
        "doi": "10.1016/j.indexed.2025.1",
        "title": "Indexed Article",
        "journal": "Solar Energy",
        "year": "2025",
        "authors": ["A. Author"],
    })
    article_id = storage.last_article_id(adir)
    storage.add_article_to_collection(collection_id, article_id, url=url, title="Search Title", year="2024")
    storage.refresh_collection_exports(collection_id)

    collection_dir = tmp_path / "articles" / "sciencedirect" / "searches" / "transparent-conductive-oxide_y2023-2024"
    csv_text = (collection_dir / "articles.csv").read_text()
    assert "10.1016/j.indexed.2025.1" in csv_text
    assert "Solar Energy" in csv_text
    assert "articles/sciencedirect/_library/10.1016-j.indexed.2025.1" in csv_text
    link = collection_dir / "article_links" / "10.1016-j.indexed.2025.1"
    fallback = collection_dir / "article_links" / "10.1016-j.indexed.2025.1.link.json"
    assert link.exists() or fallback.exists()
