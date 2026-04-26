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

  # 第三步：爬取检索结果
  python main.py crawl --file search_results.txt --no-pdf --no-figures

  # 或爬取单篇
  python main.py crawl --url https://www.sciencedirect.com/science/article/pii/XXX

  # 查看进度
  python main.py status

═══════════════════════════════════════════════════════════════
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# ── 路径修正（允许从任意目录运行）──────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    OUTPUT_DIR, COOKIE_FILE, LOG_DIR, STATE_FILE,
    JOURNAL_CONFIGS,
)
from core.cookie_manager import CookieManager
from core.browser import BrowserEngine
from core.downloader import BinaryDownloadSession
from core.storage import StorageManager
from core.parser import ArticleParser
from search.browser_search import BrowserJournalSearcher, normalize_sciencedirect_article_url
from utils.state import CrawlState


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


# ─────────────────────────────────────────────
#  各子命令实现
# ─────────────────────────────────────────────
def cmd_login(args):
    """打开 Patchright 浏览器，让用户完成站点登录并保存 profile。"""
    site = args.site.lower()
    cfg = JOURNAL_CONFIGS.get(site)
    if not cfg:
        print(f"❌ 未知站点: {site}，支持: {', '.join(JOURNAL_CONFIGS)}")
        sys.exit(1)
    if site != "sciencedirect":
        print("❌ login 目前只支持 --site sciencedirect")
        sys.exit(1)

    cm = CookieManager(COOKIE_FILE)
    cm.load()
    engine = BrowserEngine(cm)
    try:
        engine.start(domain=_domain_from_url(cfg["search_base"]))
        engine.goto(cfg["search_base"])
        engine.wait_for_user(
            "\n请在弹出的 Chrome 窗口中完成 ScienceDirect / Elsevier 登录。"
            "\n确认已回到 ScienceDirect 页面且登录状态正常后，再回到终端按 Enter。"
        )
    finally:
        engine.stop()
    print("\n✅ 浏览器登录状态已保存到 browser_profile")


def cmd_search(args):
    """通过 Patchright 浏览器检索 ScienceDirect。"""
    cm = CookieManager(COOKIE_FILE)
    if not cm.load():
        print("❌ 请先运行: python main.py login --site sciencedirect")
        sys.exit(1)

    site = args.site.lower()
    if site != "sciencedirect":
        print("❌ 当前清理后的主线只支持 --site sciencedirect")
        sys.exit(1)

    profile_dir = None
    if getattr(args, "fresh_browser_profile", False):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        profile_dir = Path(__file__).parent / "browser_profile_runs" / stamp
    engine = BrowserEngine(
        cm,
        profile_dir=profile_dir,
        inject_cookies=getattr(args, "inject_browser_cookies", False),
    )
    try:
        engine.start(domain=_domain_from_url(JOURNAL_CONFIGS[site]["search_base"]))
        searcher = BrowserJournalSearcher(engine)
        results = searcher.search(
            site       = site,
            query      = args.query,
            year_from  = args.year_from,
            year_to    = args.year_to,
            max_results= args.max,
        )
    finally:
        engine.stop()

    return _handle_search_results(results, cm, args)


def _handle_search_results(results, cm, args):
    if not results:
        print("未找到任何结果，请检查关键词或 Cookie 是否有效")
        sys.exit(0)

    # 保存 URL 列表
    url_file = Path(args.output_urls)
    urls = [r.url for r in results]
    url_file.write_text("\n".join(urls), encoding="utf-8")
    print(f"\n✅ 找到 {len(results)} 篇，URL 已保存至 {url_file}")

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
        _do_browser_crawl(cm, urls, args)
    else:
        print(f"\n如需爬取，运行:\n  python main.py crawl --file {url_file}")


def cmd_crawl(args):
    """通过 Patchright 浏览器爬取文章（URL 来源：文件 / 直接指定）。"""
    cm = CookieManager(COOKIE_FILE)
    if not cm.load():
        print("❌ 请先运行: python main.py login --site sciencedirect")
        sys.exit(1)

    urls = _collect_crawl_urls(args)

    print(f"\n共 {len(urls)} 个URL待爬取")
    _do_browser_crawl(cm, urls, args)


def cmd_status(args):
    """查看爬取进度。"""
    state = CrawlState(STATE_FILE)
    state.print_summary()

    storage = StorageManager(OUTPUT_DIR)
    report = storage.generate_report()
    print(f"\n报告已更新: {OUTPUT_DIR / 'report.md'}")


def cmd_list_sites(args):
    """列出支持的期刊站点。"""
    print("\n支持的期刊站点：\n")
    for key, cfg in JOURNAL_CONFIGS.items():
        print(f"  {key:<15} {cfg['name']}")
    print()


def _collect_crawl_urls(args) -> list[str]:
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

    urls = []
    seen = set()
    for raw_url in raw_urls:
        url = normalize_sciencedirect_article_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _do_browser_crawl(cm: CookieManager, urls: list[str], args):
    storage = StorageManager(OUTPUT_DIR)
    state   = CrawlState(STATE_FILE)
    parser  = ArticleParser(BinaryDownloadSession(cm), storage)

    state.add_urls(urls)
    pending = [u for u in state.pending_urls() if u in set(urls)]
    log.info(f"浏览器待处理: {len(pending)} / {len(urls)} 篇")

    opts = {
        "html":     getattr(args, "html",     True),
        "pdf":      getattr(args, "pdf",      True),
        "figures":  getattr(args, "figures",  True),
        "tables":   getattr(args, "tables",   True),
        "fulltext": getattr(args, "fulltext", True),
    }

    success = failed = skipped = 0
    engine = BrowserEngine(
        cm,
        inject_cookies=getattr(args, "inject_browser_cookies", False),
    )
    try:
        engine.start(domain="www.sciencedirect.com")
        for i, url in enumerate(pending, 1):
            log.info(f"\n[{i}/{len(pending)}] {url}")
            if storage.article_exists(url):
                log.info("  ↩ 已存在，跳过")
                state.mark_skipped(url)
                skipped += 1
                continue
            try:
                html = engine.open_article(url)
                ok = parser.parse_html(url, html, options=opts)
                if ok:
                    state.mark_done(url)
                    success += 1
                else:
                    state.mark_failed(url)
                    failed += 1
            except KeyboardInterrupt:
                log.info("\n用户中断，保存进度后退出...")
                state.print_summary()
                sys.exit(0)
            except Exception as e:
                log.error(f"  ✗ 浏览器爬取异常: {e}", exc_info=True)
                state.mark_failed(url)
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
    storage.generate_report()
    state.print_summary()


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────
def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or url


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
    s_search.add_argument("--output-urls", default="search_results.txt")
    s_search.add_argument("--crawl",     action="store_true", help="检索后立即爬取")
    s_search.add_argument("--browser",   action="store_true", help="兼容参数；搜索始终使用 Patchright 浏览器")
    s_search.add_argument("--fresh-browser-profile", action="store_true",
                          help="浏览器搜索使用一次新的干净 Chrome profile")
    s_search.add_argument("--inject-browser-cookies", action="store_true",
                          help="将 cookies.json 注入浏览器搜索 profile（默认不注入）")
    _add_content_flags(s_search)

    # crawl
    s_crawl = sub.add_parser("crawl", help="爬取文章")
    grp = s_crawl.add_mutually_exclusive_group(required=True)
    grp.add_argument("--file", help="URL列表文件（每行一个）")
    grp.add_argument("--url",  help="单个文章URL")
    s_crawl.add_argument("--browser", action="store_true", help="兼容参数；爬取始终使用 Patchright 浏览器")
    s_crawl.add_argument("--inject-browser-cookies", action="store_true",
                         help="将 cookies.json 注入浏览器 crawl profile（默认不注入）")
    _add_content_flags(s_crawl)

    # status
    sub.add_parser("status", help="查看爬取进度")

    # sites
    sub.add_parser("sites", help="列出支持的期刊站点")

    return p


def _add_content_flags(p):
    """向 search/crawl 子命令添加内容选择标志。"""
    g = p.add_argument_group("内容选择（默认全选）")
    g.add_argument("--no-html",     dest="html",     action="store_false")
    g.add_argument("--no-pdf",      dest="pdf",      action="store_false")
    g.add_argument("--no-figures",  dest="figures",  action="store_false")
    g.add_argument("--no-tables",   dest="tables",   action="store_false")
    g.add_argument("--no-fulltext", dest="fulltext", action="store_false")
    p.set_defaults(html=True, pdf=True, figures=True, tables=True, fulltext=True)


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
    }

    if not args.command:
        parser.print_help()
        print("\n【示例】")
        print("  python main.py login --site sciencedirect")
        print("  python main.py search --site sciencedirect "
              '--query "transparent conductive oxide" '
              "--year-from 2024 --year-to 2025 --max 100")
        print("  python main.py crawl --file search_results.txt --no-pdf --no-figures")
        sys.exit(0)

    CMD_MAP[args.command](args)
