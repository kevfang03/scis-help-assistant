import re

import pytest

from scis_help.web.render import render_answer
from tests.web.fakes import PRINT_URL, VPN_URL, SourceLink

SOURCES = [
    SourceLink(1, VPN_URL, "Example VPN", "Windows", True),
    SourceLink(2, PRINT_URL, "Lab printers", "", True),
]
ALLOWED_TAGS = {"p", "ul", "ol", "li", "strong", "em", "code", "pre", "a", "br", "sup"}


def tags(html: str) -> set[str]:
    return {name.lower() for name in re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html)}


def test_basic_markdown_and_references():
    html = render_answer(
        "Open the **VPN** client [1].\n\n1. Pick *Drexel*\n2. Run `vpn --connect`\n\n- a\n- b",
        SOURCES,
    )
    assert "<strong>VPN</strong>" in html
    assert "<em>Drexel</em>" in html
    assert "<code>vpn --connect</code>" in html
    assert "<ol>" in html and "<ul>" in html
    assert (
        f'<sup><a href="{VPN_URL}" target="_blank" rel="noopener noreferrer">[1]</a></sup>' in html
    )
    assert tags(html) <= ALLOWED_TAGS


def test_unknown_reference_number_stays_text():
    html = render_answer("See [7] and [2].", SOURCES)
    assert "[7]" in html and "<sup>" in html
    assert html.count("<a ") == 1


def test_link_to_a_source_url_survives():
    html = render_answer(f"Read [the VPN page]({VPN_URL}).", SOURCES)
    assert f'<a href="{VPN_URL}" target="_blank" rel="noopener noreferrer">the VPN page</a>' in html


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script> hi",
        '<img src=x onerror="alert(1)">',
        "<a href='https://evil.test/'>click</a>",
        "[click](javascript:alert(1))",
        "[click](JaVaScRiPt:alert(1))",
        "[click](data:text/html;base64,PHNjcmlwdD4=)",
        "[click](https://evil.test/phish)",
        "<https://evil.test/auto>",
        "![x](https://evil.test/pixel.png)",
        "[1]: https://evil.test/ref\n\nSee [1] and [x][1].",
        '[ok](https://evil.test/ "title" onmouseover="alert(1)")',
        "# Heading\n\n> quote\n\n---\n\n<div onclick=alert(1)>x</div>",
        "```html\n<script>alert(1)</script>\n```",
        "`<b onmouseover=alert(1)>`",
    ],
)
def test_hostile_markdown_never_survives(text):
    html = render_answer(text, SOURCES)
    lowered = html.lower()
    assert "<script" not in lowered
    assert "<img" not in lowered and "<div" not in lowered and "<h1" not in lowered
    assert not re.search(r"<[^>]*\son\w+\s*=", lowered)  # no event-handler attributes
    assert "javascript:" not in re.findall(r'href="([^"]*)"', lowered).__str__()
    for href in re.findall(r'href="([^"]*)"', html):
        assert href in {VPN_URL, PRINT_URL}
    assert "evil.test" not in "".join(re.findall(r'href="([^"]*)"', html))
    assert tags(html) <= ALLOWED_TAGS


def test_external_link_keeps_its_text_only():
    html = render_answer("Try [this site](https://evil.test/x) now.", SOURCES)
    assert "this site" in html and "evil.test" not in html
    assert "<a" not in html


def test_no_nested_anchor_for_reference_inside_link_text():
    html = render_answer(f"[see [1]]({VPN_URL})", SOURCES)
    assert html.count("<a ") == 1 and "<sup>" not in html


def test_desk_also_retrieved_sources_are_linkable_but_not_numbered():
    sources = [*SOURCES, SourceLink(0, "https://support.example.test/other/", "Other", "", False)]
    html = render_answer("[0] and [other](https://support.example.test/other/)", sources)
    assert "<sup>" not in html  # n = 0 is not a reference number
    assert 'href="https://support.example.test/other/"' in html


def test_bare_trusted_and_source_urls_become_links_others_stay_text():
    contact = "https://support.example.test/"
    html = render_answer(
        f'Talk to a person at {contact} ("Talk to a person"), see {VPN_URL}. Not https://evil.test/.',
        SOURCES,
        trusted=[contact],
    )
    assert f'<a href="{contact}" target="_blank" rel="noopener noreferrer">{contact}</a>' in html
    assert f'<a href="{VPN_URL}"' in html
    assert "https://evil.test/" in html and 'href="https://evil.test/' not in html
    assert "&quot;Talk to a person&quot;" in html


def test_urls_in():
    from scis_help.web.render import urls_in

    assert urls_in('the help desk: https://support.example.test/ ("Talk to a person")') == [
        "https://support.example.test/"
    ]


def test_backticked_urls_stay_plain_code_never_links():
    # Fix A: the engine keeps a URL found verbatim in a source only as code; even a source or
    # trusted URL in backticks must not become a link.
    contact = "https://support.example.test/"
    html = render_answer(
        f"Open `http://neverssl.com`, ping `tux.cs.example.edu` or see `{VPN_URL}` [1].",
        SOURCES,
        trusted=[contact],
    )
    assert "<code>http://neverssl.com</code>" in html
    assert "<code>tux.cs.example.edu</code>" in html
    assert f"<code>{VPN_URL}</code>" in html
    assert html.count("<a ") == 1  # only the [1] reference
    assert re.findall(r'href="([^"]*)"', html) == [VPN_URL]
