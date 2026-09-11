#!/usr/bin/env python3
"""One-click QNN profiling orchestrator.

Usage:
    python run_profile.py --config profile-config.json --action all
    python run_profile.py --config profile-config.json --action level2
    python run_profile.py --config profile-config.json --action analyze

Actions:
    all             - Full pipeline: deploy, export metadata, generate inputs, run all levels, report
    deploy          - Deploy Genie runtime + QNN tools to device
    level1          - Level 1: Genie app-level profiling
    level2          - Level 2: QNN detailed profiling
    level3          - Level 3: QNN optrace profiling
    analyze         - Analyze existing results and generate report
    export-context  - Export context binary metadata (graph info) from device
    generate-inputs - Generate synthetic QNN inputs from context metadata
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

# Ensure the scripts package is importable
SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts import profile_common as common
from scripts import run_level1_genie
from scripts import run_level2_detailed
from scripts import run_level3_optrace
from scripts import analyze_results

logger = logging.getLogger("qnn_profile")


def _resolve_output_dir(cfg: dict, args_output: str | None) -> Path:
    """Determine the output directory for this profiling run."""
    if args_output:
        return Path(args_output)
    cfg_output = cfg.get("output_dir", "")
    if cfg_output:
        return Path(cfg_output)
    return Path(__file__).resolve().parent


def action_deploy(cfg: dict) -> None:
    """Deploy Genie runtime + QNN tools to the device."""
    common.assert_device(cfg["serial"])
    # Deploy Genie runtime (genie-t2t-run, model, libs)
    run_level1_genie.deploy(cfg)
    # Deploy QNN tools (qnn-net-run, profile_lib, context-binary-utility)
    run_level2_detailed.deploy_qnn_tools(cfg)
    logger.info("Deployment completed successfully.")


def action_level1(cfg: dict, output_dir: Path) -> dict:
    """Run Level 1 Genie profiling."""
    level1_dir = output_dir / "level1"
    logger.info("=" * 60)
    logger.info("Level 1: Genie App-Level Profiling")
    logger.info("=" * 60)
    results = run_level1_genie.run(cfg, level1_dir)
    logger.info("Level 1 results: %s", json.dumps(results, indent=2))
    return results


def action_level2(cfg: dict, output_dir: Path) -> dict:
    """Run Level 2 QNN detailed profiling."""
    level2_dir = output_dir / "level2" / "detailed"
    logger.info("=" * 60)
    logger.info("Level 2: QNN Detailed Profiling")
    logger.info("=" * 60)
    results = run_level2_detailed.run(cfg, level2_dir, mode="detailed")
    for key, entry in results.items():
        logger.info("  %s -> %s", key, entry.get("profile_csv", "no csv"))
    return results


def action_level3(cfg: dict, output_dir: Path) -> dict:
    """Run Level 3 QNN optrace profiling."""
    level3_dir = output_dir / "level3" / "optrace"
    logger.info("=" * 60)
    logger.info("Level 3: QNN Optrace Profiling")
    logger.info("=" * 60)
    results = run_level3_optrace.run(cfg, level3_dir)
    for key, entry in results.items():
        logger.info("  %s -> %s", key, entry.get("profile_from_optrace", "no csv"))
    return results


def action_analyze(cfg: dict, output_dir: Path) -> Path:
    """Analyze existing results and generate report."""
    logger.info("=" * 60)
    logger.info("Analyzing results and generating report...")
    logger.info("=" * 60)

    level1_dir = output_dir / "level1"
    level2_dir = output_dir / "level2" / "detailed"
    level3_dir = output_dir / "level3" / "optrace"

    report_path = analyze_results.analyze(
        cfg=cfg,
        output_dir=output_dir,
        level1_dir=level1_dir if level1_dir.is_dir() else None,
        level2_dir=level2_dir if level2_dir.is_dir() else None,
        level3_dir=level3_dir if level3_dir.is_dir() else None,
    )
    logger.info("Report generated: %s", report_path)
    return report_path


def action_export_context(cfg: dict, output_dir: Path) -> None:
    """Export context binary metadata from device to local context_info/."""
    logger.info("=" * 60)
    logger.info("Exporting context binary metadata...")
    logger.info("=" * 60)

    serial = cfg["serial"]
    dev_root = cfg["device_root"]
    dev_qnn = cfg["device_qnn_root"]
    context_binaries = cfg["context_binaries"]
    local_output = output_dir / "context_info" / "context_info"
    dev_export_dir = "/data/local/tmp/qnn_context_info"

    common.assert_device(serial)

    # Ensure tool exists on device
    common.device_executable_exists(f"{dev_qnn}/bin/qnn-context-binary-utility", serial)

    # Export metadata for each context binary
    common.adb_run(
        ["shell", f"rm -rf '{dev_export_dir}' && mkdir -p '{dev_export_dir}'"],
        serial=serial,
    )

    for idx, ctx_bin in enumerate(context_binaries):
        graph_json = f"graph{idx + 1}.json"
        dev_ctx = f"{dev_root}/model/{ctx_bin}"
        dev_json = f"{dev_export_dir}/{graph_json}"

        logger.info("[Export] Processing %s -> %s", ctx_bin, graph_json)
        common.adb_run(
            ["shell", f"test -f '{dev_ctx}'"],
            serial=serial,
        )

        script = (
            f"export LD_LIBRARY_PATH='{dev_qnn}/lib:{dev_root}/model' && "
            f"'{dev_qnn}/bin/qnn-context-binary-utility' "
            f"--context_binary='{dev_ctx}' --json_file='{dev_json}'"
        )
        common.device_shell(script, serial=serial)

        common.adb_run(
            ["shell", f"test -s '{dev_json}'"],
            serial=serial,
        )

    # Pull JSON files to local
    local_output.mkdir(parents=True, exist_ok=True)
    for idx in range(len(context_binaries)):
        graph_json = f"graph{idx + 1}.json"
        common.adb_run(
            ["pull", f"{dev_export_dir}/{graph_json}", str(local_output / graph_json)],
            serial=serial,
        )

    logger.info("Context metadata exported to: %s", local_output)


def action_generate_inputs(cfg: dict, output_dir: Path) -> None:
    """Generate synthetic QNN inputs from context metadata."""
    logger.info("=" * 60)
    logger.info("Generating synthetic QNN inputs...")
    logger.info("=" * 60)

    metadata_dir = output_dir / "context_info" / "context_info"
    inputs_dir = output_dir / "qnn_inputs"

    if not metadata_dir.is_dir():
        logger.error("Context metadata not found: %s", metadata_dir)
        logger.error("Run 'export-context' action first to export context metadata.")
        sys.exit(1)

    # Find generate_qnn_synthetic_inputs.py
    # Search order: config tools_dir > toolkit's tools/ > parent project's tools/
    script = None
    tools_dir = cfg.get("tools_dir", "")
    candidates = []
    if tools_dir:
        candidates.append(Path(tools_dir) / "generate_qnn_synthetic_inputs.py")
    candidates.append(Path(__file__).resolve().parent.parent / "tools" / "generate_qnn_synthetic_inputs.py")
    candidates.append(Path(__file__).resolve().parent.parent.parent / "tools" / "generate_qnn_synthetic_inputs.py")

    for candidate in candidates:
        if candidate.is_file():
            script = candidate
            break

    if script is None:
        logger.error("generate_qnn_synthetic_inputs.py not found. Searched:")
        for c in candidates:
            logger.error("  %s", c)
        sys.exit(1)

    if script.is_file():
        subprocess.run(
            [sys.executable, str(script),
             "--metadata-dir", str(metadata_dir),
             "--output-dir", str(inputs_dir),
             "--device-root", f"{cfg['device_root']}/qnn_inputs"],
            check=True,
        )
    else:
        logger.error("generate_qnn_synthetic_inputs.py not found")
        sys.exit(1)

    # Push inputs to device
    serial = cfg["serial"]
    dev_root = cfg["device_root"]
    common.device_mkdir(f"{dev_root}/qnn_inputs", serial)
    common.adb_run(["push", f"{inputs_dir}/.", f"{dev_root}/qnn_inputs/"], serial=serial)
    logger.info("Synthetic inputs generated and pushed to device.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-click QNN profiling orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to profile-config.json (default: auto-detect from models/*/)",
    )
    parser.add_argument(
        "--action", "-a",
        choices=["all", "deploy", "level1", "level2", "level3", "analyze",
                 "export-context", "generate-inputs"],
        default="all",
        help="Action to perform (default: all)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Override output directory (default: from config)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--skip-device-check",
        action="store_true",
        help="Skip ADB device check (for dry-run or analyze-only)",
    )

    args = parser.parse_args()

    # Resolve config path: explicit --config, else auto-detect under models/
    script_dir = Path(__file__).resolve().parent
    if args.config:
        config_path = Path(args.config)
        if not config_path.is_file():
            alt = script_dir / config_path
            if alt.is_file():
                config_path = alt
    else:
        config_path = common.find_default_config(script_dir)

    # Load config
    cfg = common.load_config(config_path)

    # Setup logging
    output_dir = _resolve_output_dir(cfg, args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    common.setup_logging(log_dir=output_dir, verbose=args.verbose)

    logger.info("Project: %s", cfg["project"])
    logger.info("Model: %s", cfg["model_label"])
    logger.info("Output: %s", output_dir)
    logger.info("Action: %s", args.action)

    # Execute action
    if args.action == "deploy":
        action_deploy(cfg)

    elif args.action == "level1":
        action_level1(cfg, output_dir)

    elif args.action == "level2":
        action_level2(cfg, output_dir)

    elif args.action == "level3":
        action_level3(cfg, output_dir)

    elif args.action == "analyze":
        action_analyze(cfg, output_dir)

    elif args.action == "export-context":
        action_export_context(cfg, output_dir)

    elif args.action == "generate-inputs":
        action_generate_inputs(cfg, output_dir)

    elif args.action == "all":
        # Full pipeline
        if not args.skip_device_check:
            common.assert_device(cfg["serial"])

        # Step 1: Deploy runtime + tools
        try:
            action_deploy(cfg)
        except Exception as e:
            logger.error("Deploy failed: %s", e)
            logger.warning("Continuing (assuming already deployed)...")

        # Step 2: Export context metadata
        try:
            action_export_context(cfg, output_dir)
        except Exception as e:
            logger.error("Context export failed: %s", e)
            logger.warning("Continuing with existing metadata...")

        # Step 3: Generate synthetic inputs
        try:
            action_generate_inputs(cfg, output_dir)
        except Exception as e:
            logger.error("Input generation failed: %s", e)
            logger.error("Cannot proceed without QNN inputs.")
            return 1

        # Step 4-6: Levels (honor config "levels" filter)
        enabled_levels = cfg.get("levels") or ["level1", "level2", "level3"]
        for level_name, level_action in [
            ("level1", action_level1),
            ("level2", action_level2),
            ("level3", action_level3),
        ]:
            if level_name not in enabled_levels:
                logger.info("Skipping %s (not in config 'levels')", level_name)
                continue
            try:
                level_action(cfg, output_dir)
            except Exception as e:
                logger.error("%s failed: %s", level_name, e)
                logger.warning("Continuing...")

        # Step 7: Analysis
        try:
            report = action_analyze(cfg, output_dir)
            logger.info("=" * 60)
            logger.info("All profiling complete!")
            logger.info("Report: %s", report)
            logger.info("=" * 60)
        except Exception as e:
            logger.error("Analysis failed: %s", e)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
