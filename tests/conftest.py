import os
import socket

import keyring
import keyring.backend
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


class MemoryKeyring(keyring.backend.KeyringBackend):
    """An in-memory keychain so tests never read or write the real one."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        self.passwords.pop((service, username), None)


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(previous)
