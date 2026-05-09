.PHONY: help validate fetch-roo diff build test format lint sync

help:
	@echo "polder make targets:"
	@echo "  sync       install/update dependencies via uv"
	@echo "  validate   run JSON Schema validation on data/"
	@echo "  fetch-roo  fetch ROO exportOO.xml and write YAML"
	@echo "  diff       compare _cache/ with data/, write diff.json"
	@echo "  build      build SQLite + Frictionless data package"
	@echo "  test       run pytest"
	@echo "  format     run ruff format"
	@echo "  lint       run ruff check"

sync:
	uv sync

validate:
	uv run polder-validate

fetch-roo:
	uv run polder-fetch-roo

diff:
	uv run polder-diff

build:
	uv run polder-build

test:
	uv run pytest

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests
