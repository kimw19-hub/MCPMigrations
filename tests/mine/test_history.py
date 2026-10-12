import subprocess
from collections.abc import Callable
from pathlib import Path

from mcp_migration.mine.history import mine_repo

V1 = 'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("Demo")\n'
V2 = 'from mcp.server.mcpserver import MCPServer\nmcp = MCPServer("Demo")\n'


def test_mine_repo_detects_migration(
    git_repo: tuple[Path, Callable[[dict[str, str], str], None]],
) -> None:
    repo, commit = git_repo
    commit({"server.py": V1, "pyproject.toml": 'dependencies = ["mcp>=1.9,<2"]\n'}, "init")
    commit({"README.md": "docs only\n"}, "docs")
    commit(
        {"server.py": V2, "pyproject.toml": 'dependencies = ["mcp>=2,<3"]\n'},
        "Migrate to MCP SDK v2\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    )

    result = mine_repo(repo)

    assert result.error is None
    assert [c.message for c in result.commits] == ["init", "Migrate to MCP SDK v2"]
    init, migration = result.commits
    assert init.pin_below_2_added and not init.migration_candidate
    assert migration.migration_candidate
    assert migration.author_kind == "ai_agent_assisted"
    assert migration.repo == "acme/weather-mcp"
    assert result.head is not None
    assert result.head.static_era == "sdk_v2_default"


def test_head_scan_reads_the_object_database_not_the_working_tree(
    git_repo: tuple[Path, Callable[[dict[str, str], str], None]], tmp_path: Path
) -> None:
    """Clones are made with --no-checkout, so scanning the filesystem would
    report zero signals for every repository and silently mislabel its era."""
    repo, commit = git_repo
    commit({"server.py": "from mcp.server.mcpserver import MCPServer\n"}, "v2 server")

    bare = tmp_path / "acme__no-checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-checkout", str(repo), str(bare)],
        check=True,
        capture_output=True,
    )
    # Nothing is checked out: a filesystem walk would find no source files.
    assert not (bare / "server.py").exists()

    from mcp_migration.mine.history import scan_head

    checked_out = scan_head(repo)
    no_checkout = scan_head(bare)
    assert no_checkout.signals == checked_out.signals
    assert no_checkout.static_era == checked_out.static_era
    assert no_checkout.signals.get("py_v2_mcpserver_import", 0) > 0


def test_head_scan_ignores_untracked_files(
    git_repo: tuple[Path, Callable[[dict[str, str], str], None]],
) -> None:
    """Reading the HEAD tree also excludes local build output and scratch files
    that a filesystem walk would wrongly count as repository content."""
    repo, commit = git_repo
    commit({"server.py": "from mcp.server.mcpserver import MCPServer\n"}, "v2 server")
    (repo / "scratch.py").write_text("from mcp.server.fastmcp import FastMCP\n")

    from mcp_migration.mine.history import scan_head

    assert scan_head(repo).signals.get("py_v1_fastmcp_import", 0) == 0


def test_the_prefilter_changes_the_cost_not_the_records(
    git_repo: tuple[Path, Callable[[dict[str, str], str], None]],
) -> None:
    """Every case where the two walks could plausibly part ways, in one history."""
    from mcp_migration.mine.history import candidate_shas

    repo, commit = git_repo

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    commit({"server.py": V1.upper()}, "root commit, signal only in upper case")
    commit({"engine.cpp": "listRoots(x);\n"}, "a signal in a file the miner ignores")
    commit({"node_modules/m/i.js": 'require("@modelcontextprotocol/sdk")\n'}, "vendored")
    commit({"deploy/requirements-prod.txt": "mcp<2\n"}, "nested requirements pin")
    commit({"notes.py": "nothing to see\n"}, "relevant file, no signal")
    git("checkout", "-q", "-b", "side")
    commit({"server.py": V2}, "migrate on a branch")
    git("checkout", "-q", "-")
    commit({"client.ts": 'import "@modelcontextprotocol/sdk"\n'}, "meanwhile on main")
    git("merge", "-q", "--no-edit", "side")
    commit({"server.py": V2 + "headers['Mcp-Session-Id']\n"}, "after the merge")
    # A branch whose work the merge throws away: pydriller still visits it, and
    # git log would skip it under a pathspec without --full-history.
    git("checkout", "-q", "-b", "abandoned")
    commit({"tools.py": "from mcp.server.fastmcp import FastMCP\n"}, "abandoned experiment")
    git("checkout", "-q", "-")
    git("merge", "-q", "-s", "ours", "--no-edit", "abandoned")
    # A rename with a signal in the moved content: --no-renames is only a
    # precaution, but the prefilter must still agree with the walk here.
    git("mv", "client.ts", "client_v1.ts")
    commit({"client_v1.ts": 'import "@modelcontextprotocol/sdk"\n// moved\n'}, "rename")

    full, fast = mine_repo(repo), mine_repo(repo, prefilter=True)

    assert full.error is None and fast.error is None
    assert [c.to_dict() for c in fast.commits] == [c.to_dict() for c in full.commits]
    assert fast.head == full.head
    # A superset, and a loose one: the case-insensitive root commit and the
    # vendored file get through to the miner, which then discards them. What it
    # does keep back is what makes it cheap -- files the miner never reads,
    # commits with no signal, and the merge.
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H %s"], capture_output=True, text=True
    ).stdout.splitlines()
    subject = dict(line.split(" ", 1) for line in log)
    passed = {subject[sha] for sha in candidate_shas(repo)}
    assert {subject[c.sha] for c in full.commits} < passed
    assert passed.isdisjoint(
        {"a signal in a file the miner ignores", "relevant file, no signal", "Merge branch 'side'"}
    )


def test_a_prefilter_over_budget_is_a_timeout(
    git_repo: tuple[Path, Callable[[dict[str, str], str], None]],
    monkeypatch: object,
) -> None:
    import pytest

    from mcp_migration.mine.history import TIMEOUT_PREFIX

    repo, commit = git_repo
    commit({"server.py": V1}, "init")

    def slow(*args: object, **kwargs: object) -> set[str]:
        raise subprocess.TimeoutExpired("git log", 1)

    mp = pytest.MonkeyPatch()
    mp.setattr("mcp_migration.mine.history.candidate_shas", slow)
    try:
        result = mine_repo(repo, budget_s=1, prefilter=True)
    finally:
        mp.undo()
    assert result.error is not None and result.error.startswith(TIMEOUT_PREFIX)
    assert result.head is None
