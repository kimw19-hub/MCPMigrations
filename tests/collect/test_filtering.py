from __future__ import annotations

from datetime import date

from mcp_migration.collect.filtering import Criteria, apply, exclusion_reason

CRITERIA = Criteria(pushed_after=date(2024, 11, 1), max_disk_kb=100_000)


def _meta(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "repo": "a/b",
        "archived": False,
        "fork": False,
        "empty": False,
        "disk_usage_kb": 1000,
        "pushed_at": "2026-08-01T00:00:00Z",
    }
    return base | overrides


def test_keeps_an_active_repository() -> None:
    assert exclusion_reason(_meta(), CRITERIA) is None


def test_excludes_by_each_criterion() -> None:
    assert exclusion_reason(_meta(empty=True), CRITERIA) == "empty"
    assert exclusion_reason(_meta(archived=True), CRITERIA) == "archived"
    assert exclusion_reason(_meta(fork=True), CRITERIA) == "fork"
    assert exclusion_reason(_meta(pushed_at=None), CRITERIA) == "no_push_date"
    assert (
        exclusion_reason(_meta(pushed_at="2024-01-01T00:00:00Z"), CRITERIA)
        == "pushed_before_cutoff"
    )
    assert exclusion_reason(_meta(disk_usage_kb=200_000), CRITERIA) == "too_large"


def test_keep_list_overrides_every_exclusion() -> None:
    criteria = Criteria(pushed_after=date(2026, 1, 1), keep=frozenset({"a/b"}))
    assert (
        exclusion_reason(_meta(archived=True, pushed_at="2020-01-01T00:00:00Z"), criteria) is None
    )


def test_apply_counts_reasons_and_deduplicates() -> None:
    kept, reasons = apply(
        [_meta(), _meta(), _meta(repo="c/d", archived=True), _meta(repo="e/f", fork=True)],
        CRITERIA,
    )
    assert kept == ["a/b"]  # the duplicate collapses
    assert reasons == {"archived": 1, "fork": 1}


def test_forks_and_archived_can_be_included() -> None:
    lenient = Criteria(pushed_after=date(2024, 11, 1), exclude_archived=False, exclude_forks=False)
    assert exclusion_reason(_meta(archived=True, fork=True), lenient) is None


def test_official_sdk_repositories_are_excluded_by_default() -> None:
    """The SDKs implement the breaking change, so their history contains every
    signal by construction and would dominate any aggregate."""
    meta = _meta(repo="modelcontextprotocol/python-sdk")
    assert exclusion_reason(meta, CRITERIA) == "upstream_sdk"
    assert exclusion_reason(meta, Criteria(exclude_upstream=False)) is None


def test_upstream_matching_is_case_insensitive() -> None:
    assert exclusion_reason(_meta(repo="ModelContextProtocol/Python-SDK"), CRITERIA) == (
        "upstream_sdk"
    )
    assert exclusion_reason(_meta(repo="modelcontextprotocol/typescript-sdk"), CRITERIA) == (
        "upstream_sdk"
    )


def test_a_server_named_like_the_sdk_is_not_excluded() -> None:
    """Only the exact upstream slugs are excluded, not anything resembling them."""
    assert exclusion_reason(_meta(repo="someone/python-sdk-mcp-server"), CRITERIA) is None


def test_the_size_cap_is_reversible() -> None:
    """Excluding large repos risks dropping mature servers, so it must be opt-out."""
    huge = _meta(disk_usage_kb=5_000_000)
    assert exclusion_reason(huge, CRITERIA) == "too_large"
    assert exclusion_reason(huge, Criteria(max_disk_kb=None)) is None


def test_a_named_repository_survives_the_size_cap() -> None:
    huge = _meta(repo="big/server", disk_usage_kb=5_000_000)
    assert exclusion_reason(huge, Criteria(keep=frozenset({"big/server"}))) is None
