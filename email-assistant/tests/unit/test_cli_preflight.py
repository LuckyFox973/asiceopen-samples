"""The callback reachability test `auth-url` runs before spending a state token."""

from __future__ import annotations

import contextlib
import socket

import pytest
from sqlalchemy.exc import OperationalError

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


class TestSyncLoop:
    """`sync` keeps going until the mailbox is caught up, so a person does not have to."""

    @staticmethod
    def _args(*flags):
        """Through the real parser, so a new option cannot drift out of these tests."""
        return cli.build_parser().parse_args(
            ["sync", "hello@example.sk", "--mode", "initial", *flags]
        )

    @staticmethod
    def _counts(new=100):
        return {"new": new, "updated": 0, "unchanged": 0, "attachments": 0, "threads": 0}

    def _record_passes(self, monkeypatch, outcomes):
        calls = []

        def fake_pass(_args):
            calls.append(1)
            return outcomes[len(calls) - 1]

        monkeypatch.setattr(cli, "_sync_once", fake_pass)
        return calls

    def test_partial_passes_repeat_until_completed(self, monkeypatch, capsys):
        calls = self._record_passes(
            monkeypatch,
            [
                ("partial", None, self._counts()),
                ("partial", None, self._counts()),
                ("completed", None, self._counts(40)),
            ],
        )
        assert cli.cmd_sync(self._args()) == 0
        assert len(calls) == 3
        assert "240 messages" in capsys.readouterr().out

    def test_an_error_stops_the_loop(self, monkeypatch, capsys):
        calls = self._record_passes(
            monkeypatch, [("partial", "rate limit exceeded", self._counts())]
        )
        assert cli.cmd_sync(self._args()) == 1
        assert len(calls) == 1
        assert "rate limit exceeded" in capsys.readouterr().err

    def test_a_pass_that_fetches_nothing_stops_the_loop(self, monkeypatch, capsys):
        """`partial` forever with nothing arriving would spin until interrupted."""
        calls = self._record_passes(monkeypatch, [("partial", None, self._counts(0))])
        assert cli.cmd_sync(self._args()) == 1
        assert len(calls) == 1
        assert "fetched nothing" in capsys.readouterr().out

    def test_once_does_a_single_pass(self, monkeypatch, capsys):
        calls = self._record_passes(monkeypatch, [("partial", None, self._counts())])
        assert cli.cmd_sync(self._args("--once")) == 0
        assert len(calls) == 1
        assert "run the same command again" in capsys.readouterr().out


class TestExtractLoop:
    """`extract` works through the whole queue, not one batch of it."""

    @staticmethod
    def _args(*flags):
        return cli.build_parser().parse_args(["extract", *flags])

    @staticmethod
    def _counts(extracted=100, failed=0):
        return {
            "considered": extracted + failed,
            "extracted": extracted,
            "characters": extracted * 1000,
            "needs_ocr": 0,
            "unsupported": 0,
            "empty": 0,
            "failed": failed,
        }

    def _batches(self, monkeypatch, outcomes, pending_at_start):
        """Drive the loop through `outcomes`, each (counts, still_pending, errors)."""
        calls = []
        monkeypatch.setattr(
            cli, "session_scope", lambda: contextlib.nullcontext(object()), raising=True
        )
        monkeypatch.setattr(
            "app.services.documents.extraction_summary",
            # **kwargs on purpose: a stub pinned to today's arguments has
            # broken this suite three times for no defect in the code.
            lambda _session, **_kwargs: {"pending": pending_at_start},
        )

        def fake_batch(_args, *_rest, **_kwargs):
            calls.append(1)
            return outcomes[len(calls) - 1]

        monkeypatch.setattr(cli, "_extract_batch", fake_batch)
        return calls

    def test_batches_repeat_until_the_queue_is_empty(self, monkeypatch, capsys):
        calls = self._batches(
            monkeypatch,
            [
                (self._counts(), 437, []),
                (self._counts(), 337, []),
                (self._counts(37), 0, []),
            ],
            pending_at_start=537,
        )
        assert cli.cmd_extract(self._args()) == 0
        assert len(calls) == 3
        assert "237 extracted" in capsys.readouterr().out

    def test_a_batch_that_clears_nothing_stops_the_loop(self, monkeypatch, capsys):
        """Files whose extraction raises write no result and stay pending forever."""
        calls = self._batches(
            monkeypatch,
            [(self._counts(0, failed=8), 8, ["deadbeef: broken PDF"])],
            pending_at_start=8,
        )
        assert cli.cmd_extract(self._args()) == 1
        assert len(calls) == 1
        assert "broken PDF" in capsys.readouterr().err

    def test_an_empty_queue_does_no_work(self, monkeypatch, capsys):
        calls = self._batches(monkeypatch, [], pending_at_start=0)
        assert cli.cmd_extract(self._args()) == 0
        assert calls == []
        assert "Nothing to extract" in capsys.readouterr().out

    def test_once_does_a_single_batch(self, monkeypatch):
        calls = self._batches(monkeypatch, [(self._counts(), 437, [])], pending_at_start=537)
        assert cli.cmd_extract(self._args("--once")) == 0
        assert len(calls) == 1


class TestDatabaseDownMessage:
    """A stopped database is the commonest failure; it should read like a sentence."""

    def test_the_driver_message_survives_without_its_class_prefix(self):
        exc = OperationalError(
            "SELECT 1",
            {},
            Exception('connection failed: connection to server at "127.0.0.1" refused'),
        )
        line = cli._first_line(exc)
        assert line.startswith("connection failed")
        assert "psycopg" not in line
        assert "sqlalchemy" not in line.lower()

    def test_main_reports_it_in_plain_words(self, monkeypatch, capsys):
        def explode(_args):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        monkeypatch.setattr(cli, "cmd_stats", explode)
        assert cli.main(["stats"]) == 1
        err = capsys.readouterr().err
        assert "database is not reachable" in err
        assert "brew services start postgresql@16" in err
        assert "Traceback" not in err

    def test_ctrl_c_says_nothing_is_lost(self, monkeypatch, capsys):
        def interrupt(_args):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "cmd_stats", interrupt)
        assert cli.main(["stats"]) == 130
        assert "Nothing already stored is lost" in capsys.readouterr().out


class TestExtractionScope:
    """One decision, made once. Three places used to compute it separately, and
    when the check before starting disagreed with the batch, `--redo` printed
    "nothing to extract" and did nothing."""

    @staticmethod
    def _scope(*flags):
        return cli.ExtractionScope.of(cli.build_parser().parse_args(["extract", *flags]))

    def test_a_plain_run_reads_only_what_has_no_result(self):
        scope = self._scope()
        assert scope.retry_failed is False
        assert scope.redo is False
        assert scope.since is None

    def test_a_retry_marks_the_run(self):
        scope = self._scope("--retry-failed")
        assert scope.retry_failed is True
        assert scope.redo is False
        assert scope.since is not None

    def test_a_redo_implies_a_retry(self):
        """Re-reading everything includes everything a retry would have."""
        scope = self._scope("--redo")
        assert scope.redo is True
        assert scope.retry_failed is True
        assert scope.since is not None

    def test_every_caller_gets_the_same_three_values(self):
        """as_kwargs is what the query sees; a flag missing here is invisible."""
        scope = self._scope("--redo")
        assert scope.as_kwargs() == {
            "retry_failed": True,
            "redo": True,
            "since": scope.since,
        }

    def test_the_flags_reach_the_query(self, monkeypatch):
        """The failure this class exists for: the pre-check dropped --redo."""
        seen: list[dict] = []

        def spy(_session, **kwargs):
            seen.append(kwargs)
            return {"pending": 0}

        monkeypatch.setattr("app.services.documents.extraction_summary", spy)
        monkeypatch.setattr(
            cli, "session_scope", lambda: contextlib.nullcontext(object()), raising=True
        )
        cli.cmd_extract(cli.build_parser().parse_args(["extract", "--redo"]))

        assert seen, "the summary was never consulted"
        assert seen[0]["redo"] is True
        assert seen[0]["since"] is not None
