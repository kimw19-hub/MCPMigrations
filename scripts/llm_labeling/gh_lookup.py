"""Ad-hoc GitHub lookup: python gh.py owner/repo issue|pr|commit N_or_sha"""

import random
import sys

import requests

from mcp_migration.config import github_tokens

repo, kind, ref = sys.argv[1:4]
t = random.choice(github_tokens())
h = {"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json"}
base = f"https://api.github.com/repos/{repo}"
if kind in ("issue", "pr"):
    d = requests.get(f"{base}/issues/{ref}", headers=h, timeout=30).json()
    print(
        d.get("title"),
        "| by",
        (d.get("user") or {}).get("login"),
        "| state",
        d.get("state"),
        "| created",
        d.get("created_at"),
    )
    print((d.get("body") or "")[:1500])
    if "pull_request" in d:
        p = requests.get(f"{base}/pulls/{ref}", headers=h, timeout=30).json()
        print("merged:", p.get("merged_at"), "| head:", (p.get("head") or {}).get("ref"))
elif kind == "commit":
    d = requests.get(f"{base}/commits/{ref}", headers=h, timeout=30).json()
    print(d.get("commit", {}).get("message", d)[:2000])
    for f in d.get("files", [])[:30]:
        print(" ", f["filename"], f["status"])
elif kind == "file":  # ref = sha:path
    sha, path = ref.split(":", 1)
    r = requests.get(
        f"{base}/contents/{path}?ref={sha}",
        headers={**h, "Accept": "application/vnd.github.raw"},
        timeout=30,
    )
    print(r.text[:4000])
