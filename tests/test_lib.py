"""Tests voor de polder library."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from polder.lib import (
    InlineMandaat,
    Organisatie,
    Persoon,
    Polder,
    Post,
    Source,
)


@pytest.fixture
def mini_polder(tmp_path: Path) -> Path:
    """Bouw een minimale polder-tree met 2 orgs, 1 persoon, 1 post."""
    root = tmp_path
    (root / "data" / "organisaties" / "ministeries").mkdir(parents=True)
    (root / "data" / "organisaties" / "agentschappen").mkdir(parents=True)
    (root / "data" / "personen" / "current").mkdir(parents=True)
    (root / "data" / "posten").mkdir(parents=True)
    (root / "data" / "mandaten").mkdir(parents=True)

    org_bzk = {
        "id": "org:min-bzk",
        "type": "ministerie",
        "classification": "ministerie",
        "parent_id": None,
        "names": [
            {
                "value": "Binnenlandse Zaken en Koninkrijksrelaties",
                "abbr": "BZK",
                "valid_from": "2010-10-14",
            }
        ],
        "valid_from": "2010-10-14",
        "sources": [
            {"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}
        ],
        "identifiers": {"wikidata": "Q1727053"},
    }
    (root / "data" / "organisaties" / "ministeries" / "bzk.yaml").write_text(
        yaml.safe_dump(org_bzk, sort_keys=False), encoding="utf-8"
    )

    org_rvig = {
        "id": "org:rvig",
        "type": "agentschap",
        "classification": "agentschap",
        "parent_id": "org:min-bzk",
        "names": [{"value": "RvIG", "valid_from": "2014-01-01"}],
        "valid_from": "2014-01-01",
        "sources": [
            {"id": "roo", "url": "https://example.org/roo", "retrieved": "2026-05-09"}
        ],
    }
    (root / "data" / "organisaties" / "agentschappen" / "rvig.yaml").write_text(
        yaml.safe_dump(org_rvig, sort_keys=False), encoding="utf-8"
    )

    post = {
        "id": "post:sg-min-bzk",
        "organization_id": "org:min-bzk",
        "label": "Secretaris-generaal BZK",
        "classification": "abd-tmg",
        "valid_from": "2010-10-14",
    }
    (root / "data" / "posten" / "sg-min-bzk.yaml").write_text(
        yaml.safe_dump(post, sort_keys=False), encoding="utf-8"
    )

    persoon = {
        "id": "person:jansen-jp-1965",
        "name": {"full": "J.P. Jansen", "family": "Jansen", "given": "J.P."},
        "birth": {"year": 1965},
        "gender": "f",
        "mandaten": [
            {
                "id": "m1",
                "organization_id": "org:min-bzk",
                "post_id": "post:sg-min-bzk",
                "role": "Secretaris-generaal",
                "start_date": "2020-01-01",
                "end_date": None,
                "sources": [
                    {
                        "id": "stcrt",
                        "url": "https://example.org/stcrt/1",
                        "retrieved": "2026-05-09",
                    }
                ],
            }
        ],
        "sources": [
            {"id": "abd", "url": "https://example.org/abd", "retrieved": "2026-05-09"}
        ],
    }
    (root / "data" / "personen" / "current" / "jansen-jp-1965.yaml").write_text(
        yaml.safe_dump(persoon, sort_keys=False), encoding="utf-8"
    )

    return root


def test_polder_local_opens(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    assert p.root == mini_polder.resolve()


def test_polder_local_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Polder.local(tmp_path / "nope")


def test_organisaties_repo(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    orgs = list(p.organisaties.all())
    assert len(orgs) == 2
    bzk = p.organisaties.get("org:min-bzk")
    assert bzk is not None
    assert bzk.type == "ministerie"
    assert bzk.names[0].abbr == "BZK"


def test_organisaties_by_type(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    ministeries = p.organisaties.by_type("ministerie")
    assert len(ministeries) == 1
    assert ministeries[0].id == "org:min-bzk"


def test_organisaties_with_identifier(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    found = p.organisaties.with_identifier("wikidata", "Q1727053")
    assert found is not None
    assert found.id == "org:min-bzk"
    assert p.organisaties.with_identifier("wikidata", "Q9999") is None


def test_organisaties_active_on(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    today = date(2026, 5, 9)
    active = p.organisaties.active_on(today)
    assert len(active) == 2
    # Voor 2014: alleen BZK
    early = p.organisaties.active_on(date(2012, 1, 1))
    assert {o.id for o in early} == {"org:min-bzk"}


def test_personen_and_mandaten(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    personen = list(p.personen.all())
    assert len(personen) == 1
    pers = personen[0]
    assert pers.name.family == "Jansen"

    current = p.personen.current()
    assert len(current) == 1

    mandaten = list(p.mandaten.all())
    assert len(mandaten) == 1
    assert mandaten[0].person_id == "person:jansen-jp-1965"
    assert mandaten[0].post_id == "post:sg-min-bzk"

    by_org = p.mandaten.at_organization("org:min-bzk")
    assert len(by_org) == 1
    by_post = p.mandaten.for_post("post:sg-min-bzk")
    assert len(by_post) == 1
    by_person = p.mandaten.for_person("person:jansen-jp-1965")
    assert len(by_person) == 1


def test_posten_repo(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    posts = list(p.posten.all())
    assert len(posts) == 1
    assert posts[0].classification == "abd-tmg"
    at_bzk = p.posten.at_organization("org:min-bzk")
    assert len(at_bzk) == 1


def test_repo_where(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    ministeries = p.organisaties.where(lambda o: o.type == "ministerie")
    assert len(ministeries) == 1


def test_repo_caching(mini_polder: Path) -> None:
    p = Polder.local(mini_polder)
    a = list(p.organisaties.all())
    b = list(p.organisaties.all())
    # Cached: zelfde objecten.
    assert a[0] is b[0]
    p.organisaties.reload()
    c = list(p.organisaties.all())
    assert c[0] is not a[0]


def test_organisatie_from_yaml_roundtrip(tmp_path: Path) -> None:
    org = Organisatie(
        id="org:test",
        type="ministerie",
        classification="ministerie",
        names=[{"value": "Test", "valid_from": "2020-01-01"}],
        valid_from=date(2020, 1, 1),
        sources=[Source(id="t", url="https://example.org", retrieved=date(2026, 5, 9))],
    )
    path = tmp_path / "test.yaml"
    org.to_yaml(path)
    loaded = Organisatie.from_yaml(path)
    assert loaded.id == "org:test"
    assert loaded.names[0].value == "Test"


def test_persoon_from_yaml(mini_polder: Path) -> None:
    path = mini_polder / "data" / "personen" / "current" / "jansen-jp-1965.yaml"
    p = Persoon.from_yaml(path)
    assert p.id == "person:jansen-jp-1965"
    assert p.mandaten and isinstance(p.mandaten[0], InlineMandaat)


def test_post_from_yaml(mini_polder: Path) -> None:
    path = mini_polder / "data" / "posten" / "sg-min-bzk.yaml"
    post = Post.from_yaml(path)
    assert post.classification == "abd-tmg"
    assert post.organization_id == "org:min-bzk"
