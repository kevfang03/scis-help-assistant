# Architecture

Status key: **Decided** (agreed with the owner) · **Proposed** (architect's recommendation, waiting for the owner's OK).

## Product (Decided, 2026-10-18)
SCIS Help Assistant, an unofficial student project. A help chat like the popups on retail sites, for SCIS students and faculty.
- The user describes a problem in their own words. Retrieval is by meaning (RAG), not keywords.
- Answers are brief, come **only** from retrieved public support pages, and cite every page used. If the pages don't cover it: "The support pages don't cover this", plus the help-desk contact.
- At most **one** clarifying question per conversation when the query is vague ("Are you on the VPN?").
- "Did this help? 👍/👎" feedback, and a local **unanswered-questions log** (a docs-gap report): counts and scrubbed questions only.
- **Desk mode** for student assistants on shift: more technical detail and every retrieved source listed. Same engine, same index.
- Delivery: a CLI (`uv run scis-help "question"`) and a local web page with an embeddable chat-bubble widget (`uv run scis-help web`). Embeddable with one `<script>` tag, but **not deployed** on any Drexel site.
- Later, optional: the internal BookStack wiki as a desk-only source (see [Sources](#sources)). Not built now.

## The source site (checked 2026-10-18)
- Host: **`https://support.cci.drexel.edu/`** is live and still the current host after the SCIS rename. Page titles end "· CCI Technology Services and Support"; the body mixes CCI and SCIS naming.
- Platform: a static Hugo site with the Docsy theme. Server-rendered HTML; no JavaScript needed to read it.
- Main content: `<article class="td-article">` inside `<main id="main">`. The title is in `<title>` (strip the " · CCI Technology Services and Support" suffix) and the article `<h1>`. Also removed: the "Still stuck?" footer (`section.td-help`) and section-index widgets (`.td-views`, `.td-filter`). Many older pages use `h4`/`<b>` as headings; those stay plain lines.
- Two sitemap URLs (`/software-courses/discord/`, `/service-catalog/educational-services/tux/`) are JavaScript-redirect stubs with no content. They fail on every crawl, which is expected.
- `sitemap.xml`: **692 URLs** with `<lastmod>`. Top sections by count: remote-conferencing-and-class-capture 208, software-courses 181, platforms-resources 69, getting-connected 65, canvas 52, service-catalog 42, other-it-resources 29, printing 25, scis-spaces 14, plus a few root-level pages.
- `robots.txt`: **404** (no rules published). We still crawl politely (below) and re-check robots.txt on every crawl.
- Public help-desk contact on the home page ("Talk to a person"): the CCI Commons help desk, phone, email, room and hours. The fallback message points to the home page rather than hard-coding these (see OPEN_QUESTIONS).

## Data flow
```text
crawl:   sitemap.xml ─► fetch (≤1 req/s, cache, lastmod) ─► extract article text ─► pages.sqlite
index:   pages.sqlite ─► chunk by heading ─► embed (fastembed, local) + BM25 ─► index/
ask:     question ─► scrub ─► hybrid retrieve (top k) ─► confidence gate
             ├─ weak ─► "not covered" + contact (no LLM call) ─► unanswered log
             └─ ok   ─► Haiku (cached system prompt, sources in user turn, structured output)
                         ─► validate cited ids ─► answer + links from index metadata
                                                  └─ status not_covered ─► unanswered log
```

## Layout
```text
src/scis_help/
  cli.py                 # argparse; subcommands below
  common/                # config.py, paths.py, logsetup.py, exitcodes.py, tls.py, scrub.py
  sources/               # base.py (Source protocol), support_site.py (crawler + extractor)
  index/                 # chunk.py, embed.py, bm25.py, store.py, retrieve.py
  llm/                   # client.py (SDK wrapper, budget, usage log), keys.py
  answer/                # engine.py, prompts.py, models.py
  web/                   # app.py (FastAPI), static/ (widget.js, widget.css, demo.html, desk.html)
  feedback/              # gaps.py (unanswered log), feedback.py
  eval/                  # run.py (retrieval and answer metrics)
evals/questions.yaml     # hand-written eval set (committed; public questions only)
tests/                   # mirrors src; fixtures/ holds small synthetic HTML in Docsy shape
```

## Local data (platformdirs, never in the repo)
`user_data_dir("scis-help")`:
- `pages.sqlite`: `pages(url PK, title, section, text, lastmod, fetched_at, content_hash, source)`.
- `index/`: `chunks.sqlite` (chunk id, url, title, heading path, text, source, audience), `vectors.npy` (float32, L2-normalised), `bm25/`, `manifest.json` (embedding model id, dimension, chunk count, build time, page count).
- `logs/scis-help.log` (rotating), `llm-usage.json` (per-day token totals, 31 days), `unanswered.jsonl`, `feedback.jsonl`.

`user_cache_dir("scis-help")`: `models/` for the fastembed model download (~70 MB).

**Windows gotcha (found 2026-10-21):** Claude Desktop is a packaged (MSIX) app, so `%LOCALAPPDATA%` writes from its sessions land in `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\scis-help`, not in the real `%LOCALAPPDATA%\scis-help` that the owner's own terminal uses. Claude sessions and the owner's terminal keep **separate** crawls, indexes and logs; the owner runs `crawl` and `index` once in their own terminal. Keyring (Windows Credential Manager) is shared, so a key saved from the owner's terminal is visible to Claude sessions.

## Decisions

### D1. Embeddings and index: local fastembed + BM25 hybrid, brute-force numpy (Decided, 2026-10-18)
- **Embedder:** `fastembed` with `BAAI/bge-small-en-v1.5` (384 dimensions, ~70 MB ONNX, CPU, no PyTorch). Free, offline after the first download, wheels for Windows and macOS. Use `query_embed` for questions and `passage_embed` for chunks (bge's query prefix is handled for us). Anthropic has no embedding endpoint; a paid embedding API would add a second vendor, a second key and per-crawl cost for no gain at this size.
- **Lexical:** BM25 over the same chunks (`bm25s`), because course codes, product names and error strings ("CCIREPO", "GlobalProtect", "CS 265") match better lexically.
- **Fusion:** Reciprocal Rank Fusion (k = 60) of the two top-20 lists, then de-duplicate to at most 2 chunks per page; return top `k` (default 5) chunks.
- **Store:** 690 pages → 1,027 chunks (measured 2026-10-21) → about 1.5 MB of vectors. Brute-force cosine (one numpy matmul) takes milliseconds. **No vector database** (Chroma, LanceDB, sqlite-vec would be more moving parts for no benefit at this scale). Chunk metadata in SQLite (stdlib).
- **Confidence gate:** if the best dense cosine score is below `retrieval.min_score` (default 0.55, tuned on the dev split) **and** BM25 has no hit, skip the LLM and answer "not covered". Saves cost on out-of-scope questions.
- **Index manifest** records the model id; `ask` refuses to run (config error) if the configured model differs from the index's.

### D2. Chunking (Decided, 2026-10-18)
Split each article at `h2`/`h3` boundaries; merge small sections and split long ones to about 150–450 words with a 1-sentence overlap. Each chunk's embedded text is `"{page title} › {heading path}\n{text}"`. Code blocks and tables are kept as text. Pages under 30 words (index/listing pages) are indexed as one chunk.

### D3. Crawler (Decided, 2026-10-18)
- Seeds from `sitemap.xml` only; never follows links off `support.cci.drexel.edu`, never fetches PDFs or images.
- Fetches robots.txt first each crawl; if it exists, obeys it (`urllib.robotparser`), and it is re-checked when it changes.
- `crawl.delay_seconds` default **1.5** (minimum 1.0, enforced), one connection, `User-Agent: scis-help-assistant/0.1 (unofficial student project; +https://github.com/kevfang03/scis-help-assistant)`.
- Incremental: skip a page when its sitemap `lastmod` ≤ stored `lastmod`; `--full` refetches all. Pages that left the sitemap are deleted from `pages.sqlite`.
- Uses `httpx` with the truststore SSL context. Retries 2× on 5xx/timeouts with back-off; a 429 stops the crawl.
- Stores extracted text, URL, title, section (first path segment), lastmod and a content hash. No raw HTML is stored.
- A full crawl takes about 23 minutes (1.5 s delay plus response time; measured 2026-10-21). A re-crawl with nothing changed takes seconds because of `lastmod`.
- Redirects are followed by hand: each hop is throttled, and an off-host redirect is never requested.

### D4. Answering (Decided, 2026-10-18)
- **Model:** `claude-haiku-5-5` ($0.10 / $0.50 per million input/output tokens for prompts ≤ 100K; cache reads ~0.1× input; 5-minute cache writes 1.25×). Checked 2026-10-18 with the `claude-api` skill; re-verify in the payload.
- **Request:** `output_config={"effort": "low", "format": <json schema>}` via `client.messages.parse(...)` with a pydantic model; no sampling parameters (Haiku 5.5 rejects non-default ones); `max_tokens` 600 (desk 900). Always check `stop_reason` (`refusal`, `max_tokens`) before reading output.
- **Prompt layout for caching:** one fixed system prompt (rules for both modes, the answer style, the clarify rule, the "not covered" rule, 2–3 short worked examples), marked `cache_control: ephemeral`. It must be ≥ 512 tokens or it silently won't cache. The **mode**, the retrieved sources (numbered `[S1]…[S5]` with title and text, no URLs) and the scrubbed question go in the user turn. Both modes share one cache entry.
- **Output schema:**
  ```text
  status: "answered" | "clarify" | "not_covered"
  answer: str            # markdown, short; refers to sources as [S1] etc.
  used_sources: list[str]  # e.g. ["S1", "S3"]
  clarifying_question: str | null
  ```
- **Validation (in code, not trusted to the model):** unknown source ids are dropped; `answered` with no valid source → `not_covered`; `clarify` is allowed only if no clarifying question was asked earlier in the session (otherwise the engine re-asks with "answer or say not covered"; one retry max). Links are built from index metadata for the valid ids. Public mode shows the used sources; desk mode shows used sources first, then every other retrieved source under "Also retrieved".
- The Citations API is not used: it is incompatible with structured outputs, and our source-id validation gives the same guarantee for links.
- **Not covered** text: "The SCIS support pages don't cover this. Talk to a person at the help desk: {contact}." (`answer.help_contact` setting.) Default contact (owner's choice, 2026-10-18): a link to the support home page's "Talk to a person" section, `https://support.cci.drexel.edu/`, not a copied phone number or email.
- **Clarify flow:** the CLI asks the one follow-up interactively; the web keeps per-session state. The follow-up answer is appended to the original question for a second retrieval.

### D5. Web app and widget (Decided, 2026-10-18)
- **FastAPI + uvicorn**, serving JSON endpoints and static files. No front-end build step.
- **Widget:** one vanilla-JS file, `widget.js`, plus `widget.css`, rendered inside a **Shadow DOM** so the host page's styles can't break it. Embedded with one tag: `<script src="http://127.0.0.1:8765/widget.js" data-mode="public" defer></script>`. A floating bubble opens a chat panel: a privacy notice ("Don't enter passwords or student IDs"), messages, source links, 👍/👎 per answer, and an "Unofficial student project" footer.
- Pages: `/` neutral demo host page (a plain fake "course resources" page with the widget); `/desk` the same widget with `data-mode="desk"`.
- Endpoints: `POST /api/chat {session_id, message, mode}` → `{answer_id, status, answer_html, sources[], clarifying_question}`; `POST /api/feedback {answer_id, helpful}`; `GET /api/health`.
- Answer markdown is rendered server-side with a safe subset (paragraphs, lists, bold, code, links only to index URLs); the widget inserts it with `textContent`/sanitised DOM, never raw model HTML.
- Guardrails: message ≤ 500 chars; per session ≤ 20 messages and ≤ 40K tokens; per client IP ≤ 10 messages/minute; global daily budget from the LLM layer. Sessions are in memory (lost on restart; fine for a local demo). Binds `127.0.0.1:8765` by default. CORS off by default; `web.allowed_origins` for embedding on another local page.
- Desk mode is not authenticated: today it only shows more detail from the same public pages. If a desk-only source is ever added, desk results must be served only to `127.0.0.1` callers and the CLI.

### D6. LLM layer (Decided, 2026-10-18)
Copied in shape from enterprise-ai-ops-suite payload 06a, simplified (no data-class gate: input is always public pages plus a scrubbed user question).
- Key in keyring, service `scis-help`, user `anthropic-api-key`. `scis-help llm set-key` (hidden prompt, `sk-ant-` check, asks before replacing), `llm status` (no network), `llm test` (one tiny call).
- SDK client: `anthropic.Anthropic(api_key=..., http_client=anthropic.DefaultHttpxClient(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)), timeout=30, max_retries=2)`.
- Daily token budget (`llm.daily_token_budget`, default 200,000) with a hard stop before the call; cache reads count at 10%. Usage per local day in `llm-usage.json`, kept 31 days, atomic writes.
- One log line per call: purpose, mode, model, input/output/cache-read/cache-write tokens, cost estimate, status. Never the question, sources, prompt or output.

### D7. Scrubbing (Decided, 2026-10-18)
Applied to every user message before retrieval, the LLM and logs. Replaces, in order: password phrases (`password`/`pw`/`passcode` followed by `is`/`:`/`=` and a token → `[PASSWORD]`), emails → `[EMAIL]`, phone numbers → `[PHONE]`, 8-digit Drexel IDs → `[ID]`, Drexel user ids (2–3 letters + 2–4 digits, e.g. `abc123`) → `[USER]`. Over-scrubbing is acceptable. If anything was replaced, the reply starts with a one-line reminder not to share passwords or IDs. Counts only are logged.

### D8. Unanswered log and feedback (Decided, 2026-10-18)
- `unanswered.jsonl`: one line per `not_covered` (or gated) answer: date, mode, scrubbed question, top retrieved URL and score. `scis-help gaps` prints questions grouped by normalised text with counts, most frequent first (the docs-gap report).
- `feedback.jsonl`: date, answer id, helpful (bool), mode, status, used URLs. No question text unless the answer was 👎 (then the scrubbed question, to find bad answers). `scis-help feedback` prints totals and the 👎 list.
- No IPs, session ids or user agents are stored anywhere.

### D9. Evaluation (Decided, 2026-10-18)
- `evals/questions.yaml`, about **30** questions: ~24 in-scope (each with 1–2 expected page URLs, written the way a student would ask, not in the page's wording) and ~6 out-of-scope (expected `not_covered`). Each has `split: dev | holdout` (about 2/3 dev). Tuning (`min_score`, `k`, chunk size, prompt) uses **dev only**; holdout is run once for the README numbers.
- `scis-help eval retrieval [--split dev|holdout|all]`: free, no LLM. Reports **hit@3** and hit@5 (any expected URL among the top pages) and MRR.
- `scis-help eval answers [--split ...] [--judge]`: runs the full engine. Reports: answered rate on in-scope, `not_covered` accuracy on out-of-scope, **citation hit** (a used source is an expected URL), and **groundedness**: with `--judge`, a Haiku judge rates each answer as fully supported / partly / unsupported by its cited chunks (structured output). Costs about one cent per run.
- Results are written to `evals/results/<date>-<split>.json` (gitignored) and a Markdown table printed for the README.

### D10. Config (Decided, 2026-10-18)
`pydantic-settings`, env prefix `SCIS_HELP_`, nested delimiter `__`, reads `.env`. Sections: `crawl` (base_url, delay_seconds, max_pages), `index` (embed_model, chunk_words), `retrieval` (k, min_score), `llm` (enabled, model, effort, max_output_tokens, daily_token_budget, timeout_seconds, prices), `answer` (help_contact), `web` (host, port, allowed_origins, rate limits, session caps). `.env.example` lists every key with a comment.

## Sources
A `Source` protocol so the BookStack wiki can plug in later without engine changes:
```python
@dataclass(frozen=True)
class Document:
    url: str
    title: str
    text: str            # extracted plain text with heading markers
    section: str
    lastmod: str | None
    source: str          # "support_site"
    audience: Literal["public", "desk"]

class Source(Protocol):
    name: str
    audience: Literal["public", "desk"]
    def iter_documents(self) -> Iterator[Document]: ...   # reads the local store; crawling is separate
```
Retrieval filters by audience: public mode sees only `public` chunks; desk mode sees both.

**BookStack (not built):** only if (a) the owner's BookStack account has API access and (b) the manager approves in writing. Then: read-only API with the owner's token in keyring, desk audience only, local index only, never committed, never sent to the web widget except to `127.0.0.1`, pages or sections about passwords/keys/credentials skipped, and the LLM data question re-checked (internal wiki text going to the Anthropic API needs its own OK).

## Cost estimate
Per question: system prompt ~900 tokens (cached after the first call), 5 chunks ~2,000 tokens, question and output ~400 → about $0.0004. The 200K-token daily budget allows roughly 70–90 questions a day for about $0.03. An eval run with the judge is under $0.02.

## Payload plan (Decided, 2026-10-18)
| # | Payload | Depends on | Target date |
|---|---|---|---|
| 01 | Scaffold (uv, config, paths, logging, TLS, exit codes) + support-site crawler + chunking + hybrid index + `crawl`, `index`, `search` (retrieval only, no LLM) | – | 10-18/19 |
| 02 | LLM layer (key, budget, usage) + scrubber + answer engine + `ask` CLI (public/desk, clarify) + eval harness and a drafted `evals/questions.yaml` + `gaps` | 01 | 10-19/20 |
| 03 | FastAPI web app + Shadow-DOM widget + demo/desk pages + feedback + rate/session caps | 02's `Engine` interface (can start in parallel once 02's interfaces are fixed) | 10-20/21 |
| 04 | README (problem, architecture diagram, eval table, governance, demo GIF slot), MIT licence, GitHub Actions (ruff + pytest) + badge, public-readiness check | 01–03 | 10-21/22 |
Buffer: 10-22/23 for the owner's live checks on both machines, the demo GIF and the eval run.

**Status 2026-10-21:** 01 done (`1cfc08e`..`9dae68d`). Because of the deadline, 02 runs on `main` and 03 runs at the same time in its own worktree on branch `payload-03-web`; 03 merges `main` once 02 lands, and the owner merges 03 into `main`. 04 is written once 02's eval numbers exist.
