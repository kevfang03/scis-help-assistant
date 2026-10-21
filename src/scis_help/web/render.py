"""Answer markdown to safe HTML for the widget.

The model writes the markdown, so nothing in it is trusted: raw HTML is off, headings, quotes,
rules, images and reference definitions are not parsed, and a link survives only when its href
is one of the answer's source URLs (which come from the index, never from the model) or a
trusted URL from the settings (the help-desk contact); any other link becomes its text. Those
same URLs written bare in the text become links, and ``[n]`` references become superscript
links to source ``n``. The output uses only p, ul, ol, li, strong, em, code, pre, a, br and sup.
"""

import re
from collections.abc import Sequence
from typing import Any, Protocol

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

LINK_ATTRS = 'target="_blank" rel="noopener noreferrer"'
_URL = r"https?://[^\s<>\"'()]+[^\s<>\"'().,;:!?]"
_IN_TEXT = re.compile(rf"\[(?P<n>\d{{1,2}})\]|{_URL}")  # [n] references and bare URLs


class Source(Protocol):
    n: int
    url: str


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False})
    md.disable(["heading", "lheading", "blockquote", "hr", "reference", "image", "html_block"])
    md.disable(["html_inline"])
    md.add_render_rule("link_open", _link_open)
    md.add_render_rule("link_close", _link_close)
    md.add_render_rule("text", _text)
    md.add_render_rule("fence", _code_block)
    md.add_render_rule("code_block", _code_block)
    return md


def _link_open(self: Any, tokens: list, idx: int, options: Any, env: dict) -> str:
    href = tokens[idx].attrGet("href") or ""
    url = env["allowed"].get(href)
    env["links"].append(url is not None)
    if url is None:
        return ""
    return f'<a href="{escapeHtml(url)}" {LINK_ATTRS}>'


def _link_close(self: Any, tokens: list, idx: int, options: Any, env: dict) -> str:
    return "</a>" if env["links"].pop() else ""


def _text(self: Any, tokens: list, idx: int, options: Any, env: dict) -> str:
    content = tokens[idx].content
    if env["links"]:  # inside a link: no nested anchors
        return escapeHtml(content)
    parts: list[str] = []
    end = 0
    for match in _IN_TEXT.finditer(content):
        parts.append(escapeHtml(content[end : match.start()]))
        end = match.end()
        label = escapeHtml(match.group(0))
        reference = match.group("n") is not None
        if reference:
            url = env["numbered"].get(int(match.group("n")))
        else:  # a bare URL
            url = env["allowed"].get(match.group(0))
        if url is None:
            parts.append(label)
            continue
        link = f'<a href="{escapeHtml(url)}" {LINK_ATTRS}>{label}</a>'
        parts.append(f"<sup>{link}</sup>" if reference else link)
    parts.append(escapeHtml(content[end:]))
    return "".join(parts)


def _code_block(self: Any, tokens: list, idx: int, options: Any, env: dict) -> str:
    return f"<pre><code>{escapeHtml(tokens[idx].content)}</code></pre>\n"


_MD = _parser()


def render_answer(text: str, sources: Sequence[Source], trusted: Sequence[str] = ()) -> str:
    """Sanitised HTML for ``text``; links only to ``sources`` URLs and ``trusted`` URLs."""
    allowed: dict[str, str] = {}
    for url in [*(source.url for source in sources), *trusted]:
        allowed[url] = url
        allowed[_MD.normalizeLink(url)] = url
    numbered = {source.n: source.url for source in sources if source.n > 0}
    env = {"allowed": allowed, "numbered": numbered, "links": []}
    return _MD.render(text, env)


def urls_in(text: str) -> list[str]:
    """The http(s) URLs written in ``text`` (for the help-contact setting)."""
    return re.findall(_URL, text)
