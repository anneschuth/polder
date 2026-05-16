"""Tests voor casing-normalisatie en het fix-casing command."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from polder.cli.commands.fix_casing_cmd import (
    _canon,
    _fix_org,
    _fix_persoon,
    _fix_post,
    fix_casing,
)
from polder.lib.casing import canonicalize_leading_case


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("directie Foo", "Directie Foo"),
        ("afdeling Bar", "Afdeling Bar"),
        ("directoraat-generaal Werk", "Directoraat-generaal Werk"),
        ("minister van OCW", "Minister van OCW"),
        ("staatssecretaris van Defensie", "Staatssecretaris van Defensie"),
        ("afdelingshoofd Beleid Wonen", "Afdelingshoofd Beleid Wonen"),
        # al correct: idempotent
        ("Directie Foo", "Directie Foo"),
        ("Minister van OCW", "Minister van OCW"),
        # gecureerde uitzonderingen: ongemoeid
        ("pSG Cluster", "pSG Cluster"),
        ("plv. Secretaris-generaal", "plv. Secretaris-generaal"),
        ("het participatiebedrijf", "het participatiebedrijf"),
        ("de Noordelijke Rekenkamer", "de Noordelijke Rekenkamer"),
        ("euregio rijn-maas-noord", "euregio rijn-maas-noord"),
        (
            "provinciaal fonds nazorg gesloten stortplaatsen Zuid-Holland",
            "provinciaal fonds nazorg gesloten stortplaatsen Zuid-Holland",
        ),
        # randgevallen
        ("", ""),
        ("(APS) directie Strategie", "(APS) directie Strategie"),
        # geen alfabetisch teken / whitespace-only: mag niet crashen
        ("   ", "   "),
        ("\t", "\t"),
        ("123", "123"),
        ("-", "-"),
    ],
)
def test_canonicalize_leading_case(src: str, expected: str) -> None:
    assert canonicalize_leading_case(src) == expected


def test_canonicalize_none() -> None:
    assert canonicalize_leading_case(None) == ""


def test_whitespace_only_does_not_crash() -> None:
    # regressie: "   ".split(None, 1)[0] gooide IndexError voordat de
    # first-alpha-guard naar voren werd gehaald.
    for s in ["   ", "\t\n", " ", ""]:
        assert canonicalize_leading_case(s) == s


def test_idempotent_double_apply() -> None:
    for s in ["directie Foo", "minister van VWS", "pSG Cluster", "Afdeling X", "   ", "123-x"]:
        once = canonicalize_leading_case(s)
        assert canonicalize_leading_case(once) == once


def test_collision_map_is_source_faithful() -> None:
    # GR-term gekapitaliseerd, inhoudswoorden bron-getrouw (geen eigen
    # title-casing): bron had "schoolverzuim"/"regio" klein.
    assert (
        _canon("Gemeenschappelijke regeling schoolverzuim en VSV regio West-Brabant")
        == "Gemeenschappelijke Regeling schoolverzuim en VSV regio West-Brabant"
    )
    # secretaris-generaal: Nederlandse soortnaam, klein ná het koppelteken,
    # consistent met directoraat-generaal. "Plaatsvervangend secretaris-
    # generaal" heeft óók de S klein: de dominante bron-variant en alle
    # ~30 post-labels/rollen gebruiken die vorm, dus de org-onderdelen
    # moeten ermee samenvallen (anders cross-entity casefold-collision).
    assert _canon("Secretaris-Generaal") == "Secretaris-generaal"
    assert _canon("Plaatsvervangend Secretaris-Generaal") == "Plaatsvervangend secretaris-generaal"
    assert _canon("Plaatsvervangend Secretaris-generaal") == "Plaatsvervangend secretaris-generaal"
    assert _canon("Directoraat-Generaal Langdurige Zorg") == "Directoraat-generaal Langdurige Zorg"
    # eigennaam-stilering behouden
    assert _canon("Stichting Aanzet") == "Stichting AanZet"
    assert _canon("gemeenschappelijke regeling WIHW") == "Gemeenschappelijke Regeling WIHW"


def test_collision_map_idempotent() -> None:
    # geen enkele canon-waarde mag zelf een key zijn (anders convergeert
    # een her-run niet). Dubbelcheck op de echte map.
    from polder.cli.commands.fix_casing_cmd import COLLISION_MAP

    canon_values = set(COLLISION_MAP.values())
    assert not (canon_values & set(COLLISION_MAP)), "canon-waarde is ook een key"
    for src, dst in COLLISION_MAP.items():
        assert _canon(src) == dst
        assert _canon(dst) == dst  # tweede run laat de canon staan


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_fix_casing_all_three_fields_and_idempotent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    org = data / "organisaties" / "organisatieonderdelen" / "x.yaml"
    post = data / "posten" / "p.yaml"
    persoon = data / "personen" / "q.yaml"
    _write(org, {"id": "org:x", "names": [{"value": "directie Foo", "valid_from": "2020-01-01"}]})
    _write(post, {"id": "post:y", "label": "directeur Foo"})
    _write(
        persoon,
        {
            "id": "person:z",
            "mandaten": [{"id": "m1", "role": "minister van OCW"}],
        },
    )

    fix_casing(data_dir=data, dry_run=False)

    assert yaml.safe_load(org.read_text())["names"][0]["value"] == "Directie Foo"
    assert yaml.safe_load(post.read_text())["label"] == "Directeur Foo"
    assert yaml.safe_load(persoon.read_text())["mandaten"][0]["role"] == "Minister van OCW"

    # tweede run = byte-identiek (idempotent)
    before = {p: p.read_bytes() for p in (org, post, persoon)}
    fix_casing(data_dir=data, dry_run=False)
    for p, b in before.items():
        assert p.read_bytes() == b


def test_fix_casing_skips_staging(tmp_path: Path) -> None:
    data = tmp_path / "data"
    staged = data / "organisaties" / "_staging" / "s.yaml"
    _write(
        staged, {"id": "org:s", "names": [{"value": "directie Staged", "valid_from": "2020-01-01"}]}
    )
    fix_casing(data_dir=data, dry_run=False)
    assert yaml.safe_load(staged.read_text())["names"][0]["value"] == "directie Staged"


def _data_root() -> Path:
    # tests/ -> repo-root/data
    return Path(__file__).resolve().parent.parent / "data"


def test_no_cross_entity_casefold_collisions() -> None:
    """Geen enkel concept mag in twee casefold-equivalente vormen bestaan,
    over org-namen, post-labels en mandaat-rollen samen. Dit is de
    eigenschap die de hele PR borgt; een per-veld-check miste eerder een
    "Plaatsvervangend Secretaris-generaal" vs "... secretaris-generaal"
    botsing tussen org-onderdelen en post-labels.
    """
    root = _data_root()
    if not root.exists():  # pragma: no cover - alleen in losse checkouts
        pytest.skip("data/ niet aanwezig")

    by_casefold: dict[str, set[str]] = {}

    def add(value: object) -> None:
        if isinstance(value, str) and value:
            by_casefold.setdefault(value.casefold(), set()).add(value)

    for p in (root / "organisaties").rglob("*.yaml"):
        if "_staging" in p.parts:
            continue
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(d, dict):
            for n in d.get("names") or []:
                if isinstance(n, dict):
                    add(n.get("value"))
    for p in (root / "posten").rglob("*.yaml"):
        if "_staging" in p.parts:
            continue
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(d, dict):
            add(d.get("label"))
    for p in (root / "personen").rglob("*.yaml"):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(d, dict):
            for m in d.get("mandaten") or []:
                if isinstance(m, dict):
                    add(m.get("role"))

    collisions = {k: sorted(v) for k, v in by_casefold.items() if len(v) > 1}
    assert not collisions, f"casefold-collisions blijven bestaan: {collisions}"


def test_real_data_is_idempotent_under_fix_casing() -> None:
    """`polder fix-casing --dry-run` op de echte (gebackfillde) data moet
    0 wijzigingen opleveren — de canonicalisatie is volledig toegepast en
    de COLLISION_MAP convergeert."""
    root = _data_root()
    if not root.exists():  # pragma: no cover
        pytest.skip("data/ niet aanwezig")

    changed = 0
    fixers = {"organisaties": _fix_org, "posten": _fix_post, "personen": _fix_persoon}
    for subdir, fixer in fixers.items():
        for yp in (root / subdir).rglob("*.yaml"):
            if "_staging" in yp.parts:
                continue
            d = yaml.safe_load(yp.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                changed += fixer(d)
    assert changed == 0, f"{changed} velden zouden nog wijzigen — backfill incompleet"
