"""Collect GitHub evidence for every sampled episode, cached as JSON.

Usage: python scripts/llm_labeling/collect.py SHEET.csv

Run from the repo root with the venv's python; GITHUB_TOKENS in the env.
Responses are cached under data/interim/coder-llm-cache, so the dossiers can be
rebuilt offline and identically.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

from mcp_migration.config import github_tokens
from mcp_migration.storage import read_jsonl

CACHE = Path("data/interim/coder-llm-cache")
API = "https://api.github.com"
MANIFEST_NAMES = {"pyproject.toml", "setup.py", "setup.cfg", "package.json", "go.mod"}

tokens = github_tokens()
_cycle = itertools.cycle(range(len(tokens)))
_lock = threading.Lock()
sessions = []
for t in tokens:
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {t}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mcp-migration-dataset",
        }
    )
    sessions.append(s)


def get(path: str, raw: bool = False) -> tuple[int, object]:
    key = hashlib.sha1(path.encode()).hexdigest()
    f = CACHE / "http" / key[:2] / f"{key}.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d["status"], d["body"]
    for attempt in range(6):
        with _lock:
            i = next(_cycle)
        headers = {"Accept": "application/vnd.github.raw"} if raw else {}
        try:
            r = sessions[i].get(API + path, headers=headers, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (403, 429) and (
            r.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in r.text.lower()
        ):
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        body = (r.text if raw else r.json()) if r.status_code == 200 else None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"status": r.status_code, "body": body}))
        return r.status_code, body
    return 0, None


def collect(ep: dict) -> str:
    repo, shas = ep["repo"], ep["shas"]
    for sha in shas:
        get(f"/repos/{repo}/commits/{sha}")
        get(f"/repos/{repo}/commits/{sha}/pulls")
    # Issues and PRs created around the episode (since = updated; filter later).
    since = (datetime.fromisoformat(ep["start"]) - timedelta(days=14)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for page in (1, 2, 3):
        st, body = get(
            f"/repos/{repo}/issues?state=all&since={since}&per_page=100&page={page}&sort=created&direction=asc"
        )
        if st != 200 or not isinstance(body, list) or len(body) < 100:
            break
    # The tree after the episode: manifests and MCP files at the last commit.
    last = shas[-1]
    st, tree = get(f"/repos/{repo}/git/trees/{last}?recursive=1")
    wanted: set[str] = set()
    if st == 200 and isinstance(tree, dict):
        for item in tree.get("tree", []):
            p = item["path"]
            name = p.rsplit("/", 1)[-1]
            if item["type"] != "blob" or "node_modules/" in p or p.count("/") > 3:
                continue
            if name in MANIFEST_NAMES or (
                name.startswith("requirements") and name.endswith((".txt", ".in"))
            ):
                wanted.add(p)
    for sha in shas:
        st, c = get(f"/repos/{repo}/commits/{sha}")
        if st == 200 and isinstance(c, dict):
            for f in c.get("files", []):
                if (
                    f["filename"].endswith((".py", ".ts", ".js", ".mjs", ".tsx"))
                    and f["status"] != "removed"
                ):
                    wanted.add(f["filename"])
    for p in sorted(wanted)[:40]:
        get(f"/repos/{repo}/contents/{quote(p)}?ref={last}", raw=True)
    return ep["episode_id"]


def main() -> None:
    sheet = sys.argv[1] if len(sys.argv) > 1 else "data/labeling/round1/sample.csv"
    with open(sheet, encoding="utf-8-sig") as fh:
        ids = [r["episode_id"] for r in csv.DictReader(fh)]
    wanted = set(ids)
    eps = [
        e for e in read_jsonl(Path("data/processed/episodes.jsonl")) if e["episode_id"] in wanted
    ]
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        for fut in as_completed([pool.submit(collect, e) for e in eps]):
            fut.result()
            done += 1
            if done % 25 == 0:
                print(done, "episodes", flush=True)
    print("finished", done, flush=True)


if __name__ == "__main__":
    sys.exit(main())
