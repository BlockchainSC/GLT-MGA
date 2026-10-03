#!/usr/bin/env python3

import argparse
import copy
import json
from collections import Counter
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]

STEP16_BASE = BASE / "train_data"

ABLATION_BASE = BASE / "train_data" / "ablation_study"


# Step8 fixed view order:
# 0 = ANCHOR
# 1 = AST
# 2 = CFG
# 3 = DFG
VIEW_NAMES = {
    0: "ANCHOR",
    1: "AST",
    2: "CFG",
    3: "DFG",
}


VARIANTS = {
    "ast": {
        "name": "GLT-MGA(AST)",
        "folder": "GLT-MGA_AST",
        "keep": {"ANCHOR", "AST"},
        "required_view": "AST",
    },

    "cfg": {
        "name": "GLT-MGA(CFG)",
        "folder": "GLT-MGA_CFG",
        "keep": {"ANCHOR", "CFG"},
        "required_view": "CFG",
    },

    "dfg": {
        "name": "GLT-MGA(DFG)",
        "folder": "GLT-MGA_DFG",
        "keep": {"ANCHOR", "DFG"},
        "required_view": "DFG",
    },

    "without_ast": {
        "name": "GLT-MGA(AST-)",
        "folder": "GLT-MGA_without_AST",
        "keep": {"ANCHOR", "CFG", "DFG"},
    },

    "without_cfg": {
        "name": "GLT-MGA(CFG-)",
        "folder": "GLT-MGA_without_CFG",
        "keep": {"ANCHOR", "AST", "DFG"},
    },

    "without_dfg": {
        "name": "GLT-MGA(DFG-)",
        "folder": "GLT-MGA_without_DFG",
        "keep": {"ANCHOR", "AST", "CFG"},
    },

    "local": {
        "name": "GLT-MGA(L)",
        "folder": "GLT-MGA_L",
        "keep": {"ANCHOR", "AST", "CFG", "DFG"},
        "global_enabled": False,
    },

    "full": {
        "name": "Full GLT-MGA",
        "folder": "Full_GLT-MGA",
        "keep": {"ANCHOR", "AST", "CFG", "DFG"},
        "global_enabled": True,
    },
}


TASK_SOURCE_DIR = {
    "reentrancy": "reentrancy",
    "timestamp": "timestamp",
}


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--task",
        required=True,
        choices=["reentrancy", "timestamp"],
    )

    parser.add_argument(
        "--variant",
        required=True,
        choices=[
            "ast",
            "cfg",
            "dfg",
            "without_ast",
            "without_cfg",
            "without_dfg",
            "local",
            "full",
            "all",
        ],
    )

    return parser.parse_args()


def get_view_name(feature):
    """
    Step16 local_node_features come from Step10 one-hot features.

    First four positions correspond to:
      [ANCHOR, AST, CFG, DFG]
    """

    if not isinstance(feature, list):
        raise ValueError("Node feature is not a list.")

    if len(feature) < 4:
        raise ValueError(
            f"Node feature has length {len(feature)}, expected >= 4."
        )

    first_four = [float(x) for x in feature[:4]]

    hot = [
        i
        for i, value in enumerate(first_four)
        if value > 0.5
    ]

    if len(hot) != 1:
        raise ValueError(
            "Invalid view one-hot encoding. "
            f"First four values = {first_four}"
        )

    return VIEW_NAMES[hot[0]]


def convert_row(row, keep_views, variant_name, global_enabled=True):
    item = copy.deepcopy(row)

    old_features = item.get("local_node_features", [])
    old_graph = item.get("local_graph", [])

    if not old_features:
        raise ValueError(
            f"Missing local_node_features for "
            f"{item.get('function_key', '')}"
        )

    # ---------------------------------------------------
    # 1. Select nodes belonging to requested views
    # ---------------------------------------------------

    kept_ids = []
    before_view_counts = Counter()

    for old_id, feature in enumerate(old_features):
        view = get_view_name(feature)
        before_view_counts[view] += 1

        if view in keep_views:
            kept_ids.append(old_id)

    if not kept_ids:
        raise ValueError(
            f"No nodes retained for {item.get('function_key', '')}"
        )

    # ---------------------------------------------------
    # 2. Re-index retained nodes
    # ---------------------------------------------------

    old_to_new = {
        old_id: new_id
        for new_id, old_id in enumerate(kept_ids)
    }

    new_features = [
        old_features[old_id]
        for old_id in kept_ids
    ]

    # ---------------------------------------------------
    # 3. Keep only edges whose BOTH endpoints survive
    # ---------------------------------------------------

    new_graph = []

    for edge in old_graph:

        if not isinstance(edge, list) or len(edge) != 3:
            raise ValueError(
                f"Invalid local edge format: {edge}"
            )

        src, edge_type, dst = edge

        src = int(src)
        dst = int(dst)

        if src in old_to_new and dst in old_to_new:
            new_graph.append([
                old_to_new[src],
                edge_type,
                old_to_new[dst],
            ])

    # ---------------------------------------------------
    # 4. Validate re-indexing
    # ---------------------------------------------------

    n = len(new_features)

    for edge in new_graph:
        src, _, dst = edge

        if not (0 <= src < n and 0 <= dst < n):
            raise ValueError(
                f"Bad re-indexed edge: {edge}, nodes={n}"
            )

    # ---------------------------------------------------
    # 5. Replace ONLY LOCAL stream
    # ---------------------------------------------------

    item["local_node_features"] = new_features
    item["local_graph"] = new_graph

    # Metadata only; model should not use these.
    item["ablation_variant"] = variant_name
    item["ablation_local_views"] = sorted(keep_views)
    item["ablation_global_enabled"] = bool(global_enabled)

    # GLT-MGA(L) keeps the global tensors for input-shape compatibility, but
    # disables their contribution through the model's global mask.
    if not global_enabled:
        item["global_mask"] = 0.0

    # ---------------------------------------------------
    # 6. Confirm global stream was untouched
    # ---------------------------------------------------

    for field in [
        "global_graph",
        "global_node_features",
        "label",
        "targets",
        "function_key",
    ]:
        if field in row:
            assert item[field] == row[field], (
                f"{field} changed unexpectedly."
            )

    if global_enabled and "global_mask" in row:
        assert item["global_mask"] == row["global_mask"], (
            "global_mask changed for a global-enabled variant."
        )

    return item, before_view_counts


def process_file(src_file, dst_file, config):

    with src_file.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    converted = []

    original_nodes = 0
    retained_nodes = 0
    original_edges = 0
    retained_edges = 0

    source_view_counts = Counter()
    output_view_counts = Counter()

    for row in rows:

        original_nodes += len(
            row.get("local_node_features", [])
        )

        original_edges += len(
            row.get("local_graph", [])
        )

        item, before_counts = convert_row(
            row,
            keep_views=config["keep"],
            variant_name=config["name"],
            global_enabled=config.get("global_enabled", True),
        )

        source_view_counts.update(before_counts)

        retained_nodes += len(
            item["local_node_features"]
        )

        retained_edges += len(
            item["local_graph"]
        )

        for feature in item["local_node_features"]:
            output_view_counts[get_view_name(feature)] += 1

        # Per-sample guard: no view outside the requested configuration may
        # survive, and single-view variants must contain their target view.
        sample_views = {
            get_view_name(feature)
            for feature in item["local_node_features"]
        }
        unexpected_views = sample_views - set(config["keep"])
        if unexpected_views:
            raise ValueError(
                f"Unexpected views survived ablation for "
                f"{row.get('function_key', '')}: "
                f"{sorted(unexpected_views)}"
            )

        required_view = config.get("required_view")
        if required_view and required_view not in sample_views:
            raise ValueError(
                f"Required view {required_view} is missing for "
                f"{row.get('function_key', '')}"
            )

        converted.append(item)

    # File-level guard: catches an unexpected view even if a future change
    # bypasses the per-sample validation above.
    unexpected_views = (
        set(output_view_counts.keys()) - set(config["keep"])
    )
    if unexpected_views:
        raise ValueError(
            f"Unexpected views survived ablation: "
            f"{sorted(unexpected_views)}"
        )

    dst_file.parent.mkdir(parents=True, exist_ok=True)

    with dst_file.open("w", encoding="utf-8") as f:
        json.dump(converted, f, indent=2)

    return {
        "samples": len(converted),
        "original_local_nodes": original_nodes,
        "retained_local_nodes": retained_nodes,
        "original_local_edges": original_edges,
        "retained_local_edges": retained_edges,
        "source_view_counts": dict(source_view_counts),
        "output_view_counts": dict(output_view_counts),
        "global_enabled": bool(config.get("global_enabled", True)),
    }


def process_variant(task, variant_key):
    config = VARIANTS[variant_key]
    src = STEP16_BASE / TASK_SOURCE_DIR[task]
    dst = ABLATION_BASE / config["folder"] / TASK_SOURCE_DIR[task]

    if not src.exists():
        raise SystemExit(
            f"Source Step16 folder not found:\n{src}"
        )

    print("=" * 70)
    print("Task:", task)
    print("Variant:", config["name"])
    print("Keep local views:", sorted(config["keep"]))
    print("Source:", src)
    print("Destination:", dst)
    print("=" * 70)

    summary = {
        "task": task,
        "variant": config["name"],
        "keep_local_views": sorted(config["keep"]),
        "global_stream": (
            "UNCHANGED" if config.get("global_enabled", True) else "MASKED"
        ),
        "splits": {},
    }

    for split in ["train.json", "valid.json"]:

        src_file = src / split
        dst_file = dst / split

        if not src_file.is_file():
            raise SystemExit(
                f"Missing source file: {src_file}"
            )

        stats = process_file(
            src_file,
            dst_file,
            config,
        )

        summary["splits"][split] = stats

        print()
        print(split)
        print(json.dumps(stats, indent=2))

    with (dst / "ablation_summary.json").open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(summary, f, indent=2)

    print()
    print("=" * 70)
    print("Ablation dataset created successfully:")
    print(dst)
    print("=" * 70)


def main():
    args = parse_args()
    variant_keys = list(VARIANTS) if args.variant == "all" else [args.variant]
    for variant_key in variant_keys:
        process_variant(args.task, variant_key)


if __name__ == "__main__":
    main()
