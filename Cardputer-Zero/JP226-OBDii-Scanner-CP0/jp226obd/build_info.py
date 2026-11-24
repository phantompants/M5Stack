"""Build identity shown on the DIAG page."""

import datetime
import json
import os
import subprocess
from typing import Dict


def read_build_info() -> Dict[str, str]:
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stamp_path = os.path.join(app_root, "build_stamp.json")
    try:
        with open(stamp_path, "r", encoding="utf-8") as handle:
            stamp = json.load(handle)
        if isinstance(stamp, dict):
            git_hash = stamp.get("git_hash")
            build_date = stamp.get("build_date")
            if isinstance(git_hash, str) and isinstance(build_date, str):
                return {"git_hash": git_hash, "build_date": build_date}
    except (OSError, ValueError):
        pass

    project_root = os.path.dirname(app_root)
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=project_root,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=project_root, stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
        if dirty:
            git_hash += "+"
    except (OSError, subprocess.SubprocessError):
        git_hash = "dev"
    return {
        "git_hash": git_hash or "dev",
        "build_date": datetime.date.today().isoformat(),
    }
