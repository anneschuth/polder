"""Shared pytest fixtures.

Typer/Rich render `--help` into a box whose width follows the terminal.
On a developer machine that box is wide; in CI there is no TTY and the
width collapses to 80 columns, which word-wraps long option names like
``--dry-run`` across lines and breaks substring assertions in the help
tests. Pin a generous width for the whole session so help output is
deterministic regardless of where the suite runs.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _stable_terminal_width() -> None:
    import os

    os.environ["COLUMNS"] = "200"
    os.environ["LINES"] = "50"
