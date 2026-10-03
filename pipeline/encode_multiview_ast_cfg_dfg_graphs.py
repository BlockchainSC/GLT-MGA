#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


# Keep large graph payloads and CFG->DFG construction sidecars out of encoded metadata.
DROP_GRAPH_KEYS = {"nodes", "edges", "analysis_nodes"}
SAFE_METADATA_KEYS = {
    "relative_path",
    "source_path",
    "file_path",
    "contract_name",
    "contract_key",
    "contract_kind",
    "function_name",
    "function_kind",
    "function_signature",
    "line_start",
    "line_end",
    "byte_start",
    "byte_length",
    "src",
    "param_count",
    "return_count",
    "modifier_count",
    "visibility",
    "state_mutability",
    "implemented",
    "is_constructor",
    "is_fallback",
    "is_receive",
    "sample_id",
    "graph_key",
    "function_key",
    "function_src_key",
    "sample_key",
    "id",
}


FEATURE_NAMES = [
    "view_id",
    "node_kind_id",
    "node_type_id",
    "token_id",
]


ALLOWED_STATEMENT_AST_TYPES = {
    "AST_FUNCTION",
    "AST_BLOCK",
    "AST_STMT",
    "AST_CONTROL_STMT",
    "AST_DECL_STMT",
    "AST_RETURN_STMT",
    "AST_EXPR_PLACEHOLDER",
}

FORBIDDEN_FULL_AST_TYPES = {
    "Identifier",
    "Literal",
    "MemberAccess",
    "FunctionCall",
    "FunctionCallOptions",
    "BinaryOperation",
    "UnaryOperation",
    "IndexAccess",
    "ElementaryTypeName",
    "TupleExpression",
    "Assignment",
    "VariableDeclaration",
}

FORBIDDEN_AST_EDGE_TYPES = {
    "AST_NEXT_SIBLING",
    "AST_PREV_SIBLING",
}

AST_ALIGN_TYPES = {
    "AST_STMT",
    "AST_CONTROL_STMT",
    "AST_DECL_STMT",
    "AST_RETURN_STMT",
}


class Vocab:
    def __init__(self) -> None:
        self.item_to_id: Dict[str, int] = {}
        self.id_to_item: List[str] = []

    def add(self, item: Any) -> int:
        text = str(item) if item is not None else "<EMPTY>"
        if text == "":
            text = "<EMPTY>"
        if text not in self.item_to_id:
            self.item_to_id[text] = len(self.id_to_item)
            self.id_to_item.append(text)
        return self.item_to_id[text]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size": len(self.id_to_item),
            "item_to_id": self.item_to_id,
            "id_to_item": self.id_to_item,
        }


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}, line {line_no}: {exc}") from exc


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def line_overlap_size(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    if a_start <= 0 or a_end <= 0 or b_start <= 0 or b_end <= 0:
        return 0
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0, end - start + 1)


def get_line_span_from_node(node: Dict[str, Any]) -> Tuple[int, int]:
    return safe_int(node.get("line_start", 0), 0), safe_int(node.get("line_end", 0), 0)


def is_ast_align_node(node: Dict[str, Any]) -> bool:
    return safe_str(node.get("node_type", "")) in AST_ALIGN_TYPES


def is_dfg_stmt_node(node: Dict[str, Any]) -> bool:
    return safe_str(node.get("node_kind", "")) == "STMT"


def best_cfg_match_for_ast(ast_node: Dict[str, Any], cfg_nodes: List[Dict[str, Any]]) -> Optional[int]:
    a_start, a_end = get_line_span_from_node(ast_node)
    best_idx = None
    best_score = 0

    for idx, cfg_node in enumerate(cfg_nodes):
        c_start, c_end = get_line_span_from_node(cfg_node)
        score = line_overlap_size(a_start, a_end, c_start, c_end)

        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx if best_score > 0 else None


def as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def make_function_key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str, str, str]:
    """
    Same function matching key.
    Since AST/CFG/DFG are produced from the same candidate inventory,
    this key should match across all three views.
    """
    return (
        safe_str(row.get("relative_path", "")),
        safe_str(row.get("contract_name", "")),
        safe_str(row.get("function_name", "")),
        safe_str(row.get("function_kind", "")),
        safe_str(row.get("line_start", "")),
        safe_str(row.get("line_end", "")),
        safe_str(row.get("param_count", "")),
    )


def load_graph_index(path: Path, view_name: str) -> Tuple[Dict[Tuple[str, ...], Dict[str, Any]], List[Dict[str, Any]]]:
    index: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    duplicates: List[Dict[str, Any]] = []

    for row in tqdm(load_jsonl(path), desc=f"Indexing {view_name}", unit="graph"):
        key = make_function_key(row)
        if key in index:
            duplicates.append({
                "view": view_name,
                "key": key,
                "relative_path": row.get("relative_path", ""),
                "contract_name": row.get("contract_name", ""),
                "function_name": row.get("function_name", ""),
            })
            continue
        index[key] = row

    return index, duplicates


def get_node_original_id(node: Dict[str, Any], fallback: int) -> int:
    for key in ("id", "node_id", "local_id"):
        if key in node:
            return safe_int(node.get(key), fallback)
    return fallback


def get_edge_src_dst(edge: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    src_keys = ("src", "source", "from", "u")
    dst_keys = ("dst", "target", "to", "v")

    src = None
    dst = None

    for key in src_keys:
        if key in edge:
            src = safe_int(edge.get(key), -1)
            break

    for key in dst_keys:
        if key in edge:
            dst = safe_int(edge.get(key), -1)
            break

    if src is None or dst is None or src < 0 or dst < 0:
        return None

    return src, dst


def get_edge_type(edge: Dict[str, Any], default: str = "EDGE") -> str:
    for key in ("edge_type", "type", "label", "kind"):
        value = safe_str(edge.get(key, ""))
        if value:
            return value
    return default


def get_node_kind(view: str, node: Dict[str, Any]) -> str:
    if view == "AST":
        return "AST_NODE"

    if view == "CFG":
        return "CFG_STMT"

    if view == "DFG":
        kind = safe_str(node.get("node_kind", ""))
        if kind == "VAR":
            return "DFG_VAR"
        return "DFG_STMT"

    return "NODE"


def get_node_type(view: str, node: Dict[str, Any]) -> str:
    if view == "AST":
        node_type = safe_str(node.get("node_type", "")) or "AST_UNKNOWN"
        original_type = safe_str(node.get("original_node_type", ""))

        # Statement-lite AST should only contain these abstract types.
        if node_type in ALLOWED_STATEMENT_AST_TYPES:
            return node_type

        # If old full AST details appear, do not silently encode them.
        if node_type in FORBIDDEN_FULL_AST_TYPES or original_type in FORBIDDEN_FULL_AST_TYPES:
            return "AST_FORBIDDEN_DETAIL"

        return "AST_UNKNOWN_STATEMENT"

    if view == "DFG" and safe_str(node.get("node_kind", "")) == "VAR":
        scope = safe_str(node.get("var_scope", "VAR"))
        return f"VAR_{scope}"

    for key in (
        "normalized_type",
        "node_type",
        "ast_type",
        "raw_node_type",
        "type",
        "label",
        "kind",
        "name",
    ):
        value = safe_str(node.get(key, ""))
        if value:
            return value

    return "UNKNOWN"


def make_token(
    view: str,
    node: Dict[str, Any],
    identifier_mode: str,
) -> str:
    """
    typed mode avoids raw identifier overfitting.
    raw mode preserves variable names.
    """
    if view == "AST":
        return "AST_TOKEN"

    if view == "CFG":
        return "CFG_TOKEN"

    if view == "DFG":
        if safe_str(node.get("node_kind", "")) == "VAR":
            return "DFG_VAR_TOKEN"
        return "DFG_STMT_TOKEN"

    return get_node_type(view, node)


def make_node_features(
    view: str,
    node_kind_id: int,
    node_type_id: int,
    token_id: int,
    node: Dict[str, Any],
    view_vocab: Vocab,
) -> List[int]:
    view_id = view_vocab.add(view)

    return [
        view_id,
        node_kind_id,
        node_type_id,
        token_id,
    ]


def select_anchor_nodes(
    view: str,
    graph: Dict[str, Any],
    id_map: Dict[int, int],
    anchor_mode: str,
) -> List[int]:
    view_node_ids = list(id_map.values())
    if anchor_mode == "none":
        return []
    if anchor_mode == "all":
        return view_node_ids

    selected: List[int] = []
    graph_nodes = graph.get("nodes", []) or []

    if view == "AST":
        if view_node_ids:
            return [view_node_ids[0]]
        return []

    if view == "CFG":
        for old_id, new_id in id_map.items():
            for node in graph_nodes:
                if get_node_original_id(node, -999) == old_id and bool(node.get("is_entry", False)):
                    selected.append(new_id)
                    break
        if selected:
            return selected
        return [view_node_ids[0]] if view_node_ids else []

    if view == "DFG":
        entry_cfg_id = safe_int(graph.get("entry_local_node_id", -1), -1)
        for old_id, new_id in id_map.items():
            for node in graph_nodes:
                if get_node_original_id(node, -999) != old_id:
                    continue
                if entry_cfg_id >= 0 and safe_int(node.get("source_cfg_node_id", -1), -1) == entry_cfg_id:
                    selected.append(new_id)
                    break
                if bool(node.get("is_entry", False)):
                    selected.append(new_id)
                    break
        if selected:
            return selected
        return [view_node_ids[0]] if view_node_ids else []

    return [view_node_ids[0]] if view_node_ids else []


def build_metadata(row: Dict[str, Any], task: str) -> Dict[str, Any]:
    meta = {}
    for key in SAFE_METADATA_KEYS:
        if key in row and key not in DROP_GRAPH_KEYS:
            meta[key] = row[key]

    meta["encoded_task"] = task
    return meta


def encode_multiview_graph(
    ast_graph: Dict[str, Any],
    cfg_graph: Dict[str, Any],
    dfg_graph: Dict[str, Any],
    vocabs: Dict[str, Vocab],
    task: str,
    identifier_mode: str,
    anchor_mode: str,
    include_debug: bool,
) -> Dict[str, Any]:

    node_features: List[List[int]] = []
    node_view_ids: List[int] = []
    node_type_ids: List[int] = []
    node_token_ids: List[int] = []
    node_debug: List[Dict[str, Any]] = []

    edge_index: List[List[int]] = []
    edge_type_ids: List[int] = []
    edge_debug: List[Dict[str, Any]] = []

    view_vocab = vocabs["view"]
    node_kind_vocab = vocabs["node_kind"]
    node_type_vocab = vocabs["node_type"]
    token_vocab = vocabs["token"]
    edge_type_vocab = vocabs["edge_type"]

    # Function anchor node
    anchor_node = {
        "id": 0,
        "node_kind": "FUNC_ANCHOR",
        "normalized_type": "FUNC_ANCHOR",
        "expression_text": "",
    }

    anchor_kind_id = node_kind_vocab.add("FUNC_ANCHOR")
    anchor_type_id = node_type_vocab.add("FUNC_ANCHOR")
    anchor_token_id = token_vocab.add("FUNC_ANCHOR")

    node_features.append(
        make_node_features(
            view="ANCHOR",
            node_kind_id=anchor_kind_id,
            node_type_id=anchor_type_id,
            token_id=anchor_token_id,
            node=anchor_node,
            view_vocab=view_vocab,
        )
    )
    node_view_ids.append(view_vocab.add("ANCHOR"))
    node_type_ids.append(anchor_type_id)
    node_token_ids.append(anchor_token_id)

    if include_debug:
        node_debug.append({
            "new_id": 0,
            "view": "ANCHOR",
            "original_id": -1,
            "node_kind": "FUNC_ANCHOR",
            "node_type": "FUNC_ANCHOR",
            "token": "FUNC_ANCHOR",
            "expression_text": "",
        })

    id_maps: Dict[str, Dict[int, int]] = {
        "AST": {},
        "CFG": {},
        "DFG": {},
    }

    view_graphs = [
        ("AST", ast_graph),
        ("CFG", cfg_graph),
        ("DFG", dfg_graph),
    ]

    ast_forbidden_node_count = 0
    ast_unknown_node_count = 0
    ast_node_types_seen = defaultdict(int)
    ast_forbidden_edge_count = 0

    # Add nodes
    for view, graph in view_graphs:
        nodes = graph.get("nodes", []) or []

        for idx, node in enumerate(nodes):
            original_id = get_node_original_id(node, idx)
            new_id = len(node_features)

            node_kind = get_node_kind(view, node)
            node_type = get_node_type(view, node)
            token = make_token(view, node, identifier_mode=identifier_mode)

            if view == "AST":
                ast_node_types_seen[node_type] += 1

                if node_type == "AST_FORBIDDEN_DETAIL":
                    ast_forbidden_node_count += 1

                if node_type == "AST_UNKNOWN_STATEMENT":
                    ast_unknown_node_count += 1

            node_kind_id = node_kind_vocab.add(node_kind)
            node_type_id = node_type_vocab.add(f"{view}::{node_type}")
            token_id = token_vocab.add(token)

            id_maps[view][original_id] = new_id

            features = make_node_features(
                view=view,
                node_kind_id=node_kind_id,
                node_type_id=node_type_id,
                token_id=token_id,
                node=node,
                view_vocab=view_vocab,
            )

            node_features.append(features)
            node_view_ids.append(view_vocab.add(view))
            node_type_ids.append(node_type_id)
            node_token_ids.append(token_id)

            if include_debug:
                node_debug.append({
                    "new_id": new_id,
                    "view": view,
                    "original_id": original_id,
                    "node_kind": node_kind,
                    "node_type": node_type,
                    "token": token,
                    "expression_text": safe_str(node.get("expression_text", ""))[:300],
                })

    def add_encoded_edge(src: int, dst: int, edge_type: str) -> None:
        edge_index.append([src, dst])
        edge_type_ids.append(edge_type_vocab.add(edge_type))

        if include_debug:
            edge_debug.append({
                "src": src,
                "dst": dst,
                "edge_type": edge_type,
            })

    # Add original view edges
    for view, graph in view_graphs:
        for edge in graph.get("edges", []) or []:
            pair = get_edge_src_dst(edge)
            if pair is None:
                continue

            old_src, old_dst = pair
            if old_src not in id_maps[view] or old_dst not in id_maps[view]:
                continue

            new_src = id_maps[view][old_src]
            new_dst = id_maps[view][old_dst]
            raw_edge_type = get_edge_type(edge)

            if view == "AST" and raw_edge_type in FORBIDDEN_AST_EDGE_TYPES:
                ast_forbidden_edge_count += 1
                continue

            edge_type = f"{view}::{raw_edge_type}"

            add_encoded_edge(new_src, new_dst, edge_type)

    # Add cross-view alignment edges
    alignment_edge_counts = {
        "AST_CFG": 0,
        "CFG_DFG": 0,
    }

    ast_nodes = ast_graph.get("nodes", []) or []
    cfg_nodes = cfg_graph.get("nodes", []) or []
    dfg_nodes = dfg_graph.get("nodes", []) or []

    ast_node_by_old_id = {
        get_node_original_id(n, idx): n
        for idx, n in enumerate(ast_nodes)
    }
    cfg_node_by_old_id = {
        get_node_original_id(n, idx): n
        for idx, n in enumerate(cfg_nodes)
    }
    dfg_node_by_old_id = {
        get_node_original_id(n, idx): n
        for idx, n in enumerate(dfg_nodes)
    }

    # ---------- AST <-> CFG alignment by line overlap ----------
    for ast_old_id, ast_new_id in id_maps["AST"].items():
        ast_node = ast_node_by_old_id.get(ast_old_id)
        if ast_node is None:
            continue

        if not is_ast_align_node(ast_node):
            continue

        cfg_idx = best_cfg_match_for_ast(ast_node, cfg_nodes)
        if cfg_idx is None:
            continue

        cfg_old_id = get_node_original_id(cfg_nodes[cfg_idx], cfg_idx)
        if cfg_old_id not in id_maps["CFG"]:
            continue

        cfg_new_id = id_maps["CFG"][cfg_old_id]

        add_encoded_edge(ast_new_id, cfg_new_id, "AST_TO_CFG_ALIGN")
        add_encoded_edge(cfg_new_id, ast_new_id, "CFG_TO_AST_ALIGN")
        alignment_edge_counts["AST_CFG"] += 2

    # ---------- CFG <-> DFG alignment by source_cfg_node_id ----------
    cfg_by_source_id = {}

    for cfg_old_id, cfg_new_id in id_maps["CFG"].items():
        cfg_node = cfg_node_by_old_id.get(cfg_old_id)
        if cfg_node is None:
            continue

        sid = safe_int(cfg_node.get("source_cfg_node_id", cfg_old_id), cfg_old_id)
        cfg_by_source_id[sid] = cfg_new_id

    for dfg_old_id, dfg_new_id in id_maps["DFG"].items():
        dfg_node = dfg_node_by_old_id.get(dfg_old_id)
        if dfg_node is None:
            continue

        if not is_dfg_stmt_node(dfg_node):
            continue

        sid = safe_int(dfg_node.get("source_cfg_node_id", -1), -1)
        if sid < 0 or sid not in cfg_by_source_id:
            continue

        cfg_new_id = cfg_by_source_id[sid]

        add_encoded_edge(cfg_new_id, dfg_new_id, "CFG_TO_DFG_ALIGN")
        add_encoded_edge(dfg_new_id, cfg_new_id, "DFG_TO_CFG_ALIGN")
        alignment_edge_counts["CFG_DFG"] += 2

    # Add anchor fusion edges
    for view, graph in view_graphs:
        selected = select_anchor_nodes(
            view=view,
            graph=graph,
            id_map=id_maps[view],
            anchor_mode=anchor_mode,
        )

        for new_id in selected:
            add_encoded_edge(0, new_id, f"ANCHOR_TO_{view}")
            add_encoded_edge(new_id, 0, f"{view}_TO_ANCHOR")

    metadata = build_metadata(dfg_graph, task=task)
    metadata["ast_graph_mode"] = safe_str(ast_graph.get("graph_mode", ""))
    metadata["ast_encoding_level"] = safe_str(ast_graph.get("ast_encoding_level", ""))
    metadata["ast_expression_detail"] = safe_str(ast_graph.get("ast_expression_detail", ""))
    metadata["ast_node_count"] = len(ast_graph.get("nodes", []) or [])
    metadata["ast_edge_count"] = len(ast_graph.get("edges", []) or [])

    encoded = {
        "graph_type": "encoded_function_multiview_ast_cfg_dfg",
        "graph_mode": "aligned_multiview_with_function_anchor",
        "task": task,
        "metadata": metadata,

        "num_nodes": len(node_features),
        "num_edges": len(edge_index),

        "node_feature_names": FEATURE_NAMES,
        "node_features": node_features,

        "node_view_ids": node_view_ids,
        "node_type_ids": node_type_ids,
        "node_token_ids": node_token_ids,

        "edge_index": edge_index,
        "edge_type_ids": edge_type_ids,

        "view_node_counts": {
            "AST": len(ast_graph.get("nodes", []) or []),
            "CFG": len(cfg_graph.get("nodes", []) or []),
            "DFG": len(dfg_graph.get("nodes", []) or []),
        },
        "view_edge_counts": {
            "AST": len(ast_graph.get("edges", []) or []),
            "CFG": len(cfg_graph.get("edges", []) or []),
            "DFG": len(dfg_graph.get("edges", []) or []),
        },
        "ast_statement_lite_check": {
            "forbidden_node_count": ast_forbidden_node_count,
            "unknown_node_count": ast_unknown_node_count,
            "forbidden_edge_count": ast_forbidden_edge_count,
            "node_types_seen": dict(ast_node_types_seen),
        },
        "alignment_edge_counts": alignment_edge_counts,
        "anchor_mode": anchor_mode,
        "identifier_mode": identifier_mode,
    }

    if include_debug:
        encoded["node_debug"] = node_debug
        encoded["edge_debug"] = edge_debug

    # Preserve label only if it already exists.
    for label_key in ("label", "target", "y", "vulnerability_label"):
        if label_key in dfg_graph:
            encoded[label_key] = dfg_graph[label_key]

    return encoded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encode AST + CFG + DFG function graphs into numeric multi-view graph JSONL."
    )

    parser.add_argument("--ast-graphs", type=Path, required=True)
    parser.add_argument("--cfg-graphs", type=Path, required=True)
    parser.add_argument("--dfg-graphs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)

    parser.add_argument("--task", type=str, required=True, choices=["reentrancy", "timestamp", "generic"])
    parser.add_argument("--max-graphs", type=int, default=0)

    parser.add_argument(
        "--identifier-mode",
        choices=["typed", "raw"],
        default="typed",
        help="typed avoids raw identifier overfitting; raw preserves exact variable names.",
    )

    parser.add_argument(
        "--anchor-mode",
        choices=["all", "root", "none"],
        default="root",
        help="How to connect AST/CFG/DFG views using function anchor.",
    )

    parser.add_argument(
        "--include-debug",
        action="store_true",
        help="Save node_debug and edge_debug strings for inspection.",
    )

    args = parser.parse_args()

    ast_path = args.ast_graphs.resolve()
    cfg_path = args.cfg_graphs.resolve()
    dfg_path = args.dfg_graphs.resolve()
    output_dir = args.output_dir.resolve()

    if not ast_path.is_file():
        raise SystemExit(f"AST graphs file not found: {ast_path}")
    if not cfg_path.is_file():
        raise SystemExit(f"CFG graphs file not found: {cfg_path}")
    if not dfg_path.is_file():
        raise SystemExit(f"DFG graphs file not found: {dfg_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    encoded_path = output_dir / "encoded_multiview_graphs.jsonl"
    vocab_path = output_dir / "vocab.json"
    summary_path = output_dir / "summary.json"
    missing_report_path = output_dir / "missing_report.json"

    ast_index, ast_dups = load_graph_index(ast_path, "AST")
    cfg_index, cfg_dups = load_graph_index(cfg_path, "CFG")

    vocabs = {
        "view": Vocab(),
        "node_kind": Vocab(),
        "node_type": Vocab(),
        "token": Vocab(),
        "edge_type": Vocab(),
    }

    # Initialize common view ids in stable order.
    for view in ["ANCHOR", "AST", "CFG", "DFG"]:
        vocabs["view"].add(view)

    total_dfg_seen = 0
    encoded_count = 0
    missing_ast = 0
    missing_cfg = 0
    missing_examples: List[Dict[str, Any]] = []

    total_nodes = 0
    total_edges = 0
    total_ast_nodes = 0
    total_cfg_nodes = 0
    total_dfg_nodes = 0
    total_ast_forbidden_nodes = 0
    total_ast_unknown_nodes = 0
    total_ast_forbidden_edges = 0
    total_ast_cfg_align_edges = 0
    total_cfg_dfg_align_edges = 0

    with encoded_path.open("w", encoding="utf-8") as out_f:
        for dfg_graph in tqdm(load_jsonl(dfg_path), desc="Encoding multiview graphs", unit="graph"):
            if args.max_graphs > 0 and encoded_count >= args.max_graphs:
                break

            total_dfg_seen += 1
            key = make_function_key(dfg_graph)

            ast_graph = ast_index.get(key)
            cfg_graph = cfg_index.get(key)

            if ast_graph is None:
                missing_ast += 1
            if cfg_graph is None:
                missing_cfg += 1

            if ast_graph is None or cfg_graph is None:
                if len(missing_examples) < 30:
                    missing_examples.append({
                        "key": key,
                        "relative_path": dfg_graph.get("relative_path", ""),
                        "contract_name": dfg_graph.get("contract_name", ""),
                        "function_name": dfg_graph.get("function_name", ""),
                        "missing_ast": ast_graph is None,
                        "missing_cfg": cfg_graph is None,
                    })
                continue

            encoded = encode_multiview_graph(
                ast_graph=ast_graph,
                cfg_graph=cfg_graph,
                dfg_graph=dfg_graph,
                vocabs=vocabs,
                task=args.task,
                identifier_mode=args.identifier_mode,
                anchor_mode=args.anchor_mode,
                include_debug=args.include_debug,
            )

            out_f.write(json.dumps(encoded, ensure_ascii=False) + "\n")

            encoded_count += 1
            total_nodes += encoded["num_nodes"]
            total_edges += encoded["num_edges"]
            total_ast_nodes += encoded["view_node_counts"]["AST"]
            total_cfg_nodes += encoded["view_node_counts"]["CFG"]
            total_dfg_nodes += encoded["view_node_counts"]["DFG"]
            check = encoded.get("ast_statement_lite_check", {})
            total_ast_forbidden_nodes += int(check.get("forbidden_node_count", 0))
            total_ast_unknown_nodes += int(check.get("unknown_node_count", 0))
            total_ast_forbidden_edges += int(check.get("forbidden_edge_count", 0))
            align_counts = encoded.get("alignment_edge_counts", {})
            total_ast_cfg_align_edges += int(align_counts.get("AST_CFG", 0))
            total_cfg_dfg_align_edges += int(align_counts.get("CFG_DFG", 0))

    vocab_obj = {
        "feature_names": FEATURE_NAMES,
        "identifier_mode": args.identifier_mode,
        "anchor_mode": args.anchor_mode,
        "vocabs": {name: vocab.to_dict() for name, vocab in vocabs.items()},
    }
    save_json(vocab_path, vocab_obj)

    missing_report = {
        "missing_ast": missing_ast,
        "missing_cfg": missing_cfg,
        "missing_examples": missing_examples,
        "ast_duplicate_count": len(ast_dups),
        "cfg_duplicate_count": len(cfg_dups),
        "ast_duplicates_sample": ast_dups[:20],
        "cfg_duplicates_sample": cfg_dups[:20],
    }
    save_json(missing_report_path, missing_report)

    summary = {
        "stage": "step8_encode_multiview_ast_cfg_dfg",
        "task": args.task,
        "ast_graphs": str(ast_path),
        "cfg_graphs": str(cfg_path),
        "dfg_graphs": str(dfg_path),
        "encoded_graphs": str(encoded_path),
        "vocab_json": str(vocab_path),
        "missing_report_json": str(missing_report_path),

        "total_dfg_seen": total_dfg_seen,
        "encoded_graphs_built": encoded_count,
        "missing_ast": missing_ast,
        "missing_cfg": missing_cfg,

        "total_encoded_nodes": total_nodes,
        "total_encoded_edges": total_edges,
        "avg_nodes_per_graph": total_nodes / encoded_count if encoded_count else 0.0,
        "avg_edges_per_graph": total_edges / encoded_count if encoded_count else 0.0,

        "total_ast_nodes": total_ast_nodes,
        "total_cfg_nodes": total_cfg_nodes,
        "total_dfg_nodes": total_dfg_nodes,

        "vocab_sizes": {
            name: len(vocab.id_to_item)
            for name, vocab in vocabs.items()
        },
        "ast_statement_lite_validation": {
            "total_forbidden_ast_nodes": total_ast_forbidden_nodes,
            "total_unknown_ast_nodes": total_ast_unknown_nodes,
            "total_forbidden_ast_edges_skipped": total_ast_forbidden_edges,
            "expected_ast_node_types": sorted(ALLOWED_STATEMENT_AST_TYPES),
            "forbidden_full_ast_types": sorted(FORBIDDEN_FULL_AST_TYPES),
        },
        "alignment_edge_counts": {
            "total_ast_cfg_align_edges": total_ast_cfg_align_edges,
            "total_cfg_dfg_align_edges": total_cfg_dfg_align_edges,
            "avg_ast_cfg_align_edges": total_ast_cfg_align_edges / encoded_count if encoded_count else 0.0,
            "avg_cfg_dfg_align_edges": total_cfg_dfg_align_edges / encoded_count if encoded_count else 0.0,
        },

        "identifier_mode": args.identifier_mode,
        "anchor_mode": args.anchor_mode,
        "note": (
            "This stage encodes AST, CFG, and DFG views into numeric graph format. "
            "It does not create vulnerability labels or train/valid/test splits."
        ),
    }

    save_json(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
