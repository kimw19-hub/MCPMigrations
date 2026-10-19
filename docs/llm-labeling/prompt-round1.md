# Round 1: instruction, inputs and working rules

Round 1 pre-labeled the 520 episodes in `data/labeling/round1/sample.csv`. The
output is `data/labeling/round1/llm/coderLLM.csv`. Coders A and B then verified it
(`round1/coderA.csv`, `round1/coderB.csv`). The instruction below is quoted as
given; the "CoderLLM" folder it names is now `round1/llm/`. This file records what the LLM was
given and the rules it worked by, reconstructed from the session's own files.

## Run

| | |
|---|---|
| Model | Claude Opus 5.5 (`claude-opus-5-5`) |
| Tool | Claude Code, VS Code extension, one agentic session |
| Dates | 2026-10-10 to 2026-10-13. The LLM sheet was written 2026-10-13 and committed in `7af891a`. |
| Sampling settings | Not exposed by Claude Code; unknown |
| Codebook | `docs/codebook.md` and `docs/coding-examples.md` as of commit `be0c12d` |
| Evidence | GitHub API responses cached in `data/interim/coder-llm-cache/` |

## The instruction

The task was given in one chat message:

> Act as a coder given the codebook and coding example and code the sample
> data. Try to be as accurate as possible. Put the results in a folder
> "CoderLLM" inside the labeling folder

Asked how to gather evidence, the researcher chose to use the local clones and
a GitHub token. The LLM had already read the codebook and the coding examples
earlier in the session.

There was no separate system prompt beyond Claude Code's own. The operational
"prompt" is therefore the instruction above, the two documents, and the
working rules below, which the LLM wrote for itself while coding.

## Inputs per episode

For every episode, `scripts/llm_labeling/collect.py` fetched:

- the commits, with their messages and diffs;
- the pull requests the commits arrived in;
- issues and PRs from 14 days before to 14 days after the episode;
- the manifest and changed files at the episode's last commit.

`scripts/llm_labeling/dossier.py --with-detector` rendered these into batch
files of 20 episodes (`batch_NN.md`). Each dossier shows:

- every commit: author account, git name and email domain, message and
  trailers (agent trailers flagged), PR title and body, and the MCP-relevant
  diff lines, each annotated with the detector signal it matched;
- issues and PRs in the window whose titles contain relevant words;
- the repository state after the episode (dependency and import lines);
- signal commits after the episode;
- a last line `DETECTOR (read after judging)` with the detector's kind, era,
  scope and revisions.

For long or unclear episodes the LLM also used `scripts/llm_labeling/episode.py`
to list every mined commit, and `scripts/llm_labeling/gh_lookup.py` for single
commits, issues, PRs and files.

## Output and validation

Per batch, the LLM wrote a JSON object mapping each `episode_id` to the
codebook columns, with `notes` always filled.
`scripts/llm_labeling/record.py` checked every row with the labeling app's
validator and appended it to a running file. The 520 rows were then written
into the sheet format.

After assembly, two trigger values were corrected from `proactive` to
`unknown`, because the notes showed no stated reason for the bound:

- `letuhao/lore-weave#0`
- `bagofwords1/bagofwords#2`

## Working rules (final version, verbatim)

The LLM consolidated its rules into a file after the first batches and
applied them to every batch after that. Where the codebook left a case open, a
precedent episode was named in the rule (gleipnir, tylluan, gram). This is the
file's last version:

```markdown
# CoderLLM decision rules (apply identically to every batch)

## detector_correct
- yes: real activity about the official MCP SDK major or protocol revisions:
  v1->v2 move, v2 adoption (incl. greenfield v2), genuine v1 removal (deliberate
  move off a v1 API that v2 removes counts), a `<2` pin (deferral), or a change
  to the protocol version the project's own code speaks/negotiates (custom
  implementations count; other-language official SDKs count, e.g. C#, rmcp, go-sdk).
  Long mixed episodes: yes if they contain such genuine activity; note what it is
  and any kind mismatch (e.g. MCPError being the project's own class).
- no: only v1 minor bumps / line edits / lockfile churn; MCPError/McpError
  that is the project's own class; revision literals that are test fixtures,
  model/blog dates, unrelated; feature work on v1 citing spec pages; accidental
  wipes restored; forks importing upstream non-migration commits.
- Mirrors/forks with upstream migration commits: code the content (yes), note mirror.

## end_state (state after the episode's last commit)
- dual_era: code runs on both SDK majors (try/except imports) unless pinned <2,
  OR serves both protocol eras via a compat layer on v1 SDK, OR mixes v1 and v2
  packages, OR custom impl adds 2026-07-28 while keeping legacy (tylluan, gram).
  (gleipnir/felag custom bilingual = no_official_sdk; tylluan/gram mixed = dual_era.)
- pinned_below_2: dependency capped <2 (pin dominates compat code).
- sdk_v2_default: on v2 SDK (incl. createMcpHandler serving modern+legacy).
  Other-language official SDK new generation (C# 2.0, rmcp 3.x, go-sdk 2026 rev) -> sdk_v2_default.
- modern_only: only with explicit evidence legacy removed ("hard cut", "clean-break").
- still_v1: v1 SDK, no cap. (pre-3 rmcp -> still_v1)
- no_official_sdk: custom implementation / no SDK.
- reverted: migration attempted then undone within the episode (e.g. v2 then back to <2).
- abandoned: MCP server/package removed after adoption.

## trigger (codebook definitions; order user_issue > bot_pr_breakage > downstream_change > proactive)
- user_issue: issue/report by someone else prompted it (maintainer's own issue != user).
- bot_pr_breakage: Dependabot/Renovate PR bumping the SDK prompted the work
  (the PR itself, a fix after it, or a manual migration that "supersedes" it).
- downstream_change: external change forced it, no issue/bot PR: fresh install
  resolved to 2.0 and broke, CI red, dependency (e.g. livekit) requires <2.
- proactive: deliberate move with nothing broken (planned ADR/epic, "upgrade to v2",
  greenfield choosing v2, adding upper bounds as policy).
- unknown: nothing says why.

## author_kind_verified (code by the commits doing the migration-related change; PR-level evidence counts)
- ai_agent_assisted: agent trailers (Claude/Copilot/Cursor/Codex/factory-droid/qwen),
  agent author accounts (claude, cursoragent, copilot-swe-agent), "Generated with",
  Claude-Session links on the migration commit, agent trailers on commits in the same PR.
- bot: only bot-authored work with no agent markers.
- human: positive evidence only: personal account + terse/informal or web-UI
  default messages + no agent markers anywhere.
- blank + note "ambiguous": personal account, no markers, but polished/long or
  templated messages; or mixed authorship without markers on the migration commit.

## deprecated
Only if the episode really changes sampling/roots/logging/subscriptions usage
(not minified build output, not test fixtures). removed / replaced / kept.

## notes
Always: one or two sentences of evidence. Required for no, blanks, close calls.
```

The file also had a workflow line naming the helper scripts by their scratch
names. Those scripts are now `scripts/llm_labeling/`.

## Known weaknesses

These were found while verifying and adjudicating round 1, and they motivate
the changes in [`CHANGELOG.md`](CHANGELOG.md).

1. **The detector's verdict was in the dossier.** Every dossier ended with the
   detector's labels, marked "read after judging". An LLM reading the whole
   dossier cannot unsee them, so the LLM coder was not blind to the output it
   was judging.
2. **The rules grew during coding.** Precedents named after specific episodes
   were added as cases came up, so early and late batches were not coded under
   identical rules.
3. **No blind subset.** Every row the humans saw was pre-labelled. Round 1
   cannot separate human agreement from shared acceptance of the suggestions.
4. **No confidence signal.** The output had no field saying which labels the
   LLM was unsure of, so verifiers could not prioritise.
5. **Open cases.** Adjudication had to settle disagreements over cases the
   rules left open. These became R1–R9 in the codebook:
   - custom implementations;
   - `reverted` versus `pinned_below_2`;
   - when the author counts as `human`;
   - mixed authorship;
   - policy bounds.

## Not used

Commit `da55806` added an earlier, exploratory LLM-assisted sheet
(`coderA/coderLLM.csv`, `coderB/coderLLM.csv`). It agrees with the round-1 LLM
sheet on about 85% of detector verdicts. It was removed in `7af891a` and is not
the sheet the coders verified.
