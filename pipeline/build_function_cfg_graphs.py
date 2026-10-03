#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import bisect
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


REENTRANCY_ANCHOR = "external_value_call"
TIMESTAMP_ANCHOR = "block.timestamp"

RE_REENTRANCY_EXTERNAL_VALUE_CALL = re.compile(
    r"("
    r"\.\s*call\s*\.\s*value\s*\("
    r"|"
    r"\.\s*call\s*\{\s*value\s*:"
    r")",
    re.IGNORECASE | re.DOTALL,
)

RE_TIMESTAMP_SOURCE = re.compile(
    r"\bblock\s*\.\s*timestamp\b",
    re.IGNORECASE,
)

SEMVER_RE = re.compile(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b")
PRAGMA_SOLIDITY_RE = re.compile(
    r"\bpragma\s+solidity\s+([^;]+);",
    re.IGNORECASE,
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.read_text(encoding="latin-1", errors="replace")


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


def safe_str(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        return str(obj)
    except Exception:
        return ""


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


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


def min_required_version(pragma_specs: List[str]) -> Tuple[int, int, int]:
    versions: List[Tuple[int, int, int]] = []
    for spec in pragma_specs:
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

    candidates.sort(key=parse_version_tuple)

    # Old Solidity datasets often use broad pragmas like >=0.4.10.
    # Highest installed 0.8.x can break old syntax, so prefer latest valid 0.4.x.
    min_v = min_required_version(pragma_specs)
    old_04_candidates = [
        v for v in candidates
        if parse_version_tuple(v)[0] == 0 and parse_version_tuple(v)[1] == 4
    ]

    if min_v < (0, 5, 0) and old_04_candidates:
        old_04_candidates.sort(key=parse_version_tuple)
        return old_04_candidates[-1], "pragma_match_prefer_latest_0_4_for_old_source"

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


def parse_src_field(src: str) -> Tuple[int, int, int]:
    try:
        s, l, i = src.split(":")
        return int(s), int(l), int(i)
    except Exception:
        return 0, 0, -1


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


def merge_line_spans(*spans: Tuple[int, int]) -> Tuple[int, int]:
    starts = [start for start, end in spans if start > 0 and end > 0]
    ends = [end for start, end in spans if start > 0 and end > 0]
    if not starts or not ends:
        return 0, 0
    return min(starts), max(ends)


def unique_sorted_strings(items: List[Any]) -> List[str]:
    out = []
    seen = set()
    for item in items:
        text = safe_str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sorted(out)


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


def infer_function_line_span_from_nodes(fn: Any) -> Tuple[int, int]:
    spans: List[Tuple[int, int]] = []
    for node in getattr(fn, "nodes", []) or []:
        mapping = getattr(node, "source_mapping", None)
        span = get_mapping_line_span(mapping)
        if span != (0, 0):
            spans.append(span)
    return merge_line_spans(*spans)


def flatten_call_list(items: Any) -> List[str]:
    result: List[str] = []
    if not items:
        return result

    try:
        iterable = list(items)
    except Exception:
        return result

    for item in iterable:
        if isinstance(item, tuple):
            parts = [safe_str(x).strip() for x in item if safe_str(x).strip()]
            text = " | ".join(parts)
            if text:
                result.append(text)
        else:
            text = safe_str(item).strip()
            if text:
                result.append(text)
    return unique_sorted_strings(result)


def build_function_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("relative_path", ""),
        row.get("contract_name", ""),
        row.get("function_name", ""),
        row.get("line_start", 0),
        row.get("line_end", 0),
    )


def slither_function_entry(fn: Any, source_path: Path) -> Optional[Dict[str, Any]]:
    mapping = getattr(fn, "source_mapping", None)
    file_path = get_mapping_file_path(mapping)

    if file_path and not mapping_file_matches_source(file_path, source_path):
        return None

    function_name, function_kind, is_constructor, is_fallback, is_receive = \
        normalize_slither_function_identity(fn)

    line_start, line_end = get_mapping_line_span(mapping)
    start = get_mapping_attr(mapping, "start", None)
    length = get_mapping_attr(mapping, "length", None)
    node_line_start, node_line_end = infer_function_line_span_from_nodes(fn)
    effective_line_start, effective_line_end = merge_line_spans(
        (line_start, line_end),
        (node_line_start, node_line_end),
    )

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
    target_param_count_raw = candidate_row.get("param_count", None)
    target_param_count = None

    if target_param_count_raw is not None:
        try:
            target_param_count = int(target_param_count_raw)
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

    # Strongest match: contract + function + start + length
    for item in same_contract_function:
        if (
            item["start"] == target_start
            and item["length"] == target_length
        ):
            return item, "contract_function_param_start_length"

    # Fallback: contract + function + exact source-mapping line span
    for item in same_contract_function:
        if (
            item["line_start"] == target_line_start
            and item["line_end"] == target_line_end
        ):
            return item, "contract_function_param_exact_line_span"

    # Stronger relaxed match: exact span after merging Slither function and node spans.
    for item in same_contract_function:
        if (
            item["effective_line_start"] == target_line_start
            and item["effective_line_end"] == target_line_end
        ):
            return item, "contract_function_param_exact_effective_line_span"

    # Relaxed: candidate anchor line falls inside Slither's function span.
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

    # Relaxed: source line spans overlap, allowing small mapping differences.
    matched = best_line_overlap_match(
        same_contract_function,
        target_line_start,
        target_line_end,
        tolerance=3,
    )
    if matched is not None:
        return matched, "contract_function_param_line_overlap"

    # Last resort for unique function after contract/function filtering.
    if len(same_contract_function) == 1:
        if param_filter_applied:
            return same_contract_function[0], "contract_function_param_unique_signature"
        return same_contract_function[0], "contract_function_unique_name_only"

    return None, ""


def node_type_name(node: Any) -> str:
    raw = safe_str(getattr(node, "type", ""))
    if "." in raw:
        raw = raw.split(".")[-1]
    return raw or "UNKNOWN"


def normalize_cfg_node_type(raw_type: str) -> str:
    raw_key = safe_str(raw_type).upper().replace(" ", "_")
    mapping = {
        "ENTRYPOINT": "ENTRY",
        "ENTRY_POINT": "ENTRY",
        "IF": "CONDITION",
        "IFLOOP": "CONDITION",
        "IF_LOOP": "CONDITION",
        "EXPRESSION": "EXPRESSION",
        "VARIABLE": "ASSIGNMENT",
        "NEW_VARIABLE": "ASSIGNMENT",
        "RETURN": "RETURN",
        "THROW": "REVERT",
        "BREAK": "BREAK",
        "CONTINUE": "CONTINUE",
        "ENDIF": "MERGE",
        "END_IF": "MERGE",
        "STARTLOOP": "LOOP_BEGIN",
        "START_LOOP": "LOOP_BEGIN",
        "BEGIN_LOOP": "LOOP_BEGIN",
        "ENDLOOP": "LOOP_END",
        "END_LOOP": "LOOP_END",
        "ASSEMBLY": "ASSEMBLY",
        "INLINE_ASM": "ASSEMBLY",
        "PLACEHOLDER": "PLACEHOLDER",
        "TRY": "TRY",
        "CATCH": "CATCH",
    }
    return mapping.get(raw_key, raw_key)


def source_span_from_node(node: Any, line_starts: List[int]) -> Tuple[int, int]:
    mapping = getattr(node, "source_mapping", None)
    return compute_line_range_from_mapping(mapping, line_starts)


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
        if not line_norm:
            continue

        if expr_norm in line_norm:
            return i, i

        if len(line_norm) >= 12 and line_norm in expr_norm:
            return i, i

    for i in range(start, end + 1):
        window = "".join(source_lines[i - 1:min(end, i + 2)])
        window_norm = normalize_for_line_search(window)
        if not window_norm:
            continue

        if expr_norm in window_norm:
            return i, min(end, i + 2)

        if len(window_norm) >= 12 and window_norm in expr_norm:
            return i, min(end, i + 2)

    return 0, 0


def extract_cfg_node_analysis(
    node: Any,
    line_starts: List[int],
    source_lines: List[str],
    function_line_start: int,
    function_line_end: int,
) -> Dict[str, Any]:
    raw_type = node_type_name(node)
    normalized_type = normalize_cfg_node_type(raw_type)

    expr = safe_str(getattr(node, "expression", "")).strip()
    irs = getattr(node, "irs", []) or []
    ir_texts = [safe_str(ir).strip() for ir in irs if safe_str(ir).strip()]
    combined_text = "\n".join(([expr] if expr else []) + ir_texts)

    line_start, line_end = source_span_from_node(node, line_starts)
    line_recovery_method = "source_mapping"

    if line_start <= 0 or line_end <= 0:
        fb_start, fb_end = find_expr_line_fallback(
            source_lines=source_lines,
            expr=expr,
            function_start=function_line_start,
            function_end=function_line_end,
        )
        if fb_start > 0 and fb_end > 0:
            line_start, line_end = fb_start, fb_end
            line_recovery_method = "expression_text_fallback"
        else:
            line_recovery_method = "unresolved"

    reads = unique_sorted_strings(
        [getattr(x, "name", None) or safe_str(x) for x in (getattr(node, "variables_read", []) or [])]
    )
    writes = unique_sorted_strings(
        [getattr(x, "name", None) or safe_str(x) for x in (getattr(node, "variables_written", []) or [])]
    )
    state_reads = unique_sorted_strings(
        [getattr(x, "name", None) or safe_str(x) for x in (getattr(node, "state_variables_read", []) or [])]
    )
    state_writes = unique_sorted_strings(
        [getattr(x, "name", None) or safe_str(x) for x in (getattr(node, "state_variables_written", []) or [])]
    )

    internal_calls = flatten_call_list(getattr(node, "internal_calls", []) or [])
    high_level_calls = flatten_call_list(getattr(node, "high_level_calls", []) or [])
    low_level_calls = flatten_call_list(getattr(node, "low_level_calls", []) or [])
    solidity_calls = flatten_call_list(getattr(node, "solidity_calls", []) or [])

    sons = list(getattr(node, "sons", []) or [])
    fathers = list(getattr(node, "fathers", []) or [])

    return {
        "slither_node_id": int(getattr(node, "node_id", -1)),
        "raw_node_type": raw_type,
        "normalized_type": normalized_type,
        "expression_text": expr,
        "ir_count": len(ir_texts),
        "line_start": int(line_start or 0),
        "line_end": int(line_end or 0),
        "line_recovery_method": line_recovery_method,
        "contains_call_value": bool(RE_REENTRANCY_EXTERNAL_VALUE_CALL.search(combined_text)),
        "contains_block_timestamp": bool(RE_TIMESTAMP_SOURCE.search(combined_text)),
        "read_count": len(reads),
        "write_count": len(writes),
        "state_read_count": len(state_reads),
        "state_write_count": len(state_writes),
        "reads": reads,
        "writes": writes,
        "state_reads": state_reads,
        "state_writes": state_writes,
        "internal_calls": internal_calls,
        "high_level_calls": high_level_calls,
        "low_level_calls": low_level_calls,
        "solidity_calls": solidity_calls,
        "sons_count": len(sons),
        "fathers_count": len(fathers),
        "is_conditional": normalized_type == "CONDITION" or len(sons) > 1,
    }


def make_public_cfg_node(
    local_id: int,
    father_ids: List[int],
    is_entry: bool,
    node_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    line_start = int(node_analysis.get("line_start", 0) or 0)
    line_end = int(node_analysis.get("line_end", 0) or 0)

    return {
        "id": local_id,
        "source_cfg_node_id": local_id,
        "slither_node_id": int(node_analysis.get("slither_node_id", -1)),
        "stmt_key": f"CFG:{line_start}:{line_end}:{local_id}",
        "parent_ids": sorted(set(father_ids)),
        "is_entry": bool(is_entry),
        "normalized_type": safe_str(node_analysis.get("normalized_type", "")) or "UNKNOWN",
        "is_conditional": bool(node_analysis.get("is_conditional", False)),
        "line_start": line_start,
        "line_end": line_end,
    }


def cfg_edge_type(src_node: Any, dst_node: Any) -> str:
    son_true = getattr(src_node, "son_true", None)
    son_false = getattr(src_node, "son_false", None)

    if son_true is not None and dst_node is son_true:
        return "CFG_TRUE"
    if son_false is not None and dst_node is son_false:
        return "CFG_FALSE"
    return "CFG_NEXT"


def reverse_edge_type(edge_type: str) -> str:
    if edge_type == "CFG_TRUE":
        return "CFG_TRUE_REV"
    if edge_type == "CFG_FALSE":
        return "CFG_FALSE_REV"
    return "CFG_PREV"


def build_cfg_graph(
    fn: Any,
    line_starts: List[int],
    source_lines: List[str],
    function_line_start: int,
    function_line_end: int,
    add_reverse_edges: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], int]:
    slither_nodes = list(getattr(fn, "nodes", []) or [])
    slither_nodes = sorted(slither_nodes, key=lambda n: int(getattr(n, "node_id", -1)))

    nodes: List[Dict[str, Any]] = []
    analysis_nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    local_id_map: Dict[int, int] = {}
    for local_id, node in enumerate(slither_nodes):
        sl_id = int(getattr(node, "node_id", -1))
        local_id_map[sl_id] = local_id

    entry_node = getattr(fn, "entry_point", None)
    entry_local_id = 0
    if entry_node is not None:
        entry_local_id = local_id_map.get(int(getattr(entry_node, "node_id", -1)), 0)

    for local_id, node in enumerate(slither_nodes):
        sl_id = int(getattr(node, "node_id", -1))
        fathers = list(getattr(node, "fathers", []) or [])
        father_ids = []
        for father in fathers:
            father_sl_id = int(getattr(father, "node_id", -1))
            if father_sl_id in local_id_map:
                father_ids.append(local_id_map[father_sl_id])

        node_analysis = {
            "id": local_id,
            "parent_ids": sorted(set(father_ids)),
            "is_entry": local_id == entry_local_id,
        }
        node_analysis.update(
            extract_cfg_node_analysis(
                node,
                line_starts=line_starts,
                source_lines=source_lines,
                function_line_start=function_line_start,
                function_line_end=function_line_end,
            )
        )
        analysis_nodes.append(node_analysis)
        nodes.append(
            make_public_cfg_node(
                local_id=local_id,
                father_ids=father_ids,
                is_entry=local_id == entry_local_id,
                node_analysis=node_analysis,
            )
        )

    for node in slither_nodes:
        src_local_id = local_id_map[int(getattr(node, "node_id", -1))]
        sons = list(getattr(node, "sons", []) or [])

        for son in sons:
            son_sl_id = int(getattr(son, "node_id", -1))
            if son_sl_id not in local_id_map:
                continue

            dst_local_id = local_id_map[son_sl_id]
            fwd_type = cfg_edge_type(node, son)
            edges.append({
                "src": src_local_id,
                "dst": dst_local_id,
                "edge_type": fwd_type,
            })

            if add_reverse_edges:
                edges.append({
                    "src": dst_local_id,
                    "dst": src_local_id,
                    "edge_type": reverse_edge_type(fwd_type),
                })

    return nodes, edges, analysis_nodes, entry_local_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build function-level CFG graphs from selected candidate functions using Slither."
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        required=True,
        help="Path to candidates JSONL (reentrancy/timestamp/all candidates)",
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
        "--no-reverse-edges",
        action="store_true",
        help="Disable reverse CFG edges",
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

    graph_rows: List[Dict[str, Any]] = []

    total_candidate_rows = len(candidate_rows)
    graphs_built = 0
    missing_source_files = 0
    unmatched_functions = 0
    slither_failed_files = 0
    slither_failed_candidates = 0
    total_graph_nodes = 0
    total_graph_edges = 0
    match_reason_counts: Dict[str, int] = defaultdict(int)
    solc_version_file_counts: Dict[str, int] = defaultdict(int)
    solc_version_candidate_counts: Dict[str, int] = defaultdict(int)
    solc_failed_file_counts: Dict[str, int] = defaultdict(int)
    solc_failed_candidate_counts: Dict[str, int] = defaultdict(int)
    solc_resolution_reason_counts: Dict[str, int] = defaultdict(int)
    WEAK_MATCH_REASONS = {"contract_function_unique_name_only"}

    failed_files: List[Dict[str, Any]] = []

    for rel_path, rows in tqdm(sorted(grouped.items()), desc="Building CFG graphs", unit="file"):
        if not rel_path:
            unmatched_functions += len(rows)
            continue

        source_path = source_root / rel_path
        if not source_path.is_file():
            missing_source_files += len(rows)
            continue

        source_text = read_text(source_path)
        line_starts = build_line_byte_index(source_text)
        source_lines = source_text.splitlines()

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
                "error": f"{type(exc).__name__}: {exc}",
                "candidate_count": len(rows),
                "solc_version": solc_version,
                "solc_binary": solc_binary,
                "solc_resolution_reason": solc_reason,
                "solc_pragma_specs": solc_info.get("pragma_specs", []),
            })
            continue

        for row in rows:
            matched_entry, match_reason = find_matching_slither_function(function_entries, row)
            if matched_entry is None:
                unmatched_functions += 1
                continue
            matched_fn = matched_entry["function_obj"]

            nodes, edges, analysis_nodes, entry_local_id = build_cfg_graph(
                matched_fn,
                line_starts=line_starts,
                source_lines=source_lines,
                function_line_start=int(row.get("line_start", 0) or 0),
                function_line_end=int(row.get("line_end", 0) or 0),
                add_reverse_edges=add_reverse_edges,
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
            out["graph_type"] = "function_level_cfg"
            out["graph_mode"] = "function_cfg_slither"
            out["match_reason"] = match_reason
            out["match_is_weak"] = match_reason in WEAK_MATCH_REASONS
            out["solc_version"] = solc_version
            out["solc_binary"] = solc_binary
            out["solc_resolution_reason"] = solc_reason
            out["slither_function_name"] = matched_entry.get("function_name", "")
            out["slither_line_start"] = matched_entry.get("line_start", 0)
            out["slither_line_end"] = matched_entry.get("line_end", 0)
            out["slither_node_line_start"] = matched_entry.get("node_line_start", 0)
            out["slither_node_line_end"] = matched_entry.get("node_line_end", 0)
            out["slither_effective_line_start"] = matched_entry.get("effective_line_start", 0)
            out["slither_effective_line_end"] = matched_entry.get("effective_line_end", 0)
            out["slither_start"] = matched_entry.get("start")
            out["slither_length"] = matched_entry.get("length")
            out["slither_param_count"] = matched_entry.get("param_count", 0)
            out["entry_local_node_id"] = entry_local_id
            out["node_count"] = len(nodes)
            out["edge_count"] = len(edges)
            out["analysis_node_count"] = len(analysis_nodes)
            out["nodes"] = nodes
            out["edges"] = edges
            out["analysis_nodes"] = analysis_nodes

            graph_rows.append(out)

            graphs_built += 1
            match_reason_counts[match_reason] += 1
            total_graph_nodes += len(nodes)
            total_graph_edges += len(edges)

    graphs_path = output_dir / "function_cfg_graphs.jsonl"
    summary_path = output_dir / "summary.json"

    write_jsonl(graphs_path, graph_rows)

    weak_matches = sum(
        count for reason, count in match_reason_counts.items()
        if reason in WEAK_MATCH_REASONS
    )

    summary = {
        "stage": "step6_function_level_cfg_graph_build",
        "candidates": str(candidates_path),
        "source_root": str(source_root),
        "total_candidate_rows": total_candidate_rows,
        "graphs_built": graphs_built,
        "missing_source_files": missing_source_files,
        "unmatched_functions": unmatched_functions,
        "slither_failed_files": slither_failed_files,
        "slither_failed_candidates": slither_failed_candidates,
        "total_graph_nodes": total_graph_nodes,
        "total_graph_edges": total_graph_edges,
        "avg_nodes_per_graph": (total_graph_nodes / graphs_built) if graphs_built else 0.0,
        "avg_edges_per_graph": (total_graph_edges / graphs_built) if graphs_built else 0.0,
        "match_reason_counts": dict(sorted(match_reason_counts.items())),
        "unique_signature_matches": match_reason_counts.get(
            "contract_function_param_unique_signature",
            0,
        ),
        "unique_name_only_matches": match_reason_counts.get(
            "contract_function_unique_name_only",
            0,
        ),
        "strong_matches": graphs_built - weak_matches,
        "weak_match_ratio": (
            weak_matches / graphs_built
            if graphs_built else 0.0
        ),
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
        "graphs_jsonl": str(graphs_path),
        "failed_files_sample": failed_files[:20],
        "note": (
            "This stage builds control-flow CFG graphs for training and keeps "
            "a separate analysis_nodes sidecar for downstream DFG construction. "
            "The public CFG node list is intentionally control-flow focused and "
            "does not expose raw expression text."
        ),
    }

    save_json(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
