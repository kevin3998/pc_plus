"""全局配置。"""

import os
from pathlib import Path

from sites.registry import site_configs

# ═══════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════
ROOT_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = ROOT_DIR / "output"
DATA_DIR    = ROOT_DIR / "data"
CATALOG_DB  = DATA_DIR / "catalog.sqlite"
COOKIE_FILE = ROOT_DIR / "cookies.json"
LOG_DIR     = ROOT_DIR / "logs"
STATE_FILE  = ROOT_DIR / "crawl_state.json"   # 断点续爬状态

# ═══════════════════════════════════════════════════
#  下载内容选项（可在命令行覆盖）
# ═══════════════════════════════════════════════════
DOWNLOAD_HTML     = True
DOWNLOAD_PDF      = False
DOWNLOAD_FIGURES  = True
DOWNLOAD_TABLES   = True
DOWNLOAD_FULLTEXT = True
DOWNLOAD_SUPPLEMENTARY = False

DEFAULT_USER_AGENT = os.getenv(
    "SCHOLAR_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
)

# ═══════════════════════════════════════════════════
#  站点配置
# ═══════════════════════════════════════════════════
JOURNAL_CONFIGS = {
    key: value
    for key, value in site_configs().items()
}
