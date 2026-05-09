"""`polder daily-update` command.

Wrapper rond `scripts/daily_update_local.sh`. Spiegel van de daily-update
GitHub Actions workflow, zonder commits of PR-aanmaak.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def daily_update() -> None:
    """Run de daily-update pipeline lokaal: fetchers + validate + diff + review."""
    bash = shutil.which("bash")
    if not bash:
        raise typer.BadParameter("bash niet gevonden in PATH.")
    script = _repo_root() / "scripts" / "daily_update_local.sh"
    if not script.exists():
        raise typer.BadParameter(f"script niet gevonden: {script}")
    typer.echo(f"+ bash {script}", err=True)
    proc = subprocess.run([bash, str(script)], check=False)
    raise typer.Exit(code=proc.returncode)
