"""Write a round's validated LLM codes into its sheets.

Usage: python scripts/llm_labeling/assemble.py ROUND_DIR WORKDIR/codes/labels.jsonl

Writes ROUND_DIR/llm/coderLLM.csv (every episode, with notes and confidence),
and ROUND_DIR/coderA.csv and coderB.csv. The coders' sheets copy the LLM's
values for pre-labelled rows and leave the rows in ROUND_DIR/blind_ids.txt
empty, so those are coded without a suggestion. If ROUND_DIR/subset_ids.txt
exists (scripts/llm_labeling/subset.py), the coders' sheets carry only those
episodes; the LLM sheet keeps every episode of the draw.
"""

import csv
import json
import sys
from pathlib import Path

from mcp_migration.report.labels import invalid_rows
from mcp_migration.report.sampling import CODEBOOK_COLUMNS

round_dir, labels = Path(sys.argv[1]), Path(sys.argv[2])
coded = {}
for line in labels.read_text().splitlines():
    if line.strip():
        rec = json.loads(line)
        coded[rec.pop("episode_id")] = rec
with (round_dir / "sample.csv").open(encoding="utf-8-sig") as fh:
    rows = list(csv.DictReader(fh))
missing = [r["episode_id"] for r in rows if r["episode_id"] not in coded]
if missing:
    sys.exit(f"{len(missing)} episodes not coded yet, e.g. {missing[:3]}")
blind_file = round_dir / "blind_ids.txt"
blind = set(blind_file.read_text().split()) if blind_file.exists() else set()
subset_file = round_dir / "subset_ids.txt"
subset = set(subset_file.read_text().split()) if subset_file.exists() else None
if subset is not None and not blind <= subset:
    sys.exit("every blind episode must be in the subset")

fields = list(rows[0].keys())
llm_rows = [
    {
        **r,
        **{c: coded[r["episode_id"]].get(c, "") for c in CODEBOOK_COLUMNS},
        "confidence": coded[r["episode_id"]].get("confidence", ""),
    }
    for r in rows
]
problems = invalid_rows(llm_rows)
if problems:
    sys.exit("\n".join(problems))


def write(path: Path, out_rows: list[dict], extra: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*fields, *extra])
        writer.writeheader()
        writer.writerows(out_rows)


write(round_dir / "llm" / "coderLLM.csv", llm_rows, ("confidence",))
coder_rows = [
    r
    if r["episode_id"] not in blind
    else {**r, **{c: "" for c in CODEBOOK_COLUMNS}, "confidence": ""}
    for r in llm_rows
    if subset is None or r["episode_id"] in subset
]
for coder in ("A", "B"):
    target = round_dir / f"coder{coder}.csv"
    if target.exists() and any(
        row.get("detector_correct") for row in csv.DictReader(target.open(encoding="utf-8-sig"))
    ):
        sys.exit(f"{target} already has coded rows; not overwriting")
    write(target, coder_rows, ("confidence",))
print(
    f"wrote {len(rows)} episodes to the LLM sheet and {len(coder_rows)} to each coder's sheet;"
    f" {len(blind)} blind rows left empty"
)
