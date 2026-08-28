"""The callback reachability test `auth-url` runs before spending a state token."""

from __future__ import annotations

import socket

import pytest

from app import cli
from app.cli import unreachable_callback


@pytest.fixture
def listening_port() -> int:
    """A real socket, so the check is exercised against a live listener."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


@pytest.fixture
def closed_port() -> int:
    """A port nothing is listening on — bound, read, then released."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class TestUnreachableCallback:
    def test_a_listening_callback_is_accepted(self, listening_port):
        uri = f"http://127.0.0.1:{listening_port}/api/v1/auth/google/callback"
        assert unreachable_callback(uri) is None

    def test_a_closed_port_is_reported(self, closed_port):
        uri = f"http://127.0.0.1:{closed_port}/api/v1/auth/google/callback"
        problem = unreachable_callback(uri)
        assert problem is not None
        assert str(closed_port) in problem

    def test_remote_hosts_are_not_probed(self):
        """A deployed callback may be healthy and still unreachable from here."""
        assert unreachable_callback("https://mail.example.com/api/v1/auth/google/callback") is None

    def test_localhost_resolves_like_the_loopback_address(self, closed_port):
        assert unreachable_callback(f"http://localhost:{closed_port}/callback") is not None


class NoRows:
    """A session that finds nothing, whichever way it is asked."""

    def get(self, *_args, **_kwargs):
        return None

    def scalar(self, *_args, **_kwargs):
        return None


class TestUnknownMailbox:
    """A mistyped address should name the real ones, not send you to another command."""

    def test_the_connected_mailboxes_are_listed(self, monkeypatch):
        connected = [type("Account", (), {"email": "hello@foxgroup.sk"})()]
        monkeypatch.setattr(cli, "list_accounts", lambda _session: connected)

        with pytest.raises(SystemExit) as excinfo:
            cli._resolve_account(NoRows(), "typo@example.sk")

        message = str(excinfo.value)
        assert "typo@example.sk" in message
        assert "hello@foxgroup.sk" in message

    def test_an_empty_database_points_at_auth_url(self, monkeypatch):
        monkeypatch.setattr(cli, "list_accounts", lambda _session: [])

        with pytest.raises(SystemExit) as excinfo:
            cli._resolve_account(NoRows(), "nobody@example.sk")

        assert "auth-url" in str(excinfo.value)
