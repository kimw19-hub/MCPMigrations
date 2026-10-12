"""Repository metadata via the GitHub GraphQL API.

Cloning every seeded repository is impractical (tens of thousands of repos,
hundreds of GB). Metadata is fetched in batches so that repositories which
cannot contain a migration -- last pushed before the revision existed, archived,
empty -- are excluded before cloning. GraphQL is used because it returns up to
``BATCH_SIZE`` repositories per request, where REST would need one call each.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Any

from mcp_migration.collect.search import GitHubClient

log = logging.getLogger(__name__)

BATCH_SIZE = 50

_FIELDS = """
    nameWithOwner
    isArchived
    isFork
    isEmpty
    diskUsage
    stargazerCount
    createdAt
    pushedAt
    primaryLanguage { name }
    defaultBranchRef { name }
"""


def _build_query(slugs: Sequence[str]) -> str:
    parts = []
    for i, slug in enumerate(slugs):
        owner, _, name = slug.partition("/")
        parts.append(f'  r{i}: repository(owner: "{owner}", name: "{name}") {{{_FIELDS}}}')
    return "query {\n" + "\n".join(parts) + "\n}"


def _flatten(node: dict[str, Any]) -> dict[str, Any]:
    language = node.get("primaryLanguage") or {}
    branch = node.get("defaultBranchRef") or {}
    return {
        "repo": str(node["nameWithOwner"]).lower(),
        "archived": bool(node.get("isArchived")),
        "fork": bool(node.get("isFork")),
        "empty": bool(node.get("isEmpty")),
        "disk_usage_kb": node.get("diskUsage"),
        "stars": node.get("stargazerCount"),
        "created_at": node.get("createdAt"),
        "pushed_at": node.get("pushedAt"),
        "language": language.get("name"),
        "default_branch": branch.get("name"),
    }


def fetch_metadata(
    client: GitHubClient, slugs: Sequence[str], batch_size: int = BATCH_SIZE
) -> Iterator[dict[str, Any]]:
    """Yield one record per repository that still exists and is reachable.

    Repositories that have been deleted, renamed, or made private resolve to a
    null node; GraphQL reports them in ``errors`` while still returning the rest
    of the batch, so they are simply skipped.
    """
    for start in range(0, len(slugs), batch_size):
        batch = slugs[start : start + batch_size]
        payload = client.graphql(_build_query(batch))
        data = payload.get("data") or {}
        missing = 0
        for i in range(len(batch)):
            node = data.get(f"r{i}")
            if node is None:
                missing += 1
                continue
            yield _flatten(node)
        log.info(
            "metadata %d-%d of %d (%d unavailable)",
            start + 1,
            start + len(batch),
            len(slugs),
            missing,
        )
