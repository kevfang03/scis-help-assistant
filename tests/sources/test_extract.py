import pytest

from scis_help.sources.support_site import ExtractionFailed, extract
from tests.fakes import BASE, fixture_text


def _vpn() -> tuple[str, str]:
    return extract(fixture_text("vpn.html"), BASE + "getting-connected/example-vpn/")


def test_title_comes_from_h1():
    title, _ = _vpn()
    assert title == "Connecting to the Example VPN"


def test_title_falls_back_to_title_tag_without_suffix():
    page = fixture_text("vpn.html").replace(
        '<h1 class="td-article__title" id="td-article-title">Connecting to the Example VPN</h1>',
        "",
    )
    title, text = extract(page, BASE)
    assert title == "Connecting to the Example VPN"
    assert "CCI Technology Services and Support" not in title


def test_heading_markers_and_order():
    _, text = _vpn()
    lines = text.splitlines()
    assert lines[0] == "How to install the Example VPN client and connect from off campus."
    assert "## Before you begin" in lines
    assert "## Windows" in lines
    assert lines.index("## Windows") < lines.index("### Install the client")
    assert lines.index("### Install the client") < lines.index("### Connect")


def test_lists_line_breaks_tables_and_code():
    _, text = _vpn()
    lines = text.splitlines()
    assert "- Download the client from the software portal." in lines
    assert "You need an active example account and the Example Connect client." in lines
    assert "Off campus, the VPN is required for the lab file server." in lines
    assert "Gateway | Who" in lines
    assert "vpn.example.test | Students" in lines
    assert "gateway: vpn.example.test" in lines
    assert "group:   students" in lines  # code keeps its spacing


def test_page_chrome_is_removed():
    _, text = _vpn()
    for chrome in (
        "Example Help Site",  # navbar
        "Home",  # breadcrumb
        "Still stuck?",  # help footer
        "help desk can walk you through",
        "Last modified",  # page meta
        "On this page",  # table of contents
        "Was this page helpful",  # feedback
        "dataLayer",  # script
        "max-width",  # style
        "editor's comment",  # HTML comment
        "Connecting to the Example VPN",  # the h1 is the title, not text
    ):
        assert chrome not in text, chrome


def test_section_index_widgets_are_removed():
    title, text = extract(fixture_text("home.html"), BASE)
    assert title == "Example Help Site"
    assert "### Getting Connected" in text
    for chrome in ("Overview", "Filter pages", "View all pages", "No pages here match"):
        assert chrome not in text, chrome


def test_page_without_article_or_main_fails():
    with pytest.raises(ExtractionFailed):
        extract(fixture_text("no-article.html"), BASE + "broken/")


def test_main_is_used_when_article_is_missing():
    page = "<html><body><main id='main'><h2>Only main</h2><p>Some text.</p></main></body></html>"
    title, text = extract(page, BASE)
    assert text.splitlines() == ["## Only main", "Some text."]
