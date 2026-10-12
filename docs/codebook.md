# Labeling codebook (unit: migration episode)

Two coders label `data/labeling/sample.csv` independently, each in their own
copy under `data/labeling/coderA/` and `data/labeling/coderB/`.
`mcp-migration labels` then computes detector
precision (overall and per episode kind) and Cohen's kappa per column, and
`stats` carries those figures into the manuscript. Resolve disagreements by
discussion *after* recording the kappa, so the agreement figure describes
independent coding rather than the consensus that follows it.

There are five judgements per episode plus free-text notes.

## How to code, step by step

### Before you start

1. **Use your own copy.** Coder A codes `data/labeling/coderA/sample.csv` and
   coder B `data/labeling/coderB/sample.csv`; both start as copies of the blank
   sheet. Never edit `data/labeling/sample.csv` itself; it is the shared draw
   both copies come from, and `labels` compares the two.
2. **Agree not to discuss episodes while coding.** Cohen's kappa measures
   independent agreement. Talking through a hard case as it comes up turns the
   agreement figure into a measure of the conversation. Save it for step 8.
3. **Do a pilot of 20 rows together first.** Code the first 20 rows
   independently, compare, and talk through every disagreement until the
   codebook reads the same way to both of you. Then **discard those 20 codings
   and redo them** with the rest. A pilot costs an hour and is the single
   biggest thing you can do for the final kappa.
4. **Code in the labeling app** (recommended). Run `scripts/reproduce.sh` once
   so the mined data is unpacked, then start your own copy:

   ```
   pip install -e ".[label]"
   export GITHUB_TOKEN=...        # optional; without one GitHub allows 60 lookups an hour
   mcp-migration label --coder A  # coder B: --coder B
   ```

   It opens in your browser and edits your own sheet directly, saving every
   change as you make it. It offers only the values this codebook allows,
   clears the other columns when you answer `no`, and hides the detector's
   labels until you have answered `detector_correct` (step 6). For each commit,
   "GitHub details" loads the full message and its trailers, the pull request it
   arrived in, and the diff with the detector's matches highlighted. Press `?`
   for keyboard shortcuts. Do not have the sheet open in a spreadsheet while the
   app is running, or whichever saves last wins.

   **If you use a spreadsheet instead**, it may reformat the `start` and `end`
   timestamps. That is harmless: `labels` reads only `episode_id` and the
   columns you fill. Save as CSV, not xlsx.

### Per episode

5. **Open it.** The labeling app shows each episode in full. Without it, one
   command prints everything:

   ```
   mcp-migration show 'owner/repo#0'
   ```

   It gives the window, the commit list with messages and the signals each one
   moved, and a GitHub link — a single commit, or a compare view across the
   whole range. Follow the link when the messages are not enough.
6. **Form your own view before reading the detector's.** `show` prints
   `kind` and `era` because you need to know which episode you are looking at,
   but both are the output you are judging. Read the diff first.
7. **Answer in order.** `detector_correct` first. If `no`, write `notes` and
   move to the next row — leave the rest empty. If `yes`, fill `end_state`,
   `trigger` and `author_kind_verified`, then `deprecated` only if one of the
   four features is involved, which is rare.

### When you are both done

8. **Run the comparison before you reconcile:**

   ```
   mcp-migration labels data/labeling/coderA/sample.csv data/labeling/coderB/sample.csv
   ```

   This writes precision (weighted by stratum size), per-kind precision, and
   kappa per column. Record those numbers. They describe independent coding, and once you have discussed
   the disagreements you cannot recover them.
9. **Then reconcile.** Work through the disagreements, agree a value for each,
   and keep a reconciled sheet if you want one for analysis. The manuscript
   reports the kappa from step 8, not from the reconciled sheet.
10. **Refresh the statistics:** `mcp-migration stats`, which carries precision
    and kappa into the outputs.

## The unit

An **episode** is the set of commits in one repository that touch an MCP version
signal and fall within 30 days of each other. It is not a single commit: the
`n_commits` column says how many it spans, and some run to hundreds. Code the
episode as a whole; `mcp-migration show` lists every commit and links the range.

## Columns you fill

Leave a cell empty only when the evidence is genuinely unavailable, and say why
in `notes`. Empty cells are skipped pairwise when kappa is computed, so a blank
costs less than a guess.

**If `detector_correct` is `no`, fill only `detector_correct` and `notes`, and
leave the rest empty.** The other dimensions describe a migration episode, and
answering them for something that is not one puts noise into every column.

| Column | Allowed values | Guidance |
|---|---|---|
| `detector_correct` | `yes`, `no` | Is this really a migration-related episode? Drives detector precision. |
| `end_state` | `modern_only`, `dual_era`, `sdk_v2_default`, `pinned_below_2`, `still_v1`, `standalone_fastmcp`, `no_official_sdk`, `reverted`, `abandoned` | The repository's state **after** the episode. `modern_only` needs evidence of legacy removal or a runtime probe; without it prefer `sdk_v2_default`. The first seven mirror `head_static_era`, so this column also measures how often the static detector is right. |
| `trigger` | `proactive`, `user_issue`, `bot_pr_breakage`, `downstream_change`, `unknown` | Why the work happened. Check linked issues and PRs and the first commit message; `unknown` is a legitimate answer and is preferable to a guess. |
| `author_kind_verified` | `human`, `bot`, `ai_agent_assisted` | Check the PR body and commit trailers. Differs from the detector's `human_or_unknown`: code `human` only with positive evidence of a human author, and note the ambiguity otherwise. |
| `deprecated` | blank, or `feature:disposition` pairs | **Usually blank.** Fill only when the episode touches one of the four deprecated features. See below. |
| `notes` | free text | Required when a cell is left empty, when a value was a close call, and for anything the columns above cannot carry. Excluded from the kappa computation. |

### The `trigger` column

What prompted the work, judged from linked issues and PRs, the commit messages,
and issues and PRs opened around the episode.

| value | definition |
|---|---|
| `proactive` | The project moved on its own initiative, with nothing broken: adopting the v2 SDK or a new revision on its own schedule. |
| `user_issue` | An issue or report by someone else (a user or contributor) prompted the work; link it in `notes`. |
| `bot_pr_breakage` | A dependency bot's PR (Dependabot, Renovate) bumping the SDK prompted the work: the episode is that PR, or the fix after it broke the build. |
| `downstream_change` | A change outside the project forced the work, with no issue or bot PR behind it: typically the SDK release breaking a fresh install, found by the maintainers themselves. |
| `unknown` | The evidence does not say why. Preferable to a guess. |

If more than one applies, take the first that fits in this order: `user_issue`,
`bot_pr_breakage`, `downstream_change`, `proactive`.

### The `deprecated` column

Leave it blank unless the episode touches sampling, roots, protocol-level
logging, or resource subscriptions, which few episodes do. When it does,
write semicolon-separated `feature:disposition` pairs, for example
`sampling:replaced;roots:removed`.

Features, and what the detector matched on:

| feature | what it is | patterns |
|---|---|---|
| `sampling` | the server borrowing the client's model | `sampling/createMessage`, `create_message(`, `createMessage(` |
| `roots` | the client exposing filesystem roots | `roots/list`, `list_roots(`, `listRoots(` |
| `logging` | protocol-level logging, not the language's own logger | `logging/setLevel`, `send_log_message(`, `sendLoggingMessage(` |
| `subscriptions` | clients subscribing to resource changes | `resources/subscribe`, `subscribe_resource` |

The method-name patterns also match unrelated LLM libraries that use the same
names (`create_message` especially), so check that the call is MCP's before
answering `detector_correct`.

Dispositions:

- `removed` — the calls are gone and nothing took their place.
- `replaced` — the calls are gone and something else now does the job. Say what
  in `notes`, e.g. restructured through `InputRequiredResult`, moved to the
  language's own logger, or delegated to an external model API.
- `kept` — the calls are still there, typically alongside a
  `MCPDeprecationWarning` or a caught `NoBackChannelError`.

`removed` and `replaced` are the pair coders most often split on. The test is
whether the server still performs the behaviour by other means.

## Columns already filled in

The sheet arrives with eight columns populated. They are context, not answers.

| Column | Meaning |
|---|---|
| `episode_id` | Identifier; the key into `episodes.jsonl`. |
| `repo` | `owner/name` on GitHub. |
| `kind` | **The detector's own classification.** |
| `start`, `end` | First and last commit timestamps. |
| `n_commits` | How many commits the episode spans. |
| `head_static_era` | **The detector's own guess** at the repository's state at HEAD. |
| `first_commit_url` | Link to the first commit. |

`kind` and `head_static_era` are in the sheet because the sample is stratified
over them, not as a starting hypothesis (step 6).

---

Keep this file and `CODEBOOK_COLUMNS` in `src/mcp_migration/report/sampling.py`
in sync; `tests/report/test_labels.py` enforces it.
