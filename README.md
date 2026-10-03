# GLT-MGA: Global-Local Temporal Multi-View Graph Attention for Smart Contract Vulnerability Detection

Research artifact for **GLT-MGA**, a global-local multi-view graph-attention framework for smart contract vulnerability detection.

GLT-MGA performs function-level **reentrancy (SWC-107)** and **timestamp-dependency (SWC-116)** detection. Its local branch aligns AST, CFG, and DFG evidence; its bounded global branch models relevant same-contract call and shared-state context; and gated fusion combines the local and global representations.

## Architecture

<p align="center">
  <a href="figures/figure_1_overview.pdf">
    <img src="figures/figure_1_overview.png" alt="GLT-MGA architecture" width="900">
  </a>
</p>

The PNG above is used for GitHub preview. The paper-quality vector version is available at [`figures/figure_1_overview.pdf`](figures/figure_1_overview.pdf).

## Additional manuscript figures

The following figures provide a compact visual summary of the motivation and core GLT-MGA components. PNG files are used for inline GitHub rendering, while the corresponding PDFs remain available as paper-quality vector versions.

### Target-centered local evidence and bounded same-contract context

<p align="center">
  <a href="figures/problem_overview.pdf">
    <img src="figures/problem_overview.png" alt="Target-centered local evidence and bounded same-contract context" width="900">
  </a>
</p>

This conceptual view shows how GLT-MGA combines focused target-function AST, CFG, and DFG evidence with bounded same-contract caller, callee, and shared-state context, rather than using either an isolated function or the complete contract.

### Reentrancy motivating example

<p align="center">
  <a href="figures/reentrancy.pdf">
    <img src="figures/reentrancy.png" alt="Reentrancy motivating example" width="850">
  </a>
</p>

The example highlights the vulnerability-relevant call-before-write execution order in the target function and the complementary same-contract shared-state context.

### Timestamp-dependency motivating example

<p align="center">
  <a href="figures/timestamp.pdf">
    <img src="figures/timestamp.png" alt="Timestamp-dependency motivating example" width="850">
  </a>
</p>

The example highlights a `block.timestamp`-dependent decision that controls a later value transfer, together with related same-contract shared-state context.

### Scheduled temporal message propagation

<p align="center">
  <a href="figures/tmp.pdf">
    <img src="figures/tmp.png" alt="Scheduled temporal message propagation" width="850">
  </a>
</p>

GLT-MGA schedules relation-specific graph edges across propagation substeps so that information is propagated through program relations in an ordered manner.

### Multi-head query-based attentive readout

<p align="center">
  <a href="figures/MHA.pdf">
    <img src="figures/MHA.png" alt="Multi-head query-based attentive readout" width="850">
  </a>
</p>

Learnable head queries assign relative importance to graph nodes, and the resulting head embeddings are concatenated to form the branch-level graph representation.

## Results

| Task | Configuration | Accuracy | Precision | Recall | F1 | AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Reentrancy | R2-S10-M4 | 98.74 | 97.58 | 99.18 | 98.37 | 99.84 |
| Timestamp dependency | R3-S15-M4 | 94.46 | 95.56 | 91.73 | 93.61 | 98.06 |

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

| Task | Propagation rounds (R) | Max. substeps (S<sub>max</sub>) | Attention heads (M) | Threshold |
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

The `local` variant corresponds to **GLT-MGA(L)** in the paper and disables global propagation/readout and local-global fusion. The `ast`, `cfg`, and `dfg` variants correspond to **GLT-MGA(AST)**, **GLT-MGA(CFG)**, and **GLT-MGA(DFG)**, while `without_ast`, `without_cfg`, and `without_dfg` correspond to **GLT-MGA(AST-)**, **GLT-MGA(CFG-)**, and **GLT-MGA(DFG-)**, respectively. The `full` variant retains all three local views together with the global branch.

## Result artifacts

- `results/main/` contains retained artifacts for the reported main experiments.
- `results/rq2/` contains component-ablation artifacts.
- `results/rq3/` contains architecture-sensitivity artifacts.
- Checkpoints, caches, and routine logs are excluded from the curated result folders.

## Figures

Paper-quality vector figures are retained as PDFs under `figures/`, with PNG copies used only for inline GitHub previews. Each preview above links to its corresponding PDF.

The README-rendered figure pairs are:

```text
figures/figure_1_overview.pdf   + figures/figure_1_overview.png
figures/problem_overview.pdf    + figures/problem_overview.png
figures/reentrancy.pdf          + figures/reentrancy.png
figures/timestamp.pdf           + figures/timestamp.png
figures/tmp.pdf                 + figures/tmp.png
figures/MHA.pdf                 + figures/MHA.png
```

Propagation-round sensitivity figures remain available with the experimental artifacts and do not need to be duplicated inline in the README.

## Citation

Citation information will be added when the final publication record and DOI are available.

