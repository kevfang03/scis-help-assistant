# Governance and data handling

SCIS Help Assistant is an **unofficial student project**, not affiliated with or endorsed by Drexel University, and not deployed on any Drexel site. It reads only the **public** pages of support.cci.drexel.edu. This page records what it stores, what it sends where, and the controls that hold it to that. The decisions behind it, with dates, are in [ARCHITECTURE.md](ARCHITECTURE.md) and [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

## Data sources and where things are stored

| Where | What goes there | What never does |
|---|---|---|
| **The repository** | Code, tests with small hand-written synthetic HTML fixtures, docs, and `evals/questions.yaml` (questions and public page URLs only). | Crawled page text, the index, logs, feedback, the unanswered-questions log, eval results (`evals/results/` is gitignored), `.env`, API keys. |
| **The user data folder** (`platformdirs`, `scis-help status` prints it) | `pages.sqlite` (URL, title, section, extracted text, lastmod, fetch time, content hash per page); `index/` (chunks, vectors, BM25, manifest); `logs/`; `llm-usage.json` (token totals per day, 31 days kept); `unanswered.jsonl`; `feedback.jsonl`. | Raw HTML, images, PDFs, IP addresses, session ids, user agents, API keys. |
| **The user cache folder** | The fastembed model download (`BAAI/bge-small-en-v1.5`, about 70 MB). | Anything from users. |
| **Anthropic (Claude API)** | Per answer: the fixed system prompt, up to 5 retrieved chunks of public page text (titles and text, no URLs), and the **scrubbed** question. Per eval judge call: the scrubbed question, the answer and the cited chunks. | Unscrubbed messages, IP addresses, session ids, anything from the data folder other than the chunks above. |
| **Claude development sessions** | Code, synthetic fixtures, the public pages, eval numbers and counts. | Ticket data, student data, internal wiki text, screenshots of internal tools. |

Only public documentation goes into this project. No ticket data, student data, internal wiki text or screenshots of internal tools, ever, in the repository or in chats.

## Crawl policy

- Pages come only from `https://support.cci.drexel.edu/sitemap.xml`. The crawler never follows links off the site and never fetches PDFs or images.
- `robots.txt` is fetched at the start of every crawl and obeyed if it exists (it returned 404 when checked on 2026-10-18). A published `Crawl-delay` can only raise the delay.
- One connection, **1.5 s between requests** by default (`crawl.delay_seconds`; values under 1.0 s are rejected), and a descriptive User-Agent: `scis-help-assistant/0.1 (unofficial student project; +https://github.com/kevfang03/scis-help-assistant)`.
- Redirects are followed by hand: each hop is throttled, and an off-site redirect is never requested. Server errors are retried twice with back-off; a 429 stops the crawl.
- Re-crawls are incremental: a page is fetched again only when its sitemap `lastmod` is newer (`--full` refetches everything). Pages that left the sitemap are deleted from the local store. A full crawl takes about 23 minutes; a re-crawl with nothing changed takes seconds.
- Only extracted article text, URL, title, section and lastmod are stored.
- A courtesy note to the site's maintainers is optional and **not yet sent**; it is tracked in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

## User-input privacy

- The chat widget shows a notice: don't enter passwords or student IDs.
- Every message is **scrubbed before** retrieval, the LLM call and any log ([`common/scrub.py`](../src/scis_help/common/scrub.py)). In order: password phrases (`password`, `passwd`, `passcode`, `pass`, `pw` followed by `is`, `:` or `=` and a value) → `[PASSWORD]`; email addresses → `[EMAIL]`; US phone numbers → `[PHONE]`; 8-digit Drexel IDs → `[ID]`; Drexel user ids (2–3 letters and 2–4 digits, such as `abc123`) → `[USER]`. Over-scrubbing is accepted. If anything was replaced, the reply starts with a reminder not to share them.
- **Logs hold** counts, ids, statuses, scores, token counts and scrubbed text only. Each LLM call logs purpose, mode, model, token counts, estimated cost and status, never the question, sources, prompt or output.
- `unanswered.jsonl` keeps the date, mode, scrubbed question and top retrieved URL and score of each "not covered" answer (the docs-gap report). `feedback.jsonl` keeps the date, answer id, helpful yes/no, mode, status and used URLs; the scrubbed question only when the answer was rated 👎.
- **Never stored or logged anywhere:** IP addresses, session ids, user agents. The web server's access log is off (it would log client IPs). The per-client rate limiter holds IPs in memory only, for at most one 60-second window. Web sessions live in memory and are lost on restart.

## Grounding and citations

- Answers come only from retrieved chunks of the public pages; the system prompt tells the model to use nothing else.
- The model refers to sources as `[S1]`, `[S2]`. **Validation runs in code, not in the model:** unknown source ids are dropped, an "answered" with no valid source becomes "not covered", and a second clarifying question in one conversation becomes "not covered".
- **Links shown to users come only from the index metadata** of chunks the model was given, never from the model's text. Markdown links in the model's text lose their target. A URL or host name may stay in the answer text only as plain code text, and only if it appears verbatim in a source the model was given; every other URL is removed.
- When retrieval is weak (best dense score below `retrieval.min_score`, 0.65), the LLM is not called. Then, or when the model finds the sources don't cover the question, the answer is "The SCIS support pages don't cover this" plus the help-desk contact (a link to the support home page's "Talk to a person" section, not a copied phone number or email).

## Cost controls

- **Retrieval first:** the confidence gate skips the LLM call for weak matches.
- **The cheapest model that works:** `claude-haiku-5-5`, effort `low`, at most 1,000 output tokens (1,400 in desk mode).
- **Prompt caching:** the fixed system prompt (1,882 tokens, above the 512-token caching minimum) is read from cache on every answer call.
- **A daily token budget** (`llm.daily_token_budget`, default 200,000; cache reads count at 10%) hard-stops calls before they are made. Without budget or without a key, the assistant still lists the most relevant pages.
- **Web caps:** messages up to 500 characters; per session up to 20 messages and 40,000 tokens; per client up to 10 messages a minute.
- The owner uses a separate API key for this project with its own spend limit in the Anthropic Console.
- **Measured (2026-10-21):** the whole payload 02 day, 131 calls including every eval run, cost about $0.045. An eval run with the judge cost $0.0114 for the 20 dev questions and $0.0043 for the 10 owner questions.

## Security

- **Secrets only in the OS keychain** (`keyring`, service `scis-help`: Windows Credential Manager or macOS Keychain). `scis-help llm set-key` uses a hidden prompt. The key never appears in code, `.env`, logs, test output or chat.
- **TLS verification stays on**, through the operating system's trust store (`truststore`), for the crawler and the Anthropic SDK.
- **Loopback only:** the web app binds `127.0.0.1:8765` by default. CORS is off unless `web.allowed_origins` names a local page. The OpenAPI and docs routes are off, and request validation errors return a generic "Bad request." instead of echoing the message.
- **Desk mode is local-only** (`web.desk_local_only`, on by default): `/desk` and `mode=desk` are served only to `127.0.0.1` / `::1`. Today desk mode shows only more detail from the same public pages.
- **Sanitised rendering:** the model's markdown is rendered on the server with a small safe subset (paragraphs, lists, bold, italics, code, superscript references); raw HTML is off, and links survive only when they point to a source URL from the index or the configured help-desk contact. The widget builds the DOM from that output without injecting raw model HTML, inside a Shadow DOM.
- **No external requests from the widget:** it talks only to the server it was loaded from. The demo and desk pages carry a strict Content-Security-Policy (`default-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`).
- **Tests never touch the network:** every socket connection fails in tests, the SDK, HTTP transport and embedder are faked, and the keyring is an in-memory fake.

## Limitations

- **The eval sets are small and the holdout is weaker than dev.** On the 10 holdout questions (run once, never tuned on), retrieval hit@3 was 75% and groundedness 57%, against 94% and 88% on the 20 dev questions it was tuned on. The owner-written split is reported separately in the [README](../README.md#evaluation). Groundedness is judged by Haiku itself, and the judge prompt was changed during tuning (see ARCHITECTURE.md D9).
- **The support pages can be out of date,** and the local copy is only as fresh as the last crawl. The assistant repeats what the pages say, including any mistakes in them.
- **It is not a replacement for the help desk.** It cannot see accounts, tickets or computers, cannot reset anything, and points to the help desk whenever the pages don't cover a question.
- Web sessions and rate-limit counters are in memory, which is fine for a local demo and not built for a multi-user deployment.

## The BookStack option (not built)

The desk's internal BookStack wiki could later become a **desk-only** source through the existing `Source` interface. It is not built, and it needs, in this order:

1. API access on the owner's BookStack account.
2. The manager's approval **in writing**.
3. A separate OK for sending internal wiki text to the Anthropic API.

If approved, it would run read-only with the owner's token in the keychain, keep its index local and never committed, skip pages or sections about passwords, keys or credentials, and serve its results only in desk mode to `127.0.0.1` and the CLI, never to the public widget. See ARCHITECTURE.md, "Sources".
