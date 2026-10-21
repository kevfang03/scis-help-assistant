# Project instructions for Claude

Read this file, then [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md), before doing anything.

## What this is
**SCIS Help Assistant**: an **unofficial student project**. A chat help bot for students and faculty of Drexel's SCIS (formerly CCI). The user describes a problem in their own words; the bot finds the right page(s) in the **public** SCIS support documentation (support.cci.drexel.edu), answers briefly, and links every page it used. If the pages don't cover it, it says so and gives the help-desk contact instead of guessing.

## Who the owner is
A Drexel University CS student working as a student assistant at the SCIS IT service desk. This is a **portfolio project** for AI automation co-op roles, due **resume-ready by 2026-10-23**. Code quality, documented decisions, eval numbers and a clean governance story matter as much as features. Keep scope tight.

The owner prefers short, exact, numbered steps, with **every command in its own code block, including the `cd`**. Dev folders:
- Windows PC: `C:\Users\Admin\Documents\scis-help-assistant`
- Mac: `~/Documents/GitHub/scis-help-assistant` (cloned with GitHub Desktop)

## Two-session workflow
Two separate Claude sessions work on this repo. Figure out which one you are from the first message; if unclear, ask.

1. **Architect session (planning).** Talks through features with the owner, makes design calls (recommend one option, then ask), updates `docs/`, and writes **Execution Payloads** to `docs/payloads/NN-short-name.md`. Does not write implementation code.
2. **Worker session (implementation).** Reads one Execution Payload and implements exactly that: code, tests, local commits. If a payload is ambiguous or conflicts with these docs, stop and ask instead of guessing.

### Execution Payload format
Goal · Context/links to docs · Files to create or modify · Step-by-step tasks · Interfaces (signatures, config keys, data shapes) · Acceptance checks (commands and expected results) · Out of scope · Suggested effort level.

### Effort levels (Opus 5.5)
| Session and task | Effort |
|---|---|
| Architect: design decisions, writing payloads | high |
| Worker: scaffold, config, crawler, index, CLI, CI, README | high |
| Worker: answer engine, prompts, eval harness, web widget | high |
| Worker: debugging something that still fails after two tries | xhigh |

## Hard rules (both sessions)
- **Git:** commit locally in small, logical commits once tests pass. **Never push** (the owner pushes). Never force-push, rewrite history (no amend of pushed commits, no rebase) or change git config. After a payload, stop and summarise the diff for the owner.
- **Secrets:** only in `keyring` (Windows Credential Manager / macOS Keychain), service `scis-help`. Never in code, commits, logs, test output or chat. **Never ask for the API key in chat**; the owner runs `uv run scis-help llm set-key` (hidden prompt) themselves. `.env` holds non-secret settings only; keep `.env.example` current with every setting.
- **Data:** only **public** documentation goes into this project. No Drexel ticket data, student data, screenshots of internal tools, or internal wiki text, ever, in the repo or in chats. Crawled page text, the index, logs, feedback and the unanswered-questions log live in the platformdirs data folder, never in the repo. Test fixtures are small and synthetic (written by hand in the site's HTML shape, not copied pages).
- **Grounding:** answers come only from retrieved pages. Links shown to users come from the index metadata, **never from model output**. If retrieval is weak or the model cites nothing valid, the answer is "the support pages don't cover this" plus the help-desk contact. A URL or host name may appear in answer text only as plain text, and only if it appears verbatim in a provided source.
- **User-input privacy:** the UI tells users not to enter passwords or student IDs. Every user message is scrubbed (passwords, Drexel IDs, user ids, emails, phone numbers) **before** retrieval, the LLM call and any log. Logs hold counts, ids and scrubbed text only.
- **Cost:** retrieval-first; skip the LLM when retrieval confidence is below the threshold. Default model is the cheapest that works (`claude-haiku-5-5`). The fixed system prompt is cached (it must stay ≥ 512 tokens or it silently won't cache). Every call logs model, input/output/cache tokens and estimated cost. A daily token budget hard-stops calls; per-session message and token caps and a per-client rate limit apply in the web app.
- **Claude API:** official `anthropic` SDK only. The SDK client is built with `http_client=anthropic.DefaultHttpxClient(verify=<truststore.SSLContext>)`: uv's Python on macOS has no CA bundle (learned the hard way). Never disable TLS verification. Look up model ids, prices and SDK shapes in current docs (the `claude-api` skill), not from memory.
- **Crawling:** only `https://support.cci.drexel.edu/` pages listed in its sitemap. Respect robots.txt if present, ≤ 1 request/second, a descriptive User-Agent, local cache, store only extracted page text, URL, title and lastmod.
- **No impersonation:** the demo page and widget say "Unofficial student project", use neutral styling, and carry no Drexel/SCIS logos, colours or official wording. Not deployed on any Drexel site.
- **Cross-platform:** Python 3.12+, `uv`, `pathlib`, `platformdirs` for every data/cache/log path, no OS-specific paths or shell calls. Must work on the Windows PC and the Mac.
- **Web server** binds to `127.0.0.1` by default.

## Conventions
- Layout: `src/scis_help/` (subpackages per [ARCHITECTURE.md](docs/ARCHITECTURE.md#layout)); tests in `tests/` mirroring it.
- Tooling: `uv`, `pyproject.toml` (hatchling), `pytest`, `ruff` (line length 100; rules E, F, I, UP, B). Type hints on public functions. Dataclasses or pydantic models for data shapes.
- Tests never touch the network: fake the SDK client, the HTTP transport (`httpx.MockTransport`) and the embedder.
- CLI entry point: `scis-help` (`uv run scis-help ...`). Exit codes in `common/exitcodes.py`.
