# SCIS Help Assistant

**Unofficial student project.** Not affiliated with, endorsed by or deployed on any Drexel
University or SCIS site.

A help assistant that finds the right page in the public SCIS support documentation
(support.cci.drexel.edu), answers briefly and links every page it used. This stub covers the
retrieval half; the full README comes later.

## Setup

Needs [uv](https://docs.astral.sh/uv/). From the repository folder:

```bash
uv sync
```

## Crawl the public support pages and build the index

The crawler reads only the pages listed in the site's sitemap, one request every 1.5 seconds,
so a first full crawl takes about 17 minutes. Later crawls fetch only changed pages.

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
