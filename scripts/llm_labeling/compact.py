"""Print a compact reading view of a dossier batch.

Usage: python scripts/llm_labeling/compact.py WORKDIR/dossiers/batch_NN.md

The dossier is the evidence. This view keeps what decides the codes:
- per commit: the author line, the first message lines, trailers, the PR, and
  the diff lines that matched a detector signal;
- the issues around the episode and the repository state after it.

A dossier clipped at its size limit loses its last commits. For those, the
mined record of every commit (subject and signals moved) is appended, so no
commit of an episode goes unseen. It prints no detector output.
"""

import json
import re
import sys
from pathlib import Path

from mcp_migration.labeling.context import load_commits

COMMITS = Path("data/interim/commits")
EPISODES = Path("data/processed/episodes.jsonl")
MSG_LINES = 8
SIGNAL_LINES = 10


def main() -> None:
    text = Path(sys.argv[1]).read_text()
    wanted = {m.group(1) for m in re.finditer(r"^### \d+\. (\S+)", text, re.M)}
    episodes = {}
    with EPISODES.open() as fh:
        for line in fh:
            e = json.loads(line)
            if e["episode_id"] in wanted:
                episodes[e["episode_id"]] = e
    for block in re.split(r"(?m)^(?=### )", text):
        if block.strip():
            print(compact(block, episodes))


def compact(block: str, episodes: dict) -> str:
    lines = block.splitlines()
    eid = lines[0].split(". ", 1)[1].strip()
    out = [lines[0], lines[1] if len(lines) > 1 else ""]
    seen_shas = set()
    i, n = 2, len(lines)
    while i < n:
        ln = lines[i]
        if re.match(r"^  \[\d+\] ", ln):
            seen_shas.add(ln.split()[1])
            out.append(ln[:230])
            i += 1
            msg, sig = 0, 0
            while i < n and not re.match(r"^  (\[\d+\]|Issues|State|Later|…)", lines[i]):
                body = lines[i]
                if body.startswith("      message:"):
                    pass
                elif body.startswith("        ") and msg < MSG_LINES:
                    if body.strip():
                        out.append(body[:200])
                        msg += 1
                elif body.startswith(
                    ("      subject:", "      trailers:", "      PR", "        body:")
                ) or body.startswith("      mined signals"):
                    out.append(body[:260])
                elif "⟵" in body and sig < SIGNAL_LINES:
                    out.append(body[:200])
                    sig += 1
                i += 1
            continue
        if ln.startswith(("  Issues", "    issue #", "    PR #", "  State", "  Later", "    20")):
            out.append(ln[:240])
        elif ln.startswith("    ") and not ln.startswith("     ") and ": " in ln:
            out.append(ln[:240])  # a state line: path: import/dependency lines
        elif ln.startswith("  … "):
            out.append(ln[:200])
        i += 1
    ep = episodes.get(eid)
    if ep:
        missing = [s for s in ep["shas"] if s[:10] not in seen_shas]
        if missing:
            commits = load_commits(COMMITS, ep["repo"])
            out.append(
                f"  (dossier clipped or condensed: {len(missing)} commits from the mined record)"
            )
            for s in missing[:25]:
                c = commits.get(s, {})
                out.append(
                    f"    {s[:10]} {c.get('date', '')[:10]} {c.get('message', '')[:100]}"
                    f" | +{sorted(c.get('added', {}))} -{sorted(c.get('removed', {}))}"
                )
            if len(missing) > 25:
                out.append(f"    … {len(missing) - 25} more")
    return "\n".join(out)


if __name__ == "__main__":
    main()
