#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.read_text(encoding="latin-1", errors="replace")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_src_field(src: str) -> Tuple[int, int, int]:
    try:
        s, l, i = src.split(":")
        return int(s), int(l), int(i)
    except Exception:
        return 0, 0, -1


def build_line_byte_index(text: str) -> List[int]:
    line_starts = [0]
    current = 0
    for line in text.splitlines(keepends=True):
        current += len(line.encode("utf-8", errors="replace"))
        line_starts.append(current)
    return line_starts


def byte_offset_to_line(line_starts: List[int], byte_offset: int) -> int:
    idx = bisect.bisect_right(line_starts, byte_offset) - 1
    return max(1, idx + 1)


def compute_line_range_from_src(src: str, line_starts: List[int]) -> Tuple[int, int]:
    byte_start, byte_length, _ = parse_src_field(src)
    byte_end = byte_start + max(0, byte_length - 1)
    line_start = byte_offset_to_line(line_starts, byte_start)
    line_end = byte_offset_to_line(line_starts, max(byte_start, byte_end))
    return line_start, line_end


def normalize_function_identity(
    node: Dict[str, Any],
    current_contract_name: str = "",
) -> Tuple[str, str, bool, bool, bool]:
    kind = node.get("kind", "") or ""
    name = node.get("name", "") or ""

    is_constructor = (
        bool(node.get("isConstructor", False))
        or kind == "constructor"
        or (
            current_contract_name
            and name == current_contract_name
            and kind == ""
        )
    )

    is_receive = (not is_constructor) and (kind == "receive")

    is_fallback = (
        (not is_constructor)
        and (not is_receive)
        and (kind == "fallback" or name == "")
    )

    if is_constructor:
        return "__constructor__", "constructor", True, False, False
    if is_receive:
        return "__receive__", "receive", False, False, True
    if is_fallback:
        return "__fallback__", "fallback", False, True, False

    return name if name else "__anonymous__", (kind or "function"), False, False, False


def iter_ast_children(node: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    children: List[Tuple[str, Dict[str, Any]]] = []
    for key, value in node.items():
        if isinstance(value, dict) and "nodeType" in value:
            children.append((key, value))
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict) and "nodeType" in item:
                    children.append((f"{key}[{idx}]", item))
    return children


def walk_function_nodes(
    node: Any,
    current_contract_name: str,
    current_contract_kind: str,
    line_starts: List[int],
    results: List[Dict[str, Any]],
) -> None:
    if isinstance(node, dict):
        node_type = node.get("nodeType", "")

        if node_type == "ContractDefinition":
            current_contract_name = node.get("name", "") or ""
            current_contract_kind = node.get("contractKind", "") or ""

        elif node_type == "FunctionDefinition":
            function_name, function_kind, is_constructor, is_fallback, is_receive = \
                normalize_function_identity(node, current_contract_name)

            src = node.get("src", "0:0:-1")
            line_start, line_end = compute_line_range_from_src(src, line_starts)

            results.append({
                "contract_name": current_contract_name,
                "contract_kind": current_contract_kind,
                "function_name": function_name,
                "function_kind": function_kind,
                "is_constructor": is_constructor,
                "is_fallback": is_fallback,
                "is_receive": is_receive,
                "src": src,
                "line_start": line_start,
                "line_end": line_end,
                "node": node,
            })

        for value in node.values():
            walk_function_nodes(
                value,
                current_contract_name=current_contract_name,
                current_contract_kind=current_contract_kind,
                line_starts=line_starts,
                results=results,
            )

    elif isinstance(node, list):
        for item in node:
            walk_function_nodes(
                item,
                current_contract_name=current_contract_name,
                current_contract_kind=current_contract_kind,
                line_starts=line_starts,
                results=results,
            )


def find_matching_function_node(
    ast_obj: Dict[str, Any],
    candidate_row: Dict[str, Any],
    source_text: str,
) -> Optional[Dict[str, Any]]:
    line_starts = build_line_byte_index(source_text)
    function_nodes: List[Dict[str, Any]] = []

    walk_function_nodes(
        ast_obj,
        current_contract_name="",
        current_contract_kind="",
        line_starts=line_starts,
        results=function_nodes,
    )

    target_contract = candidate_row.get("contract_name", "")
    target_function = candidate_row.get("function_name", "")
    target_src = candidate_row.get("src", "")
    target_line_start = int(candidate_row.get("line_start", 0) or 0)
    target_line_end = int(candidate_row.get("line_end", 0) or 0)

    # Strongest match: contract + function + src
    for item in function_nodes:
        if (
            item["contract_name"] == target_contract
            and item["function_name"] == target_function
            and item["src"] == target_src
        ):
            return item["node"]

    # Fallback: contract + function + line range
    for item in function_nodes:
        if (
            item["contract_name"] == target_contract
            and item["function_name"] == target_function
            and item["line_start"] == target_line_start
            and item["line_end"] == target_line_end
        ):
            return item["node"]

    return None


def extract_skeleton_node_features(
    node_type: str,
    src: str = "",
    original_node_type: str = "",
) -> Dict[str, Any]:
    return {
        "ast_id": None,
        "node_type": node_type,
        "original_node_type": "",
        "name": "",
        "kind": "",
        "operator": "",
        "value": "",
        "member_name": "",
        "visibility": "",
        "state_mutability": "",
        "type_string": "",
        "src": src,
        "implemented": None,
        "is_constructor": None,
    }


def extract_node_features(node: Dict[str, Any]) -> Dict[str, Any]:
    original_node_type = node.get("nodeType", "") or ""
    return extract_skeleton_node_features(
        node_type=original_node_type,
        src=node.get("src", "") or "",
        original_node_type=original_node_type,
    )


def is_block(node: Dict[str, Any]) -> bool:
    return node.get("nodeType") == "Block"


def is_control_stmt(node: Dict[str, Any]) -> bool:
    return node.get("nodeType") in {
        "IfStatement",
        "ForStatement",
        "WhileStatement",
        "DoWhileStatement",
        "TryStatement",
    }


def is_decl_stmt(node: Dict[str, Any]) -> bool:
    return node.get("nodeType") == "VariableDeclarationStatement"


def is_return_stmt(node: Dict[str, Any]) -> bool:
    return node.get("nodeType") == "Return"


def is_expression_stmt(node: Dict[str, Any]) -> bool:
    return node.get("nodeType") in {
        "ExpressionStatement",
        "EmitStatement",
        "RevertStatement",
        "UncheckedBlock",
    }


def make_skeleton_type(node: Dict[str, Any]) -> str:
    nt = node.get("nodeType", "")

    if nt == "FunctionDefinition":
        return "AST_FUNCTION"
    if nt == "Block":
        return "AST_BLOCK"
    if is_control_stmt(node):
        return "AST_CONTROL_STMT"
    if is_decl_stmt(node):
        return "AST_DECL_STMT"
    if is_return_stmt(node):
        return "AST_RETURN_STMT"
    if is_expression_stmt(node):
        return "AST_STMT"

    return "AST_STMT"


def build_statement_lite_ast_graph(
    root_node: Dict[str, Any],
    line_starts: List[int],
    add_sibling_edges: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def add_node(
        node_type: str,
        original_node: Optional[Dict[str, Any]],
        parent_id: Optional[int],
        relation_to_parent: str,
        child_index: int,
        depth: int,
    ) -> int:
        local_id = len(nodes)
        src = original_node.get("src", "") if isinstance(original_node, dict) else ""
        if src:
            node_line_start, node_line_end = compute_line_range_from_src(src, line_starts)
        else:
            node_line_start, node_line_end = 0, 0

        rec = {
            "id": local_id,
            "parent_id": parent_id,
            "depth": depth,
            "child_index": child_index,
            "relation_to_parent": relation_to_parent,
            "line_start": node_line_start,
            "line_end": node_line_end,
            "child_count": 0,
            "is_leaf": True,
        }
        rec.update(
            extract_skeleton_node_features(
                node_type=node_type,
                src=src,
                original_node_type=(
                    original_node.get("nodeType", "")
                    if isinstance(original_node, dict)
                    else ""
                ),
            )
        )
        nodes.append(rec)

        if parent_id is not None:
            edges.append({
                "src": parent_id,
                "dst": local_id,
                "edge_type": "AST_CHILD",
                "relation": relation_to_parent,
            })
            edges.append({
                "src": local_id,
                "dst": parent_id,
                "edge_type": "AST_PARENT",
                "relation": relation_to_parent,
            })

        return local_id

    def add_placeholder(
        parent_id: int,
        relation: str,
        depth: int,
        child_index: int,
        placeholder_type: str = "AST_EXPR_PLACEHOLDER",
        original_node: Optional[Dict[str, Any]] = None,
    ) -> int:
        return add_node(
            node_type=placeholder_type,
            original_node=original_node,
            parent_id=parent_id,
            relation_to_parent=relation,
            child_index=child_index,
            depth=depth,
        )

    def connect_siblings(child_ids: List[int]) -> None:
        if not add_sibling_edges:
            return
        for left, right in zip(child_ids, child_ids[1:]):
            edges.append({
                "src": left,
                "dst": right,
                "edge_type": "AST_NEXT_SIBLING",
                "relation": "",
            })
            edges.append({
                "src": right,
                "dst": left,
                "edge_type": "AST_PREV_SIBLING",
                "relation": "",
            })

    def get_block_statements(block_node: Dict[str, Any]) -> List[Dict[str, Any]]:
        stmts = block_node.get("statements", [])
        if isinstance(stmts, list):
            return [
                s for s in stmts
                if isinstance(s, dict) and "nodeType" in s
            ]
        return []

    def add_block(
        block_node: Dict[str, Any],
        parent_id: int,
        relation: str,
        depth: int,
        child_index: int,
    ) -> int:
        block_id = add_node(
            node_type="AST_BLOCK",
            original_node=block_node,
            parent_id=parent_id,
            relation_to_parent=relation,
            child_index=child_index,
            depth=depth,
        )

        child_ids: List[int] = []
        for idx, stmt in enumerate(get_block_statements(block_node)):
            child_ids.append(add_statement(stmt, block_id, "stmt", depth + 1, idx))

        connect_siblings(child_ids)

        nodes[block_id]["child_count"] = len(child_ids)
        nodes[block_id]["is_leaf"] = len(child_ids) == 0
        return block_id

    def add_statement(
        stmt: Dict[str, Any],
        parent_id: int,
        relation: str,
        depth: int,
        child_index: int,
    ) -> int:
        nt = stmt.get("nodeType", "")
        skel_type = make_skeleton_type(stmt)

        stmt_id = add_node(
            node_type=skel_type,
            original_node=stmt,
            parent_id=parent_id,
            relation_to_parent=relation,
            child_index=child_index,
            depth=depth,
        )

        child_ids: List[int] = []

        if is_control_stmt(stmt):
            # Hide condition content.
            child_ids.append(
                add_placeholder(
                    parent_id=stmt_id,
                    relation="condition",
                    depth=depth + 1,
                    child_index=len(child_ids),
                    placeholder_type="AST_EXPR_PLACEHOLDER",
                    original_node=stmt,
                )
            )

            true_body = stmt.get("trueBody") or stmt.get("body")
            if isinstance(true_body, dict) and "nodeType" in true_body:
                if is_block(true_body):
                    child_ids.append(
                        add_block(
                            true_body,
                            stmt_id,
                            "true_body",
                            depth + 1,
                            len(child_ids),
                        )
                    )
                else:
                    child_ids.append(
                        add_statement(
                            true_body,
                            stmt_id,
                            "true_body",
                            depth + 1,
                            len(child_ids),
                        )
                    )

            false_body = stmt.get("falseBody")
            if isinstance(false_body, dict) and "nodeType" in false_body:
                if is_block(false_body):
                    child_ids.append(
                        add_block(
                            false_body,
                            stmt_id,
                            "false_body",
                            depth + 1,
                            len(child_ids),
                        )
                    )
                else:
                    child_ids.append(
                        add_statement(
                            false_body,
                            stmt_id,
                            "false_body",
                            depth + 1,
                            len(child_ids),
                        )
                    )

        elif is_return_stmt(stmt):
            # Do not expand returned expression.
            if stmt.get("expression") is not None:
                child_ids.append(
                    add_placeholder(
                        stmt_id,
                        "return_expr",
                        depth + 1,
                        0,
                        original_node=stmt,
                    )
                )

        elif is_decl_stmt(stmt):
            # Do not expand variable names/types/init expression.
            if stmt.get("initialValue") is not None:
                child_ids.append(
                    add_placeholder(
                        stmt_id,
                        "decl_init_expr",
                        depth + 1,
                        0,
                        original_node=stmt,
                    )
                )

        elif is_expression_stmt(stmt):
            # Do not expand expression tree.
            child_ids.append(
                add_placeholder(
                    stmt_id,
                    "expr",
                    depth + 1,
                    0,
                    original_node=stmt,
                )
            )

        elif nt == "Block":
            for idx, inner_stmt in enumerate(get_block_statements(stmt)):
                child_ids.append(add_statement(inner_stmt, stmt_id, "stmt", depth + 1, idx))

        else:
            # Unknown statement-level node: keep only generic expression placeholder.
            child_ids.append(
                add_placeholder(
                    stmt_id,
                    "unknown_expr",
                    depth + 1,
                    0,
                    original_node=stmt,
                )
            )

        connect_siblings(child_ids)

        nodes[stmt_id]["child_count"] = len(child_ids)
        nodes[stmt_id]["is_leaf"] = len(child_ids) == 0
        return stmt_id

    func_id = add_node(
        node_type="AST_FUNCTION",
        original_node=root_node,
        parent_id=None,
        relation_to_parent="ROOT",
        child_index=0,
        depth=0,
    )

    child_ids: List[int] = []

    body = root_node.get("body")
    if isinstance(body, dict) and is_block(body):
        child_ids.append(add_block(body, func_id, "body", 1, 0))
    else:
        child_ids.append(
            add_node(
                node_type="AST_BLOCK",
                original_node=None,
                parent_id=func_id,
                relation_to_parent="body",
                child_index=0,
                depth=1,
            )
        )

    connect_siblings(child_ids)

    nodes[func_id]["child_count"] = len(child_ids)
    nodes[func_id]["is_leaf"] = len(child_ids) == 0

    return nodes, edges


def build_ast_graph(
    root_node: Dict[str, Any],
    line_starts: List[int],
    add_sibling_edges: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return build_statement_lite_ast_graph(
        root_node=root_node,
        line_starts=line_starts,
        add_sibling_edges=add_sibling_edges,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build function-level AST graphs from selected candidate functions."
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        required=True,
        help="Path to candidates JSONL (all_candidates.jsonl or per-task candidates file)",
    )
    parser.add_argument(
        "--raw-ast-root",
        type=Path,
        required=True,
        help="Root folder containing raw AST JSON files from step-3",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Root folder containing cleaned Solidity files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Optional limit for quick testing (0 = all)",
    )
    parser.add_argument(
        "--no-sibling-edges",
        action="store_true",
        help="Disable AST sibling edges",
    )
    args = parser.parse_args()

    candidates_path = args.candidates.resolve()
    raw_ast_root = args.raw_ast_root.resolve()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    add_sibling_edges = not args.no_sibling_edges

    if not candidates_path.is_file():
        raise SystemExit(f"Candidates file not found: {candidates_path}")
    if not raw_ast_root.is_dir():
        raise SystemExit(f"Raw AST root not found: {raw_ast_root}")
    if not source_root.is_dir():
        raise SystemExit(f"Source root not found: {source_root}")

    graph_rows: List[Dict[str, Any]] = []

    total_candidates = 0
    built_graphs = 0
    missing_ast_files = 0
    missing_source_files = 0
    unmatched_functions = 0
    total_nodes = 0
    total_edges = 0

    candidate_rows = list(load_jsonl(candidates_path))
    if args.max_candidates > 0:
        candidate_rows = candidate_rows[:args.max_candidates]

    for row in tqdm(candidate_rows, desc="Building AST graphs", unit="function"):
        total_candidates += 1

        rel_path = row.get("relative_path", "")
        if not rel_path:
            unmatched_functions += 1
            continue

        source_path = source_root / rel_path
        if not source_path.is_file():
            missing_source_files += 1
            continue

        ast_path = raw_ast_root / Path(rel_path).with_suffix(".ast.json")
        if not ast_path.is_file():
            missing_ast_files += 1
            continue

        source_text = read_text(source_path)
        line_starts = build_line_byte_index(source_text)
        ast_obj = load_json(ast_path)

        matched_node = find_matching_function_node(ast_obj, row, source_text)
        if matched_node is None:
            unmatched_functions += 1
            continue

        nodes, edges = build_statement_lite_ast_graph(
            matched_node,
            line_starts=line_starts,
            add_sibling_edges=False,
        )

        out = dict(row)
        out["contract_key"] = "|".join([
            str(out.get("relative_path", "")),
            str(out.get("contract_name", "")),
        ])
        out["function_key"] = "|".join([
            str(out.get("relative_path", "")),
            str(out.get("contract_name", "")),
            str(out.get("function_name", "")),
            str(out.get("function_kind", "")),
            str(out.get("line_start", "")),
            str(out.get("line_end", "")),
            str(out.get("param_count", "")),
        ])
        out["function_src_key"] = "|".join([
            str(out.get("relative_path", "")),
            str(out.get("contract_name", "")),
            str(out.get("function_name", "")),
            str(out.get("src", "")),
        ])
        out["graph_type"] = "function_level_ast"
        out["graph_mode"] = "function_statement_lite_ast_skeleton"
        out["ast_encoding_level"] = "statement_lite"
        out["ast_expression_detail"] = "collapsed_to_placeholder"
        out["root_node_type"] = "FunctionDefinition"
        out["root_local_node_id"] = 0
        out["node_count"] = len(nodes)
        out["edge_count"] = len(edges)
        out["nodes"] = nodes
        out["edges"] = edges

        graph_rows.append(out)

        built_graphs += 1
        total_nodes += len(nodes)
        total_edges += len(edges)

    graphs_path = output_dir / "function_ast_graphs.jsonl"
    summary_path = output_dir / "summary.json"

    write_jsonl(graphs_path, graph_rows)

    summary = {
        "stage": "step5_function_level_ast_graph_build",
        "candidates": str(candidates_path),
        "raw_ast_root": str(raw_ast_root),
        "source_root": str(source_root),
        "total_candidate_rows": total_candidates,
        "graphs_built": built_graphs,
        "missing_ast_files": missing_ast_files,
        "missing_source_files": missing_source_files,
        "unmatched_functions": unmatched_functions,
        "total_graph_nodes": total_nodes,
        "total_graph_edges": total_edges,
        "avg_nodes_per_graph": (total_nodes / built_graphs) if built_graphs else 0.0,
        "avg_edges_per_graph": (total_edges / built_graphs) if built_graphs else 0.0,
        "ast_encoding_level": "statement_lite",
        "expression_nodes_collapsed": True,
        "statement_level_only": True,
        "sibling_edges_enabled": False,
        "graphs_jsonl": str(graphs_path),
        "note": (
            "This stage builds function-level AST graphs only. "
            "It does not build CFG or DFG."
        ),
    }

    save_json(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
