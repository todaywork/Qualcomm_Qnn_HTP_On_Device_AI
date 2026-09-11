"""Common utilities for QNN profiling scripts.

Provides shared functions: ADB wrappers, config loading, logging,
device/local file assertions, and shell script encoding.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("qnn_profile")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def find_default_config(toolkit_root: str | Path) -> Path:
    """Auto-discover the model config for this toolkit.

    Search order:
      1. <toolkit_root>/profile-config.json          (legacy single-model layout)
      2. <toolkit_root>/models/*/profile-config.json (multi-model layout;
         requires exactly one model, otherwise the user must pass --config)
    """
    root = Path(toolkit_root)
    legacy = root / "profile-config.json"
    if legacy.is_file():
        return legacy

    configs = sorted((root / "models").glob("*/profile-config.json"))
    if len(configs) == 1:
        logger.info("Auto-detected model config: %s", configs[0])
        return configs[0]
    if not configs:
        raise FileNotFoundError(
            f"No profile-config.json found under {root} or {root / 'models'}/*/."
        )
    names = ", ".join(c.parent.name for c in configs)
    raise RuntimeError(
        f"Multiple model configs found ({names}). Please pass --config explicitly."
    )


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a profile-config.json file."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    required_fields = [
        "project", "model_label", "sdk_root", "htp_arch",
        "device_root", "device_qnn_root", "context_binaries",
        "context_length", "vocab_size", "num_layers", "num_heads",
        "kv_dim", "hidden_size",
    ]
    missing = [f for f in required_fields if not config.get(f)]
    if missing:
        raise ValueError(f"Config missing required fields: {', '.join(missing)}")

    if len(config["context_binaries"]) < 1:
        raise ValueError("Config must have at least one context_binary")

    # Resolve relative paths against the config file's directory so the
    # whole toolkit tree stays relocatable.
    base = config_path.resolve().parent
    for key in ("sdk_root", "qnn_sdk_root", "model_local_dir", "output_dir", "tools_dir"):
        value = config.get(key)
        if value and not Path(value).is_absolute():
            config[key] = str((base / value).resolve())

    # Auto-detect device serial if not specified
    serial = config.get("serial", "").strip()
    if not serial:
        serial = detect_device()
        config["serial"] = serial
        logger.info("Auto-detected device serial: %s", serial)

    return config


# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------

def detect_device() -> str:
    """Auto-detect the serial of the first connected ADB device.

    Returns the device serial number.
    Raises RuntimeError if no device or multiple devices are found.
    """
    result = adb_run(["devices", "-l"], check=False, capture=True)
    if result.returncode != 0:
        raise RuntimeError("ADB command failed. Is ADB installed and in PATH?")

    lines = result.stdout.strip().splitlines()
    # Skip the "List of devices attached" header
    devices = []
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])

    if not devices:
        raise RuntimeError(
            "No ADB device connected.\n"
            "  Please check: 1) USB connected 2) USB debugging enabled 3) Authorized on device"
        )
    if len(devices) > 1:
        raise RuntimeError(
            f"Multiple ADB devices found: {', '.join(devices)}\n"
            f"  Please specify 'serial' in config file, or disconnect extra devices."
        )
    return devices[0]


def adb_run(args: list[str], serial: str = "", check: bool = True,
            capture: bool = False) -> subprocess.CompletedProcess:
    """Run an ADB command with optional serial targeting."""
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args

    logger.debug("ADB: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if capture else ""
        raise RuntimeError(
            f"ADB command failed (exit {result.returncode}): {' '.join(cmd)}\n{stderr}"
        )
    return result


def assert_device(serial: str = "") -> None:
    """Verify the target ADB device is connected and authorized."""
    result = adb_run(["get-state"], serial=serial, check=False, capture=True)
    state = result.stdout.strip()
    if state != "device":
        # List available devices for diagnosis
        devices_result = adb_run(["devices", "-l"], check=False, capture=True)
        devices_output = devices_result.stdout.strip() if devices_result.returncode == 0 else "(unable to query)"
        raise RuntimeError(
            f"ADB device unavailable (state: '{state}').\n"
            f"  Requested serial: {serial or '(default)'}\n"
            f"  Connected devices:\n{devices_output}\n"
            f"  Please check: 1) USB connected 2) USB debugging enabled 3) Authorized on device"
        )


def device_shell(script: str, serial: str = "", merge_stderr: bool = True) -> str:
    """Run a shell script on the device via base64 encoding."""
    normalized = script.replace("\r\n", "\n")
    b64 = base64.b64encode(normalized.encode("utf-8")).decode("ascii")

    shell_cmd = f"echo '{b64}' | base64 -d | sh"
    if merge_stderr:
        shell_cmd += " 2>&1"

    args = ["shell", shell_cmd]
    result = adb_run(args, serial=serial, check=True, capture=True)
    return result.stdout


def device_shell_checked(script: str, serial: str = "") -> None:
    """Run a shell script on the device, raise on failure."""
    normalized = script.replace("\r\n", "\n")
    b64 = base64.b64encode(normalized.encode("utf-8")).decode("ascii")
    adb_run(["shell", f"echo '{b64}' | base64 -d | sh"], serial=serial, check=True)


def device_mkdir(path: str, serial: str = "") -> None:
    """Create a directory on the Android device."""
    adb_run(["shell", f"mkdir -p '{path}'"], serial=serial, check=True)


def device_file_exists(path: str, serial: str = "") -> bool:
    """Check if a file exists on the device."""
    result = adb_run(["shell", f"test -f '{path}'"], serial=serial, check=False)
    return result.returncode == 0


def device_executable_exists(path: str, serial: str = "") -> bool:
    """Check if an executable exists on the device."""
    result = adb_run(["shell", f"test -x '{path}'"], serial=serial, check=False)
    return result.returncode == 0


def get_device_property(prop: str, serial: str = "") -> str:
    """Get an Android system property."""
    result = adb_run(["shell", f"getprop '{prop}'"], serial=serial,
                     check=False, capture=True)
    return result.stdout.strip()


def get_device_info(serial: str = "") -> dict[str, str]:
    """Collect device model, ABI, and SDK level for reports."""
    return {
        "model": get_device_property("ro.product.model", serial),
        "abi": get_device_property("ro.product.cpu.abi", serial),
        "sdk": get_device_property("ro.build.version.sdk", serial),
        "brand": get_device_property("ro.product.brand", serial),
        "hardware": get_device_property("ro.board.platform", serial),
    }


# ---------------------------------------------------------------------------
# Local file assertions
# ---------------------------------------------------------------------------

def find_sibling_sdk_with_htp(sdk_root: str | Path) -> Path | None:
    """Find a sibling SDK version that has libQnnHtp.so in aarch64-android/.

    SDK 2.45 is known to be missing libQnnHtp.so for Android. This function
    searches sibling SDK directories under the same parent (QAIRT/) for the
    newest version that includes the library.

    Returns the path to the sibling SDK root, or None if not found.
    """
    sdk = Path(sdk_root)
    qairt_parent = sdk.parent  # e.g. E:\QualComm\AIStack\QAIRT
    if not qairt_parent or not qairt_parent.is_dir():
        return None

    # Collect sibling SDK dirs that have libQnnHtp.so in aarch64-android/
    candidates = []
    for d in qairt_parent.iterdir():
        if d.is_dir() and d != sdk:
            htp_so = d / "lib" / "aarch64-android" / "libQnnHtp.so"
            if htp_so.is_file():
                candidates.append(d)

    if not candidates:
        return None

    # Sort by name (version string) descending — pick newest
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


def resolve_lib_path(
    name: str,
    sdk_root: str | Path,
    htp_arch: str,
    model_dir: str | Path | None = None,
    jni_libs_dir: str | Path | None = None,
    sibling_sdk: str | Path | None = None,
    skip_jni_libs: bool = False,
) -> Path | None:
    """Resolve a 64-bit Android library path.

    Search order:
      1. jni_libs_dir/               (project jniLibs — correct 64-bit Android)
      2. sdk/lib/aarch64-android/    (SDK Android libs)
      3. sibling_sdk/lib/aarch64-android/ (sibling SDK with missing libs filled in)
      4. sdk/lib/hexagon-vNN/unsigned/ (SDK DSP libs — 32-bit, last resort)
      5. model_dir/dsp/              (model DSP libs — 32-bit, last resort)

    NOTE: model dsp/ and SDK hexagon dirs contain 32-bit Hexagon DSP libs
    which are NOT suitable for 64-bit Android processes. They are searched
    last to avoid accidentally picking up wrong-architecture libraries.

    Args:
        skip_jni_libs: If True, skip jni_libs_dir in the search. Use this for
            QNN core libs (libQnnHtp.so, libQnnSystem.so) that must match the
            SDK tool version — jniLibs may contain an older incompatible version.

    Returns the first matching path, or None if not found.
    """
    sdk = Path(sdk_root)
    candidates = []
    if jni_libs_dir and not skip_jni_libs:
        candidates.append(Path(jni_libs_dir) / name)
    candidates.append(sdk / "lib" / "aarch64-android" / name)
    # Sibling SDK fallback (e.g. SDK 2.45 missing libQnnHtp.so → use 2.46)
    if sibling_sdk:
        candidates.append(Path(sibling_sdk) / "lib" / "aarch64-android" / name)
    # DSP dirs searched LAST (32-bit, not suitable for 64-bit Android)
    candidates.append(sdk / "lib" / f"hexagon-v{htp_arch}" / "unsigned" / name)
    if model_dir:
        candidates.append(Path(model_dir) / "dsp" / name)
    for p in candidates:
        if p.is_file():
            return p
    return None


def find_jni_libs_dir(toolkit_root: str | Path | None = None) -> Path | None:
    """Auto-detect the project's jniLibs/arm64-v8a/ directory.

    Searches relative to the toolkit root (profile-results/ parent).
    """
    if toolkit_root is None:
        toolkit_root = Path(__file__).resolve().parent.parent
    else:
        toolkit_root = Path(toolkit_root)

    candidates = [
        toolkit_root / "genie-inference" / "src" / "main" / "jniLibs" / "arm64-v8a",
        toolkit_root.parent / "genie-inference" / "src" / "main" / "jniLibs" / "arm64-v8a",
    ]
    for d in candidates:
        if d.is_dir():
            return d
    return None


def assert_local_file(path: str | Path) -> None:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Local file not found: {p}")


def assert_local_dir(path: str | Path) -> None:
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"Local directory not found: {p}")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str | Path | None = None, verbose: bool = False) -> None:
    """Configure structured logging for the profiling pipeline."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "[%(asctime)s] %(levelname)-7s %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        fh = logging.FileHandler(log_path / f"profile-{ts}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        handlers.append(fh)

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_number(value: int | float | None) -> str:
    """Format a number with thousand separators."""
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def fmt_ms(value: float | None) -> str:
    """Format a millisecond value with 3 decimal places."""
    if value is None:
        return "N/A"
    return f"{value:.3f}"


def fmt_pct(value: float | None) -> str:
    """Format a percentage value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def timestamp_dir_name() -> str:
    """Generate a timestamp string suitable for directory names."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")
