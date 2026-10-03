#!/usr/bin/env python3
"""Portable entry point for deterministic GLT-MGA ablation datasets."""

import argparse
from pathlib import Path

import ablation_study_legacy as legacy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VARIANTS = tuple(legacy.VARIANTS)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task", required=True, choices=("reentrancy", "timestamp")
    )
    parser.add_argument(
        "--variant", required=True, choices=VARIANTS + ("all",)
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=PROJECT_ROOT / "train_data",
        help="Directory containing reentrancy/ and timestamp/ partitions.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "train_data" / "ablation_study",
        help="Destination root for derived variants.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    legacy.STEP16_BASE = args.data_root.resolve()
    legacy.ABLATION_BASE = args.output_root.resolve()
    legacy.TASK_SOURCE_DIR = {
        "reentrancy": "reentrancy",
        "timestamp": "timestamp",
    }

    variants = VARIANTS if args.variant == "all" else (args.variant,)
    for variant in variants:
        legacy.process_variant(args.task, variant)


if __name__ == "__main__":
    main()
