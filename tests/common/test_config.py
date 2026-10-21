import pytest
from pydantic import ValidationError

from scis_help.common import paths
from scis_help.common.atomic import write_json_atomic
from scis_help.common.config import CrawlSettings, load_settings


def test_defaults():
    settings = load_settings()
    assert settings.crawl.base_url == "https://support.cci.drexel.edu/"
    assert settings.crawl.delay_seconds == 1.5
    assert settings.index.embed_model == "BAAI/bge-small-en-v1.5"
    assert settings.retrieval.k == 5


def test_env_overrides_nested_values(monkeypatch):
    monkeypatch.setenv("SCIS_HELP_RETRIEVAL__K", "7")
    monkeypatch.setenv("SCIS_HELP_CRAWL__DELAY_SECONDS", "2.5")
    settings = load_settings()
    assert settings.retrieval.k == 7
    assert settings.crawl.delay_seconds == 2.5


def test_dotenv_is_read(tmp_path):
    (tmp_path / ".env").write_text("SCIS_HELP_RETRIEVAL__MIN_SCORE=0.4\n", encoding="utf-8")
    assert load_settings().retrieval.min_score == 0.4


def test_delay_below_one_second_is_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        CrawlSettings(delay_seconds=0.5)
    monkeypatch.setenv("SCIS_HELP_CRAWL__DELAY_SECONDS", "0.2")
    with pytest.raises(ValidationError):
        load_settings()


def test_paths_follow_scis_help_home(isolated_home):
    assert paths.data_dir() == isolated_home / "data"
    assert paths.cache_dir() == isolated_home / "cache"
    assert paths.log_dir() == isolated_home / "data" / "logs"
    assert paths.pages_db() == isolated_home / "data" / "pages.sqlite"
    assert paths.index_dir() == isolated_home / "data" / "index"
    assert paths.model_cache_dir() == isolated_home / "cache" / "models"
    assert not isolated_home.exists()  # path helpers never create folders


def test_atomic_json_write_replaces_whole_file(tmp_path):
    target = tmp_path / "sub" / "state.json"
    write_json_atomic(target, {"a": 1})
    write_json_atomic(target, {"b": 2})
    assert target.read_text(encoding="utf-8").replace(" ", "").replace("\n", "") == '{"b":2}'
    assert not (tmp_path / "sub" / "state.json.tmp").exists()


def test_web_defaults_and_origins_from_env(monkeypatch):
    settings = load_settings()
    assert (settings.web.host, settings.web.port) == ("127.0.0.1", 8765)
    assert settings.web.allowed_origins == []
    assert settings.web.desk_local_only is True
    monkeypatch.setenv("SCIS_HELP_WEB__ALLOWED_ORIGINS", '["http://127.0.0.1:9000"]')
    assert load_settings().web.allowed_origins == ["http://127.0.0.1:9000"]
