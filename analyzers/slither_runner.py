"""
Slither wrapper. Slither is project-aware: target_path can be a single
.sol file OR a project directory (Foundry/Hardhat root) - it resolves
imports and remappings automatically either way, which is what makes
whole-repo analysis a single call instead of a per-file loop.
"""
import subprocess
import json
import logging

logger = logging.getLogger(__name__)


def run_slither(target_path: str) -> list[dict]:
    cmd = ["slither", target_path, "--json", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(
            "Slither produced no valid JSON for %s. stderr: %s",
            target_path, result.stderr[:2000],
        )
        return []
    return data.get("results", {}).get("detectors", [])
