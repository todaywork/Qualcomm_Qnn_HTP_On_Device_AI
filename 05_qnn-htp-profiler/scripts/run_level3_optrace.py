"""Level 3: QNN optrace profiling.

Thin wrapper around run_level2_detailed with mode="optrace".
Uses --profiling_level detailed --profiling_option optrace.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import run_level2_detailed

logger = logging.getLogger("qnn_profile")


def run(cfg: dict[str, Any], output_dir: Path) -> dict[str, dict[str, str]]:
    """Run Level 3 optrace profiling.

    Delegates to run_level2_detailed.run() with mode="optrace".
    """
    logger.info("[Level3] Starting optrace profiling...")
    return run_level2_detailed.run(cfg, output_dir, mode="optrace")
