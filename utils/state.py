"""
utils/state.py  —  断点续爬状态管理
"""

import json
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("state")


class CrawlState:
    """
    记录每个 URL 的爬取状态，支持：
      · 断点续爬（跳过已成功的）
      · 失败重试计数
      · 任务进度显示
    """

    STATUS_PENDING = "pending"
    STATUS_DONE    = "done"
    STATUS_FAILED  = "failed"
    STATUS_SKIPPED = "skipped"

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"urls": {}, "meta": {}}

    def _save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def add_urls(self, urls: list[str]):
        """批量注册 URL（不覆盖已有状态）。"""
        for url in urls:
            if url not in self._data["urls"]:
                self._data["urls"][url] = {
                    "status":  self.STATUS_PENDING,
                    "retries": 0,
                    "added":   datetime.now().isoformat(),
                }
        self._save()
        log.info(f"注册 {len(urls)} 个URL到任务队列")

    def mark_done(self, url: str):
        self._update(url, status=self.STATUS_DONE)

    def mark_failed(self, url: str):
        entry = self._data["urls"].get(url, {})
        retries = entry.get("retries", 0) + 1
        self._update(url, status=self.STATUS_FAILED, retries=retries)

    def mark_skipped(self, url: str):
        self._update(url, status=self.STATUS_SKIPPED)

    def _update(self, url: str, **kwargs):
        if url not in self._data["urls"]:
            self._data["urls"][url] = {}
        self._data["urls"][url].update(kwargs)
        self._data["urls"][url]["updated"] = datetime.now().isoformat()
        self._save()

    def pending_urls(self) -> list[str]:
        """返回待处理 URL（pending + 失败重试 < 3次）。"""
        result = []
        for url, info in self._data["urls"].items():
            status = info.get("status")
            if status == self.STATUS_PENDING:
                result.append(url)
            elif status == self.STATUS_FAILED and info.get("retries", 0) < 3:
                result.append(url)
        return result

    def summary(self) -> dict:
        counts = {
            "total":   0,
            "done":    0,
            "failed":  0,
            "pending": 0,
            "skipped": 0,
        }
        for info in self._data["urls"].values():
            counts["total"] += 1
            status = info.get("status", "pending")
            if status in counts:
                counts[status] += 1
            else:
                counts["pending"] += 1
        return counts

    def print_summary(self):
        s = self.summary()
        log.info(
            f"\n{'─'*40}\n"
            f"  任务总计: {s['total']}\n"
            f"  ✓ 完成:   {s['done']}\n"
            f"  ✗ 失败:   {s['failed']}\n"
            f"  ↻ 待处理: {s['pending']}\n"
            f"  → 跳过:   {s['skipped']}\n"
            f"{'─'*40}"
        )