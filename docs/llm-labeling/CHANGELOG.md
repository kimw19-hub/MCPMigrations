# Prompt changelog

Each entry gives what changed, the evidence from the earlier round that called
for it, and where the change lives. Changes to the detector itself are
included where they change what a prompt has to explain.

## Round 1 → round 2

### Prompt and inputs

| # | Change | Why (evidence from round 1) | Where |
|---|---|---|---|
| P1 | The detector's verdict is removed from the dossiers. | Round-1 dossiers ended with `DETECTOR (read after judging)`. An LLM reading the whole dossier sees it before deciding, so it was not blind to the output it judged. | `scripts/llm_labeling/dossier.py` (detector line only with `--with-detector`) |
| P2 | One fixed prompt replaces a one-line instruction plus rules written while coding. | In round 1 the rules and their precedents (gleipnir, tylluan, gram) grew during coding, so early and late batches did not follow identical rules. | `prompt-round2.md` |
| P3 | Adjudication rules R1–R9 are binding in the prompt. | Disagreements in 38 episodes all fell into cases the round-1 rules left open. Settling them produced R1–R9. | `docs/codebook.md`, "Adjudication rules" |
| P4 | Any `<2` cap is `yes`, including caps added before 2.0 was released. | The coders first disagreed on 36 pin episodes, 33 of them with a cap added before the 2.0 release. On review both coders accepted all 36, so the rule is now explicit. | prompt step 1 |
| P5 | `reverted` takes precedence over `pinned_below_2` (R3). | `aiss2_team6#1` and `sonar-voice-agent#0`: v2 adopted, then undone with a `<2` cap. The coders split between the two codes. | prompt step 2 |
| P6 | Fully custom code is `no_official_sdk` even when it serves both protocol eras (R4). | `suankan/pocket-advisor#0` was coded `dual_era` by one coder and `no_official_sdk` by the other. | prompt step 2 |
| P7 | Author is judged on the migration commit. `bot` only if that change is bot-made. Otherwise blank unless there is positive evidence of a human (R6–R9). | Author was the least reliable column: verifier κ 0.80, mostly blank vs `human`, plus cases where Dependabot bumped earlier in the episode or only other commits had agent markers. | prompt step 4 |
| P8 | Bounds or upgrades added as policy are `proactive` (R5). Third-party issues the change doesn't cite are not `user_issue`. | Three trigger splits (`unknown` vs `proactive`) on hardening commits, and `golf-mcp/golf#0`, where open conformance issues were not cited by the change. | prompt step 3 |
| P9 | A `confidence` field (high/medium/low) is added, with low required when evidence is thin. | Round 1 gave verifiers no signal about which labels were uncertain. Ahmed et al. (MSR 2025) show model confidence can identify where LLM labels are safe to rely on. | prompt step 7; `record.py` |
| P10 | "RULE GAP:" notes replace new precedents. | In round 1, uncovered cases produced new precedents mid-round (see P2). Flagging them leaves the decision to the humans. | prompt "Consistency" |
| P11 | The prompt defines what each episode kind means in this round. | The detector rules changed after round 1 (below), so the kinds no longer mean what they did. | prompt "What an episode kind means" |

### Protocol

| # | Change | Why | Where |
|---|---|---|---|
| R1 | Blind subset: about 60 episodes are coded by both coders without pre-labels. | Round 1 had none. Verifiers of the same suggestions agree largely because they accept them (round-1 verifier κ 0.99; coder B changed no detector verdict), and annotators are known to adopt LLM suggestions (Schroeder et al., 2025). | `data/labeling/round2/blind_ids.txt`; `labels --blind` |
| R2 | The report separates the share of pre-labels kept, verifier κ, blind κ and the LLM's accuracy. | κ between assisted coders is not independent reliability. The SE LLM guidelines ask for validation against human annotators, reported per round. | `labels --prelabels`; macros in `numbers.tex` |
| R3 | The sample excludes every round-1 episode. | Round 1 was used to redesign the detector rules, so it cannot also measure them. | `sample --exclude` |
| R4 | The coders label a random subset of 176 of the 361 drawn episodes: about 45 per kind, proportional within each (kind, era) stratum, every stratum and all 60 blind episodes kept. | Verifying 301 pre-labelled rows was more than the precision figures need. Projected 95% half-widths at 45 per kind are about 0.13 for `migration` against 0.09 for the full draw. The subset is random with a fixed seed and depends only on the draw and the blind list, never on the LLM's labels or confidence, and it was fixed before any coding. | `scripts/llm_labeling/subset.py`; `round2/subset_ids.txt` |
| R5 | A precision interval never uses an effective sample size larger than the number of episodes judged. | With few episodes per stratum, a kind whose only "no" verdicts sit in a low-weight stratum got a near-zero variance and an interval that claimed near certainty. | `weighted_precision` in `report/labels.py` |

### Detector changes made between the rounds

These changed which episodes exist in each kind. The prompt (P11) describes the
result.

| # | Change | Round-1 evidence | Effect on the dataset |
|---|---|---|---|
| D1 | `v1_removal` requires that v1 SDK lines leave on net and standalone FastMCP arrives. Otherwise the episode is `incidental`. | 93 of 105 coded `v1_removal` episodes were false. 8 of the 12 true ones were this switch. | 16,415 → 261 episodes |
| D2 | `revision_change` requires that one revision literal is replaced by a different one. | 95 of 105 were false: fixtures, docs, greenfield code on old revisions. 7 of 10 true ones match. | 10,146 → 2,519 |
| D3 | `MCPError` alone is no longer evidence of v2. It still groups commits, so episode ids don't change. | 33 of 45 coded episodes resting on `MCPError` alone were false (the project's own class). | `migration` 4,369 → 3,773; `v2_adoption` 3,868 → 3,033 |
| D4 | Rejected clusters go to `episodes_incidental.jsonl`, not `episodes.jsonl`. | Keeps them auditable without counting them. | 25,188 set aside |

On the round-1 sample, the redesigned detector's weighted precision is about
0.89, against 0.27 before. Because those rules were fitted on round 1, that
figure is a development estimate. Round 2 measures them on held-out data.
