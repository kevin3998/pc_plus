"""
core/storage.py
─────────────────────────────────────────────────────────────
输出目录结构（按文章 DOI/标题哈希）：

output/
  {slug}/                     ← DOI 转义或标题 MD5
    meta.json                 ← 完整元数据
    fulltext.md               ← 正文（Markdown）
    abstract.txt              ← 摘要纯文本
    article.html              ← 原始 HTML 全文（如下载成功）
    article.pdf               ← PDF（如有权限）
    figures/
      fig_001.jpg             ← 图片
      fig_001_caption.txt     ← 图注
      fig_001_label.txt       ← 图号（Figure 1 等）
    tables/
      table_001.csv           ← 表格 CSV
      table_001.html          ← 原始 HTML 表格
      table_001_caption.txt   ← 表题
    supplementary/            ← 补充材料（SI）
      si_001.*

crawl_state.json              ← 断点续爬状态
index.json                    ← 全库索引（自动维护）
─────────────────────────────────────────────────────────────
"""

import csv
import hashlib
import json
import logging
import re
from io import StringIO
from pathlib import Path
from datetime import datetime

log = logging.getLogger("storage")


def _slug(doi_or_url: str) -> str:
    """生成文件夹名：优先用 DOI 转义，否则 MD5。"""
    doi_or_url = doi_or_url.strip()
    # 10.1016/j.xxx → 10.1016-j.xxx（去除斜杠）
    if doi_or_url.startswith("10."):
        return re.sub(r"[/\\:*?\"<>|]", "-", doi_or_url)[:80]
    return hashlib.md5(doi_or_url.encode()).hexdigest()[:16]


class StorageManager:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base / "index.json"
        self._index: list = self._load_index()

    # ─────────────────────────────────────────────
    #  目录初始化
    # ─────────────────────────────────────────────
    def article_dir(self, doi_or_url: str) -> Path:
        d = self.base / _slug(doi_or_url)
        for sub in ("figures", "tables", "supplementary"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def article_exists(self, doi_or_url: str) -> bool:
        """用于断点续爬：判断是否已爬取。"""
        d = self.base / _slug(doi_or_url)
        return (d / "meta.json").exists()

    # ─────────────────────────────────────────────
    #  各类内容保存
    # ─────────────────────────────────────────────
    def save_meta(self, adir: Path, meta: dict):
        meta["_saved_at"] = datetime.now().isoformat()
        _write_json(adir / "meta.json", meta)
        # 更新全库索引
        self._update_index(meta)

    def save_fulltext(self, adir: Path, markdown: str):
        _write_text(adir / "fulltext.md", markdown)

    def save_abstract(self, adir: Path, text: str):
        _write_text(adir / "abstract.txt", text)

    def save_html(self, adir: Path, html: str):
        _write_text(adir / "article.html", html)

    def save_pdf(self, adir: Path, data: bytes) -> bool:
        if not data:
            return False
        path = adir / "article.pdf"
        path.write_bytes(data)
        log.info(f"    ✓ PDF 已保存 ({len(data)//1024} KB)")
        return True

    def save_figure(self, adir: Path, idx: int, data: bytes,
                    ext: str, caption: str = "", label: str = ""):
        stem = f"fig_{idx:03d}"
        (adir / "figures" / f"{stem}{ext}").write_bytes(data)
        if caption:
            _write_text(adir / "figures" / f"{stem}_caption.txt", caption)
        if label:
            _write_text(adir / "figures" / f"{stem}_label.txt", label)
        log.info(f"    ✓ 图 {idx} 已保存 ({len(data)//1024} KB) {label}")

    def save_table(self, adir: Path, idx: int,
                   html: str, rows: list[list[str]], caption: str = ""):
        stem = f"table_{idx:03d}"
        _write_text(adir / "tables" / f"{stem}.html", html)
        if rows:
            buf = StringIO()
            w = csv.writer(buf, quoting=csv.QUOTE_ALL)
            w.writerows(rows)
            _write_text(adir / "tables" / f"{stem}.csv", buf.getvalue())
        if caption:
            _write_text(adir / "tables" / f"{stem}_caption.txt", caption)
        log.info(f"    ✓ 表 {idx} 已保存  {caption[:40]}")

    def save_supplementary(self, adir: Path, idx: int,
                           data: bytes, filename: str):
        dest = adir / "supplementary" / f"si_{idx:03d}_{filename}"
        dest.write_bytes(data)
        log.info(f"    ✓ 补充材料 {idx}: {filename}")

    # ─────────────────────────────────────────────
    #  全库索引
    # ─────────────────────────────────────────────
    def _load_index(self) -> list:
        if self.index_file.exists():
            try:
                with open(self.index_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _update_index(self, meta: dict):
        doi = meta.get("doi", meta.get("url", ""))
        # 去重
        self._index = [i for i in self._index if i.get("doi") != doi]
        self._index.append({
            "doi":     doi,
            "title":   meta.get("title", ""),
            "authors": meta.get("authors", [])[:3],
            "journal": meta.get("journal", ""),
            "year":    meta.get("year", ""),
            "url":     meta.get("url", ""),
            "saved":   meta.get("_saved_at", ""),
        })
        _write_json(self.index_file, self._index)

    def generate_report(self) -> str:
        """生成下载摘要报告。"""
        lines = [
            f"# 爬取报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"共 {len(self._index)} 篇文章\n",
            "| # | 标题 | 期刊 | 年份 | DOI |",
            "|---|------|------|------|-----|",
        ]
        for i, item in enumerate(self._index, 1):
            title = item.get("title", "")[:50]
            journal = item.get("journal", "")[:20]
            year = item.get("year", "")
            doi = item.get("doi", "")
            lines.append(f"| {i} | {title} | {journal} | {year} | {doi} |")

        report = "\n".join(lines)
        _write_text(self.base / "report.md", report)
        return report


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────
def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)