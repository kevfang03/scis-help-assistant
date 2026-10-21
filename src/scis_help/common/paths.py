"""Where the assistant keeps its files. Nothing here creates a folder; writers do.

Page text, the index, logs and the model download live in the platformdirs folders, never in
the repository. Tests (and anyone who wants a portable folder) set ``SCIS_HELP_HOME``: data and
cache then live under it.
"""

import os
from pathlib import Path

import platformdirs

APP_NAME = "scis-help"
HOME_ENV_VAR = "SCIS_HELP_HOME"


def _home() -> Path | None:
    value = os.environ.get(HOME_ENV_VAR, "").strip()
    return Path(value) if value else None


def data_dir() -> Path:
    home = _home()
    return home / "data" if home else platformdirs.user_data_path(APP_NAME, appauthor=False)


def cache_dir() -> Path:
    home = _home()
    return home / "cache" if home else platformdirs.user_cache_path(APP_NAME, appauthor=False)


def log_dir() -> Path:
    return data_dir() / "logs"


def pages_db() -> Path:
    return data_dir() / "pages.sqlite"


def index_dir() -> Path:
    return data_dir() / "index"


def model_cache_dir() -> Path:
    """Where fastembed downloads the embedding model (~70 MB)."""
    return cache_dir() / "models"
