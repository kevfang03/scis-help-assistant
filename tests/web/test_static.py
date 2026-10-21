import re
import subprocess
from pathlib import Path

import pytest

from scis_help.web.app import STATIC_DIR


def test_pages_and_widget_files(make_client):
    client, _ = make_client()
    for path, kind in [
        ("/", "text/html"),
        ("/desk", "text/html"),
        ("/widget.js", "text/javascript"),
        ("/widget.css", "text/css"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(kind), path
        assert response.headers["x-content-type-options"] == "nosniff"
    home = client.get("/")
    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    assert "Not affiliated with or endorsed by Drexel University" in home.text
    assert 'data-mode="desk"' in client.get("/desk").text
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_static_files_name_drexel_only_in_the_disclaimer():
    for path in STATIC_DIR.iterdir():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "drexel" in line.lower():
                assert "Not affiliated with or endorsed by Drexel University." in line, path.name


def test_widget_has_no_external_requests_and_required_text():
    js = (STATIC_DIR / "widget.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "widget.css").read_text(encoding="utf-8")
    # Only the API base derived from the script's own src; no CDNs, fonts or analytics.
    assert not re.search(r"https?://(?!127\.0\.0\.1:8765/widget\.js|www\.w3\.org/2000/svg)", js)
    assert "@import" not in css and "url(" not in css
    assert "Help Assistant (unofficial)" in js
    assert "Don't enter passwords or student IDs." in js
    assert "Unofficial student project · answers come from public SCIS support pages" in js
    assert "attachShadow" in js and "innerHTML" in js  # innerHTML only on an inert template
    assert js.count("innerHTML") == 1


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_widget_js_parses():
    result = subprocess.run(
        ["node", "--check", str(STATIC_DIR / "widget.js")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_static_dir_ships_in_the_package():
    assert Path(STATIC_DIR, "widget.js").is_file()
