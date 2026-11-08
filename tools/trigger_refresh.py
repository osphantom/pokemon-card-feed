"""Ask GitHub to refresh the feed now. Windows Task Scheduler runs this every 15 minutes.

GitHub's own schedule can run hours late, while a manual start runs right away.
This holds no secrets. It uses the GitHub CLI's existing sign-in.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

REPO = "osphantom/pokemon-card-feed"
DEFAULT_GH = r"C:\Program Files\GitHub CLI\gh.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def command(gh: str) -> list[str]:
    return [gh, "workflow", "run", "feed.yml", "--repo", REPO]


def main() -> int:
    gh = shutil.which("gh") or DEFAULT_GH
    try:
        return subprocess.run(command(gh), capture_output=True, timeout=60, creationflags=NO_WINDOW).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


if __name__ == "__main__":
    sys.exit(main())
