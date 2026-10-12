"""Explorer payloads."""

from pathlib import Path


def test_compare_base_survives_the_clone_being_deleted(tmp_path: Path) -> None:
    """`harvest` removes the clone, so the base has to come from the records."""
    import json

    from mcp_migration.report.viz import episode_rows

    commits = tmp_path / "commits"
    commits.mkdir()
    (commits / "a__b.jsonl").write_text(
        json.dumps({"sha": "a" * 40, "parent_sha": "b" * 40}) + "\n", encoding="utf-8"
    )
    episode = {
        "episode_id": "a/b#0",
        "repo": "a/b",
        "kind": "migration",
        "start": "2026-08-01T00:00:00+00:00",
        "shas": ["a" * 40, "c" * 40],
    }
    # No repos_dir at all: nothing may shell out to git.
    [row] = episode_rows([episode], commits, None)
    assert row["b"] == "b" * 10
