

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict, Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


KEYWORDS = {
    "if", "else", "for", "while", "do", "return", "returns", "require", "assert",
    "revert", "throw", "emit", "new", "delete", "public", "private", "internal",
    "external", "view", "pure", "payable", "memory", "storage", "calldata",
    "uint", "uint256", "uint128", "uint64", "uint32", "uint16", "uint8",
    "int", "int256", "bool", "address", "string", "bytes", "byte", "mapping",
    "contract", "library", "interface", "struct", "enum", "event", "modifier",
    "function", "constructor", "fallback", "receive", "true", "false", "this",
    "msg", "sender", "value", "block", "timestamp", "now", "tx", "origin",
    "balance", "call", "transfer", "send", "gas", "var", "constant",
}

DECL_SKIP_PREFIXES = (
    "function", "modifier", "event", "struct", "enum", "using", "import",
    "pragma", "constructor", "fallback", "receive", "error"
)


RE_REENTRANCY_EXTERNAL_VALUE_CALL = re.compile(
    r"("
    r"\.\s*call\s*\.\s*value\s*\("
    r"|"
    r"\.\s*call\s*\{\s*value\s*:"
    r")",
    re.IGNORECASE | re.DOTALL,
)

RE_LOW_LEVEL_CALL = re.compile(
    r"\.\s*(call|delegatecall|staticcall)\s*(?:\{|\.|\()",
    re.IGNORECASE | re.DOTALL,
)

RE_VALUE_TRANSFER_CALL = re.compile(
    r"("
    r"\.\s*call\s*\.\s*value\s*\("
    r"|"
    r"\.\s*call\s*\{\s*value\s*:"
    r"|"
    r"\.\s*send\s*\("
    r")",
    re.IGNORECASE | re.DOTALL,
)

RE_TOKEN_TRANSFER_CALL = re.compile(
    r"\.\s*(transfer|transferFrom|safeTransfer|safeTransferFrom)\s*\(",
    re.IGNORECASE,
)

RE_ENV_SOURCE = re.compile(
    r"\b(block\s*\.\s*timestamp|now|block\s*\.\s*number|blockhash\s*\()\b",
    re.IGNORECASE,
)

RE_TIMESTAMP_SOURCE = re.compile(
    r"\bblock\s*\.\s*timestamp\b",
    re.IGNORECASE,
)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
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
    return row.get("metadata", {}) or row


def get_contract_key(row: Dict[str, Any]) -> str:
    m = get_meta(row)
    if m.get("contract_key"):
        return str(m["contract_key"])
    return "|".join([
        str(m.get("relative_path", "")),
        str(m.get("contract_name", "")),
    ])


def get_function_key(row: Dict[str, Any]) -> str:
    m = get_meta(row)
    if m.get("function_key"):
        return str(m["function_key"])
    return "|".join([
        str(m.get("relative_path", "")),
        str(m.get("contract_name", "")),
        str(m.get("function_name", "")),
        str(m.get("function_kind", "")),
        str(m.get("line_start", "")),
        str(m.get("line_end", "")),
        str(m.get("param_count", "")),
    ])


def get_relative_path(row: Dict[str, Any]) -> str:
    m = get_meta(row)
    return str(m.get("relative_path") or "")


def get_source_path(row: Dict[str, Any], source_root: Path | None = None) -> Path | None:
    m = get_meta(row)
    if m.get("source_path"):
        p = Path(str(m["source_path"]))
        if p.exists():
            return p
    rel = m.get("relative_path")
    if rel and source_root:
        p = source_root / str(rel)
        if p.exists():
            return p
    return None


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def extract_function_text(row: Dict[str, Any], source_text: str) -> str:
    m = get_meta(row)

    byte_start = m.get("byte_start")
    byte_length = m.get("byte_length")
    if byte_start is not None and byte_length is not None:
        s = safe_int(byte_start)
        l = safe_int(byte_length)
        if s >= 0 and l > 0 and s + l <= len(source_text):
            return source_text[s:s + l]

    line_start = safe_int(m.get("line_start"), 0)
    line_end = safe_int(m.get("line_end"), 0)
    if line_start > 0 and line_end >= line_start:
        lines = source_text.splitlines()
        return "\n".join(lines[line_start - 1:line_end])

    return ""


def strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    code = re.sub(r"//.*", " ", code)
    return code


def find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def extract_contract_text(source: str, contract_name: str) -> str:
    if not contract_name:
        return source

    pattern = re.compile(r"\b(contract|library|interface)\s+" + re.escape(contract_name) + r"\b")
    m = pattern.search(source)
    if not m:
        return source

    brace = source.find("{", m.end())
    if brace < 0:
        return source

    end = find_matching_brace(source, brace)
    if end < 0:
        return source[brace + 1:]

    return source[brace + 1:end]


def remove_function_blocks(contract_text: str) -> str:
    text = contract_text
    out = []
    pos = 0
    pattern = re.compile(r"\b(function|modifier|constructor|fallback|receive)\b")

    while True:
        m = pattern.search(text, pos)
        if not m:
            out.append(text[pos:])
            break

        out.append(text[pos:m.start()])
        brace = text.find("{", m.end())
        semicolon = text.find(";", m.end())

        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            end = find_matching_brace(text, brace)
            if end >= 0:
                pos = end + 1
            else:
                pos = brace + 1
        elif semicolon >= 0:
            pos = semicolon + 1
        else:
            pos = m.end()

    return "\n".join(out)


def extract_state_vars(contract_text: str) -> Set[str]:
    clean = strip_comments(remove_function_blocks(contract_text))
    state_vars = set()

    for stmt in clean.split(";"):
        s = " ".join(stmt.strip().split())
        if not s:
            continue

        low = s.lower()
        if low.startswith(DECL_SKIP_PREFIXES):
            continue
        if "{" in s or "}" in s:
            continue

        # Remove declaration initializer without breaking mapping arrows (=>).
        s = re.split(r"(?<![!<>=])=(?!=|>)", s, maxsplit=1)[0].strip()

        # Remove array suffix from final variable
        ids = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", s)
        ids = [x for x in ids if x.lower() not in KEYWORDS]
        if not ids:
            continue

        # last identifier is usually variable name
        name = ids[-1]
        if len(name) >= 2:
            state_vars.add(name)

    return state_vars


def identifiers(code: str) -> Set[str]:
    code = strip_comments(code)
    ids = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code)
    return {x for x in ids if x.lower() not in KEYWORDS}


def state_var_access_pattern(name: str) -> str:
    return (
        r"\b" + re.escape(name) + r"\b"
        r"\s*(?:\[[^\]]*\]\s*|\.\s*[A-Za-z_][A-Za-z0-9_]*\s*)*"
    )


def state_read_write_sets(code: str, state_vars_l: Set[str]) -> Tuple[Set[str], Set[str]]:
    clean = strip_comments(code)
    writes: Set[str] = set()
    read_text = clean

    assignment_ops = r"(?:\+=|-=|\*=|/=|%=|\|=|&=|\^=|<<=|>>=|(?<![=!<>])=(?!=))"

    for var in state_vars_l:
        access = state_var_access_pattern(var)

        simple_assign = re.compile(access + r"(?<![=!<>])=(?!=)", re.IGNORECASE)
        compound_assign = re.compile(access + assignment_ops, re.IGNORECASE)
        prefix_update = re.compile(r"(?:\+\+|--)\s*" + access, re.IGNORECASE)
        suffix_update = re.compile(access + r"\s*(?:\+\+|--)", re.IGNORECASE)
        delete_update = re.compile(r"\bdelete\s+" + access, re.IGNORECASE)
        mutating_method = re.compile(
            r"\b" + re.escape(var) + r"\b\s*(?:\[[^\]]*\]\s*)*\.\s*(?:push|pop)\s*\(",
            re.IGNORECASE,
        )

        if (
            compound_assign.search(clean)
            or prefix_update.search(clean)
            or suffix_update.search(clean)
            or delete_update.search(clean)
            or mutating_method.search(clean)
        ):
            writes.add(var)

        # Simple assignment and delete LHS are write-only for this heuristic.
        read_text = simple_assign.sub(" ", read_text)
        read_text = delete_update.sub(" ", read_text)

    reads = {x.lower() for x in identifiers(read_text)}.intersection(state_vars_l)
    return reads, writes


def called_function_names(code: str, all_function_names: Set[str]) -> Set[str]:
    code = strip_comments(code or "")
    out = set()
    for name in all_function_names:
        if not name or name.startswith("__"):
            continue
        if re.search(r"\b" + re.escape(name) + r"\s*\(", code):
            out.add(name)
    return out


def is_fallback_function(row: Dict[str, Any]) -> bool:
    m = get_meta(row)
    kind = str(m.get("function_kind", "")).lower()
    name = str(m.get("function_name", "")).lower()
    return (
        m.get("is_fallback") is True
        or kind == "fallback"
        or name == "fallback"
    )


def is_receive_function(row: Dict[str, Any]) -> bool:
    m = get_meta(row)
    kind = str(m.get("function_kind", "")).lower()
    name = str(m.get("function_name", "")).lower()
    return (
        m.get("is_receive") is True
        or kind == "receive"
        or name == "receive"
    )


def has_external_value_call(code: str) -> bool:
    return RE_VALUE_TRANSFER_CALL.search(code or "") is not None


def timestamp_written_state_vars(code: str, state_vars_l: Set[str]) -> Set[str]:
    """
    Return state variables that are written using block.timestamp.
    Example:
      unlockTime = block.timestamp + 1 days;
    """
    out: Set[str] = set()

    for stmt in re.split(r";|\n", strip_comments(code or "")):
        if not RE_TIMESTAMP_SOURCE.search(stmt):
            continue

        _reads, writes = state_read_write_sets(stmt, state_vars_l)
        out.update(writes)

    return out


def function_facts(
    code: str,
    reads: Set[str],
    writes: Set[str],
    internal_calls: Set[str],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    code = code or ""
    mut = str(meta.get("state_mutability", "")).lower()
    vis = str(meta.get("visibility", "")).lower()

    call_count = len(internal_calls)
    low_level_count = len(RE_LOW_LEVEL_CALL.findall(code))
    value_transfer_count = len(RE_VALUE_TRANSFER_CALL.findall(code))
    token_transfer_count = len(RE_TOKEN_TRANSFER_CALL.findall(code))
    env_count = len(RE_ENV_SOURCE.findall(code))

    return {
        "is_public_or_external": 1 if vis in {"public", "external"} else 0,
        "is_payable": 1 if mut == "payable" else 0,
        "is_view_or_pure": 1 if mut in {"view", "pure"} else 0,

        "has_internal_call": 1 if call_count > 0 else 0,
        "has_low_level_call": 1 if low_level_count > 0 else 0,
        "has_value_transfer": 1 if value_transfer_count > 0 else 0,
        "has_token_transfer": 1 if token_transfer_count > 0 else 0,
        "has_env_read": 1 if env_count > 0 else 0,

        "has_state_read": 1 if reads else 0,
        "has_state_write": 1 if writes else 0,

        "num_internal_calls": call_count,
        "num_low_level_calls": low_level_count,
        "num_value_transfers": value_transfer_count,
        "num_token_transfers": token_transfer_count,
        "num_env_reads": env_count,
        "num_state_reads": len(reads),
        "num_state_writes": len(writes),
    }


def fact_score(task: str, seed_facts: Dict[str, Any], other_facts: Dict[str, Any]) -> int:
    task = (task or "").lower()
    score = 0

    if task == "reentrancy":
        if seed_facts.get("has_value_transfer"):
            score += 2
        if seed_facts.get("has_low_level_call"):
            score += 2
        if other_facts.get("has_state_write"):
            score += 2
        if other_facts.get("has_value_transfer"):
            score += 1
        if other_facts.get("is_view_or_pure"):
            score -= 2

    elif task == "timestamp":
        if seed_facts.get("has_env_read"):
            score += 2
        if seed_facts.get("has_state_write"):
            score += 2
        if other_facts.get("has_state_read"):
            score += 2
        if other_facts.get("is_view_or_pure") and not other_facts.get("has_state_write"):
            score -= 1

    return score


def is_internal_only(info: Dict[str, Any]) -> bool:
    rels = set(info.get("relation_types", set()))
    return bool(rels) and rels.issubset({"INTERNAL_CALL_IN", "INTERNAL_CALL_OUT"})


def load_graph_key_set(path: Path | None) -> Set[str]:
    if not path:
        return set()
    if not path.exists():
        return set()
    keys = set()
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            keys.add(get_function_key(row))
    return keys


def relation_allowed(task: str, relation_type: str) -> bool:
    """
    Clean generic relation filter.

    For node-label-only / no-shortcut setting:
      both reentrancy and timestamp use the same generic function-interaction relations.
      No vulnerability-specific relation type is exposed.
    """
    task = (task or "all").lower()

    if task == "all":
        return True

    if task in {"reentrancy", "timestamp"}:
        return relation_type in {
            "INTERNAL_CALL_IN",
            "INTERNAL_CALL_OUT",
            "SHARED_STATE_WRITE_READ",
            "SHARED_STATE_WRITE_WRITE",
        }

    return False


def relation_score(task: str, relation_type: str) -> int:
    task = (task or "all").lower()

    if task in {"reentrancy", "timestamp"}:
        scores = {
            "SHARED_STATE_WRITE_READ": 6,
            "SHARED_STATE_WRITE_WRITE": 5,
            "INTERNAL_CALL_IN": 1,
            "INTERNAL_CALL_OUT": 1,
        }
    else:
        scores = {
            "INTERNAL_CALL_IN": 5,
            "INTERNAL_CALL_OUT": 5,
            "SHARED_STATE_WRITE_READ": 4,
            "SHARED_STATE_WRITE_WRITE": 4,
        }

    return scores.get(relation_type, 1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Seed-function contract relation coverage analysis."
    )
    ap.add_argument("--seed-jsonl", type=Path, required=True,
                    help="Step8 encoded JSONL or Step9 labeled JSONL.")
    ap.add_argument("--inventory-jsonl", type=Path, required=True,
                    help="Step3 function_inventory.jsonl")
    ap.add_argument("--source-root", type=Path, required=True,
                    help="Cleaned .sol files folder.")
    ap.add_argument("--ast-graphs", type=Path, default=None)
    ap.add_argument("--cfg-graphs", type=Path, default=None)
    ap.add_argument("--dfg-graphs", type=Path, default=None)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--top-k", type=int, default=5,
                    help="Number of related functions to select per seed.")
    ap.add_argument("--include-read-read", action="store_true",
                    help="Include weak read-read shared-state relations.")
    ap.add_argument("--prefer-existing-graphs", action="store_true",
                    help="Give bonus if related function already has AST/CFG/DFG graphs.")
    ap.add_argument(
        "--disable-fact-score",
        action="store_true",
        help="Disable vulnerability-specific fact_score bonus for clean node-label-only ablation.",
    )
    ap.add_argument(
        "--task",
        choices=["reentrancy", "timestamp", "all"],
        required=True,
        help="Clean relation filtering. "
             "reentrancy/timestamp both keep only generic internal-call and "
             "shared-state write-read/write-write relations; "
             "all keeps every relation for coverage/debug."
    )
    args = ap.parse_args()

    seed_path_str = str(args.seed_jsonl).lower()
    if args.task == "reentrancy" and "reentrancy" not in seed_path_str:
        raise ValueError(
            "Task is reentrancy but seed-jsonl path does not look "
            f"reentrancy-specific: {args.seed_jsonl}"
        )
    if args.task == "timestamp" and "timestamp" not in seed_path_str:
        raise ValueError(
            "Task is timestamp but seed-jsonl path does not look "
            f"timestamp-specific: {args.seed_jsonl}"
        )

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_rows_raw = read_jsonl(args.seed_jsonl)
    inv_rows_raw = read_jsonl(args.inventory_jsonl)

    # Only implemented functions from inventory
    inv_rows = []
    for r in inv_rows_raw:
        m = get_meta(r)
        if m.get("implemented") is False:
            continue
        inv_rows.append(r)

    # Deduplicate seeds by function_key
    seed_by_key = {}
    for r in seed_rows_raw:
        seed_by_key[get_function_key(r)] = r
    seed_rows = list(seed_by_key.values())
    seed_keys = set(seed_by_key.keys())

    inventory_by_contract = defaultdict(list)
    inventory_by_key = {}

    for r in inv_rows:
        ck = get_contract_key(r)
        fk = get_function_key(r)
        inventory_by_contract[ck].append(r)
        inventory_by_key[fk] = r

    seed_contracts = sorted({get_contract_key(r) for r in seed_rows})

    ast_keys = load_graph_key_set(args.ast_graphs)
    cfg_keys = load_graph_key_set(args.cfg_graphs)
    dfg_keys = load_graph_key_set(args.dfg_graphs)

    source_cache: Dict[Path, str] = {}

    per_seed = []
    per_contract_summary = []
    all_ranked_related = []
    all_topk_related = []
    expanded_function_keys = set()
    expanded_rows_by_key = {}

    count_internal = 0
    count_shared = 0
    count_isolated = 0
    count_related_all3_seed = 0
    count_reentrancy_helper_state_write_seed = 0
    count_timestamp_cross_function_state_use_seed = 0
    processed_seed_functions = 0
    skipped_seed_functions_missing_inventory = 0
    skipped_seed_functions_missing_source = 0

    unique_related_keys = set()
    unique_related_ast = set()
    unique_related_cfg = set()
    unique_related_dfg = set()
    unique_related_all3 = set()

    contract_function_counts = []
    contracts_with_fallback = 0
    contracts_with_receive = 0
    contracts_with_fallback_or_receive = 0
    fallback_receive_functions_total = 0
    seed_functions_in_contract_with_fallback_or_receive = 0
    missing_inventory_contracts = 0
    missing_source_contracts = 0

    for ck in tqdm(seed_contracts, desc="Analyzing seed contracts", unit="contract"):
        contract_funcs = inventory_by_contract.get(ck, [])

        if not contract_funcs:
            missing_inventory_contracts += 1
            skipped_seed_functions_missing_inventory += len([
                r for r in seed_rows if get_contract_key(r) == ck
            ])
            continue

        contract_function_counts.append(len(contract_funcs))

        seed_funcs_in_contract = [r for r in seed_rows if get_contract_key(r) == ck]

        fallback_funcs = [r for r in contract_funcs if is_fallback_function(r)]
        receive_funcs = [r for r in contract_funcs if is_receive_function(r)]

        has_fallback = bool(fallback_funcs)
        has_receive = bool(receive_funcs)
        has_fallback_or_receive = has_fallback or has_receive

        if has_fallback:
            contracts_with_fallback += 1
        if has_receive:
            contracts_with_receive += 1
        if has_fallback_or_receive:
            contracts_with_fallback_or_receive += 1
            seed_functions_in_contract_with_fallback_or_receive += len(seed_funcs_in_contract)

        fallback_receive_functions_total += len(fallback_funcs) + len(receive_funcs)

        sample_row = seed_funcs_in_contract[0] if seed_funcs_in_contract else contract_funcs[0]
        sp = get_source_path(sample_row, args.source_root)
        if not sp or not sp.exists():
            missing_source_contracts += 1
            skipped_seed_functions_missing_source += len(seed_funcs_in_contract)
            continue

        if sp not in source_cache:
            source_cache[sp] = sp.read_text(encoding="utf-8", errors="ignore")
        source_text = source_cache[sp]

        contract_name = get_meta(sample_row).get("contract_name", "")
        contract_text = extract_contract_text(source_text, str(contract_name))
        state_vars = extract_state_vars(contract_text)
        state_vars_l = {x.lower() for x in state_vars}

        function_texts = {}
        function_state_reads = {}
        function_state_writes = {}
        function_timestamp_state_writes = {}
        function_internal_calls = {}
        function_facts_by_key = {}
        function_names = set()

        for fr in contract_funcs:
            fm = get_meta(fr)
            fname = str(fm.get("function_name", ""))
            if fname:
                function_names.add(fname)

        for fr in contract_funcs:
            fm = get_meta(fr)
            fk = get_function_key(fr)
            fname = str(fm.get("function_name", ""))
            ftxt = extract_function_text(fr, source_text)
            function_texts[fk] = ftxt
            reads, writes = state_read_write_sets(ftxt, state_vars_l)
            internal_calls = called_function_names(ftxt, function_names)
            internal_calls.discard(fname)
            function_state_reads[fk] = reads
            function_state_writes[fk] = writes
            function_internal_calls[fk] = internal_calls
            function_timestamp_state_writes[fk] = timestamp_written_state_vars(ftxt, state_vars_l)
            function_facts_by_key[fk] = function_facts(
                code=ftxt,
                reads=reads,
                writes=writes,
                internal_calls=internal_calls,
                meta=fm,
            )

        contract_internal_edges = 0
        contract_shared_edges = 0

        for seed in seed_funcs_in_contract:
            processed_seed_functions += 1
            sm = get_meta(seed)
            seed_fk = get_function_key(seed)
            seed_name = str(sm.get("function_name", ""))
            seed_text = function_texts.get(seed_fk) or extract_function_text(seed, source_text)

            seed_calls = called_function_names(seed_text, function_names)
            seed_calls.discard(seed_name)
            seed_reads = function_state_reads.get(seed_fk, set())
            seed_writes = function_state_writes.get(seed_fk, set())
            seed_timestamp_state_writes = function_timestamp_state_writes.get(seed_fk, set())
            seed_facts = function_facts_by_key.get(seed_fk, {})
            seed_has_external_value_call = has_external_value_call(seed_text)

            internal_related = set()
            shared_related = set()
            reentrancy_helper_related = set()
            timestamp_cross_related = set()
            shared_vars_total = set()
            reentrancy_helper_vars_total = set()
            timestamp_cross_vars_total = set()
            related_info = defaultdict(lambda: {
                "relation_types": set(),
                "shared_vars": set(),
                "relation_vars_by_type": defaultdict(set),
                "score": 0,
                "has_ast": False,
                "has_cfg": False,
                "has_dfg": False,
                "has_all_ast_cfg_dfg": False,
                "seed_facts": {},
                "related_facts": {},
                "state_access_role": {
                    "seed_reads_shared": 0,
                    "seed_writes_shared": 0,
                    "related_reads_shared": 0,
                    "related_writes_shared": 0,
                },
            })

            def add_relation(
                related_fk: str,
                relation_type: str,
                relation_vars: Optional[Set[str]] = None,
            ) -> bool:
                if not relation_allowed(args.task, relation_type):
                    return False

                info = related_info[related_fk]
                info["relation_types"].add(relation_type)
                info["score"] += relation_score(args.task, relation_type)
                info["seed_facts"] = seed_facts
                info["related_facts"] = other_facts
                if relation_vars:
                    info["shared_vars"].update(relation_vars)
                    info["relation_vars_by_type"][relation_type].update(relation_vars)

                    role = info["state_access_role"]
                    role["seed_reads_shared"] = max(
                        role["seed_reads_shared"],
                        len(seed_reads.intersection(relation_vars)),
                    )
                    role["seed_writes_shared"] = max(
                        role["seed_writes_shared"],
                        len(seed_writes.intersection(relation_vars)),
                    )
                    role["related_reads_shared"] = max(
                        role["related_reads_shared"],
                        len(other_reads.intersection(relation_vars)),
                    )
                    role["related_writes_shared"] = max(
                        role["related_writes_shared"],
                        len(other_writes.intersection(relation_vars)),
                    )
                return True

            for other in contract_funcs:
                other_fk = get_function_key(other)
                if other_fk == seed_fk:
                    continue

                om = get_meta(other)
                other_name = str(om.get("function_name", ""))
                other_text = function_texts.get(other_fk, "")
                other_calls = function_internal_calls.get(other_fk, set())
                other_reads = function_state_reads.get(other_fk, set())
                other_writes = function_state_writes.get(other_fk, set())
                other_timestamp_state_writes = function_timestamp_state_writes.get(other_fk, set())
                other_facts = function_facts_by_key.get(other_fk, {})

                # seed calls other OR other calls seed
                if (
                    other_name in seed_calls
                    and relation_allowed(args.task, "INTERNAL_CALL_OUT")
                ):
                    if add_relation(other_fk, "INTERNAL_CALL_OUT"):
                        internal_related.add(other_fk)

                if (
                    seed_name in other_calls
                    and relation_allowed(args.task, "INTERNAL_CALL_IN")
                ):
                    if add_relation(other_fk, "INTERNAL_CALL_IN"):
                        internal_related.add(other_fk)

                # Reentrancy global helper pattern:
                # seed has external value call, seed calls helper, helper writes state shared with seed.
                if (
                    relation_allowed(args.task, "REENTRANCY_HELPER_STATE_WRITE")
                    and seed_has_external_value_call
                    and other_name in seed_calls
                ):
                    helper_shared_writes = other_writes.intersection(seed_reads | seed_writes)

                    if helper_shared_writes:
                        if add_relation(
                            other_fk,
                            "REENTRANCY_HELPER_STATE_WRITE",
                            helper_shared_writes,
                        ):
                            reentrancy_helper_related.add(other_fk)
                            reentrancy_helper_vars_total.update(helper_shared_writes)

                shared_write_read = (
                    seed_writes.intersection(other_reads)
                    | other_writes.intersection(seed_reads)
                )
                shared_write_write = seed_writes.intersection(other_writes)
                shared_read_read = seed_reads.intersection(other_reads)
                weak_read_read = shared_read_read - shared_write_read - shared_write_write

                shared_vars = set()
                if (
                    shared_write_read
                    and relation_allowed(args.task, "SHARED_STATE_WRITE_READ")
                ):
                    if add_relation(other_fk, "SHARED_STATE_WRITE_READ", shared_write_read):
                        shared_vars.update(shared_write_read)

                if (
                    shared_write_write
                    and relation_allowed(args.task, "SHARED_STATE_WRITE_WRITE")
                ):
                    if add_relation(other_fk, "SHARED_STATE_WRITE_WRITE", shared_write_write):
                        shared_vars.update(shared_write_write)

                if (
                    args.include_read_read
                    and weak_read_read
                    and relation_allowed(args.task, "SHARED_STATE_READ_READ_WEAK")
                ):
                    if add_relation(other_fk, "SHARED_STATE_READ_READ_WEAK", weak_read_read):
                        shared_vars.update(weak_read_read)

                if shared_vars:
                    shared_related.add(other_fk)
                    shared_vars_total.update(shared_vars)
                    related_info[other_fk]["shared_vars"].update(shared_vars)

                # Timestamp global pattern:
                # seed writes state using block.timestamp, another function reads same state.
                timestamp_vars_seed_to_other = seed_timestamp_state_writes.intersection(other_reads)

                if (
                    timestamp_vars_seed_to_other
                    and relation_allowed(args.task, "TIMESTAMP_STATE_WRITE_READ")
                ):
                    if add_relation(
                        other_fk,
                        "TIMESTAMP_STATE_WRITE_READ",
                        timestamp_vars_seed_to_other,
                    ):
                        timestamp_cross_related.add(other_fk)
                        timestamp_cross_vars_total.update(timestamp_vars_seed_to_other)

                # Optional reverse pattern:
                # other function writes timestamp-derived state, seed reads it.
                timestamp_vars_other_to_seed = other_timestamp_state_writes.intersection(seed_reads)

                if (
                    timestamp_vars_other_to_seed
                    and relation_allowed(args.task, "TIMESTAMP_STATE_READ_FROM_OTHER_WRITE")
                ):
                    if add_relation(
                        other_fk,
                        "TIMESTAMP_STATE_READ_FROM_OTHER_WRITE",
                        timestamp_vars_other_to_seed,
                    ):
                        timestamp_cross_related.add(other_fk)
                        timestamp_cross_vars_total.update(timestamp_vars_other_to_seed)

                if other_fk in related_info:
                    related_info[other_fk]["seed_facts"] = seed_facts
                    related_info[other_fk]["related_facts"] = other_facts
                    if not args.disable_fact_score:
                        related_info[other_fk]["score"] += fact_score(
                            args.task,
                            seed_facts,
                            other_facts,
                        )

            related = (
                internal_related
                .union(shared_related)
                .union(reentrancy_helper_related)
                .union(timestamp_cross_related)
            )

            for other_fk, info in related_info.items():
                info["has_ast"] = other_fk in ast_keys
                info["has_cfg"] = other_fk in cfg_keys
                info["has_dfg"] = other_fk in dfg_keys
                info["has_all_ast_cfg_dfg"] = (
                    info["has_ast"] and info["has_cfg"] and info["has_dfg"]
                )
                if args.task == "timestamp" and is_internal_only(info):
                    info["score"] -= 2
                if args.prefer_existing_graphs and info["has_all_ast_cfg_dfg"]:
                    info["score"] += 3

            ranked_related = []
            for rk, info in related_info.items():
                ranked_related.append({
                    "seed_function_key": seed_fk,
                    "related_function_key": rk,
                    "contract_key": ck,
                    "relative_path": sm.get("relative_path", ""),
                    "contract_name": sm.get("contract_name", ""),
                    "seed_function_name": sm.get("function_name", ""),
                    "relation_types": sorted(info["relation_types"]),
                    "shared_vars": sorted(info["shared_vars"]),
                    "relation_vars_by_type": {
                        rt: sorted(vars_for_type)
                        for rt, vars_for_type in info.get("relation_vars_by_type", {}).items()
                    },
                    "seed_function_facts": info.get("seed_facts", {}),
                    "related_function_facts": info.get("related_facts", {}),
                    "state_access_role": info.get("state_access_role", {}),
                    "score": info["score"],
                    "has_ast": info["has_ast"],
                    "has_cfg": info["has_cfg"],
                    "has_dfg": info["has_dfg"],
                    "has_all_ast_cfg_dfg": info["has_all_ast_cfg_dfg"],
                })

            ranked_related.sort(
                key=lambda x: (
                    x["score"],
                    x["has_all_ast_cfg_dfg"],
                    len(x["relation_types"]),
                ),
                reverse=True,
            )

            # Timestamp global context should not force weak/noisy internal-only
            # relations just to fill top-k. In clean mode, top-k is "up to k".
            if args.task == "timestamp":
                ranked_related = [
                    x for x in ranked_related
                    if x.get("score", 0) > 0
                ]

            topk_related = ranked_related[:args.top_k]
            for i, item in enumerate(topk_related, start=1):
                item["rank"] = i
                item["selected_topk"] = True

            all_ranked_related.extend(ranked_related)
            all_topk_related.extend(topk_related)

            expanded_function_keys.add(seed_fk)
            expanded_rows_by_key[seed_fk] = seed

            for item in topk_related:
                rk = item["related_function_key"]
                expanded_function_keys.add(rk)
                if rk in inventory_by_key:
                    expanded_rows_by_key[rk] = inventory_by_key[rk]

            unique_related_keys.update(related)

            related_ast = {x for x in related if x in ast_keys}
            related_cfg = {x for x in related if x in cfg_keys}
            related_dfg = {x for x in related if x in dfg_keys}
            related_all3 = {x for x in related if x in ast_keys and x in cfg_keys and x in dfg_keys}

            unique_related_ast.update(related_ast)
            unique_related_cfg.update(related_cfg)
            unique_related_dfg.update(related_dfg)
            unique_related_all3.update(related_all3)

            has_internal = bool(internal_related)
            has_shared = bool(shared_related)
            has_reentrancy_helper = bool(reentrancy_helper_related)
            has_timestamp_cross = bool(timestamp_cross_related)
            has_any = has_internal or has_shared or has_reentrancy_helper or has_timestamp_cross

            if has_internal:
                count_internal += 1
            if has_shared:
                count_shared += 1
            if reentrancy_helper_related:
                count_reentrancy_helper_state_write_seed += 1
            if timestamp_cross_related:
                count_timestamp_cross_function_state_use_seed += 1
            if not has_any:
                count_isolated += 1
            if related_all3:
                count_related_all3_seed += 1

            contract_internal_edges += len(internal_related)
            contract_shared_edges += len(shared_related)

            per_seed.append({
                "contract_key": ck,
                "function_key": seed_fk,
                "relative_path": sm.get("relative_path", ""),
                "contract_name": sm.get("contract_name", ""),
                "function_name": sm.get("function_name", ""),
                "line_start": sm.get("line_start", ""),
                "line_end": sm.get("line_end", ""),
                "contract_total_functions": len(contract_funcs),
                "has_external_value_call": seed_has_external_value_call,
                "timestamp_state_written_vars": (
                    sorted(seed_timestamp_state_writes)
                    if args.task in {"timestamp", "all"} else []
                ),
                "internal_call_related_functions": len(internal_related),
                "shared_state_related_functions": len(shared_related),
                "reentrancy_helper_state_write_related_functions": len(reentrancy_helper_related),
                "reentrancy_helper_state_write_vars": (
                    sorted(reentrancy_helper_vars_total)
                    if args.task in {"reentrancy", "all"} else []
                ),
                "timestamp_cross_function_state_use_related_functions": len(timestamp_cross_related),
                "timestamp_cross_function_state_vars": (
                    sorted(timestamp_cross_vars_total)
                    if args.task in {"timestamp", "all"} else []
                ),
                "total_related_functions": len(related),
                "shared_state_vars": (
                    sorted(shared_vars_total)
                    if args.task in {"reentrancy", "all"} else []
                ),
                "contract_has_fallback": has_fallback,
                "contract_has_receive": has_receive,
                "contract_has_fallback_or_receive": has_fallback_or_receive,
                "is_isolated": not has_any,
                "related_functions_with_ast": len(related_ast),
                "related_functions_with_cfg": len(related_cfg),
                "related_functions_with_dfg": len(related_dfg),
                "related_functions_with_all_ast_cfg_dfg": len(related_all3),
            })

        per_contract_summary.append({
            "contract_key": ck,
            "total_functions_in_contract": len(contract_funcs),
            "seed_functions_in_contract": len(seed_funcs_in_contract),
            "has_fallback": has_fallback,
            "has_receive": has_receive,
            "fallback_receive_count": len(fallback_funcs) + len(receive_funcs),
            "state_vars_detected": len(state_vars),
            "internal_relation_hits_from_seeds": contract_internal_edges,
            "shared_state_relation_hits_from_seeds": contract_shared_edges,
        })

    avg_funcs = (
        sum(contract_function_counts) / len(contract_function_counts)
        if contract_function_counts else 0.0
    )
    relation_types_for_summary = [
        "INTERNAL_CALL_IN",
        "INTERNAL_CALL_OUT",
        "SHARED_STATE_WRITE_READ",
        "SHARED_STATE_WRITE_WRITE",
    ]
    relation_scores_for_summary = {
        rt: relation_score(args.task, rt)
        for rt in relation_types_for_summary
    }
    allowed_relation_types_for_summary = [
        rt for rt in relation_types_for_summary
        if relation_allowed(args.task, rt)
    ]
    allowed_relation_scores_for_summary = {
        rt: relation_score(args.task, rt)
        for rt in allowed_relation_types_for_summary
    }

    summary = {
        "seed_jsonl": str(args.seed_jsonl),
        "inventory_jsonl": str(args.inventory_jsonl),
        "source_root": str(args.source_root),
        "top_k": args.top_k,
        "task": args.task,
        "include_read_read": bool(args.include_read_read),
        "prefer_existing_graphs": bool(args.prefer_existing_graphs),
        "disable_fact_score": bool(args.disable_fact_score),
        "seed_functions": len(seed_rows),
        "unique_seed_contracts": len(seed_contracts),
        "missing_inventory_contracts": missing_inventory_contracts,
        "missing_source_contracts": missing_source_contracts,
        "contracts_with_fallback": contracts_with_fallback,
        "contracts_with_receive": contracts_with_receive,
        "contracts_with_fallback_or_receive": contracts_with_fallback_or_receive,
        "fallback_receive_functions_total": fallback_receive_functions_total,
        "seed_functions_in_contract_with_fallback_or_receive": (
            seed_functions_in_contract_with_fallback_or_receive
        ),
        "avg_functions_per_seed_contract": avg_funcs,
        "processed_seed_functions": processed_seed_functions,
        "skipped_seed_functions_missing_inventory": skipped_seed_functions_missing_inventory,
        "skipped_seed_functions_missing_source": skipped_seed_functions_missing_source,
        "seed_functions_isolated": count_isolated,
        "seed_functions_with_internal_call_relation": count_internal,
        "seed_functions_with_shared_state_relation": count_shared,
        "seed_functions_with_reentrancy_helper_state_write": (
            count_reentrancy_helper_state_write_seed
        ),
        "seed_functions_with_timestamp_cross_function_state_use": (
            count_timestamp_cross_function_state_use_seed
        ),
        "seed_functions_with_any_relation": processed_seed_functions - count_isolated,
        "seed_functions_with_related_functions_all_ast_cfg_dfg_available": count_related_all3_seed,
        "unique_related_functions_total": len(unique_related_keys),
        "unique_related_functions_with_ast": len(unique_related_ast),
        "unique_related_functions_with_cfg": len(unique_related_cfg),
        "unique_related_functions_with_dfg": len(unique_related_dfg),
        "unique_related_functions_with_all_ast_cfg_dfg": len(unique_related_all3),
        "ranked_related_edges": len(all_ranked_related),
        "topk_related_edges": len(all_topk_related),
        "expanded_topk_functions": len(expanded_function_keys),
        "shared_state_relation_mode": "read_write_split",
        "allowed_relation_types": allowed_relation_types_for_summary,
        "allowed_relation_scores": allowed_relation_scores_for_summary,
        "relation_scores": relation_scores_for_summary,
        "relation_filter_policy": (
            "clean setting: both reentrancy and timestamp keep only generic "
            "INTERNAL_CALL and SHARED_STATE relations; "
            "all: keep all relations for coverage/debug."
        ),
        "graph_availability_files": {
            "ast_graphs": str(args.ast_graphs) if args.ast_graphs else "",
            "cfg_graphs": str(args.cfg_graphs) if args.cfg_graphs else "",
            "dfg_graphs": str(args.dfg_graphs) if args.dfg_graphs else "",
        },
        "note": (
            "Relations are source-level heuristic checks. Internal calls are name-based. "
            "Shared-state is split into write-read, write-write, and optional weak read-read relations. "
            "Graph availability is checked against AST/CFG/DFG graph JSONL function keys."
        ),
    }
    summary.update({
        "top_k": args.top_k,
        "unique_expanded_functions_total": len(expanded_function_keys),
        "topk_related_edges_total": len(all_topk_related),
        "expanded_topk_functions_jsonl": str(out_dir / "expanded_topk_functions.jsonl"),
        "seed_topk_related_functions_jsonl": str(out_dir / "seed_topk_related_functions.jsonl"),
        "seed_topk_related_functions_debug_jsonl": str(out_dir / "seed_topk_related_functions_debug.jsonl"),
    })

    write_json(out_dir / "seed_relation_coverage_summary.json", summary)

    with (out_dir / "per_seed_relation_coverage.jsonl").open("w", encoding="utf-8") as f:
        for r in per_seed:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def clean_topk_row(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "seed_function_key": row.get("seed_function_key", ""),
            "related_function_key": row.get("related_function_key", ""),
            "contract_key": row.get("contract_key", ""),
            "relation_types": row.get("relation_types", []),
            "score": row.get("score", 0),
            "rank": row.get("rank", 0),
            "selected_topk": bool(row.get("selected_topk", False)),
        }

    with (out_dir / "seed_topk_related_functions.jsonl").open("w", encoding="utf-8") as f:
        for r in all_topk_related:
            f.write(json.dumps(clean_topk_row(r), ensure_ascii=False) + "\n")

    with (out_dir / "seed_topk_related_functions_debug.jsonl").open("w", encoding="utf-8") as f:
        for r in all_topk_related:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (out_dir / "expanded_topk_functions.jsonl").open("w", encoding="utf-8") as f:
        for fk in sorted(expanded_function_keys):
            if fk not in expanded_rows_by_key:
                continue
            row = dict(expanded_rows_by_key[fk])
            row["_debug_selected_for_topk_context"] = True
            row["function_key"] = fk
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with (out_dir / "per_contract_relation_summary.csv").open("w", encoding="utf-8", newline="") as f:
        if per_contract_summary:
            writer = csv.DictWriter(f, fieldnames=list(per_contract_summary[0].keys()))
            writer.writeheader()
            writer.writerows(per_contract_summary)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
