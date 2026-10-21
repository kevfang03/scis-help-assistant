import httpx
import pytest

from scis_help.common import paths
from scis_help.sources.pages import PageStore
from scis_help.sources.support_site import (
    USER_AGENT,
    SupportSiteSource,
    crawl,
    last_crawl,
)
from tests.fakes import BASE, FakeSite, fixture_text, html, make_settings, xml

VPN = BASE + "getting-connected/example-vpn/"
PRINTING = BASE + "printing/lab-printers/"
SHARED = BASE + "getting-connected/shared-drive/"
HOME = BASE


def _crawl(site: FakeSite, **kwargs):
    return crawl(make_settings(), transport=site.transport, sleep=site.clock.sleep, **kwargs)


def _stored() -> dict[str, str | None]:
    with PageStore(paths.pages_db()) as store:
        return store.lastmods("support_site")


def test_robots_404_allows_everything_and_pages_are_stored():
    site = FakeSite.default()
    report = _crawl(site)
    assert report.fetched == 4
    assert report.failed == 0
    assert report.skipped_robots == 0
    assert report.stopped_reason is None
    assert set(_stored()) == {HOME, VPN, PRINTING, SHARED}
    assert site.paths()[:2] == ["/robots.txt", "/sitemap.xml"]


def test_stored_page_fields():
    _crawl(FakeSite.default())
    docs = {d.url: d for d in SupportSiteSource().iter_documents()}
    vpn = docs[VPN]
    assert vpn.title == "Connecting to the Example VPN"
    assert vpn.section == "getting-connected"
    assert vpn.lastmod == "2026-03-03"
    assert vpn.source == "support_site"
    assert vpn.audience == "public"
    assert "## Windows" in vpn.text
    assert docs[HOME].section == ""
    with PageStore(paths.pages_db()) as store:
        page = next(p for p in store.iter_pages("support_site") if p.url == VPN)
    assert len(page.content_hash) == 64
    assert "<" not in page.text  # no raw HTML stored


def test_user_agent_is_sent():
    seen = []
    site = FakeSite.default()
    original = site.handler

    def handler(request):
        seen.append(request.headers["user-agent"])
        return original(request)

    crawl(make_settings(), transport=httpx.MockTransport(handler), sleep=site.clock.sleep)
    assert seen and set(seen) == {USER_AGENT}


def test_delay_between_every_request():
    site = FakeSite.default()
    _crawl(site)
    times = [when for when, _ in site.requests]
    assert len(times) == 6  # robots, sitemap, four pages
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    assert all(gap >= 1.5 for gap in gaps), gaps
    assert site.clock.sleeps == [1.5] * 5


def test_robots_disallow_skips_pages():
    site = FakeSite.default()
    site.routes["/robots.txt"] = httpx.Response(200, text=fixture_text("robots-disallow.txt"))
    report = _crawl(site)
    assert report.skipped_robots == 1
    assert report.fetched == 3
    assert "/printing/lab-printers/" not in site.paths()
    assert PRINTING not in _stored()


def test_robots_disallow_removes_a_page_stored_earlier():
    _crawl(FakeSite.default())
    site = FakeSite.default()
    site.routes["/robots.txt"] = httpx.Response(200, text=fixture_text("robots-disallow.txt"))
    report = _crawl(site)
    assert report.removed == 1
    assert PRINTING not in _stored()


def test_robots_server_error_stops_the_crawl():
    site = FakeSite.default()
    site.routes["/robots.txt"] = httpx.Response(503)
    report = _crawl(site)
    assert report.stopped_reason == "robots_unavailable"
    assert "/sitemap.xml" not in site.paths()


def test_robots_forbidden_stops_the_crawl():
    site = FakeSite.default()
    site.routes["/robots.txt"] = httpx.Response(403)
    assert _crawl(site).stopped_reason == "robots_unavailable"


def test_robots_crawl_delay_raises_the_delay():
    site = FakeSite.default()
    site.routes["/robots.txt"] = httpx.Response(200, text="User-agent: *\nCrawl-delay: 4\n")
    _crawl(site)
    assert set(site.clock.sleeps) == {4.0}


def test_incremental_crawl_skips_unchanged_pages():
    _crawl(FakeSite.default())
    site = FakeSite.default()
    report = _crawl(site)
    assert report.fetched == 0
    assert report.unchanged == 4
    assert site.paths() == ["/robots.txt", "/sitemap.xml"]


def test_newer_lastmod_is_refetched():
    _crawl(FakeSite.default())
    site = FakeSite.default()
    newer = fixture_text("sitemap.xml").replace("2026-03-03", "2026-10-01")
    site.routes["/sitemap.xml"] = xml(newer)
    report = _crawl(site)
    assert report.fetched == 1
    assert report.unchanged == 3
    assert "/getting-connected/example-vpn/" in site.paths()
    assert _stored()[VPN] == "2026-10-01"


def test_full_refetches_everything():
    _crawl(FakeSite.default())
    report = _crawl(FakeSite.default(), full=True)
    assert report.fetched == 4
    assert report.unchanged == 0


def test_pages_that_left_the_sitemap_are_deleted():
    _crawl(FakeSite.default())
    site = FakeSite.default()
    smaller = "\n".join(
        line for line in fixture_text("sitemap.xml").splitlines() if "shared-drive" not in line
    )
    site.routes["/sitemap.xml"] = xml(smaller)
    report = _crawl(site)
    assert report.removed == 1
    assert SHARED not in _stored()


def test_empty_sitemap_never_wipes_the_store():
    _crawl(FakeSite.default())
    site = FakeSite.default()
    site.routes["/sitemap.xml"] = xml(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
    )
    report = _crawl(site)
    assert report.stopped_reason == "sitemap_empty"
    assert len(_stored()) == 4


def test_off_host_http_and_pdf_urls_are_ignored():
    site = FakeSite.default()  # FakeSite fails the test on any off-host request
    _crawl(site)
    assert "/files/guide.pdf" not in site.paths()
    assert "/plain-http/" not in site.paths()
    assert all(url.startswith("https://support.example.test/") for _, url in site.requests)


def test_sitemap_index_is_followed_on_host_only():
    site = FakeSite.default()
    site.routes["/sitemap.xml"] = xml(fixture_text("sitemap-index.xml"))
    site.routes["/pages-sitemap.xml"] = xml(fixture_text("sitemap.xml"))
    report = _crawl(site)
    assert report.fetched == 4
    assert "/pages-sitemap.xml" in site.paths()


def test_redirect_off_host_is_not_followed_or_stored():
    site = FakeSite.default()
    site.routes["/getting-connected/shared-drive/"] = httpx.Response(
        301, headers={"location": "https://other.example.test/moved/"}
    )
    report = _crawl(site)
    assert report.failed == 1
    assert SHARED not in _stored()


def test_redirect_on_host_is_followed():
    site = FakeSite.default()
    site.routes["/getting-connected/shared-drive/"] = httpx.Response(
        301, headers={"location": "/getting-connected/shared-drive-new/"}
    )
    site.routes["/getting-connected/shared-drive-new/"] = html(fixture_text("shared-drive.html"))
    report = _crawl(site)
    assert report.failed == 0
    assert SHARED in _stored()  # stored under its sitemap URL


def test_server_errors_are_retried_then_counted_as_failed():
    site = FakeSite.default()
    site.routes["/printing/lab-printers/"] = httpx.Response(502)
    report = _crawl(site)
    assert report.failed == 1
    assert report.fetched == 3
    assert site.paths().count("/printing/lab-printers/") == 3  # first try + 2 retries
    assert report.stopped_reason is None


def test_server_error_then_success_is_stored():
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(500) if len(calls) == 1 else html(fixture_text("printing.html"))

    site = FakeSite.default()
    site.routes["/printing/lab-printers/"] = flaky
    report = _crawl(site)
    assert report.failed == 0
    assert PRINTING in _stored()


def test_timeouts_are_retried():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    site = FakeSite.default()
    site.routes["/printing/lab-printers/"] = timeout
    report = _crawl(site)
    assert report.failed == 1
    assert site.paths().count("/printing/lab-printers/") == 3


def test_429_stops_the_crawl_and_keeps_what_was_stored():
    site = FakeSite.default()
    site.routes["/printing/lab-printers/"] = httpx.Response(429)
    report = _crawl(site)
    assert report.stopped_reason == "rate_limited"
    assert site.paths().count("/printing/lab-printers/") == 1  # never retried
    assert "/getting-connected/shared-drive/" not in site.paths()  # nothing after it
    assert set(_stored()) == {HOME, VPN}


def test_page_without_content_counts_as_failed():
    site = FakeSite.default()
    site.routes["/getting-connected/shared-drive/"] = html(fixture_text("no-article.html"))
    report = _crawl(site)
    assert report.failed == 1
    assert SHARED not in _stored()


def test_404_page_counts_as_failed():
    site = FakeSite.default()
    del site.routes["/printing/lab-printers/"]
    report = _crawl(site)
    assert report.failed == 1
    assert report.stopped_reason is None


@pytest.mark.parametrize("limit", [1, 2])
def test_max_pages_counts_fetches_not_skips(limit):
    _crawl(FakeSite.default(), max_pages=1)  # stores the home page only
    site = FakeSite.default()
    report = _crawl(site, max_pages=limit)
    assert report.unchanged == 1
    assert report.fetched == limit
    assert report.stopped_reason == "max_pages"


def test_last_crawl_summary_is_stored():
    _crawl(FakeSite.default())
    summary = last_crawl()
    assert summary is not None
    assert summary["in_sitemap"] == 4
    assert summary["fetched"] == 4
    assert summary["stopped_reason"] is None
