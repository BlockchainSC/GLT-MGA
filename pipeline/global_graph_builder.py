#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step13: Build lightweight global call/context graphs for seed functions.

Purpose:
  This script builds a compact global-view graph around each seed function.

Input:
  1) Step9 labeled local graph JSONL
     OR timestamp_down_sample labeled JSONL
  2) Step12 seed_topk_related_functions.jsonl
  3) Step3 function_inventory.jsonl

Output:
  - global_context_graphs.jsonl
  - vocab.json
  - summary.json

Design:
  - Seed function keeps full local AST+CFG+DFG in previous pipeline.
  - This script adds only lightweight global context.
  - Related functions are represented as function nodes only.
  - Shared state variables are represented as generic state nodes.
  - Raw function names / state variable names are kept only in metadata,
    not used as model tokens/features.

Research goal:
  Local view  = AST + CFG + DFG
  Global view = function interaction + shared-state relation + timestamp/reentrancy context
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


# -----------------------------
# Basic IO helpers
# -----------------------------

def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def get_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata", None)
    if isinstance(meta, dict):
        return meta
    return row


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x)


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def get_function_key(row: Dict[str, Any]) -> str:
    meta = get_meta(row)

    if meta.get("function_key"):
        return safe_str(meta.get("function_key"))
    if row.get("function_key"):
        return safe_str(row.get("function_key"))

    return "|".join([
        safe_str(meta.get("relative_path", row.get("relative_path", ""))),
        safe_str(meta.get("contract_name", row.get("contract_name", ""))),
        safe_str(meta.get("function_name", row.get("function_name", ""))),
        safe_str(meta.get("function_kind", row.get("function_kind", ""))),
        safe_str(meta.get("line_start", row.get("line_start", ""))),
        safe_str(meta.get("line_end", row.get("line_end", ""))),
        safe_str(meta.get("param_count", row.get("param_count", ""))),
    ])


def get_contract_key(row: Dict[str, Any]) -> str:
    meta = get_meta(row)
    if meta.get("contract_key"):
        return safe_str(meta.get("contract_key"))
    if row.get("contract_key"):
        return safe_str(row.get("contract_key"))

    return "|".join([
        safe_str(meta.get("relative_path", row.get("relative_path", ""))),
        safe_str(meta.get("contract_name", row.get("contract_name", ""))),
    ])


def get_label(row: Dict[str, Any]) -> int:
    if "label" in row:
        return safe_int(row.get("label"), 0)
    if "target" in row:
        return safe_int(row.get("target"), 0)
    if "y" in row:
        return safe_int(row.get("y"), 0)

    meta = get_meta(row)
    return safe_int(meta.get("label"), 0)


def function_type_from_meta(meta: Dict[str, Any], is_seed: bool = False) -> str:
    kind = safe_str(meta.get("function_kind", "")).lower()
    name = safe_str(meta.get("function_name", "")).lower()

    if is_seed:
        return "CG_SEED_FUNCTION"

    if meta.get("is_fallback") is True or kind == "fallback" or name in {"fallback", "__fallback__"}:
        return "CG_FALLBACK_FUNCTION"

    if meta.get("is_receive") is True or kind == "receive" or name == "receive":
        return "CG_RECEIVE_FUNCTION"

    if kind == "constructor" or meta.get("is_constructor") is True:
        return "CG_CONSTRUCTOR_FUNCTION"

    return "CG_RELATED_FUNCTION"


def visibility_flag(meta: Dict[str, Any]) -> int:
    v = safe_str(meta.get("visibility", "")).lower()
    return 1 if v in {"public", "external"} else 0


def payable_flag(meta: Dict[str, Any]) -> int:
    s = safe_str(meta.get("state_mutability", "")).lower()
    return 1 if s == "payable" else 0


def view_flag_has_local_graph(rel_item: Dict[str, Any]) -> int:
    return 1 if rel_item.get("has_all_ast_cfg_dfg") is True else 0


def get_seed_facts(rel_item: Dict[str, Any]) -> Dict[str, Any]:
    facts = rel_item.get("seed_function_facts", {})
    return facts if isinstance(facts, dict) else {}


def get_related_facts(rel_item: Dict[str, Any]) -> Dict[str, Any]:
    facts = rel_item.get("related_function_facts", {})
    return facts if isinstance(facts, dict) else {}


def fact_flag(facts: Dict[str, Any], name: str) -> int:
    return 1 if safe_int(facts.get(name), 0) > 0 else 0


def norm_count(facts: Dict[str, Any], name: str, cap: int = 5) -> float:
    v = safe_int(facts.get(name), 0)
    if v <= 0:
        return 0.0
    return min(v, cap) / float(cap)


def graph_seed_facts_from_relations(relations: List[Dict[str, Any]]) -> Dict[str, Any]:
    for r in relations:
        facts = get_seed_facts(r)
        if facts:
            return facts
    return {}


def relation_allowed(task: str, relation_type: str) -> bool:
    task = (task or "all").lower()

    generic_clean = {
        "INTERNAL_CALL_IN",
        "INTERNAL_CALL_OUT",
        "SHARED_STATE_WRITE_READ",
        "SHARED_STATE_WRITE_WRITE",
    }

    if task == "all":
        return True

    if task in {"reentrancy", "timestamp"}:
        return relation_type in generic_clean

    return False


# -----------------------------
# Graph builder helpers
# -----------------------------

class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.node_key_to_id: Dict[str, int] = {}

    def add_node(
        self,
        key: str,
        node_kind: str,
        node_type: str,
        token: str,
        attrs: Dict[str, Any] | None = None,
    ) -> int:
        if key in self.node_key_to_id:
            return self.node_key_to_id[key]

        node_id = len(self.nodes)
        self.node_key_to_id[key] = node_id

        node = {
            "id": node_id,
            "key": key,
            "view": "GLOBAL",
            "node_kind": node_kind,
            "node_type": node_type,
            "token": token,
        }
        if attrs:
            node.update(attrs)

        self.nodes.append(node)
        return node_id

    def add_edge(
        self,
        src: int,
        dst: int,
        edge_type: str,
        attrs: Dict[str, Any] | None = None,
    ) -> None:
        edge = {
            "src": int(src),
            "dst": int(dst),
            "edge_type": edge_type,
        }
        if attrs:
            edge.update(attrs)
        self.edges.append(edge)


def add_bidirectional_edge(
    gb: GraphBuilder,
    src: int,
    dst: int,
    edge_type: str,
    reverse_edge_type: str | None = None,
    attrs: Dict[str, Any] | None = None,
) -> None:
    rev = reverse_edge_type or f"{edge_type}_REV"
    gb.add_edge(src, dst, edge_type, attrs)
    gb.add_edge(dst, src, rev, attrs)


def relation_direction(
    relation_type: str,
    seed_id: int,
    related_id: int,
    neutral_relation_names: bool = False,
) -> Tuple[int, int, str, str]:
    """
    Return directed src/dst and edge type.

    If neutral_relation_names=True:
      REENTRANCY_HELPER_STATE_WRITE -> CG_CALL_RELATED_STATE_WRITE
      TIMESTAMP_STATE_WRITE_READ -> CG_ENV_STATE_WRITE_READ
      TIMESTAMP_STATE_READ_FROM_OTHER_WRITE -> CG_ENV_STATE_READ_FROM_OTHER_WRITE
    """
    if relation_type == "INTERNAL_CALL_IN":
        return related_id, seed_id, "CG_INTERNAL_CALL_IN", "CG_INTERNAL_CALL_IN_REV"

    if relation_type == "INTERNAL_CALL_OUT":
        return seed_id, related_id, "CG_INTERNAL_CALL_OUT", "CG_INTERNAL_CALL_OUT_REV"

    if relation_type == "SHARED_STATE_WRITE_READ":
        return seed_id, related_id, "CG_SHARED_STATE_WRITE_READ", "CG_SHARED_STATE_WRITE_READ_REV"

    if relation_type == "SHARED_STATE_WRITE_WRITE":
        return seed_id, related_id, "CG_SHARED_STATE_WRITE_WRITE", "CG_SHARED_STATE_WRITE_WRITE_REV"

    if relation_type == "SHARED_STATE_READ_READ_WEAK":
        return seed_id, related_id, "CG_SHARED_STATE_READ_READ_WEAK", "CG_SHARED_STATE_READ_READ_WEAK_REV"

    if relation_type == "REENTRANCY_HELPER_STATE_WRITE":
        if neutral_relation_names:
            return seed_id, related_id, "CG_CALL_RELATED_STATE_WRITE", "CG_CALL_RELATED_STATE_WRITE_REV"
        return seed_id, related_id, "CG_REENTRANCY_HELPER_STATE_WRITE", "CG_REENTRANCY_HELPER_STATE_WRITE_REV"

    if relation_type == "TIMESTAMP_STATE_WRITE_READ":
        if neutral_relation_names:
            return seed_id, related_id, "CG_ENV_STATE_WRITE_READ", "CG_ENV_STATE_WRITE_READ_REV"
        return seed_id, related_id, "CG_TIMESTAMP_STATE_WRITE_READ", "CG_TIMESTAMP_STATE_WRITE_READ_REV"

    if relation_type == "TIMESTAMP_STATE_READ_FROM_OTHER_WRITE":
        if neutral_relation_names:
            return related_id, seed_id, "CG_ENV_STATE_READ_FROM_OTHER_WRITE", "CG_ENV_STATE_READ_FROM_OTHER_WRITE_REV"
        return related_id, seed_id, "CG_TIMESTAMP_STATE_READ_FROM_OTHER_WRITE", "CG_TIMESTAMP_STATE_READ_FROM_OTHER_WRITE_REV"

    return seed_id, related_id, "CG_GENERIC_RELATION", "CG_GENERIC_RELATION_REV"


def neutral_relation_attr(relation_type: str, neutral_relation_names: bool) -> str:
    if not neutral_relation_names:
        return relation_type

    mapping = {
        "REENTRANCY_HELPER_STATE_WRITE": "CALL_RELATED_STATE_WRITE",
        "TIMESTAMP_STATE_WRITE_READ": "ENV_STATE_WRITE_READ",
        "TIMESTAMP_STATE_READ_FROM_OTHER_WRITE": "ENV_STATE_READ_FROM_OTHER_WRITE",
    }
    return mapping.get(relation_type, relation_type)


def add_limited_call_context(
    gb: GraphBuilder,
    function_id: int,
    function_key: str,
    facts: Dict[str, Any],
    role: str,
    include_external_context_nodes: bool = False,
) -> Tuple[int, int]:
    has_call_context = (
        fact_flag(facts, "has_low_level_call")
        or fact_flag(facts, "has_value_transfer")
        or fact_flag(facts, "has_token_transfer")
    )

    if not has_call_context:
        return 0, 0

    callsite_id = gb.add_node(
        key=f"{function_key}::{role}::CALLSITE",
        node_kind="CG_CALLSITE",
        node_type="CG_CALLSITE",
        token="CG_CALLSITE_TOKEN",
        attrs={
            "is_seed": 0,
            "is_related": 0,
            "is_state_var": 0,
            "is_callsite": 1,
            "is_external_context": 0,
            "is_env_context": 0,
            "is_fallback": 0,
            "is_receive": 0,
            "is_payable": 0,
            "is_external_or_public": 0,
            "has_local_graph": 0,
            "is_view_or_pure": 0,
            "has_internal_call": 0,
            "has_low_level_call": fact_flag(facts, "has_low_level_call"),
            "has_value_transfer": fact_flag(facts, "has_value_transfer"),
            "has_token_transfer": fact_flag(facts, "has_token_transfer"),
            "has_env_read": 0,
            "has_state_read": 0,
            "has_state_write": 0,
            "num_internal_calls_norm": 0.0,
            "num_state_reads_norm": 0.0,
            "num_state_writes_norm": 0.0,
        },
    )

    add_bidirectional_edge(
        gb,
        function_id,
        callsite_id,
        "CG_FUNCTION_TO_CALLSITE",
        "CG_CALLSITE_TO_FUNCTION",
    )

    if not include_external_context_nodes:
        return 1, 0

    ext_id = gb.add_node(
        key=f"{function_key}::{role}::EXTERNAL_CONTEXT",
        node_kind="CG_EXTERNAL",
        node_type="CG_EXTERNAL_CONTEXT",
        token="CG_EXTERNAL_CONTEXT_TOKEN",
        attrs={
            "is_seed": 0,
            "is_related": 0,
            "is_state_var": 0,
            "is_callsite": 0,
            "is_external_context": 1,
            "is_env_context": 0,
            "is_fallback": 0,
            "is_receive": 0,
            "is_payable": 0,
            "is_external_or_public": 0,
            "has_local_graph": 0,
            "is_view_or_pure": 0,
            "has_internal_call": 0,
            "has_low_level_call": 0,
            "has_value_transfer": 0,
            "has_token_transfer": 0,
            "has_env_read": 0,
            "has_state_read": 0,
            "has_state_write": 0,
            "num_internal_calls_norm": 0.0,
            "num_state_reads_norm": 0.0,
            "num_state_writes_norm": 0.0,
        },
    )

    add_bidirectional_edge(
        gb,
        callsite_id,
        ext_id,
        "CG_CALLSITE_TO_EXTERNAL_CONTEXT",
        "CG_EXTERNAL_CONTEXT_TO_CALLSITE",
    )

    return 1, 1


def add_env_context(
    gb: GraphBuilder,
    function_id: int,
    function_key: str,
    facts: Dict[str, Any],
    role: str,
) -> int:
    if not fact_flag(facts, "has_env_read"):
        return 0

    env_id = gb.add_node(
        key=f"{function_key}::{role}::ENV_CONTEXT",
        node_kind="CG_ENV",
        node_type="CG_ENV_CONTEXT",
        token="CG_ENV_TOKEN",
        attrs={
            "is_seed": 0,
            "is_related": 0,
            "is_state_var": 0,
            "is_callsite": 0,
            "is_external_context": 0,
            "is_env_context": 1,
            "is_fallback": 0,
            "is_receive": 0,
            "is_payable": 0,
            "is_external_or_public": 0,
            "has_local_graph": 0,
            "is_view_or_pure": 0,
            "has_internal_call": 0,
            "has_low_level_call": 0,
            "has_value_transfer": 0,
            "has_token_transfer": 0,
            "has_env_read": 1,
            "has_state_read": 0,
            "has_state_write": 0,
            "num_internal_calls_norm": 0.0,
            "num_state_reads_norm": 0.0,
            "num_state_writes_norm": 0.0,
        },
    )

    add_bidirectional_edge(
        gb,
        function_id,
        env_id,
        "CG_FUNCTION_USES_ENV",
        "CG_ENV_USED_BY_FUNCTION",
    )

    return 1


def build_one_global_graph(
    seed_row: Dict[str, Any],
    relations: List[Dict[str, Any]],
    inventory_by_key: Dict[str, Dict[str, Any]],
    max_related: int,
    include_state_nodes: bool,
    max_state_vars_per_relation: int,
    task: str,
    neutral_relation_names: bool = False,
    no_anchor_to_related: bool = False,
    include_callsite_nodes: bool = False,
    include_env_nodes: bool = False,
) -> Dict[str, Any]:
    seed_meta = get_meta(seed_row)
    seed_fk = get_function_key(seed_row)
    contract_key = get_contract_key(seed_row)
    seed_facts = graph_seed_facts_from_relations(relations)

    gb = GraphBuilder()

    global_anchor_id = gb.add_node(
        key=f"{seed_fk}::GLOBAL_ANCHOR",
        node_kind="CG_ANCHOR",
        node_type="CG_GLOBAL_ANCHOR",
        token="CG_ANCHOR_TOKEN",
        attrs={
            "is_seed": 0,
            "is_related": 0,
            "is_state_var": 0,
            "is_fallback": 0,
            "is_receive": 0,
            "is_payable": 0,
            "is_external_or_public": 0,
            "has_local_graph": 1,
            "is_view_or_pure": 0,
            "has_internal_call": 0,
            "has_low_level_call": 0,
            "has_value_transfer": 0,
            "has_token_transfer": 0,
            "has_env_read": 0,
            "has_state_read": 0,
            "has_state_write": 0,
            "num_internal_calls_norm": 0.0,
            "num_state_reads_norm": 0.0,
            "num_state_writes_norm": 0.0,
        },
    )

    seed_node_id = gb.add_node(
        key=f"{seed_fk}::CG_SEED",
        node_kind="CG_FUNCTION",
        node_type=function_type_from_meta(seed_meta, is_seed=True),
        token="CG_FUNCTION_TOKEN",
        attrs={
            "function_key": seed_fk,
            "function_name": safe_str(seed_meta.get("function_name", "")),
            "function_kind": safe_str(seed_meta.get("function_kind", "")),
            "visibility": safe_str(seed_meta.get("visibility", "")),
            "state_mutability": safe_str(seed_meta.get("state_mutability", "")),
            "is_seed": 1,
            "is_related": 0,
            "is_state_var": 0,
            "is_fallback": 1 if seed_meta.get("is_fallback") is True else 0,
            "is_receive": 1 if seed_meta.get("is_receive") is True else 0,
            "is_payable": payable_flag(seed_meta),
            "is_external_or_public": visibility_flag(seed_meta),
            "has_local_graph": 1,
            "is_view_or_pure": fact_flag(seed_facts, "is_view_or_pure"),
            "has_internal_call": fact_flag(seed_facts, "has_internal_call"),
            "has_low_level_call": fact_flag(seed_facts, "has_low_level_call"),
            "has_value_transfer": fact_flag(seed_facts, "has_value_transfer"),
            "has_token_transfer": fact_flag(seed_facts, "has_token_transfer"),
            "has_env_read": fact_flag(seed_facts, "has_env_read"),
            "has_state_read": fact_flag(seed_facts, "has_state_read"),
            "has_state_write": fact_flag(seed_facts, "has_state_write"),
            "num_internal_calls_norm": norm_count(seed_facts, "num_internal_calls"),
            "num_state_reads_norm": norm_count(seed_facts, "num_state_reads"),
            "num_state_writes_norm": norm_count(seed_facts, "num_state_writes"),
        },
    )

    add_bidirectional_edge(
        gb,
        global_anchor_id,
        seed_node_id,
        "CG_ANCHOR_TO_SEED",
        "CG_SEED_TO_ANCHOR",
    )

    callsite_node_count = 0
    external_context_node_count = 0
    env_node_count = 0
    if include_callsite_nodes:
        callsite_added, external_context_added = add_limited_call_context(
            gb,
            seed_node_id,
            seed_fk,
            seed_facts,
            "SEED",
            include_external_context_nodes=False,
        )
        callsite_node_count += callsite_added
        external_context_node_count += external_context_added

    if include_env_nodes:
        env_node_count += add_env_context(
            gb,
            seed_node_id,
            seed_fk,
            seed_facts,
            "SEED",
        )

    # Sort by rank and score to keep deterministic top-k.
    relations = sorted(
        relations,
        key=lambda r: (
            safe_int(r.get("rank"), 999999),
            -safe_int(r.get("score"), 0),
            safe_str(r.get("related_function_key", "")),
        ),
    )

    related_function_count = 0
    state_node_count = 0
    relation_type_counter = Counter()

    for rel in relations:
        if related_function_count >= max_related:
            break

        relation_types = rel.get("relation_types", [])
        if not isinstance(relation_types, list):
            relation_types = []

        relation_types = [
            safe_str(rt)
            for rt in relation_types
            if safe_str(rt) and relation_allowed(task, safe_str(rt))
        ]

        if not relation_types:
            continue

        related_fk = safe_str(rel.get("related_function_key", ""))
        if not related_fk:
            continue

        related_meta = get_meta(inventory_by_key.get(related_fk, {}))
        if not related_meta:
            # Use relation row metadata as fallback.
            related_meta = {
                "function_key": related_fk,
                "contract_key": contract_key,
                "function_name": "",
                "function_kind": "function",
            }
        related_facts = get_related_facts(rel)

        related_id = gb.add_node(
            key=f"{related_fk}::CG_RELATED",
            node_kind="CG_FUNCTION",
            node_type=function_type_from_meta(related_meta, is_seed=False),
            token="CG_FUNCTION_TOKEN",
            attrs={
                "function_key": related_fk,
                "function_name": safe_str(related_meta.get("function_name", "")),
                "function_kind": safe_str(related_meta.get("function_kind", "")),
                "visibility": safe_str(related_meta.get("visibility", "")),
                "state_mutability": safe_str(related_meta.get("state_mutability", "")),
                "rank": safe_int(rel.get("rank"), 0),
                "score": safe_int(rel.get("score"), 0),
                "is_seed": 0,
                "is_related": 1,
                "is_state_var": 0,
                "is_fallback": 1 if related_meta.get("is_fallback") is True else 0,
                "is_receive": 1 if related_meta.get("is_receive") is True else 0,
                "is_payable": payable_flag(related_meta),
                "is_external_or_public": visibility_flag(related_meta),
                "has_local_graph": view_flag_has_local_graph(rel),
                "is_view_or_pure": fact_flag(related_facts, "is_view_or_pure"),
                "has_internal_call": fact_flag(related_facts, "has_internal_call"),
                "has_low_level_call": fact_flag(related_facts, "has_low_level_call"),
                "has_value_transfer": fact_flag(related_facts, "has_value_transfer"),
                "has_token_transfer": fact_flag(related_facts, "has_token_transfer"),
                "has_env_read": fact_flag(related_facts, "has_env_read"),
                "has_state_read": fact_flag(related_facts, "has_state_read"),
                "has_state_write": fact_flag(related_facts, "has_state_write"),
                "num_internal_calls_norm": norm_count(related_facts, "num_internal_calls"),
                "num_state_reads_norm": norm_count(related_facts, "num_state_reads"),
                "num_state_writes_norm": norm_count(related_facts, "num_state_writes"),
            },
        )
        related_function_count += 1
        if include_callsite_nodes:
            callsite_added, external_context_added = add_limited_call_context(
                gb,
                related_id,
                related_fk,
                related_facts,
                "RELATED",
                include_external_context_nodes=False,
            )
            callsite_node_count += callsite_added
            external_context_node_count += external_context_added

        if include_env_nodes:
            env_node_count += add_env_context(
                gb,
                related_id,
                related_fk,
                related_facts,
                "RELATED",
            )

        if not no_anchor_to_related:
            add_bidirectional_edge(
                gb,
                global_anchor_id,
                related_id,
                "CG_ANCHOR_TO_RELATED",
                "CG_RELATED_TO_ANCHOR",
                attrs={"rank": safe_int(rel.get("rank"), 0)},
            )

        shared_vars = rel.get("shared_vars", [])
        if not isinstance(shared_vars, list):
            shared_vars = []
        relation_vars_by_type = rel.get("relation_vars_by_type", {})
        if not isinstance(relation_vars_by_type, dict):
            relation_vars_by_type = {}

        for relation_type in relation_types:
            relation_type = safe_str(relation_type)
            relation_attr = neutral_relation_attr(
                relation_type,
                neutral_relation_names,
            )
            relation_type_counter[relation_attr] += 1
            vars_for_relation = relation_vars_by_type.get(relation_type, shared_vars)
            if not isinstance(vars_for_relation, list):
                vars_for_relation = []

            src, dst, edge_type, rev_edge_type = relation_direction(
                relation_type,
                seed_node_id,
                related_id,
                neutral_relation_names=neutral_relation_names,
            )

            add_bidirectional_edge(
                gb,
                src,
                dst,
                edge_type,
                rev_edge_type,
                attrs={
                    "relation_type": relation_attr,
                    "rank": safe_int(rel.get("rank"), 0),
                    "score": safe_int(rel.get("score"), 0),
                },
            )

            if include_state_nodes and relation_type.startswith(("SHARED_STATE", "REENTRANCY", "TIMESTAMP")):
                state_role = rel.get("state_access_role", {})
                if not isinstance(state_role, dict):
                    state_role = {}

                for sv_i, sv in enumerate(vars_for_relation[:max_state_vars_per_relation]):
                    sv_text = safe_str(sv).lower()
                    if not sv_text:
                        continue

                    # Important: raw state variable names are not stored in graph nodes or keys.
                    # The model token remains generic.
                    state_id = gb.add_node(
                        key=f"{seed_fk}::{related_fk}::STATE::{relation_attr}::{state_node_count}",
                        node_kind="CG_STATE",
                        node_type="CG_SHARED_STATE_VAR",
                        token="CG_STATE_TOKEN",
                        attrs={
                            "relation_type": relation_attr,
                            "is_seed": 0,
                            "is_related": 0,
                            "is_state_var": 1,
                            "is_fallback": 0,
                            "is_receive": 0,
                            "is_payable": 0,
                            "is_external_or_public": 0,
                            "has_local_graph": 0,
                            "is_view_or_pure": 0,
                            "has_internal_call": 0,
                            "has_low_level_call": 0,
                            "has_value_transfer": 0,
                            "has_token_transfer": 0,
                            "has_env_read": 0,
                            "has_state_read": 0,
                            "has_state_write": 0,
                            "num_internal_calls_norm": 0.0,
                            "num_state_reads_norm": 0.0,
                            "num_state_writes_norm": 0.0,
                        },
                    )
                    state_node_count += 1

                    if state_role:
                        if safe_int(state_role.get("seed_reads_shared"), 0) > 0:
                            add_bidirectional_edge(
                                gb,
                                seed_node_id,
                                state_id,
                                "CG_FUNCTION_READS_STATE",
                                "CG_STATE_READ_BY_FUNCTION",
                                attrs={"relation_type": relation_attr},
                            )

                        if safe_int(state_role.get("seed_writes_shared"), 0) > 0:
                            add_bidirectional_edge(
                                gb,
                                seed_node_id,
                                state_id,
                                "CG_FUNCTION_WRITES_STATE",
                                "CG_STATE_WRITTEN_BY_FUNCTION",
                                attrs={"relation_type": relation_attr},
                            )

                        if safe_int(state_role.get("related_reads_shared"), 0) > 0:
                            add_bidirectional_edge(
                                gb,
                                related_id,
                                state_id,
                                "CG_FUNCTION_READS_STATE",
                                "CG_STATE_READ_BY_FUNCTION",
                                attrs={"relation_type": relation_attr},
                            )

                        if safe_int(state_role.get("related_writes_shared"), 0) > 0:
                            add_bidirectional_edge(
                                gb,
                                related_id,
                                state_id,
                                "CG_FUNCTION_WRITES_STATE",
                                "CG_STATE_WRITTEN_BY_FUNCTION",
                                attrs={"relation_type": relation_attr},
                            )
                    else:
                        add_bidirectional_edge(
                            gb,
                            seed_node_id,
                            state_id,
                            "CG_FUNCTION_TO_STATE_CONTEXT",
                            "CG_STATE_TO_FUNCTION_CONTEXT",
                            attrs={"relation_type": relation_attr},
                        )

                        add_bidirectional_edge(
                            gb,
                            related_id,
                            state_id,
                            "CG_FUNCTION_TO_STATE_CONTEXT",
                            "CG_STATE_TO_FUNCTION_CONTEXT",
                            attrs={"relation_type": relation_attr},
                        )

    metadata = {
        "function_key": seed_fk,
        "contract_key": contract_key,
        "task": task,
        "relative_path": safe_str(seed_meta.get("relative_path", "")),
        "contract_name": safe_str(seed_meta.get("contract_name", "")),
        "function_name": safe_str(seed_meta.get("function_name", "")),
        "function_kind": safe_str(seed_meta.get("function_kind", "")),
        "line_start": seed_meta.get("line_start", ""),
        "line_end": seed_meta.get("line_end", ""),
        "label": get_label(seed_row),
        "label_name": "positive" if get_label(seed_row) == 1 else "negative",
        "global_related_function_count": related_function_count,
        "global_state_node_count": state_node_count,
        "global_callsite_node_count": callsite_node_count,
        "global_external_context_node_count": external_context_node_count,
        "global_env_node_count": env_node_count,
        "global_relation_type_counts": dict(relation_type_counter),
        "neutral_relation_names": bool(neutral_relation_names),
    }

    return {
        "graph_type": "lightweight_global_function_context_graph",
        "graph_mode": "seed_centered_topk_global_context",
        "metadata": metadata,
        "label": get_label(seed_row),
        "target": get_label(seed_row),
        "y": get_label(seed_row),
        "nodes": gb.nodes,
        "edges": gb.edges,
    }


# -----------------------------
# Encoding helpers
# -----------------------------

def build_vocab(raw_graphs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    vocabs = {
        "view": {},
        "node_kind": {},
        "node_type": {},
        "token": {},
        "edge_type": {},
    }

    def add(vocab_name: str, item: str) -> None:
        vocab = vocabs[vocab_name]
        if item not in vocab:
            vocab[item] = len(vocab)

    for g in raw_graphs:
        for n in g["nodes"]:
            add("view", safe_str(n.get("view", "GLOBAL")))
            add("node_kind", safe_str(n.get("node_kind", "CG_UNKNOWN_NODE")))
            add("node_type", safe_str(n.get("node_type", "CG_UNKNOWN_TYPE")))
            add("token", safe_str(n.get("token", "CG_TOKEN")))
        for e in g["edges"]:
            add("edge_type", safe_str(e.get("edge_type", "CG_UNKNOWN_EDGE")))

    return {
        name: {
            "size": len(item_to_id),
            "item_to_id": item_to_id,
            "id_to_item": [k for k, _ in sorted(item_to_id.items(), key=lambda kv: kv[1])],
        }
        for name, item_to_id in vocabs.items()
    }


def encode_graph(raw: Dict[str, Any], vocabs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    view_vocab = vocabs["view"]["item_to_id"]
    kind_vocab = vocabs["node_kind"]["item_to_id"]
    type_vocab = vocabs["node_type"]["item_to_id"]
    token_vocab = vocabs["token"]["item_to_id"]
    edge_vocab = vocabs["edge_type"]["item_to_id"]

    node_features = []
    node_view_ids = []
    node_type_ids = []
    node_token_ids = []

    for n in raw["nodes"]:
        view_id = view_vocab[safe_str(n.get("view", "GLOBAL"))]
        kind_id = kind_vocab[safe_str(n.get("node_kind", "CG_UNKNOWN_NODE"))]
        type_id = type_vocab[safe_str(n.get("node_type", "CG_UNKNOWN_TYPE"))]
        token_id = token_vocab[safe_str(n.get("token", "CG_TOKEN"))]

        features = [
            view_id,
            kind_id,
            type_id,
            token_id,

            safe_int(n.get("is_seed"), 0),
            safe_int(n.get("is_related"), 0),
            safe_int(n.get("is_state_var"), 0),
            safe_int(n.get("is_callsite"), 0),
            safe_int(n.get("is_external_context"), 0),
            safe_int(n.get("is_env_context"), 0),

            safe_int(n.get("is_fallback"), 0),
            safe_int(n.get("is_receive"), 0),
            safe_int(n.get("is_payable"), 0),
            safe_int(n.get("is_external_or_public"), 0),
        ]

        node_features.append(features)
        node_view_ids.append(view_id)
        node_type_ids.append(type_id)
        node_token_ids.append(token_id)

    edge_index = []
    edge_type_ids = []
    graph_triplets = []

    for e in raw["edges"]:
        src = safe_int(e.get("src"), 0)
        dst = safe_int(e.get("dst"), 0)
        et = safe_str(e.get("edge_type", "CG_UNKNOWN_EDGE"))
        et_id = edge_vocab[et]

        edge_index.append([src, dst])
        edge_type_ids.append(et_id)
        graph_triplets.append([src, et_id, dst])

    meta = raw.get("metadata", {})
    if not isinstance(meta, dict):
        meta = {}

    label = safe_int(raw.get("label", meta.get("label", 0)), 0)
    target = safe_int(raw.get("target", label), label)
    y = safe_int(raw.get("y", label), label)

    node_feature_names = [
        "view_id",
        "node_kind_id",
        "node_type_id",
        "token_id",
        "is_seed",
        "is_related",
        "is_state_var",
        "is_callsite",
        "is_external_context",
        "is_env_context",
        "is_fallback",
        "is_receive",
        "is_payable",
        "is_external_or_public",
    ]

    out = {
        "graph_type": raw.get("graph_type", "global_context_graph"),
        "graph_mode": raw.get("graph_mode", "seed_centered_global_context"),
        "task": raw.get("task", meta.get("task", "")),
        "metadata": {
            "function_key": safe_str(meta.get("function_key", "")),
            "contract_key": safe_str(meta.get("contract_key", "")),
            "relative_path": safe_str(meta.get("relative_path", "")),
            "line_start": meta.get("line_start", ""),
            "line_end": meta.get("line_end", ""),
            "task": safe_str(meta.get("task", raw.get("task", ""))),
            "global_related_function_count": safe_int(
                meta.get("global_related_function_count", 0),
                0,
            ),
            "global_relation_type_counts": meta.get("global_relation_type_counts", {}),
        },
        "label": label,
        "target": target,
        "y": y,
        "num_nodes": len(raw["nodes"]),
        "num_edges": len(raw["edges"]),
        "node_feature_names": node_feature_names,
    }
    out["node_features"] = node_features
    out["node_view_ids"] = node_view_ids
    out["node_type_ids"] = node_type_ids
    out["node_token_ids"] = node_token_ids
    out["edge_index"] = edge_index
    out["edge_type_ids"] = edge_type_ids
    out["graph"] = graph_triplets

    return out


# -----------------------------
# Main
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build lightweight seed-centered global context graphs."
    )
    ap.add_argument("--seed-jsonl", type=Path, required=True,
                    help="Step9 labeled JSONL or timestamp_down_sample labeled JSONL.")
    ap.add_argument("--topk-jsonl", type=Path, required=True,
                    help="Step12 seed_topk_related_functions.jsonl.")
    ap.add_argument("--inventory-jsonl", type=Path, required=True,
                    help="Step3 function_inventory.jsonl.")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--max-related", type=int, default=5)
    ap.add_argument("--include-state-nodes", action="store_true",
                    help="Add generic shared-state nodes. Recommended for first global experiment.")
    ap.add_argument("--max-state-vars-per-relation", type=int, default=3)
    ap.add_argument("--include-callsite-nodes", action="store_true")
    ap.add_argument("--include-env-nodes", action="store_true")
    ap.add_argument(
        "--task",
        choices=["reentrancy", "timestamp", "all"],
        required=True,
        help="Task-specific global graph building.",
    )
    ap.add_argument(
        "--neutral-relation-names",
        action="store_true",
        help="Map vulnerability-specific relation edge names to neutral names.",
    )
    ap.add_argument(
        "--skip-empty-global-graphs",
        action="store_true",
        help="Do not emit global graphs with no allowed related function relation.",
    )
    ap.add_argument(
        "--no-anchor-to-related",
        action="store_true",
        help="Do not add generic GLOBAL_ANCHOR <-> RELATED_FUNCTION edges.",
    )
    args = ap.parse_args()

    if args.task in {"reentrancy", "timestamp"} and not args.neutral_relation_names:
        print("[WARNING] For final research experiments, use --neutral-relation-names.")

    seed_path_str = str(args.seed_jsonl).lower()
    topk_path_str = str(args.topk_jsonl).lower()

    if args.task == "reentrancy":
        if "reentrancy" not in seed_path_str or "reentrancy" not in topk_path_str:
            raise ValueError(
                "Task is reentrancy but seed/topk path does not look reentrancy-specific."
            )

    if args.task == "timestamp":
        if "timestamp" not in seed_path_str or "timestamp" not in topk_path_str:
            raise ValueError(
                "Task is timestamp but seed/topk path does not look timestamp-specific."
            )

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_rows_raw = read_jsonl(args.seed_jsonl)
    topk_rows = read_jsonl(args.topk_jsonl)
    inv_rows = read_jsonl(args.inventory_jsonl)

    seed_by_key: Dict[str, Dict[str, Any]] = {}
    for row in seed_rows_raw:
        seed_by_key[get_function_key(row)] = row

    inventory_by_key: Dict[str, Dict[str, Any]] = {}
    for row in inv_rows:
        inventory_by_key[get_function_key(row)] = row

    relations_by_seed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rel in topk_rows:
        seed_fk = safe_str(rel.get("seed_function_key", ""))
        if seed_fk:
            relations_by_seed[seed_fk].append(rel)

    raw_graphs: List[Dict[str, Any]] = []
    stats = Counter()
    label_counts = Counter()
    relation_counts = Counter()

    for seed_fk, seed_row in seed_by_key.items():
        rels = relations_by_seed.get(seed_fk, [])

        g = build_one_global_graph(
            seed_row=seed_row,
            relations=rels,
            inventory_by_key=inventory_by_key,
            max_related=args.max_related,
            include_state_nodes=args.include_state_nodes,
            max_state_vars_per_relation=args.max_state_vars_per_relation,
            task=args.task,
            neutral_relation_names=args.neutral_relation_names,
            no_anchor_to_related=args.no_anchor_to_related,
            include_callsite_nodes=args.include_callsite_nodes,
            include_env_nodes=args.include_env_nodes,
        )

        if (
            args.skip_empty_global_graphs
            and safe_int(g.get("metadata", {}).get("global_related_function_count"), 0) == 0
        ):
            stats["graphs_skipped_empty_global"] += 1
            continue

        raw_graphs.append(g)

        stats["graphs_built"] += 1
        stats["total_nodes"] += len(g["nodes"])
        stats["total_edges"] += len(g["edges"])
        stats["global_callsite_node_count"] += safe_int(
            g.get("metadata", {}).get("global_callsite_node_count"),
            0,
        )
        stats["global_external_context_node_count"] += safe_int(
            g.get("metadata", {}).get("global_external_context_node_count"),
            0,
        )
        stats["global_env_node_count"] += safe_int(
            g.get("metadata", {}).get("global_env_node_count"),
            0,
        )
        if not rels:
            stats["graphs_without_topk_relation"] += 1
        if safe_int(g.get("metadata", {}).get("global_related_function_count"), 0) == 0:
            stats["graphs_without_allowed_topk_relation"] += 1

        label_counts[str(get_label(seed_row))] += 1

        for rt, count in g.get("metadata", {}).get("global_relation_type_counts", {}).items():
            relation_counts[safe_str(rt)] += safe_int(count)

    vocabs = build_vocab(raw_graphs)
    encoded_graphs = [encode_graph(g, vocabs) for g in raw_graphs]

    out_jsonl = out_dir / "global_context_graphs.jsonl"
    with out_jsonl.open("w", encoding="utf-8") as f:
        for g in encoded_graphs:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    vocab_json = out_dir / "vocab.json"
    write_json(vocab_json, vocabs)

    total = max(1, len(encoded_graphs))
    relation_types_for_summary = [
        "INTERNAL_CALL_IN",
        "INTERNAL_CALL_OUT",
        "SHARED_STATE_WRITE_READ",
        "SHARED_STATE_WRITE_WRITE",
    ]
    allowed_relation_types_for_summary = [
        rt for rt in relation_types_for_summary
        if relation_allowed(args.task, rt)
    ]
    summary = {
        "stage": "step13_build_lightweight_global_context_graphs",
        "seed_jsonl": str(args.seed_jsonl),
        "topk_jsonl": str(args.topk_jsonl),
        "inventory_jsonl": str(args.inventory_jsonl),
        "output_jsonl": str(out_jsonl),
        "vocab_json": str(vocab_json),
        "task": args.task,
        "allowed_relation_types": allowed_relation_types_for_summary,
        "neutral_relation_names": bool(args.neutral_relation_names),
        "skip_empty_global_graphs": bool(args.skip_empty_global_graphs),
        "graphs_skipped_empty_global": stats.get("graphs_skipped_empty_global", 0),
        "no_anchor_to_related": bool(args.no_anchor_to_related),
        "include_callsite_nodes": bool(args.include_callsite_nodes),
        "include_env_nodes": bool(args.include_env_nodes),
        "max_related": args.max_related,
        "include_state_nodes": bool(args.include_state_nodes),
        "max_state_vars_per_relation": args.max_state_vars_per_relation,
        "graphs_built": len(encoded_graphs),
        "label_counts": dict(label_counts),
        "graphs_without_topk_relation": stats.get("graphs_without_topk_relation", 0),
        "graphs_without_allowed_topk_relation": stats.get(
            "graphs_without_allowed_topk_relation", 0
        ),
        "total_nodes": stats.get("total_nodes", 0),
        "total_edges": stats.get("total_edges", 0),
        "global_callsite_node_count": stats.get("global_callsite_node_count", 0),
        "global_external_context_node_count": stats.get(
            "global_external_context_node_count", 0
        ),
        "global_env_node_count": stats.get("global_env_node_count", 0),
        "avg_nodes_per_graph": stats.get("total_nodes", 0) / total,
        "avg_edges_per_graph": stats.get("total_edges", 0) / total,
        "relation_type_counts_built": dict(relation_counts),
        "relation_type_counts_from_topk": dict(relation_counts),
        "vocab_sizes": {
            "view": vocabs["view"]["size"],
            "node_kind": vocabs["node_kind"]["size"],
            "node_type": vocabs["node_type"]["size"],
            "token": vocabs["token"]["size"],
            "edge_type": vocabs["edge_type"]["size"],
        },
        "feature_names": [
            "view_id",
            "node_kind_id",
            "node_type_id",
            "token_id",
            "is_seed",
            "is_related",
            "is_state_var",
            "is_callsite",
            "is_external_context",
            "is_env_context",
            "is_fallback",
            "is_receive",
            "is_payable",
            "is_external_or_public",
        ],
        "note": (
            "This is a lightweight global context graph. Related functions are function nodes only. "
            "Raw function names and state variable names are metadata only, not token features. "
            "If neutral_relation_names=True, vulnerability-specific relation edge names are mapped "
            "to neutral edge names before encoding. "
            "Use this output in the next local+global fusion step."
        ),
    }

    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
