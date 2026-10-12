"""GitHub search over commits, issues, and pull requests.

Search results are capped per query, so every query runs over date windows and
windows that still exceed the cap are split in half recursively.
"""

from __future__ import annotations

import logging
import re
import time
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Literal

import requests

from mcp_migration.storage import first_line

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
GRAPHQL_URL = f"{API_URL}/graphql"
PER_PAGE = 100
MAX_RESULTS = 1000  # GitHub search cap per query
SearchKind = Literal["commits", "issues"]


@dataclass(frozen=True)
class Query:
    name: str
    kind: SearchKind
    q: str
    start: date
    window_days: int = 7
    title_pattern: str | None = None  # optional local filter on hit titles

    def keeps(self, hit: dict[str, Any]) -> bool:
        return self.title_pattern is None or bool(re.search(self.title_pattern, hit["title"]))

    @property
    def date_field(self) -> str:
        return "committer-date" if self.kind == "commits" else "created"


def load_queries(path: Path) -> list[Query]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    defaults = data.get("defaults", {})
    queries = []
    for entry in data.get("query", []):
        kind = entry["kind"]
        if kind not in ("commits", "issues"):
            raise ValueError(f"query {entry['name']!r}: kind must be 'commits' or 'issues'")
        queries.append(
            Query(
                name=entry["name"],
                kind=kind,
                q=entry["q"],
                start=date.fromisoformat(str(entry.get("start", defaults["start"]))),
                window_days=int(entry.get("window_days", defaults.get("window_days", 7))),
                title_pattern=entry.get("title_pattern"),
            )
        )
    return queries


# GitHub date qualifiers accept ISO timestamps, so a busy day can be split into
# hours. Without this, 2026-07-28 -- the revision's release date, and the busiest
# day in the corpus -- exceeds the 1,000-result cap and silently loses hits.
MIN_WINDOW = timedelta(minutes=15)


def date_windows(start: date, until: date, days: int) -> Iterator[tuple[datetime, datetime]]:
    """Inclusive, non-overlapping windows covering ``start..until``."""
    current = datetime.combine(start, dtime.min)
    last = datetime.combine(until, dtime.max).replace(microsecond=0)
    span = timedelta(days=days)
    while current <= last:
        end = min(current + span - timedelta(seconds=1), last)
        yield current, end
        current = end + timedelta(seconds=1)


def format_window(start: datetime, end: datetime) -> str:
    """Render a window for a GitHub date qualifier.

    Whole days are rendered as plain dates to keep queries readable and
    comparable with GitHub's web UI; sub-day windows use full timestamps.
    """
    whole_day = start.time() == dtime.min and end.time() >= dtime.max.replace(microsecond=0)
    if whole_day:
        return f"{start.date().isoformat()}..{end.date().isoformat()}"
    return f"{start.isoformat()}..{end.isoformat()}"


def _is_secondary_limit(resp: requests.Response) -> bool:
    try:
        message = str(resp.json().get("message", ""))
    except ValueError:
        message = resp.text[:200]
    lowered = message.lower()
    return "secondary rate limit" in lowered or "abuse detection" in lowered


MAX_INTERVAL = 30.0
# GraphQL draws on the 5000/hr core budget instead, so it can go faster.
GRAPHQL_MIN_INTERVAL = 0.8


@dataclass
class _Token:
    """Pacing state for one credential.

    GitHub applies both the primary quota and the secondary limit per account
    rather than per token, so each credential needs its own backoff: one token
    being throttled says nothing about the others, and sharing a single
    interval across them would spread one account's penalty to all of them.
    """

    value: str | None
    index: int
    base_interval: float
    min_interval: float
    last_request: float = 0.0
    consecutive_ok: int = 0
    # Monotonic time before which GitHub has asked this token to stay quiet.
    available_at: float = 0.0
    # Set when GitHub rejects the credential outright; unlike a rate limit this
    # never recovers, so the token is dropped rather than retried.
    retired: bool = False

    @property
    def label(self) -> str:
        """Identify a token in logs without ever printing its value."""
        return "unauthenticated" if self.value is None else f"token {self.index + 1}"

    def ready_at(self, interval: float | None = None) -> float:
        gap = self.min_interval if interval is None else interval
        return max(self.available_at, self.last_request + gap)

    def slow_down(self) -> None:
        self.min_interval = min(self.min_interval * 1.8, MAX_INTERVAL)
        self.consecutive_ok = 0
        log.info(
            "secondary rate limit on %s: its interval is now %.1fs",
            self.label,
            self.min_interval,
        )

    def speed_up(self) -> None:
        """Decay back toward the base interval after sustained success."""
        self.consecutive_ok += 1
        if self.consecutive_ok >= 20 and self.min_interval > self.base_interval:
            self.min_interval = max(self.min_interval / 1.5, self.base_interval)
            self.consecutive_ok = 0
            log.info("%s recovered: its interval is now %.1fs", self.label, self.min_interval)


class GitHubClient:
    """Minimal REST client with throttling and rate-limit-aware retries.

    Accepts one credential or several. With several, a token that GitHub
    throttles is set aside until its penalty expires and the next request goes
    out on whichever token is free soonest, so a run continues at full speed
    instead of sleeping. This only helps when the tokens belong to *different*
    GitHub accounts: limits are keyed to the token's owner, so several tokens
    minted from one account share a single budget and rotate to no effect.
    """

    def __init__(
        self,
        token: str | Sequence[str | None] | None,
        session: requests.Session | None = None,
        max_retries: int = 5,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "mcp-migration-dataset",
            }
        )
        values: list[str | None] = [token] if isinstance(token, str) else list(token or [])
        kept = [v for v in values if v]
        # Search allows roughly 30 req/min with a token and 10 without.
        self.tokens = [
            _Token(value=v, index=i, base_interval=2.1, min_interval=2.1)
            for i, v in enumerate(kept)
        ] or [_Token(value=None, index=0, base_interval=6.1, min_interval=6.1)]
        if len(self.tokens) > 1:
            log.info("rotating over %d tokens", len(self.tokens))
        self.graphql_min_interval = GRAPHQL_MIN_INTERVAL
        self.max_retries = max_retries

    def _acquire(self, interval: float | None = None) -> _Token:
        """Wait for the token that comes free soonest and return it."""
        live = [t for t in self.tokens if not t.retired]
        if not live:
            raise RuntimeError("every GitHub token was rejected; check GITHUB_TOKENS")
        chosen = min(live, key=lambda t: t.ready_at(interval))
        wait = chosen.ready_at(interval) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        chosen.last_request = time.monotonic()
        if chosen.value is not None:
            self.session.headers["Authorization"] = f"Bearer {chosen.value}"
        else:
            self.session.headers.pop("Authorization", None)
        return chosen

    @staticmethod
    def _retry_delay(resp: requests.Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if the response is not retryable."""
        if "Retry-After" in resp.headers:
            return float(resp.headers["Retry-After"])
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = float(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            return max(reset - time.time(), 0.0) + 1.0
        if resp.status_code == 429 or resp.status_code >= 500:
            return float(5 * 2**attempt)
        # A 403 with quota left is GitHub's secondary rate limit, which carries
        # neither Retry-After nor a zeroed remaining count. Back off rather than
        # abandoning a long run.
        if resp.status_code == 403 and _is_secondary_limit(resp):
            return float(30 * 2**attempt)
        return None

    def _request(
        self, method: str, url: str, interval: float | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Issue one throttled request, retrying rate limits and transient
        network failures. Collection runs for hours, so a dropped connection
        or a read timeout must not end the run.

        A rate-limited token is benched for the length of its penalty rather
        than slept on: with other tokens available the next attempt goes out
        immediately on one of them, and with only one token benching it is
        equivalent to sleeping.
        """
        # Budget attempts per token, so adding tokens does not make a request
        # give up sooner than it would have on one.
        for attempt in range(self.max_retries * len(self.tokens)):
            token = self._acquire(interval)
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.exceptions.RequestException as exc:
                delay = float(5 * 2**attempt)
                log.warning("%s %s: %s; retrying in %.0fs", method, url, exc, delay)
                token.available_at = time.monotonic() + delay
                continue
            if resp.status_code == 401:
                # A pooled token may have been revoked or pasted in wrong. That
                # is one collaborator's problem, not a reason to end a run that
                # other credentials can still serve.
                token.retired = True
                remaining = sum(not t.retired for t in self.tokens)
                log.warning(
                    "%s was rejected (401); retiring it, %d token(s) left",
                    token.label,
                    remaining,
                )
                continue
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                delay_or_none = self._retry_delay(resp, attempt)
                if delay_or_none is not None:
                    if resp.status_code == 403 and _is_secondary_limit(resp):
                        token.slow_down()
                    log.warning(
                        "HTTP %s on %s, benching it for %.0fs",
                        resp.status_code,
                        token.label,
                        delay_or_none,
                    )
                    token.available_at = time.monotonic() + delay_or_none
                    continue
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            token.speed_up()
            return data
        raise RuntimeError(f"giving up on {url} after {self.max_retries} attempts per token")

    def graphql(self, query: str) -> dict[str, Any]:
        """POST a GraphQL query. Per-node errors are logged, not raised, because
        a batch containing one deleted repository still returns the others."""
        data = self._request(
            "POST",
            GRAPHQL_URL,
            interval=self.graphql_min_interval,
            json={"query": query},
            timeout=60,
        )
        for error in data.get("errors", []):
            log.debug("GraphQL: %s", error.get("message"))
        return data

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("GET", f"{API_URL}{path}", params=params, timeout=30)


def _to_hit(query: Query, item: dict[str, Any]) -> dict[str, Any]:
    if query.kind == "commits":
        commit = item.get("commit") or {}
        return {
            "query": query.name,
            "kind": "commit",
            "repo": (item.get("repository") or {}).get("full_name", "").lower(),
            "id": item.get("sha"),
            "url": item.get("html_url"),
            "title": first_line(commit.get("message")),
            "date": (commit.get("committer") or {}).get("date"),
        }
    return {
        "query": query.name,
        "kind": "pr" if "pull_request" in item else "issue",
        "repo": item.get("repository_url", "").removeprefix(f"{API_URL}/repos/").lower(),
        "id": item.get("number"),
        "url": item.get("html_url"),
        "title": first_line(item.get("title")),
        "date": item.get("created_at"),
        "author_type": (item.get("user") or {}).get("type"),
    }


def _search_window(
    client: GitHubClient, query: Query, start: datetime, end: datetime
) -> Iterator[dict[str, Any]]:
    path = f"/search/{query.kind}"
    q = f"{query.q} {query.date_field}:{format_window(start, end)}"
    page = 1
    data = client.get(path, {"q": q, "per_page": PER_PAGE, "page": page})
    total = int(data.get("total_count", 0))

    if total > MAX_RESULTS and (end - start) > MIN_WINDOW:
        # Whole seconds only: GitHub rejects timestamps carrying microseconds.
        mid = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        yield from _search_window(client, query, start, mid)
        yield from _search_window(client, query, mid + timedelta(seconds=1), end)
        return
    if total > MAX_RESULTS:
        log.warning(
            "%s %s: %d results, only %d retrievable (window is at the %s floor)",
            query.name,
            format_window(start, end),
            total,
            MAX_RESULTS,
            MIN_WINDOW,
        )
    if data.get("incomplete_results"):
        log.warning(
            "%s %s: GitHub reported incomplete results", query.name, format_window(start, end)
        )

    while True:
        items = data.get("items", [])
        for item in items:
            hit = _to_hit(query, item)
            if query.keeps(hit):
                yield hit
        if len(items) < PER_PAGE or page * PER_PAGE >= min(total, MAX_RESULTS):
            return
        page += 1
        data = client.get(path, {"q": q, "per_page": PER_PAGE, "page": page})


def search(client: GitHubClient, query: Query, until: date) -> Iterator[dict[str, Any]]:
    for start, end in date_windows(query.start, until, query.window_days):
        log.info("%s: %s", query.name, format_window(start, end))
        yield from _search_window(client, query, start, end)
