"""Render one compact evidence dossier per sampled episode from the cache.

Usage: python scripts/llm_labeling/dossier.py SHEET.csv OUTDIR [--with-detector]
(run from the repo root, after collect.py has filled the cache)

Writes OUTDIR/batch_NN.md with 20 episodes each, in sheet order.

Round 1 ended every dossier with the detector's verdict, marked "read after
judging". A model reading the whole dossier sees it anyway, so from round 2 it
is left out; --with-detector reproduces the round-1 dossiers.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from mcp_migration.labeling.context import load_commits, trailers
from mcp_migration.mine.signals import count_signals
from mcp_migration.storage import read_jsonl

CACHE = Path("data/interim/coder-llm-cache")
COMMITS = Path("data/interim/commits")
MCP_RX = re.compile(
    r"(?i)\bmcp\b|mcp[._-]|modelcontextprotocol|fastmcp|mcpserver|mcperror|protocolversion|\b20(24|25|26)-\d\d-\d\d\b.*(?i:protocol|version)"
)
ISSUE_RX = re.compile(
    r"(?i)mcp|sdk|fastmcp|2\.0|v2|import|break|broken|upgrade|migrat|protocol|depend|bump|pin"
)
MANIFEST_NAMES = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "go.mod")
BATCH = 20


def cached(path: str) -> tuple[int, object]:
    key = hashlib.sha1(path.encode()).hexdigest()
    f = CACHE / "http" / key[:2] / f"{key}.json"
    if not f.exists():
        return -1, None
    d = json.loads(f.read_text())
    return d["status"], d["body"]


def clip(text: str, n: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text if len(text) <= n else text[:n] + f" …[+{len(text) - n} chars]"


def indent(text: str, pad: str = "      ") -> str:
    return "\n".join(pad + line for line in text.splitlines())


def mcp_diff_lines(files: list[dict], limit: int) -> list[str]:
    out: list[str] = []
    for f in files:
        patch = f.get("patch") or ""
        hits = []
        for line in patch.splitlines():
            if line[:1] not in "+-" or line.startswith(("+++", "---")):
                continue
            body = line[1:]
            sig = sorted(count_signals([body]))
            if sig or MCP_RX.search(body):
                hits.append(f"{line[:160]}" + (f"   ⟵ {', '.join(sig)}" if sig else ""))
        if hits:
            out.append(f"    {f['filename']} ({f['status']} +{f['additions']} -{f['deletions']}):")
            out += [f"      {h}" for h in hits[:12]]
            if len(hits) > 12:
                out.append(f"      … {len(hits) - 12} more MCP lines")
        if len(out) > limit:
            out.append("    … (diff lines truncated)")
            break
    return out


def commit_block(repo: str, sha: str, mined: dict, full: bool, idx: int) -> str:
    st, c = cached(f"/repos/{repo}/commits/{sha}")
    head = f"  [{idx}] {sha[:10]} {mined.get('date', '')[:10]}"
    if st != 200 or not isinstance(c, dict):
        msg = mined.get("message", "")
        return (
            head
            + f" | GitHub: unavailable (HTTP {st})\n      subject: {msg}\n      mined signals +{sorted(mined.get('added', {}))} -{sorted(mined.get('removed', {}))}"
        )
    a = c.get("author") or {}
    cm = c.get("committer") or {}
    git_author = c["commit"]["author"]
    message = c["commit"]["message"]
    who = f"{a.get('login') or '(no account)'}" + (
        f" [{a.get('type')}]" if a.get("type") and a.get("type") != "User" else ""
    )
    who += f" <git name: {git_author.get('name')}, email domain: {(git_author.get('email') or '').split('@')[-1]}>"
    if cm.get("login") and cm.get("login") != a.get("login") and cm.get("login") != "web-flow":
        who += f"; committer {cm['login']}"
    lines = [
        head
        + f" | author {who} | files {len(c.get('files', []))} +{c['stats']['additions']} -{c['stats']['deletions']}"
    ]
    if not full:
        lines.append(f"      subject: {message.splitlines()[0][:200]}")
        return "\n".join(lines)
    lines.append("      message:\n" + indent(clip(message, 1200), "        "))
    tr = trailers(message)
    if tr:
        lines.append(
            "      trailers: "
            + " | ".join(t["text"][:100] + (" [AGENT]" if t["agent"] else "") for t in tr)
        )
    st2, pulls = cached(f"/repos/{repo}/commits/{sha}/pulls")
    if st2 == 200 and isinstance(pulls, list) and pulls:
        for p in pulls[:2]:
            u = p.get("user") or {}
            state = "merged" if p.get("merged_at") else p.get("state")
            lines.append(
                f"      PR #{p['number']} [{state}] by {u.get('login')}{' [' + u.get('type') + ']' if u.get('type') != 'User' else ''}: {p['title'][:150]}"
            )
            body = (p.get("body") or "").strip()
            if body:
                lines.append("        body: " + clip(body, 900).replace("\n", " / "))
    else:
        lines.append("      PR: none found")
    lines += mcp_diff_lines(c.get("files", []), 30)
    return "\n".join(lines)


def issues_block(ep: dict) -> list[str]:
    repo = ep["repo"]
    lo = datetime.fromisoformat(ep["start"]) - timedelta(days=14)
    hi = datetime.fromisoformat(ep["end"]) + timedelta(days=14)
    since = lo.strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict] = []
    for page in (1, 2, 3):
        st, body = cached(
            f"/repos/{repo}/issues?state=all&since={since}&per_page=100&page={page}&sort=created&direction=asc"
        )
        if st != 200 or not isinstance(body, list):
            break
        items += body
        if len(body) < 100:
            break
    inwin = [
        i
        for i in items
        if lo <= datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")) <= hi
    ]
    rel = [i for i in inwin if ISSUE_RX.search(i.get("title") or "")]
    out = [
        f"  Issues/PRs created {lo.date()}..{hi.date()}: {len(inwin)} total, {len(rel)} with relevant words"
    ]
    for i in rel[:10]:
        u = i.get("user") or {}
        kind = "PR" if "pull_request" in i else "issue"
        out.append(
            f"    {kind} #{i['number']} {i['created_at'][:10]} by {u.get('login')}{' [' + u.get('type') + ']' if u.get('type') != 'User' else ''} ({i['state']}): {i['title'][:140]}"
        )
        if kind == "issue" and i.get("body"):
            out.append("      " + clip(i["body"], 300).replace("\n", " / "))
    return out


def state_block(ep: dict) -> list[str]:
    repo, last = ep["repo"], ep["shas"][-1]
    st, tree = cached(f"/repos/{repo}/git/trees/{last}?recursive=1")
    if st != 200 or not isinstance(tree, dict):
        return [f"  State after episode: tree unavailable (HTTP {st})"]
    paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]
    out = [f"  State after episode (at {last[:10]}; {len(paths)} files):"]
    candidates = [
        p
        for p in paths
        if p.rsplit("/", 1)[-1] in MANIFEST_NAMES or p.rsplit("/", 1)[-1].startswith("requirements")
    ]
    for sha in ep["shas"]:
        s2, c = cached(f"/repos/{repo}/commits/{sha}")
        if s2 == 200 and isinstance(c, dict):
            candidates += [
                f["filename"]
                for f in c.get("files", [])
                if f["filename"].endswith((".py", ".ts", ".js", ".mjs", ".tsx"))
            ]
    seen, shown = set(), 0
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        s3, text = cached(f"/repos/{repo}/contents/{quote(p)}?ref={last}")
        if s3 != 200 or not isinstance(text, str):
            continue
        hits = [
            ln.strip()[:150]
            for ln in text.splitlines()
            if MCP_RX.search(ln)
            and (
                "import" in ln
                or "require" in ln
                or ("mcp" in ln.lower() and any(ch in ln for ch in '"=<>~^'))
                or count_signals([ln])
            )
        ]
        if hits:
            out.append(f"    {p}: " + " ‖ ".join(dict.fromkeys(hits[:6])))
            shown += 1
        if shown >= 14:
            out.append("    … more files")
            break
    if shown == 0:
        out.append("    (no MCP dependency or import lines found in manifests or changed files)")
    return out


def later_block(ep: dict, commits: dict) -> list[str]:
    after = sorted(
        (c for c in commits.values() if c["date"] > ep["end"] and not c["is_merge"]),
        key=lambda c: c["date"],
    )
    if not after:
        return ["  Later signal commits in repo: none mined after the episode"]
    out = [f"  Later signal commits in repo ({len(after)}; first 5):"]
    for c in after[:5]:
        out.append(
            f"    {c['date'][:10]} {c['message'][:90]} | +{sorted(c['added'])} -{sorted(c['removed'])}"
        )
    return out


def render(pos: int, ep: dict, with_detector: bool = False) -> str:
    repo = ep["repo"]
    commits = load_commits(COMMITS, repo)
    shas = ep["shas"]
    lines = [
        f"### {pos}. {ep['episode_id']}",
        f"  {len(shas)} commit(s), {ep['start'][:10]} → {ep['end'][:10]} ({ep['duration_days']} d), +{ep['lines_added']}/-{ep['lines_deleted']}",
    ]
    # Show every commit in full when few; otherwise full for first, last and
    # any commit moving SDK-generation/pin signals, one line for the rest.
    key = {
        "py_v2_mcpserver_import",
        "ts_v2_split_pkg",
        "py_pin_mcp_v2",
        "py_pin_mcp_below_2",
        "py_v1_fastmcp_import",
        "ts_v1_sdk_pkg",
        "py_v2_MCPError",
        "py_v1_McpError",
        "py_standalone_fastmcp",
    }
    full_budget = 12
    for i, sha in enumerate(shas, 1):
        m = commits.get(sha, {})
        moved = key & (set(m.get("added", {})) | set(m.get("removed", {})))
        full = len(shas) <= 6 or i in (1, len(shas)) or (moved and full_budget > 0)
        if full and len(shas) > 6:
            full_budget -= 1
        if len(shas) > 40 and not full and i > 3 and i < len(shas) - 1:
            if i == 4:
                lines.append(f"  … {len(shas) - 5} further commits listed by subject only …")
            lines.append(
                f"  [{i}] {sha[:10]} {m.get('date', '')[:10]} {m.get('message', '')[:90]} | +{sorted(m.get('added', {}))} -{sorted(m.get('removed', {}))}"
            )
            continue
        lines.append(commit_block(repo, sha, m, bool(full), i))
    lines += issues_block(ep)
    lines += state_block(ep)
    lines += later_block(ep, commits)
    if with_detector:
        lines.append(
            f"  DETECTOR (read after judging): kind={ep['kind']} era={ep['head_static_era']} scope={ep['scope']} authors={ep['author_kinds']} revisions+={ep['revisions_added']} -={ep['revisions_removed']} deprecated_removed={ep['deprecated_removed']} replacements={ep['replacements']}"
        )
    text = "\n".join(lines)
    return clip(text, 14000) if len(text) > 14000 else text


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_detector = "--with-detector" in sys.argv
    sheet, out = Path(args[0]), Path(args[1])
    out.mkdir(parents=True, exist_ok=True)
    with open(sheet, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    wanted = {r["episode_id"] for r in rows}
    eps = {
        e["episode_id"]: e
        for e in read_jsonl(Path("data/processed/episodes.jsonl"))
        if e["episode_id"] in wanted
    }
    for b in range(0, len(rows), BATCH):
        chunk = rows[b : b + BATCH]
        text = "\n\n".join(
            render(b + i + 1, eps[r["episode_id"]], with_detector) for i, r in enumerate(chunk)
        )
        (out / f"batch_{b // BATCH + 1:02d}.md").write_text(text + "\n")
    print("wrote", (len(rows) + BATCH - 1) // BATCH, "batches to", out)


if __name__ == "__main__":
    main()
