# GLT-MGA

Research artifact for **Global-Local Temporal Multi-View Graph Attention for Smart Contract Vulnerability Detection**.

GLT-MGA performs function-level reentrancy (SWC-107) and timestamp-dependency (SWC-116) detection. Its local branch aligns AST, CFG, and DFG evidence; its bounded global branch models relevant same-contract call and shared-state context; gated fusion combines both representations.

![GLT-MGA architecture](figures/architecture.png)

## Results

| Task | Configuration | Accuracy | Precision | Recall | F1 | AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Reentrancy | R2-S10-H4 | 98.74 | 97.58 | 99.18 | 98.37 | 99.84 |
| Timestamp dependency | R3-S15-H4 | 94.46 | 95.56 | 91.73 | 93.61 | 98.06 |

Values are percentages from seed `9930` and the fixed outer held-out test sets. Raw metric artifacts are in `results/main/`.

## Evaluation protocol

- Physical `valid.json` files retain a legacy name but serve only as outer **test** partitions.
- A stratified, contract-level 10% validation subset is formed exclusively from outer training data.
- Internal train, internal validation, and outer test contracts are disjoint.
- Early stopping and checkpoint selection use internal validation loss only.
- Final metrics use the restored best checkpoint and fixed threshold `0.45`.

See [docs/DATA.md](docs/DATA.md) and [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Layout

```text
GLT-MGA_test.py                    Main model entry point
BasicModel_gated_test.py           Split, training, evaluation, and reports
GLT-MGA_test_ablation.py           Local-only ablation entry point
BasicModel_gated_test_ablataion.py Local-only base (legacy filename spelling)
configs/main/                      Final task configurations
dataset/                           Solidity source corpus
train_data/                        Exact encoded outer partitions
pipeline/                          Numbered data-construction stages
scripts/                           Reproduction and integrity commands
tools/                             Portable paper-figure utilities
results/                           Curated metrics and figures, no checkpoints
docs/                              Protocol and provenance documentation
```

## Setup

The tested stack is Python `3.7.12`, TensorFlow `1.14.0`, NumPy `1.21.6`, scikit-learn `1.0.2`, and Matplotlib `3.5.3`.

```bash
conda env create -f environment.yml
conda activate glt-mga
git lfs pull
python scripts/verify_dataset.py
```

Git LFS is required because `train_data/timestamp/train.json` exceeds GitHub's regular-file size limit.

## Main reproduction

```bash
bash scripts/run_main.sh reentrancy
bash scripts/run_main.sh timestamp
```

The scripts write new artifacts under `runs/main/`. Training time and exact floating-point results can vary across GPU/cuDNN stacks, while split membership and evaluation semantics remain fixed.

## Ablation reproduction

Derived ablation JSONs are generated rather than committed as nearly 1 GB of duplicate data:

```bash
python pipeline/ablation_study.py --task reentrancy --variant all
python pipeline/ablation_study.py --task timestamp --variant all
bash scripts/run_ablation.sh reentrancy all 9930
bash scripts/run_ablation.sh timestamp all 9930
```

The `local` variant disables global propagation/readout and bypasses local-global fusion. Other variants preserve the global branch and alter only retained local views.

## Provenance

`results/rq2/` and `results/rq3/` retain the experimental evidence used for component and architectural analyses. [docs/RESULTS.md](docs/RESULTS.md) records source paths and discloses a Reentrancy `GLT-MGA(L)` seed inconsistency found during release verification. It should be resolved before every Table IV value is described as seed `9930`.

Checkpoints, caches, and repetitive logs are excluded. The retained reports include metrics, histories, split manifests, timing data, and plots.

## Citation and license

Citation metadata is in `CITATION.cff`; add the final repository URL, DOI, and publication record when available. A software/data license must be selected by the authors before the repository is described as open source.
