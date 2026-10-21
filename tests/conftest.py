import os
import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data folder and no settings from the real environment."""
    for name in list(os.environ):
        if name.startswith("SCIS_HELP_"):
            monkeypatch.delenv(name)
    home = tmp_path / "home"
    monkeypatch.setenv("SCIS_HELP_HOME", str(home))
    monkeypatch.chdir(tmp_path)  # no stray .env
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network: any socket connection fails loudly."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
