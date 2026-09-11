"""Analyze profiling results and generate Markdown reports.

Parses Level 1 (genie-profile.json), Level 2 (profile.csv), and
Level 3 (optrace/profile-from-optrace.csv) results, then produces
a structured performance analysis report.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import profile_common as common

logger = logging.getLogger("qnn_profile")


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_profile_csv(path: Path) -> dict[str, Any]:
    """Parse a QNN profile.csv file and extract key metrics."""
    if not path.is_file():
        return {}

    rows = []
    metadata: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Header metadata lines (before the CSV header)
            if line.startswith("input log file") or line.startswith("qnn-profile-viewer") or \
               line.startswith("qnn-net-run") or line.startswith("Backend version"):
                key, _, val = line.partition(",")
                metadata[key.strip()] = val.strip()
                continue
            # CSV header
            if line.startswith("Msg Timestamp"):
                continue
            # Data row
            parts = next(csv.reader([line]))
            if len(parts) >= 7:
                try:
                    rows.append({
                        "timestamp": int(parts[0]),
                        "message": parts[1].strip(),
                        "time": parts[2].strip(),
                        "unit": parts[3].strip(),
                        "timing_source": parts[4].strip(),
                        "event_level": parts[5].strip(),
                        "event_id": parts[6].strip(),
                    })
                except (ValueError, IndexError):
                    continue

    return {"metadata": metadata, "rows": rows}


def _extract_root_metrics(parsed: dict[str, Any]) -> dict[str, float | int]:
    """Extract ROOT-level timing metrics from parsed CSV data."""
    metrics: dict[str, float | int] = {}
    for row in parsed.get("rows", []):
        if row["event_level"] != "ROOT":
            continue
        msg = row["message"]
        unit = row["unit"]
        try:
            value = float(row["time"])
        except ValueError:
            continue

        key = f"{msg}|{unit}"
        if "EXECUTE" in msg and unit == "US" and "NETRUN" in row["timing_source"]:
            metrics["netrun_execute_us"] = value
        elif "EXECUTE" in msg and unit == "US" and "RPC" in row["event_id"]:
            metrics["rpc_execute_us"] = value
        elif "EXECUTE" in msg and unit == "US" and row["event_id"].lower() == "accelerator (execute) time":
            metrics["accelerator_execute_us"] = value
        elif "EXECUTE" in msg and unit == "CYCLES" and "ROOT" in row["event_level"]:
            metrics["accelerator_cycles"] = int(value)
        elif msg == "INIT" and unit == "US" and "NETRUN" in row["timing_source"]:
            metrics["init_us"] = value
        elif "DE-INIT" in msg and unit == "US" and "NETRUN" in row["timing_source"]:
            metrics["deinit_us"] = value

    return metrics


def _extract_sub_events(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract SUB-EVENT rows with cycles data."""
    events = []
    for row in parsed.get("rows", []):
        if row["event_level"] != "SUB-EVENT":
            continue
        if row["unit"] != "CYCLES":
            continue
        try:
            cycles = int(float(row["time"]))
        except ValueError:
            continue
        event_id = row["event_id"]
        # Parse operator name from "OperatorName:OpId_NNN (cycles)"
        # or "Output OpId_N" or "Input OpId_N"
        op_name = event_id
        match = re.match(r"(.+?):OpId_\d+\s*\(cycles\)", event_id)
        if match:
            op_name = match.group(1)
        else:
            match2 = re.match(r"(\w+)\s+OpId_\d+", event_id)
            if match2:
                op_name = match2.group(1)
        events.append({"name": op_name, "cycles": cycles, "raw_id": event_id})
    return events


def _classify_operators(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group sub-events by operator type and compute aggregate stats."""
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "cycles": 0})
    for ev in events:
        name = ev["name"]
        # Classify by operator type prefix
        op_type = _extract_op_type(name)
        groups[op_type]["count"] += 1
        groups[op_type]["cycles"] += ev["cycles"]
    return dict(groups)


def _extract_op_type(name: str) -> str:
    """Extract operator type from a full operator name."""
    # Common patterns
    patterns = [
        (r"MatMul", "MatMul"),
        (r"Conv", "Conv"),
        (r"Softmax", "Softmax"),
        (r"RMSNorm", "RMSNorm"),
        (r"Add_\d+", "Add"),
        (r"Mul_\d+", "Mul"),
        (r"Slice_\d+", "Slice"),
        (r"Concat", "Concat"),
        (r"Transpose", "Transpose"),
        (r"^Input(?:\s|$)", "Input"),
        (r"^Output(?:\s|$)", "Output"),
        (r"Gather", "Gather"),
        (r"mlp_act_fn_Mul", "Mul"),
        (r"self_attn_Mul", "Mul"),
        (r"residual_Mul", "Mul"),
        (r"mlp_act_fn_Add", "Add"),
        (r"self_attn_Add", "Add"),
        (r"residual_Add", "Add"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, name):
            return label
    return "Other"


# ---------------------------------------------------------------------------
# Level 1 analysis
# ---------------------------------------------------------------------------

def _analyze_level1(profile_json_path: Path) -> dict[str, Any] | None:
    """Parse genie-profile.json and extract key metrics."""
    if not profile_json_path.is_file():
        return None
    try:
        data = json.loads(profile_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse Level1 profile: %s", e)
        return None

    result: dict[str, Any] = {"raw": data, "dialogs": []}
    for component in data.get("components", []):
        if component.get("type") != "dialog":
            continue
        dialog_info: dict[str, Any] = {"name": component["name"], "events": {}}
        for event in component.get("events", []):
            etype = event.get("type", "")
            info: dict[str, Any] = {"duration_us": event.get("duration")}
            if "init-time" in event:
                info["init_time_us"] = event["init-time"].get("value")
            if "prompt-processing-rate" in event:
                info["prefill_rate"] = event["prompt-processing-rate"].get("value")
            if "time-to-first-token" in event:
                info["ttft_us"] = event["time-to-first-token"].get("value")
            if "token-generation-rate" in event:
                info["decode_rate"] = event["token-generation-rate"].get("value")
            if "token-generation-time" in event:
                value = event["token-generation-time"]
                unit = value.get("unit")
                scale = {"us": 1, "ms": 1000, "s": 1000000}.get(unit)
                if scale is not None and value.get("value") is not None:
                    info["decode_time_us"] = value["value"] * scale
            if "num-generated-tokens" in event:
                info["gen_tokens"] = event["num-generated-tokens"].get("value")
            if "num-prompt-tokens" in event:
                info["prompt_tokens"] = event["num-prompt-tokens"].get("value")
            dialog_info["events"][etype] = info
        result["dialogs"].append(dialog_info)
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _generate_report(cfg, level1_data, level2_data, level3_data, device_info):
    from .report_zh import generate
    return generate(cfg, level1_data, level2_data, level3_data, device_info)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    cfg: dict[str, Any],
    output_dir: Path,
    level1_dir: Path | None = None,
    level2_dir: Path | None = None,
    level3_dir: Path | None = None,
) -> Path:
    """Run analysis and generate a Markdown report.

    Args:
        cfg: Project configuration.
        output_dir: Base output directory for this profiling run.
        level1_dir: Directory containing genie-profile.json (Level 1 results).
        level2_dir: Directory containing detailed profiling results (Level 2).
        level3_dir: Directory containing optrace profiling results (Level 3).

    Returns:
        Path to the generated report file.
    """
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Collect device info
    try:
        device_info = common.get_device_info(cfg["serial"])
    except Exception:
        device_info = {"model": "unknown", "abi": "unknown", "sdk": "unknown",
                       "brand": "", "hardware": ""}

    # Level 1
    level1_data = None
    if level1_dir:
        # Try multiple possible filenames for the profile JSON
        for name in ["genie-profile.json", "level1_profile.json"]:
            profile_json = level1_dir / name
            if profile_json.is_file():
                level1_data = _analyze_level1(profile_json)
                break

    # Level 2
    level2_data: dict[str, dict[str, str]] = {}
    if level2_dir:
        for stage in ["prefill", "decode"]:
            for part in ["part1", "part2"]:
                key = f"{stage}/{part}"
                csv_path = level2_dir / stage / part / "output" / "profile.csv"
                level2_data[key] = {"profile_csv": str(csv_path)}

    # Level 3
    level3_data = None
    if level3_dir:
        level3_data = {}
        for stage in ["prefill", "decode"]:
            for part in ["part1", "part2"]:
                key = f"{stage}/{part}"
                # optrace may produce profile-from-optrace.csv instead
                optrace_csv = level3_dir / stage / part / "output" / "profile-from-optrace.csv"
                profile_csv = level3_dir / stage / part / "output" / "profile.csv"
                entry: dict[str, str] = {}
                if optrace_csv.is_file():
                    entry["profile_from_optrace"] = str(optrace_csv)
                if profile_csv.is_file():
                    entry["profile_csv"] = str(profile_csv)
                level3_data[key] = entry

    # Generate report
    report_content = _generate_report(cfg, level1_data, level2_data, level3_data, device_info)
    ts = common.timestamp_dir_name()
    report_path = reports_dir / f"{ts}-three-level-performance-analysis.md"
    report_path.write_text(report_content, encoding="utf-8")
    logger.info("Report generated: %s", report_path)

    return report_path
