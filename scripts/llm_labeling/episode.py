"""Print every commit of an episode from the mined records, with signal files."""

import sys
from pathlib import Path

from mcp_migration.labeling.context import load_commits
from mcp_migration.storage import read_jsonl

eid = sys.argv[1]
e = next(x for x in read_jsonl(Path("data/processed/episodes.jsonl")) if x["episode_id"] == eid)
cs = load_commits(Path("data/interim/commits"), e["repo"])
for s in e["shas"]:
    c = cs[s]
    print(c["date"][:10], s[:8], c["author_kind"][:5], "|", c["message"][:80])
    print("     +", sorted(c["added"]), "-", sorted(c["removed"]), "files:", c["signal_files"][:4])
