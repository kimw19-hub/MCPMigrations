"""Draw a smaller random subset of a round's sample for the coders.

Usage: python scripts/llm_labeling/subset.py ROUND_DIR [--per-kind 45] [--seed 2026]

Writes ROUND_DIR/subset_ids.txt. The round's full draw (sample.csv) is kept;
the subset only decides which rows the coders' sheets carry.

The subset is random within each (kind, head era) stratum, in proportion to the
draw, with at least one episode per stratum and every blind episode kept. It
depends on nothing but the draw, the blind list and the seed: never on the LLM's
labels or confidence, which would make precision depend on what the model
already believed. Run it before anyone codes, and do not redraw it afterwards.
"""

import argparse
import random
from collections import defaultdict
from pathlib import Path

from mcp_migration.report.labels import read_sheet

parser = argparse.ArgumentParser()
parser.add_argument("round_dir", type=Path)
parser.add_argument("--per-kind", type=int, default=45)
parser.add_argument("--seed", type=int, default=2026)
args = parser.parse_args()

rows = read_sheet(args.round_dir / "sample.csv")
blind_file = args.round_dir / "blind_ids.txt"
blind = set(blind_file.read_text().split()) if blind_file.exists() else set()
out = args.round_dir / "subset_ids.txt"
if out.exists():
    raise SystemExit(f"{out} exists; the subset is fixed once drawn")

strata: dict[tuple[str, str], list[str]] = defaultdict(list)
for r in rows:
    strata[(r["kind"], r["head_static_era"])].append(r["episode_id"])

rng = random.Random(args.seed)
keep = set(blind)
for kind in sorted({k for k, _ in strata}):
    cells = {s: ids for s, ids in sorted(strata.items()) if s[0] == kind}
    total = sum(len(ids) for ids in cells.values())
    for ids in cells.values():
        quota = max(1, round(args.per_kind * len(ids) / total))
        have = sum(1 for i in ids if i in keep)
        rest = sorted(i for i in ids if i not in keep)
        rng.shuffle(rest)
        keep.update(rest[: max(0, quota - have)])

ordered = [r["episode_id"] for r in rows if r["episode_id"] in keep]
out.write_text("\n".join(ordered) + "\n")
print(f"kept {len(ordered)} of {len(rows)} episodes ({len(blind)} blind) in {out}")
