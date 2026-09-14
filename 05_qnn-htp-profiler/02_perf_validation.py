#!/usr/bin/env python3
"""02 性能验证：只做三级 profiling + 分析报告，不重新推送资源。

前提：已执行过 01_resource_push.py（设备上已有运行时、模型和输入）。
本脚本不再 deploy，反复执行也很快（实测约 30 秒）。

包含：
  Level 1 - Genie 端到端 profiling（genie-t2t-run）
  Level 2 - QNN detailed profiling（qnn-net-run，4 个 stage x part 组合）
  Level 3 - QNN optrace profiling（qnn-net-run，4 个组合）
  analyze - 汇总生成性能报告（profile-results/reports/）

用法：
    python 02_perf_validation.py [--config profile-config.json] [-v]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts import profile_common as common
import run_profile

logger = logging.getLogger("qnn_profile")


def main() -> int:
    parser = argparse.ArgumentParser(description="02 性能验证（Level1/2/3 + 报告，不含资源推送）")
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

    failed: list[str] = []
    total_start = time.monotonic()

    enabled_levels = cfg.get("levels") or ["level1", "level2", "level3"]
    for name, action in [
        ("level1", run_profile.action_level1),
        ("level2", run_profile.action_level2),
        ("level3", run_profile.action_level3),
    ]:
        if name not in enabled_levels:
            logger.info("跳过 %s（未列入 config 'levels'）", name)
            continue
        step_start = time.monotonic()
        try:
            action(cfg, output_dir)
            logger.info("[Timing] %s 耗时 %.1f 秒", name, time.monotonic() - step_start)
        except Exception as e:
            logger.error("%s failed: %s", name, e)
            failed.append(name)

    if failed:
        logger.warning("以下级别失败，跳过报告生成：%s", ", ".join(failed))
        return 1

    report = run_profile.action_analyze(cfg, output_dir)
    logger.info("=" * 60)
    logger.info("性能验证完成，总耗时 %.1f 秒", time.monotonic() - total_start)
    logger.info("Report: %s", report)
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
