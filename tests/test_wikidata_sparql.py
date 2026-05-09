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
        endpoint: str = ws.SPARQL_ENDPOINT,
        request_interval: float | None = None,
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


# ---------------------------------------------------------------------------
# Bewindspersoon: parser, builder, enrich
# ---------------------------------------------------------------------------


BEWINDSPERSOON_RESPONSE = {
    "head": {
        "vars": [
            "person",
            "personLabel",
            "tkid",
            "birthyear",
            "familyLabel",
            "role",
            "roleLabel",
            "ministry",
            "ministryLabel",
            "start",
            "end",
        ]
    },
    "results": {
        "bindings": [
            # Mark Rutte als minister-president, P642 leeg → mapping via role-Q.
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q57792"},
                "personLabel": {"type": "literal", "value": "Mark Rutte"},
                "birthyear": {"type": "literal", "value": "1967"},
                "familyLabel": {"type": "literal", "value": "Rutte"},
                "role": {"type": "uri", "value": "http://www.wikidata.org/entity/Q3058109"},
                "roleLabel": {"type": "literal", "value": "minister-president van Nederland"},
                "start": {"type": "literal", "value": "2010-10-14T00:00:00Z"},
                "end": {"type": "literal", "value": "2024-07-02T00:00:00Z"},
            },
            # Bewindspersoon op een huidig ministerie via P642.
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q19999"},
                "personLabel": {"type": "literal", "value": "Jan de Test"},
                "birthyear": {"type": "literal", "value": "1955"},
                "familyLabel": {"type": "literal", "value": "de Test"},
                "role": {"type": "uri", "value": "http://www.wikidata.org/entity/Q88888"},
                "roleLabel": {"type": "literal", "value": "minister van Financiën"},
                "ministry": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1037511"},
                "ministryLabel": {"type": "literal", "value": "Ministerie van Financiën"},
                "start": {"type": "literal", "value": "1990-11-07T00:00:00Z"},
                "end": {"type": "literal", "value": "1994-08-22T00:00:00Z"},
            },
            # Onbekend ministerie-Q-id → mandaat wordt overgeslagen.
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q44444"},
                "personLabel": {"type": "literal", "value": "Onbekend Persoon"},
                "birthyear": {"type": "literal", "value": "1960"},
                "familyLabel": {"type": "literal", "value": "Persoon"},
                "role": {"type": "uri", "value": "http://www.wikidata.org/entity/Q77777"},
                "roleLabel": {"type": "literal", "value": "minister van Iets"},
                "ministry": {"type": "uri", "value": "http://www.wikidata.org/entity/Q9999999"},
                "start": {"type": "literal", "value": "1960-01-01T00:00:00Z"},
                "end": {"type": "literal", "value": "1962-01-01T00:00:00Z"},
            },
        ]
    },
}


def test_parse_bewindspersoon_bindings():
    rows = ws.parse_bewindspersoon_bindings(BEWINDSPERSOON_RESPONSE["results"]["bindings"])
    assert len(rows) == 3
    rutte = next(r for r in rows if r["person_qid"] == "Q57792")
    assert rutte["birthyear"] == 1967
    assert rutte["start"] == "2010-10-14"
    assert rutte["end"] == "2024-07-02"
    assert rutte["role_qid"] == "Q3058109"
    assert rutte["ministry_qid"] is None


def test_person_id_from_label():
    slug, name = ws.person_id_from_label("Mark Rutte", "Rutte", None, 1967, "Q57792")
    assert slug == "rutte-1967"  # Geen initialen in label
    assert name["family"] == "Rutte"
    assert name["full"] == "Mark Rutte"
    # Met initialen
    slug2, name2 = ws.person_id_from_label("M. Rutte", "Rutte", "M.", 1967, "Q57792")
    assert slug2 == "rutte-m-1967"
    assert name2["initials"] == "M."


def test_build_bewindspersoon_records_handles_minister_president():
    rows = ws.parse_bewindspersoon_bindings(BEWINDSPERSOON_RESPONSE["results"]["bindings"])
    bundle = ws.build_bewindspersoon_records(
        rows,
        "minister",
        ministry_qid_to_slug=ws.MINISTRY_QID_TO_SLUG,
        today="2026-05-09",
    )
    persons = bundle["persons"]
    posts = bundle["posts"]
    assert "Q57792" in persons
    rutte = persons["Q57792"]
    # Mandaat naar minister-president.
    mandaten = rutte["mandaten"]
    assert any(m["post_id"] == "post:minister-president" for m in mandaten)
    # Post bootstrap: post:minister-president EN post:minister-min-fin.
    assert "post:minister-president" in posts
    assert "post:minister-min-fin" in posts
    # Onbekend ministerie: persoon Q44444 wordt aangemaakt maar zonder mandaat
    # (we doen niet expliciet een test voor 0-mandaten; check dat geen post
    # voor het onbekende ministerie wordt gebootstrapt).
    assert not any("9999999" in p for p in posts)


def test_build_bewindspersoon_skips_records_without_birthyear():
    rows = [
        {
            "person_qid": "Q42",
            "person_label": "Geen Geboorte",
            "birthyear": None,
            "family": "Geboorte",
            "role_qid": "Q1",
            "role_label": "minister",
            "ministry_qid": "Q1037511",
            "start": "2000-01-01",
            "end": "2001-01-01",
        }
    ]
    bundle = ws.build_bewindspersoon_records(
        rows, "minister", ministry_qid_to_slug=ws.MINISTRY_QID_TO_SLUG, today="2026-05-09"
    )
    assert bundle["persons"] == {}


def test_enrich_bewindspersonen_writes_records_and_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    # Bouw minimale data-root met een ministerie-record.
    org_dir = tmp_path / "data" / "organisaties" / "ministeries"
    org_dir.mkdir(parents=True)
    fin = {
        "id": "org:min-fin",
        "type": "ministerie",
        "identifiers": {"wikidata": "Q1037511"},
        "names": [{"value": "Financiën", "abbr": "Fin"}],
        "valid_from": "1798-01-01",
        "sources": [{"id": "manual", "url": "https://example.org", "retrieved": "2026-05-09"}],
    }
    with (org_dir / "fin.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(fin, fh, sort_keys=False, allow_unicode=True)
    az = {
        "id": "org:min-az",
        "type": "ministerie",
        "identifiers": {"wikidata": "Q939757"},
        "names": [{"value": "Algemene Zaken", "abbr": "AZ"}],
        "valid_from": "1937-01-01",
        "sources": [{"id": "manual", "url": "https://example.org", "retrieved": "2026-05-09"}],
    }
    with (org_dir / "az.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(az, fh, sort_keys=False, allow_unicode=True)

    (tmp_path / "data" / "personen" / "current").mkdir(parents=True)
    (tmp_path / "data" / "personen" / "historisch").mkdir(parents=True)

    response_by_query: dict[str, dict[str, Any]] = {
        ws.BEWINDSPERSOON_QUERIES["minister"].strip(): BEWINDSPERSOON_RESPONSE,
        ws.BEWINDSPERSOON_QUERIES["staatssecretaris"].strip(): {
            "results": {"bindings": []}
        },
    }

    def fake_query(query: str, **_: Any) -> list[dict[str, Any]]:
        return list(response_by_query[query.strip()]["results"]["bindings"])

    monkeypatch.setattr(ws, "query_sparql", fake_query)

    stats = ws.enrich_bewindspersonen(
        tmp_path / "data",
        cache_dir=tmp_path / "cache",
        today="2026-05-09",
    )

    # Verwacht: Mark Rutte en Jan de Test geschreven; minister-president-post bootstrap.
    historisch = list((tmp_path / "data" / "personen" / "historisch").glob("*.yaml"))
    assert any("rutte" in p.name for p in historisch)
    rutte_path = next(p for p in historisch if "rutte" in p.name)
    rutte = yaml.safe_load(rutte_path.read_text(encoding="utf-8"))
    assert rutte["identifiers"]["wikidata"] == "Q57792"
    assert any(m["post_id"] == "post:minister-president" for m in rutte["mandaten"])

    # Post-yaml's gebootstrapt.
    posten_root = tmp_path / "data" / "posten" / "ministers"
    assert (posten_root / "minister-president.yaml").exists()
    assert (posten_root / "minister-min-fin.yaml").exists()
    mp_post = yaml.safe_load(
        (posten_root / "minister-president.yaml").read_text(encoding="utf-8")
    )
    assert mp_post["classification"] == "bewindspersoon"
    assert mp_post["organization_id"] == "org:min-az"

    assert stats["minister"]["new_persons"] >= 2
    assert stats["minister"]["bootstrapped_posts"] >= 2


def test_enrich_bewindspersonen_merges_into_existing_tk_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Bestaand TK-record met tk_persoon_id moet wikidata-id en mandaat krijgen."""
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    org_dir = tmp_path / "data" / "organisaties" / "ministeries"
    org_dir.mkdir(parents=True)
    az = {
        "id": "org:min-az",
        "type": "ministerie",
        "identifiers": {"wikidata": "Q939757"},
        "names": [{"value": "Algemene Zaken", "abbr": "AZ"}],
        "valid_from": "1937-01-01",
        "sources": [{"id": "manual", "url": "https://example.org", "retrieved": "2026-05-09"}],
    }
    with (org_dir / "az.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(az, fh, sort_keys=False, allow_unicode=True)

    person_dir = tmp_path / "data" / "personen" / "historisch"
    person_dir.mkdir(parents=True)
    (tmp_path / "data" / "personen" / "current").mkdir(parents=True)
    rutte_existing = {
        "id": "person:rutte-m-1967",
        "identifiers": {"tk_persoon_id": "abc-123"},
        "name": {"full": "Mark Rutte", "family": "Rutte", "given": "Mark", "initials": "M."},
        "birth": {"year": 1967},
        "gender": "m",
        "mandaten": [
            {
                "id": "tk-1",
                "organization_id": "org:tweede-kamer",
                "post_id": "post:kamerlid",
                "role": "Kamerlid voor VVD",
                "start_date": "2006-06-28",
                "end_date": "2010-10-13",
                "sources": [
                    {"id": "tk_odata", "url": "https://x.example/y", "retrieved": "2026-05-09"}
                ],
            }
        ],
        "sources": [
            {"id": "tk_odata", "url": "https://x.example/y", "retrieved": "2026-05-09"}
        ],
    }
    with (person_dir / "rutte-m-1967.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(rutte_existing, fh, sort_keys=False, allow_unicode=True)

    # Wikidata-respons levert MP-mandaat met dezelfde person, met tkid die overlapt.
    bw_response = {
        "results": {
            "bindings": [
                {
                    "person": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q57792",
                    },
                    "personLabel": {"type": "literal", "value": "Mark Rutte"},
                    "tkid": {"type": "literal", "value": "abc-123"},
                    "birthyear": {"type": "literal", "value": "1967"},
                    "familyLabel": {"type": "literal", "value": "Rutte"},
                    "role": {
                        "type": "uri",
                        "value": "http://www.wikidata.org/entity/Q3058109",
                    },
                    "roleLabel": {"type": "literal", "value": "minister-president"},
                    "start": {"type": "literal", "value": "2010-10-14T00:00:00Z"},
                    "end": {"type": "literal", "value": "2024-07-02T00:00:00Z"},
                }
            ]
        }
    }
    response_by_query = {
        ws.BEWINDSPERSOON_QUERIES["minister"].strip(): bw_response,
        ws.BEWINDSPERSOON_QUERIES["staatssecretaris"].strip(): {
            "results": {"bindings": []}
        },
    }
    monkeypatch.setattr(
        ws,
        "query_sparql",
        lambda query, **_: list(response_by_query[query.strip()]["results"]["bindings"]),
    )

    ws.enrich_bewindspersonen(
        tmp_path / "data", cache_dir=tmp_path / "cache", today="2026-05-09"
    )

    rutte = yaml.safe_load(
        (person_dir / "rutte-m-1967.yaml").read_text(encoding="utf-8")
    )
    assert rutte["identifiers"]["tk_persoon_id"] == "abc-123"
    assert rutte["identifiers"]["wikidata"] == "Q57792"
    # Bestaand kamerlid-mandaat blijft, MP-mandaat toegevoegd.
    posts = {m["post_id"] for m in rutte["mandaten"]}
    assert "post:kamerlid" in posts
    assert "post:minister-president" in posts
    src_ids = {s["id"] for s in rutte["sources"]}
    assert "tk_odata" in src_ids
    assert "wikidata" in src_ids


def test_enrich_bewindspersonen_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Twee runs achter elkaar mogen geen duplicaten opleveren."""
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    org_dir = tmp_path / "data" / "organisaties" / "ministeries"
    org_dir.mkdir(parents=True)
    az = {
        "id": "org:min-az",
        "type": "ministerie",
        "identifiers": {"wikidata": "Q939757"},
        "names": [{"value": "Algemene Zaken", "abbr": "AZ"}],
        "valid_from": "1937-01-01",
        "sources": [{"id": "manual", "url": "https://example.org", "retrieved": "2026-05-09"}],
    }
    with (org_dir / "az.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(az, fh, sort_keys=False, allow_unicode=True)
    (tmp_path / "data" / "personen" / "current").mkdir(parents=True)
    (tmp_path / "data" / "personen" / "historisch").mkdir(parents=True)

    response_by_query = {
        ws.BEWINDSPERSOON_QUERIES["minister"].strip(): BEWINDSPERSOON_RESPONSE,
        ws.BEWINDSPERSOON_QUERIES["staatssecretaris"].strip(): {
            "results": {"bindings": []}
        },
    }
    monkeypatch.setattr(
        ws,
        "query_sparql",
        lambda query, **_: list(response_by_query[query.strip()]["results"]["bindings"]),
    )

    ws.enrich_bewindspersonen(
        tmp_path / "data", cache_dir=tmp_path / "cache", today="2026-05-09"
    )
    rutte_path = tmp_path / "data" / "personen" / "historisch" / "rutte-1967.yaml"
    assert rutte_path.exists()
    first = yaml.safe_load(rutte_path.read_text(encoding="utf-8"))
    n_mandaten_first = len(first["mandaten"])

    ws.enrich_bewindspersonen(
        tmp_path / "data", cache_dir=tmp_path / "cache", today="2026-05-09"
    )
    second = yaml.safe_load(rutte_path.read_text(encoding="utf-8"))
    assert len(second["mandaten"]) == n_mandaten_first


# ---------------------------------------------------------------------------
# ABD-TMG
# ---------------------------------------------------------------------------


ABD_TMG_RESPONSE = {
    "results": {
        "bindings": [
            {
                "person": {"type": "uri", "value": "http://www.wikidata.org/entity/Q33333"},
                "personLabel": {"type": "literal", "value": "Sjoerd Voorbeeld"},
                "birthyear": {"type": "literal", "value": "1965"},
                "familyLabel": {"type": "literal", "value": "Voorbeeld"},
                "role": {"type": "uri", "value": "http://www.wikidata.org/entity/Q22222"},
                "roleLabel": {"type": "literal", "value": "secretaris-generaal van Financiën"},
                "ministry": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1037511"},
                "ministryLabel": {"type": "literal", "value": "Financiën"},
                "start": {"type": "literal", "value": "2020-09-01T00:00:00Z"},
                "roleType": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2003810"},
            }
        ]
    }
}


def test_parse_abd_tmg_bindings():
    rows = ws.parse_abd_tmg_bindings(ABD_TMG_RESPONSE["results"]["bindings"])
    assert len(rows) == 1
    assert rows[0]["role_type_qid"] == "Q2003810"
    assert rows[0]["ministry_qid"] == "Q1037511"


def test_enrich_abd_tmg_writes_sg_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)
    org_dir = tmp_path / "data" / "organisaties" / "ministeries"
    org_dir.mkdir(parents=True)
    with (org_dir / "fin.yaml").open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                "id": "org:min-fin",
                "type": "ministerie",
                "identifiers": {"wikidata": "Q1037511"},
                "names": [{"value": "Financiën"}],
                "valid_from": "1798-01-01",
                "sources": [
                    {"id": "manual", "url": "https://example.org", "retrieved": "2026-05-09"}
                ],
            },
            fh,
            sort_keys=False,
        )
    (tmp_path / "data" / "personen" / "current").mkdir(parents=True)
    (tmp_path / "data" / "personen" / "historisch").mkdir(parents=True)

    monkeypatch.setattr(
        ws,
        "query_sparql",
        lambda *a, **k: list(ABD_TMG_RESPONSE["results"]["bindings"]),
    )

    stats = ws.enrich_abd_tmg(
        tmp_path / "data", cache_dir=tmp_path / "cache", today="2026-05-09"
    )

    sg_post = tmp_path / "data" / "posten" / "abd-sg" / "sg-min-fin.yaml"
    assert sg_post.exists()
    sg = yaml.safe_load(sg_post.read_text(encoding="utf-8"))
    assert sg["classification"] == "abd-tmg"
    assert sg["organization_id"] == "org:min-fin"

    person_path = tmp_path / "data" / "personen" / "current" / "voorbeeld-1965.yaml"
    assert person_path.exists()
    person = yaml.safe_load(person_path.read_text(encoding="utf-8"))
    assert person["identifiers"]["wikidata"] == "Q33333"
    assert any(m["post_id"] == "post:sg-min-fin" for m in person["mandaten"])
    assert stats["bootstrapped_posts"] == 1
    assert stats["new_persons"] == 1
