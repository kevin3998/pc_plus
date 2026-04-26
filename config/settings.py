"""全局配置：当前主线只支持 ScienceDirect + Patchright 浏览器。"""

import os
from pathlib import Path

# ═══════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════
ROOT_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = ROOT_DIR / "output"
COOKIE_FILE = ROOT_DIR / "cookies.json"
LOG_DIR     = ROOT_DIR / "logs"
STATE_FILE  = ROOT_DIR / "crawl_state.json"   # 断点续爬状态

# ═══════════════════════════════════════════════════
#  下载内容选项（可在命令行覆盖）
# ═══════════════════════════════════════════════════
DOWNLOAD_HTML     = True
DOWNLOAD_PDF      = True
DOWNLOAD_FIGURES  = True
DOWNLOAD_TABLES   = True
DOWNLOAD_FULLTEXT = True

DEFAULT_USER_AGENT = os.getenv(
    "SCHOLAR_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
)

# ═══════════════════════════════════════════════════
#  站点配置
# ═══════════════════════════════════════════════════
JOURNAL_CONFIGS = {
    "sciencedirect": {
        "name": "ScienceDirect (Elsevier)",
        "search_base": "https://www.sciencedirect.com/search",
        "article_pattern": r"sciencedirect\.com/science/article/",
    },
}
