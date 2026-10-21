"""Command line: ``scis-help crawl | index | search | status``."""

import argparse
import sys
from collections.abc import Callable

from pydantic import ValidationError

from scis_help.common import paths
from scis_help.common.config import Settings, load_settings
from scis_help.common.exitcodes import EXIT_CONFIG, EXIT_NETWORK, EXIT_NO_INDEX, EXIT_OK
from scis_help.common.logsetup import configure_logging
from scis_help.index import store
from scis_help.index.chunk import HEADING_SEPARATOR

NO_INDEX_HINT = "Run `scis-help crawl`, then `scis-help index`."


def _crawl(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.sources.support_site import NETWORK_STOP_REASONS, crawl, last_crawl

    report = crawl(settings, full=args.full, max_pages=args.max_pages)
    in_sitemap = (last_crawl() or {}).get("in_sitemap", "?")
    line = (
        f"Crawl: {in_sitemap} in sitemap, {report.fetched} fetched, {report.unchanged} unchanged,"
        f" {report.removed} removed, {report.failed} failed"
    )
    if report.skipped_robots:
        line += f", {report.skipped_robots} skipped by robots.txt"
    print(line + ".")
    if report.stopped_reason is not None:
        print(f"Stopped early: {report.stopped_reason}.")
    return EXIT_NETWORK if report.stopped_reason in NETWORK_STOP_REASONS else EXIT_OK


def _index(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.index.build import NoDocuments, build_index
    from scis_help.sources.support_site import SupportSiteSource

    try:
        report = build_index(settings, [SupportSiteSource()])
    except NoDocuments:
        print("No pages stored yet. Run `scis-help crawl` first.", file=sys.stderr)
        return EXIT_NO_INDEX
    print(
        f"Index: {report.pages:,} pages, {report.chunks:,} chunks, model {report.model_id},"
        f" {report.seconds:.1f} s."
    )
    return EXIT_OK


def _search(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.index.retrieve import IndexMissing, Retriever

    try:
        retriever = Retriever.load(settings)
    except IndexMissing as error:
        print(f"No usable index: {error}. {NO_INDEX_HINT}", file=sys.stderr)
        return EXIT_NO_INDEX
    result = retriever.search(args.query, k=args.k, audience="desk" if args.desk else "public")
    for hit in result.hits:
        chunk = hit.chunk
        label = chunk.title + (HEADING_SEPARATOR + chunk.heading if chunk.heading else "")
        bm25 = f"{hit.bm25:5.2f}" if hit.bm25 is not None else "    -"
        print(
            f"{hit.rank:>2}. rrf {hit.rrf:.4f}  dense {hit.dense:.3f}  bm25 {bm25}  {label}"
            f"  {chunk.url}"
        )
    if not result.hits:
        print("No results.")
    print(
        f"confident: {'yes' if result.confident else 'no'} (best dense {result.best_dense:.2f},"
        f" best bm25 {result.best_bm25:.1f})"
    )
    return EXIT_OK


def _status(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.sources.pages import PageStore
    from scis_help.sources.support_site import last_crawl

    print(f"Data folder: {paths.data_dir()}")
    db = paths.pages_db()
    pages = 0
    if db.exists():
        with PageStore(db) as pages_store:
            pages = pages_store.count()
    summary = last_crawl()
    if summary is None:
        print(f"Pages stored: {pages:,} (never crawled)")
    else:
        crawl_note = (
            f"last crawl {summary.get('at')}: {summary.get('in_sitemap')} in sitemap,"
            f" {summary.get('fetched')} fetched, {summary.get('failed')} failed"
        )
        if summary.get("stopped_reason"):
            crawl_note += f", stopped early: {summary['stopped_reason']}"
        print(f"Pages stored: {pages:,} ({crawl_note})")
    manifest = store.read_manifest(paths.index_dir())
    if manifest is None:
        print(f"Index: none. {NO_INDEX_HINT}")
    else:
        print(
            f"Index: {manifest.get('chunks', 0):,} chunks from {manifest.get('pages', 0):,}"
            f" pages, model {manifest.get('model_id')}, built {manifest.get('built_at')}"
        )
        if manifest.get("model_id") != settings.index.embed_model:
            print(f"  The settings name {settings.index.embed_model}: run `scis-help index` again.")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scis-help",
        description="SCIS Help Assistant (unofficial student project): search the public SCIS"
        " support pages.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on stderr")
    commands = parser.add_subparsers(dest="command", metavar="command")

    crawl = commands.add_parser("crawl", help="fetch new and changed support pages")
    crawl.add_argument("--full", action="store_true", help="refetch every page")
    crawl.add_argument("--max-pages", type=int, metavar="N", help="fetch at most N pages")
    crawl.set_defaults(handler=_crawl)

    index = commands.add_parser("index", help="rebuild the search index from stored pages")
    index.set_defaults(handler=_index)

    search = commands.add_parser("search", help="show the best-matching pages (no LLM)")
    search.add_argument("query")
    search.add_argument("--k", type=int, metavar="N", help="number of results (default 5)")
    search.add_argument("--desk", action="store_true", help="include desk-only sources")
    search.set_defaults(handler=_search)

    status = commands.add_parser("status", help="data folder, pages and index (no network)")
    status.set_defaults(handler=_status)
    return parser


def _utf8_output() -> None:
    """Page titles and the › separator are not cp1252: write UTF-8 when piped on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    parser = _parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace, Settings], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_OK
    try:
        settings = load_settings()
    except ValidationError as error:
        print(f"Configuration error:\n{error}", file=sys.stderr)
        return EXIT_CONFIG
    if getattr(args, "max_pages", None) is not None and args.max_pages < 1:
        print("--max-pages must be at least 1.", file=sys.stderr)
        return EXIT_CONFIG
    if getattr(args, "k", None) is not None and not 1 <= args.k <= 20:
        print("--k must be between 1 and 20.", file=sys.stderr)
        return EXIT_CONFIG
    configure_logging(paths.log_dir(), verbose=args.verbose)
    return handler(args, settings)


if __name__ == "__main__":
    sys.exit(main())
