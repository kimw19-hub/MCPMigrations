# Data

Released under Creative Commons Attribution 4.0 International (CC BY 4.0); the
full legal text is in `LICENSE` in this directory. The code that produced it is
MIT-licensed (see `LICENSE` at the repository root).

`scripts/reproduce.sh` unpacks the two archives into this directory. The
unpacked files are git-ignored; the archives are the released copy.

## Files

| Path | Stage | Content |
|---|---|---|
| `external/mcpcorpus_repos.txt` | input | `owner/repo` names derived from MCPCorpus; seeds, and the technical-lag cohort |
| `raw/search/<query>.jsonl` | `search` | GitHub search hits, one file per query in `config/queries.toml` |
| `raw/search_hits.jsonl` | `search` | All queries merged |
| `interim/repos.txt` | `seed` | Deduplicated seed list |
| `interim/repo_meta.jsonl` | `metadata` | GitHub metadata per resolvable repository |
| `interim/repos_to_clone.txt` | `filter` | Repositories meeting the inclusion criteria |
| `interim/commits/<owner>__<repo>.jsonl` | `harvest` | Every commit since 2024-11-01 that adds or removes a signal |
| `interim/heads/<owner>__<repo>.json` | `harvest` | Signals and static era of the repository's HEAD |
| `interim/harvest_outcomes.jsonl` | `harvest` | Clone candidates not mined, and why |
| `processed/episodes.jsonl` | `episodes` | Signal commits grouped into episodes (rebuilt by `reproduce.sh`) |
| `processed/qualitative.json` | `qualitative` | Coded pin commits and breakage issues, with the text that fired each code |
| `labeling/round<N>/sample.csv` | `sample --round N` | One round's stratified labeling sheet; each coder fills a copy (`round<N>/coderA.csv`, `coderB.csv`). See `labeling/README.md` for the other files in a round |

`mined.tar.gz` holds the `interim/` files and `search-hits.tar.gz`
holds `raw/`. `processed/qualitative.json` is shipped rather than rebuilt,
because rebuilding it re-reads issues that may since have been edited or
deleted.

`ts-scans.tar.gz` unpacks to `ts/` in this directory, which is where
the protocol-support and deferral analysis (`report/measurement.py`) reads it. It holds a TypeScript
mining pass (`interim/`), HEAD scans (`head-scan/`) and application-code scans
of every v2 repository (`app-scan/`, with the earlier `app-scan-v1/`), and the
manual readings that correct and validate them: `audit-labels.json` and
`holdout-labels.json` (support calls read by hand), `legacy-only-audit.json`
(every negative TypeScript call), `feature-marker-check.json`,
`upgrade-claims-labels.json`, `pin-wording-check.json`,
`issue-relevance.json`, `sampling-vignette-labels.json` and
`history-validation.json`. The scans were run outside the committed pipeline,
so they are shipped rather than rebuilt; the manual readings cannot be rebuilt
at all.

## Records

**Signal commit** (`interim/commits/*.jsonl`, one per line)

| Field | Meaning |
|---|---|
| `repo`, `sha`, `parent_sha` | Repository, commit, and its first parent (`""` at a root commit) |
| `date` | Committer date, ISO 8601 UTC |
| `author_kind` | `bot`, `ai_agent_assisted` (by co-author trailer; a lower bound) or `human_or_unknown`. Names and e-mails are not stored |
| `is_merge` | Merge commits carry no diff and form no episode |
| `message` | First line of the commit message |
| `signal_files` | Files in which a signal changed |
| `added`, `removed` | Lines matching each signal, added and removed; `revision_literal:<date>` counts protocol revision strings |
| `lines_added`, `lines_deleted` | Commit churn |
| `migration_candidate` | The commit itself adds v2 signals and removes v1 ones |
| `pin_below_2_added` | The commit adds an `mcp<2` ceiling |

**HEAD state** (`interim/heads/*.json`): `repo`, `head_sha`, `signals` (counts
over every relevant file at HEAD), and `static_era`, one of `sdk_v2_modern_features`,
`sdk_v2_default`, `mixed_v1_v2`, `sdk_v1_pinned_below_2`, `sdk_v1`,
`standalone_fastmcp` or `no_official_sdk`.

**Episode** (`processed/episodes.jsonl`)

| Field | Meaning |
|---|---|
| `episode_id` | `owner/repo#n`, stable across rebuilds of the same commits |
| `kind` | `migration`, `v2_adoption`, `v1_removal`, `pin_below_2` or `revision_change` |
| `scope` | `sdk_major_only`, `protocol_only` or `sdk_and_protocol` |
| `start`, `end`, `duration_days`, `shas` | The commits grouped, at most 30 days apart |
| `revisions_added`, `revisions_removed` | Protocol revision strings introduced and retired |
| `deprecated_removed` | Deprecated features (sampling, roots, logging, subscriptions) removed |
| `replacements` | A removed capability and its successor added in the same episode |
| `head_static_era` | The repository's static era at HEAD |
| `author_kinds`, `lines_added`, `lines_deleted` | Aggregated from the commits |

**Repository metadata** (`interim/repo_meta.jsonl`): `repo`, `archived`, `fork`,
`empty`, `disk_usage_kb`, `stars`, `created_at`, `pushed_at`, `language`,
`default_branch`.

**Search hit** (`raw/search/*.jsonl`): `query`, `kind` (`commits` or `issues`),
`repo`, `id`, `title`, `url`, `date`.

**Harvest outcome** (`interim/harvest_outcomes.jsonl`): `repo`, `reason`
(`unavailable` or `timeout`) and `detail`.

The signal patterns themselves are in `src/mcp_migration/mine/signals.py`.
