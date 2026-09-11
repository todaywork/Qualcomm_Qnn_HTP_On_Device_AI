"""Level 1: Genie app-level profiling.

Deploys Genie runtime (genie-t2t-run + libs), runs inference with
profiling enabled, and collects genie-profile.json, console log, logcat.

Based on the logic in tools/qwen3-genie-device-profile.ps1.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from . import profile_common as common

logger = logging.getLogger("qnn_profile")


def _build_deploy_script(cfg: dict[str, Any]) -> str:
    """Build the device-side shell script to verify Genie deployment."""
    dev = cfg["device_root"]
    genie_config = cfg.get("genie_config", "genie_config.json")
    return f"""\
export LD_LIBRARY_PATH={dev}/lib:/vendor/lib64:/system/lib64
export ADSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
export CDSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
export CDSP1_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
{dev}/bin/genie-t2t-run --help >/dev/null || exit 20
test -f {dev}/model/{genie_config} || exit 21
"""


def _build_run_script(cfg: dict[str, Any], prompt_file: str) -> str:
    """Build the device-side shell script for Genie inference + profiling."""
    dev = cfg["device_root"]
    timeout = cfg.get("timeout_seconds", 240)
    genie_config = cfg.get("genie_config", "genie_config.json")

    return f"""\
cd {dev}/model || exit 10
export LD_LIBRARY_PATH={dev}/lib:/vendor/lib64:/system/lib64
export ADSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
export CDSP_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
export CDSP1_LIBRARY_PATH="/vendor/lib/rfsa/adsp;{dev}/dsp;{dev}/lib"
rm -f genie-profile.json
timeout {timeout} {dev}/bin/genie-t2t-run \\
  --config {genie_config} \\
  --prompt_file {prompt_file} \\
  --log verbose \\
  --profile genie-profile.json
"""


def _resolve_lib(name: str, sdk_root: Path, htp_arch: str, jni_libs: Path | None) -> Path | None:
    """Resolve a lib path: SDK aarch64-android first, then jniLibs fallback,
    then SDK hexagon dir (DSP-side libs like libQnnHtpV73Skel.so only exist there)."""
    android_lib = sdk_root / "lib" / "aarch64-android"
    p = android_lib / name
    if p.is_file():
        return p
    if jni_libs:
        p = jni_libs / name
        if p.is_file():
            return p
    p = sdk_root / "lib" / f"hexagon-v{htp_arch}" / "unsigned" / name
    if p.is_file():
        return p
    return None


def deploy(cfg: dict[str, Any]) -> None:
    """Deploy Genie runtime to the device."""
    serial = cfg["serial"]
    # Prefer qnn_sdk_root (level2/3 SDK) so genie-t2t-run/libGenie/QNN libs all
    # come from one SDK version — mixing libGenie from one QAIRT version with
    # libQnnSystem/libQnnHtp from another fails with "Unable to find a valid
    # system interface" at dialog creation.
    sdk_root = Path(cfg["qnn_sdk_root"]) if cfg.get("qnn_sdk_root") else Path(cfg["sdk_root"])
    model_local_dir = cfg.get("model_local_dir", "")
    dev = cfg["device_root"]
    htp_arch = cfg["htp_arch"]

    android_bin = sdk_root / "bin" / "aarch64-android"
    android_lib = sdk_root / "lib" / "aarch64-android"
    model_dir = Path(model_local_dir) if model_local_dir else None

    # Validate local files
    logger.info("[Level1-Deploy] Checking local SDK and model files...")
    if model_dir:
        if not model_dir.is_dir():
            raise FileNotFoundError(f"Model directory not found: {model_dir}")
        genie_cfg = model_dir / cfg.get("genie_config", "genie_config.json")
        if not genie_cfg.is_file():
            raise FileNotFoundError(f"Missing genie_config.json: {genie_cfg}")

    # Resolve libs: SDK first, jniLibs fallback. NEVER mix sibling SDKs.
    # Different projects may have different .so versions — all must match sdk_root.
    jni_libs = common.find_jni_libs_dir()
    if jni_libs:
        logger.info("[Level1-Deploy] Using jniLibs: %s", jni_libs)

    _r = lambda name: _resolve_lib(name, sdk_root, htp_arch, jni_libs)
    lib_htp = _r("libQnnHtp.so")
    lib_system = _r("libQnnSystem.so")
    lib_htp_v = _r(f"libQnnHtpV{htp_arch}.so")
    lib_htp_skel = _r(f"libQnnHtpV{htp_arch}Skel.so")
    lib_cpp = _r("libc++.so.1")
    lib_cppabi = _r("libc++abi.so.1")

    # Mandatory files
    mandatory = {
        "genie-t2t-run": android_bin / "genie-t2t-run",
        "libGenie.so": android_lib / "libGenie.so",
        "libQnnHtpNetRunExtensions.so": android_lib / "libQnnHtpNetRunExtensions.so",
        f"libQnnHtpV{htp_arch}Stub.so": android_lib / f"libQnnHtpV{htp_arch}Stub.so",
    }
    for name, path in mandatory.items():
        common.assert_local_file(path)

    # Optional libs (warn if missing)
    optional = {
        "libQnnHtp.so": lib_htp,
        "libQnnSystem.so": lib_system,
        f"libQnnHtpV{htp_arch}.so": lib_htp_v,
        f"libQnnHtpV{htp_arch}Skel.so": lib_htp_skel,
    }
    for name, path in optional.items():
        if path is None:
            logger.warning("[Level1-Deploy] %s not found (SDK or jniLibs)", name)

    # Check device
    logger.info("[Level1-Deploy] Checking ADB device...")
    common.assert_device(serial)

    # Recreate device directory
    logger.info("[Level1-Deploy] Recreating device directory...")
    common.adb_run(
        ["shell", f"rm -rf '{dev}' && mkdir -p '{dev}/bin' '{dev}/lib' '{dev}/dsp' '{dev}/model'"],
        serial=serial,
    )

    # Push model files (entire model directory including dsp/, context bins, tokenizer, etc.)
    logger.info("[Level1-Deploy] Pushing model files...")
    if model_dir:
        common.adb_run(["push", f"{model_dir}/.", f"{dev}/model/"], serial=serial)

    # Push runtime binaries to {dev}/bin/
    common.adb_run(["push", str(android_bin / "genie-t2t-run"), f"{dev}/bin/"], serial=serial)
    common.adb_run(["shell", f"chmod 755 '{dev}/bin/genie-t2t-run'"], serial=serial)

    # Push android libs to {dev}/lib/
    common.adb_run(["push", str(android_lib / "libGenie.so"), f"{dev}/lib/"], serial=serial)
    common.adb_run(["push", str(android_lib / "libQnnHtpNetRunExtensions.so"), f"{dev}/lib/"], serial=serial)
    common.adb_run(["push", str(android_lib / f"libQnnHtpV{htp_arch}Stub.so"), f"{dev}/lib/"], serial=serial)
    if lib_htp:
        common.adb_run(["push", str(lib_htp), f"{dev}/lib/"], serial=serial)
    if lib_system:
        common.adb_run(["push", str(lib_system), f"{dev}/lib/"], serial=serial)

    # Push DSP libs to {dev}/dsp/
    if lib_htp_v:
        common.adb_run(["push", str(lib_htp_v), f"{dev}/dsp/"], serial=serial)
    if lib_htp_skel:
        common.adb_run(["push", str(lib_htp_skel), f"{dev}/dsp/"], serial=serial)
    if lib_cpp:
        common.adb_run(["push", str(lib_cpp), f"{dev}/dsp/"], serial=serial)
        # Android-side QNN libs may also depend on libc++ at dlopen time
        common.adb_run(["push", str(lib_cpp), f"{dev}/lib/"], serial=serial)
    if lib_cppabi:
        common.adb_run(["push", str(lib_cppabi), f"{dev}/dsp/"], serial=serial)
        common.adb_run(["push", str(lib_cppabi), f"{dev}/lib/"], serial=serial)

    # Verify deployment
    logger.info("[Level1-Deploy] Verifying deployed runtime...")
    verify_script = _build_deploy_script(cfg)
    common.device_shell(verify_script, serial=serial)

    # App-compat layout: only needed when device_root is shared with the
    # Qwen3GenieDemo app (its JNI hardcodes flat top-level paths). Enable via
    # "app_compat": true in that model's config; skipped for other models.
    if cfg.get("app_compat"):
        logger.info("[Level1-Deploy] Creating app-compatible flat layout...")
        genie_config = cfg.get("genie_config", "genie_config.json")
        tokenizers = sorted(model_dir.glob("*tokenizer*.json")) if model_dir else []
        if not tokenizers:
            raise FileNotFoundError(f"No tokenizer json found in model dir: {model_dir}")
        flat_files = [
            tokenizers[0].name,
            "htp_backend_ext_config.json",
            genie_config,
            *cfg["context_binaries"],
        ]
        copies = "".join(f"cp '{dev}/model/{f}' '{dev}/' && " for f in flat_files)
        compat_script = (
            f"{copies}cp '{dev}/dsp/libQnnHtpV{htp_arch}Skel.so' '{dev}/' && "
            f"chmod -R a+rX '{dev}'"
        )
        common.device_shell_checked(compat_script, serial=serial)

    sdk_label = sdk_root.name
    common.adb_run(
        ["shell", f"printf '%s\\n' 'sdk={sdk_label} htp=v{htp_arch}' > '{dev}/deployment-info.txt'"],
        serial=serial,
    )
    logger.info("[Level1-Deploy] Deployment completed: %s", dev)


def build_prompt(cfg: dict[str, Any]) -> str:
    """Apply the Qwen role template when a system prompt is configured."""
    user_prompt = cfg.get("prompt", "Hello")
    system_prompt = cfg.get("system_prompt")
    if system_prompt is None:
        return user_prompt + "\n"
    return (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def run(cfg: dict[str, Any], output_dir: Path) -> dict[str, str]:
    """Run Level 1 Genie profiling and collect results.

    Returns a dict of output file paths.
    """
    serial = cfg["serial"]
    dev = cfg["device_root"]
    prompt = build_prompt(cfg)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Verify deployment
    logger.info("[Level1] Checking device and deployed environment...")
    common.assert_device(serial)
    genie_config = cfg.get("genie_config", "genie_config.json")
    common.adb_run(
        ["shell", f"test -x '{dev}/bin/genie-t2t-run' && test -f '{dev}/model/{genie_config}'"],
        serial=serial,
    )

    # Push prompt
    logger.info("[Level1] Updating the prompt...")
    logger.info("[Level1] 系统提示词：%s", cfg.get("system_prompt", "（未设置）"))
    logger.info("[Level1] 用户提示词：%s", cfg.get("prompt", "Hello"))
    prompt_bytes = prompt.encode("utf-8")
    (output_dir / "sample_prompt.txt").write_bytes(prompt_bytes)
    import base64
    prompt_b64 = base64.b64encode(prompt_bytes).decode("ascii")
    common.adb_run(
        ["shell", f"echo '{prompt_b64}' | base64 -d > '{dev}/model/sample_prompt.txt'"],
        serial=serial,
    )

    # Prepare output paths
    console_log = output_dir / "genie-console.log"
    logcat_file = output_dir / "genie-logcat.txt"
    profile_file = output_dir / "genie-profile.json"

    # Clear logcat
    common.adb_run(["logcat", "-c"], serial=serial, check=False)

    # Run inference (capture output even on failure for diagnostics)
    logger.info("[Level1] Starting inference and profile collection...")
    run_script = _build_run_script(cfg, "sample_prompt.txt")
    import base64
    normalized = run_script.replace("\r\n", "\n")
    b64 = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
    shell_cmd = f"echo '{b64}' | base64 -d | sh 2>&1"
    result = common.adb_run(
        ["shell", shell_cmd], serial=serial, check=False, capture=True,
    )
    output = result.stdout

    # Save console output (always, even on failure)
    console_log.write_text(output, encoding="utf-8")
    logger.info("[Level1] Console log saved: %s", console_log)

    if result.returncode != 0:
        # Log first few lines of error for quick diagnosis
        error_lines = output.strip().splitlines()[-10:] if output.strip() else ["(no output)"]
        logger.error("[Level1] Inference failed (exit %d):", result.returncode)
        for line in error_lines:
            logger.error("  %s", line)
        raise RuntimeError(
            f"genie-t2t-run failed (exit {result.returncode}). See: {console_log}"
        )

    # Collect logcat
    logger.info("[Level1] Collecting logcat and profile...")
    logcat_result = common.adb_run(["logcat", "-d"], serial=serial, check=False, capture=True)
    logcat_file.write_text(logcat_result.stdout, encoding="utf-8")

    # Pull profile JSON
    status = "Not generated; model initialization may have failed"
    if common.device_file_exists(f"{dev}/model/genie-profile.json", serial):
        common.adb_run(["pull", f"{dev}/model/genie-profile.json", str(profile_file)], serial=serial)
        status = str(profile_file)
        logger.info("[Level1] Profile JSON pulled: %s", profile_file)
    else:
        logger.warning("[Level1] genie-profile.json not found on device")

    results = {
        "console_log": str(console_log),
        "logcat": str(logcat_file),
        "profile_json": status,
    }
    logger.info("[Level1] Test finished. Profile: %s", status)
    return results
