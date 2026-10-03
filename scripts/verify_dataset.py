#!/usr/bin/env python3
"""Verify released GLT-MGA partitions without importing TensorFlow."""

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {
    "contract_key",
    "function_key",
    "targets",
    "label",
    "local_graph",
    "local_node_features",
    "global_graph",
    "global_node_features",
    "global_mask",
}

EXPECTED = {
    "reentrancy": {
        "train": {"samples": 1295, "labels": {0: 788, 1: 507}, "contracts": 1152,
                  "sha256": "b261537601bccdeed2b67c2998358481235601adf9b2ce29e30c0858ffc3e3d8"},
        "valid": {"samples": 317, "labels": {0: 195, 1: 122}, "contracts": 288,
                  "sha256": "676d5ffc9b28746d572ea2c9485f733714571cc6aa834e8ceea8cb7155a49bdf"},
    },
    "timestamp": {
        "train": {"samples": 3445, "labels": {0: 1897, 1: 1548}, "contracts": 2768,
                  "sha256": "44169fc624c37fb42b025fd696c503b31584f49b4e40868ee99293a14b87a518"},
        "valid": {"samples": 848, "labels": {0: 473, 1: 375}, "contracts": 692,
                  "sha256": "0107bed4231a591241156de9ed6f971b16c88518e1723dc83a0f80d7fdbee49b"},
    },
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_file(task, split):
    path = ROOT / "train_data" / task / (split + ".json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    expected = EXPECTED[task][split]
    labels = Counter(int(row["label"]) for row in rows)
    contracts = {str(row["contract_key"]) for row in rows}
    missing = REQUIRED_FIELDS - set(rows[0]) if rows else REQUIRED_FIELDS
    actual_hash = digest(path)

    assert len(rows) == expected["samples"], (path, len(rows))
    assert dict(labels) == expected["labels"], (path, dict(labels))
    assert len(contracts) == expected["contracts"], (path, len(contracts))
    assert not missing, (path, sorted(missing))
    assert actual_hash == expected["sha256"], (path, actual_hash)
    return contracts


def main():
    for task in ("reentrancy", "timestamp"):
        train_contracts = verify_file(task, "train")
        test_contracts = verify_file(task, "valid")
        overlap = train_contracts & test_contracts
        assert not overlap, (task, "contract overlap", sorted(overlap)[:5])
        print(
            "{}: PASS ({} train contracts, {} outer-test contracts, overlap=0)".format(
                task, len(train_contracts), len(test_contracts)
            )
        )
    print("All released partitions passed integrity checks.")


if __name__ == "__main__":
    main()
