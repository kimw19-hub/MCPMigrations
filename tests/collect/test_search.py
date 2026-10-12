from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from mcp_migration import config
from mcp_migration.collect import search as gh
from mcp_migration.collect.search import (
    Query,
    date_windows,
    format_window,
    load_queries,
    search,
)


def test_date_windows_cover_range_without_overlap() -> None:
    windows = list(date_windows(date(2026, 1, 1), date(2026, 1, 10), 4))
    assert [format_window(*w) for w in windows] == [
        "2026-01-01..2026-01-04",
        "2026-01-05..2026-01-08",
        "2026-01-09..2026-01-10",
    ]
    # Consecutive windows must abut exactly, with no gap that would drop commits.
    for (_, end), (next_start, _) in pairwise(windows):
        assert (next_start - end).total_seconds() == 1


def test_whole_days_render_as_dates_and_partial_days_as_timestamps() -> None:
    assert format_window(datetime(2026, 7, 28), datetime(2026, 7, 28, 23, 59, 59)) == (
        "2026-07-28..2026-07-28"
    )
    assert format_window(datetime(2026, 7, 28), datetime(2026, 7, 28, 11, 59, 59)) == (
        "2026-07-28T00:00:00..2026-07-28T11:59:59"
    )


class FakeClient:
    """Reports >1000 results until the window is narrow enough, forcing bisection."""

    def __init__(self, floor: str = "day") -> None:
        self.queries: list[str] = []
        self.floor = floor

    @staticmethod
    def _window(q: str) -> tuple[str, str]:
        """Extract the range from the trailing date qualifier.

        The qualifier is `created:` for issues and `committer-date:` for
        commits, so match on the range itself rather than the qualifier name.
        """
        token = next(t for t in q.split() if ".." in t)
        start, _, end = token.partition(":")[2].partition("..")
        return start, end

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        q = params["q"]
        self.queries.append(q)
        start, end = self._window(q)
        if self.floor == "day":
            too_wide = start.split("T")[0] != end.split("T")[0]
        else:
            too_wide = "T" not in start
        if too_wide:
            return {"total_count": 5000, "items": []}
        item = {
            "number": 1,
            "title": f"PR {start}",
            "repository_url": f"{gh.API_URL}/repos/Acme/Server",
            "pull_request": {},
        }
        return {"total_count": 1, "items": [item]}


def test_search_bisects_large_windows() -> None:
    query = Query(name="t", kind="issues", q="is:pr x", start=date(2026, 1, 1), window_days=4)
    hits = list(search(FakeClient(), query, until=date(2026, 1, 4)))  # type: ignore[arg-type]
    assert len(hits) == 4
    assert {h["kind"] for h in hits} == {"pr"}
    assert hits[0]["repo"] == "acme/server"


def test_search_splits_below_one_day_when_a_single_day_exceeds_the_cap() -> None:
    """2026-07-28 exceeds 1,000 commit hits; without sub-day splitting they are lost."""
    client = FakeClient(floor="subday")
    query = Query(name="t", kind="commits", q="x", start=date(2026, 7, 28), window_days=1)
    hits = list(search(client, query, until=date(2026, 7, 28)))  # type: ignore[arg-type]
    assert hits, "sub-day windows must eventually return hits"
    assert any("T" in q for q in client.queries), "expected a timestamped window"


def test_title_pattern_filters_hits() -> None:
    query = Query(
        name="t",
        kind="issues",
        q="x",
        start=date(2026, 1, 1),
        title_pattern=r"(?i)bump mcp from 1\.\S+ to 2\.",
    )
    assert query.keeps({"title": "Bump mcp from 1.29.1 to 2.1.1"})
    assert not query.keeps({"title": "Bump mcp from 1.27.2 to 1.28.1"})


def test_load_queries(tmp_path: Path) -> None:
    path = tmp_path / "q.toml"
    path.write_text(
        "[defaults]\nstart = 2026-05-21\n\n"
        '[[query]]\nname = "a"\nkind = "commits"\nq = "x"\n\n'
        '[[query]]\nname = "b"\nkind = "issues"\nq = "y"\nstart = 2025-01-01\nwindow_days = 2\n'
    )
    a, b = load_queries(path)
    assert (a.start, a.window_days, a.date_field) == (date(2026, 5, 21), 7, "committer-date")
    assert (b.start, b.window_days, b.date_field) == (date(2025, 1, 1), 2, "created")


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status: int, headers: dict[str, str], body: dict[str, Any]) -> None:
        self.status_code = status
        self.headers = headers
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status on {self.status_code}")


def test_secondary_rate_limit_403_is_retried_not_raised() -> None:
    """GitHub's secondary limit sends 403 with no Retry-After and quota remaining."""
    resp = _Resp(
        403,
        {"X-RateLimit-Remaining": "24"},
        {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."},
    )
    delay = gh.GitHubClient._retry_delay(resp, attempt=0)  # type: ignore[arg-type]
    assert delay is not None and delay > 0


def test_ordinary_403_is_not_retried() -> None:
    """A permissions 403 must fail fast rather than sleeping through the retries."""
    resp = _Resp(403, {"X-RateLimit-Remaining": "24"}, {"message": "Must have push access."})
    assert gh.GitHubClient._retry_delay(resp, attempt=0) is None  # type: ignore[arg-type]


def test_primary_rate_limit_waits_for_reset() -> None:
    resp = _Resp(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99999999999"}, {})
    delay = gh.GitHubClient._retry_delay(resp, attempt=0)  # type: ignore[arg-type]
    assert delay is not None and delay > 0


def test_network_errors_are_retried_then_succeed() -> None:
    """A dropped connection mid-run must be retried, not end the collection."""
    import requests as _requests

    class FlakySession:
        def __init__(self) -> None:
            self.calls = 0
            self.headers: dict[str, str] = {}

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise _requests.exceptions.ReadTimeout("read timed out")
            return _Resp(200, {}, {"total_count": 0, "items": []})

    session = FlakySession()
    client = gh.GitHubClient(None, session=session)  # type: ignore[arg-type]
    client.tokens[0].min_interval = 0.0
    with mock.patch.object(gh.time, "sleep"):
        assert client.get("/search/issues", {"q": "x"}) == {"total_count": 0, "items": []}
    assert session.calls == 2


def test_persistent_network_failure_eventually_gives_up() -> None:
    import requests as _requests

    class DeadSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            raise _requests.exceptions.ConnectionError("no route to host")

    client = gh.GitHubClient(
        None,
        session=DeadSession(),  # type: ignore[arg-type]
        max_retries=3,
    )
    client.tokens[0].min_interval = 0.0
    with mock.patch.object(gh.time, "sleep"), pytest.raises(RuntimeError):
        client.get("/search/issues", {"q": "x"})


def _client_with(responses: list[Any]) -> tuple[Any, Any]:
    class ScriptedSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            resp = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return resp

    session = ScriptedSession()
    client = gh.GitHubClient("tok", session=session)  # type: ignore[arg-type]
    return client, session


SECONDARY = {"message": "You have exceeded a secondary rate limit."}


def test_secondary_limit_widens_the_request_interval() -> None:
    """Paying a per-request penalty repeatedly is worse than spacing requests out."""
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    limited = _Resp(403, {"X-RateLimit-Remaining": "29"}, SECONDARY)
    client, _ = _client_with([limited, ok])
    before = client.tokens[0].min_interval
    with mock.patch.object(gh.time, "sleep"):
        client.get("/search/issues", {"q": "x"})
    assert client.tokens[0].min_interval > before


def test_interval_is_capped() -> None:
    limited = _Resp(403, {"X-RateLimit-Remaining": "29"}, SECONDARY)
    client, _ = _client_with([limited])
    client.max_retries = 40
    with mock.patch.object(gh.time, "sleep"), pytest.raises(RuntimeError):
        client.get("/search/issues", {"q": "x"})
    assert client.tokens[0].min_interval <= gh.MAX_INTERVAL


def test_interval_decays_after_sustained_success() -> None:
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    client, _ = _client_with([ok])
    client.tokens[0].min_interval = 20.0
    with mock.patch.object(gh.time, "sleep"):
        for _ in range(25):
            client.get("/search/issues", {"q": "x"})
    assert client.tokens[0].min_interval < 20.0


def _rotating_client(responses: list[Any], tokens: list[str]) -> tuple[Any, Any]:
    """A client over several tokens, recording which token sent each request."""

    class RecordingSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls = 0
            self.used: list[str | None] = []

        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            auth = self.headers.get("Authorization")
            self.used.append(auth.removeprefix("Bearer ") if auth else None)
            resp = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return resp

    session = RecordingSession()
    client = gh.GitHubClient(tokens, session=session)  # type: ignore[arg-type]
    return client, session


def test_a_throttled_token_hands_off_to_another() -> None:
    """The point of rotation: a benched token must not stall the run."""
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    limited = _Resp(403, {"X-RateLimit-Remaining": "29"}, SECONDARY)
    client, session = _rotating_client([limited, ok], ["a", "b"])
    with mock.patch.object(gh.time, "sleep"):
        client.get("/search/issues", {"q": "x"})
    assert session.used == ["a", "b"]
    # Only the token GitHub actually complained about is slowed.
    assert client.tokens[0].min_interval > client.tokens[1].min_interval


def test_a_rejected_token_is_retired_and_the_run_continues() -> None:
    """A revoked or mistyped pooled token must not end a multi-hour run."""
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    rejected = _Resp(401, {}, {"message": "Bad credentials"})
    client, session = _rotating_client([rejected, ok], ["dud", "good"])
    with mock.patch.object(gh.time, "sleep"):
        client.get("/search/issues", {"q": "x"})
    assert session.used == ["dud", "good"]
    assert client.tokens[0].retired and not client.tokens[1].retired


def test_a_retired_token_is_never_chosen_again() -> None:
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    client, session = _rotating_client([ok], ["dud", "good"])
    client.tokens[0].retired = True
    with mock.patch.object(gh.time, "sleep"):
        client.get("/search/issues", {"q": "x"})
        client.get("/search/issues", {"q": "x"})
    assert session.used == ["good", "good"]


def test_losing_every_token_fails_loudly() -> None:
    rejected = _Resp(401, {}, {"message": "Bad credentials"})
    client, _ = _rotating_client([rejected], ["a", "b"])
    with (
        mock.patch.object(gh.time, "sleep"),
        pytest.raises(RuntimeError, match="rejected"),
    ):
        client.get("/search/issues", {"q": "x"})


def test_rotation_prefers_the_token_free_soonest() -> None:
    ok = _Resp(200, {}, {"total_count": 0, "items": []})
    client, session = _rotating_client([ok], ["a", "b"])
    client.tokens[0].available_at = gh.time.monotonic() + 600
    with mock.patch.object(gh.time, "sleep"):
        client.get("/search/issues", {"q": "x"})
    assert session.used == ["b"]


def test_duplicate_tokens_are_not_counted_as_capacity(monkeypatch: Any) -> None:
    """Listing one credential twice is not two accounts' worth of quota."""
    monkeypatch.setenv("GITHUB_TOKENS", "a, a, b")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert config.github_tokens() == ["a", "b"]


def test_tokens_parse_from_either_variable(monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_TOKENS", "a b")
    monkeypatch.setenv("GITHUB_TOKEN", "c")
    assert config.github_tokens() == ["a", "b", "c"]
    monkeypatch.delenv("GITHUB_TOKENS")
    assert config.github_tokens() == ["c"]
    monkeypatch.delenv("GITHUB_TOKEN")
    assert config.github_tokens() == []


def test_no_token_still_yields_one_unauthenticated_slot() -> None:
    client = gh.GitHubClient([])
    assert len(client.tokens) == 1
    assert client.tokens[0].value is None
    # Unauthenticated search is 10 req/min, so it must pace slower than 30.
    assert client.tokens[0].base_interval > 6.0


def test_a_single_token_still_works_as_a_bare_string() -> None:
    client = gh.GitHubClient("tok")
    assert [t.value for t in client.tokens] == ["tok"]


def test_token_values_never_reach_the_logs() -> None:
    client = gh.GitHubClient(["supersecret", "alsosecret"])
    labels = [t.label for t in client.tokens]
    assert labels == ["token 1", "token 2"]
    assert not any("secret" in label for label in labels)


def test_a_plain_403_does_not_widen_the_interval() -> None:
    """Only secondary limits should slow the client; a permissions error must not."""
    forbidden = _Resp(403, {"X-RateLimit-Remaining": "29"}, {"message": "Must have push access."})
    client, _ = _client_with([forbidden])
    before = client.tokens[0].min_interval
    with mock.patch.object(gh.time, "sleep"), pytest.raises(AssertionError):
        client.get("/search/issues", {"q": "x"})
    assert client.tokens[0].min_interval == before
