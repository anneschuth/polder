"""Tests voor `polder.fetchers.wikidata_sparql`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from polder.fetchers import wikidata_sparql as ws

# ---------------------------------------------------------------------------
# Fixtures: realistische SPARQL JSON-responses
# ---------------------------------------------------------------------------


ORG_RESPONSE = {
    "head": {"vars": ["item", "itemLabel", "abbr", "oin"]},
    "results": {
        "bindings": [
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1727053"},
                "itemLabel": {
                    "type": "literal",
                    "xml:lang": "nl",
                    "value": "Ministerie van Binnenlandse Zaken en Koninkrijksrelaties",
                },
                "abbr": {"type": "literal", "value": "BZK"},
                "oin": {"type": "literal", "value": "00000001003214345000"},
            },
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q123456"},
                "itemLabel": {
                    "type": "literal",
                    "xml:lang": "nl",
                    "value": "Ministerie van Financiën",
                },
                "abbr": {"type": "literal", "value": "Fin"},
            },
            {
                # Geen Q-id: moet gefilterd worden.
                "item": {"type": "literal", "value": "garbage"},
                "itemLabel": {"type": "literal", "value": "Iets raars"},
            },
        ]
    },
}


GEMEENTE_RESPONSE = {
    "head": {"vars": ["item", "itemLabel", "abbr", "oin"]},
    "results": {
        "bindings": [
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q727"},
                "itemLabel": {"type": "literal", "value": "Amsterdam"},
            },
            {
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q39297"},
                "itemLabel": {"type": "literal", "value": "Utrecht"},
            },
        ]
    },
}


PERSON_RESPONSE = {
    "head": {"vars": ["person", "personLabel", "tkid", "birthyear", "initials", "family"]},
    "results": {
        "bindings": [
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q12345"},
                "personLabel": {"type": "literal", "value": "Ismail el Abassi"},
                "tkid": {
                    "type": "literal",
                    "value": "209ce05e-66ee-4999-928c-00f152b40ead",
                },
                "birthyear": {"type": "literal", "value": "1983"},
                "initials": {"type": "literal", "value": "I."},
                "family": {"type": "literal", "value": "el Abassi"},
            },
            {
                # Geen tkid → fallback naar natural-key (family + initials + birthyear).
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q99999"},
                "personLabel": {"type": "literal", "value": "Jan de Vries"},
                "birthyear": {"type": "literal", "value": "1970"},
                "initials": {"type": "literal", "value": "J."},
                "family": {"type": "literal", "value": "de Vries"},
            },
        ]
    },
}


# ---------------------------------------------------------------------------
# extract_qid
# ---------------------------------------------------------------------------


def test_extract_qid_from_uri():
    assert ws.extract_qid("http://www.wikidata.org/entity/Q1727053") == "Q1727053"
    assert ws.extract_qid("https://www.wikidata.org/wiki/Q42") == "Q42"


def test_extract_qid_returns_none_for_invalid():
    assert ws.extract_qid(None) is None
    assert ws.extract_qid("") is None
    assert ws.extract_qid("not-a-qid") is None


# ---------------------------------------------------------------------------
# normalize_org_name
# ---------------------------------------------------------------------------


def test_normalize_strips_prefixes():
    assert (
        ws.normalize_org_name("Ministerie van Binnenlandse Zaken en Koninkrijksrelaties")
        == "binnenlandse zaken en koninkrijksrelaties"
    )
    assert ws.normalize_org_name("Gemeente Amsterdam") == "amsterdam"
    assert ws.normalize_org_name("Provincie Noord-Holland") == "noord holland"


def test_normalize_handles_accents_and_dashes():
    assert ws.normalize_org_name("'s-Hertogenbosch") == "'s hertogenbosch".replace(" ", " ")
    # Gemeente Súdwest-Fryslân — ASCII fold + dash → space.
    assert ws.normalize_org_name("Gemeente Súdwest-Fryslân") == "sudwest fryslan"


def test_normalize_empty():
    assert ws.normalize_org_name(None) == ""
    assert ws.normalize_org_name("") == ""


# ---------------------------------------------------------------------------
# parse_org_bindings + parse_person_bindings
# ---------------------------------------------------------------------------


def test_parse_org_bindings_filters_non_qid():
    bindings = ORG_RESPONSE["results"]["bindings"]
    rows = ws.parse_org_bindings(bindings)
    assert len(rows) == 2
    qids = {r["qid"] for r in rows}
    assert qids == {"Q1727053", "Q123456"}
    bzk = next(r for r in rows if r["qid"] == "Q1727053")
    assert bzk["oin"] == "00000001003214345000"
    assert bzk["abbr"] == "BZK"


def test_parse_person_bindings_birthyear_int():
    rows = ws.parse_person_bindings(PERSON_RESPONSE["results"]["bindings"])
    assert len(rows) == 2
    abassi = next(r for r in rows if r["qid"] == "Q12345")
    assert abassi["birthyear"] == 1983
    assert abassi["tkid"] == "209ce05e-66ee-4999-928c-00f152b40ead"


# ---------------------------------------------------------------------------
# build_org_index + match_organisations
# ---------------------------------------------------------------------------


def test_match_org_by_oin():
    rows = ws.parse_org_bindings(ORG_RESPONSE["results"]["bindings"])
    index = ws.build_org_index(rows)

    record = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "identifiers": {"oin": "00000001003214345000"},
        "names": [{"value": "Ministerie van BZK", "abbr": "BZK"}],
    }
    matches = ws.match_organisations([record], index)
    assert len(matches) == 1
    _rec, row, method = matches[0]
    assert method == "oin"
    assert row["qid"] == "Q1727053"


def test_match_org_by_name_when_oin_absent():
    rows = ws.parse_org_bindings(ORG_RESPONSE["results"]["bindings"])
    index = ws.build_org_index(rows)

    record = {
        "id": "org:min-fin",
        "type": "ministerie",
        "names": [{"value": "Ministerie van Financiën", "abbr": "Fin"}],
    }
    matches = ws.match_organisations([record], index)
    assert len(matches) == 1
    _rec, row, method = matches[0]
    assert method == "name"
    assert row["qid"] == "Q123456"


def test_match_org_skips_already_filled():
    rows = ws.parse_org_bindings(ORG_RESPONSE["results"]["bindings"])
    index = ws.build_org_index(rows)
    record = {
        "id": "org:min-bzk",
        "identifiers": {"wikidata": "Q999"},
        "names": [{"value": "Ministerie van BZK"}],
    }
    matches = ws.match_organisations([record], index)
    assert matches == []


def test_match_gemeente_by_name():
    rows = ws.parse_org_bindings(GEMEENTE_RESPONSE["results"]["bindings"])
    index = ws.build_org_index(rows)
    record = {
        "id": "org:gemeente-amsterdam",
        "type": "gemeente",
        "names": [{"value": "Gemeente Amsterdam"}],
    }
    matches = ws.match_organisations([record], index)
    assert len(matches) == 1
    assert matches[0][1]["qid"] == "Q727"
    assert matches[0][2] == "name"


# ---------------------------------------------------------------------------
# build_person_index + match_personen
# ---------------------------------------------------------------------------


def test_match_person_by_tkid():
    rows = ws.parse_person_bindings(PERSON_RESPONSE["results"]["bindings"])
    index = ws.build_person_index(rows)
    record = {
        "id": "person:abassi-i-1983",
        "identifiers": {"tk_persoon_id": "209ce05e-66ee-4999-928c-00f152b40ead"},
        "name": {"family": "el Abassi", "initials": "I."},
        "birth": {"year": 1983},
    }
    matches = ws.match_personen([record], index)
    assert len(matches) == 1
    _, row, method = matches[0]
    assert method == "tkid"
    assert row["qid"] == "Q12345"


def test_match_person_by_natural_key():
    rows = ws.parse_person_bindings(PERSON_RESPONSE["results"]["bindings"])
    index = ws.build_person_index(rows)
    record = {
        "id": "person:vries-j-1970",
        "name": {"family": "de Vries", "initials": "J."},
        "birth": {"year": 1970},
    }
    matches = ws.match_personen([record], index)
    assert len(matches) == 1
    _, row, method = matches[0]
    assert method == "natural"
    assert row["qid"] == "Q99999"


def test_match_person_no_match_when_birthyear_off():
    rows = ws.parse_person_bindings(PERSON_RESPONSE["results"]["bindings"])
    index = ws.build_person_index(rows)
    record = {
        "id": "person:vries-j-1980",
        "name": {"family": "de Vries", "initials": "J."},
        "birth": {"year": 1980},
    }
    matches = ws.match_personen([record], index)
    assert matches == []


# ---------------------------------------------------------------------------
# merge_wikidata_into_record
# ---------------------------------------------------------------------------


def test_merge_adds_qid_and_source():
    record = {
        "id": "org:min-bzk",
        "identifiers": {"oin": "00000001003214345000"},
        "sources": [
            {"id": "roo", "url": "https://organisaties.overheid.nl/9632/", "retrieved": "2026-01-01"}
        ],
    }
    merged = ws.merge_wikidata_into_record(record, "Q1727053", today="2026-05-09")
    assert merged["identifiers"]["wikidata"] == "Q1727053"
    assert merged["identifiers"]["oin"] == "00000001003214345000"
    source_ids = {s["id"] for s in merged["sources"]}
    assert source_ids == {"roo", "wikidata"}
    wikidata_src = next(s for s in merged["sources"] if s["id"] == "wikidata")
    assert wikidata_src["url"] == "https://www.wikidata.org/wiki/Q1727053"
    assert wikidata_src["retrieved"] == "2026-05-09"


def test_merge_does_not_mutate_input():
    record = {"id": "org:x", "identifiers": {"oin": "abc"}}
    snapshot = json.dumps(record, sort_keys=True)
    ws.merge_wikidata_into_record(record, "Q1", today="2026-05-09")
    assert json.dumps(record, sort_keys=True) == snapshot


# ---------------------------------------------------------------------------
# query_sparql cache + http mocking
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def get(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> _StubResponse:
        self.calls.append((url, params, headers))
        return _StubResponse(self.payload)


def test_query_sparql_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Voorkom dat de rate-limiter de testsuite vertraagt.
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    cache_dir = tmp_path / "cache"
    client = _StubClient(ORG_RESPONSE)
    query = "SELECT ?x WHERE { ?x ?y ?z }"

    bindings_first = ws.query_sparql(query, cache_dir=cache_dir, client=client)
    assert len(bindings_first) == 3
    assert len(client.calls) == 1
    # Cache-bestand is geschreven.
    files = list(cache_dir.glob("*.json"))
    assert len(files) == 1

    # Tweede call moet uit cache komen — geen extra HTTP-call.
    bindings_second = ws.query_sparql(query, cache_dir=cache_dir, client=client)
    assert len(bindings_second) == 3
    assert len(client.calls) == 1


def test_query_sparql_sends_useragent_and_accept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    client = _StubClient(ORG_RESPONSE)
    ws.query_sparql("SELECT * WHERE { ?a ?b ?c }", cache_dir=tmp_path / "c", client=client)
    _, _, headers = client.calls[0]
    assert headers["User-Agent"].startswith("polder-bot/")
    assert "anne.schuth@gmail.com" in headers["User-Agent"]
    assert headers["Accept"] == "application/sparql-results+json"


# ---------------------------------------------------------------------------
# enrich_organisations end-to-end met mock SPARQL
# ---------------------------------------------------------------------------


def _write_org_yaml(directory: Path, slug: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{slug}.yaml"
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
    return target


def test_enrich_organisations_writes_qid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    org_root = tmp_path / "organisaties"
    bzk_path = _write_org_yaml(
        org_root / "ministeries",
        "bzk",
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "classification": "ministerie",
            "names": [{"value": "Ministerie van BZK", "abbr": "BZK"}],
            "identifiers": {"oin": "00000001003214345000"},
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/9632/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    ams_path = _write_org_yaml(
        org_root / "gemeenten",
        "amsterdam",
        {
            "id": "org:gemeente-amsterdam",
            "type": "gemeente",
            "names": [{"value": "Gemeente Amsterdam"}],
            "sources": [
                {
                    "id": "roo",
                    "url": "https://organisaties.overheid.nl/000/",
                    "retrieved": "2026-01-01",
                }
            ],
        },
    )
    # Lege provincies/waterschappen folders worden overgeslagen.

    response_by_query: dict[str, dict[str, Any]] = {
        ws.ORG_QUERIES["ministerie"].strip(): ORG_RESPONSE,
        ws.ORG_QUERIES["gemeente"].strip(): GEMEENTE_RESPONSE,
        ws.ORG_QUERIES["provincie"].strip(): {"results": {"bindings": []}},
        ws.ORG_QUERIES["waterschap"].strip(): {"results": {"bindings": []}},
    }

    def fake_query(
        query: str,
        *,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        client: Any | None = None,
        timeout: float = 60.0,
    ) -> list[dict[str, Any]]:
        payload = response_by_query[query.strip()]
        return list(payload["results"]["bindings"])

    monkeypatch.setattr(ws, "query_sparql", fake_query)

    stats = ws.enrich_organisations(
        org_root, cache_dir=tmp_path / "cache", today="2026-05-09"
    )

    bzk_loaded = yaml.safe_load(bzk_path.read_text(encoding="utf-8"))
    assert bzk_loaded["identifiers"]["wikidata"] == "Q1727053"
    assert bzk_loaded["identifiers"]["oin"] == "00000001003214345000"
    src_ids = {s["id"] for s in bzk_loaded["sources"]}
    assert src_ids == {"roo", "wikidata"}

    ams_loaded = yaml.safe_load(ams_path.read_text(encoding="utf-8"))
    assert ams_loaded["identifiers"]["wikidata"] == "Q727"

    assert stats["ministerie"]["written"] == 1
    assert stats["ministerie"]["matched_oin"] == 1
    assert stats["gemeente"]["written"] == 1
    assert stats["gemeente"]["matched_name"] == 1


def test_enrich_organisations_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    org_root = tmp_path / "organisaties"
    bzk_path = _write_org_yaml(
        org_root / "ministeries",
        "bzk",
        {
            "id": "org:min-bzk",
            "type": "ministerie",
            "names": [{"value": "Ministerie van BZK", "abbr": "BZK"}],
            "identifiers": {"oin": "00000001003214345000"},
        },
    )
    before = bzk_path.read_text(encoding="utf-8")

    response_by_query: dict[str, dict[str, Any]] = {
        ws.ORG_QUERIES["ministerie"].strip(): ORG_RESPONSE,
        ws.ORG_QUERIES["gemeente"].strip(): {"results": {"bindings": []}},
        ws.ORG_QUERIES["provincie"].strip(): {"results": {"bindings": []}},
        ws.ORG_QUERIES["waterschap"].strip(): {"results": {"bindings": []}},
    }

    def fake_query(query: str, **_: Any) -> list[dict[str, Any]]:
        return list(response_by_query[query.strip()]["results"]["bindings"])

    monkeypatch.setattr(ws, "query_sparql", fake_query)

    ws.enrich_organisations(
        org_root, cache_dir=tmp_path / "cache", dry_run=True, today="2026-05-09"
    )
    after = bzk_path.read_text(encoding="utf-8")
    assert after == before


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def test_main_dry_run_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    # Lege data-root → niets te doen, maar exit-code moet 0 zijn.
    (tmp_path / "data" / "organisaties").mkdir(parents=True)
    (tmp_path / "data" / "personen").mkdir(parents=True)

    monkeypatch.setattr(ws, "query_sparql", lambda *a, **k: [])

    rc = ws.main(
        [
            "--orgs",
            "--dry-run",
            "--data-root",
            str(tmp_path / "data"),
            "--cache",
            str(tmp_path / "cache"),
        ]
    )
    assert rc == 0
