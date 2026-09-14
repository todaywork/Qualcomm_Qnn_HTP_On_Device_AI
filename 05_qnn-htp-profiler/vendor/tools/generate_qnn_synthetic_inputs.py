#!/usr/bin/env python3
"""Generate native QNN smoke-test inputs from context-binary metadata.

The generated tensors preserve graph names, shapes, native data types, and
scale/offset encodings. They are intended for profiling only, not accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any


TYPE_INFO: dict[str, tuple[str, int, int | float, int | float]] = {
    "QNN_DATATYPE_INT_8": ("b", 1, -128, 127),
    "QNN_DATATYPE_UINT_8": ("B", 1, 0, 255),
    "QNN_DATATYPE_SFIXED_POINT_8": ("b", 1, -128, 127),
    "QNN_DATATYPE_UFIXED_POINT_8": ("B", 1, 0, 255),
    "QNN_DATATYPE_INT_16": ("h", 2, -32768, 32767),
    "QNN_DATATYPE_UINT_16": ("H", 2, 0, 65535),
    "QNN_DATATYPE_SFIXED_POINT_16": ("h", 2, -32768, 32767),
    "QNN_DATATYPE_UFIXED_POINT_16": ("H", 2, 0, 65535),
    "QNN_DATATYPE_INT_32": ("i", 4, -(2**31), 2**31 - 1),
    "QNN_DATATYPE_UINT_32": ("I", 4, 0, 2**32 - 1),
    "QNN_DATATYPE_FLOAT_16": ("e", 2, -65504.0, 65504.0),
    "QNN_DATATYPE_FLOAT_32": ("f", 4, -3.4e38, 3.4e38),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("profile-results/context_info/context_info"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("profile-results/qnn_inputs"),
    )
    parser.add_argument(
        "--device-root",
        default="/data/local/tmp/genie_qwen25_quality/qnn_inputs",
    )
    parser.add_argument("--token-id", type=int, default=151643)
    return parser.parse_args()


def product(values: list[int]) -> int:
    result = 1
    for value in values:
        result *= value
    return result


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def desired_real_value(name: str) -> float:
    if name == "position_ids_cos":
        return 1.0
    return 0.0


def native_value(tensor: dict[str, Any], token_id: int) -> int | float:
    name = tensor["name"]
    data_type = tensor["dataType"]
    fmt, _, minimum, maximum = TYPE_INFO[data_type]

    if name == "input_ids":
        return max(min(token_id, int(maximum)), int(minimum))

    real_value = desired_real_value(name)
    quant = tensor.get("quantizeParams", {})
    scale_offset = quant.get("scaleOffset")
    if scale_offset and fmt not in {"e", "f"}:
        scale = float(scale_offset["scale"])
        offset = int(scale_offset["offset"])
        if scale == 0:
            raise ValueError(f"Tensor {name} has zero quantization scale")
        value: int | float = round(real_value / scale) - offset
    else:
        value = real_value

    if fmt in {"e", "f"}:
        return float(max(min(value, maximum), minimum))
    return int(max(min(value, int(maximum)), int(minimum)))


def write_constant_tensor(
    path: Path, data_type: str, count: int, value: int | float
) -> int:
    fmt, item_size, _, _ = TYPE_INFO[data_type]
    packed = struct.pack("<" + fmt, value)
    chunk_items = min(count, max(1, (1024 * 1024) // item_size))
    chunk = packed * chunk_items
    remaining = count
    with path.open("wb") as stream:
        while remaining:
            current = min(remaining, chunk_items)
            stream.write(chunk if current == chunk_items else packed * current)
            remaining -= current
    return count * item_size


def load_graphs(metadata_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for metadata_path in sorted(metadata_dir.glob("graph*.json")):
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
        for graph in document["info"]["graphs"]:
            result.append((metadata_path.name, graph["info"]))
    if not result:
        raise FileNotFoundError(f"No graph*.json found under {metadata_dir}")
    return result


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "purpose": "QNN profiling smoke test only; not for accuracy validation",
        "token_id": args.token_id,
        "device_root": args.device_root,
        "graphs": [],
    }

    for metadata_file, graph in load_graphs(args.metadata_dir):
        graph_name = graph["graphName"]
        graph_dir = args.output_dir / graph_name
        graph_dir.mkdir(parents=True, exist_ok=True)
        input_entries: list[str] = []
        graph_manifest: dict[str, Any] = {
            "name": graph_name,
            "metadata_file": metadata_file,
            "inputs": [],
        }

        for wrapped in graph["graphInputs"]:
            tensor = wrapped["info"]
            name = tensor["name"]
            data_type = tensor["dataType"]
            if data_type not in TYPE_INFO:
                raise ValueError(f"Unsupported data type {data_type} for {name}")
            dimensions = [int(value) for value in tensor["dimensions"]]
            count = product(dimensions)
            value = native_value(tensor, args.token_id)
            raw_name = safe_name(name) + ".raw"
            raw_path = graph_dir / raw_name
            byte_size = write_constant_tensor(raw_path, data_type, count, value)
            device_path = f"{args.device_root}/{graph_name}/{raw_name}"
            input_entries.append(f"{name}:={device_path}")
            graph_manifest["inputs"].append(
                {
                    "name": name,
                    "data_type": data_type,
                    "dimensions": dimensions,
                    "element_count": count,
                    "native_fill_value": value,
                    "byte_size": byte_size,
                    "file": raw_name,
                }
            )

        (graph_dir / "input_list.txt").write_text(
            " ".join(input_entries) + "\n", encoding="utf-8", newline="\n"
        )
        manifest["graphs"].append(graph_manifest)
        total = sum(item["byte_size"] for item in graph_manifest["inputs"])
        print(f"{graph_name}: {len(graph_manifest['inputs'])} inputs, {total} bytes")

    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
