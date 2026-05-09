.PHONY: help sync validate fetch fetch-roo fetch-tk fetch-ek fetch-logius \
	fetch-wikidata fetch-ar-rwt fetch-koop fetch-ori fetch-tooi fetch-kiesraad \
	fetch-abd diff build serve test format lint daily-update review-diff \
	parse-stcrt parse-organogram ingest ingest-commit

# Het Makefile is nu een dunne wrapper rond `uv run polder ...`. Alle echte
# logica zit in de `polder` CLI; zie `polder --help` of `docs/cli.md`.

help:
	@uv run polder --help

sync:
	uv sync

validate:
	uv run polder validate

fetch:
	uv run polder fetch all

fetch-roo:
	uv run polder fetch roo

fetch-tk:
	uv run polder fetch tk

fetch-ek:
	uv run polder fetch ek

fetch-logius:
	uv run polder fetch logius

fetch-wikidata:
	uv run polder fetch wikidata

fetch-ar-rwt:
	uv run polder fetch ar-rwt

fetch-koop:
	uv run polder fetch koop

fetch-ori:
	uv run polder fetch ori

fetch-tooi:
	uv run polder fetch tooi

fetch-kiesraad:
	uv run polder fetch kiesraad

fetch-abd:
	uv run polder fetch abd

diff:
	uv run polder diff

build:
	uv run polder build all

serve:
	uv run polder serve

daily-update:
	uv run polder daily-update

review-diff:
	@if [ -z "$(DIFF)" ]; then echo "usage: make review-diff DIFF=path/to/diff.json"; exit 2; fi
	uv run polder skill review-diff $(DIFF)

parse-stcrt:
	@if [ -z "$(XML)" ]; then echo "usage: make parse-stcrt XML=path/to/kb.xml"; exit 2; fi
	uv run polder skill parse-staatscourant $(XML)

parse-organogram:
	@if [ -z "$(PDF)" ] || [ -z "$(MIN)" ]; then echo "usage: make parse-organogram PDF=path MIN=min-bzk"; exit 2; fi
	uv run polder skill parse-organogram $(PDF) $(MIN)

ingest:
	uv run polder ingest

ingest-commit:
	uv run polder ingest --commit --push

test:
	uv run pytest

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests
