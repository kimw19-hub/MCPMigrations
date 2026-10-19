# Round 2: prompt

Round 2 pre-labels `data/labeling/round2/sample.csv` (361 episodes, none from
round 1). The output goes to `data/labeling/round2/llm/coderLLM.csv`.
[`CHANGELOG.md`](CHANGELOG.md) explains every difference from round 1.

## Run

| | |
|---|---|
| Model | Claude Opus 5.5 (`claude-opus-5-5`) |
| Tool | Claude Code, VS Code extension |
| Sampling settings | Not exposed by Claude Code; unknown |
| Codebook | `docs/codebook.md`, including adjudication rules R1–R9, and `docs/coding-examples.md`, as committed with this file |
| Evidence | `scripts/llm_labeling/collect.py data/labeling/round2/sample.csv`, then `scripts/llm_labeling/dossier.py data/labeling/round2/sample.csv WORKDIR`, with no detector line |

## Prompt (used verbatim)

```text
You are pre-labeling migration episodes for an empirical study of how projects
moved between major versions of the official Model Context Protocol (MCP) SDK
and between MCP protocol revisions. Two human coders will verify every label
you write, and a blind subset will be compared with your labels to measure
your accuracy. Accuracy matters more than speed; say when you are unsure.

INPUTS
- docs/codebook.md: the definitions of every column and value, including the
  adjudication rules R1-R9. These rules are binding.
- docs/coding-examples.md: worked examples.
- WORKDIR/batch_NN.md: one evidence dossier per episode, 20 per file. A
  dossier shows the commits (author account, message, trailers, pull request,
  MCP-relevant diff lines annotated with the signal they matched), issues and
  PRs around the episode, the repository state after it, and later commits.
  It does not show what the detector concluded: judge the evidence, not a
  prior label.
- When a dossier is not enough, you may run
  scripts/llm_labeling/episode.py EPISODE_ID and
  scripts/llm_labeling/gh_lookup.py REPO commit|issue|pr|file REF.
  Record in notes anything you learned this way.

WHAT AN EPISODE KIND MEANS IN THIS ROUND
- migration: v2 SDK evidence arrives and v1 evidence leaves.
- v2_adoption: v2 SDK or 2026-07-28 protocol evidence arrives, nothing v1 leaves.
- pin_below_2: an `mcp ... <2` cap is added.
- v1_removal: v1 SDK code leaves and standalone FastMCP arrives.
- revision_change: one protocol revision literal is replaced by another.
An MCPError identifier alone is not evidence of v2.

FOR EACH EPISODE, IN THIS ORDER
1. detector_correct (yes|no). yes when the episode contains genuine activity
   about the official MCP SDK major version or the protocol revision a project
   speaks:
   - a v1 to v2 move;
   - v2 adoption, including greenfield v2;
   - a `<2` cap, whenever it was added, including before 2.0 was released;
   - a deliberate move off a v1 API that v2 removes (R2);
   - a change in the revision the project's own code speaks, custom
     implementations included (R1);
   - a move between mcp.server.fastmcp and standalone fastmcp;
   - new generations of official SDKs in other languages (C#, rmcp, go-sdk).
   no for:
   - minor bumps within a major, lockfile churn, formatting, moved lines;
   - deleted examples or tests;
   - forks replaying unrelated upstream history;
   - a project's own MCPError/McpError class;
   - revision dates in fixtures, docs, comments or model names;
   - greenfield code that merely starts on an old revision;
   - pins that existed before the episode.
   If no, fill only notes and stop.
2. end_state, for the repository after the episode's last commit:
   - modern_only only with explicit evidence that legacy support was removed;
   - reverted beats pinned_below_2 when v2 was adopted and then undone (R3);
   - a <2 cap beats compatibility code;
   - a fully custom implementation is no_official_sdk even when it serves
     both eras (R4);
   - dual_era is for code that runs on, or mixes, official SDK majors;
   - fastmcp 4 on mcp 2 is standalone_fastmcp;
   - a new generation of an official SDK in another language is
     sdk_v2_default.
3. trigger, the first that applies in this order:
   - user_issue: an issue or report by someone other than the maintainers
     that the change responds to. Third-party issues the change does not
     cite are not a trigger.
   - bot_pr_breakage: a Dependabot or Renovate PR bumping the SDK, or a fix
     or superseding migration after one.
   - downstream_change: an external change broke or forced it (a fresh
     install resolved to 2.0, CI went red, a dependency requires <2).
   - proactive: deliberate, with nothing broken, including bounds or
     upgrades added as policy (R5).
   - unknown: nothing says why.
4. author_kind_verified, judged on the commit that makes the migration change
   (R8):
   - ai_agent_assisted: agent trailers, agent author accounts, "Generated
     with" footers or session links on that commit, or agent trailers on
     other commits of the same PR;
   - bot: only when that change itself was made by a bot (R9);
   - human: only with positive evidence, meaning terse, informal or GitHub
     web-UI messages and no agent markers anywhere (R6);
   - otherwise leave it blank and say why in notes (R7).
5. deprecated: only if the episode changes how sampling, roots, logging or
   resource subscriptions are used, as feature:disposition pairs with
   disposition removed, replaced or kept. Ignore minified output and test
   fixtures.
6. notes: always one to three sentences citing the evidence (commit subject,
   file, trailer, issue number) for each non-obvious value.
7. confidence: high, medium or low, for the episode as a whole. low whenever
   the evidence is thin (commits unavailable, squashed snapshots) or a value
   rests on a judgement call.

CONSISTENCY
- Episodes made of the same commits (mirrors, forks) get the same labels.
  Say which episode they duplicate.
- The rules above are fixed for this round. Do not create precedents. If a
  case is not covered, choose the closest reading, set confidence to low,
  and start notes with "RULE GAP:" plus a one-line description.

OUTPUT
For each batch, write WORKDIR/codes/bNN.json: a JSON object mapping each
episode_id to {"detector_correct", "end_state", "trigger",
"author_kind_verified", "deprecated", "notes", "confidence"}. Use empty
strings for columns that do not apply. Then run
scripts/llm_labeling/record.py WORKDIR/codes/bNN.json and fix any row it
rejects.
```

## After pre-labeling

- **The LLM sheet.** `scripts/llm_labeling/assemble.py` writes the validated
  rows into `data/labeling/round2/llm/coderLLM.csv`, keeping `confidence` and
  `notes`.
- **The coders' sheets.**
  - Pre-labelled rows copy the LLM's values and notes.
  - Blind rows (`data/labeling/round2/blind_ids.txt`) are left empty.
  - The sheets carry only the 176 episodes in
    `data/labeling/round2/subset_ids.txt`, drawn after pre-labeling and before
    any coding; the LLM pre-labeled all 361.
  - Coders may sort by `confidence` to review low-confidence rows first, but
    they verify every row.
