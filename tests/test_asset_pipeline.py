import sqlite3


def asset_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("select * from assets order by id").fetchall()
    finally:
        conn.close()


def test_asset_downloader_uses_browser_fallback_after_request_failure():
    from core.assets import AssetCandidate, AssetDownloader

    class RequestSession:
        def download_binary(self, url, referer="", timeout=30):
            return None

    class BrowserFallback:
        def download_binary(self, url, referer="", timeout=30):
            return {
                "status": 200,
                "content_type": "image/jpeg",
                "data": b"\xff\xd8" + b"x" * 2500,
            }

    downloader = AssetDownloader(
        RequestSession(),
        browser=BrowserFallback(),
        browser_fallback=True,
    )

    result = downloader.download_one(
        AssetCandidate(type="figure", url="https://example.test/figure.jpg"),
        referer="https://example.test/article",
    )

    assert result.status == "done"
    assert result.method == "browser"
    assert result.data.startswith(b"\xff\xd8")


def test_asset_downloader_uses_browser_fallback_after_invalid_image_html():
    from core.assets import AssetCandidate, AssetDownloader

    class RequestSession:
        def download_binary(self, url, referer="", timeout=30):
            return b"<!doctype html><html><body>Preparing your download</body></html>"

    class BrowserFallback:
        def download_binary(self, url, referer="", timeout=30):
            return {
                "status": 200,
                "content_type": "image/jpeg",
                "data": b"\xff\xd8" + b"x" * 2500,
            }

    downloader = AssetDownloader(
        RequestSession(),
        browser=BrowserFallback(),
        browser_fallback=True,
    )

    result = downloader.download_one(
        AssetCandidate(type="figure", url="https://example.test/figure.jpg"),
        referer="https://www.sciencedirect.com/science/article/pii/S1",
    )

    assert result.status == "done"
    assert result.method == "browser"
    assert result.data.startswith(b"\xff\xd8")


def test_parser_content_options_preserve_numeric_asset_values():
    from core.parser import _content_options

    opts = _content_options({
        "max_figure_candidates_per_figure": 4,
        "min_image_bytes": 1000,
        "asset_timeout": 30,
    })

    assert opts["max_figure_candidates_per_figure"] == 4
    assert opts["min_image_bytes"] == 1000
    assert opts["asset_timeout"] == 30


def test_storage_records_failed_asset_without_file(tmp_path):
    from core.storage import StorageManager

    storage = StorageManager(tmp_path, site="springer")
    adir = storage.article_dir("10.1007/s10854-025-12345")
    storage.save_meta(adir, {
        "url": "https://link.springer.com/article/10.1007/s10854-025-12345",
        "doi": "10.1007/s10854-025-12345",
        "title": "Springer Article",
    })

    storage.record_asset_failure(
        adir,
        asset_type="figure",
        source_url="https://example.test/figure.jpg",
        error="http_403",
        content_type="text/html",
    )

    rows = asset_rows(storage.db_path)
    assert len(rows) == 1
    assert rows[0]["type"] == "figure"
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "http_403"
    assert rows[0]["path"] == ""


def test_parser_prefers_high_resolution_figure_candidate(tmp_path):
    from core.parser import ArticleParser
    from core.storage import StorageManager

    class ImageSession:
        def download_binary(self, url, referer="", timeout=30):
            if "thumb" in url:
                return b"\xff\xd8" + b"x" * 300
            return b"\xff\xd8" + b"x" * 2500

    storage = StorageManager(tmp_path, site="sciencedirect")
    parser = ArticleParser(ImageSession(), storage)
    html = """
    <html><head>
      <meta name="citation_title" content="Figure Article">
      <meta name="citation_doi" content="10.1016/j.figure.2025.1">
    </head><body>
      <article>
        <figure>
          <figcaption>Figure caption</figcaption>
          <img src="https://img.test/thumb.jpg"
               srcset="https://img.test/thumb.jpg 200w, https://img.test/high.jpg 1200w">
        </figure>
      </article>
    </body></html>
    """

    assert parser.parse_html("https://www.sciencedirect.com/science/article/pii/SFIG", html, options={
        "html": False,
        "figures": True,
        "tables": False,
        "fulltext": False,
        "min_image_bytes": 1000,
    })

    rows = asset_rows(storage.db_path)
    assert rows[-1]["status"] == "done"
    assert rows[-1]["source_url"] == "https://img.test/high.jpg"
    assert rows[-1]["caption"] == "Figure caption"


def test_cli_asset_options_are_parsed_and_forwarded():
    import main

    parser = main.build_parser()
    args = parser.parse_args([
        "crawl",
        "--url",
        "https://link.springer.com/article/10.1007/s10854-025-12345",
        "--no-asset-browser-fallback",
        "--max-figure-candidates-per-figure",
        "2",
        "--min-image-bytes",
        "2048",
        "--asset-timeout",
        "12",
    ])

    opts = main._content_options_from_args(args)
    assert opts["asset_browser_fallback"] is False
    assert opts["max_figure_candidates_per_figure"] == 2
    assert opts["min_image_bytes"] == 2048
    assert opts["asset_timeout"] == 12
