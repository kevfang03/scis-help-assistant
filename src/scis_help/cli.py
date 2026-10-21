"""Command line: ``scis-help ask | crawl | index | search | status | llm | gaps | feedback |
eval``. A first argument that is not a command is a question: ``scis-help "vpn fails"``."""

import argparse
import getpass
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from scis_help.common import paths
from scis_help.common.config import Settings, load_settings
from scis_help.common.exitcodes import (
    EXIT_CONFIG,
    EXIT_LLM,
    EXIT_NETWORK,
    EXIT_NO_INDEX,
    EXIT_OK,
)
from scis_help.common.logsetup import configure_logging
from scis_help.index import store
from scis_help.index.chunk import HEADING_SEPARATOR

NO_INDEX_HINT = "Run `scis-help crawl`, then `scis-help index`."
SET_KEY_HINT = "Run `uv run scis-help llm set-key` to save it."
SCRUB_NOTE = "Note: please don't share passwords or student IDs; I removed them."
GLOBAL_FLAGS = ("-v", "--verbose")
DEFAULT_QUESTIONS = Path("evals") / "questions.yaml"

LINKS_ONLY_REASONS = {
    "budget": "the daily LLM budget is used up",
    "no_key": f"no API key is saved. {SET_KEY_HINT}",
    "disabled": "the LLM is turned off in the settings",
    "refused": "the model declined to answer",
}


def _is_tty() -> bool:
    """Interactive prompts only when a person is typing (tests replace this)."""
    return sys.stdin.isatty()


def _read_line(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _yes(prompt: str) -> bool:
    return _read_line(prompt).strip().lower() in ("y", "yes")


# -- ask --


def _source_line(source) -> str:
    label = source.title + (HEADING_SEPARATOR + source.heading if source.heading else "")
    number = f"[{source.n}] " if source.n else "- "
    return f"{number}{label} — {source.url}"


def _print_answer(answer) -> None:
    if answer.scrub_notice:
        print(SCRUB_NOTE)
    print(answer.text)
    if answer.status == "links_only" and answer.reason:
        why = LINKS_ONLY_REASONS.get(answer.reason)
        if why is None and answer.reason.startswith("llm_failed:"):
            why = f"the LLM call failed ({answer.reason.split(':', 1)[1]})"
        print(f"(No answer written: {why or answer.reason}.)")
    shown = [source for source in answer.sources if source.n]
    also = [source for source in answer.sources if not source.n]
    if shown:
        print("\nSources:")
        for source in shown:
            print(_source_line(source))
    if also:
        print("\nAlso retrieved:")
        for source in also:
            print(_source_line(source))


def _ask(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.answer.engine import Engine
    from scis_help.answer.models import Conversation
    from scis_help.feedback import feedback
    from scis_help.index.retrieve import IndexMissing

    question = " ".join(args.question).strip()
    if not question:
        print('Ask a question, e.g. scis-help "my vpn keeps disconnecting".', file=sys.stderr)
        return EXIT_CONFIG
    try:
        engine = Engine(settings)
    except IndexMissing as error:
        print(f"No usable index: {error}. {NO_INDEX_HINT}", file=sys.stderr)
        return EXIT_NO_INDEX
    mode = "desk" if args.desk else "public"
    conversation = Conversation()
    answer = engine.ask(question, mode=mode, conversation=conversation)
    if answer.status == "clarify":
        if answer.scrub_notice:
            print(SCRUB_NOTE)
        print(answer.text)
        if not _is_tty():
            return EXIT_OK
        reply = _read_line("> ").strip()
        if not reply:
            return EXIT_OK
        answer = engine.ask(reply, mode=mode, conversation=conversation)
    _print_answer(answer)
    if answer.status == "answered" and not args.no_feedback and _is_tty():
        choice = _read_line("\nDid this help? [y/n/Enter to skip] ").strip().lower()
        if choice in ("y", "yes", "n", "no"):
            feedback.record(answer, helpful=choice.startswith("y"))
            print("Thanks.")
    return EXIT_OK


# -- llm --


def _llm_set_key(args: argparse.Namespace, settings: Settings) -> int:
    import keyring.errors

    from scis_help.llm.keys import API_KEY_PREFIX, get_api_key, set_api_key

    try:
        existing = get_api_key()
    except keyring.errors.KeyringError as error:
        print(f"The system keychain is not available ({type(error).__name__}).", file=sys.stderr)
        return EXIT_CONFIG
    if existing and not _yes("A key is already saved. Replace it? [y/N] "):
        print("Kept the saved key.")
        return EXIT_OK
    key = getpass.getpass(f"Anthropic API key (starts with {API_KEY_PREFIX}; input hidden): ")
    try:
        set_api_key(key)
    except ValueError as error:
        print(f"Not saved: {error}.", file=sys.stderr)
        return EXIT_CONFIG
    except keyring.errors.KeyringError as error:
        print(f"Not saved: the keychain failed ({type(error).__name__}).", file=sys.stderr)
        return EXIT_CONFIG
    print("Saved in the system keychain (service scis-help).")
    return EXIT_OK


def _llm_status(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.llm.client import LlmClient

    llm = settings.llm
    client = LlmClient(settings)
    usage = client.usage_today()
    state = "enabled" if llm.enabled else "disabled"
    print(f"LLM: {state}, model {llm.model}, effort {llm.effort}")
    print(f"API key saved: {'yes' if client.key_saved else 'no. ' + SET_KEY_HINT}")
    print(
        f"Today: {usage['calls']} calls; tokens {usage['input_tokens']:,} in,"
        f" {usage['output_tokens']:,} out, {usage['cache_read_tokens']:,} cache read,"
        f" {usage['cache_write_tokens']:,} cache write; ~${usage['cost_usd']:.4f}"
    )
    print(
        f"Budget: {usage['budget_tokens']:,} of {llm.daily_token_budget:,} tokens used,"
        f" {client.budget_left():,} left"
    )
    return EXIT_OK


class _Ping(BaseModel):
    ok: bool


def _llm_test(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.llm.client import LlmClient, LlmFailed, LlmUnavailable

    try:
        result = LlmClient(settings).complete(
            system="You answer health checks. Reply with ok set to true.",
            user="Health check.",
            output=_Ping,
            purpose="llm_test",
            max_tokens=200,
            force_enabled=True,
        )
    except LlmUnavailable as error:
        if error.reason == "no_key":
            print(f"LLM test: no API key saved. {SET_KEY_HINT}", file=sys.stderr)
            return EXIT_CONFIG
        print(f"LLM test: not run ({error.reason}).", file=sys.stderr)
        return EXIT_LLM
    except LlmFailed as error:
        print(f"LLM test: failed ({error.reason}).", file=sys.stderr)
        return EXIT_LLM
    if result.status != "ok":
        print(f"LLM test: failed ({result.status}).", file=sys.stderr)
        return EXIT_LLM
    usage = result.usage
    print(
        f"LLM test: ok (in {usage.input_tokens}, out {usage.output_tokens} tokens,"
        f" ~${usage.cost_usd:.4f})."
    )
    return EXIT_OK


# -- reports --


def _gaps(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.feedback.gaps import report

    rows = report(days=args.days)
    if not rows:
        print(f"No unanswered questions in the last {args.days} days.")
        return EXIT_OK
    print(f"Unanswered questions, last {args.days} days (count, scrubbed question):")
    for question, count in rows:
        print(f"{count:>4}  {question}")
    return EXIT_OK


def _feedback(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.feedback.feedback import summary

    totals = summary(days=args.days)
    print(f"Feedback, last {args.days} days: {totals['up']} helpful, {totals['down']} not helpful.")
    for item in totals["down_items"]:
        urls = ", ".join(item.get("urls") or []) or "no sources"
        print(f"- {item.get('date')} [{item.get('status')}] {item.get('question')} ({urls})")
    return EXIT_OK


# -- eval --


def _eval_setup(args: argparse.Namespace, settings: Settings):
    """(questions, retriever, exit code); questions is None when the code is an error."""
    from scis_help.eval.run import QuestionsError, load_questions, select
    from scis_help.index.retrieve import IndexMissing, Retriever

    try:
        questions = select(load_questions(args.questions), args.split)
    except QuestionsError as error:
        print(f"Eval questions: {error}", file=sys.stderr)
        return None, None, EXIT_CONFIG
    try:
        retriever = Retriever.load(settings)
    except IndexMissing as error:
        print(f"No usable index: {error}. {NO_INDEX_HINT}", file=sys.stderr)
        return None, None, EXIT_NO_INDEX
    return questions, retriever, EXIT_OK


def _eval_retrieval(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.eval.run import eval_retrieval, retrieval_table, write_results

    questions, retriever, code = _eval_setup(args, settings)
    if questions is None:
        return code
    report = eval_retrieval(retriever, questions, args.split)
    for row in report.rows:
        rank = row.rank if row.rank is not None else "-"
        confident = "yes" if row.confident else "no "
        print(
            f"{row.id}  {row.expect:<11}  rank {rank}  confident {confident}"
            f"  dense {row.best_dense:.3f}  bm25 {row.best_bm25:5.2f}"
        )
    print()
    print(retrieval_table(report))
    path = write_results(args.questions.parent / "results", "retrieval", report)
    print(f"\nResults: {path}")
    return EXIT_OK


def _eval_answers(args: argparse.Namespace, settings: Settings) -> int:
    from scis_help.answer.engine import Engine
    from scis_help.eval.run import answers_table, estimate_cost, eval_answers, write_results
    from scis_help.llm.client import LlmClient

    questions, retriever, code = _eval_setup(args, settings)
    if questions is None:
        return code
    llm = LlmClient(settings)
    if not llm.key_saved:
        print(f"No API key saved. {SET_KEY_HINT}", file=sys.stderr)
        return EXIT_CONFIG
    cost = estimate_cost(questions, judge=args.judge, prices=settings.llm)
    print(
        f"{len(questions)} questions{' with the judge' if args.judge else ''}: about"
        f" ${cost:.3f} (budget left today: {llm.budget_left():,} tokens)."
    )
    if not args.yes:
        if not _is_tty():
            print("Run again with --yes to confirm.", file=sys.stderr)
            return EXIT_CONFIG
        if not _yes("Continue? [y/N] "):
            return EXIT_OK
    engine = Engine(settings, retriever=retriever, llm=llm)
    report = eval_answers(
        engine,
        questions,
        args.split,
        retriever=retriever,
        judge=llm if args.judge else None,
        progress=print,
    )
    print()
    print(answers_table(report))
    path = write_results(args.questions.parent / "results", "answers", report)
    print(f"\nResults: {path}")
    return EXIT_OK


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

    ask = commands.add_parser("ask", help="answer a question from the support pages")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--desk", action="store_true", help="desk mode: more detail, every source")
    ask.add_argument("--no-feedback", action="store_true", help="don't ask 'Did this help?'")
    ask.set_defaults(handler=_ask)

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

    llm = commands.add_parser("llm", help="the Claude API key and today's usage")
    llm_commands = llm.add_subparsers(dest="llm_command", metavar="command", required=True)
    for name, handler, help_text in (
        ("set-key", _llm_set_key, "save the API key in the system keychain (hidden prompt)"),
        ("status", _llm_status, "settings, key saved or not, today's usage (no network)"),
        ("test", _llm_test, "one tiny API call"),
    ):
        llm_commands.add_parser(name, help=help_text).set_defaults(handler=handler)

    for name, handler, help_text in (
        ("gaps", _gaps, "questions the support pages didn't cover"),
        ("feedback", _feedback, "'Did this help?' totals and the 'no' list"),
    ):
        report = commands.add_parser(name, help=help_text)
        report.add_argument("--days", type=int, default=30, metavar="N")
        report.set_defaults(handler=handler)

    evaluate = commands.add_parser("eval", help="retrieval and answer metrics")
    eval_commands = evaluate.add_subparsers(dest="eval_command", metavar="command", required=True)
    for name, handler, help_text in (
        ("retrieval", _eval_retrieval, "hit@3, hit@5, MRR and the gate (free, no LLM)"),
        ("answers", _eval_answers, "the full engine (paid; asks first)"),
    ):
        sub = eval_commands.add_parser(name, help=help_text)
        sub.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
        sub.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, metavar="PATH")
        if name == "answers":
            sub.add_argument("--judge", action="store_true", help="rate groundedness with Haiku")
            sub.add_argument("--yes", action="store_true", help="don't ask before spending")
        sub.set_defaults(handler=handler)
    return parser


def _route_question(argv: list[str], commands: set[str]) -> list[str]:
    """``scis-help [--desk] "question"`` -> ``scis-help ask [--desk] "question"``."""
    if any(arg in ("-h", "--help") for arg in argv):
        return argv
    positional = [arg for arg in argv if not arg.startswith("-")]
    if not positional or positional[0] in commands:
        return argv
    flags = [arg for arg in argv if arg in GLOBAL_FLAGS]
    return flags + ["ask"] + [arg for arg in argv if arg not in GLOBAL_FLAGS]


def _command_names(parser: argparse.ArgumentParser) -> set[str]:
    return {
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }


def _utf8_output() -> None:
    """Page titles and the › separator are not cp1252: write UTF-8 when piped on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    parser = _parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_route_question(argv, _command_names(parser)))
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
    if getattr(args, "days", None) is not None and args.days < 1:
        print("--days must be at least 1.", file=sys.stderr)
        return EXIT_CONFIG
    configure_logging(paths.log_dir(), verbose=args.verbose)
    return handler(args, settings)


if __name__ == "__main__":
    sys.exit(main())
