# PC Plus Codex 文献爬取工具

面向 ScienceDirect、SpringerLink 等站点的文献检索与文章资产保存工具。当前采用浏览器登录态 + SQLite 索引 + 文件资产库的保存方式，适合按检索条件长期归档文献、正文、图片、表格和元数据。

## 当前支持能力

### 站点

查看支持站点：

```bash
python main.py sites
```

当前注册站点：

- `sciencedirect`：支持登录、检索、爬取、正文解析、图片/表格保存。
- `springer`：支持站点适配、检索和爬取基础流程。
- `nature`：已注册站点和域名识别，但搜索未完整实现。

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

默认不保存：

- PDF：需要显式加 `--pdf`
- 补充材料：需要显式加 `--supplementary`，接口已保留，后续可扩展下载逻辑

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

默认会保存 HTML、正文、图片、表格，但不会下载 PDF。

如果确实需要下载 PDF：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt --pdf
```

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
--pdf
--no-pdf
--no-figures
--no-tables
--no-fulltext
--supplementary
--no-supplementary
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
          assets/pdf/article.pdf
          assets/figures/fig_001.jpg
          assets/tables/table_001.csv
          assets/tables/table_001.html
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

底层去重资产库。每篇文章真实文件只保存一份。

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

如果需要 PDF：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt --pdf
```

如果只想验证元数据和正文，不下载图片：

```bash
python main.py crawl --file data/runs/{run_id}/urls.txt --no-figures
```

## 注意事项

- `data/` 已加入 `.gitignore`，运行数据不会进入 Git。
- `browser_profiles/` 保存登录态，包含浏览器缓存和账号状态，建议不要提交到 Git。
- 同一篇文章出现在多个 search collection 时，只会在 `_library/` 保存一份真实资产。
- 已存在文章默认会跳过重新下载，但仍会加入新的 search collection。
- PDF 和补充材料默认关闭，避免请求过多和权限问题。
- 站点权限取决于账号、机构网络和当前 IP 环境。
