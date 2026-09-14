"""Re-compare inference results vs expected, add new column to xlsx.

Usage:
    python recompare.py <input.xlsx> [output.xlsx]

If output is not specified, saves as <input_stem>_recompared_<HHMMSS>.xlsx
in the same directory as the input file.
"""
import argparse
import json
import re
import sys
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


def extract_json(text: str) -> Any | None:
    """Extract first valid JSON object from text, tolerant of trailing garbage."""
    cleaned = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0]', '', text)
    cleaned = cleaned.strip()
    # Try full string first
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass
    # Find first '{' and try to parse balanced JSON
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:i+1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, TypeError):
                    return None
    return None


def is_subset_match(expected: Any, actual: Any) -> bool:
    """Check if expected and actual match exactly.

    - Top-level dict: every key in expected must exist in actual with matching
      value. Extra keys in actual at top level are ignored.
    - Nested dicts (e.g. slot): must match exactly — same keys AND values.
    - Lists: same length and every element matches pairwise.
    - Scalars: normalised string comparison (strip + case-insensitive).
    """
    return _match(expected, actual, top_level=True)


def _match(expected: Any, actual: Any, *, top_level: bool = False) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if top_level:
            # Top level: expected keys must exist in actual, extras ignored
            for key, exp_val in expected.items():
                if key not in actual:
                    return False
                if not _match(exp_val, actual[key]):
                    return False
            return True
        else:
            # Nested dicts (slot etc.): exact match — same keys, same values
            if set(expected.keys()) != set(actual.keys()):
                return False
            for key in expected:
                if not _match(expected[key], actual[key]):
                    return False
            return True
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return all(_match(e, a) for e, a in zip(expected, actual))
    # Scalar: normalise to stripped lowercase string for comparison
    return str(expected).strip().lower() == str(actual).strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-compare inference results vs expected")
    parser.add_argument("input", help="Input xlsx file path")
    parser.add_argument("output", nargs="?", default="", help="Output xlsx file path (optional)")
    args = parser.parse_args()

    xlsx_path = Path(args.input).resolve()
    if not xlsx_path.exists():
        print(f"Error: File not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = xlsx_path.with_name(
            xlsx_path.stem + f"_recompared_{datetime.now():%H%M%S}" + xlsx_path.suffix
        )

    wb = load_workbook(xlsx_path)
    ws = wb.worksheets[0]

    header_row = [cell.value for cell in ws[1]]
    print(f"Headers: {header_row}")

    expected_col = header_row.index("预期结果") + 1 if "预期结果" in header_row else None
    actual_col = header_row.index("推理结果") + 1 if "推理结果" in header_row else None
    old_cmp_col = header_row.index("比对结果") + 1 if "比对结果" in header_row else None

    if not all([expected_col, actual_col, old_cmp_col]):
        print("Error: Required columns not found (预期结果, 推理结果, 比对结果)", file=sys.stderr)
        sys.exit(1)

    print(f"预期结果 col={expected_col}, 推理结果 col={actual_col}, 比对结果 col={old_cmp_col}")

    # Insert new column after 比对结果
    new_col = old_cmp_col + 1
    ws.insert_cols(new_col)
    ws.cell(1, new_col).value = "新比对结果"
    if ws.cell(1, old_cmp_col).has_style:
        ws.cell(1, new_col)._style = copy(ws.cell(1, old_cmp_col)._style)

    match_count = 0
    mismatch_count = 0
    error_count = 0

    for row in range(2, ws.max_row + 1):
        expected_val = ws.cell(row, expected_col).value
        actual_val = ws.cell(row, actual_col).value
        old_cmp = ws.cell(row, old_cmp_col).value

        if expected_val is None and actual_val is None:
            continue

        expected_str = "" if expected_val is None else str(expected_val)
        actual_str = "" if actual_val is None else str(actual_val)

        if actual_str.startswith("ERROR:"):
            ws.cell(row, new_col).value = "错误"
            error_count += 1
            continue

        expected_json = extract_json(expected_str)
        actual_json = extract_json(actual_str)

        if expected_json is not None and actual_json is not None and is_subset_match(expected_json, actual_json):
            result = "一致"
            match_count += 1
        else:
            result = "不一致"
            mismatch_count += 1
            print(f"\nRow {row} MISMATCH (old={old_cmp}):")
            print(f"  Expected parsed: {expected_json}")
            print(f"  Actual parsed:   {actual_json}")
            if expected_json is None:
                print(f"  -> Expected raw:  {expected_str!r}")
            if actual_json is None:
                print(f"  -> Actual raw:    {actual_str!r}")

        ws.cell(row, new_col).value = result

    ws.column_dimensions[ws.cell(1, new_col).column_letter].width = 14

    wb.save(output_path)
    wb.close()

    print(f"\n=== Summary ===")
    print(f"Total rows : {match_count + mismatch_count + error_count}")
    print(f"Match      : {match_count}")
    print(f"Mismatch   : {mismatch_count}")
    print(f"Error      : {error_count}")
    print(f"Saved to   : {output_path}")


if __name__ == "__main__":
    main()
