#!/usr/bin/env python3
"""01 资源推送：把 profiling 所需资源一次性部署到设备。

包含三步（都是重活，只需在环境变化后执行一次）：
  1. deploy          - 推送 Genie 运行时 + 模型（~600MB）+ QNN 工具链到设备
  2. export-context  - 从设备导出 context binary 图元数据
  3. generate-inputs - 生成并推送 Level2/3 合成输入（~235MB）

完成后用 02_perf_validation.py 做性能验证，无需重复推送。

用法：
    python 01_resource_push.py [--config profile-config.json] [-v]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts import profile_common as common
import run_profile

logger = logging.getLogger("qnn_profile")


def main() -> int:
    parser = argparse.ArgumentParser(description="01 资源推送（deploy + 元数据 + 输入）")
    parser.add_argument("--config", "-c", type=Path, default=None,
                        help="模型配置路径（默认自动发现 models/*/profile-config.json）")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    config_path = args.config or common.find_default_config(Path(__file__).resolve().parent)
    cfg = common.load_config(config_path)
    output_dir = Path(cfg.get("output_dir") or config_path.resolve().parent)
    output_dir.mkdir(parents=True, exist_ok=True)
    common.setup_logging(log_dir=output_dir, verbose=args.verbose)

    logger.info("Project: %s | Model: %s", cfg["project"], cfg["model_label"])
    common.assert_device(cfg["serial"])

    logger.info("=" * 60)
    logger.info("Step 1/3: Deploy Genie runtime + QNN tools")
    logger.info("=" * 60)
    run_profile.action_deploy(cfg)

    run_profile.action_export_context(cfg, output_dir)
    run_profile.action_generate_inputs(cfg, output_dir)

    logger.info("=" * 60)
    logger.info("资源推送完成，可以运行 02_perf_validation.py 做性能验证")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
