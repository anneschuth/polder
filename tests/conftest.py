"""Shared pytest fixtures and test-session setup.

Typer/Rich render `--help` into a box whose width follows the terminal.
On a developer machine that box is wide; in CI there is no TTY and Rich
falls back to an 80-column default, which word-wraps long option names
like ``--dry-run`` across lines and breaks the substring assertions in
the help tests.

Rich reads ``COLUMNS`` from the environment when it constructs a
``Console``. Set it at import time (before any test module imports the
Typer app and before the first ``CliRunner`` invocation) so the help
output is deterministic regardless of where the suite runs.
"""

import os

os.environ.setdefault("COLUMNS", "200")
os.environ.setdefault("LINES", "50")
# A developer shell often already exports a narrow COLUMNS; override it
# so local runs match CI instead of masking the wrapping bug.
os.environ["COLUMNS"] = "200"
os.environ["LINES"] = "50"
