# Round-1 LLM pre-labels

`coderLLM.csv` holds the round-1 pre-labels: the 520 episodes of
`data/labeling/round1/sample.csv` labeled by an LLM (Claude Opus 5.5 in Claude Code,
2026-10-10 to 13) under the codebook. **Coders A and B then verified every
row** (`round1/coderA.csv`, `round1/coderB.csv`), and their disagreements were
adjudicated (`round1/adjudicated.csv`). Every label used in the analysis has
been checked by both coders.

How the pre-labels were made is documented in
[`docs/llm-labeling/prompt-round1.md`](../../../docs/llm-labeling/prompt-round1.md).
That includes the instruction, the evidence dossiers, the working rules
verbatim, and the known weaknesses that round 2 fixes. Agreement under assisted
labeling is reported as described in
[`docs/llm-labeling/README.md`](../../../docs/llm-labeling/README.md): the share
of pre-label cells each coder kept, verifier κ, and the LLM's agreement with
the adjudicated labels.

| | Coder A | Coder B |
|---|---|---|
| Cells changed (of 2,600) | 42 (1.6%) | 28 (1.1%) |
| Episodes with any change | 33 | 28 |
| Detector verdicts changed | 3 | 0 |

## Method

- **Evidence.** One dossier per episode, built from GitHub API responses
  (`scripts/llm_labeling/collect.py`, `dossier.py --with-detector`). For every
  commit it gives the message and trailers, the author account, git name and
  email domain, the pull request, and the SDK- and protocol-relevant diff
  lines. It also lists issues and PRs around the episode and the repository
  state after it. When a dossier was not conclusive, the LLM looked up commits,
  issues, PRs and files directly.
- **Detector verdict.** Each dossier ended with the detector's verdict, marked
  "read after judging". Because the LLM read whole dossiers, it was not blind to
  it. Round 2 leaves that line out.
- **Process.** Episodes were coded in 26 batches of 20. Every row was checked
  with the labeling app's validator, and all 520 are complete. Episodes made of
  the same commits (mirrors, forks, vendored copies) got the same codes. After
  assembly, two triggers were corrected to `unknown`.

## Decision rules

### `detector_correct`

- **yes**: real activity on the official MCP SDK major version or on protocol
  revisions. This includes:
  - v1→v2 moves;
  - v2 adoption, including greenfield projects that start on v2;
  - deliberately moving off v1 APIs;
  - a `<2` pin added as a deferral;
  - a change to the protocol revision that a custom implementation speaks;
  - official SDKs in other languages (C#, rmcp, go-sdk);
  - fastmcp major moves that cross SDK eras.
- **no**:
  - v1/v2 minor bumps or lockfile churn;
  - `MCPError`/`McpError` when it is the project's own class;
  - revision-like dates that are test fixtures or unrelated literals;
  - feature work that cites spec pages but changes no revision;
  - pins that already existed before the episode.

### `end_state`

- A `<2` cap wins over compat code: such episodes are `pinned_below_2`.
- `dual_era`:
  - code that runs on both SDK majors;
  - a compat layer on v1 that serves both protocol eras;
  - custom code mixed with an official SDK and serving both eras.
- Fully custom implementations, including bilingual ones, and third-party SDKs
  are `no_official_sdk`.
- fastmcp 4 on mcp 2 is `standalone_fastmcp`.
- New-generation official SDKs in other languages are `sdk_v2_default`.
- `modern_only` requires explicit evidence that legacy support was removed.

### `trigger`

- When more than one applies, the first in this order wins: user_issue >
  bot_pr_breakage > downstream_change > proactive.
- An issue the maintainer opened themselves is not `user_issue`.
- `unknown` means nothing in the commits, issues or PRs says why.

### `author_kind_verified`

- Judged from the commits that make the migration change. Evidence at PR level
  counts.
- `ai_agent_assisted`:
  - agent co-author trailers;
  - agent accounts;
  - "Generated with" footers or session links.
- `human`: only with positive evidence, meaning a personal account,
  terse or web-UI default messages, and no agent markers anywhere.
- Left blank, with a note, when authorship is ambiguous. For example, a
  personal account with no markers but polished, template-like messages.

### `deprecated`

- Coded only when the episode really changes how sampling, roots, logging or
  subscriptions are used.
- Changes that appear only in minified output or test fixtures do not count.

## Distribution

| column | counts |
|---|---|
| detector_correct | yes 272 · no 248 |
| end_state | pinned_below_2 99 · sdk_v2_default 70 · no_official_sdk 33 · standalone_fastmcp 27 · dual_era 25 · still_v1 8 · reverted 4 · abandoned 3 · modern_only 3 |
| trigger | proactive 162 · downstream_change 60 · unknown 36 · bot_pr_breakage 12 · user_issue 2 |
| author_kind_verified | ai_agent_assisted 129 · human 21 · blank 122 (among yes rows) |
| deprecated | 10 rows |

Detector precision computed from these pre-labels alone, before verification. The study uses the adjudicated figures instead:

| kind | precision |
|---|---|
| pin_below_2 | 0.917 |
| migration | 0.848 |
| v2_adoption | 0.694 |
| revision_change | 0.150 |
| v1_removal | 0.037 |

`mcp-migration labels` also reports an overall precision of 0.270. That figure
is weighted by stratum size, so the large `revision_change` and `v1_removal`
strata pull it down.

## Caveats

- For a few episodes the commits could no longer be fetched. These were coded
  from commit subjects and repo state, and their notes say so.
- Author codes are conservative. "human" is used only with positive
  evidence, which is why many yes rows are blank.
