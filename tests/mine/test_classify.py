import pytest

from mcp_migration.mine.classify import (
    AuthorKind,
    StaticEra,
    author_kind,
    is_migration_candidate,
    static_era,
)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"py_v1_fastmcp_import": 1, "py_v2_mcpserver_import": 1}, StaticEra.MIXED_V1_V2),
        ({"ts_v2_split_pkg": 1, "modern_input_required": 2}, StaticEra.SDK_V2_MODERN_FEATURES),
        ({"py_pin_mcp_v2": 1}, StaticEra.SDK_V2_DEFAULT),
        ({"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1}, StaticEra.SDK_V1_PINNED_BELOW_2),
        ({"ts_v1_sdk_pkg": 3}, StaticEra.SDK_V1),
        ({"py_standalone_fastmcp": 1}, StaticEra.STANDALONE_FASTMCP),
        ({}, StaticEra.NO_OFFICIAL_SDK),
    ],
)
def test_static_era(counts: dict[str, int], expected: StaticEra) -> None:
    assert static_era(counts) is expected


def test_author_kind() -> None:
    assert author_kind("dependabot[bot]", None, "Bump mcp") is AuthorKind.BOT
    trailer = "Migrate\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    assert author_kind("dev", "d@x.io", trailer) is AuthorKind.AI_AGENT_ASSISTED
    assert author_kind("dev", "d@x.io", "Fix bug") is AuthorKind.HUMAN_OR_UNKNOWN


def test_migration_candidate_requires_both_directions() -> None:
    assert is_migration_candidate({"py_v2_mcpserver_import": 1}, {"py_v1_fastmcp_import": 1})
    assert not is_migration_candidate({"py_v2_mcpserver_import": 1}, {})
    assert not is_migration_candidate({}, {"py_v1_fastmcp_import": 1})
