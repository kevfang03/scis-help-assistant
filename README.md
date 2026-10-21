# SCIS Help Assistant

**Unofficial student project.** Not affiliated with, endorsed by or deployed on any Drexel
University or SCIS site.

A help assistant that finds the right page in the public SCIS support documentation
(support.cci.drexel.edu), answers briefly and links every page it used. This stub covers setup and
the commands; the full README comes later.

## Setup

Needs [uv](https://docs.astral.sh/uv/). From the repository folder:

```bash
uv sync
```

## Crawl the public support pages and build the index

The crawler reads only the pages listed in the site's sitemap, one request every 1.5 seconds,
so a first full crawl takes about 23 minutes. Later crawls fetch only changed pages.

```bash
uv run scis-help crawl
```

```bash
uv run scis-help index
```

```bash
uv run scis-help search "vpn won't connect"
```

```bash
uv run scis-help status
```

Page text, the index and logs live in your user data folder (`scis-help status` prints it),
never in the repository.

## Answers with Claude

The answers come from Claude Haiku through the Anthropic API. The API key is kept only in your
system keychain (Windows Credential Manager or macOS Keychain), never in a file. Save it with a
hidden prompt:

```bash
uv run scis-help llm set-key
```

Check the settings, whether a key is saved, and today's usage (no network):

```bash
uv run scis-help llm status
```

Make one tiny test call:

```bash
uv run scis-help llm test
```

Ask a question (add `--desk` for the help-desk view with every retrieved page):

```bash
uv run scis-help "my vpn keeps disconnecting at home"
```

Without a key, or when the daily token budget is used up, the assistant still lists the most
relevant pages. Please don't type passwords or student IDs; they are removed before anything
is searched, sent or logged.

## Reports and evaluation

Questions the support pages didn't cover, and "Did this help?" answers:

```bash
uv run scis-help gaps
```

```bash
uv run scis-help feedback
```

Retrieval metrics on the eval questions (free, no API calls):

```bash
uv run scis-help eval retrieval --split dev
```

Answer metrics, with a Haiku judge for groundedness (a few cents; asks before spending):

```bash
uv run scis-help eval answers --split dev --judge
```

## Local web app and chat widget

Starts a local demo page with the chat bubble on `http://127.0.0.1:8765/` (desk mode on
`/desk`, this computer only) and opens your browser. Needs the index from the steps above.
Without a saved API key the answers are links only.

```bash
uv run scis-help web
```

To embed the widget on another local page, add its origin to
`SCIS_HELP_WEB__ALLOWED_ORIGINS` (see `.env.example`) and put one tag on the page:

```html
<script src="http://127.0.0.1:8765/widget.js" data-mode="public" defer></script>
```
