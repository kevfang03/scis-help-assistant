"""The public SCIS support site (support.cci.drexel.edu): a polite crawler, an extractor, and
the ``Source`` that reads what the crawler stored.

Crawl rules (ARCHITECTURE.md D3): pages listed in the site's sitemap only, on the base host
only; robots.txt checked first on every crawl; one request at a time with ``delay_seconds``
between requests; redirects followed by hand so nothing off the host is ever requested; a 429
stops the crawl. Only the extracted text, URL, title, section and lastmod are stored.
"""

import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from bs4.element import NavigableString, PreformattedString, Tag

from scis_help.common import paths, tls
from scis_help.common.config import Settings
from scis_help.sources.base import Audience, Document
from scis_help.sources.pages import PageStore, StoredPage

log = logging.getLogger(__name__)

SOURCE_NAME = "support_site"
USER_AGENT = (
    "scis-help-assistant/0.1 (unofficial student project; "
    "+https://github.com/kevfang03/scis-help-assistant)"
)
TITLE_SUFFIX = " · CCI Technology Services and Support"
LAST_CRAWL_KEY = "last_crawl"

_MAX_RETRIES = 2
_BACKOFF_SECONDS = 2.0
_MAX_REDIRECTS = 3
_MAX_CHILD_SITEMAPS = 50
# Stop when this many pages in a row fail on network errors or 5xx: the site or the link is down.
_MAX_CONSECUTIVE_NETWORK_FAILURES = 5
_HTML_SUFFIXES = {"", ".html", ".htm"}

# Stop reasons that mean the crawl could not finish (CLI exit 3); "max_pages" is not one.
NETWORK_STOP_REASONS = frozenset(
    {"robots_unavailable", "sitemap_unavailable", "sitemap_empty", "rate_limited", "network"}
)


@dataclass(frozen=True, slots=True)
class CrawlReport:
    fetched: int
    unchanged: int
    removed: int
    failed: int
    skipped_robots: int
    stopped_reason: str | None


class ExtractionFailed(ValueError):
    """The page has neither ``article.td-article`` nor ``main#main``."""


# --- Extraction -------------------------------------------------------------------------------

# Page chrome removed before reading the text: Docsy navigation, breadcrumbs, page meta, the
# table of contents, feedback and "Still stuck?" help blocks, section-index widgets.
_REMOVE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "form",
    "input",
    "button",
    "label",
    "select",
    "svg",
    "iframe",
    "h1",
)
_REMOVE_SELECTORS = (
    ".td-breadcrumb",
    ".breadcrumb",
    ".td-page-meta",
    ".td-toc",
    "#TableOfContents",
    ".td-feedback",
    ".feedback",
    ".td-help",
    ".td-article__footer",
    ".td-views",
    ".td-filter",
    ".td-sec__meta",
    ".visually-hidden",
    ".d-print-none",
    "[aria-hidden=true]",
    "[hidden]",
)
_INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "cite",
        "code",
        "data",
        "dfn",
        "em",
        "i",
        "kbd",
        "mark",
        "q",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
        "img",
    }
)
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_WHITESPACE = re.compile(r"\s+")
_LAST_MODIFIED = re.compile(r"^(last modified|last updated)\b", re.IGNORECASE)


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", _ZERO_WIDTH.sub("", text)).strip()


class _TextWriter:
    """Collects inline text into lines; block elements start a new line."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._current: list[str] = []

    def text(self, value: str) -> None:
        self._current.append(value)

    def newline(self) -> None:
        line = _clean("".join(self._current))
        self._current = []
        if line and line != "-":
            self.lines.append(line)

    def line(self, value: str) -> None:
        self.newline()
        if value:
            self.lines.append(value)


def _render(node: Tag, out: _TextWriter) -> None:
    for child in node.children:
        if isinstance(child, PreformattedString):  # comments, doctype, CDATA
            continue
        if isinstance(child, NavigableString):
            out.text(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name == "br":
            out.newline()
        elif name in ("h2", "h3"):
            heading = _clean(child.get_text(" "))
            marker = "## " if name == "h2" else "### "
            out.line(marker + heading if heading else "")
        elif name in ("h4", "h5", "h6"):
            out.line(_clean(child.get_text(" ")))
        elif name == "pre":
            out.newline()
            for raw in _ZERO_WIDTH.sub("", child.get_text()).splitlines():
                if raw.strip():
                    out.line(raw.rstrip())
        elif name == "tr":
            cells = [_clean(cell.get_text(" ")) for cell in child.find_all(["th", "td"])]
            out.line(" | ".join(cell for cell in cells if cell))
        elif name == "li":
            out.newline()
            out.text("- ")
            _render(child, out)
            out.newline()
        elif name in _INLINE_TAGS:
            _render(child, out)
        else:  # p, div, section, table, ul, ol, dl, blockquote, figure ...
            out.newline()
            _render(child, out)
            out.newline()


def _title(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading is not None and _clean(heading.get_text(" ")):
        return _clean(heading.get_text(" "))
    title = _clean(soup.title.get_text()) if soup.title is not None else ""
    if title.endswith(TITLE_SUFFIX.strip()):
        title = title[: -len(TITLE_SUFFIX.strip())].rstrip(" ·|-–—").strip()
    return title


def extract(html: str, url: str) -> tuple[str, str]:
    """(title, text) of one support page.

    The text is plain: ``## `` before each h2, ``### `` before each h3, list items as ``- ``,
    table rows as cells joined by `` | ``, code blocks kept line by line. Raises
    ExtractionFailed when the page has no article or main content.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup)
    content = soup.select_one("article.td-article") or soup.select_one("main#main")
    if content is None:
        raise ExtractionFailed(url)
    for tag in content.find_all(_REMOVE_TAGS):
        tag.decompose()
    for selector in _REMOVE_SELECTORS:
        for tag in content.select(selector):
            tag.decompose()
    out = _TextWriter()
    _render(content, out)
    out.newline()
    lines = [line for line in out.lines if not _LAST_MODIFIED.match(line)]
    return title, "\n".join(lines)


# --- Crawling ---------------------------------------------------------------------------------


class _RateLimited(Exception):
    pass


class _Unavailable(Exception):
    """Retries ran out on 5xx, timeouts or connection errors."""


class _Fetcher:
    """One request at a time, ``delay`` seconds apart (back-off waits count as the gap)."""

    def __init__(self, client: httpx.Client, delay: float, sleep: Callable[[float], None]) -> None:
        self.client = client
        self.delay = delay
        self._sleep = sleep
        self._first = True

    def get(self, url: str) -> httpx.Response:
        problem = "error"
        for attempt in range(_MAX_RETRIES + 1):
            if attempt:
                self._sleep(max(self.delay, _BACKOFF_SECONDS * 2 ** (attempt - 1)))
            elif not self._first:
                self._sleep(self.delay)
            self._first = False
            try:
                response = self.client.get(url)
            except httpx.TransportError as error:  # timeouts, connection errors
                problem = type(error).__name__
                continue
            if response.status_code == 429:
                raise _RateLimited(url)
            if response.status_code >= 500:
                problem = str(response.status_code)
                continue
            return response
        raise _Unavailable(problem)

    def get_on_host(self, url: str, host: str) -> httpx.Response:
        """GET, following up to three redirects that stay on ``host`` (https only)."""
        for _ in range(_MAX_REDIRECTS + 1):
            response = self.get(url)
            if not response.is_redirect:
                return response
            target = response.url.join(response.headers.get("location", ""))
            if target.scheme != "https" or target.host != host:
                raise _OffHost(str(target.copy_with(query=None, fragment=None)))
            url = str(target)
        raise _OffHost("too many redirects")


class _OffHost(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Entry:
    url: str
    lastmod: str | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_sitemap(xml_text: str) -> tuple[list[_Entry], list[str]]:
    """(page entries, child sitemap URLs). Raises ET.ParseError on bad XML."""
    root = ET.fromstring(xml_text)
    entries: list[_Entry] = []
    children: list[str] = []
    is_index = _local(root.tag) == "sitemapindex"
    for item in root:
        if _local(item.tag) not in ("url", "sitemap"):
            continue
        loc = lastmod = None
        for field in item:
            if _local(field.tag) == "loc" and field.text:
                loc = field.text.strip()
            elif _local(field.tag) == "lastmod" and field.text:
                lastmod = field.text.strip()
        if not loc:
            continue
        if is_index:
            children.append(loc)
        else:
            entries.append(_Entry(loc, lastmod))
    return entries, children


def _wanted(url: str, base: httpx.URL) -> bool:
    """An https page on the base host, under the base path, that looks like HTML."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    if parsed.scheme != "https" or parsed.host != base.host:
        return False
    if not parsed.path.startswith(base.path):
        return False
    return PurePosixPath(parsed.path).suffix.lower() in _HTML_SUFFIXES


def _parse_time(value: str) -> datetime:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _unchanged(sitemap_lastmod: str | None, stored_lastmod: str | None) -> bool:
    """True when the sitemap's lastmod is not newer than the stored one."""
    if sitemap_lastmod is None or stored_lastmod is None:
        return False
    try:
        return _parse_time(sitemap_lastmod) <= _parse_time(stored_lastmod)
    except ValueError:
        return sitemap_lastmod <= stored_lastmod


def _section(url: str) -> str:
    parts = [part for part in httpx.URL(url).path.split("/") if part]
    return parts[0] if parts else ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def crawl(
    settings: Settings,
    *,
    full: bool = False,
    max_pages: int | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlReport:
    """Fetch new and changed sitemap pages into ``pages.sqlite``; see the module docstring."""
    config = settings.crawl
    base_text = config.base_url if config.base_url.endswith("/") else config.base_url + "/"
    base = httpx.URL(base_text)
    limit = max_pages if max_pages is not None else config.max_pages
    client = httpx.Client(
        verify=tls.ssl_context(),
        timeout=config.timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,  # followed by hand in _Fetcher, on-host only
        transport=transport,
    )
    with client:
        fetcher = _Fetcher(client, config.delay_seconds, sleep)
        report, in_sitemap = _crawl(fetcher, base, full=full, limit=limit)
    summary = {"at": _now(), "in_sitemap": in_sitemap, **asdict(report)}
    with PageStore(paths.pages_db()) as store:
        store.set_meta(LAST_CRAWL_KEY, json.dumps(summary))
    if report.stopped_reason in NETWORK_STOP_REASONS:
        log.warning("crawl stopped early", extra={"reason": report.stopped_reason})
    log.info("crawl finished", extra=summary)
    return report


def _stop(reason: str) -> tuple[CrawlReport, int]:
    """A crawl that stopped before reading the sitemap's pages: nothing fetched or removed."""
    report = CrawlReport(
        fetched=0, unchanged=0, removed=0, failed=0, skipped_robots=0, stopped_reason=reason
    )
    return report, 0


def _read_robots(fetcher: _Fetcher, base: httpx.URL) -> RobotFileParser | None | str:
    """A parser, None (no robots.txt: allow all), or a stop reason."""
    url = str(base.join("/robots.txt"))
    try:
        response = fetcher.get_on_host(url, base.host)
    except _RateLimited:
        return "rate_limited"
    except (_Unavailable, _OffHost):
        return "robots_unavailable"
    if response.status_code in (404, 410):
        return None
    if response.status_code != 200:
        log.warning("robots.txt unavailable", extra={"url": url, "status": response.status_code})
        return "robots_unavailable"
    parser = RobotFileParser(url)
    parser.parse(response.text.splitlines())
    return parser


def _read_sitemap(fetcher: _Fetcher, base: httpx.URL) -> list[_Entry] | str:
    """Sitemap page entries (following one level of sitemap index), or a stop reason."""
    pending = [str(base.join("sitemap.xml"))]
    entries: list[_Entry] = []
    seen: set[str] = set()
    while pending and len(seen) <= _MAX_CHILD_SITEMAPS:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            response = fetcher.get_on_host(url, base.host)
        except _RateLimited:
            return "rate_limited"
        except (_Unavailable, _OffHost):
            return "sitemap_unavailable"
        if response.status_code != 200:
            log.warning("sitemap unavailable", extra={"url": url, "status": response.status_code})
            return "sitemap_unavailable"
        try:
            found, children = _parse_sitemap(response.text)
        except ET.ParseError:
            log.warning("sitemap is not valid XML", extra={"url": url})
            return "sitemap_unavailable"
        entries.extend(found)
        pending.extend(child for child in children if _wanted_sitemap(child, base))
    return entries


def _wanted_sitemap(url: str, base: httpx.URL) -> bool:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    return parsed.scheme == "https" and parsed.host == base.host


def _crawl(
    fetcher: _Fetcher, base: httpx.URL, *, full: bool, limit: int
) -> tuple[CrawlReport, int]:
    """(report, number of wanted pages in the sitemap)."""
    robots = _read_robots(fetcher, base)
    if isinstance(robots, str):
        return _stop(robots)
    if robots is not None:
        crawl_delay = robots.crawl_delay(USER_AGENT)
        if crawl_delay is not None:
            fetcher.delay = max(fetcher.delay, float(crawl_delay))

    sitemap = _read_sitemap(fetcher, base)
    if isinstance(sitemap, str):
        return _stop(sitemap)

    entries: dict[str, _Entry] = {}
    for entry in sitemap:
        url = entry.url.split("#", 1)[0]
        if _wanted(url, base) and url not in entries:
            entries[url] = _Entry(url, entry.lastmod)
    if not entries:
        # Never let an empty or broken sitemap wipe the store.
        return _stop("sitemap_empty")

    allowed = [e for e in entries.values() if robots is None or robots.can_fetch(USER_AGENT, e.url)]
    skipped_robots = len(entries) - len(allowed)

    fetched = unchanged = failed = attempts = network_failures = 0
    stopped_reason: str | None = None
    with PageStore(paths.pages_db()) as store:
        stored = store.lastmods(SOURCE_NAME)
        removed = store.delete_missing({e.url for e in allowed}, SOURCE_NAME)
        store.commit()
        for entry in allowed:
            if not full and entry.url in stored and _unchanged(entry.lastmod, stored[entry.url]):
                unchanged += 1
                continue
            if attempts >= limit:
                stopped_reason = "max_pages"
                break
            attempts += 1
            try:
                response = fetcher.get_on_host(entry.url, base.host)
            except _RateLimited:
                stopped_reason = "rate_limited"
                log.warning("rate limited (429); crawl stopped", extra={"url": entry.url})
                break
            except _Unavailable as error:
                failed += 1
                network_failures += 1
                log.warning("page failed", extra={"url": entry.url, "status": str(error)})
                if network_failures >= _MAX_CONSECUTIVE_NETWORK_FAILURES:
                    stopped_reason = "network"
                    break
                continue
            except _OffHost:
                failed += 1
                log.warning("page redirected off the site", extra={"url": entry.url})
                continue
            network_failures = 0
            content_type = response.headers.get("content-type", "")
            if response.status_code != 200 or "html" not in content_type.lower():
                failed += 1
                log.warning(
                    "page failed",
                    extra={"url": entry.url, "status": response.status_code},
                )
                continue
            try:
                title, text = extract(response.text, entry.url)
            except ExtractionFailed:
                failed += 1
                log.warning("page failed", extra={"url": entry.url, "status": "no_content"})
                continue
            store.upsert(
                StoredPage(
                    url=entry.url,
                    title=title,
                    section=_section(entry.url),
                    text=text,
                    lastmod=entry.lastmod,
                    fetched_at=_now(),
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    source=SOURCE_NAME,
                )
            )
            fetched += 1
            if fetched % 25 == 0:
                store.commit()  # keep progress if the crawl is interrupted

    report = CrawlReport(
        fetched=fetched,
        unchanged=unchanged,
        removed=removed,
        failed=failed,
        skipped_robots=skipped_robots,
        stopped_reason=stopped_reason,
    )
    return report, len(entries)


def last_crawl(db_path: Path | None = None) -> dict[str, object] | None:
    """The summary the last crawl stored (time, sitemap size, report counts), if any."""
    path = db_path or paths.pages_db()
    if not path.exists():
        return None
    with PageStore(path) as store:
        value = store.get_meta(LAST_CRAWL_KEY)
    return None if value is None else json.loads(value)


# --- Source -----------------------------------------------------------------------------------


class SupportSiteSource:
    """The crawled support pages, read from ``pages.sqlite``. No network."""

    name = SOURCE_NAME
    audience: Audience = "public"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or paths.pages_db()

    def iter_documents(self) -> Iterator[Document]:
        if not self.db_path.exists():
            return
        with PageStore(self.db_path) as store:
            pages = list(store.iter_pages(SOURCE_NAME))
        for page in pages:
            yield Document(
                url=page.url,
                title=page.title,
                text=page.text,
                section=page.section,
                lastmod=page.lastmod,
                source=SOURCE_NAME,
                audience=self.audience,
            )
