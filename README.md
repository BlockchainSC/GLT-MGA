# GLT-MGA:Global-Local Temporal Multi-View Graph Attention for Smart Contract Vulnerability Detection

Research artifact for **GLT-MGA**, a global-local multi-view graph-attention framework for smart-contract vulnerability detection.

GLT-MGA performs function-level **reentrancy (SWC-107)** and **timestamp-dependency (SWC-116)** detection. Its local branch aligns AST, CFG, and DFG evidence; its bounded global branch models relevant same-contract call and shared-state context; and gated fusion combines the local and global representations.

## Method Overview

<p align="center">
  <a href="figures/problem_overview.pdf">
    <img src="figures/problem_overview.png" alt="Conceptual overview of target-centered local evidence and bounded same-contract context in GLT-MGA" width="900">
  </a>
</p>

GLT-MGA is designed to preserve fine-grained evidence from the target function while incorporating only relevant same-contract context. The local representation aligns AST, CFG, and DFG views over the task-relevant region, whereas the bounded global context captures selected caller, callee, and shared-state relations without encoding the complete contract.

### Vulnerability Patterns

<table>
<tr>
<td width="50%" valign="top" align="center">

<strong>Reentrancy (SWC-107)</strong><br><br>

<a href="figures/reentrancy.pdf">
  <img src="figures/reentrancy.png" alt="Motivating reentrancy example" width="100%">
</a>

<sub>A low-level external call occurs before the target-function state update; related same-contract functions provide complementary shared-state context.</sub>

</td>
<td width="50%" valign="top" align="center">

<strong>Timestamp Dependency (SWC-116)</strong><br><br>

<a href="figures/timestamp.pdf">
  <img src="figures/timestamp.png" alt="Motivating timestamp-dependency example" width="100%">
</a>

<sub>A <code>block.timestamp</code>-derived decision controls a subsequent value transfer, with related same-contract functions providing shared-state context.</sub>

</td>
</tr>
</table>

## GLT-MGA Architecture

<p align="center">
  <a href="figures/figure_1_overview.pdf">
    <img src="figures/figure_1_overview.png" alt="Proposed GLT-MGA framework for function-level smart contract vulnerability detection" width="900">
  </a>
</p>

For each target function, GLT-MGA constructs an aligned local multi-view graph and a bounded target-centered global context graph. The two branches are encoded separately through scheduled relation-specific propagation and branch-specific multi-head query-based attentive readout. A context-availability mask and a learned scalar gate then control the contribution of global context to the fused function representation used for binary classification.

### Core Encoding Components

<table>
<tr>
<td width="50%" valign="top" align="center">

<strong>Scheduled Temporal Message Propagation</strong><br><br>

<a href="figures/tmp.pdf">
  <img src="figures/tmp.png" alt="Scheduled temporal message propagation in GLT-MGA" width="100%">
</a>

<sub>Relation-typed edges are processed through a graph-derived sequence of scheduled substeps, enabling ordered information propagation over program relations.</sub>

</td>
<td width="50%" valign="top" align="center">

<strong>Multi-Head Query-Based Attentive Readout</strong><br><br>

<a href="figures/MHA.pdf">
  <img src="figures/MHA.png" alt="Branch-specific multi-head query-based attentive readout in GLT-MGA" width="100%">
</a>

<sub>Learnable head queries assign relative importance to graph nodes, and the weighted head representations are concatenated into a fixed-dimensional branch embedding.</sub>

</td>
</tr>
</table>

## Results

| Task | Configuration | Accuracy | Precision | Recall | F1 | AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Reentrancy | R2-S10-H4 | 98.74 | 97.58 | 99.18 | 98.37 | 99.84 |
| Timestamp dependency | R3-S15-H4 | 94.46 | 95.56 | 91.73 | 93.61 | 98.06 |

Values are percentages from seed `9930` and the fixed outer held-out test sets. Retained main-run artifacts are available under `results/main/`.

## Evaluation protocol

- Physical `valid.json` files retain a legacy filename but serve only as the outer held-out **test** partitions.
- A stratified, contract-level 10% internal validation subset is formed exclusively from outer training data.
- Internal training, internal validation, and outer test contracts are disjoint.
- Early stopping and checkpoint selection use internal validation loss only.
- Final evaluation restores the best validation checkpoint and applies the fixed decision threshold `0.45`.
- The fixed random seed used for the reported main experiments is `9930`.

See [`docs/DATA.md`](docs/DATA.md) and [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) for dataset and protocol details.

## Repository layout

```text
GLT-MGA.py                         Main GLT-MGA model entry point
BasicModel.py                      Training, validation, testing, and reporting
configs/main/                      Final task-specific configurations
experiments/ablation/
  GLT-MGA_ablation.py              Local-only ablation entry point
dataset/                           Solidity source corpus
train_data/                        Processed function-level train/test partitions
pipeline/                          Selected data-construction and ablation utilities
scripts/
  run_main.sh                      Main experiment launcher
  run_ablation.sh                  Ablation launcher
  verify_dataset.py                Dataset-integrity verification
tools/paper_figures/               Paper-figure generation utilities
results/                           Retained main, ablation, and sensitivity artifacts
figures/                           Manuscript figures
docs/                              Dataset and reproducibility documentation
```

## Environment setup

The tested stack is Python `3.7.12`, TensorFlow `1.14.0`, NumPy `1.21.6`, scikit-learn `1.0.2`, and Matplotlib `3.5.3`.

```bash
conda env create -f environment.yml
conda activate glt-mga
git lfs pull
python scripts/verify_dataset.py
```

Git LFS is required for the large processed JSON files and the PDF figure artifacts tracked by this repository.

## Run the main model

Run all commands from the repository root.

### Recommended reproduction commands

The launcher applies the task-specific configuration, seed `9930` by default, and the fixed decision threshold `0.45`.

```bash
bash scripts/run_main.sh reentrancy 9930
bash scripts/run_main.sh timestamp 9930
```

New run artifacts are written under:

```text
runs/main/reentrancy/
runs/main/timestamp/
```

### Equivalent direct Python commands

For **Reentrancy**:

```bash
python -u GLT-MGA.py \
  --task reentrancy \
  --config-file configs/main/reentrancy.json \
  --random_seed 9930 \
  --thresholds 0.45 \
  --log_dir runs/manual/reentrancy_seed9930
```

For **Timestamp Dependency**:

```bash
python -u GLT-MGA.py \
  --task timestamp \
  --config-file configs/main/timestamp.json \
  --random_seed 9930 \
  --thresholds 0.45 \
  --log_dir runs/manual/timestamp_seed9930
```

The final task configurations are:

| Task | Propagation rounds (R) | Max. substeps (S) | Attention heads (H) | Threshold |
| --- | ---: | ---: | ---: | ---: |
| Reentrancy | 2 | 10 | 4 | 0.45 |
| Timestamp dependency | 3 | 15 | 4 | 0.45 |

To inspect all command-line options:

```bash
python GLT-MGA.py --help
```

The main entry point supports `--task`, `--config-file`, `--random_seed`, `--thresholds`, `--log_dir`, `--data_dir`, and `--restore`.

Training time and exact floating-point results can vary across GPU/cuDNN stacks, while the released data partitions and evaluation protocol remain fixed.

## Ablation reproduction

Derived ablation JSON files are generated instead of committing duplicate processed datasets:

```bash
python pipeline/ablation_study.py --task reentrancy --variant all
python pipeline/ablation_study.py --task timestamp --variant all

bash scripts/run_ablation.sh reentrancy all 9930
bash scripts/run_ablation.sh timestamp all 9930
```

The `local` variant disables global propagation/readout and bypasses local-global fusion. Other variants preserve the global branch while altering the retained local views.

## Result artifacts

- `results/main/` contains retained artifacts for the reported main experiments.
- `results/rq2/` contains component-ablation artifacts.
- `results/rq3/` contains architecture-sensitivity artifacts.
- Checkpoints, caches, and routine logs are excluded from the curated result folders.

## Figures

The `figures/` directory contains the manuscript figures and their vector source versions used in the paper. The figures embedded above provide the conceptual motivation, the complete GLT-MGA architecture, and the two principal encoding components. Additional experimental figures are retained with the corresponding result artifacts.

## Citation

Citation information will be added when the final publication record and DOI are available.

