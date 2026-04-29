# PC Plus Codex 文献爬取工具

面向 ScienceDirect、SpringerLink、Nature 等站点的文献检索与文章资产保存工具。当前采用浏览器登录态 + SQLite 索引 + 文件资产库的保存方式，适合按检索条件长期归档文献、正文、图片、表格和元数据。

## 当前支持能力

### 站点

查看支持站点：

```bash
python main.py sites
```

当前注册站点：

- `sciencedirect`：支持登录、检索、爬取、正文解析、图片/表格保存。
- `springer`：支持站点适配、检索和爬取基础流程。
- `nature`：支持 Nature Portfolio 搜索、正文完整性判断、图片候选提取，并可按 Nature Communications / npj 子刊过滤。

### CLI 子命令

- `login`：打开浏览器，让用户手动登录或完成验证码。
- `search`：按站点、关键词、年份检索文章。
- `crawl`：爬取 URL 文件或单篇文章。
- `status`：查看最近或指定 run 的进度。
- `sites`：列出支持站点。

### 默认保存内容

默认保存：

- HTML 原文：`raw/article.html`
- 元数据：`meta.json`
- 正文 Markdown：`parsed/fulltext.md`
- 摘要：`parsed/abstract.txt`
- 图片：`assets/figures/`
- 表格：`assets/tables/`

当前不再下载 PDF 和 supplementary。旧版本产生的 `assets/pdf/`、`assets/supplementary/` 可作为历史文件手动清理；新爬取流程不会创建这两类目录。

## 安装与准备

建议在已有 Python 环境中安装依赖：

```bash
python -m pip install -r requirements.txt
patchright install chromium
```

如果使用机构账号或需要权限访问全文，建议保持登录、检索和爬取在同一网络环境下完成，例如校园网、VPN 或机构远程访问环境。

## 标准使用流程

### 1. 登录站点

ScienceDirect 示例：

```bash
python main.py login --site sciencedirect
```

命令会打开 Chrome。你需要在浏览器里完成登录、机构认证或验证码，然后回到终端按 Enter。

登录状态会保存到：

```text
browser_profiles/sciencedirect/
```

Springer 示例：

```bash
python main.py login --site springer
```

### 2. 检索文章

```bash
python main.py search \
  --site sciencedirect \
  --query "transparent conductive oxide" \
  --year-from 2023 \
  --year-to 2024 \
  --max 20
```

Nature Communications 示例：

```bash
python main.py search \
  --site nature \
  --query "perovskite solar cells" \
  --journal nc \
  --year-from 2024 \
  --year-to 2026 \
  --max 50
```

npj 子刊族示例：

```bash
python main.py search \
  --site nature \
  --query "transparent conductive oxide" \
  --journal-family npj \
  --year-from 2024 \
  --year-to 2026 \
  --max 100
```

NC / npj 不是独立站点，当前通过 `nature` adapter 的 `--journal` 和 `--journal-family` 过滤实现。期刊别名和子刊族配置集中在 `sites/nature_journals.py`，新增子刊时优先扩展这个文件。

检索完成后会输出一个 run id，并生成 URL 文件：

```text
data/runs/{run_id}/urls.txt
```

同时会生成对应检索条件的长期分类目录：

```text
data/articles/sciencedirect/searches/{collection_slug}/
```

### 3. 爬取检索结果

使用上一步生成的 `urls.txt`：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt
```

默认会保存 HTML、正文、图片、表格。

如果不需要图片：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt --no-figures
```

如果想检索后立即爬取：

```bash
python main.py search \
  --site sciencedirect \
  --query "transparent conductive oxide" \
  --year-from 2023 \
  --year-to 2024 \
  --max 20 \
  --crawl
```

### 4. 爬取单篇文章

自动识别站点：

```bash
python main.py crawl --url "https://www.sciencedirect.com/science/article/pii/S2214860424004342"
```

强制指定站点：

```bash
python main.py crawl \
  --site sciencedirect \
  --url "https://www.sciencedirect.com/science/article/pii/S2214860424004342"
```

### 5. 查看进度

查看最近一次 run：

```bash
python main.py status
```

查看指定 run：

```bash
python main.py status --run-id {run_id}
```

## 常用参数

内容控制：

```bash
--no-html
--no-figures
--no-tables
--no-fulltext
```

资产下载控制：

```bash
--asset-browser-fallback
--no-asset-browser-fallback
--max-figure-candidates-per-figure 4
--min-image-bytes 1000
--asset-timeout 30
```

浏览器与 Cookie：

```bash
--inject-browser-cookies
--fresh-browser-profile
```

`--fresh-browser-profile` 只用于 `search`，会创建一次性的干净浏览器 profile。

### Nature 子刊正文与高清图片验证

正文验证示例（不下载图片和表格）：

```bash
python main.py crawl \
  --site nature \
  --url "PASTE_NATURE_ARTICLE_URL" \
  --no-figures \
  --no-tables
```

检查 `parsed/fulltext.md` 是否包含真实正文段落，例如 Introduction、Results、Discussion 或 Methods，不应只包含 Abstract、References、Recommended articles 或访问提示。

高清图片验证示例：

```bash
python main.py crawl \
  --site nature \
  --url "PASTE_NATURE_ARTICLE_URL" \
  --max-figure-candidates-per-figure 6 \
  --min-image-bytes 1000
```

检查 `assets/figures/` 中是否为正文图；SQLite 的 `assets.source_url` 优先应出现 `/full/`、`/lw1200/` 或其他高分辨率 `media.springernature.com` 地址。

失败图片候选不会中断整篇文章，可用以下命令查看最近失败原因：

```bash
sqlite3 data/catalog.sqlite \
  "select type,status,error,source_url,label from assets where status='failed' order by id desc limit 20;"
```

## 保存结构说明

新数据默认写入 `data/`：

```text
data/
  catalog.sqlite
  articles/
    {site}/
      _library/
        {article_key}/
          meta.json
          raw/article.html
          parsed/fulltext.md
          parsed/abstract.txt
          assets/figures/fig_001.jpg
          assets/tables/table_001.csv
          assets/tables/table_001.html
      _failed/
        {article_key}/
          meta.json
          raw/article.html
          parsed/abstract.txt
      searches/
        {collection_slug}/
          collection.json
          urls.txt
          articles.jsonl
          articles.csv
          article_links/{article_key}
  runs/
    {run_id}/
      run.json
      urls.txt
      search_results.jsonl
      report.md
```

### `_library/`

底层去重资产库。每篇成功文章真实文件只保存一份。

### `_failed/`

正文不完整、无全文权限或解析失败的文章会归档到 `_failed/{article_key}`，不写入 `articles` 表，也不会进入 `_library`。这样后续补爬同一篇文献时，不会被断点续爬误判为“已存在”。

失败目录中的 `meta.json` 会包含：

- `_status`
- `_failure_reason`
- `_failed_at` 或 `_reclassified_at`

重复失败会覆盖同一个 `_failed/{article_key}` 目录，不会无限新增重复目录。

### `searches/`

按检索条件生成的分类视图。这里保存该 query 条件下的文章清单和导出文件：

- `articles.csv`
- `articles.jsonl`
- `urls.txt`
- `collection.json`

### `article_links/`

这是指向 `_library/{article_key}` 的符号链接，不是复制文件。你点开后看到的 `meta.json/raw/parsed/assets` 实际来自 `_library` 中的真实文章目录。

验证链接：

```bash
ls -l data/articles/sciencedirect/searches/*/article_links
```

## 导出检索集合

`searches/{collection_slug}/article_links` 是指向 `_library` 的映射，不是独立副本。如果需要把某个检索集合另存为独立文件夹，使用导出脚本复制真实文章目录：

```bash
python scripts/export_collection.py \
  --site sciencedirect \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2010-2015 \
  --out /Users/chenlintao/Desktop/exported_indium_oxide_2010_2015
```

先预览，不写文件：

```bash
python scripts/export_collection.py \
  --site sciencedirect \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2010-2015 \
  --out /Users/chenlintao/Desktop/exported_indium_oxide_2010_2015 \
  --dry-run
```

合并多个分批检索集合：

```bash
python scripts/export_collection.py \
  --site sciencedirect \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2010-2015 \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2014-2015 \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2016-2019 \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2020-2025 \
  --out /Users/chenlintao/Desktop/exported_indium_oxide_2010_2025
```

如果输出目录已存在，覆盖已导出的文章目录：

```bash
python scripts/export_collection.py \
  --site sciencedirect \
  --collection indium-oxide-and-doped-and-transparent-conductive-oxide_y2020-2025 \
  --out /Users/chenlintao/Desktop/exported_indium_oxide_2020_2025 \
  --overwrite
```

导出结果结构：

```text
exported_collection/
  articles/
    001__{article_key}/
      meta.json
      raw/
      parsed/
      assets/
  manifest.csv
  manifest.jsonl
  missing.csv
  export_summary.json
```

脚本只导出已有 `parsed/fulltext.md` 的成功文章；缺少全文、没有 `article_id` 或源目录缺失的记录会写入 `missing.csv`。多 collection 导出会按 `article_id` 去重，`manifest` 中的 `source_collections` 和 `duplicate_count` 会记录来源集合和重复次数。

## 手动验证与中断

如果遇到验证码、人机验证或需要重新登录，程序会在终端提示你到浏览器中完成验证，然后按 Enter 继续。

如果手动 Ctrl-C 中断：

- 已完成的文章会保留为 `done`
- 当前未完成和后续文章会留在 run 状态里
- 失败项会记录在 SQLite 的 `run_items`
- 后续可以用相同 URL 文件重新运行 `crawl`

## 推荐测试命令

ScienceDirect 基础流程：

```bash
python main.py login --site sciencedirect

python main.py search \
  --site sciencedirect \
  --query "transparent conductive oxide" \
  --year-from 2023 \
  --year-to 2024 \
  --max 5

python main.py crawl --file data/runs/{run_id}/urls.txt

python main.py status --run-id {run_id}
```

如果只想验证元数据和正文，不下载图片：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt --no-figures
```

## 注意事项

- `data/` 已加入 `.gitignore`，运行数据不会进入 Git。
- `browser_profiles/` 保存登录态，包含浏览器缓存和账号状态，建议不要提交到 Git。
- 同一篇文章出现在多个 search collection 时，只会在 `_library/` 保存一份真实资产。
- 已存在成功文章默认会跳过重新下载，但仍会加入新的 search collection。
- 失败文章归档在 `_failed/`，不阻止后续补爬。
- PDF 和补充材料下载逻辑已移除，避免请求过多和权限问题。
- 站点权限取决于账号、机构网络和当前 IP 环境。
