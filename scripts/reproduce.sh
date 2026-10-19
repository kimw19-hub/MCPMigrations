#!/usr/bin/env bash
# Regenerate every statistic and figure from the released data, then
# check the result against results/expected/. No network access is needed:
# the GitHub collection and mining stages are already done, and their outputs
# are what data/*.tar.gz contain.
set -euo pipefail
cd "$(dirname "$0")/.."

# Unpack once. tar keeps each file's modification time, which matters: the
# technical-lag analysis censors at the earliest date a repository was mined,
# and reads that date from data/interim/heads.
for archive in data/mined.tar.gz data/search-hits.tar.gz data/ts-scans.tar.gz; do
  marker="data/.unpacked-$(basename "$archive" .tar.gz)"
  if [ ! -e "$marker" ]; then
    echo "unpacking $archive"
    tar -xzf "$archive"
    touch "$marker"
  fi
done

mkdir -p results
mcp-migration episodes                                   # -> data/processed/episodes.jsonl
mcp-migration stats --out results/summary.json --tex results/numbers.tex
mcp-migration figures --out-dir results/figures --summary results/summary.json  # needs the [paper] extra
python scripts/verify.py
