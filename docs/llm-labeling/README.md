# LLM-assisted labeling

This folder documents how the labeled sample was produced. It covers which
model was used, the prompts, how the prompts changed between rounds and why,
and how the labels are validated against human judgement.

| File | Contents |
|---|---|
| [`prompt-round1.md`](prompt-round1.md) | The round-1 instruction, inputs and working rules, as used |
| [`prompt-round2.md`](prompt-round2.md) | The refined round-2 prompt, to be used verbatim |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed from round 1 to round 2, and the evidence behind each change |
| [`../../scripts/llm_labeling/`](../../scripts/llm_labeling/) | The scripts that collect evidence, render dossiers and validate the LLM's output |

## Declaration of use

- **Model and tool.** Claude Opus 5.5 (`claude-opus-5-5`), run as an agent in
  Claude Code (VS Code extension). Claude Code does not expose sampling
  parameters, so temperature and similar settings are unknown. That is a
  limitation to report, not a setting that was chosen.
- **Role.** Pre-annotator. The LLM labels every sampled episode under the
  codebook. Two human coders (A and B) then verify every label. No label
  enters the analysis without human verification.
- **Where it enters the study.** Only the manual-labeling step that measures
  detector precision and codes `end_state`, `trigger`, `author_kind_verified`
  and `deprecated`. Mining, episode building and statistics are deterministic
  code.
- **Why.** Each episode needs its commits, pull requests, issues and final
  repository state to be read. Pre-labeling with written evidence notes cuts
  that reading time. The human verifiers remain responsible for every label.

## Protocol per round

1. **Draw.** `mcp-migration sample` draws a stratified sample over (kind × head
   era). Each round excludes all episodes labeled in earlier rounds
   (`--exclude`), because a round that shaped the detector's rules cannot also
   measure them.
2. **Collect evidence.** `scripts/llm_labeling/collect.py SHEET` fetches each
   episode's commits, pull requests, issues and post-episode files from the
   GitHub API. Responses are cached, so the dossiers can be rebuilt exactly.
3. **Render dossiers.** `scripts/llm_labeling/dossier.py SHEET OUTDIR` writes
   one evidence dossier per episode, 20 episodes per batch file.
4. **Pre-label.** The LLM codes each batch under the round's prompt. It
   writes a JSON object per batch, and `scripts/llm_labeling/record.py`
   validates each row against the codebook, using the same check as the
   labeling app. The result is the round's LLM sheet (`llm/coderLLM.csv`).
5. **Hold out a blind subset.** A random subset, stratified by kind, gets no
   pre-labels in the coders' sheets. Both coders code it from scratch.
6. **Verify independently.** Each coder verifies the pre-labelled rows and codes
   the blind rows in their own sheet, without discussing episodes and without
   opening the LLM sheet for the blind rows.
7. **Measure, then adjudicate.** `mcp-migration labels` with `--prelabels` and
   `--blind` records the agreement figures below. Only after that are
   disagreements resolved, into an adjudicated sheet, under written rules
   (R1–R9 in the codebook).

## What is reported, and why

Agreement between two coders who verified the same suggestions measures how
often they accepted them, not independent judgement. Annotators shown LLM
suggestions tend to adopt them (Schroeder et al., 2025), so that figure alone
would overstate reliability. The report therefore separates three things.

| Figure | Rows | What it measures | `numbers.tex` macro |
|---|---|---|---|
| Share of pre-label cells each coder kept | pre-labelled rows | how much the human changed | `\pctLlmAccepted`, `\nLlmCellsChanged` |
| Kappa between the coders | all rows | agreement between verifiers (inflated by anchoring) | `\kappaVerifiers` (also `\kappaOverall`) |
| Kappa between the coders | blind subset | independent human agreement | `\kappaBlind`, `\nBlind` |
| Kappa between the LLM and the reference labels | blind subset (or all rows, if no blind subset) | accuracy of the LLM's pre-labels | `\kappaLlmDetector` |
| Detector precision, per kind and overall | adjudicated sheet | the study's result | `\precisionOverall`, `\prec…` |

The target for independent agreement is κ ≥ 0.8. The guidelines treat
reliability below 0.8 as supporting only tentative conclusions and below 0.667
as unusable.

### Round 1 (`data/labeling/round1/`, 520 episodes)

- **Blind subset.** None: every coded row in round 1 was pre-labelled. Round 1
  therefore has no independent agreement figure, and the LLM-versus-reference
  comparison is anchored.
- **Edits to pre-labels.** Coder A changed 42 of 2,600 cells (1.6%) in 33
  episodes, 3 of them detector verdicts. Coder B changed 28 cells (1.1%) in 28
  episodes and no detector verdicts. Most changes were in
  `author_kind_verified`.
- **Verifier κ.** 0.99 for `detector_correct`, 0.80 for
  `author_kind_verified`.
- **Use in the study.** Round 1 was also used to redesign the detector rules
  for `revision_change`, `v1_removal` and `MCPError`, so its precision for those
  kinds is a development estimate only.

### Round 2 (`data/labeling/round2/`)

- **Sample.** Drawn after the detector change, excluding all round-1 episodes:
  361 episodes, of which a random subset of 176 is coded (about 45 per kind,
  116 pre-labelled and 60 blind; see R4 in the changelog).
- **Blind subset.** Held out in `blind_ids.txt`, so round 2 gives the
  independent κ and the unanchored LLM accuracy. Its precision figures are the
  ones to report for the changed kinds.

## How this follows published guidance

The design follows the SE guidelines for empirical studies involving LLMs
(Baltes et al., 2025, maintained at [llm-guidelines.org](https://llm-guidelines.org/guidelines/)):

| Guideline | What it asks | Where it is met here |
|---|---|---|
| Declare usage | state the model, its role, where it enters the research and why, in the methods section | "Declaration of use" above. A publication using these labels should repeat it in its methods. |
| Model version | name, version, date of execution, output-affecting parameters; report what a commercial tool exposes and acknowledge the rest | Model id and dates in the prompt files. Unknown sampling settings are listed as a limitation. |
| Design | publish all prompts, explain how they were developed, keep a prompt changelog with reasons | `prompt-round1.md`, `prompt-round2.md`, `CHANGELOG.md` |
| Traces | share interaction logs where possible, or representative examples | Dossiers can be rebuilt from the cached evidence. Per-episode notes in the LLM sheet record the evidence behind each label. |
| Human validation | validate LLM annotations against human annotators, report agreement by round, describe aggregation | Two human verifiers per episode, a blind subset per round, adjudication rules R1–R9, figures reported per round |
| Limitations | discuss threats specific to LLM use | Anchoring, the missing round-1 blind subset, round 1 doubling as rule-development data, unknown sampling settings |

Related work points the same way:

- **Ahmed et al. (MSR 2025).** Across ten annotation tasks, replacing one human
  annotator with an LLM gave agreement close to human–human agreement on
  low-context, deductive tasks. Model–model agreement and model confidence
  indicate where that substitution is safe. This study goes less far: the LLM
  replaces no human, and both humans verify every label.
- **Wang et al. (CHI 2024).** Proposes a pipeline in which LLMs label and
  explain, the labels are checked, and humans re-annotate the doubtful ones.
  The round-2 prompt's `confidence` field supports that kind of triage, though
  here every row is still verified.
- **Schroeder, Roy and Kabbara (Findings of ACL 2025).** Annotators shown LLM
  suggestions largely took them, which shifted the label distribution. This is
  the reason for the blind subset.
- **De Martino et al. (2025, PRIMES 2.0).** A staged framework for LLM-based
  mining-software-repositories studies that maps each step to its validity
  threats. The round structure here (refine on one round, measure on a fresh
  one) follows the same concern about validating on held-out data.

## References

- Ahmed, T., Devanbu, P., Treude, C., Pradel, M. *Can LLMs Replace Manual
  Annotation of Software Engineering Artifacts?* MSR 2025, pp. 526–538.
  doi:10.1109/MSR66628.2025.00086. arXiv:2408.05534.
- Baltes, S., et al. *Guidelines for Empirical Studies in Software Engineering
  involving Large Language Models.* arXiv:2508.15503, 2025.
  <https://llm-guidelines.org/guidelines/>.
- Wagner, S., Muñoz Barón, M., Falessi, D., Baltes, S. *Towards Evaluation
  Guidelines for Empirical Studies involving LLMs.* WSESE @ ICSE 2025.
  arXiv:2411.07668.
- Wang, X., Kim, H., Miao, Z., Mitra, K., Rahman, S. *Human-LLM Collaborative
  Annotation Through Effective Verification of LLM Labels.* CHI 2024.
  doi:10.1145/3613904.3641960.
- Schroeder, H., Roy, D., Kabbara, J. *Just Put a Human in the Loop?
  Investigating LLM-Assisted Annotation for Subjective Tasks.* Findings of ACL
  2025, pp. 25771–25795.
- De Martino, V., Castaño, J., Palomba, F., Franch, X., Martínez-Fernández, S.
  *A Methodological Framework for LLM-Based Mining of Software Repositories.*
  arXiv:2508.02233, 2025.
