"""Diagnostic: dump exactly what Typer/Rich renders for `--help` in this env.

The help tests pass everywhere locally (Python 3.12/3.13, clean env,
CI=true, full suite) but fail on the GitHub runner with only an empty
Rich box. This script prints the raw rendering plus the environment
signals Rich uses to pick a width/terminal mode, so a CI run shows the
actual cause instead of pytest's truncated assertion repr.
"""

import os
import shutil
import sys


def main() -> None:
    print("=== environment ===")
    for key in ("CI", "GITHUB_ACTIONS", "TERM", "COLUMNS", "LINES", "NO_COLOR", "FORCE_COLOR"):
        print(f"{key}={os.environ.get(key)!r}")
    print(f"python={sys.version}")
    print(f"stdout.isatty={sys.stdout.isatty()}")
    print(f"terminal_size={shutil.get_terminal_size()}")

    from importlib.metadata import version

    for pkg in ("rich", "typer", "click"):
        print(f"{pkg}={version(pkg)}")

    print("\n=== rich Console detection ===")
    from rich.console import Console

    c = Console()
    print(f"console.width={c.width} is_terminal={c.is_terminal} legacy={c.legacy_windows}")
    print(f"color_system={c.color_system!r} _environ_keys_seen")

    print("\n=== CliRunner ingest --help ===")
    from typer.testing import CliRunner

    from polder.cli.main import app

    result = CliRunner().invoke(app, ["ingest", "--help"])
    out = result.stdout
    print(f"exit_code={result.exit_code}")
    print(f"len(stdout)={len(out)}")
    print(f"'--commit' in stdout: {'--commit' in out}")
    print(f"'Usage' in stdout: {'Usage' in out}")
    print("--- repr(stdout) first 600 chars ---")
    print(repr(out[:600]))
    print("--- repr(stdout) last 300 chars ---")
    print(repr(out[-300:]))


if __name__ == "__main__":
    main()
