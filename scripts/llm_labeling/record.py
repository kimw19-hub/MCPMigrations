"""Validate one batch of LLM codes and append them to labels.jsonl beside it.

Usage: python scripts/llm_labeling/record.py WORKDIR/codes/bNN.json
The batch is a JSON object mapping episode_id to the codebook columns, plus an
optional ``confidence`` (high, medium or low; required from round 2). Every row
goes through the same validation as the labeling app.
"""

import json
import sys
from pathlib import Path

from mcp_migration.labeling.sheet import status, validate

CONFIDENCE = ("high", "medium", "low")
OUT = Path(sys.argv[1]).parent / "labels.jsonl"
data = json.loads(Path(sys.argv[1]).read_text())
done = (
    {json.loads(line)["episode_id"] for line in OUT.read_text().splitlines()}
    if OUT.exists()
    else set()
)
with OUT.open("a") as fh:
    for eid, lab in data.items():
        lab = dict(lab)
        confidence = str(lab.pop("confidence", "")).strip().lower()
        if confidence and confidence not in CONFIDENCE:
            sys.exit(f"{eid}: confidence {confidence!r} is not one of {', '.join(CONFIDENCE)}")
        clean = validate(
            {"deprecated": "", "end_state": "", "trigger": "", "author_kind_verified": "", **lab}
        )
        st = status(clean)
        if st != "done":
            print("NOT DONE:", eid, st, clean)
        if eid in done:
            print("DUPLICATE (skipped):", eid)
            continue
        row = {"episode_id": eid, **clean}
        if confidence:
            row["confidence"] = confidence
        fh.write(json.dumps(row) + "\n")
print("recorded", len(data), "; total", len(OUT.read_text().splitlines()))
