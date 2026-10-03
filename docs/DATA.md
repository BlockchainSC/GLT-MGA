# Data Card

## Composition

| Task | Total functions | Vulnerable | Safe | Outer train | Outer test |
| --- | ---: | ---: | ---: | ---: | ---: |
| SWC-107 Reentrancy | 1,612 | 629 | 983 | 1,295 | 317 |
| SWC-116 Timestamp dependency | 4,293 | 1,923 | 2,370 | 3,445 | 848 |

Samples are functions; partitions and overlap checks use `contract_key`.

## Files

```text
dataset/reentrancy/source_code/   Reentrancy Solidity corpus
dataset/timestamp/source_code/    Timestamp Solidity corpus
train_data/reentrancy/train.json  Outer training partition
train_data/reentrancy/valid.json  Outer held-out test partition
train_data/timestamp/train.json   Outer training partition
train_data/timestamp/valid.json   Outer held-out test partition
```

`valid.json` is a legacy physical filename. The loader maps it to `test_data`; it is never used for training, early stopping, threshold tuning, or checkpoint selection.

## Contract-level protocol

| Task | Outer-train contracts | Internal-train samples | Internal-validation samples | Outer-test contracts | Train/test overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reentrancy | 1,152 | 1,169 | 126 | 288 | 0 |
| Timestamp dependency | 2,768 | 3,092 | 353 | 692 | 0 |

The 10% internal validation split is stratified by contract label. Internal train/validation overlap is also zero.

## Encoded schema

Each sample contains `contract_key`, `function_key`, metadata, label/targets, local graph/features, global graph/features, and global-context mask fields. The local stream represents aligned AST-CFG-DFG evidence; the global stream represents bounded same-contract context.

## Verify integrity

```bash
python scripts/verify_dataset.py
```

The verifier checks sample and label counts, contract counts, required fields, SHA-256 digests, and zero outer train/test contract overlap.

## Derived ablation data

Large ablation JSON files are reproducible derivatives and are not committed. Generate them with `pipeline/ablation_study.py`; the generated files are ignored by Git.

## Responsible use

These labels support research evaluation and are not proofs of exploitability or safety. Use model outputs as audit-prioritization signals.
