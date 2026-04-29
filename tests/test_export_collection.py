import csv
import json
from pathlib import Path


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_export_collection_copies_complete_articles_and_writes_reports(tmp_path):
    from core.storage import StorageManager
    from scripts.export_collection import export_collection
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    collection_id = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2023,
        year_to=2024,
        max_results=2,
    )
    complete_url = "https://www.sciencedirect.com/science/article/pii/SCOMPLETE"
    missing_url = "https://www.sciencedirect.com/science/article/pii/SMISSING"
    storage.add_collection_search_results(collection_id, [
        SearchResult(url=complete_url, title="Complete Search Result", year="2024"),
        SearchResult(url=missing_url, title="Missing Search Result", year="2023"),
    ])

    article_dir = storage.article_dir("10.1016/j.complete.2024.1")
    storage.save_meta(article_dir, {
        "url": complete_url,
        "doi": "10.1016/j.complete.2024.1",
        "title": "Complete Article",
        "journal": "Solar Energy",
        "year": "2024",
    })
    storage.save_fulltext(article_dir, "# Complete Article\n\nBody")
    article_id = storage.last_article_id(article_dir)
    storage.add_article_to_collection(collection_id, article_id, url=complete_url)

    out_dir = tmp_path / "exported"
    result = export_collection(
        db_path=tmp_path / "catalog.sqlite",
        base_dir=tmp_path,
        site="sciencedirect",
        collection_slug="transparent-conductive-oxide_y2023-2024",
        out_dir=out_dir,
    )

    assert result.exported == 1
    assert result.missing == 1
    exported_article = out_dir / "articles" / "001__10.1016-j.complete.2024.1"
    assert (exported_article / "meta.json").exists()
    assert (exported_article / "parsed" / "fulltext.md").exists()

    manifest_rows = _read_csv(out_dir / "manifest.csv")
    assert manifest_rows[0]["article_key"] == "10.1016-j.complete.2024.1"
    assert manifest_rows[0]["title"] == "Complete Article"
    assert manifest_rows[0]["figure_count"] == "0"

    missing_rows = _read_csv(out_dir / "missing.csv")
    assert missing_rows[0]["url"] == missing_url
    assert missing_rows[0]["reason"] == "no_article_id"

    summary = json.loads((out_dir / "export_summary.json").read_text(encoding="utf-8"))
    assert summary["exported"] == 1
    assert summary["missing"] == 1


def test_export_collection_dry_run_does_not_create_output(tmp_path):
    from core.storage import StorageManager
    from scripts.export_collection import export_collection
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    collection_id = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2023,
        year_to=2024,
        max_results=1,
    )
    storage.add_collection_search_results(collection_id, [
        SearchResult(url="https://www.sciencedirect.com/science/article/pii/S1", title="One", year="2024"),
    ])

    out_dir = tmp_path / "dry-run-export"
    result = export_collection(
        db_path=tmp_path / "catalog.sqlite",
        base_dir=tmp_path,
        site="sciencedirect",
        collection_slug="transparent-conductive-oxide_y2023-2024",
        out_dir=out_dir,
        dry_run=True,
    )

    assert result.total_items == 1
    assert result.exported == 0
    assert result.missing == 1
    assert not out_dir.exists()


def test_export_collection_merges_multiple_collections_and_deduplicates(tmp_path):
    from core.storage import StorageManager
    from scripts.export_collection import export_collections
    from search.browser_search import SearchResult

    storage = StorageManager(tmp_path, site="sciencedirect")
    first = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2020,
        year_to=2021,
    )
    second = storage.create_or_get_collection(
        site="sciencedirect",
        query="transparent conductive oxide",
        year_from=2021,
        year_to=2022,
    )
    duplicate_url = "https://www.sciencedirect.com/science/article/pii/SDUP"
    unique_url = "https://www.sciencedirect.com/science/article/pii/SUNIQUE"
    storage.add_collection_search_results(first, [
        SearchResult(url=duplicate_url, title="Duplicate", year="2021"),
    ])
    storage.add_collection_search_results(second, [
        SearchResult(url=duplicate_url, title="Duplicate Again", year="2021"),
        SearchResult(url=unique_url, title="Unique", year="2022"),
    ])

    duplicate_dir = storage.article_dir("10.1016/j.duplicate.2021.1")
    storage.save_meta(duplicate_dir, {
        "url": duplicate_url,
        "doi": "10.1016/j.duplicate.2021.1",
        "title": "Duplicate Article",
    })
    storage.save_fulltext(duplicate_dir, "# Duplicate\n")
    duplicate_id = storage.last_article_id(duplicate_dir)
    storage.add_article_to_collection(first, duplicate_id, url=duplicate_url)
    storage.add_article_to_collection(second, duplicate_id, url=duplicate_url)

    unique_dir = storage.article_dir("10.1016/j.unique.2022.1")
    storage.save_meta(unique_dir, {
        "url": unique_url,
        "doi": "10.1016/j.unique.2022.1",
        "title": "Unique Article",
    })
    storage.save_fulltext(unique_dir, "# Unique\n")
    unique_id = storage.last_article_id(unique_dir)
    storage.add_article_to_collection(second, unique_id, url=unique_url)

    out_dir = tmp_path / "merged"
    result = export_collections(
        db_path=tmp_path / "catalog.sqlite",
        base_dir=tmp_path,
        site="sciencedirect",
        collection_slugs=[
            "transparent-conductive-oxide_y2020-2021",
            "transparent-conductive-oxide_y2021-2022",
        ],
        out_dir=out_dir,
    )

    assert result.total_items == 3
    assert result.exported == 2
    assert result.missing == 0
    assert (out_dir / "articles" / "001__10.1016-j.duplicate.2021.1").exists()
    assert (out_dir / "articles" / "002__10.1016-j.unique.2022.1").exists()

    manifest_rows = _read_csv(out_dir / "manifest.csv")
    assert len(manifest_rows) == 2
    assert manifest_rows[0]["duplicate_count"] == "2"
    assert manifest_rows[0]["source_collections"] == (
        "transparent-conductive-oxide_y2020-2021;"
        "transparent-conductive-oxide_y2021-2022"
    )

    summary = json.loads((out_dir / "export_summary.json").read_text(encoding="utf-8"))
    assert summary["collections"] == [
        "transparent-conductive-oxide_y2020-2021",
        "transparent-conductive-oxide_y2021-2022",
    ]
    assert summary["deduplicated"] == 1
