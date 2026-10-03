#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build function-level PURE DFG graphs directly from Slither read/write facts.

Purpose:
- Keep DFG role separate from CFG.
- DFG captures only data relations:
    variable -> statement read/use
    statement -> variable write/update
    statement -> statement def-use dependency
- No CFG edge output.
- No vulnerability-specific shortcut tags.
- No anchor-specific node/edge names.

Input:
- candidates JSONL used by AST/CFG builders
- cleaned Solidity source root

Output:
- function_pure_dfg_graphs.jsonl
- summary.json

Recommended use:
Use this script to replace the old CFG-derived DFG builder.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x

try:
    from slither.slither import Slither
except Exception as exc:
    raise SystemExit(
        "Slither Python API not found. Install/use an environment with slither-analyzer available."
    ) from exc


# -------------------------
# Basic IO helpers
# -------------------------

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


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


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


# -------------------------
# Solidity compiler version helpers
# -------------------------

SEMVER_RE = re.compile(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b")
PRAGMA_SOLIDITY_RE = re.compile(
    r"\bpragma\s+solidity\s+([^;]+);",
    re.IGNORECASE,
)


def strip_comments_for_pragma(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    src = re.sub(r"//.*", " ", src)
    return src


def parse_version_tuple(version: str) -> Tuple[int, int, int]:
    m = SEMVER_RE.search(version)
    if not m:
        return 0, 0, 0
    patch = m.group(3) if m.group(3) is not None else "0"
    return int(m.group(1)), int(m.group(2)), int(patch)


def version_tuple_to_str(version: Tuple[int, int, int]) -> str:
    return f"{version[0]}.{version[1]}.{version[2]}"


def extract_pragma_specs(source_text: str) -> List[str]:
    clean = strip_comments_for_pragma(source_text)
    return [m.group(1).strip() for m in PRAGMA_SOLIDITY_RE.finditer(clean)]


def list_installed_solc_binaries(artifacts_dir: Path) -> Dict[str, Path]:
    binaries: Dict[str, Path] = {}
    if not artifacts_dir.is_dir():
        return binaries

    for path in artifacts_dir.glob("solc-*/solc-*"):
        if not path.is_file():
            continue
        version = path.name.replace("solc-", "", 1)
        if SEMVER_RE.fullmatch(version):
            binaries[version] = path

    return dict(sorted(
        binaries.items(),
        key=lambda item: parse_version_tuple(item[0]),
    ))


def satisfies_comparator(
    version: Tuple[int, int, int],
    op: str,
    target: Tuple[int, int, int],
) -> bool:
    if op in {"", "="}:
        return version == target
    if op == ">=":
        return version >= target
    if op == ">":
        return version > target
    if op == "<=":
        return version <= target
    if op == "<":
        return version < target
    return False


def caret_upper_bound(version: Tuple[int, int, int]) -> Tuple[int, int, int]:
    major, minor, patch = version
    if major > 0:
        return major + 1, 0, 0
    if minor > 0:
        return 0, minor + 1, 0
    return 0, 0, patch + 1


def tilde_upper_bound(version: Tuple[int, int, int]) -> Tuple[int, int, int]:
    major, minor, _ = version
    return major, minor + 1, 0


def version_satisfies_alt(version: Tuple[int, int, int], alt: str) -> bool:
    found = False
    for m in re.finditer(r"(>=|<=|>|<|\^|~|=)?\s*(\d+\.\d+(?:\.\d+)?)", alt):
        found = True
        op = m.group(1) or ""
        target = parse_version_tuple(m.group(2))

        if op == "^":
            if not (version >= target and version < caret_upper_bound(target)):
                return False
            continue

        if op == "~":
            if not (version >= target and version < tilde_upper_bound(target)):
                return False
            continue

        if not satisfies_comparator(version, op, target):
            return False

    return found


def version_satisfies_spec(version: Tuple[int, int, int], spec: str) -> bool:
    alternatives = [x.strip() for x in spec.split("||") if x.strip()]
    if not alternatives:
        return True
    return any(version_satisfies_alt(version, alt) for alt in alternatives)


def has_upper_bound(specs: List[str]) -> bool:
    joined = " ".join(specs)
    return bool(re.search(r"(<|<=)\s*\d+\.\d+(?:\.\d+)?", joined))


def min_required_version(specs: List[str]) -> Tuple[int, int, int]:
    versions = []
    for spec in specs:
        for m in re.finditer(r"(>=|>|=|\^|~)?\s*(\d+\.\d+(?:\.\d+)?)", spec):
            versions.append(parse_version_tuple(m.group(2)))
    if not versions:
        return 0, 0, 0
    return min(versions)


def choose_solc_version(
    pragma_specs: List[str],
    installed_versions: List[str],
    default_version: str = "",
) -> Tuple[str, str]:
    if not installed_versions:
        return "", "no_installed_solc_versions"

    if not pragma_specs:
        if default_version:
            return default_version, "default_no_pragma"
        return "", "path_default_no_pragma"

    candidates = []
    for version in installed_versions:
        vt = parse_version_tuple(version)
        if all(version_satisfies_spec(vt, spec) for spec in pragma_specs):
            candidates.append(version)

    if not candidates:
        return "", "no_installed_solc_satisfies_pragma"

    min_v = min_required_version(pragma_specs)
    old_04_candidates = [
        v for v in candidates
        if parse_version_tuple(v)[0] == 0 and parse_version_tuple(v)[1] == 4
    ]

    if min_v < (0, 5, 0) and old_04_candidates and not has_upper_bound(pragma_specs):
        old_04_candidates.sort(key=parse_version_tuple)
        return old_04_candidates[-1], "pragma_match_prefer_latest_0_4_for_old_source"

    candidates.sort(key=parse_version_tuple)
    return candidates[-1], "pragma_match_highest_installed"


def resolve_solc_for_source(
    source_path: Path,
    multi_solc: bool,
    installed_solc_binaries: Dict[str, Path],
    default_version: str = "",
) -> Dict[str, Any]:
    if not multi_solc:
        return {
            "version": "",
            "binary": "",
            "pragma_specs": [],
            "reason": "multi_solc_disabled",
        }

    source_text = source_path.read_text(encoding="utf-8", errors="ignore")
    pragma_specs = extract_pragma_specs(source_text)
    version, reason = choose_solc_version(
        pragma_specs,
        list(installed_solc_binaries.keys()),
        default_version=default_version,
    )

    if not version:
        return {
            "version": "",
            "binary": "",
            "pragma_specs": pragma_specs,
            "reason": reason,
        }

    binary = installed_solc_binaries.get(version)
    if not binary or not binary.is_file():
        return {
            "version": version,
            "binary": "",
            "pragma_specs": pragma_specs,
            "reason": "selected_solc_binary_missing",
        }

    return {
        "version": version,
        "binary": str(binary),
        "pragma_specs": pragma_specs,
        "reason": reason,
    }


# -------------------------
# Function key compatibility
# -------------------------

SAFE_GRAPH_META_KEYS = [
    "source_path",
    "relative_path",
    "contract_name",
    "contract_kind",
    "function_name",
    "function_kind",
    "visibility",
    "state_mutability",
    "implemented",
    "is_constructor",
    "is_fallback",
    "is_receive",
    "param_count",
    "return_count",
    "modifier_count",
    "src",
    "byte_start",
    "byte_length",
    "line_start",
    "line_end",
]


def build_contract_key(row: Dict[str, Any]) -> str:
    if row.get("contract_key"):
        return safe_str(row["contract_key"])
    return "|".join([
        safe_str(row.get("relative_path", "")),
        safe_str(row.get("contract_name", "")),
    ])


def build_function_key(row: Dict[str, Any]) -> str:
    """
    Same key style as your AST/CFG builders:
    relative_path|contract_name|function_name|function_kind|line_start|line_end|param_count
    """
    if row.get("function_key"):
        return safe_str(row["function_key"])

    return "|".join([
        safe_str(row.get("relative_path", "")),
        safe_str(row.get("contract_name", "")),
        safe_str(row.get("function_name", "")),
        safe_str(row.get("function_kind", "")),
        safe_str(row.get("line_start", "")),
        safe_str(row.get("line_end", "")),
        safe_str(row.get("param_count", "")),
    ])


def add_identity_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep only safe identity/location metadata.
    Do not carry seed/anchor/label metadata into graph output.
    """
    out = {k: row.get(k, "") for k in SAFE_GRAPH_META_KEYS if k in row}

    out["contract_key"] = build_contract_key(row)
    out["function_key"] = build_function_key(row)
    out["function_src_key"] = "|".join([
        safe_str(row.get("relative_path", "")),
        safe_str(row.get("contract_name", "")),
        safe_str(row.get("function_name", "")),
        safe_str(row.get("src", "")),
    ])
    return out


# -------------------------
# Slither source mapping helpers
# -------------------------

def parse_src_field(src: str) -> Tuple[int, int, int]:
    try:
        s, l, i = src.split(":")
        return int(s), int(l), int(i)
    except Exception:
        return 0, 0, -1


def get_mapping_attr(mapping: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(mapping, attr, default)
    except Exception:
        return default


def get_mapping_file_path(mapping: Any) -> str:
    if mapping is None:
        return ""

    filename = get_mapping_attr(mapping, "filename", None)
    if filename is None:
        return ""

    for attr in ("absolute", "relative", "short"):
        value = get_mapping_attr(filename, attr, "")
        if value:
            return safe_str(value)

    return safe_str(filename)


def mapping_file_matches_source(file_path: str, source_path: Path) -> bool:
    if not file_path:
        return True

    candidate = Path(file_path)

    try:
        if candidate.is_absolute() and candidate.resolve() == source_path.resolve():
            return True
    except Exception:
        pass

    file_text = safe_str(file_path)
    return (
        candidate.name == source_path.name
        or file_text == str(source_path)
        or file_text.endswith("/" + source_path.name)
    )


def get_mapping_lines(mapping: Any) -> List[int]:
    lines = get_mapping_attr(mapping, "lines", []) or []
    result: List[int] = []

    def collect(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                collect(item)
            return
        try:
            result.append(int(value))
        except Exception:
            return

    collect(lines)
    return result


def get_mapping_line_span(mapping: Any) -> Tuple[int, int]:
    lines = get_mapping_lines(mapping)
    if not lines:
        return 0, 0
    return min(lines), max(lines)


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


def compute_line_range_from_mapping(mapping: Any, line_starts: List[int]) -> Tuple[int, int]:
    line_start, line_end = get_mapping_line_span(mapping)
    if line_start > 0 and line_end > 0:
        return line_start, line_end

    start = get_mapping_attr(mapping, "start", None)
    length = get_mapping_attr(mapping, "length", None)

    try:
        start = int(start)
        length = int(length)
    except Exception:
        return 0, 0

    if start < 0 or length <= 0:
        return 0, 0

    byte_end = start + max(0, length - 1)
    return (
        byte_offset_to_line(line_starts, start),
        byte_offset_to_line(line_starts, byte_end),
    )


def normalize_for_line_search(text: str) -> str:
    text = safe_str(text)
    text = re.sub(r"\b(require|assert|revert)\s*\([^)]*\)\s*\(", r"\1(", text)
    text = re.sub(r"\s+", "", text)
    return text


def find_expr_line_fallback(
    source_lines: List[str],
    expr: str,
    function_start: int,
    function_end: int,
) -> Tuple[int, int]:
    expr = safe_str(expr).strip()
    if not expr:
        return 0, 0

    expr_norm = normalize_for_line_search(expr)
    if not expr_norm:
        return 0, 0

    start = max(1, int(function_start or 1))
    end = min(len(source_lines), int(function_end or len(source_lines)))

    for i in range(start, end + 1):
        line_norm = normalize_for_line_search(source_lines[i - 1])
        if expr_norm in line_norm or line_norm in expr_norm:
            return i, i

    for i in range(start, end + 1):
        window = "".join(source_lines[i - 1:min(end, i + 2)])
        window_norm = normalize_for_line_search(window)
        if expr_norm in window_norm:
            return i, min(end, i + 2)

    return 0, 0


def merge_line_spans(*spans: Tuple[int, int]) -> Tuple[int, int]:
    starts = [s for s, e in spans if s > 0 and e > 0]
    ends = [e for s, e in spans if s > 0 and e > 0]
    if not starts or not ends:
        return 0, 0
    return min(starts), max(ends)


def infer_function_line_span_from_nodes(fn: Any) -> Tuple[int, int]:
    spans: List[Tuple[int, int]] = []
    for node in getattr(fn, "nodes", []) or []:
        span = get_mapping_line_span(getattr(node, "source_mapping", None))
        if span != (0, 0):
            spans.append(span)
    return merge_line_spans(*spans)


# -------------------------
# Slither function matching
# -------------------------

def normalize_slither_function_identity(fn: Any) -> Tuple[str, str, bool, bool, bool]:
    is_constructor = bool(getattr(fn, "is_constructor", False))
    is_fallback = bool(getattr(fn, "is_fallback", False))
    is_receive = bool(getattr(fn, "is_receive", False))
    name = safe_str(getattr(fn, "name", ""))

    if is_constructor:
        return "__constructor__", "constructor", True, False, False
    if is_receive:
        return "__receive__", "receive", False, False, True
    if is_fallback:
        return "__fallback__", "fallback", False, True, False

    return name if name else "__anonymous__", "function", False, False, False


def get_function_param_count(fn: Any) -> int:
    params = getattr(fn, "parameters", None)
    if params is None:
        params = getattr(fn, "parameters_src", None)
    if params is None:
        return 0
    try:
        return len(list(params))
    except Exception:
        return 0


def slither_function_entry(fn: Any, source_path: Path) -> Optional[Dict[str, Any]]:
    mapping = getattr(fn, "source_mapping", None)
    file_path = get_mapping_file_path(mapping)

    if file_path and not mapping_file_matches_source(file_path, source_path):
        return None

    function_name, function_kind, is_constructor, is_fallback, is_receive = \
        normalize_slither_function_identity(fn)

    line_start, line_end = get_mapping_line_span(mapping)
    node_line_start, node_line_end = infer_function_line_span_from_nodes(fn)
    effective_line_start, effective_line_end = merge_line_spans(
        (line_start, line_end),
        (node_line_start, node_line_end),
    )

    start = get_mapping_attr(mapping, "start", None)
    length = get_mapping_attr(mapping, "length", None)

    contract = getattr(fn, "contract_declarer", None) or getattr(fn, "contract", None)
    contract_name = safe_str(getattr(contract, "name", ""))

    return {
        "contract_name": contract_name,
        "function_name": function_name,
        "function_kind": function_kind,
        "is_constructor": is_constructor,
        "is_fallback": is_fallback,
        "is_receive": is_receive,
        "line_start": int(line_start or 0),
        "line_end": int(line_end or 0),
        "node_line_start": int(node_line_start or 0),
        "node_line_end": int(node_line_end or 0),
        "effective_line_start": int(effective_line_start or 0),
        "effective_line_end": int(effective_line_end or 0),
        "start": safe_int(start, None) if start is not None else None,
        "length": safe_int(length, None) if length is not None else None,
        "param_count": get_function_param_count(fn),
        "function_obj": fn,
    }


def build_slither_function_index(source_path: Path, solc_binary: str = "") -> List[Dict[str, Any]]:
    slither_kwargs: Dict[str, Any] = {}
    if solc_binary:
        slither_kwargs["solc"] = solc_binary

    sl = Slither(str(source_path), **slither_kwargs)
    entries: List[Dict[str, Any]] = []

    for contract in sl.contracts:
        funcs = getattr(contract, "functions_declared", None)
        if funcs is None:
            funcs = getattr(contract, "functions", [])

        for fn in funcs:
            entry = slither_function_entry(fn, source_path)
            if entry is not None:
                entries.append(entry)

    return entries


def function_names_compatible(
    target_function: str,
    item_function: str,
    target_contract: str,
) -> bool:
    if item_function == target_function:
        return True
    if target_function == "__constructor__" and item_function == target_contract:
        return True
    if target_function == target_contract and item_function == "__constructor__":
        return True
    if target_function == "__fallback__" and item_function in {"__anonymous__", ""}:
        return True
    if target_function in {"__anonymous__", ""} and item_function == "__fallback__":
        return True
    return False


def line_ranges_overlap(
    a_start: int,
    a_end: int,
    b_start: int,
    b_end: int,
    tolerance: int = 0,
) -> bool:
    if a_start <= 0 or a_end <= 0 or b_start <= 0 or b_end <= 0:
        return False
    a_start -= tolerance
    a_end += tolerance
    b_start -= tolerance
    b_end += tolerance
    return max(a_start, b_start) <= min(a_end, b_end)


def line_overlap_size(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    if not line_ranges_overlap(a_start, a_end, b_start, b_end):
        return 0
    return min(a_end, b_end) - max(a_start, b_start) + 1


def anchor_lines(candidate_row: Dict[str, Any]) -> List[int]:
    lines = (
        candidate_row.get("anchor_lines_in_file")
        or candidate_row.get("reentrancy_anchor_lines_in_file")
        or candidate_row.get("timestamp_anchor_lines_in_file")
        or []
    )
    out: List[int] = []
    for line in lines:
        try:
            out.append(int(line))
        except Exception:
            continue
    return out


def line_in_span(line: int, start: int, end: int, tolerance: int = 0) -> bool:
    if line <= 0 or start <= 0 or end <= 0:
        return False
    return (start - tolerance) <= line <= (end + tolerance)


def best_line_overlap_match(
    items: List[Dict[str, Any]],
    target_line_start: int,
    target_line_end: int,
    tolerance: int = 2,
) -> Optional[Dict[str, Any]]:
    scored: List[Tuple[int, int, Dict[str, Any]]] = []

    for item in items:
        item_start = int(item.get("effective_line_start", 0) or item.get("line_start", 0) or 0)
        item_end = int(item.get("effective_line_end", 0) or item.get("line_end", 0) or 0)

        if not line_ranges_overlap(
            target_line_start,
            target_line_end,
            item_start,
            item_end,
            tolerance=tolerance,
        ):
            continue

        overlap = line_overlap_size(target_line_start, target_line_end, item_start, item_end)
        distance = abs(target_line_start - item_start) + abs(target_line_end - item_end)
        scored.append((overlap, -distance, item))

    if not scored:
        return None

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]


def find_matching_slither_function(
    function_entries: List[Dict[str, Any]],
    candidate_row: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    target_contract = candidate_row.get("contract_name", "")
    target_function = candidate_row.get("function_name", "")
    target_line_start = int(candidate_row.get("line_start", 0) or 0)
    target_line_end = int(candidate_row.get("line_end", 0) or 0)
    target_src = candidate_row.get("src", "")
    target_start, target_length, _ = parse_src_field(target_src)

    target_param_count = None
    if candidate_row.get("param_count", None) is not None:
        try:
            target_param_count = int(candidate_row.get("param_count"))
        except Exception:
            target_param_count = None

    same_contract_function_all = [
        item for item in function_entries
        if item["contract_name"] == target_contract
        and function_names_compatible(
            target_function,
            item["function_name"],
            target_contract,
        )
    ]

    same_contract_function = same_contract_function_all
    param_filter_applied = False

    if target_param_count is not None:
        param_matched = [
            item for item in same_contract_function
            if int(item.get("param_count", -1)) == target_param_count
        ]
        if param_matched:
            same_contract_function = param_matched
            param_filter_applied = True

    # strongest: source start/length
    for item in same_contract_function:
        if item["start"] == target_start and item["length"] == target_length:
            return item, "contract_function_param_start_length"

    # exact source-mapping line span
    for item in same_contract_function:
        if item["line_start"] == target_line_start and item["line_end"] == target_line_end:
            return item, "contract_function_param_exact_line_span"

    # effective span from Slither nodes
    for item in same_contract_function:
        if (
            item["effective_line_start"] == target_line_start
            and item["effective_line_end"] == target_line_end
        ):
            return item, "contract_function_param_exact_effective_line_span"

    # anchor lines inside function span
    anchors = anchor_lines(candidate_row)
    anchor_matches = []
    for item in same_contract_function:
        item_start = int(item.get("effective_line_start", 0) or item.get("line_start", 0) or 0)
        item_end = int(item.get("effective_line_end", 0) or item.get("line_end", 0) or 0)
        if any(line_in_span(line, item_start, item_end, tolerance=2) for line in anchors):
            anchor_matches.append(item)

    if anchor_matches:
        matched = best_line_overlap_match(
            anchor_matches,
            target_line_start,
            target_line_end,
            tolerance=5,
        )
        if matched is not None:
            return matched, "contract_function_param_anchor_line_overlap"
        if len(anchor_matches) == 1:
            return anchor_matches[0], "contract_function_param_unique_anchor_line"

    # line overlap
    matched = best_line_overlap_match(
        same_contract_function,
        target_line_start,
        target_line_end,
        tolerance=3,
    )
    if matched is not None:
        return matched, "contract_function_param_line_overlap"

    if len(same_contract_function) == 1:
        if param_filter_applied:
            return same_contract_function[0], "contract_function_param_unique_signature"
        return same_contract_function[0], "contract_function_unique_name_only"

    return None, ""


# -------------------------
# Pure DFG extraction
# -------------------------

def safe_var_text(obj: Any) -> str:
    if obj is None:
        return ""

    name = getattr(obj, "name", None)
    if name is not None:
        return safe_var_text(name)

    if isinstance(obj, (list, tuple, set)):
        return "|".join(safe_var_text(x) for x in obj)

    if isinstance(obj, dict):
        try:
            return json.dumps(obj, sort_keys=True, default=str)
        except Exception:
            return safe_str(obj)

    return safe_str(obj)


def var_name(x: Any) -> str:
    return safe_var_text(x).strip()


def unique_sorted_strings(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()

    for item in items:
        text = safe_str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)

    return sorted(out)


def is_builtin_name(name: str) -> bool:
    name = safe_str(name).strip()
    if not name:
        return False

    return (
        name in {"now", "this"}
        or name.startswith("msg.")
        or name.startswith("block.")
        or name.startswith("tx.")
    )


def collect_function_param_names(fn: Any) -> Set[str]:
    names: Set[str] = set()
    for p in getattr(fn, "parameters", []) or []:
        name = var_name(p)
        if name:
            names.add(name)
    return names


def collect_contract_state_var_names(fn: Any) -> Set[str]:
    names: Set[str] = set()

    contract = getattr(fn, "contract_declarer", None) or getattr(fn, "contract", None)
    if contract is None:
        return names

    for sv in getattr(contract, "state_variables", []) or []:
        name = var_name(sv)
        if name:
            names.add(name)

    return names


def make_var_key(scope: str, name: Any) -> str:
    clean_name = safe_var_text(name).strip()
    if not clean_name:
        clean_name = "UNKNOWN_VAR"
    return f"{scope}::{clean_name}"


def split_var_key(key: str) -> Tuple[str, str]:
    if "::" not in key:
        return "LOCAL", key
    scope, name = key.split("::", 1)
    return scope, name


def is_state_var_key(key: str) -> bool:
    scope, _ = split_var_key(key)
    return scope == "STATE"


def normalize_var_scope(
    name: str,
    state_vars: Set[str],
    params: Set[str],
    force_state: bool = False,
) -> str:
    if force_state or name in state_vars:
        return "STATE"
    if name in params:
        return "PARAM"
    if is_builtin_name(name):
        return "BUILTIN"
    return "LOCAL"


def get_node_line_span(node: Any) -> Tuple[int, int]:
    mapping = getattr(node, "source_mapping", None)
    return get_mapping_line_span(mapping)


def get_statement_nodes(fn: Any) -> List[Any]:
    nodes = list(getattr(fn, "nodes", []) or [])
    nodes = sorted(nodes, key=lambda n: int(getattr(n, "node_id", -1)))
    return nodes


def extract_node_data_facts(
    node: Any,
    state_vars: Set[str],
    params: Set[str],
) -> Tuple[Set[str], Set[str]]:
    """
    Extract read/write variable keys from a Slither node.

    Important:
    - This does not inspect CFG predecessors/successors.
    - This does not do reaching-definition propagation.
    - This does not add vulnerability-specific labels.
    """

    reads_raw = unique_sorted_strings(
        var_name(x) for x in (getattr(node, "variables_read", []) or [])
    )
    writes_raw = unique_sorted_strings(
        var_name(x) for x in (getattr(node, "variables_written", []) or [])
    )

    state_reads_raw = unique_sorted_strings(
        var_name(x) for x in (getattr(node, "state_variables_read", []) or [])
    )
    state_writes_raw = unique_sorted_strings(
        var_name(x) for x in (getattr(node, "state_variables_written", []) or [])
    )

    read_keys: Set[str] = set()
    write_keys: Set[str] = set()

    # Explicit state-variable reads/writes
    for name in state_reads_raw:
        if name:
            read_keys.add(make_var_key("STATE", name))

    for name in state_writes_raw:
        if name:
            write_keys.add(make_var_key("STATE", name))

    # General variable reads/writes
    for name in reads_raw:
        if not name:
            continue
        if name in state_reads_raw:
            continue
        scope = normalize_var_scope(name, state_vars, params)
        read_keys.add(make_var_key(scope, name))

    for name in writes_raw:
        if not name:
            continue
        if name in state_writes_raw:
            continue
        scope = normalize_var_scope(name, state_vars, params)
        write_keys.add(make_var_key(scope, name))

    return read_keys, write_keys


def add_edge(
    edges: List[Dict[str, Any]],
    seen: Set[Tuple[int, int, str, str]],
    src: int,
    dst: int,
    edge_type: str,
    var_key: str,
    add_reverse: bool,
) -> None:
    key = (src, dst, edge_type, var_key)
    if key not in seen:
        edges.append({
            "src": src,
            "dst": dst,
            "edge_type": edge_type,
            "internal_var_key": var_key,
        })
        seen.add(key)

    if add_reverse:
        rev_type = edge_type + "_REV"
        rev_key = (dst, src, rev_type, var_key)
        if rev_key not in seen:
            edges.append({
                "src": dst,
                "dst": src,
                "edge_type": rev_type,
                "internal_var_key": var_key,
            })
            seen.add(rev_key)


def build_pure_dfg_graph(
    fn: Any,
    add_reverse_edges: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """
    Pure DFG:
    - statement nodes
    - variable/entity nodes
    - variable -> statement READ edges
    - statement -> variable WRITE edges
    - statement -> statement DEF_USE edges from source-order reaching definitions

    It does not output CFG edges or expression/call/timestamp shortcut features.
    """

    slither_nodes = get_statement_nodes(fn)
    state_vars = collect_contract_state_var_names(fn)
    params = collect_function_param_names(fn)

    stmt_nodes: List[Dict[str, Any]] = []
    read_by_stmt: Dict[int, Set[str]] = {}
    write_by_stmt: Dict[int, Set[str]] = {}
    all_var_keys: Set[str] = set()

    for local_id, node in enumerate(slither_nodes):
        line_start, line_end = get_node_line_span(node)
        slither_node_id = int(getattr(node, "node_id", -1))

        stmt_nodes.append({
            "id": local_id,
            "node_kind": "STMT",
            "source_cfg_node_id": local_id,
            "slither_node_id": slither_node_id,
            "stmt_key": f"CFG:{int(line_start or 0)}:{int(line_end or 0)}:{local_id}",
            "source_slither_node_id": slither_node_id,
            "line_start": int(line_start or 0),
            "line_end": int(line_end or 0),
        })

        read_keys, write_keys = extract_node_data_facts(
            node=node,
            state_vars=state_vars,
            params=params,
        )

        read_by_stmt[local_id] = read_keys
        write_by_stmt[local_id] = write_keys

        all_var_keys.update(read_keys)
        all_var_keys.update(write_keys)

    def_use_pairs: List[Tuple[int, int, str, str]] = []
    last_writer_by_var: Dict[str, int] = {}

    for stmt_id in range(len(stmt_nodes)):
        for rkey in sorted(read_by_stmt.get(stmt_id, set())):
            writer_stmt_id = last_writer_by_var.get(rkey)
            if writer_stmt_id is None or writer_stmt_id == stmt_id:
                continue

            edge_type = "DFG_STATE_DEF_USE" if is_state_var_key(rkey) else "DFG_DEF_USE"
            def_use_pairs.append((writer_stmt_id, stmt_id, edge_type, rkey))

        for wkey in sorted(write_by_stmt.get(stmt_id, set())):
            last_writer_by_var[wkey] = stmt_id

    active_stmt_ids: Set[int] = set()
    for stmt_id in range(len(stmt_nodes)):
        if read_by_stmt.get(stmt_id) or write_by_stmt.get(stmt_id):
            active_stmt_ids.add(stmt_id)

    for src_stmt_id, dst_stmt_id, _, _ in def_use_pairs:
        active_stmt_ids.add(src_stmt_id)
        active_stmt_ids.add(dst_stmt_id)

    kept_stmt_nodes: List[Dict[str, Any]] = []
    old_stmt_to_new: Dict[int, int] = {}

    for old_stmt_id in sorted(active_stmt_ids):
        if old_stmt_id < 0 or old_stmt_id >= len(stmt_nodes):
            continue

        new_id = len(kept_stmt_nodes)
        old_stmt_to_new[old_stmt_id] = new_id

        node = dict(stmt_nodes[old_stmt_id])
        node["id"] = new_id
        kept_stmt_nodes.append(node)

    active_var_keys: Set[str] = set()
    for old_stmt_id in active_stmt_ids:
        active_var_keys.update(read_by_stmt.get(old_stmt_id, set()))
        active_var_keys.update(write_by_stmt.get(old_stmt_id, set()))

    nodes: List[Dict[str, Any]] = list(kept_stmt_nodes)

    var_id_map: Dict[str, int] = {}
    next_id = len(nodes)

    for var_key in sorted(active_var_keys):
        scope, name = split_var_key(var_key)
        var_id_map[var_key] = next_id
        nodes.append({
            "id": next_id,
            "node_kind": "VAR",
            "var_scope": scope,
            "internal_var_key": var_key,
            "internal_var_name": safe_str(name),
        })
        next_id += 1

    edges: List[Dict[str, Any]] = []
    seen_edges: Set[Tuple[int, int, str, str]] = set()

    read_edges = 0
    write_edges = 0
    def_use_edges = 0
    state_def_use_edges = 0

    for old_stmt_id in sorted(active_stmt_ids):
        if old_stmt_id not in old_stmt_to_new:
            continue

        stmt_id = old_stmt_to_new[old_stmt_id]

        for rkey in sorted(read_by_stmt.get(old_stmt_id, set())):
            if rkey not in var_id_map:
                continue

            var_node_id = var_id_map[rkey]
            add_edge(
                edges=edges,
                seen=seen_edges,
                src=var_node_id,
                dst=stmt_id,
                edge_type="DFG_READ",
                var_key=rkey,
                add_reverse=add_reverse_edges,
            )
            read_edges += 1

        for wkey in sorted(write_by_stmt.get(old_stmt_id, set())):
            if wkey not in var_id_map:
                continue

            var_node_id = var_id_map[wkey]
            add_edge(
                edges=edges,
                seen=seen_edges,
                src=stmt_id,
                dst=var_node_id,
                edge_type="DFG_WRITE",
                var_key=wkey,
                add_reverse=add_reverse_edges,
            )
            write_edges += 1

    for src_old_stmt_id, dst_old_stmt_id, edge_type, var_key in def_use_pairs:
        if src_old_stmt_id not in old_stmt_to_new or dst_old_stmt_id not in old_stmt_to_new:
            continue

        before_count = len(edges)
        add_edge(
            edges=edges,
            seen=seen_edges,
            src=old_stmt_to_new[src_old_stmt_id],
            dst=old_stmt_to_new[dst_old_stmt_id],
            edge_type=edge_type,
            var_key=var_key,
            add_reverse=add_reverse_edges,
        )

        if len(edges) > before_count:
            if edge_type == "DFG_STATE_DEF_USE":
                state_def_use_edges += 1
            else:
                def_use_edges += 1

    stats = {
        "stmt_node_count": len(kept_stmt_nodes),
        "raw_stmt_node_count": len(stmt_nodes),
        "pruned_stmt_node_count": len(stmt_nodes) - len(kept_stmt_nodes),
        "var_node_count": len(var_id_map),
        "dfg_node_count": len(nodes),
        "dfg_edge_count": len(edges),
        "dfg_read_edges": read_edges,
        "dfg_write_edges": write_edges,
        "dfg_def_use_edges": def_use_edges,
        "dfg_state_def_use_edges": state_def_use_edges,
        "unique_var_count": len(var_id_map),
        "state_var_count": sum(1 for k in var_id_map if k.startswith("STATE::")),
        "param_var_count": sum(1 for k in var_id_map if k.startswith("PARAM::")),
        "builtin_var_count": sum(1 for k in var_id_map if k.startswith("BUILTIN::")),
        "local_var_count": sum(1 for k in var_id_map if k.startswith("LOCAL::")),
    }

    return nodes, edges, stats


# -------------------------
# Main
# -------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build pure function-level DFG graphs directly from Slither read/write facts."
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        required=True,
        help="Candidates JSONL used by AST/CFG builders.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Root folder containing cleaned Solidity files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for pure DFG graphs.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Optional limit for quick testing. 0 = all.",
    )
    parser.add_argument(
        "--no-reverse-edges",
        action="store_true",
        help="Disable reverse DFG edges.",
    )
    parser.add_argument(
        "--multi-solc",
        action="store_true",
        help="Resolve a matching solc binary per source file using pragma solidity.",
    )
    parser.add_argument(
        "--solc-artifacts-dir",
        type=Path,
        default=Path.home() / ".solc-select" / "artifacts",
        help="Directory containing solc-select artifacts, e.g. ~/.solc-select/artifacts.",
    )
    parser.add_argument(
        "--default-solc-version",
        default="",
        help="Optional solc version to use when a file has no pragma and --multi-solc is enabled.",
    )
    args = parser.parse_args()

    candidates_path = args.candidates.resolve()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    solc_artifacts_dir = args.solc_artifacts_dir.expanduser().resolve()
    add_reverse_edges = not args.no_reverse_edges

    if not candidates_path.is_file():
        raise SystemExit(f"Candidates file not found: {candidates_path}")
    if not source_root.is_dir():
        raise SystemExit(f"Source root not found: {source_root}")

    output_dir.mkdir(parents=True, exist_ok=True)

    installed_solc_binaries = list_installed_solc_binaries(solc_artifacts_dir) if args.multi_solc else {}
    installed_solc_versions = sorted(installed_solc_binaries.keys(), key=parse_version_tuple)

    if args.multi_solc and not installed_solc_binaries:
        raise SystemExit(f"--multi-solc enabled but no solc binaries found in: {solc_artifacts_dir}")

    candidate_rows = list(load_jsonl(candidates_path))
    if args.max_candidates > 0:
        candidate_rows = candidate_rows[:args.max_candidates]

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        grouped[row.get("relative_path", "")].append(row)

    out_graphs_path = output_dir / "function_pure_dfg_graphs.jsonl"
    summary_path = output_dir / "summary.json"

    total_candidate_rows = len(candidate_rows)
    graphs_built = 0
    missing_source_files = 0
    unmatched_functions = 0
    slither_failed_files = 0
    slither_failed_candidates = 0
    total_nodes = 0
    total_edges = 0
    total_stmt_nodes = 0
    total_raw_stmt_nodes = 0
    total_pruned_stmt_nodes = 0
    total_var_nodes = 0
    total_read_edges = 0
    total_write_edges = 0
    total_def_use_edges = 0
    total_state_def_use_edges = 0
    match_reason_counts: Dict[str, int] = defaultdict(int)
    solc_version_file_counts: Dict[str, int] = defaultdict(int)
    solc_version_candidate_counts: Dict[str, int] = defaultdict(int)
    solc_failed_file_counts: Dict[str, int] = defaultdict(int)
    solc_failed_candidate_counts: Dict[str, int] = defaultdict(int)
    solc_resolution_reason_counts: Dict[str, int] = defaultdict(int)
    failed_files: List[Dict[str, Any]] = []

    with out_graphs_path.open("w", encoding="utf-8") as out_f:
        for rel_path, rows in tqdm(sorted(grouped.items()), desc="Building pure DFG graphs", unit="file"):
            if not rel_path:
                unmatched_functions += len(rows)
                continue

            source_path = source_root / rel_path
            if not source_path.is_file():
                missing_source_files += len(rows)
                continue

            solc_info = resolve_solc_for_source(
                source_path=source_path,
                multi_solc=bool(args.multi_solc),
                installed_solc_binaries=installed_solc_binaries,
                default_version=safe_str(args.default_solc_version),
            )
            solc_version = safe_str(solc_info.get("version", "")) or "PATH_DEFAULT"
            solc_binary = safe_str(solc_info.get("binary", ""))
            solc_reason = safe_str(solc_info.get("reason", ""))

            solc_version_file_counts[solc_version] += 1
            solc_version_candidate_counts[solc_version] += len(rows)
            solc_resolution_reason_counts[solc_reason] += 1

            try:
                function_entries = build_slither_function_index(source_path, solc_binary=solc_binary)
            except Exception as exc:
                slither_failed_files += 1
                slither_failed_candidates += len(rows)
                solc_failed_file_counts[solc_version] += 1
                solc_failed_candidate_counts[solc_version] += len(rows)
                failed_files.append({
                    "relative_path": rel_path,
                    "candidate_count": len(rows),
                    "solc_version": solc_version,
                    "solc_binary": solc_binary,
                    "solc_resolution_reason": solc_reason,
                    "solc_pragma_specs": solc_info.get("pragma_specs", []),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            for row in rows:
                matched_entry, match_reason = find_matching_slither_function(function_entries, row)
                if matched_entry is None:
                    unmatched_functions += 1
                    continue

                matched_fn = matched_entry["function_obj"]

                nodes, edges, stats = build_pure_dfg_graph(
                    matched_fn,
                    add_reverse_edges=add_reverse_edges,
                )

                out = add_identity_keys(row)
                out["graph_type"] = "function_level_dfg"
                out["graph_mode"] = "pure_slither_read_write_def_use"
                out["dfg_source"] = "slither_direct_source_order_reaching_defs_no_cfg_edges"
                out["match_reason"] = match_reason
                out["solc_version"] = solc_version
                out["solc_binary"] = solc_binary
                out["solc_resolution_reason"] = solc_reason
                out["slither_function_name"] = matched_entry.get("function_name", "")
                out["slither_line_start"] = matched_entry.get("line_start", 0)
                out["slither_line_end"] = matched_entry.get("line_end", 0)
                out["slither_effective_line_start"] = matched_entry.get("effective_line_start", 0)
                out["slither_effective_line_end"] = matched_entry.get("effective_line_end", 0)
                out["slither_param_count"] = matched_entry.get("param_count", 0)

                out["node_count"] = len(nodes)
                out["edge_count"] = len(edges)
                out["stmt_node_count"] = stats["stmt_node_count"]
                out["raw_stmt_node_count"] = stats["raw_stmt_node_count"]
                out["pruned_stmt_node_count"] = stats["pruned_stmt_node_count"]
                out["var_node_count"] = stats["var_node_count"]
                out["dfg_read_edges"] = stats["dfg_read_edges"]
                out["dfg_write_edges"] = stats["dfg_write_edges"]
                out["dfg_def_use_edges"] = stats["dfg_def_use_edges"]
                out["dfg_state_def_use_edges"] = stats["dfg_state_def_use_edges"]
                out["unique_var_count"] = stats["unique_var_count"]
                out["state_var_count"] = stats["state_var_count"]
                out["param_var_count"] = stats["param_var_count"]
                out["builtin_var_count"] = stats["builtin_var_count"]
                out["local_var_count"] = stats["local_var_count"]

                out["nodes"] = nodes
                out["edges"] = edges

                out_f.write(json.dumps(out, ensure_ascii=False) + "\n")

                graphs_built += 1
                match_reason_counts[match_reason] += 1
                total_nodes += len(nodes)
                total_edges += len(edges)
                total_stmt_nodes += stats["stmt_node_count"]
                total_raw_stmt_nodes += stats["raw_stmt_node_count"]
                total_pruned_stmt_nodes += stats["pruned_stmt_node_count"]
                total_var_nodes += stats["var_node_count"]
                total_read_edges += stats["dfg_read_edges"]
                total_write_edges += stats["dfg_write_edges"]
                total_def_use_edges += stats["dfg_def_use_edges"]
                total_state_def_use_edges += stats["dfg_state_def_use_edges"]

    summary = {
        "stage": "step7_function_level_pure_dfg_graph_build",
        "candidates": str(candidates_path),
        "source_root": str(source_root),
        "total_candidate_rows": total_candidate_rows,
        "graphs_built": graphs_built,
        "missing_source_files": missing_source_files,
        "unmatched_functions": unmatched_functions,
        "slither_failed_files": slither_failed_files,
        "slither_failed_candidates": slither_failed_candidates,
        "total_graph_nodes": total_nodes,
        "total_graph_edges": total_edges,
        "total_stmt_nodes": total_stmt_nodes,
        "total_raw_stmt_nodes": total_raw_stmt_nodes,
        "total_pruned_stmt_nodes": total_pruned_stmt_nodes,
        "total_var_nodes": total_var_nodes,
        "total_read_edges": total_read_edges,
        "total_write_edges": total_write_edges,
        "total_def_use_edges": total_def_use_edges,
        "total_state_def_use_edges": total_state_def_use_edges,
        "avg_nodes_per_graph": (total_nodes / graphs_built) if graphs_built else 0.0,
        "avg_edges_per_graph": (total_edges / graphs_built) if graphs_built else 0.0,
        "avg_stmt_nodes_per_graph": (total_stmt_nodes / graphs_built) if graphs_built else 0.0,
        "avg_raw_stmt_nodes_per_graph": (total_raw_stmt_nodes / graphs_built) if graphs_built else 0.0,
        "avg_pruned_stmt_nodes_per_graph": (total_pruned_stmt_nodes / graphs_built) if graphs_built else 0.0,
        "avg_var_nodes_per_graph": (total_var_nodes / graphs_built) if graphs_built else 0.0,
        "match_reason_counts": dict(sorted(match_reason_counts.items())),
        "reverse_edges_enabled": add_reverse_edges,
        "multi_solc_enabled": bool(args.multi_solc),
        "solc_artifacts_dir": str(solc_artifacts_dir),
        "default_solc_version": safe_str(args.default_solc_version),
        "installed_solc_versions": installed_solc_versions,
        "solc_version_file_counts": dict(sorted(solc_version_file_counts.items())),
        "solc_version_candidate_counts": dict(sorted(solc_version_candidate_counts.items())),
        "solc_failed_file_counts": dict(sorted(solc_failed_file_counts.items())),
        "solc_failed_candidate_counts": dict(sorted(solc_failed_candidate_counts.items())),
        "solc_resolution_reason_counts": dict(sorted(solc_resolution_reason_counts.items())),
        "graphs_jsonl": str(out_graphs_path),
        "failed_files_sample": failed_files[:20],
        "note": (
            "This stage builds PURE DFG graphs directly from Slither read/write facts. "
            "It does not read CFG graphs, does not use CFG_NEXT/CFG_TRUE/CFG_FALSE, "
            "and does not output expression/call/timestamp shortcut features. "
            "Edges are generic DFG_READ, DFG_WRITE, DFG_DEF_USE, and DFG_STATE_DEF_USE only."
        ),
    }

    save_json(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
