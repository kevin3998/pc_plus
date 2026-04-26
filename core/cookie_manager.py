"""Cookie persistence shared by Patchright and secondary asset downloads."""

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("cookie")


class CookieManager:
    def __init__(self, cookie_file: Path):
        self.cookie_file = cookie_file
        self.cookies: dict = {}
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────
    #  加载 / 保存
    # ─────────────────────────────────────────────
    def load(self) -> bool:
        if not self.cookie_file.exists():
            log.warning(f"Cookie文件不存在: {self.cookie_file}")
            return False
        try:
            with open(self.cookie_file, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                self.cookies = {i["name"]: i.get("value", "") for i in raw if "name" in i}
            elif isinstance(raw, dict):
                self.cookies = raw
            else:
                return False

            log.info(f"已加载 {len(self.cookies)} 个Cookie (来自文件)")
            # 记录关键 CF Cookie
            for key in ("cf_clearance", "__cf_bm", "sd_access", "sd_session_id"):
                if key in self.cookies:
                    log.debug(f"  {key}: 已设置")
            return True
        except Exception as e:
            log.error(f"Cookie文件读取失败: {e}")
            return False

    def _save(self):
        with self._lock:
            self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                json.dump(self.cookies, f, ensure_ascii=False, indent=2)
        log.debug(f"Cookie已保存 ({len(self.cookies)} 条)")

    def sync_from_session(self, session_cookies: dict):
        """Persist cookies collected from the browser context."""
        with self._lock:
            self.cookies.update(session_cookies)
        self._save()
