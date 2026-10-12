"""A local web app for coding the labeling sheet.

One coder, one process, one sheet: each coder runs their own copy against
their own CSV, which keeps the coding independent the way kappa assumes. The
app reads the mined records the replication package ships, so it needs no
network except for the optional GitHub detail on each commit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from mcp_migration.config import Paths
from mcp_migration.labeling.context import (
    GitHubFetcher,
    episode_context,
    load_commits,
    search_text,
)
from mcp_migration.labeling.sheet import ALLOWED, InvalidLabel, Sheet, status
from mcp_migration.report.sampling import (
    CODEBOOK_COLUMNS,
    DEPRECATED_FEATURES,
    DISPOSITIONS,
)
from mcp_migration.storage import read_jsonl

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Corpus:
    """The sampled episodes and their commits, loaded once at start-up."""

    def __init__(self, paths: Paths, episode_ids: list[str]) -> None:
        wanted = set(episode_ids)
        self.episodes: dict[str, dict[str, Any]] = {
            str(e["episode_id"]): e for e in read_jsonl(paths.episodes) if e["episode_id"] in wanted
        }
        missing = wanted - self.episodes.keys()
        if missing:
            log.warning(
                "%d sheet rows have no episode in %s, e.g. %s",
                len(missing),
                paths.episodes,
                sorted(missing)[0],
            )
        self.commits: dict[str, dict[str, dict[str, Any]]] = {}
        for episode in self.episodes.values():
            repo = str(episode["repo"])
            if repo not in self.commits:
                self.commits[repo] = load_commits(paths.commits_dir, repo)
        self.text = {
            eid: search_text(e, self.commits[str(e["repo"])]) for eid, e in self.episodes.items()
        }


def create_app(
    paths: Paths,
    sheet_path: Path,
    coder: str,
    codebook: Path | None = None,
    fetcher: GitHubFetcher | None = None,
) -> FastAPI:
    sheet = Sheet(sheet_path)
    corpus = Corpus(paths, [r["episode_id"] for r in sheet.rows()])
    app = FastAPI(title=f"MCP migration labeling ({coder})", docs_url=None, redoc_url=None)

    def _row(episode_id: str) -> dict[str, str]:
        row = next((r for r in sheet.rows() if r["episode_id"] == episode_id), None)
        if row is None:
            raise HTTPException(404, f"{episode_id} is not in the sheet")
        return row

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "coder": coder,
            "sheet": str(sheet_path),
            "columns": list(CODEBOOK_COLUMNS),
            "allowed": {k: list(v) for k, v in ALLOWED.items()},
            "deprecated_features": list(DEPRECATED_FEATURES),
            "dispositions": list(DISPOSITIONS),
            "github": None if fetcher is None else {"authenticated": fetcher.authenticated},
        }

    @app.get("/api/episodes")
    def episodes(q: str = "", state: str = "") -> list[dict[str, Any]]:
        terms = q.lower().split()
        out = []
        for position, row in enumerate(sheet.rows(), start=1):
            eid = row["episode_id"]
            row_status = status(row)
            if state and row_status != state:
                continue
            if terms and not all(t in corpus.text.get(eid, eid.lower()) for t in terms):
                continue
            out.append(
                {
                    "position": position,
                    "episode_id": eid,
                    "repo": row.get("repo", ""),
                    "start": row.get("start", "")[:10],
                    "n_commits": row.get("n_commits", ""),
                    "kind": row.get("kind", ""),
                    "head_static_era": row.get("head_static_era", ""),
                    "status": row_status,
                }
            )
        return out

    @app.get("/api/episode")
    def episode(id: str) -> dict[str, Any]:
        row = _row(id)
        record = corpus.episodes.get(id)
        if record is None:
            raise HTTPException(404, f"{id} is in the sheet but not in {paths.episodes}")
        context = episode_context(record, corpus.commits.get(str(record["repo"]), {}))
        labels = {c: row.get(c, "") for c in CODEBOOK_COLUMNS}
        return {"episode": context, "labels": labels, "status": status(row)}

    @app.put("/api/episode")
    def save(id: str, labels: Annotated[dict[str, str], Body()]) -> dict[str, Any]:
        try:
            row = sheet.update(id, labels)
        except KeyError:
            raise HTTPException(404, f"{id} is not in the sheet") from None
        except InvalidLabel as exc:
            raise HTTPException(422, str(exc)) from None
        return {"labels": {c: row.get(c, "") for c in CODEBOOK_COLUMNS}, "status": status(row)}

    @app.get("/api/commit")
    def commit(repo: str, sha: str) -> dict[str, Any]:
        if fetcher is None:
            raise HTTPException(503, "GitHub lookups are switched off (--offline)")
        record = corpus.commits.get(repo, {}).get(sha)
        if record is None:
            raise HTTPException(404, "not a commit of a sampled episode")
        try:
            return fetcher.commit(repo, sha, record["signal_files"])
        except LookupError as exc:
            raise HTTPException(502, str(exc)) from None
        except Exception as exc:  # network trouble must not take the page down
            log.warning("GitHub lookup for %s@%s failed: %s", repo, sha, exc)
            raise HTTPException(502, f"GitHub lookup failed: {exc}") from None

    @app.get("/api/codebook", response_class=PlainTextResponse)
    def codebook_text() -> str:
        if codebook is None or not codebook.exists():
            raise HTTPException(404, "docs/codebook.md not found")
        return codebook.read_text(encoding="utf-8")

    return app
