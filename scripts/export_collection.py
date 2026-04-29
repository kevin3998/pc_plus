"""Export a saved search collection into a standalone folder."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path


MANIFEST_FIELDS = [
    "index",
    "collection_slug",
    "source_collections",
    "duplicate_count",
    "site",
    "status",
    "article_key",
    "doi",
    "pii",
    "title",
    "journal",
    "year",
    "url",
    "canonical_url",
    "source_article_dir",
    "export_article_dir",
    "has_fulltext",
    "has_html",
    "figure_count",
    "table_count",
]

MISSING_FIELDS = [
    "index",
    "url",
    "collection_status",
    "reason",
    "article_id",
    "article_key",
    "article_dir",
]


@dataclass
class ExportResult:
    total_items: int
    exported: int
    missing: int
    output_dir: Path
    dry_run: bool = False


def export_collection(
    *,
    db_path: Path,
    base_dir: Path,
    site: str,
    collection_slug: str,
    out_dir: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ExportResult:
    return export_collections(
        db_path=db_path,
        base_dir=base_dir,
        site=site,
        collection_slugs=[collection_slug],
        out_dir=out_dir,
        overwrite=overwrite,
        dry_run=dry_run,
    )


def export_collections(
    *,
    db_path: Path,
    base_dir: Path,
    site: str,
    collection_slugs: list[str],
    out_dir: Path,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ExportResult:
    db_path = Path(db_path)
    base_dir = Path(base_dir)
    out_dir = Path(out_dir)
    collection_slugs = [slug for slug in collection_slugs if slug]
    if not collection_slugs:
        raise ValueError("at least one collection is required")
    rows = []
    for collection_slug in collection_slugs:
        rows.extend(
            {"collection_slug": collection_slug, "row": row}
            for row in _collection_rows(db_path, site, collection_slug)
        )

    manifest_rows: list[dict] = []
    missing_rows: list[dict] = []
    seen_exportable: dict[str, dict] = {}
    exported = 0

    for raw_index, item in enumerate(rows, 1):
        collection_slug = item["collection_slug"]
        row = item["row"]
        source_dir = _source_article_dir(base_dir, row["article_dir"] or "")
        reason = _missing_reason(row, source_dir)
        if reason:
            missing_rows.append(_missing_row(raw_index, row, reason, collection_slug))
            continue

        dedupe_key = _dedupe_key(row)
        existing = seen_exportable.get(dedupe_key)
        if existing:
            existing["source_collections"].append(collection_slug)
            existing["duplicate_count"] += 1
            continue

        article_key = row["article_key"]
        export_index = len(seen_exportable) + 1
        export_dir = out_dir / "articles" / f"{export_index:03d}__{article_key}"
        if export_dir.exists() and not overwrite:
            missing_rows.append(_missing_row(raw_index, row, "existing_target", collection_slug))
            continue

        seen_exportable[dedupe_key] = {
            "raw_index": raw_index,
            "export_index": export_index,
            "row": row,
            "source_dir": source_dir,
            "export_dir": export_dir,
            "source_collections": [collection_slug],
            "duplicate_count": 1,
        }

    for item in seen_exportable.values():
        row = item["row"]
        source_dir = item["source_dir"]
        export_dir = item["export_dir"]

        manifest_row = _manifest_row(
            index=item["export_index"],
            row=row,
            source_dir=source_dir,
            export_dir=export_dir,
            site=site,
            collection_slug=item["source_collections"][0],
            source_collections=item["source_collections"],
            duplicate_count=item["duplicate_count"],
        )
        manifest_rows.append(manifest_row)

        if not dry_run:
            if export_dir.exists():
                shutil.rmtree(export_dir)
            export_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, export_dir)
        exported += 1

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(out_dir / "manifest.csv", MANIFEST_FIELDS, manifest_rows)
        _write_jsonl(out_dir / "manifest.jsonl", manifest_rows)
        _write_csv(out_dir / "missing.csv", MISSING_FIELDS, missing_rows)
        _write_summary(out_dir / "export_summary.json", {
            "site": site,
            "collection": collection_slugs[0],
            "collections": collection_slugs,
            "mode": "copy",
            "dry_run": False,
            "total_items": len(rows),
            "exported": exported,
            "missing": len(missing_rows),
            "deduplicated": len(rows) - len(missing_rows) - len(manifest_rows),
            "output_dir": str(out_dir),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

    return ExportResult(
        total_items=len(rows),
        exported=exported,
        missing=len(missing_rows),
        output_dir=out_dir,
        dry_run=dry_run,
    )


def _collection_rows(db_path: Path, site: str, collection_slug: str) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        collection = conn.execute(
            "select id from collections where site = ? and slug = ?",
            (site, collection_slug),
        ).fetchone()
        if not collection:
            available = conn.execute(
                "select site, slug from collections order by created_at desc limit 20"
            ).fetchall()
            choices = ", ".join(f"{row['site']}:{row['slug']}" for row in available)
            suffix = f" Available: {choices}" if choices else ""
            raise ValueError(f"collection not found: {site}:{collection_slug}.{suffix}")

        return conn.execute(
            """
            select
              ci.id as collection_item_id,
              ci.url,
              ci.status as collection_status,
              ci.title as search_title,
              ci.year as search_year,
              a.id as article_id,
              a.article_key,
              a.doi,
              a.pii,
              a.canonical_url,
              a.title,
              a.journal,
              a.year,
              a.authors_json,
              a.article_dir
            from collection_items ci
            left join articles a on a.id = ci.article_id
            where ci.collection_id = ?
            order by ci.id
            """,
            (collection["id"],),
        ).fetchall()


def _source_article_dir(base_dir: Path, article_dir: str) -> Path:
    if not article_dir:
        return Path("")
    path = Path(article_dir)
    return path if path.is_absolute() else base_dir / path


def _missing_reason(row: sqlite3.Row, source_dir: Path) -> str:
    if not row["article_id"]:
        return "no_article_id"
    if not row["article_dir"]:
        return "no_article_dir"
    if not source_dir.exists():
        return "source_dir_missing"
    if not (source_dir / "parsed" / "fulltext.md").exists():
        return "fulltext_missing"
    return ""


def _dedupe_key(row: sqlite3.Row) -> str:
    if row["article_id"]:
        return f"article:{row['article_id']}"
    if row["article_key"]:
        return f"key:{row['article_key']}"
    return f"url:{row['url']}"


def _missing_row(index: int, row: sqlite3.Row, reason: str, collection_slug: str = "") -> dict:
    return {
        "index": index,
        "url": row["url"] or "",
        "collection_status": row["collection_status"] or "",
        "reason": reason,
        "article_id": row["article_id"] or "",
        "article_key": row["article_key"] or "",
        "article_dir": row["article_dir"] or "",
        "collection_slug": collection_slug,
    }


def _manifest_row(
    *,
    index: int,
    row: sqlite3.Row,
    source_dir: Path,
    export_dir: Path,
    site: str,
    collection_slug: str,
    source_collections: list[str],
    duplicate_count: int,
) -> dict:
    return {
        "index": index,
        "collection_slug": collection_slug,
        "source_collections": ";".join(dict.fromkeys(source_collections)),
        "duplicate_count": duplicate_count,
        "site": site,
        "status": row["collection_status"] or "",
        "article_key": row["article_key"] or "",
        "doi": row["doi"] or "",
        "pii": row["pii"] or "",
        "title": row["title"] or row["search_title"] or "",
        "journal": row["journal"] or "",
        "year": row["year"] or row["search_year"] or "",
        "url": row["url"] or "",
        "canonical_url": row["canonical_url"] or row["url"] or "",
        "source_article_dir": str(source_dir),
        "export_article_dir": str(export_dir),
        "has_fulltext": (source_dir / "parsed" / "fulltext.md").exists(),
        "has_html": (source_dir / "raw" / "article.html").exists(),
        "figure_count": _file_count(source_dir / "assets" / "figures"),
        "table_count": _file_count(source_dir / "assets" / "tables"),
    }


def _file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _write_csv(path: Path, fields: list[str], rows: list[dict]):
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]):
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _write_summary(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a saved collection as copied article folders.")
    parser.add_argument("--site", required=True, help="Site key, e.g. sciencedirect")
    parser.add_argument(
        "--collection",
        required=True,
        action="append",
        help="Collection slug under articles/{site}/searches. Repeat to merge collections.",
    )
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Project data directory")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing exported article folders")
    parser.add_argument("--dry-run", action="store_true", help="Classify items without writing output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = export_collection(
        db_path=args.data_dir / "catalog.sqlite",
        base_dir=args.data_dir,
        site=args.site,
        collection_slug=args.collection[0],
        out_dir=args.out,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    ) if len(args.collection) == 1 else export_collections(
        db_path=args.data_dir / "catalog.sqlite",
        base_dir=args.data_dir,
        site=args.site,
        collection_slugs=args.collection,
        out_dir=args.out,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )
    action = "would export" if result.dry_run else "exported"
    print(
        f"{action}: {result.exported} | missing: {result.missing} | "
        f"total: {result.total_items} | out: {result.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
