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
    (tmp_path / "data" / "personen").mkdir(parents=True, exist_ok=True)

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

    (tmp_path / "data" / "personen").mkdir(parents=True, exist_ok=True)

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
    historisch = list((tmp_path / "data" / "personen").glob("*.yaml"))
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

    person_dir = tmp_path / "data" / "personen"
    person_dir.mkdir(parents=True)
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
    (tmp_path / "data" / "personen").mkdir(parents=True, exist_ok=True)

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
    rutte_path = tmp_path / "data" / "personen" / "rutte-1967.yaml"
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


# ---------------------------------------------------------------------------
# Naam-historie: query + parser + merge + enrich-integratie
# ---------------------------------------------------------------------------


NAME_HISTORY_RESPONSE_EZK = {
    "head": {"vars": ["qid", "prop", "name", "start", "end"]},
    "results": {
        "bindings": [
            # EZK: 2017-2025, official_name
            {
                "qid": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2986560"},
                "prop": {"type": "literal", "value": "official"},
                "name": {
                    "type": "literal",
                    "xml:lang": "nl",
                    "value": "Ministerie van Economische Zaken en Klimaat",
                },
                "start": {"type": "literal", "value": "2017-10-26T00:00:00Z"},
            },
            # EZ: 1815-2017, official_name (zelfde Q-id, vroegere naam)
            {
                "qid": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2986560"},
                "prop": {"type": "literal", "value": "official"},
                "name": {
                    "type": "literal",
                    "xml:lang": "nl",
                    "value": "Ministerie van Economische Zaken",
                },
                "start": {"type": "literal", "value": "1815-01-01T00:00:00Z"},
                "end": {"type": "literal", "value": "2017-10-26T00:00:00Z"},
            },
            # Korte naam EZK (overlapt met de huidige official).
            {
                "qid": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2986560"},
                "prop": {"type": "literal", "value": "short"},
                "name": {"type": "literal", "xml:lang": "nl", "value": "EZK"},
                "start": {"type": "literal", "value": "2017-10-26T00:00:00Z"},
            },
            # Korte naam EZ.
            {
                "qid": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2986560"},
                "prop": {"type": "literal", "value": "short"},
                "name": {"type": "literal", "xml:lang": "nl", "value": "EZ"},
                "start": {"type": "literal", "value": "1815-01-01T00:00:00Z"},
                "end": {"type": "literal", "value": "2017-10-26T00:00:00Z"},
            },
        ]
    },
}


def test_build_name_history_query_includes_values_clause():
    q = ws._build_name_history_query(["Q1", "Q2", "Q3"], qlever=True)
    assert "VALUES ?qid { wd:Q1 wd:Q2 wd:Q3 }" in q
    assert "p:P1448" in q
    assert "p:P1813" in q
    assert "pq:P580" in q
    assert "pq:P582" in q


def test_build_name_history_query_rejects_empty():
    with pytest.raises(ValueError):
        ws._build_name_history_query([], qlever=True)


def test_parse_name_history_bindings_groups_by_qid():
    parsed = ws.parse_name_history_bindings(
        NAME_HISTORY_RESPONSE_EZK["results"]["bindings"]
    )
    assert "Q2986560" in parsed
    rows = parsed["Q2986560"]
    assert len(rows) == 4
    officials = [r for r in rows if r["prop"] == "official"]
    shorts = [r for r in rows if r["prop"] == "short"]
    assert len(officials) == 2
    assert len(shorts) == 2


def test_parse_name_history_bindings_dedupes():
    bindings = list(NAME_HISTORY_RESPONSE_EZK["results"]["bindings"])
    bindings.append(bindings[0])  # duplicaat
    parsed = ws.parse_name_history_bindings(bindings)
    assert len(parsed["Q2986560"]) == 4


def test_build_name_variants_dedupes_duplicate_official_names():
    """Wikidata heeft soms meerdere P1448-statements voor dezelfde naam (verschillende
    periodes). Onze parser moet die mergen tot één entry met de vroegste start
    en de laatste end (of None als één van de statements open is)."""
    raw = [
        {"prop": "official", "value": "Ministerie van Economische Zaken",
         "valid_from": "2012-11-05", "valid_until": "2017-10-26"},
        {"prop": "official", "value": "Ministerie van Economische Zaken",
         "valid_from": None, "valid_until": "2026-02-23"},
        {"prop": "official", "value": "Ministerie van Economische Zaken en Klimaat",
         "valid_from": "2017-10-26", "valid_until": None},
    ]
    variants = ws._build_name_variants(raw)
    # Ontdubbeld: 2 entries.
    assert len(variants) == 2
    ez = next(v for v in variants if "Klimaat" not in v["value"])
    # Vroegste start onder de twee statements is "2012-11-05".
    assert ez["valid_from"] == "2012-11-05"
    # Laatste end onder de twee statements is "2026-02-23".
    assert ez["valid_until"] == "2026-02-23"


def test_build_name_variants_couples_official_with_short():
    raw = ws.parse_name_history_bindings(
        NAME_HISTORY_RESPONSE_EZK["results"]["bindings"]
    )["Q2986560"]
    variants = ws._build_name_variants(raw)
    assert len(variants) == 2
    # Sortering: oudst eerst.
    assert variants[0]["valid_from"].startswith("1815")
    assert variants[1]["valid_from"].startswith("2017")
    # Abbr's gekoppeld op overlap.
    assert variants[0]["abbr"] == "EZ"
    assert variants[1]["abbr"] == "EZK"
    # End-date gevuld voor de oudste, None voor huidige.
    assert variants[0]["valid_until"] == "2017-10-26"
    assert variants[1]["valid_until"] is None


def test_merge_names_into_record_adds_missing_history():
    record = {
        "id": "org:min-ezk",
        "names": [
            {"value": "Economische Zaken en Klimaat", "abbr": "EZK", "valid_from": "2017-10-26"}
        ],
    }
    history = [
        {"value": "Ministerie van Economische Zaken", "valid_from": "1815-01-01",
         "valid_until": "2017-10-26", "abbr": "EZ"},
        {"value": "Ministerie van Economische Zaken en Klimaat", "valid_from": "2017-10-26",
         "valid_until": None, "abbr": "EZK"},
    ]
    merged, changed = ws.merge_names_into_record(record, history)
    assert changed is True
    names = merged["names"]
    assert len(names) == 2
    # Bestaande entry behouden (abbr=EZK).
    ezk_entry = next(n for n in names if "Klimaat" in n["value"])
    assert ezk_entry["abbr"] == "EZK"
    assert ezk_entry["value"] == "Economische Zaken en Klimaat"  # bestaande value behouden
    # Historische entry toegevoegd.
    ez_entry = next(n for n in names if n["value"] == "Ministerie van Economische Zaken")
    assert ez_entry["valid_from"] == "1815-01-01"
    assert ez_entry["valid_until"] == "2017-10-26"
    assert ez_entry["abbr"] == "EZ"
    # Sortering: oudst eerst.
    assert names[0]["valid_from"] == "1815-01-01"


def test_merge_names_into_record_idempotent_when_history_already_present():
    record = {
        "id": "org:min-ezk",
        "names": [
            {"value": "Ministerie van Economische Zaken", "abbr": "EZ",
             "valid_from": "1815-01-01", "valid_until": "2017-10-26"},
            {"value": "Economische Zaken en Klimaat", "abbr": "EZK",
             "valid_from": "2017-10-26"},
        ],
    }
    history = [
        {"value": "Ministerie van Economische Zaken", "valid_from": "1815-01-01",
         "valid_until": "2017-10-26", "abbr": "EZ"},
        {"value": "Ministerie van Economische Zaken en Klimaat", "valid_from": "2017-10-26",
         "valid_until": None, "abbr": "EZK"},
    ]
    _, changed = ws.merge_names_into_record(record, history)
    assert changed is False


def test_merge_names_into_record_empty_history_no_op():
    record = {"id": "org:min-x", "names": [{"value": "Foo", "valid_from": "2020-01-01"}]}
    merged, changed = ws.merge_names_into_record(record, [])
    assert changed is False
    assert merged is record


def test_fetch_name_history_batches(monkeypatch: pytest.MonkeyPatch):
    queries_seen: list[str] = []

    def fake_query(query: str, **_: Any) -> list[dict[str, Any]]:
        queries_seen.append(query)
        # Lever alle bindings terug ongeacht de batch (niet realistisch, maar
        # voldoende om te testen dat batching werkt).
        return list(NAME_HISTORY_RESPONSE_EZK["results"]["bindings"])

    monkeypatch.setattr(ws, "query_sparql", fake_query)
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    qids = [f"Q{n}" for n in range(125)]  # 3 batches van 50
    history = ws.fetch_name_history(qids, batch_size=50)
    assert len(queries_seen) == 3
    # Onze fake levert altijd EZK-data; check dat resultaten zijn aggregated.
    assert "Q2986560" in history


def test_enrich_organisations_with_name_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: na enrich met include_name_history krijgt EZK 2 names entries."""
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    org_root = tmp_path / "organisaties"
    ezk_path = _write_org_yaml(
        org_root / "ministeries",
        "ezk",
        {
            "id": "org:min-ezk",
            "type": "ministerie",
            "classification": "ministerie",
            "names": [
                {
                    "value": "Economische Zaken en Klimaat",
                    "abbr": "EZK",
                    "valid_from": "2017-10-26",
                }
            ],
            "identifiers": {
                "oin": "00000001003214369000",
                "wikidata": "Q2986560",
            },
            "valid_from": "2017-10-26",
            "sources": [
                {"id": "roo", "url": "https://organisaties.overheid.nl/10621/", "retrieved": "2026-01-01"},
            ],
        },
    )

    org_response = {
        "head": {"vars": ["item", "itemLabel", "abbr", "oin"]},
        "results": {
            "bindings": [
                {
                    "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2986560"},
                    "itemLabel": {"type": "literal", "value": "Ministerie van Economische Zaken en Klimaat"},
                    "abbr": {"type": "literal", "value": "EZK"},
                    "oin": {"type": "literal", "value": "00000001003214369000"},
                }
            ]
        },
    }

    def fake_query(query: str, **_: Any) -> list[dict[str, Any]]:
        # Naam-historie: query bevat p:P1448. Org-query: SELECT ?item.
        if "p:P1448" in query or "P1448" in query and "p:" in query:
            return list(NAME_HISTORY_RESPONSE_EZK["results"]["bindings"])
        if "wdt:P31 wd:Q3143387" in query:  # ministerie-class
            return list(org_response["results"]["bindings"])
        return []

    monkeypatch.setattr(ws, "query_sparql", fake_query)

    stats = ws.enrich_organisations(
        org_root,
        cache_dir=tmp_path / "cache",
        today="2026-05-09",
        include_name_history=True,
    )
    assert stats["ministerie"]["names_updated"] >= 1

    ezk = yaml.safe_load(ezk_path.read_text(encoding="utf-8"))
    names = ezk["names"]
    assert len(names) == 2
    # Bestaande EZK-entry behouden.
    assert any(n["value"] == "Economische Zaken en Klimaat" and n.get("abbr") == "EZK" for n in names)
    # Historische EZ-entry toegevoegd.
    assert any(n["value"] == "Ministerie van Economische Zaken" for n in names)
    # Sortering: oudst eerst.
    assert names[0]["valid_from"] == "1815-01-01"


def test_enrich_organisations_name_history_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Tweede run zonder Wikidata-wijzigingen schrijft niet opnieuw."""
    monkeypatch.setattr(ws, "MIN_REQUEST_INTERVAL", 0.0)

    org_root = tmp_path / "organisaties"
    ezk_path = _write_org_yaml(
        org_root / "ministeries",
        "ezk",
        {
            "id": "org:min-ezk",
            "type": "ministerie",
            "names": [
                {"value": "Ministerie van Economische Zaken", "abbr": "EZ",
                 "valid_from": "1815-01-01", "valid_until": "2017-10-26"},
                {"value": "Ministerie van Economische Zaken en Klimaat", "abbr": "EZK",
                 "valid_from": "2017-10-26"},
            ],
            "identifiers": {"oin": "x", "wikidata": "Q2986560"},
            "valid_from": "2017-10-26",
            "sources": [{"id": "roo", "url": "https://example.org", "retrieved": "2026-01-01"}],
        },
    )
    org_response = {"head": {"vars": []}, "results": {"bindings": []}}

    def fake_query(query: str, **_: Any) -> list[dict[str, Any]]:
        if "p:P1448" in query:
            return list(NAME_HISTORY_RESPONSE_EZK["results"]["bindings"])
        return list(org_response["results"]["bindings"])

    monkeypatch.setattr(ws, "query_sparql", fake_query)

    before = ezk_path.read_text(encoding="utf-8")
    stats = ws.enrich_organisations(
        org_root,
        cache_dir=tmp_path / "cache",
        today="2026-05-09",
        include_name_history=True,
    )
    after = ezk_path.read_text(encoding="utf-8")
    assert stats["ministerie"]["names_updated"] == 0
    assert before == after


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
    (tmp_path / "data" / "personen").mkdir(parents=True, exist_ok=True)

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

    person_path = tmp_path / "data" / "personen" / "voorbeeld-1965.yaml"
    assert person_path.exists()
    person = yaml.safe_load(person_path.read_text(encoding="utf-8"))
    assert person["identifiers"]["wikidata"] == "Q33333"
    assert any(m["post_id"] == "post:sg-min-fin" for m in person["mandaten"])
    assert stats["bootstrapped_posts"] == 1
    assert stats["new_persons"] == 1


# ---------------------------------------------------------------------------
# Regression: family_birth fallback mag geen verkeerde Q-id koppelen bij naamgenoten
# ---------------------------------------------------------------------------


def test_match_personen_skips_family_birth_when_multiple_candidates() -> None:
    """Twee Wikidata-personen met dezelfde family+birth: geen koppeling.

    Regressie: ervoor werd Emiel van Dijk (1985) gekoppeld aan Jimmy Dijk's
    Q-id Q28861260 omdat de family_birth fallback de eerste match nam zonder
    te checken of er meer kandidaten waren.
    """
    from polder.fetchers.wikidata_sparql import build_person_index, match_personen

    rows = [
        {"qid": "Q28861260", "family": "Dijk", "initials": "J.P.", "birthyear": 1985},
        {"qid": "Q99999999", "family": "Dijk", "initials": "E.", "birthyear": 1985},
    ]
    index = build_person_index(rows)

    # Polder-record voor Emiel zonder Wikidata-id, met initialen die niet
    # exact matchen op één van beide rows (in productie kan dit gebeuren als
    # de Wikidata-row de initialen mist).
    record = {
        "id": "person:dijk-e-1985",
        "identifiers": {},
        "name": {"family": "Dijk", "initials": "X.X."},
        "birth": {"year": 1985},
    }
    matches = match_personen([record], index)
    assert matches == [], (
        "Met twee kandidaten op (Dijk, 1985) mag geen Q-id gekoppeld worden"
    )


def test_match_personen_family_birth_works_when_unique_candidate() -> None:
    """Eén Wikidata-kandidaat op (family, birth): wel koppelen."""
    from polder.fetchers.wikidata_sparql import build_person_index, match_personen

    rows = [
        {"qid": "Q12345", "family": "Klaverblad", "initials": "M.", "birthyear": 1972},
    ]
    index = build_person_index(rows)

    record = {
        "id": "person:klaverblad-1972",
        "identifiers": {},
        "name": {"family": "Klaverblad", "initials": "X.X."},
        "birth": {"year": 1972},
    }
    matches = match_personen([record], index)
    assert len(matches) == 1
    assert matches[0][2] == "family_birth"
    assert matches[0][1]["qid"] == "Q12345"


def test_build_bewindspersoon_records_skipt_mandate_zonder_start() -> None:
    """Regressie: vroeger plakte de fetcher start_date='1945-01-01' bij missing P580."""
    from polder.fetchers.wikidata_sparql import build_bewindspersoon_records

    rows = [
        {
            "person_qid": "Q105773583",
            "label": "Mikal Tseggai",
            "family": "Tseggai",
            "initials": "M.",
            "birthyear": 1995,
            "ministry_qid": "Q1075",  # min-ocw
            "role_qid": "Q15729678",
            "role_label": "minister van Onderwijs, Cultuur en Wetenschap",
            "start": None,  # geen P580: dit was de bug-trigger
            "end": None,
        },
        {
            # Tweede rij MET P580: moet wel doorkomen.
            "person_qid": "Q57792",
            "label": "Mark Rutte",
            "family": "Rutte",
            "initials": "M.",
            "birthyear": 1967,
            "ministry_qid": "Q1075",
            "role_qid": "Q83307",
            "role_label": "minister-president van Nederland",
            "start": "2010-10-14",
            "end": "2024-07-02",
        },
    ]
    result = build_bewindspersoon_records(
        rows,
        "minister",
        ministry_qid_to_slug={"Q1075": "min-ocw"},
        today="2026-05-11",
    )
    persons = result["persons"]
    # Tseggai mag in 'persons' staan (we maken het persoon-record), maar zonder
    # mandaten (de mandate-zonder-start moet geskipt zijn).
    tseggai = persons.get("Q105773583")
    if tseggai is not None:
        assert tseggai.get("mandaten") == [], (
            f"Verwachtte 0 mandaten voor Tseggai, kreeg {tseggai.get('mandaten')!r}"
        )
    # Rutte moet WEL een mandaat hebben.
    rutte = persons.get("Q57792")
    assert rutte is not None
    assert len(rutte.get("mandaten") or []) == 1
    assert rutte["mandaten"][0]["start_date"] == "2010-10-14"
