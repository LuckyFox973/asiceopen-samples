"""Staying under Gmail's per-second quota instead of discovering it.

Gmail bills 250 quota units per second per mailbox.  A backfill fetching
messages as fast as the network allows spends that inside the first second,
and every request after it is refused until the client backs off — which
looked, in the terminal, like thirteen identical failures in a row.
"""

from __future__ import annotations

import pytest

from app.gmail.client import QUOTA_UNITS, QuotaPacer


class FakeClock:
    """Time only moves when something sleeps — so the maths is the test."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def pacer(clock: FakeClock) -> QuotaPacer:
    return QuotaPacer(units_per_second=100.0, clock=clock, sleep=clock.sleep)


class TestQuotaPacer:
    def test_spending_within_the_budget_never_waits(self, pacer, clock):
        for _ in range(20):
            pacer.spend(5)
        assert clock.slept == []

    def test_overspending_waits_for_exactly_what_is_owed(self, pacer, clock):
        pacer.spend(100)  # the whole second's budget
        pacer.spend(25)
        assert clock.slept == [0.25], "25 units at 100/s is a quarter of a second"

    def test_the_budget_refills_with_time(self, pacer, clock):
        pacer.spend(100)
        clock.now += 1.0
        pacer.spend(100)
        assert clock.slept == [], "a full second refills a full second's worth"

    def test_the_budget_does_not_bank_beyond_one_second(self, pacer, clock):
        """Otherwise an idle hour would buy one enormous burst — a 403 again."""
        clock.now += 3600.0
        pacer.spend(100)
        pacer.spend(50)
        assert clock.slept == [0.5]

    def test_a_sustained_burst_settles_at_the_budgeted_rate(self, pacer, clock):
        start = clock.now
        for _ in range(60):  # 300 units at 100/s
            pacer.spend(5)
        elapsed = clock.now - start
        assert 1.9 <= elapsed <= 2.1, f"300 units at 100/s should take ~2s, took {elapsed}"

    def test_the_default_budget_stays_under_gmails_ceiling(self):
        assert QuotaPacer().units_per_second < 250


class TestQuotaCosts:
    def test_every_paced_method_has_a_cost(self):
        """A method added without a cost would raise KeyError mid-backfill."""
        import inspect

        from app.gmail.client import GmailClient

        source = inspect.getsource(GmailClient)
        paced = {
            line.split('"')[1]
            for line in source.splitlines()
            if "self._pace(" in line and '"' in line
        }
        assert paced, "the client must pace its calls"
        assert paced <= set(QUOTA_UNITS), f"no cost defined for {paced - set(QUOTA_UNITS)}"

    def test_costs_are_positive(self):
        assert all(units > 0 for units in QUOTA_UNITS.values())


class TestRetryReporting:
    """The retry hook runs only when Gmail refuses — the one path nobody sees."""

    @staticmethod
    def _forbidden(reason: str, message: str, *, with_message: bool = True) -> Exception:
        """A 403 shaped the way Gmail actually sends one."""
        import json

        from googleapiclient.errors import HttpError

        class Resp:
            status = 403
            reason = "Forbidden"

        error: dict = {
            "code": 403,
            "errors": [{"domain": "usageLimits", "reason": reason, "message": message}],
        }
        if with_message:
            error["message"] = message
        return HttpError(Resp(), json.dumps({"error": error}).encode())

    def _rate_limited(self) -> Exception:
        return self._forbidden("rateLimitExceeded", "Rate Limit Exceeded")

    def test_a_quota_403_is_retried(self):
        from app.gmail.client import _is_retryable

        assert _is_retryable(self._rate_limited()) is True

    def test_a_quota_403_is_retried_even_without_a_top_level_message(self):
        """Reading the reason beats matching text in a library's repr."""
        from app.gmail.client import _is_retryable

        terse = self._forbidden("rateLimitExceeded", "Rate Limit Exceeded", with_message=False)
        assert "rateLimitExceeded" not in str(terse), "precondition: the text does not say it"
        assert _is_retryable(terse) is True

    def test_a_missing_scope_403_is_not_retried(self):
        from app.gmail.client import _is_retryable

        insufficient = self._forbidden("insufficientPermissions", "Insufficient Permission")
        assert _is_retryable(insufficient) is False, "retrying a scope problem only wastes time"

    def test_the_hook_reports_instead_of_crashing(self, monkeypatch):
        """A broken before_sleep would turn every retry into a different error."""
        from app.gmail import client as client_module

        said: list[dict] = []
        monkeypatch.setattr(
            client_module.log, "warning", lambda event, **kw: said.append({"event": event, **kw})
        )

        attempts = {"n": 0}

        @client_module.gmail_retry
        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise self._rate_limited()
            return "ok"

        monkeypatch.setattr(client_module.time, "sleep", lambda _s: None)
        assert flaky() == "ok"
        assert attempts["n"] == 3
        assert len(said) == 2, "one line per wait, not one per library attempt"
        assert said[0]["event"] == "gmail.retrying"
        assert said[0]["after_seconds"] > 0
