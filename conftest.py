"""Root conftest: runs before any test module imports the Typer app.

Typer fixes its help-rendering mode on import of ``typer.rich_utils``:

    _TERMINAL_WIDTH = getenv("TERMINAL_WIDTH")
    MAX_WIDTH = int(_TERMINAL_WIDTH) if _TERMINAL_WIDTH else None
    ...
    if getenv("GITHUB_ACTIONS") or getenv("FORCE_COLOR") or getenv("PY_COLORS"):
        FORCE_TERMINAL = True

On GitHub Actions ``GITHUB_ACTIONS`` is set, so Typer forces terminal
mode. Rich then injects ANSI color codes *into* option names, e.g.
``--\x1b[36mmodel\x1b[0m``. The help tests assert on the raw
``result.stdout`` without stripping ANSI, so the literal substring
``--model`` is no longer present and the assertion fails. Locally
``GITHUB_ACTIONS`` is unset, Typer does not force terminal mode, no
color codes are emitted, and the substring is intact, which is why this
only ever failed in CI.

Fix: drop ``GITHUB_ACTIONS`` (and friends) from the environment and set
``NO_COLOR`` before ``typer.rich_utils`` is imported, so help output is
plain text. ``TERMINAL_WIDTH`` keeps the box wide so nothing wraps
either. A root conftest is imported before the test modules, hence
before the Typer app, so the env is clean in time.
"""

import os

for _var in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_var, None)

os.environ["NO_COLOR"] = "1"
os.environ["TERMINAL_WIDTH"] = "200"
