import os
import socket
import threading

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
    """Tests never touch the network: any socket connection fails loudly.

    The one exception is ``socket.socketpair()``: on Windows it connects two loopback sockets,
    and asyncio (so FastAPI's TestClient) needs it for its event loop.
    """
    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair
    inside_socketpair = threading.local()

    def refuse(self, *args, **kwargs):
        if getattr(inside_socketpair, "active", False):
            return real_connect(self, *args, **kwargs)
        raise AssertionError("a test tried to open a network connection")

    def socketpair(*args, **kwargs):
        inside_socketpair.active = True
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            inside_socketpair.active = False

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "socketpair", socketpair)
