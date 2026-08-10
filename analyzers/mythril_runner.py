"""
Mythril wrapper. Unlike Slither, Mythril has no project mode - it uses
symbolic execution and analyzes ONE entry contract at a time. For a
whole folder, call this once per .sol file (see normalizer.run_mythril_project).
"""
import subprocess
import json
import logging

logger = logging.getLogger(__name__)


def run_mythril(target_path: str, timeout: int = 120) -> list[dict]:
    cmd = ["myth", "analyze", target_path, "-o", "json", "--execution-timeout", str(timeout)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(
            "Mythril produced no valid JSON for %s. stderr: %s",
            target_path, result.stderr[:2000],
        )
        return []
    return data.get("issues", [])
