from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_migration.config import Paths
from mcp_migration.labeling.context import (
    annotate_patch,
    episode_context,
    file_diff_url,
    range_url,
    trailers,
)
from mcp_migration.report.sampling import CODEBOOK_COLUMNS
from mcp_migration.storage import write_jsonl

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from mcp_migration.labeling.app import create_app

SHA1, SHA2, PARENT = "a" * 40, "b" * 40, "c" * 40
BLANK = dict.fromkeys(CODEBOOK_COLUMNS, "")


def commit(sha: str, parent: str, message: str, date: str) -> dict[str, Any]:
    return {
        "repo": "acme/weather-mcp",
        "sha": sha,
        "parent_sha": parent,
        "date": date,
        "author_kind": "human_or_unknown",
        "is_merge": False,
        "message": message,
        "signal_files": ["server.py"],
        "lines_added": 3,
        "lines_deleted": 2,
        "added": {"py_v2_mcpserver_import": 1},
        "removed": {"py_v1_fastmcp_import": 1},
        "migration_candidate": True,
        "pin_below_2_added": False,
    }


EPISODE = {
    "episode_id": "acme/weather-mcp#0",
    "repo": "acme/weather-mcp",
    "kind": "migration",
    "start": "2026-08-01T10:00:00+00:00",
    "end": "2026-08-03T10:00:00+00:00",
    "duration_days": 2,
    "shas": [SHA1, SHA2],
    "lines_added": 6,
    "lines_deleted": 4,
    "author_kinds": ["human_or_unknown"],
    "deprecated_removed": [],
    "revisions_added": ["2026-07-28"],
    "revisions_removed": [],
    "replacements": [],
    "scope": "sdk_and_protocol",
    "head_static_era": "sdk_v2_default",
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    paths = Paths(tmp_path)
    write_jsonl(paths.episodes, [EPISODE])
    write_jsonl(
        paths.commits_dir / "acme__weather-mcp.jsonl",
        [
            commit(SHA1, PARENT, "Move to mcp 2", "2026-08-01T10:00:00+00:00"),
            commit(SHA2, SHA1, "Drop FastMCP", "2026-08-03T10:00:00+00:00"),
        ],
    )
    sheet = tmp_path / "labeling" / "round1" / "coderA.csv"
    sheet.parent.mkdir(parents=True)
    sheet.write_text(
        "episode_id,repo,kind,start,end,n_commits,head_static_era,first_commit_url,"
        + ",".join(CODEBOOK_COLUMNS)
        + "\nacme/weather-mcp#0,acme/weather-mcp,migration,2026-08-01,2026-08-03,2,"
        "sdk_v2_default,url,,,,,,\n",
        encoding="utf-8",
    )
    codebook = tmp_path / "codebook.md"
    codebook.write_text("# Codebook\n", encoding="utf-8")
    return TestClient(create_app(paths, sheet, "A", codebook))


def test_the_page_and_its_settings_are_served(client: TestClient) -> None:
    assert "<title>" in client.get("/").text
    meta = client.get("/api/meta").json()
    assert meta["coder"] == "A"
    assert "dual_era" in meta["allowed"]["end_state"]
    assert meta["github"] is None
    assert client.get("/api/codebook").text == "# Codebook\n"


def test_the_list_searches_commit_messages_and_signals(client: TestClient) -> None:
    assert len(client.get("/api/episodes").json()) == 1
    assert len(client.get("/api/episodes", params={"q": "drop fastmcp"}).json()) == 1
    assert len(client.get("/api/episodes", params={"q": "py_v2_mcpserver"}).json()) == 1
    assert client.get("/api/episodes", params={"q": "nothing-like-this"}).json() == []


def test_an_episode_comes_with_its_commits_and_links(client: TestClient) -> None:
    body = client.get("/api/episode", params={"id": "acme/weather-mcp#0"}).json()
    episode = body["episode"]
    assert [c["message"] for c in episode["commits"]] == ["Move to mcp 2", "Drop FastMCP"]
    assert episode["links"]["range"].endswith(f"/compare/{PARENT}...{SHA2}")
    assert "created%3A2026-07-18..2026-08-17" in episode["links"]["pulls"]
    assert body["status"] == "todo"


def test_saving_updates_the_sheet_and_the_status(client: TestClient) -> None:
    labels = {**BLANK, "detector_correct": "no"}
    saved = client.put("/api/episode", params={"id": "acme/weather-mcp#0"}, json=labels)
    assert saved.json()["status"] == "incomplete"

    labels["notes"] = "test fixture"
    saved = client.put("/api/episode", params={"id": "acme/weather-mcp#0"}, json=labels)
    assert saved.json()["status"] == "done"
    listed = client.get("/api/episodes", params={"state": "done"}).json()
    assert [r["episode_id"] for r in listed] == ["acme/weather-mcp#0"]


def test_invalid_labels_are_refused_with_the_reason(client: TestClient) -> None:
    labels = {**BLANK, "detector_correct": "maybe"}
    resp = client.put("/api/episode", params={"id": "acme/weather-mcp#0"}, json=labels)
    assert resp.status_code == 422
    assert "detector_correct" in resp.json()["detail"]


def test_github_lookups_report_that_they_are_off(client: TestClient) -> None:
    resp = client.get("/api/commit", params={"repo": "acme/weather-mcp", "sha": SHA1})
    assert resp.status_code == 503


def test_file_links_use_githubs_path_anchor() -> None:
    anchor = hashlib.sha256(b"src/server.py").hexdigest()
    assert file_diff_url("o/r", SHA1, "src/server.py").endswith(f"{SHA1}#diff-{anchor}")


def test_a_single_commit_episode_links_to_the_commit() -> None:
    assert range_url("o/r", [SHA1], PARENT) == f"https://github.com/o/r/commit/{SHA1}"


def test_a_commit_missing_from_the_records_is_still_listed() -> None:
    context = episode_context({**EPISODE, "shas": [SHA1]}, {})
    assert context["commits"][0]["missing"] is True


def test_patches_mark_the_lines_the_detector_matched() -> None:
    patch = "@@ -1 +1 @@\n-from mcp.server.fastmcp import FastMCP\n+import os\n context"
    lines = annotate_patch(patch)["lines"]
    assert lines[1]["s"] == ["py_v1_fastmcp_import"]
    assert lines[2]["s"] == [] and lines[0]["s"] == []


def test_trailers_flag_coding_agents() -> None:
    message = "Fix\n\nSigned-off-by: Ann <a@x>\nCo-Authored-By: Claude <noreply@anthropic.com>"
    found = trailers(message)
    assert [t["agent"] for t in found] == [False, True]


def test_github_details_are_cached(tmp_path: Path) -> None:
    from mcp_migration.labeling.context import GitHubFetcher

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls = 0

        def get(self, url: str, timeout: int) -> Any:
            self.calls += 1
            payload: Any = (
                []
                if url.endswith("/pulls")
                else {
                    "commit": {"message": "Move\n\nCo-authored-by: Copilot <c@x>"},
                    "author": {"login": "ann", "type": "User"},
                    "committer": None,
                    "files": [
                        {
                            "filename": "server.py",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "patch": "+x",
                        }
                    ],
                }
            )
            return type(
                "R",
                (),
                {
                    "status_code": 200,
                    "headers": {},
                    "json": lambda self: payload,
                    "raise_for_status": lambda self: None,
                },
            )()

    session = Session()
    fetcher = GitHubFetcher(tmp_path, None, session)  # type: ignore[arg-type]
    first = fetcher.commit("o/r", SHA1, ["server.py"])
    again = fetcher.commit("o/r", SHA1, ["server.py"])
    assert session.calls == 2
    assert first == again == json.loads((tmp_path / "o__r" / f"{SHA1}.json").read_text())
    assert first["files"][0]["signal"] is True
    assert first["trailers"][0]["agent"] is True
