# Replication package: MCP server migrations across a breaking protocol revision

This package reproduces every number and figure in the paper from the data we
mined, and contains the pipeline that mined it. It covers MCP (Model Context
Protocol) servers on GitHub responding to the `2026-07-28` protocol revision and
the v2 SDKs: whether they migrate, defer or remove functionality (RQ1), whether
upgrading the SDK amounts to migrating the protocol (RQ2), how mechanical and
architectural changes differ (RQ3), and how long projects take to adopt each
release (technical lag).

## Contents

| Path | What it is |
|---|---|
| `src/mcp_migration/` | The pipeline: collection, mining, episode extraction, statistics, figures, and the labeling app |
| `tests/` | Unit tests, including a synthetic git repository |
| `config/queries.toml` | The GitHub search queries, with measured yields |
| `docs/codebook.md` | The manual labeling codebook |
| `data/mined.tar.gz` | Everything mined: metadata, signal commits, HEAD states, harvest outcomes (44 MB) |
| `data/search-hits.tar.gz` | Raw GitHub search hits, per query (7 MB) |
| `data/external/mcpcorpus_repos.txt` | Repository list derived from MCPCorpus, the cohort for technical lag |
| `data/labeling/sample.csv` | The stratified labeling sheet (520 episodes) |
| `data/processed/qualitative.json` | Coded developer text: pin commits and breakage issues, with evidence |
| `results/expected/` | The outputs the paper was written from |
| `scripts/reproduce.sh` | Regenerates everything and checks it against `results/expected/` |

`data/README.md` documents every data file and its fields.

## Reproducing the paper

Requires Python 3.11 or later. No GitHub access or token is needed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,paper]"
scripts/reproduce.sh
```

The script unpacks the data, rebuilds the episodes from the mined commits,
recomputes every statistic and figure, and compares them with
`results/expected/`. It finishes with:

```
reproduced: every statistic and every number in the paper matches
```

It takes a few minutes. Outputs go to `results/`:

- `results/summary.json`: every statistic, as JSON
- `results/numbers.tex`: every number the paper prints, as LaTeX macros
- `results/figures/*.pdf`: every data figure

Run the tests with `pytest`.

## Where each result comes from

All keys refer to `results/summary.json`.

| Result | Keys | Figure |
|---|---|---|
| Corpus construction (pipeline figure) | `n_search_queries`, `n_seed_repos`, `n_resolved_repos`, `n_clone_candidates`, `exclusion`, `harvest`, `n_mined_repos`, `n_signal_commits`, `n_episodes`, `n_sample_size` | |
| RQ1: migrate, defer or remove | `episode_kinds`, `n_pinned_repos`, `n_pinned_later_moved`, `qualitative` | `episodes-by-kind.pdf`, `kind-by-era.pdf` |
| RQ2: SDK upgrade versus protocol migration | `migration_end_states`, `head_eras` | `migration-end-states.pdf` |
| RQ3: mechanical versus architectural change | `deprecated_removed`, `replacements` | |
| Technical lag | `technical_lag`, `technical_lag_repos`; `technical_lag_all` shows the whole-dataset contrast | `technical-lag.pdf` |
| Revision uptake and authorship | `revisions_added`, `n_agent_episodes`, `n_bot_episodes` | `revision-adoption.pdf`, `author-kind-share.pdf` |
| Episode-boundary sensitivity | `gap_sensitivity` | |
| Size cap | `repo_size`, `exclusion` | |
| Detector precision and agreement | `labels` | |

**Precision and agreement.** These come from the two coders' sheets, in
`data/labeling/coderA/` and `data/labeling/coderB/`:

```bash
mcp-migration labels data/labeling/coderA/sample.csv data/labeling/coderB/sample.csv
```

The sample is stratified with 15 episodes per (kind × end state) cell. Precision
is weighted by each cell's share of the episodes, and the interval is Wilson's at
the matching effective sample size (`src/mcp_migration/report/labels.py`).

The sheets were coded in a local web app, `mcp-migration label --coder A`
(install with `pip install -e ".[label]"`). It edits a coder's own sheet in
place, accepts only the codebook's values, and hides the detector's labels
until the coder has judged the episode. `docs/codebook.md` describes it.

## Running the pipeline from scratch

The released data is a snapshot. Re-running collection queries GitHub as it is
today, so counts will differ: repositories are deleted and histories grow. The
stages, each of which reads the previous one's output:

```bash
export GITHUB_TOKENS=...                    # one or more tokens, comma-separated
mcp-migration search                        # GitHub search      -> data/raw/search/
mcp-migration seed data/external/* --include-search-hits   #     -> data/interim/repos.txt
mcp-migration metadata                      # GraphQL metadata   -> data/interim/repo_meta.jsonl
mcp-migration filter                        # inclusion criteria -> data/interim/repos_to_clone.txt
mcp-migration harvest --budget 1800         # clone, mine, delete -> data/interim/{commits,heads}/
mcp-migration episodes                      # -> data/processed/episodes.jsonl
mcp-migration stats --out results/summary.json --tex results/numbers.tex
```

`harvest` keeps one clone on disk per worker, so a full run needs about as much
disk as the largest repositories being mined at once, not the 1.5 TB of the
whole corpus. `--prefilter` lets `git log -G` pick the commits worth diffing.
It yields the same records and is faster on repositories with long histories.

## Citing

If you use this package, please cite the paper; `CITATION.cff` has the details,
and GitHub's "Cite this repository" button reads it.

## License

The code (everything outside `data/` and `results/`) is released under the MIT
License; see `LICENSE`. The data (`data/` and `results/`) is released under
Creative Commons Attribution 4.0 International (CC BY 4.0); see `data/LICENSE`.

The data describes public GitHub repositories. Commit messages, issue excerpts
and repository names remain the work of their authors. The repository list in
`data/external/` is derived from MCPCorpus, which we credit as its source.

## Notes

- **The snapshot:** collection ran in September 2026. Most repositories were
  mined on 15–16 September 2026, and the rest by 29 September.
- **Mining time budget:** a 30-minute budget per repository was introduced part
  way through the harvest. The repositories that exceeded it are listed, with
  reasons, in `data/interim/harvest_outcomes.jsonl` (inside `mined.tar.gz`).
- **Personal data:** no commit author names or e-mail addresses are stored, only
  whether an author is a bot, an AI agent (by trailer) or neither.
- **MCPCorpus:** we redistribute only the repository names derived from it, not
  the corpus itself.
