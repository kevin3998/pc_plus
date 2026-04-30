# PC Plus Codex 文献爬取工具

面向 ScienceDirect、SpringerLink、Nature、Wiley Online Library 等站点的文献检索与文章资产保存工具。当前采用浏览器登录态 + SQLite 索引 + 文件资产库的保存方式，适合按检索条件长期归档文献、正文、图片、表格和元数据。

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
- `wiley`：支持 Wiley Online Library 检索、offset 续搜、正文完整性判断和图片候选提取。

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

### 通用流程

1. 如站点需要登录，先运行 `login` 并在 Chrome 中完成登录、机构认证或验证码。
2. 运行 `search` 生成 URL 列表和检索集合。
3. 运行 `crawl` 保存 HTML、正文、图片、表格和元数据。
4. 用 `status` 查看 run 进度。

检索完成后会输出 run id，并生成：

```text
data/runs/{run_id}/urls.txt
data/articles/{site}/searches/{collection_slug}/
```

如果希望把不同站点、不同批次、不同 query 的结果长期归入同一个研究主题，使用人工主题集合：

```bash
python main.py search \
  --site sciencedirect \
  --query '("nanofiltration membrane" OR "hydrophobic membrane") AND desalination' \
  --year-from 2021 \
  --year-to 2025 \
  --max 500 \
  --collection nanofiltration-membrane \
  --collection-title "Nanofiltration Membrane"
```

主题集合会写入：

```text
data/collections/nanofiltration-membrane/
```

真实文章仍只保存在各站点 `_library/` 中，主题集合只保存清单和链接。

### ScienceDirect

ScienceDirect 需要登录态，建议先登录：

```bash
python main.py login --site sciencedirect
```

登录状态保存到 `browser_profiles/sciencedirect/`。ScienceDirect 容易触发风控，检索默认使用 `offset` 翻页，并在翻页之间加入保守随机等待。每页按 25 条推进。

基础检索：

```bash
python main.py search \
  --site sciencedirect \
  --query "transparent conductive oxide" \
  --year-from 2023 \
  --year-to 2024 \
  --max 100
```

分批检索时，第一次先跑前 500 条：

```bash
python main.py search \
  --site sciencedirect \
  --query '("nanofiltration membrane" OR "hydrophobic membrane") AND (desalination OR "water treatment" OR filtration)' \
  --year-from 2025 \
  --year-to 2025 \
  --max 500
```

下一次直接从上次保存的 offset 继续：

```bash
python main.py search \
  --site sciencedirect \
  --query '("nanofiltration membrane" OR "hydrophobic membrane") AND (desalination OR "water treatment" OR filtration)' \
  --year-from 2025 \
  --year-to 2025 \
  --max 500 \
  --resume-search
```

手动指定起点或重置游标：

```bash
python main.py search --site sciencedirect --query "transparent conductive oxide" --year-from 2023 --year-to 2024 --max 500 --start-offset 500
python main.py search --site sciencedirect --query "transparent conductive oxide" --year-from 2023 --year-to 2024 --max 500 --reset-search-cursor
```

建议 `--max` 使用 25 的倍数，例如 `500`、`1000`。如果不是 25 的倍数，程序会保守处理续搜 offset，避免漏掉当前页尚未返回的结果。

### Nature

Nature 一般可直接检索；如果遇到访问验证，也可以先登录：

```bash
python main.py login --site nature
```

普通 Nature Portfolio 检索：

```bash
python main.py search \
  --site nature \
  --query "water treatment membrane" \
  --year-from 2021 \
  --year-to 2025 \
  --max 100
```

Nature Communications / npj 不是独立站点，当前通过 `nature` adapter 的 `--journal` 和 `--journal-family` 过滤实现。期刊别名和子刊族配置集中在 `sites/nature_journals.py`。

Nature Communications 示例：

```bash
python main.py search \
  --site nature \
  --query "perovskite solar cells" \
  --journal nc \
  --year-from 2024 \
  --year-to 2026 \
  --max 100
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

Nature 支持按页续搜，子刊过滤会进入续搜 key。不同 `--journal` / `--journal-family` 条件不会共用同一个续搜位置：

```bash
python main.py search \
  --site nature \
  --query "water treatment membrane" \
  --journal nc \
  --year-from 2021 \
  --year-to 2025 \
  --max 100 \
  --resume-search
```

也可以用 `--start-offset 100` 手动从第 101 条附近开始。Nature 每页约 50 条，非 50 倍数的 offset 会回退到所在页起点，避免漏页。

### Wiley Online Library

Wiley 高权限全文通常需要机构登录或统一认证，建议先登录：

```bash
python main.py login --site wiley
```

登录状态保存到 `browser_profiles/wiley/`，后续 `search` 和 `crawl` 会复用该 profile。爬取过程中如果再次遇到人机验证，程序会暂停，等你在 Chrome 中完成验证并回到终端按 Enter 后继续。

基础检索：

```bash
python main.py search \
  --site wiley \
  --query "perovskite solar cells" \
  --year-from 2020 \
  --year-to 2021 \
  --max 100
```

Wiley 搜索使用稳定 URL 参数翻页，不依赖点击下一页，并复用 SQLite `search_cursors` 表保存续搜位置。Wiley 目前支持按首批期刊别名做本地 DOI code 过滤：

```text
am      Advanced Materials                 adma
afm     Advanced Functional Materials      adfm
aem     Advanced Energy Materials          aenm
small   Small                              smll
```

Advanced Materials 示例：

```bash
python main.py search \
  --site wiley \
  --query "perovskite solar cells" \
  --journal am \
  --year-from 2020 \
  --year-to 2021 \
  --max 50
```

多个 Wiley 期刊：

```bash
python main.py search \
  --site wiley \
  --query "perovskite solar cells" \
  --journal am \
  --journal afm \
  --journal aem \
  --year-from 2020 \
  --year-to 2021 \
  --max 100
```

Wiley 续搜：

```bash
python main.py search \
  --site wiley \
  --query "perovskite solar cells" \
  --year-from 2020 \
  --year-to 2021 \
  --max 500 \
  --resume-search
```

也可以使用 `--start-offset 500` 手动指定起点，或使用 `--reset-search-cursor` 清除该检索条件的续搜位置。

### SpringerLink

Springer 当前支持基础检索和爬取流程，分页仍使用按钮翻页，不启用 `search_cursors` 续搜：

```bash
python main.py login --site springer

python main.py search \
  --site springer \
  --query "transparent conductive oxide" \
  --year-from 2023 \
  --year-to 2025 \
  --max 100
```

### 爬取检索结果

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

### 爬取单篇文章

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

### 查看进度

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

### Wiley 正文与高清图片验证

建议先用单篇验证登录状态、正文和图片，不要直接跑大批量：

```bash
python main.py crawl \
  --site wiley \
  --url "https://onlinelibrary.wiley.com/doi/full/10.1002/aenm.202100818" \
  --max-figure-candidates-per-figure 8 \
  --min-image-bytes 1000
```

只验证正文时可关闭图表：

```bash
python main.py crawl \
  --site wiley \
  --url "PASTE_WILEY_ARTICLE_URL" \
  --no-figures \
  --no-tables
```

检查点：

- `parsed/fulltext.md` 应包含真实正文，不应只是 Abstract、References 或访问提示。
- `assets/figures/` 中应为正文图，不应是 logo、banner、cover 或 placeholder。
- SQLite 中成功图片的 `source_url` 优先应为无 `-m` 的 Wiley `cms/asset` 高清候选；如果最终保存 `-m`，通常表示高清候选被 Wiley 拒绝。

查看最近图片下载情况：

```bash
sqlite3 data/catalog.sqlite \
  "select status,error,source_url,label,size_bytes from assets where type='figure' order by id desc limit 40;"
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
  collections/
    {topic_slug}/
      collection.json
      urls.txt
      articles.jsonl
      articles.csv
      article_links/{site}__{article_key}
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

## 主题集合

主题集合是跨站点、人工命名的长期文献管理入口，适合按研究方向组织文献，例如：

```text
data/collections/
  nanofiltration-membrane/
  tco-transparent-conductive-oxide/
```

常用命令：

```bash
python main.py collections list
python main.py collections show --collection nanofiltration-membrane
python main.py collections refresh --collection nanofiltration-membrane
```

把已有自动 search collection 导入主题集合：

```bash
python main.py collections import-search \
  --site sciencedirect \
  --search nanofiltration-membrane-or-hydrophobic-membrane-and-desalination_y2021-2025 \
  --collection nanofiltration-membrane \
  --collection-title "Nanofiltration Membrane"
```

从 URL 文件或单篇 crawl 时也可以指定主题集合：

```bash
python main.py crawl \
  --file data/runs/{run_id}/urls.txt \
  --collection nanofiltration-membrane
```

导出主题集合：

```bash
python scripts/export_collection.py \
  --topic nanofiltration-membrane \
  --out /Users/chenlintao/Desktop/nanofiltration_export
```

主题集合导出会跨站点去重，并在 `manifest.csv` / `manifest.jsonl` 中保留 `topic_collection`、`site`、`source_query`、`source_run_id` 和 `source_collection`。

## 搜索续跑状态

`--resume-search` 依赖 SQLite 中的 `search_cursors` 表。游标按以下条件生成稳定 key：

- site
- query
- year_from / year_to
- journal filters
- sort

游标记录 `next_offset`、`page_size`、`last_run_id` 和是否已到末页。`--resume-search` 会读取 `next_offset`；`--start-offset` 会覆盖本次起点；`--reset-search-cursor` 会删除同一检索条件的游标。

该功能目前针对 `sciencedirect`、`nature` 和 `wiley` 启用。Nature 的 cursor 会结合 `--journal` / `--journal-family`，不同子刊过滤条件不会共用同一个续搜位置。Springer 仍使用原有按钮翻页流程。

## 手动验证与中断

如果遇到验证码、人机验证或需要重新登录，程序会在终端提示你到浏览器中完成验证，然后按 Enter 继续。

ScienceDirect 的验证页检测只匹配明确的访问拦截、验证码、请求处理失败等提示，避免把正文里的普通 `verification` 字样误判成人机验证。如果终端反复提示验证，但浏览器页面看起来正常，优先检查页面是否已经跳到错误页、机构权限页或请求失败页。

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
- ScienceDirect、Nature、Wiley 支持 `--resume-search` 续搜，适合将大检索拆成多批获取。
- 主题集合 `data/collections/{slug}/` 是长期管理入口；`data/runs/` 只是任务记录，可以定期清理前先确认需要的结果已导入主题集合。
- 站点权限取决于账号、机构网络和当前 IP 环境。
