"""Shared pytest fixtures and test-session setup.

Typer renders ``--help`` through a Rich ``Console`` whose width comes
from ``typer.rich_utils.MAX_WIDTH`` / ``_TERMINAL_WIDTH``. Both default
to ``None``, so Rich auto-detects: on a developer machine the real
terminal width leaks through and the help fits on one line, but under
``CliRunner`` in CI (no TTY) Rich falls back to 80 columns. At 80 cols
long options like ``--commit`` and ``--parallel`` wrap across lines, so
they no longer appear as a contiguous substring and the help-text
assertions fail.

Pinning ``MAX_WIDTH`` directly is the knob ``_get_rich_console`` reads;
unlike a ``COLUMNS`` env var it does not depend on import/fixture
ordering. Setting it at import time covers every test module that
imports the Typer app.
"""

import typer.rich_utils as _rich_utils

_rich_utils.MAX_WIDTH = 200
_rich_utils._TERMINAL_WIDTH = 200
