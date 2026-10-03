# Reproducibility Protocol

## Final configurations

| Task | Rounds (R) | Max substeps (S) | Attention heads (H) | Epoch cap | Patience | Threshold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Reentrancy | 2 | 10 | 4 | 500 | 50 | 0.45 |
| Timestamp dependency | 3 | 15 | 4 | 500 | 50 | 0.45 |

Shared settings include hidden size `256`, attention hidden size `128`, Adam learning rate `0.001`, equal class weights, internal validation ratio `0.10`, and split seed `9930`.

## Main experiment

```bash
bash scripts/run_main.sh reentrancy
bash scripts/run_main.sh timestamp
```

Each run performs the following sequence:

1. Load the fixed outer training partition.
2. Create a stratified contract-level internal train/validation split.
3. Verify zero contract overlap within the internal split and against the outer test set.
4. Train for at most 500 epochs.
5. Select the checkpoint with minimum internal-validation loss; stop after 50 non-improving epochs.
6. Restore the selected checkpoint.
7. Evaluate once on the outer test set at threshold `0.45`.

The output directory contains `*_internal_split_report.json`, `*_internal_validation_metrics.json`, `*_metrics.json`, `summary_*.json`, confusion matrices, convergence histories, gate reports, and timing reports.

## RQ2 component ablation

First generate the derived datasets:

```bash
python pipeline/ablation_study.py --task reentrancy --variant all
python pipeline/ablation_study.py --task timestamp --variant all
```

Then run one or all variants:

```bash
bash scripts/run_ablation.sh reentrancy all 9930
bash scripts/run_ablation.sh timestamp all 9930
```

Variants `ast`, `cfg`, and `dfg` retain one local program view. Variants `without_ast`, `without_cfg`, and `without_dfg` remove one view. Variant `local` retains AST-CFG-DFG but disables the global branch. Variant `full` runs the complete model.

## RQ3 sensitivity

RQ3 uses one-factor-at-a-time comparisons around task-specific selected configurations. The retained artifacts in `results/rq3/` include every reported run and its configuration-specific folder name.

For Reentrancy, the center is R2-S10-H4. For Timestamp dependency, the center is R3-S15-H4. The same center result is reused across blocks instead of retraining an identical configuration.

## Determinism

Python, NumPy, and TensorFlow seeds are set. TensorFlow 1.x GPU kernels and cuDNN operations may remain nondeterministic, so exact last-decimal reproduction can vary by hardware/software stack. Split membership, configuration, and evaluation semantics are deterministic and recorded.

## Artifact policy

Curated results exclude model checkpoints and console logs. This keeps the release compact without removing metric evidence. Newly generated checkpoints remain under `runs/`, which is ignored by Git.
