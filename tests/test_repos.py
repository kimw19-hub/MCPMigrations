from pathlib import Path

import pytest

from mcp_migration.repos import extract_repos, normalize_repo, repo_dir_name, slug_from_dir_name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/Owner/Repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("https://github.com/owner/repo/tree/main/src", "owner/repo"),
        ("owner/repo", "owner/repo"),
        ("https://github.com/orgs/acme", None),
        ("not a repo", None),
    ],
)
def test_normalize_repo(text: str, expected: str | None) -> None:
    assert normalize_repo(text) == expected


def test_extract_repos_from_json_and_lines() -> None:
    text = '{"url": "https://github.com/a/b"}\nc/d\nhttps://raw.githubusercontent.com/x/y/f'
    assert extract_repos(text) == {"a/b", "c/d"}


def test_dir_name_roundtrip() -> None:
    assert slug_from_dir_name(repo_dir_name("my-org/my__repo")) == "my-org/my__repo"


def test_seed_reads_per_query_files_not_the_merged_one(tmp_path: Path) -> None:
    """The merged file is written only after every query finishes, so an
    interrupted search must still contribute its completed queries."""
    from mcp_migration.cli import build_parser
    from mcp_migration.config import Paths

    paths = Paths(tmp_path)
    paths.search_dir.mkdir(parents=True)
    (paths.search_dir / "q1.jsonl").write_text('{"repo":"a/b"}\n', encoding="utf-8")
    (paths.search_dir / "q2.jsonl").write_text('{"repo":"c/d"}\n{"repo":"a/b"}\n', encoding="utf-8")
    # No merged search_hits.jsonl exists, exactly as after an interrupted run.
    assert not paths.search_hits.exists()

    args = build_parser().parse_args(["--data-dir", str(tmp_path), "seed", "--include-search-hits"])
    assert args.func(args, paths) == 0
    assert sorted(paths.repo_list.read_text().split()) == ["a/b", "c/d"]
