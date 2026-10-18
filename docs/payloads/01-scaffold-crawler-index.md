# Execution Payload 01: scaffold, support-site crawler, hybrid index

**Suggested effort level:** high.

## Goal
Create the project and the retrieval half of the product, with no LLM yet:
1. the scaffold: uv project, config, paths, logging, TLS helper, exit codes, CLI skeleton;
2. a polite crawler for the public support site that stores extracted page text locally;
3. chunking plus a hybrid index (local fastembed vectors + BM25, fused with RRF);
4. commands `crawl`, `index`, `search` and `status`.

After this payload, `uv run scis-help search "can't connect to the vpn"` prints the right support pages, and payload 02 can build answering on the `Retriever` interface below. **Keep the interfaces exactly as written;** if one must change, stop and ask the architect session.

## Context
- [CLAUDE.md](../../CLAUDE.md): hard rules (Data, Crawling, Cross-platform, Git).
- [docs/ARCHITECTURE.md](../ARCHITECTURE.md): "The source site", "Data flow", "Layout", "Local data", D1 (index), D2 (chunking), D3 (crawler), D10 (config), "Sources".
- Patterns to copy (read, don't import): `C:\Users\Admin\Documents\enterprise-ai-ops-suite\src\ops_suite\common\` (`config.py`, `paths.py`, `logsetup.py`, `exitcodes.py`, the atomic JSON write in `tdx/alerts.py`) and that repo's `pyproject.toml` (ruff and pytest settings).
- Site facts (checked 2026-10-18): Hugo + Docsy, server-rendered. Content is `<article class="td-article">` inside `<main id="main">`. `<title>` ends with ` · CCI Technology Services and Support`. `sitemap.xml` has 692 `<url>` entries with `<lastmod>`. `robots.txt` is a 404.
- Library facts to **verify from current docs before coding** (stop and ask if any is wrong):
  - `fastembed.TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=...)`, with `.passage_embed(texts)` and `.query_embed(text)` returning iterables of numpy arrays (384 dims). It downloads the model from Hugging Face on first use.
  - `bm25s`: `bm25s.tokenize(...)`, `bm25s.BM25().index(...)`, `.save(dir)` / `bm25s.BM25.load(dir)`, `.retrieve(query_tokens, k=...)`.
  - `onnxruntime` (pulled in by fastembed) must have wheels for the Python version you pin, on Windows x64 and macOS arm64. Pin `.python-version` to `3.12` unless a newer version is confirmed.

## Files to create
- `pyproject.toml` (name `scis-help-assistant`, package `scis_help`, script `scis-help = "scis_help.cli:main"`, hatchling, ruff and pytest settings as in ops-suite, `requires-python = ">=3.12"`), `uv.lock`, `.python-version`.
- `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `.pytest_cache/`, `.ruff_cache/`, `evals/results/`, `*.sqlite`, `*.npy`, `dist/`.
- `.env.example`: every setting below, commented, with defaults.
- `README.md`: a stub (title, "Unofficial student project", the setup and crawl commands). Payload 04 writes the real one.
- `src/scis_help/__init__.py`, `cli.py`
- `src/scis_help/common/`: `config.py`, `paths.py`, `logsetup.py`, `exitcodes.py`, `tls.py`, `atomic.py`
- `src/scis_help/sources/`: `base.py`, `support_site.py`, `pages.py` (the `pages.sqlite` store)
- `src/scis_help/index/`: `chunk.py`, `embed.py`, `bm25.py`, `store.py`, `retrieve.py`, `build.py`
- `tests/` mirroring the above; `tests/fixtures/site/`: a synthetic `sitemap.xml`, robots files and 4–6 **hand-written** HTML pages in the Docsy shape (header, nav, breadcrumb, `main#main > article.td-article` with `h1`/`h2`/`h3`, a code block, a table, a feedback footer, scripts). Invent the content (e.g. "Connecting to the Example VPN"); do not copy real pages.

## Interfaces (fixed: 02 and 03 code against these)

```python
# common/config.py — pydantic-settings, env prefix SCIS_HELP_, nested delimiter "__", reads .env
class CrawlSettings(BaseModel):
    base_url: str = "https://support.cci.drexel.edu/"
    delay_seconds: float = Field(default=1.5, ge=1.0)
    max_pages: int = Field(default=2000, ge=1)
    timeout_seconds: float = 20.0
class IndexSettings(BaseModel):
    embed_model: str = "BAAI/bge-small-en-v1.5"
    chunk_min_words: int = 150
    chunk_max_words: int = 450
class RetrievalSettings(BaseModel):
    k: int = Field(default=5, ge=1, le=20)
    candidates: int = 20          # per retriever, before fusion
    max_chunks_per_page: int = 2
    min_score: float = 0.55       # dense cosine; tuned in payload 02
    min_bm25: float = 5.0         # top BM25 score that counts as a lexical hit; tuned in 02
class Settings(BaseSettings):
    crawl: CrawlSettings; index: IndexSettings; retrieval: RetrievalSettings
    # payload 02 adds llm and answer; payload 03 adds web
def load_settings() -> Settings: ...

# common/paths.py — platformdirs, app name "scis-help"; create dirs on first write
def data_dir() -> Path; def cache_dir() -> Path; def log_dir() -> Path   # log_dir = data_dir()/"logs"
def pages_db() -> Path              # data_dir()/"pages.sqlite"
def index_dir() -> Path             # data_dir()/"index"
def model_cache_dir() -> Path       # cache_dir()/"models"
# Tests override the base with the SCIS_HELP_HOME env var (if set, data and cache live under it).

# common/tls.py
def ssl_context() -> ssl.SSLContext          # truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
def use_system_trust_for_downloads() -> None # truststore.inject_into_ssl(), once, idempotent;
    # call only right before fastembed may download the model (huggingface_hub does its own HTTP)

# common/exitcodes.py
EXIT_OK = 0; EXIT_ERROR = 1; EXIT_CONFIG = 2; EXIT_NETWORK = 3; EXIT_NO_INDEX = 4
# 02 adds EXIT_LLM = 7

# sources/base.py — exactly as in ARCHITECTURE.md "Sources"
Audience = Literal["public", "desk"]
@dataclass(frozen=True, slots=True)
class Document:
    url: str; title: str; text: str; section: str; lastmod: str | None
    source: str; audience: Audience
class Source(Protocol):
    name: str; audience: Audience
    def iter_documents(self) -> Iterator[Document]: ...   # reads the local store, no network

# sources/support_site.py
@dataclass(frozen=True, slots=True)
class CrawlReport:
    fetched: int; unchanged: int; removed: int; failed: int; skipped_robots: int; stopped_reason: str | None
def crawl(settings: Settings, *, full: bool = False, max_pages: int | None = None,
          transport: httpx.BaseTransport | None = None,
          sleep: Callable[[float], None] = time.sleep) -> CrawlReport: ...
def extract(html: str, url: str) -> tuple[str, str]: ...   # (title, text with "## " / "### " heading lines)
class SupportSiteSource:   # implements Source over pages.sqlite; name "support_site", audience "public"

# index/embed.py
class Embedder(Protocol):
    model_id: str; dim: int
    def embed_passages(self, texts: Sequence[str]) -> np.ndarray: ...  # (n, dim) float32, L2-normalised
    def embed_query(self, text: str) -> np.ndarray: ...                # (dim,) float32, L2-normalised
class FastEmbedder:   # real one; lazy-loads the model into paths.model_cache_dir()
def get_embedder(settings: Settings) -> Embedder: ...

# index/retrieve.py
@dataclass(frozen=True, slots=True)
class Chunk:
    id: int; url: str; title: str; heading: str; text: str; source: str; audience: Audience
@dataclass(frozen=True, slots=True)
class Hit:
    chunk: Chunk; rank: int; rrf: float; dense: float; bm25: float | None
@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: list[Hit]           # fused, ≤ max_chunks_per_page per URL, length ≤ k
    best_dense: float
    best_bm25: float
    confident: bool           # best_dense >= min_score or best_bm25 >= min_bm25
class IndexMissing(Exception): ...      # no index, or index built with another embed model
class Retriever:
    @classmethod
    def load(cls, settings: Settings, *, embedder: Embedder | None = None) -> "Retriever": ...
    def search(self, query: str, *, k: int | None = None,
               audience: Audience = "public") -> RetrievalResult: ...
        # "public" sees only public chunks; "desk" sees public + desk.

# index/build.py
@dataclass(frozen=True, slots=True)
class BuildReport: pages: int; chunks: int; seconds: float; model_id: str
def build_index(settings: Settings, sources: Sequence[Source], *,
                embedder: Embedder | None = None) -> BuildReport: ...
    # full rebuild into a temp dir, then swap into index_dir() (no half-written index on failure)
```

`pages.sqlite` and index files: as in ARCHITECTURE.md "Local data". `manifest.json`: `{"model_id", "dim", "pages", "chunks", "built_at", "schema": 1}`.

## Step-by-step tasks
1. **Scaffold.** `uv init --package` style layout, add dependencies (`httpx`, `beautifulsoup4`, `fastembed`, `bm25s`, `numpy`, `platformdirs`, `pydantic`, `pydantic-settings`, `truststore`; dev: `pytest`, `ruff`). Config, paths, logging (rotating file in `log_dir()` plus stderr at WARNING; one `logging.getLogger("scis_help")` tree), exit codes, atomic write helper.
2. **Extractor** (`extract`). BeautifulSoup with `html.parser`. Take `article.td-article`; if missing, `main#main`; if both are missing, treat the page as failed. Remove `script`, `style`, `nav`, breadcrumbs, the Docsy page-meta and feedback blocks, and "Last modified" lines. Output plain text: `## ` before each `h2`, `### ` before each `h3`, list items as `- `, table rows as cells joined by ` | `, code blocks kept. Title: `<h1>` text, else `<title>` minus the ` · CCI Technology Services and Support` suffix. Collapse whitespace. Before finishing, run it on 3 real pages (fetched once each, by hand, with the User-Agent below) and check the output by eye; the tests use only the synthetic fixtures.
3. **Crawler** (`crawl`), per D3:
   - `httpx.Client(verify=tls.ssl_context(), timeout=..., headers={"User-Agent": "scis-help-assistant/0.1 (unofficial student project; +https://github.com/kevfang03/scis-help-assistant)"}, follow_redirects=True)`, one request at a time, `sleep(delay_seconds)` between requests (including robots and sitemap).
   - robots.txt: 404 → allow all; 200 → `urllib.robotparser`; other errors → stop (`stopped_reason="robots_unavailable"`).
   - Sitemap: parse `<loc>`/`<lastmod>` (handle a sitemap index too, in case it changes). Keep only `https` URLs on the base host; drop non-HTML extensions.
   - Incremental: skip when sitemap `lastmod` ≤ stored `lastmod` (unless `full`). Delete stored pages no longer in the sitemap. Redirects off-host are not followed into the store.
   - Retries: 2 with back-off on 5xx/timeout/connection errors. **429 → stop the crawl**, keep what was stored, `stopped_reason="rate_limited"`.
   - Log a summary line per crawl and one line per failure (URL and status only).
   - Respect `max_pages` (counts fetches, not skips).
4. **Pages store** (`pages.py`): SQLite schema from ARCHITECTURE.md; upsert; delete-missing; counts; `last_crawl` stored in a `meta` table.
5. **Chunker** (`chunk.py`), per D2: split at `## `/`### ` lines, keep the heading path (`"H2 › H3"`), merge sections under `chunk_min_words` with the next one, split over `chunk_max_words` on sentence boundaries with a one-sentence overlap. Embedded text = `"{title} › {heading}\n{text}"` (no heading → just the title). Pages under 30 words → one chunk.
6. **Embedder.** `FastEmbedder` calls `tls.use_system_trust_for_downloads()` before loading, sets `cache_dir=paths.model_cache_dir()`, normalises vectors, batches passages. Tests use a deterministic fake embedder (e.g. hashed bag-of-words → fixed-size vector), never the real model.
7. **BM25 + store + retriever.** BM25 over the same embedded text (English stopwords, lowercase; keep tokens like `cs265`, `cci-repo`). Hybrid per D1: dense top-`candidates` and BM25 top-`candidates`, RRF with k = 60, cap per URL, cut to `k`. `confident` as in the interface. `Retriever.load` raises `IndexMissing` if the index is absent or `manifest.model_id != settings.index.embed_model`.
8. **CLI** (`argparse`, subcommands; structure `main(argv: list[str] | None = None) -> int` so payload 02 can add `ask` and a bare-question shortcut):
   - `scis-help crawl [--full] [--max-pages N]` → `Crawl: 692 in sitemap, 40 fetched, 650 unchanged, 2 removed, 0 failed.` Exit 3 if it stopped on robots/429/network errors, else 0.
   - `scis-help index` → `Index: 692 pages, 3,104 chunks, model BAAI/bge-small-en-v1.5, 48.2 s.` Exit 4 if there are no pages.
   - `scis-help search "query" [--k N] [--desk]` → one line per hit: rank, rrf, dense, bm25, title, heading, URL; then `confident: yes/no (best dense 0.71, best bm25 9.3)`. Exit 4 with a "run `scis-help crawl` then `scis-help index`" hint when the index is missing.
   - `scis-help status` → data folder path, pages stored, last crawl time, index manifest summary. No network.
9. **Tests** (no network; `httpx.MockTransport`, fake embedder, `SCIS_HELP_HOME` set to `tmp_path`):
   - extractor: title, heading markers, removed chrome, tables, code, a page with no article → failed;
   - crawler: robots 404 allows; robots disallow skips (`skipped_robots`); `sleep` called with the delay between every request; incremental skip by lastmod; `--full` refetches; removed pages deleted; off-host and `.pdf` URLs ignored; 5xx retried then failed; 429 stops; `delay_seconds < 1.0` rejected by config;
   - chunker: heading paths, merge, split with overlap, short page → one chunk;
   - retriever: lexical-only match ranks first via BM25; per-page cap; `k`; audience filter (add one `desk` fake document); `confident` both ways; `IndexMissing` on missing index and on model mismatch; build swaps atomically (a failure mid-build leaves the old index);
   - CLI: `search` output shape and exit 4 hint; `status` with an empty data folder.
10. **Real run** (needs the network, the real site and the real model):
    - `uv run scis-help crawl --max-pages 20`, then `uv run scis-help index`, then a few searches.
    - Then the full crawl **in the background** (≈17 min) and `index` again.
    - Run the searches in acceptance check 5 and record the top-3 titles in your summary.
11. Ruff, pytest, then commit in small logical commits (scaffold, extractor + crawler, chunker, index + retriever, CLI, docs). **Do not push.** Summarise the diff for the owner.

## Acceptance checks
1. `uv run ruff check .` and `uv run ruff format --check .`: clean.
2. `uv run pytest -q`: all pass, no network (run once with the Wi-Fi off, or check that nothing opens a socket).
3. `git status --ignored` shows no `*.sqlite`, `*.npy` or model files inside the repo.
4. After the full crawl: `uv run scis-help status` shows ~690 pages and an index; a second `uv run scis-help crawl` fetches ~0 pages (all unchanged) and finishes in under a minute.
5. `uv run scis-help search "..."` for: "vpn won't connect", "where do I print in the building", "how do I get access to the shared file server", "zoom recording not showing in canvas", "what software do I need for my course". Each has a clearly relevant page in the top 3 (report the titles; if one misses, say so instead of tuning — tuning is payload 02's job on the eval set).
6. `uv run scis-help search "best pizza near campus"` prints `confident: no` with the defaults, or report the scores if it doesn't.
7. `git grep -n "verify=False\|CERT_NONE" -- src`: no matches.
8. **Owner, Mac (later, with payload 02's live check):** the commands below. The model download is the truststore check for Hugging Face.
   ```bash
   cd ~/Documents/GitHub/scis-help-assistant
   ```
   ```bash
   uv sync
   ```
   ```bash
   uv run scis-help crawl
   ```
   ```bash
   uv run scis-help index
   ```
   ```bash
   uv run scis-help search "vpn won't connect"
   ```

## Out of scope
- Any LLM code, the API key, scrubbing, answering, the eval set (payload 02).
- The web app (03), CI, licence and the real README (04).
- The BookStack source (only the `Source` protocol and the `audience` field exist).
- Tuning `min_score` / `min_bm25` beyond sane defaults.
