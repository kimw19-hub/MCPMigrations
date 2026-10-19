# Labeled samples

The labels are LLM-assisted. An LLM pre-labeled each sampled episode, two
coders verified every label independently, and disagreements were
adjudicated. The protocol, prompts and prompt changelog are in
[`docs/llm-labeling/`](../../docs/llm-labeling/README.md). The codebook is
[`docs/codebook.md`](../../docs/codebook.md).

Each round has its own directory with the same layout:

| Path | What it is |
|---|---|
| `round<N>/sample.csv` | The stratified draw (blank codebook columns). Do not edit. |
| `round<N>/llm/coderLLM.csv` | LLM pre-labels with evidence notes |
| `round<N>/coderA.csv`, `round<N>/coderB.csv` | Each coder's verified labels |
| `round<N>/adjudicated.csv` | Disagreements resolved under rules R1–R9, with an `adjudication_note` per changed cell |
| `round<N>/blind_ids.txt` | Episodes coded without pre-labels (round 2 onwards) |

`mcp-migration sample --round N` writes the draw and `mcp-migration label
--coder A --round N` codes `round<N>/coderA.csv`, starting it from the draw.

## Round 1: 520 episodes, every row pre-labeled

| Path | What it is |
|---|---|
| `round1/sample.csv` | The stratified draw |
| `round1/llm/coderLLM.csv` | LLM pre-labels with evidence notes ([how they were made](round1/llm/README.md)) |
| `round1/coderA.csv`, `round1/coderB.csv` | Each coder's verified labels |
| `round1/coderA_verification.csv` | Coder A's working record: the LLM sheet with a `Human_Verification` verdict (Correct or Incorrect) per episode and `Human_notes` explaining each of the 33 marked Incorrect, which are the 33 episodes `coderA.csv` changes |
| `round1/adjudicated.csv` | Disagreements resolved under rules R1–R9 |

Round 1 has no blind subset, and it was used to redesign three detector rules.
It therefore supports verifier agreement and a development estimate of
precision, but not independent agreement.

```bash
mcp-migration labels data/labeling/round1/coderA.csv data/labeling/round1/coderB.csv \
  --prelabels data/labeling/round1/llm/coderLLM.csv \
  --adjudicated data/labeling/round1/adjudicated.csv
```

## Round 2: 176 coded episodes, drawn after the detector change, none from round 1

| Path | What it is |
|---|---|
| `round2/sample.csv` | The full stratified draw, 361 episodes |
| `round2/subset_ids.txt` | The 176 episodes the coders label: a random subset of the draw, about 45 per kind, in proportion within each (kind, era) stratum, with every stratum and every blind episode kept (`scripts/llm_labeling/subset.py`, seed 2026). Fixed before anyone coded. |
| `round2/blind_ids.txt` | 60 of those (15 per kind) that both coders label without seeing pre-labels |
| `round2/llm/coderLLM.csv` | LLM pre-labels for all 361, with notes and `confidence`. Coders do not open it until they finish. |
| `round2/coderA.csv`, `round2/coderB.csv` | Each coder's sheet, the 176 subset episodes. Pre-labeled rows hold the LLM's values; blind rows are empty. |
| `round2/adjudicated.csv` | Disagreements resolved (after the agreement figures are recorded) |

```bash
mcp-migration labels data/labeling/round2/coderA.csv data/labeling/round2/coderB.csv \
  --prelabels data/labeling/round2/llm/coderLLM.csv \
  --blind data/labeling/round2/blind_ids.txt \
  --adjudicated data/labeling/round2/adjudicated.csv
```
