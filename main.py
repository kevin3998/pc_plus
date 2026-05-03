#!/usr/bin/env python3
"""
main.py  —  学术文献爬虫主入口
═══════════════════════════════════════════════════════════════

【快速上手：ScienceDirect 浏览器主线】

  # 第一步：打开 Patchright Chrome 完成登录/验证
  python main.py login --site sciencedirect

  # 第二步：按关键词检索
  python main.py search \\
    --site sciencedirect \\
    --query "transparent conductive oxide" \\
    --year-from 2024 --year-to 2025 \\
    --max 100

  # 第三步：爬取检索结果（使用 search 输出的 data/runs/{run_id}/urls.txt）
  python main.py crawl --file data/runs/{run_id}/urls.txt --no-figures

  # 或爬取单篇
  python main.py crawl --url https://www.sciencedirect.com/science/article/pii/XXX

  # 查看进度
  python main.py status

═══════════════════════════════════════════════════════════════
"""

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ── 路径修正（允许从任意目录运行）──────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    DATA_DIR, COOKIE_FILE, LOG_DIR,
    JOURNAL_CONFIGS,
)
from core.cookie_manager import CookieManager
from core.browser import BrowserEngine
from core.downloader import BinaryDownloadSession
from core.storage import StorageManager
from core.parser import ArticleParser
from sites.base import SearchFilters, SearchResult
from sites.registry import detect_adapter, get_adapter


# ─────────────────────────────────────────────
#  日志配置
# ─────────────────────────────────────────────
def setup_logging(verbose: bool = False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "crawler.log", encoding="utf-8"),
        ],
    )


log = logging.getLogger("main")


@dataclass
class CrawlItem:
    site: str
    url: str
    error: str = ""


# ─────────────────────────────────────────────
#  各子命令实现
# ─────────────────────────────────────────────
def cmd_login(args):
    """打开 Patchright 浏览器，让用户完成站点登录并保存 profile。"""
    site = args.site.lower()
    try:
        adapter = get_adapter(site)
    except KeyError:
        print(f"❌ 未知站点: {site}，支持: {', '.join(JOURNAL_CONFIGS)}")
        sys.exit(1)

    cm = CookieManager(COOKIE_FILE)
    cm.load()
    engine = BrowserEngine(cm, profile_dir=_profile_dir_for_site(site))
    try:
        login_url = adapter.login_url or adapter.search_base
        engine.start(domain=_domain_from_url(login_url))
        engine.goto(login_url)
        engine.wait_for_user(
            f"\n请在弹出的 Chrome 窗口中完成 {adapter.name} 登录/验证。"
            f"\n确认已回到 {adapter.name} 页面且状态正常后，再回到终端按 Enter。"
        )
    finally:
        engine.stop()
    print(f"\n✅ 浏览器登录状态已保存到 {_profile_dir_for_site(site)}")


def cmd_search(args):
    """通过 Patchright 浏览器检索站点。"""
    site = args.site.lower()
    try:
        adapter = get_adapter(site)
    except KeyError:
        print(f"❌ 未知站点: {site}，支持: {', '.join(JOURNAL_CONFIGS)}")
        sys.exit(1)
    if not adapter.supports_search:
        print(f"❌ {adapter.key} adapter is registered but search is not implemented yet")
        sys.exit(1)

    cm = CookieManager(COOKIE_FILE)
    cookies_loaded = cm.load()
    if adapter.requires_login and not cookies_loaded:
        print(f"❌ 请先运行: python main.py login --site {site}")
        sys.exit(1)

    storage = StorageManager(DATA_DIR, site=site)
    filters = _search_filters_from_args(args)
    search_cursor_key = None
    search_cursor_options = _search_cursor_options(filters)
    if _adapter_supports_search_cursor(adapter):
        search_cursor_key = storage.search_cursor_key(
            site=site,
            query=args.query,
            year_from=args.year_from,
            year_to=args.year_to,
            options=search_cursor_options,
        )
        if getattr(args, "reset_search_cursor", False):
            storage.reset_search_cursor(site, search_cursor_key)
            log.info("已重置 %s 搜索游标", adapter.name)
        if getattr(args, "start_offset", None) is not None:
            filters.start_offset = max(0, int(args.start_offset))
            log.info("%s 搜索从指定 offset=%s 开始", adapter.name, filters.start_offset)
        elif getattr(args, "resume_search", False):
            cursor = storage.get_search_cursor(site, search_cursor_key)
            if cursor:
                filters.start_offset = max(0, int(cursor["next_offset"]))
                log.info("%s 续搜: 从 offset=%s 开始", adapter.name, filters.start_offset)
            else:
                log.info("%s 续搜: 未找到历史游标，从 offset=0 开始", adapter.name)
    run_id = storage.create_run(
        site=site,
        query=args.query,
        year_from=args.year_from,
        year_to=args.year_to,
        max_results=args.max,
        options={
            **_content_options_from_args(args),
            "journals": list(getattr(args, "journal", []) or []),
            "journal_family": getattr(args, "journal_family", "") or "",
            "resume_search": getattr(args, "resume_search", False),
            "start_offset": filters.start_offset,
        },
        run_type="search",
    )

    profile_dir = None
    if getattr(args, "fresh_browser_profile", False):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        profile_dir = Path(__file__).parent / "browser_profile_runs" / stamp
    else:
        profile_dir = _profile_dir_for_site(site)
    engine = BrowserEngine(
        cm,
        profile_dir=profile_dir,
        inject_cookies=getattr(args, "inject_browser_cookies", False),
    )
    try:
        engine.start(domain=_domain_from_url(adapter.search_base))
        results = adapter.search(
            engine,
            query=args.query,
            year_from=args.year_from,
            year_to=args.year_to,
            max_results=args.max,
            filters=filters,
        )
    finally:
        engine.stop()

    if _adapter_supports_search_cursor(adapter) and search_cursor_key:
        next_offset = int(getattr(adapter, "last_search_next_offset", filters.start_offset) or 0)
        finished = bool(getattr(adapter, "last_search_finished", False))
        page_size = int(getattr(adapter, "last_search_page_size", 25) or 25)
        storage.upsert_search_cursor(
            site=site,
            search_key=search_cursor_key,
            query=args.query,
            year_from=args.year_from,
            year_to=args.year_to,
            options=search_cursor_options,
            page_size=page_size,
            next_offset=next_offset,
            total_seen=next_offset,
            finished=finished,
            last_run_id=run_id,
        )
        log.info("%s 搜索游标已更新: next_offset=%s finished=%s", adapter.name, next_offset, finished)

    return _handle_search_results(results, cm, args, storage, run_id)


def _handle_search_results(results, cm, args, storage: StorageManager, run_id: str):
    if not results:
        storage.finish_run(run_id)
        print("未找到任何结果，请检查关键词或 Cookie 是否有效")
        sys.exit(0)

    urls = [r.url for r in results]
    run_url_file = storage.save_run_urls(run_id, urls)
    compat_url_file = None
    if getattr(args, "output_urls", None):
        compat_url_file = Path(args.output_urls)
        compat_url_file.write_text("\n".join(urls), encoding="utf-8")
    storage.save_search_results(run_id, results)
    storage.add_run_items(run_id, urls, results=results)
    collection_id = _collection_for_search(storage, run_id)
    storage.add_collection_search_results(collection_id, results)
    topic_collection_id = _topic_collection_for_search(storage, run_id, args)
    if topic_collection_id:
        run = storage.run_info(run_id) or {}
        storage.add_topic_collection_search_results(
            topic_collection_id,
            site=run.get("site") or storage.site,
            results=results,
            source_run_id=run_id,
            source_collection_id=collection_id,
            source_query=run.get("query") or "",
        )
    print(f"\n✅ 找到 {len(results)} 篇，URL 已保存至 {run_url_file}")
    if compat_url_file:
        print(f"兼容 URL 文件: {compat_url_file}")
    print(f"Run ID: {run_id}")

    # 打印前 10 条预览
    print("\n【检索结果预览（前10条）】")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:3d}. {r.title[:65]}")
        print(f"       {r.url}")
    if len(results) > 10:
        print(f"  ... 共 {len(results)} 条")

    # 是否立即爬取
    if args.crawl:
        print(f"\n即将爬取全部 {len(results)} 篇...")
        _do_browser_crawl(cm, urls, args, storage=storage, run_id=run_id, site=args.site.lower())
    else:
        storage.finish_run(run_id)
        print(f"\n如需爬取，运行:\n  python main.py crawl --file {run_url_file}")


def cmd_crawl(args):
    """通过 Patchright 浏览器爬取文章（URL 来源：文件 / 直接指定）。"""
    cm = CookieManager(COOKIE_FILE)
    cm.load()

    items = _collect_crawl_items(args)

    print(f"\n共 {len(items)} 个URL待爬取")
    _do_browser_crawl_items(cm, items, args)


def cmd_status(args):
    """查看爬取进度。"""
    storage = StorageManager(DATA_DIR)
    run_id = getattr(args, "run_id", None) or storage.latest_run_id()
    storage.print_run_summary(run_id)
    storage.generate_report(run_id)
    report_path = storage.run_dir(run_id) / "report.md" if run_id else DATA_DIR / "report.md"
    print(f"\n报告已更新: {report_path}")


def cmd_list_sites(args):
    """列出支持的期刊站点。"""
    print("\n支持的期刊站点：\n")
    for key, cfg in JOURNAL_CONFIGS.items():
        print(f"  {key:<15} {cfg['name']}")
    print()


def cmd_collections(args):
    storage = StorageManager(DATA_DIR)
    action = getattr(args, "collections_command", "")
    if action == "list":
        rows = storage.list_topic_collections()
        print("\n主题集合：\n")
        for row in rows:
            print(f"  {row['slug']:<32} items={row['item_count'] or 0} articles={row['article_count'] or 0}")
        print()
        return
    if action == "show":
        row = storage.topic_collection_info(args.collection)
        if not row:
            print(f"未找到主题集合: {args.collection}")
            sys.exit(1)
        print(f"\n主题集合: {row['slug']}")
        print(f"标题: {row['title'] or ''}")
        print(f"条目: {row['item_count'] or 0}")
        print(f"已有文章: {row['article_count'] or 0}")
        print(f"目录: {storage.topic_collection_dir(row['id'])}")
        return
    if action == "import-search":
        topic_id = storage.import_search_collection_to_topic(
            topic_slug=args.collection,
            site=args.site,
            search_slug=args.search,
            topic_title=getattr(args, "collection_title", "") or "",
        )
        print(f"已导入到主题集合: {storage.topic_collection_dir(topic_id)}")
        return
    if action == "refresh":
        row = storage.topic_collection_info(args.collection)
        if not row:
            print(f"未找到主题集合: {args.collection}")
            sys.exit(1)
        storage.refresh_topic_collection_exports(row["id"])
        print(f"已刷新主题集合: {storage.topic_collection_dir(row['id'])}")
        return
    print("请指定 collections 子命令")
    sys.exit(1)


def _collect_crawl_urls(args) -> list[str]:
    return [item.url for item in _collect_crawl_items(args)]


def _collect_crawl_items(args) -> list[CrawlItem]:
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            raw_urls = [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
    elif args.url:
        raw_urls = [args.url]
    else:
        print("请指定 --file 或 --url")
        sys.exit(1)

    items = []
    seen = set()
    for raw_url in raw_urls:
        try:
            adapter = get_adapter(args.site) if getattr(args, "site", None) else detect_adapter(raw_url)
        except (KeyError, ValueError) as exc:
            items.append(CrawlItem(site="unknown", url=raw_url, error=str(exc)))
            continue
        url = adapter.normalize_url(raw_url)
        key = (adapter.key, url)
        if not url or key in seen:
            continue
        seen.add(key)
        items.append(CrawlItem(site=adapter.key, url=url))
    return items


def _do_browser_crawl_items(cm: CookieManager, items: list[CrawlItem], args):
    grouped: dict[str, list[str]] = {}
    failed_unknown = [item for item in items if item.site == "unknown"]
    for item in items:
        if item.site == "unknown":
            continue
        grouped.setdefault(item.site, []).append(item.url)
    if failed_unknown:
        storage = StorageManager(DATA_DIR, site="unknown")
        run_id = storage.create_run(
            site="unknown",
            max_results=len(failed_unknown),
            options={"reason": "site detection failed"},
            run_type="crawl",
        )
        urls = [item.url for item in failed_unknown]
        storage.add_run_items(run_id, urls)
        for item in failed_unknown:
            storage.mark_failed(run_id, item.url, item.error or "无法识别站点")
        storage.finish_run(run_id, status="failed")
        storage.print_run_summary(run_id)
    for site, urls in grouped.items():
        _do_browser_crawl(cm, urls, args, site=site)


def _do_browser_crawl(
    cm: CookieManager,
    urls: list[str],
    args,
    storage: StorageManager | None = None,
    run_id: str | None = None,
    site: str | None = None,
):
    site = site or getattr(args, "site", None) or detect_adapter(urls[0]).key
    adapter = get_adapter(site)
    storage = storage or StorageManager(DATA_DIR, site=site)
    if not run_id:
        run_id = storage.create_run(
            site=site,
            query=getattr(args, "query", ""),
            year_from=getattr(args, "year_from", None),
            year_to=getattr(args, "year_to", None),
            max_results=getattr(args, "max", len(urls)),
            options={
                **_content_options_from_args(args),
                "source_file": getattr(args, "file", ""),
                "source_url": getattr(args, "url", ""),
            },
            run_type="crawl",
        )
    collection_id = _collection_for_crawl(storage, args, site=site, urls=urls)
    storage.attach_run_to_collection(run_id, collection_id)
    topic_collection_id = _topic_collection_for_crawl(storage, args, run_id)
    if topic_collection_id:
        storage.add_topic_collection_search_results(
            topic_collection_id,
            site=site,
            results=[SearchResult(url=url, title="", year="") for url in urls],
            source_run_id=run_id,
            source_collection_id=collection_id,
            source_query=getattr(args, "query", "") or "",
        )
    storage.add_run_items(run_id, urls)
    pending = [u for u in storage.pending_urls(run_id) if u in set(urls)]
    log.info(f"浏览器待处理: {len(pending)} / {len(urls)} 篇")

    opts = _content_options_from_args(args)
    figures_only = bool(getattr(args, "figures_only", False))
    overwrite_figures = bool(getattr(args, "overwrite_figures", False))
    if figures_only:
        opts["figures"] = True

    success = failed = skipped = 0
    engine = BrowserEngine(
        cm,
        profile_dir=_profile_dir_for_site(site),
        inject_cookies=getattr(args, "inject_browser_cookies", False),
    )
    try:
        engine.start(domain=adapter.article_domain)
        parser = ArticleParser(BinaryDownloadSession(cm), storage, adapter=adapter, browser=engine)
        for i, url in enumerate(pending, 1):
            log.info(f"\n[{i}/{len(pending)}] {url}")
            if figures_only:
                if not storage.article_exists(url):
                    log.info("  ↩ 未找到已保存文章，无法补图")
                    storage.mark_failed(run_id, url, "figures-only requires existing article")
                    failed += 1
                    continue
                try:
                    html = engine.open_article(url)
                    ok = parser.refresh_figures(url, html, options=opts, overwrite=overwrite_figures)
                    article_id = storage.find_article_id(url, site=site)
                    if ok:
                        storage.mark_done(run_id, url, article_id=article_id)
                        storage.add_article_to_collection(collection_id, article_id, url=url)
                        if topic_collection_id:
                            storage.add_article_to_topic_collection(topic_collection_id, site, article_id, url=url, source_run_id=run_id, source_collection_id=collection_id)
                        success += 1
                    else:
                        storage.mark_failed(run_id, url, "figure refresh returned false")
                        failed += 1
                except KeyboardInterrupt:
                    log.info("\n用户中断，保存进度后退出...")
                    storage.print_run_summary(run_id)
                    storage.finish_run(run_id, status="failed")
                    sys.exit(0)
                except Exception as e:
                    log.error(f"  ✗ 补图异常: {e}", exc_info=True)
                    storage.mark_failed(run_id, url, str(e))
                    failed += 1
                continue
            if storage.article_exists(url):
                log.info("  ↩ 已存在，跳过")
                storage.mark_skipped(run_id, url)
                article_id = storage.find_article_id(url, site=site)
                storage.add_article_to_collection(collection_id, article_id, url=url, status="skipped")
                if topic_collection_id:
                    storage.add_article_to_topic_collection(topic_collection_id, site, article_id, url=url, status="skipped", source_run_id=run_id, source_collection_id=collection_id)
                skipped += 1
                continue
            try:
                html = engine.open_article(url)
                ok = parser.parse_html(url, html, options=opts)
                if ok:
                    article_id = storage.last_article_id()
                    storage.mark_done(run_id, url, article_id=article_id)
                    storage.add_article_to_collection(collection_id, article_id, url=url)
                    if topic_collection_id:
                        storage.add_article_to_topic_collection(topic_collection_id, site, article_id, url=url, source_run_id=run_id, source_collection_id=collection_id)
                    success += 1
                else:
                    storage.mark_failed(run_id, url, "parse returned false")
                    failed += 1
            except KeyboardInterrupt:
                log.info("\n用户中断，保存进度后退出...")
                storage.print_run_summary(run_id)
                storage.finish_run(run_id, status="failed")
                sys.exit(0)
            except Exception as e:
                log.error(f"  ✗ 浏览器爬取异常: {e}", exc_info=True)
                storage.mark_failed(run_id, url, str(e))
                failed += 1
    finally:
        engine.stop()

    log.info(
        f"\n{'═'*50}\n"
        f"  浏览器爬取完成\n"
        f"  ✓ 成功: {success}\n"
        f"  ✗ 失败: {failed}\n"
        f"  → 跳过: {skipped}\n"
        f"{'═'*50}"
    )
    storage.finish_run(run_id, status="failed" if failed and not success else "done")
    storage.generate_report(run_id)
    storage.print_run_summary(run_id)


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────
def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or url


def _profile_dir_for_site(site: str) -> Path:
    return Path(__file__).parent / "browser_profiles" / site.lower()


def _collection_for_search(storage: StorageManager, run_id: str) -> int:
    existing = storage.collection_for_run(run_id)
    if existing:
        return existing
    run = storage.run_info(run_id) or {}
    collection_id = storage.create_or_get_collection(
        site=run.get("site") or storage.site,
        query=run.get("query") or "",
        year_from=run.get("year_from"),
        year_to=run.get("year_to"),
        max_results=run.get("max_results"),
        options=run.get("options") or {},
    )
    storage.attach_run_to_collection(run_id, collection_id)
    return collection_id


def _topic_collection_for_search(storage: StorageManager, run_id: str, args) -> int | None:
    slug = getattr(args, "collection", "") or ""
    if not slug:
        return None
    topic_id = storage.create_or_get_topic_collection(
        slug=slug,
        title=getattr(args, "collection_title", "") or "",
    )
    storage.attach_run_to_topic_collection(run_id, topic_id)
    return topic_id


def _topic_collection_for_crawl(storage: StorageManager, args, run_id: str) -> int | None:
    slug = getattr(args, "collection", "") or ""
    if slug:
        topic_id = storage.create_or_get_topic_collection(slug=slug)
        storage.attach_run_to_topic_collection(run_id, topic_id)
        return topic_id
    source_run_id = _run_id_from_urls_file(getattr(args, "file", ""))
    if source_run_id:
        existing = storage.topic_collection_for_run(source_run_id)
        if existing:
            storage.attach_run_to_topic_collection(run_id, existing)
            return existing
    return None


def _collection_for_crawl(storage: StorageManager, args, site: str, urls: list[str]) -> int:
    source_run_id = _run_id_from_urls_file(getattr(args, "file", ""))
    if source_run_id:
        existing = storage.collection_for_run(source_run_id)
        if existing:
            return existing
    return storage.create_or_get_collection(
        site=site,
        query=getattr(args, "query", "") or "",
        year_from=getattr(args, "year_from", None),
        year_to=getattr(args, "year_to", None),
        max_results=getattr(args, "max", len(urls)),
        options={
            **_content_options_from_args(args),
            "source_file": getattr(args, "file", ""),
            "source_url": getattr(args, "url", ""),
            "journals": list(getattr(args, "journal", []) or []),
            "journal_family": getattr(args, "journal_family", "") or "",
        },
    )


def _run_id_from_urls_file(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if p.name != "urls.txt" or p.parent.name == "":
        return None
    if p.parent.parent.name != "runs":
        return None
    return p.parent.name


def _content_options_from_args(args) -> dict:
    return {
        "html":     getattr(args, "html",     True),
        "figures":  getattr(args, "figures",  True),
        "tables":   getattr(args, "tables",   True),
        "fulltext": getattr(args, "fulltext", True),
        "asset_browser_fallback": getattr(args, "asset_browser_fallback", True),
        "max_figure_candidates_per_figure": getattr(args, "max_figure_candidates_per_figure", 4),
        "min_image_bytes": getattr(args, "min_image_bytes", 1000),
        "asset_timeout": getattr(args, "asset_timeout", 30),
    }


def _search_filters_from_args(args) -> SearchFilters:
    return SearchFilters(
        journals=list(getattr(args, "journal", []) or []),
        journal_family=getattr(args, "journal_family", "") or "",
        start_offset=max(0, int(getattr(args, "start_offset", 0) or 0)),
    )


def _search_cursor_options(filters: SearchFilters) -> dict:
    return {
        "journals": list(filters.journals or []),
        "journal_family": filters.journal_family or "",
        "sort": filters.sort or "relevance",
    }


def _adapter_supports_search_cursor(adapter) -> bool:
    return bool(getattr(adapter, "supports_search_cursor", False))


# ─────────────────────────────────────────────
#  CLI 参数解析
# ─────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python main.py",
        description="学术文献爬虫 · Cookie接力架构",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="调试日志")

    sub = p.add_subparsers(dest="command")

    # login
    s_login = sub.add_parser("login", help="打开 Patchright 浏览器完成站点登录")
    s_login.add_argument("--site", required=True,
                         help=f"目标站点: {', '.join(JOURNAL_CONFIGS)}")

    # search
    s_search = sub.add_parser("search", help="检索期刊并（可选）爬取")
    s_search.add_argument("--site",      required=True)
    s_search.add_argument("--query",     required=True, help="检索关键词")
    s_search.add_argument("--year-from", type=int, default=2024)
    s_search.add_argument("--year-to",   type=int, default=2025)
    s_search.add_argument("--max",       type=int, default=200, help="最多爬取篇数")
    s_search.add_argument("--output-urls", default=None,
                          help="额外写出一份兼容 URL 列表；默认只写入当前 run 目录")
    s_search.add_argument("--collection", default="",
                          help="追加到人工主题集合，例如 nanofiltration-membrane")
    s_search.add_argument("--collection-title", default="",
                          help="主题集合显示标题，仅创建/更新主题集合时使用")
    s_search.add_argument("--crawl",     action="store_true", help="检索后立即爬取")
    s_search.add_argument("--browser",   action="store_true", help="兼容参数；搜索始终使用 Patchright 浏览器")
    s_search.add_argument("--fresh-browser-profile", action="store_true",
                          help="浏览器搜索使用一次新的干净 Chrome profile")
    s_search.add_argument("--inject-browser-cookies", action="store_true",
                          help="将 cookies.json 注入浏览器搜索 profile（默认不注入）")
    s_search.add_argument("--resume-search", action="store_true",
                          help="支持续搜的站点: 从同一检索条件上次保存的 offset 继续检索")
    s_search.add_argument("--start-offset", type=int, default=None,
                          help="支持续搜的站点: 手动指定起始 offset，例如 500")
    s_search.add_argument("--reset-search-cursor", action="store_true",
                          help="支持续搜的站点: 清除同一检索条件的续搜 offset 后再检索")
    s_search.add_argument(
        "--journal",
        action="append",
        default=[],
        help="Nature 子刊过滤，可重复指定，例如 --journal nc --journal npjcompumats",
    )
    s_search.add_argument(
        "--journal-family",
        default="",
        help="Nature 子刊族过滤，例如 npj",
    )
    _add_content_flags(s_search)

    # crawl
    s_crawl = sub.add_parser("crawl", help="爬取文章")
    grp = s_crawl.add_mutually_exclusive_group(required=True)
    grp.add_argument("--file", help="URL列表文件（每行一个）")
    grp.add_argument("--url",  help="单个文章URL")
    s_crawl.add_argument("--site", help=f"强制指定站点: {', '.join(JOURNAL_CONFIGS)}")
    s_crawl.add_argument("--collection", default="",
                         help="追加到人工主题集合，例如 nanofiltration-membrane")
    s_crawl.add_argument("--browser", action="store_true", help="兼容参数；爬取始终使用 Patchright 浏览器")
    s_crawl.add_argument("--inject-browser-cookies", action="store_true",
                         help="将 cookies.json 注入浏览器 crawl profile（默认不注入）")
    s_crawl.add_argument("--figures-only", action="store_true",
                         help="只为已存在文章补充图片；不会重写正文、HTML 或表格")
    s_crawl.add_argument("--overwrite-figures", action="store_true",
                         help="配合 --figures-only 使用，先清理旧图片再重新下载")
    _add_content_flags(s_crawl)

    # status
    s_status = sub.add_parser("status", help="查看爬取进度")
    s_status.add_argument("--run-id", help="查看指定 run 的进度")

    # sites
    sub.add_parser("sites", help="列出支持的期刊站点")

    # collections
    s_collections = sub.add_parser("collections", help="管理人工主题集合")
    col_sub = s_collections.add_subparsers(dest="collections_command")
    col_sub.add_parser("list", help="列出主题集合")
    col_show = col_sub.add_parser("show", help="查看主题集合")
    col_show.add_argument("--collection", required=True)
    col_import = col_sub.add_parser("import-search", help="导入已有站点检索集合到主题集合")
    col_import.add_argument("--site", required=True)
    col_import.add_argument("--search", required=True, help="articles/{site}/searches 下的 collection slug")
    col_import.add_argument("--collection", required=True, help="目标主题集合 slug")
    col_import.add_argument("--collection-title", default="", help="目标主题集合标题")
    col_refresh = col_sub.add_parser("refresh", help="刷新主题集合导出文件")
    col_refresh.add_argument("--collection", required=True)

    return p


def _add_content_flags(p):
    """向 search/crawl 子命令添加内容选择标志。"""
    g = p.add_argument_group("内容选择（默认保存 HTML、正文、图片、表格）")
    g.add_argument("--no-html",     dest="html",     action="store_false")
    g.add_argument("--no-figures",  dest="figures",  action="store_false")
    g.add_argument("--no-tables",   dest="tables",   action="store_false")
    g.add_argument("--no-fulltext", dest="fulltext", action="store_false")
    g.add_argument("--asset-browser-fallback", dest="asset_browser_fallback", action="store_true",
                   help="允许使用浏览器登录上下文兜底下载图片")
    g.add_argument("--no-asset-browser-fallback", dest="asset_browser_fallback", action="store_false",
                   help="禁用浏览器兜底下载")
    g.add_argument("--max-figure-candidates-per-figure", type=int, default=4)
    g.add_argument("--min-image-bytes", type=int, default=1000)
    g.add_argument("--asset-timeout", type=int, default=30)
    p.set_defaults(
        html=True,
        figures=True,
        tables=True,
        fulltext=True,
        asset_browser_fallback=True,
    )


# ─────────────────────────────────────────────
#  入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    parser = build_parser()
    args   = parser.parse_args()

    setup_logging(getattr(args, "verbose", False))

    CMD_MAP = {
        "login":  cmd_login,
        "search": cmd_search,
        "crawl":  cmd_crawl,
        "status": cmd_status,
        "sites":  cmd_list_sites,
        "collections": cmd_collections,
    }

    if not args.command:
        parser.print_help()
        print("\n【示例】")
        print("  python main.py login --site sciencedirect")
        print("  python main.py search --site sciencedirect "
              '--query "transparent conductive oxide" '
              "--year-from 2024 --year-to 2025 --max 100")
        print("  python main.py crawl --file data/runs/{run_id}/urls.txt --no-figures")
        sys.exit(0)

    CMD_MAP[args.command](args)
