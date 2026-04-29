# Nature Journal Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This plan intentionally avoids automated tests because the requester will run manual validation commands after implementation.

**Goal:** Extend the existing `nature` site adapter so Nature Communications and npj sub-journals can be searched, crawled, and stored as journal-aware batches.

**Architecture:** Keep NC and npj inside the existing `NatureAdapter` because they share `nature.com` article URLs and page structure. Add a small journal catalog and search filter model, thread those filters through CLI, storage collection options, and Nature search URL construction, then apply result-level filtering as a defensive check.

**Tech Stack:** Python, argparse CLI, BeautifulSoup parsing, Patchright browser flow, existing `StorageManager` collection system.

---

## Explicit Constraints

- Do not run `pytest`, browser tests, or any automated test command during implementation.
- Use code inspection, parser dry-runs, and local import checks only if needed.
- Do not add per-subjournal adapters such as `nature_communications.py` or `npj.py`.
- Keep the behavior backward-compatible for existing commands like `python main.py search --site nature --query "..."`.
- At completion, provide manual commands for the requester to run.

## File Structure

- Modify: `sites/base.py`
  - Add a small `SearchFilters` dataclass.
  - Extend `SiteAdapter.search()` signature to accept optional filters.

- Create: `sites/nature_journals.py`
  - Own Nature journal aliases, known journal slugs, and family expansion logic.
  - Keep journal naming and alias parsing out of `main.py` and `sites/nature.py`.

- Modify: `sites/nature.py`
  - Accept `SearchFilters`.
  - Build Nature search URLs with journal filters.
  - Filter extracted results by selected journal names when journal text is visible in result cards.
  - Keep generic Nature search unchanged when no journal filter is supplied.

- Modify: `main.py`
  - Add `--journal` and `--journal-family` to `search`.
  - Pass filters into adapter search.
  - Include journal filter options in run and collection metadata.

- Modify: `README.md`
  - Document NC/NPJ usage.
  - Correct the outdated Nature support note.

- Optional modify: `search/browser_search.py`
  - Update compatibility wrapper only if import/signature drift causes a code-level break.

## Implementation Tasks

### Task 1: Add Search Filter Model

**Files:**
- Modify: `sites/base.py`

- [ ] Add `field` import and `SearchFilters` beside `SearchResult`.

```python
from dataclasses import dataclass, field
```

```python
@dataclass
class SearchFilters:
    journals: list[str] = field(default_factory=list)
    journal_family: str = ""
    sort: str = "relevance"
```

- [ ] Change `SiteAdapter.search()` signature to:

```python
def search(
    self,
    engine,
    query: str,
    year_from: int = 2024,
    year_to: int = 2025,
    max_results: int = 200,
    filters: SearchFilters | None = None,
) -> list[SearchResult]:
    raise NotImplementedError(f"{self.key} adapter search is not implemented yet")
```

- [ ] Update `ScienceDirectAdapter.search()` and `SpringerAdapter.search()` signatures to accept `filters=None` and ignore it.

Expected code-level outcome:
- Existing call sites still work because `filters` is optional.
- New Nature path can receive structured filter intent.

### Task 2: Create Nature Journal Catalog

**Files:**
- Create: `sites/nature_journals.py`

- [ ] Add a catalog with a conservative initial set. Include Nature Communications and representative npj journals first; the catalog is data-driven and can grow without changing adapter logic.

```python
"""Nature Portfolio journal alias and filter helpers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NatureJournal:
    key: str
    name: str
    aliases: tuple[str, ...] = ()
    family: str = ""


NATURE_JOURNALS: dict[str, NatureJournal] = {
    "ncomms": NatureJournal(
        key="ncomms",
        name="Nature Communications",
        aliases=("nc", "nature communications"),
        family="nature",
    ),
    "npjcompumats": NatureJournal(
        key="npjcompumats",
        name="npj Computational Materials",
        aliases=("npj computational materials",),
        family="npj",
    ),
    "npj2dmaterials": NatureJournal(
        key="npj2dmaterials",
        name="npj 2D Materials and Applications",
        aliases=("npj 2d materials", "npj 2d materials and applications"),
        family="npj",
    ),
    "npjquantmats": NatureJournal(
        key="npjquantmats",
        name="npj Quantum Materials",
        aliases=("npj quantum materials",),
        family="npj",
    ),
    "npjcleanwater": NatureJournal(
        key="npjcleanwater",
        name="npj Clean Water",
        aliases=("npj clean water",),
        family="npj",
    ),
    "npjdigmed": NatureJournal(
        key="npjdigmed",
        name="npj Digital Medicine",
        aliases=("npj digital medicine",),
        family="npj",
    ),
}
```

- [ ] Add resolver helpers.

```python
def normalize_journal_token(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("_", " ").split())


def resolve_journals(values: list[str] | tuple[str, ...] | None, family: str = "") -> list[NatureJournal]:
    requested = [normalize_journal_token(value) for value in (values or []) if normalize_journal_token(value)]
    family_key = normalize_journal_token(family)
    resolved: list[NatureJournal] = []

    if family_key:
        for journal in NATURE_JOURNALS.values():
            if normalize_journal_token(journal.family) == family_key:
                resolved.append(journal)

    for token in requested:
        for journal in NATURE_JOURNALS.values():
            aliases = {normalize_journal_token(journal.key), normalize_journal_token(journal.name)}
            aliases.update(normalize_journal_token(alias) for alias in journal.aliases)
            if token in aliases and journal not in resolved:
                resolved.append(journal)
                break

    return resolved
```

Expected code-level outcome:
- `--journal nc` resolves to Nature Communications.
- `--journal-family npj` expands to the known npj subset.
- Unknown journal tokens do not crash at this layer; CLI validation can decide how strict to be.

### Task 3: Thread Filters Through CLI

**Files:**
- Modify: `main.py`

- [ ] Import `SearchFilters`.

```python
from sites.base import SearchFilters
```

- [ ] Add search args in `build_parser()` under the search subcommand.

```python
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
```

- [ ] Add a helper near `_content_options_from_args()`.

```python
def _search_filters_from_args(args) -> SearchFilters:
    return SearchFilters(
        journals=list(getattr(args, "journal", []) or []),
        journal_family=getattr(args, "journal_family", "") or "",
    )
```

- [ ] In `cmd_search()`, build filters before `adapter.search()`.

```python
filters = _search_filters_from_args(args)
```

- [ ] Pass filters into `adapter.search()`.

```python
results = adapter.search(
    engine,
    query=args.query,
    year_from=args.year_from,
    year_to=args.year_to,
    max_results=args.max,
    filters=filters,
)
```

- [ ] Include filter values in `storage.create_run(... options=...)`.

Change:

```python
options=_content_options_from_args(args),
```

To:

```python
options={
    **_content_options_from_args(args),
    "journals": list(getattr(args, "journal", []) or []),
    "journal_family": getattr(args, "journal_family", "") or "",
},
```

Expected code-level outcome:
- CLI accepts journal filters without affecting non-Nature sites.
- Run metadata records the filter intent.

### Task 4: Extend Nature Search URL Construction

**Files:**
- Modify: `sites/nature.py`

- [ ] Import `SearchFilters` and resolver helpers.

```python
from sites.base import SearchResult, SiteAdapter, SearchFilters, first_year
from sites.nature_journals import NatureJournal, resolve_journals
```

- [ ] Change `NatureAdapter.search()` signature to accept filters.

```python
def search(
    self,
    engine,
    query: str,
    year_from: int = 2024,
    year_to: int = 2025,
    max_results: int = 200,
    filters: SearchFilters | None = None,
):
```

- [ ] Resolve selected journals at the start of `search()`.

```python
selected_journals = resolve_journals(
    filters.journals if filters else [],
    filters.journal_family if filters else "",
)
```

- [ ] Add a helper to build Nature search params.

```python
def _search_url(
    self,
    query: str,
    year_from: int,
    year_to: int,
    journals: list[NatureJournal],
) -> str:
    params = {
        "q": query,
        "date_range": f"{year_from}-{year_to}",
    }
    if len(journals) == 1:
        params["journal"] = journals[0].name
    elif journals:
        params["journal"] = "|".join(journal.name for journal in journals)
    return f"{self.search_base}?{urlencode(params)}"
```

Important:
- The exact Nature search parameter name may need manual browser validation. This code gives one centralized place to adjust it.
- Do not scatter the parameter name in multiple functions.

- [ ] Replace the inline URL construction with:

```python
url = self._search_url(query, year_from, year_to, selected_journals)
```

Expected code-level outcome:
- All Nature search URL filter construction is centralized.
- Manual testing can quickly identify whether Nature expects `journal`, `journal_name`, or another facet parameter.

### Task 5: Add Result-Level Journal Filtering

**Files:**
- Modify: `sites/nature.py`

- [ ] Add optional selected journal handling in the search loop after extraction.

Change:

```python
page_results = [
    result
    for result in self.extract_results(html)
    if result.url not in seen and self._year_in_range(result.year, year_from, year_to)
]
```

To:

```python
page_results = [
    result
    for result in self.extract_results(html)
    if result.url not in seen
    and self._year_in_range(result.year, year_from, year_to)
    and self._result_matches_selected_journals(result, selected_journals)
]
```

- [ ] Add a permissive matcher. It should return `True` if no journal filter is selected, or if the title/url result does not expose enough journal metadata.

```python
@staticmethod
def _result_matches_selected_journals(result: SearchResult, journals: list[NatureJournal]) -> bool:
    if not journals:
        return True
    haystack = f"{result.title} {result.url}".lower()
    if not haystack.strip():
        return True
    for journal in journals:
        names = [journal.name, journal.key, *journal.aliases]
        if any((name or "").lower() in haystack for name in names):
            return True
    return True
```

Rationale:
- Nature search cards may not reliably expose journal name.
- Search URL facet should do primary filtering.
- Strict filtering belongs after article-page metadata is saved, not during result extraction.

Expected code-level outcome:
- No false negatives from sparse result cards.
- A later hardening pass can add card-level journal metadata to `SearchResult`.

### Task 6: Make Collection Metadata Journal-Aware

**Files:**
- Modify: `main.py`

- [ ] Ensure both search and crawl collection options preserve journal fields.

In `_collection_for_crawl()`, extend options:

```python
options={
    **_content_options_from_args(args),
    "source_file": getattr(args, "file", ""),
    "source_url": getattr(args, "url", ""),
    "journals": list(getattr(args, "journal", []) or []),
    "journal_family": getattr(args, "journal_family", "") or "",
},
```

- [ ] Add the same journal fields to crawl parser only if `crawl` receives those flags. If crawl does not need journal flags, leave it as metadata-only through search runs.

Expected code-level outcome:
- Search-originated Nature collections contain journal filter options.
- Existing crawl from `data/runs/{run_id}/urls.txt` remains compatible.

### Task 7: Documentation Update

**Files:**
- Modify: `README.md`

- [ ] Replace the Nature site bullet.

From:

```markdown
- `nature`：已注册站点和域名识别，但搜索未完整实现。
```

To:

```markdown
- `nature`：支持 Nature Portfolio 搜索、爬取基础流程，并可按 Nature Communications / npj 子刊过滤。
```

- [ ] Add usage examples under search.

```markdown
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
```

Expected code-level outcome:
- README no longer contradicts implementation.
- Manual operator has copy-pasteable commands.

### Task 8: Code-Only Sanity Review

**Files:**
- Inspect only, no automated tests.

- [ ] Read changed imports and signatures:

```bash
rg -n "SearchFilters|def search\\(|--journal|journal_family|resolve_journals" sites main.py
```

- [ ] Check that no adapter signature was missed:

```bash
rg -n "def search\\(" sites
```

- [ ] Check that README examples mention only implemented flags:

```bash
rg -n "--journal|--journal-family|nature" README.md main.py
```

- [ ] Optional import-only check if the requester allows it. This is not a functional test, but it does execute imports:

```bash
python - <<'PY'
from sites.registry import get_adapter
import main
print(get_adapter("nature").key)
print(main.build_parser().parse_args(["search", "--site", "nature", "--query", "x", "--journal", "nc"]).journal)
PY
```

Expected code-level outcome:
- No obvious unresolved names.
- CLI parser accepts the new options.

## Manual Test Commands For Requester

After implementation, ask the requester to run these manually.

### 1. Confirm CLI Help

```bash
python main.py search --help | rg "journal|journal-family"
```

Expected:
- Output includes `--journal`.
- Output includes `--journal-family`.

### 2. Nature Communications Search

```bash
python main.py search \
  --site nature \
  --query "perovskite solar cells" \
  --journal nc \
  --year-from 2024 \
  --year-to 2026 \
  --max 20 \
  --no-figures \
  --no-tables \
  --no-fulltext
```

Expected:
- Browser opens Nature search.
- Result URLs are `https://www.nature.com/articles/...`.
- Results should primarily be Nature Communications. If not, adjust the centralized parameter in `NatureAdapter._search_url()`.

### 3. npj Family Search

```bash
python main.py search \
  --site nature \
  --query "battery materials" \
  --journal-family npj \
  --year-from 2024 \
  --year-to 2026 \
  --max 20 \
  --no-figures \
  --no-tables \
  --no-fulltext
```

Expected:
- Browser opens Nature search.
- Result URLs are `https://www.nature.com/articles/...`.
- Result journals should be npj journals when the Nature search facet parameter is correct.

### 4. Crawl One Returned URL

Use one URL from the generated `data/runs/{run_id}/urls.txt`:

```bash
python main.py crawl \
  --site nature \
  --url "PASTE_ONE_NATURE_ARTICLE_URL_HERE" \
  --no-figures \
  --no-tables
```

Expected:
- `meta.json` is saved under `data/articles/nature/_library/...`.
- `parsed/fulltext.md` is saved if article full text is accessible.

### 5. Full Asset Crawl Smoke

Use one accessible NC or npj article:

```bash
python main.py crawl \
  --site nature \
  --url "PASTE_ONE_NATURE_ARTICLE_URL_HERE" \
  --max-figure-candidates-per-figure 4 \
  --min-image-bytes 1000
```

Expected:
- HTML and metadata are saved.
- Figures are attempted under `assets/figures/`.
- Failed figure candidates, if any, are recorded in SQLite rather than crashing the run.

## Known Risk

Nature's exact search facet parameter for journal filtering may differ from the initial `journal` parameter in this plan. The implementation must keep that parameter in `NatureAdapter._search_url()` so manual validation can correct it in one place without changing CLI, storage, or catalog logic.

