"""
SQLite-backed storage and file asset layout.

Large artifacts are written to ``data/articles/{site}/_library/{article_key}``;
SQLite is the authoritative index for articles, collections, runs, run items,
and assets.
"""

import csv
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from io import StringIO
from pathlib import Path

log = logging.getLogger("storage")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("10."):
        return re.sub(r"[/\\:*?\"<>|]", "-", value)[:100]
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:16]


def _text_slug(value: str, fallback: str = "manual-crawl") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return (text or fallback)[:80]


def _extract_pii(url: str) -> str:
    match = re.search(r"/science/article/pii/([^/?#]+)", url or "")
    return match.group(1) if match else ""


def _json(data) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


class StorageManager:
    def __init__(self, base_dir: Path, site: str = "sciencedirect", run_id: str | None = None):
        self.base = Path(base_dir)
        self.site = site
        self.run_id = run_id
        self.db_path = self.base / "catalog.sqlite"
        self.articles_root = self.base / "articles"
        self.runs_root = self.base / "runs"
        self._dir_article_ids: dict[str, int] = {}

        self.base.mkdir(parents=True, exist_ok=True)
        self.articles_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.library_root(site).mkdir(parents=True, exist_ok=True)
        self.failed_root(site).mkdir(parents=True, exist_ok=True)
        self.searches_root(site).mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ─────────────────────────────────────────────
    #  SQLite
    # ─────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists runs (
                    id text primary key,
                    site text not null,
                    query text,
                    year_from integer,
                    year_to integer,
                    max_results integer,
                    status text not null,
                    options_json text not null default '{}',
                    started_at text not null,
                    ended_at text
                );

                create table if not exists articles (
                    id integer primary key autoincrement,
                    site text not null,
                    article_key text not null,
                    doi text,
                    pii text,
                    canonical_url text,
                    title text,
                    journal text,
                    year text,
                    authors_json text not null default '[]',
                    article_dir text not null,
                    saved_at text not null,
                    updated_at text not null,
                    unique(site, article_key)
                );

                create index if not exists idx_articles_site_doi
                    on articles(site, doi);
                create index if not exists idx_articles_site_url
                    on articles(site, canonical_url);

                create table if not exists run_items (
                    id integer primary key autoincrement,
                    run_id text not null,
                    url text not null,
                    article_id integer,
                    title text,
                    year text,
                    status text not null,
                    retries integer not null default 0,
                    last_error text,
                    added_at text not null,
                    updated_at text not null,
                    unique(run_id, url),
                    foreign key(run_id) references runs(id),
                    foreign key(article_id) references articles(id)
                );

                create table if not exists assets (
                    id integer primary key autoincrement,
                    article_id integer not null,
                    type text not null,
                    path text not null,
                    source_url text,
                    status text not null,
                    size_bytes integer,
                    sha256 text,
                    content_type text,
                    caption text,
                    label text,
                    error text,
                    created_at text not null,
                    foreign key(article_id) references articles(id)
                );

                create table if not exists collections (
                    id integer primary key autoincrement,
                    site text not null,
                    slug text not null,
                    query text,
                    year_from integer,
                    year_to integer,
                    max_results integer,
                    options_json text not null default '{}',
                    created_at text not null,
                    updated_at text not null,
                    unique(site, slug)
                );

                create table if not exists collection_runs (
                    collection_id integer not null,
                    run_id text not null,
                    unique(collection_id, run_id),
                    foreign key(collection_id) references collections(id),
                    foreign key(run_id) references runs(id)
                );

                create table if not exists collection_items (
                    id integer primary key autoincrement,
                    collection_id integer not null,
                    url text not null,
                    article_id integer,
                    title text,
                    year text,
                    status text not null,
                    added_at text not null,
                    updated_at text not null,
                    unique(collection_id, url),
                    foreign key(collection_id) references collections(id),
                    foreign key(article_id) references articles(id)
                );
                """
            )

    # ─────────────────────────────────────────────
    #  Collection directories
    # ─────────────────────────────────────────────
    def site_root(self, site: str | None = None) -> Path:
        selected_site = site or self.site
        d = self.articles_root / selected_site
        d.mkdir(parents=True, exist_ok=True)
        return d

    def library_root(self, site: str | None = None) -> Path:
        d = self.site_root(site) / "_library"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def failed_root(self, site: str | None = None) -> Path:
        d = self.site_root(site) / "_failed"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def searches_root(self, site: str | None = None) -> Path:
        d = self.site_root(site) / "searches"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ─────────────────────────────────────────────
    #  Runs / state
    # ─────────────────────────────────────────────
    def create_run(
        self,
        site: str | None = None,
        query: str = "",
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int | None = None,
        options: dict | None = None,
        run_type: str = "crawl",
    ) -> str:
        site = site or self.site
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        suffix = hashlib.md5(f"{site}:{query}:{stamp}:{run_type}".encode()).hexdigest()[:6]
        run_id = f"{stamp}-{site}-{run_type}-{suffix}"
        started = _now()
        opts = dict(options or {})
        opts.setdefault("type", run_type)

        with self._connect() as conn:
            conn.execute(
                """
                insert into runs(id, site, query, year_from, year_to, max_results, status,
                                 options_json, started_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, site, query, year_from, year_to, max_results, STATUS_RUNNING, _json(opts), started),
            )

        run_dir = self.run_dir(run_id)
        _write_json(run_dir / "run.json", {
            "id": run_id,
            "site": site,
            "query": query,
            "year_from": year_from,
            "year_to": year_to,
            "max_results": max_results,
            "status": STATUS_RUNNING,
            "options": opts,
            "started_at": started,
            "ended_at": None,
        })
        self.run_id = run_id
        return run_id

    def run_dir(self, run_id: str | None = None) -> Path:
        rid = run_id or self.run_id
        if not rid:
            return self.runs_root
        d = self.runs_root / rid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def finish_run(self, run_id: str | None = None, status: str = STATUS_DONE):
        rid = run_id or self.run_id
        if not rid:
            return
        ended = _now()
        with self._connect() as conn:
            conn.execute("update runs set status = ?, ended_at = ? where id = ?", (status, ended, rid))
            row = conn.execute("select * from runs where id = ?", (rid,)).fetchone()
        if row:
            _write_json(self.run_dir(rid) / "run.json", _run_row_to_dict(row))

    def latest_run_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("select id from runs order by started_at desc limit 1").fetchone()
        return row["id"] if row else None

    def run_info(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
        return _run_row_to_dict(row) if row else None

    def add_run_items(self, run_id: str, urls: list[str], results: list | None = None):
        by_url = {getattr(r, "url", ""): r for r in (results or [])}
        now = _now()
        with self._connect() as conn:
            for url in urls:
                result = by_url.get(url)
                conn.execute(
                    """
                    insert into run_items(run_id, url, title, year, status, retries, added_at, updated_at)
                    values (?, ?, ?, ?, ?, 0, ?, ?)
                    on conflict(run_id, url) do nothing
                    """,
                    (
                        run_id,
                        url,
                        getattr(result, "title", "") if result else "",
                        getattr(result, "year", "") if result else "",
                        STATUS_PENDING,
                        now,
                        now,
                    ),
                )
        log.info("注册 %s 个URL到任务队列 run=%s", len(urls), run_id)

    def pending_urls(self, run_id: str | None = None, retry_limit: int = 3) -> list[str]:
        rid = run_id or self.run_id
        if not rid:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                select url from run_items
                where run_id = ?
                  and (status = ? or (status = ? and retries < ?))
                order by id
                """,
                (rid, STATUS_PENDING, STATUS_FAILED, retry_limit),
            ).fetchall()
        return [r["url"] for r in rows]

    def mark_done(self, run_id: str, url: str, article_id: int | None = None):
        self._mark_item(run_id, url, STATUS_DONE, article_id=article_id)

    def mark_failed(self, run_id: str, url: str, error: str = ""):
        self._mark_item(run_id, url, STATUS_FAILED, error=error, increment_retry=True)

    def mark_skipped(self, run_id: str, url: str):
        self._mark_item(run_id, url, STATUS_SKIPPED)

    def _mark_item(
        self,
        run_id: str,
        url: str,
        status: str,
        article_id: int | None = None,
        error: str = "",
        increment_retry: bool = False,
    ):
        now = _now()
        with self._connect() as conn:
            if increment_retry:
                conn.execute(
                    """
                    update run_items
                    set status = ?, retries = retries + 1, last_error = ?, updated_at = ?
                    where run_id = ? and url = ?
                    """,
                    (status, error, now, run_id, url),
                )
            else:
                conn.execute(
                    """
                    update run_items
                    set status = ?, article_id = coalesce(?, article_id), updated_at = ?
                    where run_id = ? and url = ?
                    """,
                    (status, article_id, now, run_id, url),
                )

    def print_run_summary(self, run_id: str | None = None):
        rid = run_id or self.latest_run_id()
        if not rid:
            log.info("暂无运行记录")
            return
        s = self.run_summary(rid)
        log.info(
            "\n%s\n  Run: %s\n  任务总计: %s\n  ✓ 完成:   %s\n  ✗ 失败:   %s\n  ↻ 待处理: %s\n  → 跳过:   %s\n%s",
            "─" * 40,
            rid,
            s["total"],
            s["done"],
            s["failed"],
            s["pending"],
            s["skipped"],
            "─" * 40,
        )

    def run_summary(self, run_id: str) -> dict:
        counts = {"total": 0, "done": 0, "failed": 0, "pending": 0, "skipped": 0}
        with self._connect() as conn:
            rows = conn.execute(
                "select status, count(*) as n from run_items where run_id = ? group by status",
                (run_id,),
            ).fetchall()
        for row in rows:
            counts["total"] += row["n"]
            if row["status"] in counts:
                counts[row["status"]] = row["n"]
        return counts

    def save_search_results(self, run_id: str, results: list):
        path = self.run_dir(run_id) / "search_results.jsonl"
        lines = [
            _json({
                "url": getattr(result, "url", ""),
                "title": getattr(result, "title", ""),
                "year": getattr(result, "year", ""),
            })
            for result in results
        ]
        _write_text(path, "\n".join(lines) + ("\n" if lines else ""))

    def save_run_urls(self, run_id: str, urls: list[str]) -> Path:
        path = self.run_dir(run_id) / "urls.txt"
        _write_text(path, "\n".join(urls))
        return path

    # ─────────────────────────────────────────────
    #  Collections
    # ─────────────────────────────────────────────
    def collection_slug(
        self,
        site: str | None = None,
        query: str = "",
        year_from: int | None = None,
        year_to: int | None = None,
        options: dict | None = None,
    ) -> str:
        opts = options or {}
        if query:
            year_part = f"y{year_from or 'any'}-{year_to or 'any'}"
            return f"{_text_slug(query, 'search')}_{year_part}"
        source = opts.get("source_file") or opts.get("source_url") or _now()
        digest = hashlib.md5(str(source).encode("utf-8")).hexdigest()[:6]
        return f"manual-crawl_{datetime.now().strftime('%Y%m%d')}-{digest}"

    def create_or_get_collection(
        self,
        site: str | None = None,
        query: str = "",
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int | None = None,
        options: dict | None = None,
    ) -> int:
        selected_site = site or self.site
        base_slug = self.collection_slug(selected_site, query, year_from, year_to, options)
        slug = base_slug
        opts = dict(options or {})
        now = _now()
        with self._connect() as conn:
            existing = conn.execute(
                """
                select * from collections
                where site = ? and query = ? and year_from is ? and year_to is ?
                order by id limit 1
                """,
                (selected_site, query, year_from, year_to),
            ).fetchone()
            if existing:
                return existing["id"]

            conflict = conn.execute(
                "select id from collections where site = ? and slug = ?",
                (selected_site, slug),
            ).fetchone()
            if conflict:
                digest = hashlib.md5(_json({
                    "site": selected_site,
                    "query": query,
                    "year_from": year_from,
                    "year_to": year_to,
                    "options": opts,
                }).encode("utf-8")).hexdigest()[:6]
                slug = f"{base_slug}-{digest}"

            conn.execute(
                """
                insert into collections(site, slug, query, year_from, year_to, max_results,
                                        options_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (selected_site, slug, query, year_from, year_to, max_results, _json(opts), now, now),
            )
            row = conn.execute(
                "select id from collections where site = ? and slug = ?",
                (selected_site, slug),
            ).fetchone()
        self.collection_dir(row["id"])
        self.refresh_collection_exports(row["id"])
        return row["id"]

    def collection_dir(self, collection_id_or_slug) -> Path:
        if isinstance(collection_id_or_slug, int):
            with self._connect() as conn:
                row = conn.execute(
                    "select site, slug from collections where id = ?",
                    (collection_id_or_slug,),
                ).fetchone()
            if not row:
                raise KeyError(f"unknown collection: {collection_id_or_slug}")
            site = row["site"]
            slug = row["slug"]
        else:
            site = self.site
            slug = str(collection_id_or_slug)
        d = self.searches_root(site) / slug
        (d / "article_links").mkdir(parents=True, exist_ok=True)
        return d

    def attach_run_to_collection(self, run_id: str, collection_id: int):
        with self._connect() as conn:
            conn.execute(
                """
                insert into collection_runs(collection_id, run_id)
                values (?, ?)
                on conflict(collection_id, run_id) do nothing
                """,
                (collection_id, run_id),
            )
            run = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
            collection = conn.execute("select * from collections where id = ?", (collection_id,)).fetchone()
            if run and collection:
                opts = json.loads(run["options_json"] or "{}")
                opts["collection_id"] = collection_id
                opts["collection_slug"] = collection["slug"]
                conn.execute(
                    "update runs set options_json = ? where id = ?",
                    (_json(opts), run_id),
                )
                row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
            else:
                row = None
        if row:
            _write_json(self.run_dir(run_id) / "run.json", _run_row_to_dict(row))
        self.refresh_collection_exports(collection_id)

    def collection_for_run(self, run_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "select collection_id from collection_runs where run_id = ? order by collection_id limit 1",
                (run_id,),
            ).fetchone()
            if row:
                return row["collection_id"]
            run = conn.execute("select options_json from runs where id = ?", (run_id,)).fetchone()
        if not run:
            return None
        opts = json.loads(run["options_json"] or "{}")
        return opts.get("collection_id")

    def add_collection_search_results(self, collection_id: int, results: list):
        now = _now()
        with self._connect() as conn:
            for result in results:
                url = getattr(result, "url", "")
                if not url:
                    continue
                conn.execute(
                    """
                    insert into collection_items(collection_id, url, title, year, status, added_at, updated_at)
                    values (?, ?, ?, ?, ?, ?, ?)
                    on conflict(collection_id, url) do update set
                        title = coalesce(nullif(excluded.title, ''), collection_items.title),
                        year = coalesce(nullif(excluded.year, ''), collection_items.year),
                        updated_at = excluded.updated_at
                    """,
                    (
                        collection_id,
                        url,
                        getattr(result, "title", ""),
                        getattr(result, "year", ""),
                        STATUS_PENDING,
                        now,
                        now,
                    ),
                )
        self.refresh_collection_exports(collection_id)

    def add_article_to_collection(
        self,
        collection_id: int,
        article_id: int | None,
        url: str,
        title: str = "",
        year: str = "",
        status: str = STATUS_DONE,
    ):
        if not url and article_id:
            with self._connect() as conn:
                article = conn.execute("select canonical_url from articles where id = ?", (article_id,)).fetchone()
            url = article["canonical_url"] if article else ""
        if not url:
            return
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                insert into collection_items(collection_id, url, article_id, title, year, status, added_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(collection_id, url) do update set
                    article_id = coalesce(excluded.article_id, collection_items.article_id),
                    title = coalesce(nullif(excluded.title, ''), collection_items.title),
                    year = coalesce(nullif(excluded.year, ''), collection_items.year),
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (collection_id, url, article_id, title, year, status, now, now),
            )
        self.refresh_collection_exports(collection_id)

    def refresh_collection_exports(self, collection_id: int):
        with self._connect() as conn:
            collection = conn.execute("select * from collections where id = ?", (collection_id,)).fetchone()
            if not collection:
                raise KeyError(f"unknown collection: {collection_id}")
            run_rows = conn.execute(
                "select run_id from collection_runs where collection_id = ? order by run_id",
                (collection_id,),
            ).fetchall()
            rows = conn.execute(
                """
                select ci.*, a.site as article_site, a.article_key, a.doi, a.pii, a.canonical_url,
                       a.title as article_title, a.journal, a.year as article_year,
                       a.authors_json, a.article_dir
                from collection_items ci
                left join articles a on a.id = ci.article_id
                where ci.collection_id = ?
                order by ci.id
                """,
                (collection_id,),
            ).fetchall()

        collection_dir = self.collection_dir(collection_id)
        run_ids = [row["run_id"] for row in run_rows]
        _write_json(collection_dir / "collection.json", {
            "id": collection["id"],
            "site": collection["site"],
            "slug": collection["slug"],
            "query": collection["query"] or "",
            "year_from": collection["year_from"],
            "year_to": collection["year_to"],
            "max_results": collection["max_results"],
            "options": json.loads(collection["options_json"] or "{}"),
            "run_ids": run_ids,
            "article_count": len(rows),
            "updated_at": _now(),
        })
        _write_text(collection_dir / "urls.txt", "\n".join(row["url"] for row in rows))

        export_rows = [self._collection_export_row(row, run_ids) for row in rows]
        _write_text(
            collection_dir / "articles.jsonl",
            "\n".join(_json(row) for row in export_rows) + ("\n" if export_rows else ""),
        )
        self._write_collection_csv(collection_dir / "articles.csv", export_rows)
        self._refresh_collection_links(collection_dir, rows)

    def _collection_export_row(self, row: sqlite3.Row, run_ids: list[str]) -> dict:
        authors = []
        if row["authors_json"]:
            try:
                authors = json.loads(row["authors_json"])
            except json.JSONDecodeError:
                authors = []
        article_dir = row["article_dir"] or ""
        assets_summary = {}
        if row["article_id"]:
            with self._connect() as conn:
                asset_rows = conn.execute(
                    "select type, status, count(*) as n from assets where article_id = ? group by type, status",
                    (row["article_id"],),
                ).fetchall()
            for asset in asset_rows:
                assets_summary[f"{asset['type']}:{asset['status']}"] = asset["n"]
        return {
            "title": row["article_title"] or row["title"] or "",
            "doi": row["doi"] or "",
            "pii": row["pii"] or "",
            "journal": row["journal"] or "",
            "year": row["article_year"] or row["year"] or "",
            "canonical_url": row["canonical_url"] or row["url"] or "",
            "article_key": row["article_key"] or "",
            "article_dir": article_dir,
            "run_ids": run_ids,
            "status": row["status"] or "",
            "authors": authors,
            "assets_summary": assets_summary,
        }

    def _write_collection_csv(self, path: Path, rows: list[dict]):
        fields = [
            "title", "doi", "pii", "journal", "year", "canonical_url",
            "article_key", "article_dir", "run_ids", "status",
        ]
        buf = StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["run_ids"] = ";".join(row.get("run_ids", []))
            writer.writerow(csv_row)
        _write_text(path, buf.getvalue())

    def _refresh_collection_links(self, collection_dir: Path, rows: list[sqlite3.Row]):
        links_dir = collection_dir / "article_links"
        links_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            article_dir = row["article_dir"]
            article_key = row["article_key"]
            if not article_dir or not article_key:
                continue
            link_path = links_dir / article_key
            target = self.base / article_dir
            if link_path.exists():
                continue
            if link_path.is_symlink():
                link_path.unlink()
            try:
                rel_target = Path("../../../_library") / article_key
                link_path.symlink_to(rel_target, target_is_directory=True)
            except OSError:
                _write_json(links_dir / f"{article_key}.link.json", {
                    "article_key": article_key,
                    "article_dir": article_dir,
                    "absolute_path": str(target),
                })

    # ─────────────────────────────────────────────
    #  Article files / index
    # ─────────────────────────────────────────────
    def article_key(self, doi_or_url: str) -> str:
        return _slug(doi_or_url)

    def article_dir(self, doi_or_url: str, site: str | None = None) -> Path:
        selected_site = site or self.site
        key = self.article_key(doi_or_url)
        doi = doi_or_url if doi_or_url.startswith("10.") else ""
        with self._connect() as conn:
            if doi:
                row = conn.execute(
                    "select article_dir from articles where site = ? and (doi = ? or article_key = ?) limit 1",
                    (selected_site, doi, key),
                ).fetchone()
            else:
                row = conn.execute(
                    "select article_dir from articles where site = ? and (canonical_url = ? or article_key = ?) limit 1",
                    (selected_site, doi_or_url, key),
                ).fetchone()
        d = self.base / row["article_dir"] if row and row["article_dir"] else self.library_root(selected_site) / key
        for sub in ("raw", "parsed", "assets/figures", "assets/tables"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def failed_article_dir(self, doi_or_url: str, site: str | None = None) -> Path:
        selected_site = site or self.site
        d = self.failed_root(selected_site) / self.article_key(doi_or_url)
        for sub in ("raw", "parsed"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def article_exists(self, doi_or_url: str, site: str | None = None) -> bool:
        selected_site = site or self.site
        key = self.article_key(doi_or_url)
        doi = doi_or_url if doi_or_url.startswith("10.") else ""
        with self._connect() as conn:
            if doi:
                row = conn.execute(
                    "select 1 from articles where site = ? and (doi = ? or article_key = ?) limit 1",
                    (selected_site, doi, key),
                ).fetchone()
            else:
                row = conn.execute(
                    "select 1 from articles where site = ? and (canonical_url = ? or article_key = ?) limit 1",
                    (selected_site, doi_or_url, key),
                ).fetchone()
        return row is not None

    def save_meta(self, adir: Path, meta: dict):
        meta = dict(meta)
        meta["_saved_at"] = _now()
        article_id = self._upsert_article(adir, meta)
        self._dir_article_ids[str(adir)] = article_id
        _write_json(adir / "meta.json", meta)

    def save_failed_meta(self, adir: Path, meta: dict, reason: str):
        meta = dict(meta)
        meta["_status"] = STATUS_FAILED
        meta["_failure_reason"] = reason
        meta["_failed_at"] = _now()
        _write_json(adir / "meta.json", meta)

    def save_fulltext(self, adir: Path, markdown: str):
        _write_text(adir / "parsed" / "fulltext.md", markdown)

    def save_abstract(self, adir: Path, text: str):
        _write_text(adir / "parsed" / "abstract.txt", text)

    def save_html(self, adir: Path, html: str):
        _write_text(adir / "raw" / "article.html", html)

    def save_figure(
        self,
        adir: Path,
        idx: int,
        data: bytes,
        ext: str,
        caption: str = "",
        label: str = "",
        source_url: str = "",
        content_type: str = "",
        method: str = "",
    ):
        stem = f"fig_{idx:03d}"
        path = adir / "assets" / "figures" / f"{stem}{ext}"
        path.write_bytes(data)
        if caption:
            _write_text(adir / "assets" / "figures" / f"{stem}_caption.txt", caption)
        if label:
            _write_text(adir / "assets" / "figures" / f"{stem}_label.txt", label)
        self._record_asset(
            adir,
            "figure",
            path,
            data,
            source_url=source_url,
            content_type=content_type or _content_type_from_ext(ext),
            caption=caption,
            label=label,
            error=method,
        )
        log.info("    ✓ 图 %s 已保存 (%s KB) %s", idx, len(data) // 1024, label)

    def record_asset_failure(
        self,
        adir: Path,
        asset_type: str,
        source_url: str,
        error: str,
        content_type: str = "",
        caption: str = "",
        label: str = "",
    ):
        self._record_asset(
            adir,
            asset_type,
            Path(""),
            b"",
            source_url=source_url,
            content_type=content_type,
            caption=caption,
            label=label,
            status=STATUS_FAILED,
            error=error,
        )

    def save_table(self, adir: Path, idx: int, html: str, rows: list[list[str]], caption: str = ""):
        stem = f"table_{idx:03d}"
        table_dir = adir / "assets" / "tables"
        html_path = table_dir / f"{stem}.html"
        _write_text(html_path, html)
        if rows:
            buf = StringIO()
            writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
            writer.writerows(rows)
            csv_data = buf.getvalue()
            csv_path = table_dir / f"{stem}.csv"
            _write_text(csv_path, csv_data)
            self._record_asset(
                adir,
                "table",
                csv_path,
                csv_data.encode("utf-8"),
                content_type="text/csv",
                caption=caption,
            )
        if caption:
            _write_text(table_dir / f"{stem}_caption.txt", caption)
        log.info("    ✓ 表 %s 已保存  %s", idx, caption[:40])

    def last_article_id(self, adir: Path | None = None) -> int | None:
        if adir is not None and str(adir) in self._dir_article_ids:
            return self._dir_article_ids[str(adir)]
        with self._connect() as conn:
            row = conn.execute("select id from articles order by updated_at desc limit 1").fetchone()
        return row["id"] if row else None

    def find_article_id(self, doi_or_url: str, site: str | None = None) -> int | None:
        selected_site = site or self.site
        key = self.article_key(doi_or_url)
        doi = doi_or_url if doi_or_url.startswith("10.") else ""
        with self._connect() as conn:
            if doi:
                row = conn.execute(
                    "select id from articles where site = ? and (doi = ? or article_key = ?) limit 1",
                    (selected_site, doi, key),
                ).fetchone()
            else:
                row = conn.execute(
                    "select id from articles where site = ? and (canonical_url = ? or article_key = ?) limit 1",
                    (selected_site, doi_or_url, key),
                ).fetchone()
        return row["id"] if row else None

    def _upsert_article(self, adir: Path, meta: dict) -> int:
        site = self.site
        doi = meta.get("doi", "")
        canonical_url = meta.get("url", "")
        key = adir.name or self.article_key(doi or canonical_url)
        article_dir = self._rel(adir)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                insert into articles(site, article_key, doi, pii, canonical_url, title, journal,
                                     year, authors_json, article_dir, saved_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(site, article_key) do update set
                    doi = excluded.doi,
                    pii = excluded.pii,
                    canonical_url = excluded.canonical_url,
                    title = excluded.title,
                    journal = excluded.journal,
                    year = excluded.year,
                    authors_json = excluded.authors_json,
                    article_dir = excluded.article_dir,
                    updated_at = excluded.updated_at
                """,
                (
                    site,
                    key,
                    doi,
                    _extract_pii(canonical_url),
                    canonical_url,
                    meta.get("title", ""),
                    meta.get("journal", ""),
                    meta.get("year", ""),
                    json.dumps(meta.get("authors", []), ensure_ascii=False),
                    article_dir,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "select id from articles where site = ? and article_key = ?",
                (site, key),
            ).fetchone()
        return row["id"]

    def _record_asset(
        self,
        adir: Path,
        asset_type: str,
        path: Path,
        data: bytes,
        source_url: str = "",
        content_type: str = "",
        caption: str = "",
        label: str = "",
        status: str = STATUS_DONE,
        error: str = "",
    ):
        article_id = self._article_id_for_dir(adir)
        digest = hashlib.sha256(data).hexdigest() if data else ""
        with self._connect() as conn:
            conn.execute(
                """
                insert into assets(article_id, type, path, source_url, status, size_bytes,
                                   sha256, content_type, caption, label, error, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    asset_type,
                    "" if str(path) == "." else self._rel(path),
                    source_url,
                    status,
                    len(data) if data is not None else 0,
                    digest,
                    content_type,
                    caption,
                    label,
                    error,
                    _now(),
                ),
            )

    def _article_id_for_dir(self, adir: Path) -> int:
        if str(adir) in self._dir_article_ids:
            return self._dir_article_ids[str(adir)]
        with self._connect() as conn:
            row = conn.execute(
                "select id from articles where article_dir = ?",
                (self._rel(adir),),
            ).fetchone()
        if not row:
            raise RuntimeError(f"article metadata must be saved before assets: {adir}")
        self._dir_article_ids[str(adir)] = row["id"]
        return row["id"]

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.base))
        except ValueError:
            return str(path)

    # ─────────────────────────────────────────────
    #  Reports
    # ─────────────────────────────────────────────
    def generate_report(self, run_id: str | None = None) -> str:
        rid = run_id or self.run_id
        with self._connect() as conn:
            if rid:
                run = conn.execute("select * from runs where id = ?", (rid,)).fetchone()
                article_rows = conn.execute(
                    """
                    select a.* from articles a
                    join run_items ri on ri.article_id = a.id
                    where ri.run_id = ?
                    order by ri.id
                    """,
                    (rid,),
                ).fetchall()
            else:
                run = None
                article_rows = conn.execute("select * from articles order by saved_at").fetchall()

        title = "# 爬取报告"
        lines = [
            title,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        if run:
            lines.extend([
                f"Run: {run['id']}",
                f"站点: {run['site']}",
                f"检索: {run['query'] or ''}",
            ])
        lines.extend([
            f"共 {len(article_rows)} 篇文章\n",
            "| # | 标题 | 期刊 | 年份 | DOI |",
            "|---|------|------|------|-----|",
        ])
        for i, item in enumerate(article_rows, 1):
            lines.append(
                f"| {i} | {(item['title'] or '')[:50]} | {(item['journal'] or '')[:20]} | "
                f"{item['year'] or ''} | {item['doi'] or ''} |"
            )

        report = "\n".join(lines)
        if rid:
            _write_text(self.run_dir(rid) / "report.md", report)
        else:
            _write_text(self.base / "report.md", report)
        return report


def _run_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "site": row["site"],
        "query": row["query"],
        "year_from": row["year_from"],
        "year_to": row["year_to"],
        "max_results": row["max_results"],
        "status": row["status"],
        "options": json.loads(row["options_json"] or "{}"),
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def _content_type_from_ext(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext.lower(), "application/octet-stream")


def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
