"""Level 2: QNN detailed profiling.

Runs qnn-net-run with --profiling_level detailed for each
(stage x part) combination, then pulls profile.csv results.

Based on the logic in tools/run_qnn_profile.sh.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import profile_common as common

logger = logging.getLogger("qnn_profile")

# Stage -> (AR, graph name prefix from context binary export)
STAGE_AR = {"prefill": 128, "decode": 1}
STAGE_GRAPH_PREFIX = {"prefill": "prompt", "decode": "token"}

# Part mapping: which context binary file and which graph name to use.
# NOTE: The context binary file suffix and internal graph part numbers are
# intentionally crossed (1_of_2 file contains graph 2_of_2 and vice versa).
# This mapping is configured via config["part_mapping"] or auto-detected.


def _get_part_mapping(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return {part1: {ctx_file, graph_prefix}, part2: {...}} mapping.

    If the config provides explicit part_mapping, use it.
    Otherwise, derive from context_binaries list order (default 2-part split).
    """
    if "part_mapping" in cfg:
        return cfg["part_mapping"]

    bins = cfg["context_binaries"]
    if len(bins) < 2:
        raise ValueError("Need at least 2 context binaries for part mapping")

    # Default: part1 loads bins[1] (2_of_2), part2 loads bins[0] (1_of_2)
    # This matches the known reversed naming convention.
    return {
        "part1": {"ctx_index": 1, "graph_suffix": "1_of_2"},
        "part2": {"ctx_index": 0, "graph_suffix": "2_of_2"},
    }


def _find_context_info_dir(cfg: dict[str, Any]) -> Path | None:
    """Locate the context_info directory for this model.

    Search order: cfg output_dir (per-model results dir) first, then the
    toolkit root (legacy shared layout).
    """
    roots = []
    if cfg.get("output_dir"):
        roots.append(Path(cfg["output_dir"]))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        for rel in ["context_info/context_info", "context_info"]:
            d = root / rel
            if d.is_dir():
                return d
    return None


def _detect_context_lengths(cfg: dict[str, Any]) -> list[int]:
    """Detect all context lengths from exported context_info JSON files.

    Scans graph names in graph*.json to find all CL values.
    Falls back to config context_length if detection fails.
    """
    ci_dir = _find_context_info_dir(cfg)
    if ci_dir is None:
        cl = cfg.get("context_length", 1024)
        logger.warning("[Level2] context_info not found, using config CL=%d", cl)
        return [cl]

    cls_set: set[int] = set()
    for jf in ci_dir.glob("graph*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            for g in data.get("info", {}).get("graphs", []):
                gn = g.get("info", {}).get("graphName", "")
                m = re.search(r"_cl(\d+)_", gn)
                if m:
                    cls_set.add(int(m.group(1)))
        except Exception:
            pass

    if cls_set:
        result = sorted(cls_set)
        logger.info("[Level2] Detected context lengths from context_info: %s", result)
        return result

    cl = cfg.get("context_length", 1024)
    logger.warning("[Level2] Could not parse CL from context_info, using config CL=%d", cl)
    return [cl]


def _detect_graph_names(cfg: dict[str, Any]) -> dict[tuple[int, int, str], str]:
    """Scan context_info graph*.json for actual graph names.

    Returns {(ar, cl, part_suffix): graphName}, e.g. (128, 4096, "1_of_2") ->
    "ar128_cl4096_1_of_2". Graph naming is model-dependent (some models prefix
    stage names like prompt_/token_, others don't), so resolve from the exported
    metadata instead of hardcoding a prefix.
    """
    ci_dir = _find_context_info_dir(cfg)
    result: dict[tuple[int, int, str], str] = {}
    if ci_dir is None:
        return result
    for jf in ci_dir.glob("graph*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            for g in data.get("info", {}).get("graphs", []):
                gn = g.get("info", {}).get("graphName", "")
                m = re.search(r"ar(\d+)_cl(\d+)_(.+)$", gn)
                if m:
                    result[(int(m.group(1)), int(m.group(2)), m.group(3))] = gn
        except Exception:
            pass
    return result


def _build_profile_script(
    cfg: dict[str, Any],
    stage: str,
    part: str,
    mode: str,
    ctx_file: str,
    graph_name: str,
    input_list: str,
    output_dir: str,
    all_cls: list[int] | None = None,
) -> str:
    """Build the device-side shell script for QNN profiling."""
    dev_qnn = cfg["device_qnn_root"]
    dev_root = cfg["device_root"]
    profile_lib = f"{dev_qnn}/profile_lib"

    ar = STAGE_AR[stage]
    cl = cfg["context_length"]

    # Build input selector for multi-graph context binary.
    # Each context binary contains N graphs: [prefill_cl1, prefill_cl2, ..., decode_cl1, decode_cl2, ...]
    # We must provide one input_list entry per graph (use __ to skip unused graphs).
    if all_cls and len(all_cls) > 1:
        n_cls = len(all_cls)
        cl_pos = all_cls.index(cl) if cl in all_cls else 0
        if stage == "prefill":
            graph_idx = cl_pos
        else:
            graph_idx = n_cls + cl_pos
        total_graphs = 2 * n_cls
        entries = ["__"] * total_graphs
        entries[graph_idx] = input_list
        input_selector = ",".join(entries)
    else:
        # Single-graph fallback (legacy context binaries)
        if stage == "prefill":
            input_selector = f"{input_list},__"
        else:
            input_selector = f"__,{input_list}"

    # Profile args
    if mode == "detailed":
        profile_args = "--profiling_level detailed"
        profile_reader = f"{profile_lib}/libQnnHtpProfilingReader.so"
    elif mode == "optrace":
        profile_args = "--profiling_level detailed --profiling_option optrace"
        profile_reader = f"{profile_lib}/libQnnHtpOptraceProfilingReader.so"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return f"""\
export LD_LIBRARY_PATH="{dev_qnn}/lib:{dev_root}/lib:{dev_root}/model:{profile_lib}:{dev_qnn}/bin:${{LD_LIBRARY_PATH:-}}"
export ADSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev_qnn}/dsp;{dev_root}/dsp;{dev_root}/model/dsp"

rm -rf "{output_dir}"
mkdir -p "{output_dir}"

cd "{output_dir}"
{dev_qnn}/bin/qnn-net-run \\
  --backend {dev_qnn}/lib/libQnnHtp.so \\
  --retrieve_context {ctx_file} \\
  --input_list {input_selector} \\
  --output_dir {output_dir}/output \\
  {profile_args} \\
  --perf_profile burst \\
  --use_native_input_files \\
  --use_native_output_files \\
  --num_inferences 1 \\
  --keep_num_outputs 1 \\
  --log_level info

echo "Profile artifacts:"
find "{output_dir}" -type f 2>/dev/null

# Convert raw profiling log to CSV using qnn-profile-viewer
PROFILE_VIEWER="{dev_qnn}/bin/qnn-profile-viewer"
if [ -x "$PROFILE_VIEWER" ]; then
    PROF_LOG=$(find "{output_dir}" -name 'qnn-profiling-data_*.log' -type f 2>/dev/null | head -1)
    if [ -n "$PROF_LOG" ]; then
        echo "Converting profiling log to CSV..."
        $PROFILE_VIEWER \\
            --input_log "$PROF_LOG" \\
            --output="{output_dir}/output/profile.csv" \\
            2>&1 | tail -3
        echo "profile.csv generated."
    fi
fi
"""


def _validate_qnn_version(cfg: dict[str, Any]) -> None:
    """Log qnn-net-run version for reference."""
    serial = cfg["serial"]
    dev_qnn = cfg["device_qnn_root"]
    result = common.adb_run(
        ["shell", f"{dev_qnn}/bin/qnn-net-run --version 2>&1 || true"],
        serial=serial, check=False, capture=True,
    )
    version_output = result.stdout.strip()
    logger.info("[Level2] qnn-net-run version: %s", version_output)


def deploy_qnn_tools(cfg: dict[str, Any]) -> None:
    """Deploy QNN tools (qnn-net-run, qnn-context-binary-utility, profile_lib) to device.

    Uses config 'qnn_sdk_root' for all QNN tools and libraries (level2/3 specific SDK).
    Falls back to 'sdk_root' if 'qnn_sdk_root' is not set.
    All files come from a single SDK to ensure version consistency.
    """
    serial = cfg["serial"]
    # Use qnn_sdk_root for level2/3 if available, else fall back to sdk_root
    qnn_sdk = Path(cfg["qnn_sdk_root"]) if cfg.get("qnn_sdk_root") else Path(cfg["sdk_root"])
    dev_qnn = cfg["device_qnn_root"]
    htp_arch = cfg["htp_arch"]

    android_bin = qnn_sdk / "bin" / "aarch64-android"
    android_lib = qnn_sdk / "lib" / "aarch64-android"

    # Validate mandatory local files
    for f in [android_bin / "qnn-net-run", android_bin / "qnn-context-binary-utility"]:
        common.assert_local_file(f)

    # All libs from the same QNN SDK — no mixing
    lib_htp = android_lib / "libQnnHtp.so"
    lib_system = android_lib / "libQnnSystem.so"
    lib_v73stub = android_lib / f"libQnnHtpV{htp_arch}Stub.so"
    lib_htp_ext = android_lib / "libQnnHtpNetRunExtensions.so"

    if not lib_htp.is_file():
        raise FileNotFoundError(f"libQnnHtp.so not found in QNN SDK: {android_lib}")
    logger.info("[Deploy-QNN] Using QNN SDK: %s", qnn_sdk)
    logger.info("[Deploy-QNN] libQnnHtp.so from: %s", lib_htp)

    # Find profile_lib
    toolkit_root = Path(__file__).resolve().parent.parent
    profile_lib_local = toolkit_root / "profile_lib"
    if not profile_lib_local.is_dir():
        profile_lib_local = toolkit_root.parent / "profile-results" / "profile_lib"
    if not profile_lib_local.is_dir():
        raise FileNotFoundError(
            f"profile_lib directory not found. Searched:\n"
            f"  {toolkit_root / 'profile_lib'}\n"
            f"  {profile_lib_local}\n"
            f"Please ensure profile_lib/ with libQnnHtpProfilingReader.so exists."
        )

    logger.info("[Deploy-QNN] Pushing QNN tools to %s...", dev_qnn)
    common.adb_run(
        ["shell", f"mkdir -p '{dev_qnn}/bin' '{dev_qnn}/lib' '{dev_qnn}/profile_lib' '{dev_qnn}/dsp'"],
        serial=serial,
    )

    # Push binaries from QNN SDK
    common.adb_run(["push", str(android_bin / "qnn-net-run"), f"{dev_qnn}/bin/"], serial=serial)
    common.adb_run(["push", str(android_bin / "qnn-context-binary-utility"), f"{dev_qnn}/bin/"], serial=serial)
    # Push qnn-profile-viewer (converts raw profiling log to CSV)
    profile_viewer = android_bin / "qnn-profile-viewer"
    if profile_viewer.is_file():
        common.adb_run(["push", str(profile_viewer), f"{dev_qnn}/bin/"], serial=serial)
        logger.info("[Deploy-QNN] qnn-profile-viewer deployed")
    # Push core libs (all from same QNN SDK)
    if lib_system.is_file():
        common.adb_run(["push", str(lib_system), f"{dev_qnn}/lib/"], serial=serial)
        logger.info("[Deploy-QNN] libQnnSystem.so from: %s", lib_system)
    common.adb_run(["push", str(lib_htp), f"{dev_qnn}/lib/"], serial=serial)
    if lib_v73stub.is_file():
        common.adb_run(["push", str(lib_v73stub), f"{dev_qnn}/lib/"], serial=serial)
        logger.info("[Deploy-QNN] libQnnHtpV%sStub.so from: %s", htp_arch, lib_v73stub)
    if lib_htp_ext.is_file():
        common.adb_run(["push", str(lib_htp_ext), f"{dev_qnn}/lib/"], serial=serial)
        logger.info("[Deploy-QNN] libQnnHtpNetRunExtensions.so from: %s", lib_htp_ext)
    common.adb_run(["shell", f"chmod 755 '{dev_qnn}/bin/qnn-net-run' '{dev_qnn}/bin/qnn-context-binary-utility' '{dev_qnn}/bin/qnn-profile-viewer'"], serial=serial)

    # Push C++ runtime libs (required by libQnnHtp.so on device).
    # Prefer the SDK's own lib dir (vendor copy), fallback to project jniLibs.
    cpp_lib_dirs = [android_lib]
    jni_libs = common.find_jni_libs_dir()
    if jni_libs:
        cpp_lib_dirs.append(jni_libs)
    for cpp_lib_name in ["libc++.so.1", "libc++abi.so.1"]:
        for lib_dir in cpp_lib_dirs:
            cpp_lib = lib_dir / cpp_lib_name
            if cpp_lib.is_file():
                common.adb_run(["push", str(cpp_lib), f"{dev_qnn}/lib/"], serial=serial)
                logger.info("[Deploy-QNN] %s pushed from %s", cpp_lib_name, lib_dir)
                break

    # Push profile_lib from QNN SDK, fallback to local profile_lib/
    logger.info("[Deploy-QNN] Pushing profiling libraries to %s/profile_lib/...", dev_qnn)
    profiling_readers = [
        android_lib / "libQnnHtpProfilingReader.so",
        android_lib / "libQnnHtpOptraceProfilingReader.so",
    ]
    pushed_any = False
    for so_file in profiling_readers:
        if so_file.is_file():
            common.adb_run(["push", str(so_file), f"{dev_qnn}/profile_lib/"], serial=serial)
            pushed_any = True
    if not pushed_any:
        if profile_lib_local.is_dir():
            for so_file in profile_lib_local.glob("*.so"):
                common.adb_run(["push", str(so_file), f"{dev_qnn}/profile_lib/"], serial=serial)
        else:
            logger.warning("[Deploy-QNN] No profiling readers found")

    # Push DSP libs from QNN SDK
    dsp_lib = qnn_sdk / "lib" / f"hexagon-v{htp_arch}" / "unsigned"
    if dsp_lib.is_dir():
        for so_file in dsp_lib.glob("*.so"):
            common.adb_run(["push", str(so_file), f"{dev_qnn}/dsp/"], serial=serial)

    logger.info("[Deploy-QNN] QNN tools deployed to %s", dev_qnn)


def run(
    cfg: dict[str, Any],
    output_dir: Path,
    mode: str = "detailed",
) -> dict[str, dict[str, str]]:
    """Run Level 2 (or Level 3) QNN profiling for all stage x part combos.

    Args:
        cfg: Project configuration dict.
        output_dir: Local directory to store results.
        mode: "detailed" for Level 2, "optrace" for Level 3.

    Returns:
        Dict mapping "stage/part" -> {"profile_csv": path, "output_dir": path, ...}
    """
    serial = cfg["serial"]
    dev_root = cfg["device_root"]
    dev_qnn = cfg["device_qnn_root"]
    part_mapping = _get_part_mapping(cfg)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate environment
    logger.info("[Level2/%s] Checking environment...", mode)
    common.assert_device(serial)
    common.device_executable_exists(f"{dev_qnn}/bin/qnn-net-run", serial)
    _validate_qnn_version(cfg)

    results: dict[str, dict[str, str]] = {}

    # Detect all context lengths from context_info (needed for multi-graph input selector)
    all_cls = _detect_context_lengths(cfg)
    graph_names = _detect_graph_names(cfg)

    for stage in ["prefill", "decode"]:
        ar = STAGE_AR[stage]
        for part in ["part1", "part2"]:
            key = f"{stage}/{part}"
            logger.info("[Level2/%s] Running %s...", mode, key)

            mapping = part_mapping[part]
            ctx_index = mapping["ctx_index"]
            graph_suffix = mapping["graph_suffix"]
            ctx_file = f"{dev_root}/model/{cfg['context_binaries'][ctx_index]}"
            # Resolve the real graph name from context_info (naming is
            # model-dependent); fall back to the legacy prefixed convention.
            graph_name = graph_names.get((ar, cfg["context_length"], graph_suffix))
            if not graph_name:
                graph_prefix = STAGE_GRAPH_PREFIX[stage]
                graph_name = f"{graph_prefix}_ar{ar}_cl{cfg['context_length']}_{graph_suffix}"

            input_list = f"{dev_root}/qnn_inputs/{graph_name}/input_list.txt"

            # Verify input list exists
            common.adb_run(
                ["shell", f"test -s '{input_list}'"],
                serial=serial,
            )

            # Device output directory
            dev_output = f"{dev_root}/qnn_profile/{mode}/{stage}/{part}"

            # Build and run profile script
            script = _build_profile_script(
                cfg=cfg,
                stage=stage,
                part=part,
                mode=mode,
                ctx_file=ctx_file,
                graph_name=graph_name,
                input_list=input_list,
                output_dir=dev_output,
                all_cls=all_cls,
            )
            log_output = common.device_shell(script, serial=serial, merge_stderr=True)

            # Pull results to local
            local_part_dir = output_dir / stage / part
            local_part_dir.mkdir(parents=True, exist_ok=True)

            # Save run log
            log_file = local_part_dir / "run.log"
            log_file.write_text(log_output, encoding="utf-8")

            # Pull output directory
            pull_result = common.adb_run(
                ["pull", f"{dev_output}/output/.", str(local_part_dir / "output")],
                serial=serial, check=False, capture=True,
            )
            if pull_result.returncode != 0:
                logger.warning("[Level2/%s] Failed to pull output for %s", mode, key)

            # Collect result paths
            result_entry = {
                "run_log": str(log_file),
                "output_dir": str(local_part_dir / "output"),
                "device_output": dev_output,
            }

            # Check for profile.csv
            profile_csv = local_part_dir / "output" / "profile.csv"
            if profile_csv.is_file():
                result_entry["profile_csv"] = str(profile_csv)

            # Check for optrace.csv
            optrace_csv = local_part_dir / "output" / "optrace.csv"
            if optrace_csv.is_file():
                result_entry["optrace_csv"] = str(optrace_csv)

            # Check for profile-from-optrace.csv
            optrace_profile = local_part_dir / "output" / "profile-from-optrace.csv"
            if optrace_profile.is_file():
                result_entry["profile_from_optrace"] = str(optrace_profile)

            results[key] = result_entry
            logger.info("[Level2/%s] Completed %s", mode, key)

    return results
