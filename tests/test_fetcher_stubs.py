"""Tests voor de fetcher-stubs.

Verifieert dat elke stub-module:
- importeerbaar is zonder netwerkcalls te triggeren bij import,
- een ``main()`` callable heeft die exit code 1 levert,
- een module-docstring heeft die de bron en het endpoint vermeldt.
"""

from __future__ import annotations

import importlib

import pytest

STUB_MODULES: list[str] = []


@pytest.mark.parametrize("name", STUB_MODULES)
def test_module_imports(name: str) -> None:
    module = importlib.import_module(f"polder.fetchers.{name}")
    assert callable(module.main)
    assert module.__doc__, f"{name} mist module-docstring"


@pytest.mark.parametrize("name", STUB_MODULES)
def test_main_returns_exit_code_one(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    module = importlib.import_module(f"polder.fetchers.{name}")
    rc = module.main()
    assert rc == 1, f"{name}.main() moet 1 teruggeven, kreeg {rc!r}"
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert (
        "not yet implemented" in combined.lower()
    ), f"{name}.main() moet 'not yet implemented' melden, kreeg: {combined!r}"


@pytest.mark.parametrize("name", STUB_MODULES)
def test_docstring_mentions_bron_en_endpoint(name: str) -> None:
    module = importlib.import_module(f"polder.fetchers.{name}")
    doc = module.__doc__ or ""
    assert "Bron:" in doc, f"{name} docstring mist 'Bron:'-regel"
    assert "Endpoint" in doc, f"{name} docstring mist 'Endpoint'-regel"


@pytest.mark.parametrize("name", STUB_MODULES)
def test_module_has_dunder_all(name: str) -> None:
    module = importlib.import_module(f"polder.fetchers.{name}")
    assert hasattr(module, "__all__"), f"{name} mist __all__"
    assert "main" in module.__all__, f"{name}.__all__ bevat 'main' niet"
