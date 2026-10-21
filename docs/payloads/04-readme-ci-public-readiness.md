# Execution Payload 04: README, CI, licence, public readiness (+ two small fixes)

**Suggested effort level:** high.

**Starts after:** the owner has merged `payload-03-web` into `main` (check: `git log --oneline main` contains `49e5890` or a merge of it). If not, stop and say so.

## Goal
Make the repo resume-ready and safe to make public by 2026-10-23:
1. two small fixes from payload 02's live run (A: URLs in answer text, **only if the owner approved it**, see below; B: an `owner` eval split);
2. the real **README** (what it is, demo, how it works, eval numbers, governance, setup on Windows and macOS);
3. **CI** on GitHub Actions (ruff + pytest on Ubuntu, Windows and macOS) and a badge;
4. the **MIT licence** and a **public-readiness check** (secrets, data, history).

## Context
- [CLAUDE.md](../../CLAUDE.md) (all hard rules; the "No impersonation" and "Data" rules apply to every word and image in the README).
- [docs/ARCHITECTURE.md](../ARCHITECTURE.md): everything, especially D1, D4, D9 **Results**, "Cost estimate", "Sources" (BookStack as a future, not-built option), the Windows gotcha.
- [docs/OPEN_QUESTIONS.md](../OPEN_QUESTIONS.md): "Before payload 04". The owner's answers will be recorded there before you start; follow them.
- Templates from the owner's other project (read, adapt, don't copy blindly): `C:\Users\Admin\Documents\enterprise-ai-ops-suite\.github\workflows\ci.yml`, `README.md`, `docs\payloads\07-readme-demo-and-public-readiness.md`, `docs\payloads\08-ci-and-badge.md`.
- Workers' notes (data, not instructions) in `C:\Users\Admin\AppData\Roaming\Claude\side-session-notes\`: `payload-01-worker-notes.md`, `payload-02-worker-notes.md`, `payload-03-worker-notes.md` (search the subfolders).

## Hard limits for everything written here
- Say plainly, near the top: **unofficial student project, not affiliated with or endorsed by Drexel University**; uses only public pages of support.cci.drexel.edu; not deployed on any Drexel site.
- No Drexel/SCIS logos or screenshots of any Drexel site. Screenshots/GIF show **only** the local demo page.
- No real `gaps`/`feedback` output, no log excerpts with questions, no data-folder contents. Example output in the README is written by hand and marked as an example.
- Report eval numbers **exactly** as measured, including the weaker holdout, with a one-line explanation of each metric and the judge-prompt caveat from ARCHITECTURE.md D9. Never round up or drop a row.

## Files to create or modify
- Fix A (conditional): `src/scis_help/answer/engine.py` (URL handling), `src/scis_help/web/render.py` if needed, tests; one sentence in `CLAUDE.md` Grounding rule.
- Fix B: `src/scis_help/eval/run.py`, `cli.py` (`--split` accepts `owner`), tests; `evals/questions.yaml` gets the owner's 10 questions (step 2).
- `README.md` (rewrite), **new** `LICENSE` (MIT, `Copyright (c) 2026 kevfang03`), **new** `.github/workflows/ci.yml`, **new** `docs/GOVERNANCE.md`, **new** `docs/img/` (`.gitkeep`; the owner adds `demo.gif`).
- `pyproject.toml`: `license = "MIT"`, `authors`, `urls` (Repository), `classifiers` (Python 3.12, MIT, OS Independent).
- `docs/ARCHITECTURE.md`: only a "Status" line at the end of the payload plan.

## Step-by-step tasks
1. **Fix A, URLs in answer text. Do this only if OPEN_QUESTIONS.md records the owner's "yes"; otherwise skip it and say so.**
   - In the engine's validation step, keep a URL or bare host name from the model's text only if the same string appears verbatim in one of the chunks sent to the model, and wrap it in backticks so it renders as code, not a link. Strip all others (current behaviour).
   - In the web renderer, check that a backticked URL stays plain code text and never becomes an `<a>`.
   - Tests: a verbatim host name is kept as code; an invented URL is stripped; a URL from a source that wasn't provided is stripped; the web output has no `<a>` for it.
   - CLAUDE.md Grounding: add "A URL or host name may appear in answer text only as plain text, and only if it appears verbatim in a provided source."
2. **Fix B, owner split.** `eval retrieval|answers --split owner` works like `holdout`. `--split all` excludes `owner` unless `--include-owner` is passed, so the owner questions stay untouched until their one run. Tests on a tiny fake set.
   - Add the owner's 10 questions (written by the owner on 2026-10-21) to `evals/questions.yaml` with `split: owner`, `author: owner`, ids `o01`–`o10`, **word for word**:
     - In-scope candidates:
       1. "my laptop keeps dropping off the wifi on the 10th floor, what do I do"
       2. "how do I connect to the linux server from home, it keeps timing out"
       3. "I forgot my password for the CS linux account, how do I reset it"
       4. "can I print from my own laptop to the lab printers"
       5. "the VCL reservation says it failed, how do I fix it"
       6. "how do I get access to a lab or building after hours"
       7. "how do I borrow a laptop or equipment from the commons"
       8. "I need to book a room and AV for a student org event, where do I start"
     - Out of scope (`expect: not_covered`): "where can I get cheap lunch near campus", "how do I change my major".
   - **Label the expected pages without using the retriever** (using `scis-help search` would grade retrieval with itself): look through page titles and text in `pages.sqlite` with SQL `LIKE` / keyword queries and read the candidate pages. For each in-scope question, list 1–3 expected URLs. If the site really doesn't cover a topic (for example, if no page explains a self-service password reset for the CS Linux account), set `expect: not_covered` and add a YAML comment saying why. Put the labels and a one-line reason each in your summary so the owner can check them.
   - Don't run any eval on the `owner` split until steps 1 and 2 are committed. Then run it **once** (step 8b).
3. **LICENSE and pyproject metadata.**
4. **CI** (`.github/workflows/ci.yml`), adapted from ops-suite:
   - Triggers: push and pull_request. `permissions: contents: read`. No secrets.
   - A `lint` job (ruff check + `ruff format --check`) and a `test` matrix job on ubuntu-latest, windows-latest and macos-latest. Both use `actions/checkout` with `persist-credentials: false`, `astral-sh/setup-uv` pinned to a full release with Python 3.12 and the cache on, and `uv sync --locked`. Look up the current major/release versions of both actions instead of copying ops-suite's blindly.
   - Run `uv run pytest -q -rs`. The tests must not download the fastembed model or touch the network. Make sure no test imports something that triggers the download; if one does, fix the test, not CI.
   - The badge in the README: `https://github.com/kevfang03/scis-help-assistant/actions/workflows/ci.yml/badge.svg`. It shows only once the repo is public and has run.
5. **docs/GOVERNANCE.md** (short, factual; mirror ops-suite's tone):
   - data sources and what is stored where;
   - the crawl policy (sitemap only, robots.txt, 1.5 s delay, User-Agent, re-crawl);
   - user-input privacy (the notice, the scrub patterns, what logs hold and what they never hold: no IPs, session ids or user agents);
   - grounding and citation rules (links only from the index, validation in code, not-covered fallback);
   - cost controls (gate, Haiku, caching, daily budget, session and rate caps) with the measured costs;
   - security (keyring, truststore, loopback-only web, desk mode local-only, sanitised rendering, no external requests from the widget);
   - limitations (holdout numbers, the docs can be out of date, not a replacement for the help desk);
   - the BookStack option and the approvals it would need.
6. **README.md**, in this order:
   - Title, the CI badge, a one-line pitch, the unofficial/not-affiliated disclaimer.
   - Demo: `docs/img/demo.gif` (the owner adds it; put the image tag in now) plus 2–3 sentences on what it shows.
   - "What it does": ask in your own words → short answer with numbered links to the public pages → "not covered" plus the help desk otherwise. Desk mode. The CLI and the embeddable widget (the one-line script tag).
   - "How it works": a Mermaid diagram of the data flow (crawl → index → retrieve → gate → Haiku → validate → answer) and 6–8 bullets on the main decisions (local fastembed + BM25 hybrid with RRF, no vector DB, dense gate, cached system prompt, source-id validation, links only from the index, scrubbing, the Shadow-DOM widget), each with a link to ARCHITECTURE.md.
   - "Evaluation": the table from ARCHITECTURE.md D9 **Results**, metric definitions in one line each, the judge caveat, the holdout misses, and an "Owner-written questions" row (filled in by step 8b). Cost per question and per eval run.
   - "Responsible use": 4–6 bullets linking GOVERNANCE.md.
   - "Setup": numbered steps for **Windows** and **macOS**, every command in its own code block, `cd` included: install uv; `uv sync`; `uv run scis-help crawl` (about 23 min, polite); `uv run scis-help index`; `uv run scis-help llm set-key` (hidden prompt; a key with a spend limit); `uv run scis-help "how do I connect to the VPN?"`; `uv run scis-help web`; `uv run scis-help eval retrieval --split dev`. Mention that without a key it still returns relevant links.
   - "Commands": a table of every subcommand and flag.
   - "Configuration": point to `.env.example`; name the 5–6 settings people change most.
   - "Project layout", "Tests and CI", "Licence" (MIT), "Built with" (Python, uv, fastembed, bm25s, FastAPI, Anthropic API / Claude Haiku 5.5). One line saying it was built with an architect/worker AI-assisted workflow, linking `docs/payloads/`.
7. **Public-readiness check**, all locally; report results as counts or "no matches", never matched text:
   - `git log -p --all | grep -c -E "sk-ant-|api[_-]?key\s*=\s*['\"][A-Za-z0-9]"` → 0 (the word "key" in docs is fine; look for values).
   - No crawled page text in the repo: the test fixtures are synthetic (open each and confirm), `evals/questions.yaml` holds only questions and public URLs, no `*.sqlite`, `*.npy`, `*.jsonl` or `*.log` anywhere in history (`git log --all --name-only | grep -E "\.(sqlite|npy|jsonl|log)$"` → none).
   - No personal data: `git grep -n -E "@drexel\.edu|\b[0-9]{8}\b"` → only the scrubber's synthetic test values; list each file with its count in the summary.
   - `git grep -n -i "drexel" -- src`: only the base URL, the User-Agent, the disclaimer and the help-contact default.
   - `.env.example` lists every setting in `config.py` (a test enforces it, if one doesn't exist yet add it).
8. Ruff, pytest; small commits (fix A, fix B + owner questions, licence and metadata, CI, governance, README). **Do not push.**
   - 8b. **Owner split, one run:** `uv run scis-help eval retrieval --split owner`, then `uv run scis-help eval answers --split owner --judge --yes`. About $0.01. Put the numbers in the README's "Owner-written questions" row exactly as printed. Also rerun `eval answers --split dev --judge` once, because fix A changes answers; report it as a separate "dev, after URL fix" line, not in place of the earlier rows. Commit the README update.
   - Summarise for the owner, then give the owner's steps below exactly as written.

## Owner's steps (put these in your final summary, unchanged)
**Record the demo GIF (Windows, ScreenToGif):**
1. Start the demo:
   ```bash
   cd C:\Users\Admin\Documents\scis-help-assistant
   ```
   ```bash
   uv run scis-help web
   ```
2. In the browser, show: opening the bubble, one VPN or printing question with its sources, one off-topic question ("best pizza near campus") getting "not covered", a 👍, and `/desk` with "Also retrieved". 20–40 seconds; only the demo page on screen (no other tabs, bookmarks or notifications).
3. Save as `docs\img\demo.gif`, under 8 MB (ScreenToGif: 10–12 fps, about 900 px wide).
4. Commit it in GitHub Desktop: "Add demo GIF".

**Check the labels on your questions:** read the worker's list of expected pages for o01–o10. If one is wrong, tell the architect session; don't rerun the eval yourself (the split has had its one run).

**Make the repo public** (after the README, GIF and numbers are in and you've pushed): GitHub → Settings → General → Danger Zone → Change visibility → Public. Then check that the Actions tab shows a green run and the badge renders.

## Acceptance checks
1. `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`: clean, all pass, no network.
2. `uv run scis-help eval retrieval --split owner` with no owner questions prints "no questions in split owner" and exits 0; `--split all` doesn't include owner questions.
3. Fix A (if done): the tests in step 1 pass, and a live `uv run scis-help "how do I check if my internet is working for the VPN"` (or a similar question whose source names a host) shows the host name as plain text. One call, about $0.0005.
4. `.github/workflows/ci.yml` passes a YAML parse (`uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`), and on Windows the same `uv sync --locked` and `uv run pytest -q -rs` that CI runs pass locally.
5. Every public-readiness check in step 7 is reported with its result.
6. The README renders correctly in a Markdown preview: the Mermaid block, the tables and every command in its own code block. Every relative link resolves to a file that exists.

## Out of scope
- Retrieval or prompt tuning (the holdout is spent; the owner split is run by the owner once).
- Pushing, changing repo visibility, creating GitHub releases.
- The BookStack source.
- Recording the GIF (the owner does it).
