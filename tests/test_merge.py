"""Tests voor `polder merge` (org/post/person)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from polder.cli.main import app


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def mini_data(tmp_path: Path) -> Path:
    """Mini-data met dup org + dup person + verwijzende mandate."""
    root = tmp_path / "data"
    # Canonical organisatie
    _write_yaml(
        root / "organisaties" / "organisatieonderdelen" / "aivd.yaml",
        {
            "id": "org:onderdeel-aivd",
            "type": "organisatieonderdeel",
            "classification": "organisatieonderdeel",
            "parent_id": "org:min-bzk",
            "names": [{"value": "AIVD", "valid_from": "1900-01-01"}],
            "valid_from": "1900-01-01",
            "sources": [{"id": "roo", "url": "https://x", "retrieved": "2026-01-01"}],
        },
    )
    # Duplicate organisatie
    _write_yaml(
        root / "organisaties" / "organisatieonderdelen" / "aivd-min-bzk.yaml",
        {
            "id": "org:onderdeel-aivd-min-bzk",
            "type": "organisatieonderdeel",
            "classification": "organisatieonderdeel",
            "parent_id": "org:min-bzk",
            "names": [
                {
                    "value": "Algemene Inlichtingen- en Veiligheidsdienst (AIVD)",
                    "valid_from": "2026-05-14",
                }
            ],
            "valid_from": "2026-05-14",
            "sources": [{"id": "abd_nieuws", "url": "https://y", "retrieved": "2026-05-14"}],
        },
    )
    # Persoon met mandaat dat naar dup verwijst
    _write_yaml(
        root / "personen" / "schoof-1957.yaml",
        {
            "id": "person:schoof-1957",
            "name": {"full": "Dick Schoof", "family": "Schoof", "given": "Dick"},
            "mandaten": [
                {
                    "id": "m1",
                    "organization_id": "org:onderdeel-aivd-min-bzk",  # naar dup!
                    "post_id": "post:dg-aivd",
                    "role": "directeur-generaal AIVD",
                    "start_date": "2018-07-01",
                    "end_date": "2020-04-30",
                    "sources": [
                        {"id": "abd_nieuws", "url": "https://z", "retrieved": "2026-05-14"}
                    ],
                }
            ],
            "sources": [{"id": "tk", "url": "https://tk/x", "retrieved": "2026-05-01"}],
        },
    )
    return root


def test_merge_org_dryrun_does_not_modify(mini_data: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "org",
            "org:onderdeel-aivd-min-bzk",
            "org:onderdeel-aivd",
            "--data",
            str(mini_data),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.output

    # Dup-file moet er nog zijn.
    assert (mini_data / "organisaties" / "organisatieonderdelen" / "aivd-min-bzk.yaml").exists()
    # Persoon-mandate moet nog naar dup wijzen.
    person = yaml.safe_load(
        (mini_data / "personen" / "schoof-1957.yaml").read_text(encoding="utf-8")
    )
    assert person["mandaten"][0]["organization_id"] == "org:onderdeel-aivd-min-bzk"


def test_merge_org_apply_remaps_and_deletes(mini_data: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "org",
            "org:onderdeel-aivd-min-bzk",
            "org:onderdeel-aivd",
            "--apply",
            "--data",
            str(mini_data),
        ],
    )
    assert result.exit_code == 0, result.output

    # Dup-file weg.
    assert not (mini_data / "organisaties" / "organisatieonderdelen" / "aivd-min-bzk.yaml").exists()
    # Persoon-mandate naar canonical.
    person = yaml.safe_load(
        (mini_data / "personen" / "schoof-1957.yaml").read_text(encoding="utf-8")
    )
    assert person["mandaten"][0]["organization_id"] == "org:onderdeel-aivd"


def test_merge_org_missing_dup_fails(mini_data: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "org",
            "org:onderdeel-nonexistent",
            "org:onderdeel-aivd",
            "--data",
            str(mini_data),
        ],
    )
    assert result.exit_code != 0
    assert "niet gevonden" in result.output


def test_merge_org_same_id_fails(mini_data: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["merge", "org", "org:onderdeel-aivd", "org:onderdeel-aivd", "--data", str(mini_data)],
    )
    assert result.exit_code != 0


def test_merge_org_wrong_prefix_fails(mini_data: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["merge", "org", "post:foo", "org:onderdeel-aivd", "--data", str(mini_data)],
    )
    assert result.exit_code != 0


@pytest.fixture
def mini_persons(tmp_path: Path) -> Path:
    """Mini-data met twee person-records die dezelfde persoon zijn."""
    root = tmp_path / "data"
    _write_yaml(
        root / "personen" / "kleijwegt-c-canonical.yaml",
        {
            "id": "person:kleijwegt-c-canonical",
            "name": {"full": "Coert Kleijwegt", "family": "Kleijwegt", "given": "Coert"},
            "mandaten": [
                {
                    "id": "m-canonical-1",
                    "organization_id": "org:onderdeel-aivd",
                    "post_id": "post:foo",
                    "role": "X",
                    "start_date": "2025-07-01",
                    "end_date": None,
                    "sources": [
                        {"id": "abd", "url": "https://canonical-source", "retrieved": "2026-05-14"}
                    ],
                }
            ],
            "sources": [
                {"id": "abd", "url": "https://canonical-source", "retrieved": "2026-05-14"}
            ],
        },
    )
    _write_yaml(
        root / "personen" / "kleijwegt-dup.yaml",
        {
            "id": "person:kleijwegt-dup",
            "name": {"full": "C. Kleijwegt", "family": "Kleijwegt", "given": "C."},
            "mandaten": [
                {
                    "id": "m-dup-1",
                    "organization_id": "org:onderdeel-aivd",
                    "post_id": "post:bar",
                    "role": "Y",
                    "start_date": "2021-03-01",
                    "end_date": None,
                    "sources": [
                        {"id": "abd", "url": "https://dup-source", "retrieved": "2026-05-14"}
                    ],
                }
            ],
            "sources": [{"id": "abd", "url": "https://dup-source", "retrieved": "2026-05-14"}],
        },
    )
    return root


def test_merge_person_combines_mandaten_and_sources(mini_persons: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "person",
            "person:kleijwegt-dup",
            "person:kleijwegt-c-canonical",
            "--apply",
            "--data",
            str(mini_persons),
        ],
    )
    assert result.exit_code == 0, result.output

    # Dup-file weg.
    assert not (mini_persons / "personen" / "kleijwegt-dup.yaml").exists()
    # Canonical heeft beide mandaten en beide sources.
    canonical = yaml.safe_load(
        (mini_persons / "personen" / "kleijwegt-c-canonical.yaml").read_text(encoding="utf-8")
    )
    mandate_ids = {m["id"] for m in canonical["mandaten"]}
    assert mandate_ids == {"m-canonical-1", "m-dup-1"}
    source_urls = {s["url"] for s in canonical["sources"]}
    assert source_urls == {"https://canonical-source", "https://dup-source"}


def test_merge_person_does_not_duplicate_existing_mandaat(mini_persons: Path) -> None:
    """Als beide records hetzelfde mandate-id hebben, niet dupliceren."""
    # Voeg een mandate met dezelfde id toe aan canonical.
    canonical_path = mini_persons / "personen" / "kleijwegt-c-canonical.yaml"
    data = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    data["mandaten"].append(
        {
            "id": "m-dup-1",  # Zelfde id als in dup!
            "organization_id": "org:onderdeel-aivd",
            "post_id": "post:other",
            "role": "Z",
            "start_date": "2020-01-01",
            "end_date": None,
            "sources": [],
        }
    )
    canonical_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "person",
            "person:kleijwegt-dup",
            "person:kleijwegt-c-canonical",
            "--apply",
            "--data",
            str(mini_persons),
        ],
    )
    assert result.exit_code == 0, result.output

    canonical = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    mandate_ids = [m["id"] for m in canonical["mandaten"]]
    # m-dup-1 hoort er één keer in (de bestaande), niet dubbel.
    assert mandate_ids.count("m-dup-1") == 1


def test_merge_org_consolidates_identifiers_and_sources(mini_data: Path) -> None:
    """Dup-org draagt de stabiele ROO-identifier; canonical mist die.

    De ROO-superset-import landde voor sommige entiteiten twee records:
    een schoon `org:onderdeel-<x>` zonder identifiers en een
    `org:onderdeel-<x>-min-<y>` mét de `roo_id`. Bij merge mag die
    identifier niet verloren gaan met het dup-file.
    """
    dup_path = mini_data / "organisaties" / "organisatieonderdelen" / "aivd-min-bzk.yaml"
    dup = yaml.safe_load(dup_path.read_text(encoding="utf-8"))
    dup["identifiers"] = {"roo_id": "9633", "tooi": "oorg-aivd"}
    dup_path.write_text(yaml.safe_dump(dup, sort_keys=False, allow_unicode=True), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "org",
            "org:onderdeel-aivd-min-bzk",
            "org:onderdeel-aivd",
            "--apply",
            "--data",
            str(mini_data),
        ],
    )
    assert result.exit_code == 0, result.output

    canonical = yaml.safe_load(
        (mini_data / "organisaties" / "organisatieonderdelen" / "aivd.yaml").read_text(
            encoding="utf-8"
        )
    )
    # roo_id + tooi van de dup zijn overgenomen.
    assert canonical["identifiers"] == {"roo_id": "9633", "tooi": "oorg-aivd"}
    # Beide bron-URLs aanwezig, geen dubbele.
    source_urls = sorted(s["url"] for s in canonical["sources"])
    assert source_urls == ["https://x", "https://y"]


def test_merge_org_canonical_identifier_wins_on_conflict(mini_data: Path) -> None:
    """Bij conflicterende identifier-key behoudt canonical zijn eigen waarde."""
    canonical_path = mini_data / "organisaties" / "organisatieonderdelen" / "aivd.yaml"
    canonical = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    canonical["identifiers"] = {"roo_id": "CANONICAL-9633"}
    canonical_path.write_text(
        yaml.safe_dump(canonical, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    dup_path = mini_data / "organisaties" / "organisatieonderdelen" / "aivd-min-bzk.yaml"
    dup = yaml.safe_load(dup_path.read_text(encoding="utf-8"))
    dup["identifiers"] = {"roo_id": "DUP-9633", "oin": "00000001"}
    dup_path.write_text(yaml.safe_dump(dup, sort_keys=False, allow_unicode=True), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "merge",
            "org",
            "org:onderdeel-aivd-min-bzk",
            "org:onderdeel-aivd",
            "--apply",
            "--data",
            str(mini_data),
        ],
    )
    assert result.exit_code == 0, result.output

    canonical = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    # Canonical roo_id behouden, oin van dup toegevoegd.
    assert canonical["identifiers"] == {"roo_id": "CANONICAL-9633", "oin": "00000001"}


def test_apply_string_remap_does_not_corrupt_prefix_ids(tmp_path: Path) -> None:
    """Regressie: `_apply_string_remap` mag een id dat een *prefix* is van
    een ander, ongerelateerd id niet meeverbouwen.

    Bug: merge van `org:onderdeel-sg-min-ezk` -> canonical hernoemde ook
    `org:onderdeel-sg-min-ezk-min-kgg` (een andere org) tot
    `...-min-ezk-<canon>-min-kgg`, wat dubbel-suffix-corruptie en 100+
    broken refs opleverde.
    """
    from polder.cli.commands.merge_cmd import _apply_string_remap

    f = tmp_path / "ref.yaml"
    f.write_text(
        "organization_id: org:onderdeel-sg-min-ezk\n"
        "parent_id: org:onderdeel-sg-min-ezk-min-kgg\n"
        "other: org:onderdeel-sg-min-ezk-afdeling-x\n"
        "quoted: 'org:onderdeel-sg-min-ezk'\n",
        encoding="utf-8",
    )
    changed = _apply_string_remap(
        [f], "org:onderdeel-sg-min-ezk", "org:onderdeel-sg-cluster-min-ezk"
    )
    assert changed == 1
    out = f.read_text(encoding="utf-8")
    # Het volledige token is vervangen...
    assert "organization_id: org:onderdeel-sg-cluster-min-ezk\n" in out
    assert "quoted: 'org:onderdeel-sg-cluster-min-ezk'\n" in out
    # ...maar de langere, ongerelateerde ids zijn ONGEMOEID.
    assert "parent_id: org:onderdeel-sg-min-ezk-min-kgg\n" in out
    assert "other: org:onderdeel-sg-min-ezk-afdeling-x\n" in out
    assert "-min-ezk-min-ezk" not in out


def test_apply_string_remap_replaces_at_non_slug_boundaries(tmp_path: Path) -> None:
    """Regressie (hostile review): de lookahead mocht alleen
    slug-vervolgtekens (\\w, -) blokkeren. Een id gevolgd door
    `,` `]` `.` `:` quote of regeleinde-zonder-newline moet WEL vervangen
    worden, anders blijven dangling refs achter."""
    from polder.cli.commands.merge_cmd import _apply_string_remap

    f = tmp_path / "ref.yaml"
    f.write_text(
        "flow: [org:min-bzk, org:min-az]\n"
        "trailing: org:min-bzk.\n"
        "keyish: org:min-bzk:\n"
        "url: https://x/org:min-bzk/sub\n"
        "noeol: org:min-bzk",  # geen newline aan einde
        encoding="utf-8",
    )
    changed = _apply_string_remap([f], "org:min-bzk", "org:min-binnenlandse-zaken")
    assert changed == 1
    out = f.read_text(encoding="utf-8")
    assert "[org:min-binnenlandse-zaken, org:min-az]" in out
    assert "trailing: org:min-binnenlandse-zaken.\n" in out
    assert "keyish: org:min-binnenlandse-zaken:\n" in out
    assert "url: https://x/org:min-binnenlandse-zaken/sub\n" in out
    assert out.endswith("noeol: org:min-binnenlandse-zaken")
    # de andere org (min-az) ongemoeid
    assert "org:min-az" in out


def test_apply_string_remap_replacement_is_literal(tmp_path: Path) -> None:
    """`new` mag geen regex-replacement-syntax interpreteren."""
    from polder.cli.commands.merge_cmd import _apply_string_remap

    f = tmp_path / "r.yaml"
    f.write_text("id: org:a\n", encoding="utf-8")
    # canonical met backslash-achtige tekens mag niet crashen/expanderen
    _apply_string_remap([f], "org:a", "org:b")
    assert f.read_text(encoding="utf-8") == "id: org:b\n"


def test_merge_org_aborts_when_references_remain(tmp_path: Path) -> None:
    """Veiligheid (hostile review): als na remap nog een verwijzing naar
    het dup-id bestaat buiten het dup-file, mag het dup-file NIET worden
    verwijderd (geen dangling pointers)."""
    import yaml as _yaml
    from typer.testing import CliRunner

    from polder.cli.main import app

    root = tmp_path
    (root / "organisaties" / "ministeries").mkdir(parents=True)
    (root / "posten").mkdir(parents=True)

    def w(p, d):
        p.write_text(_yaml.safe_dump(d, sort_keys=False, allow_unicode=True))

    w(
        root / "organisaties" / "ministeries" / "dup.yaml",
        {"id": "org:dup", "type": "ministerie", "names": [{"value": "Dup"}]},
    )
    w(
        root / "organisaties" / "ministeries" / "canon.yaml",
        {"id": "org:canon", "type": "ministerie", "names": [{"value": "Canon"}]},
    )
    # Een post die org:dup referenceert MAAR in een vorm die de
    # token-grens-regex niet als hele-id matcht zou kunnen missen; hier
    # gebruiken we de normale vorm zodat remap zou moeten slagen — de test
    # borgt dat als remap faalt het bestand blijft. We forceren een
    # mislukte remap door het bestand read-only te maken.
    postf = root / "posten" / "p.yaml"
    w(postf, {"id": "post:p", "organization_id": "org:dup"})
    postf.chmod(0o444)
    try:
        res = CliRunner().invoke(
            app,
            ["merge", "org", "org:dup", "org:canon", "--apply", "--data", str(root)],
        )
        # remap kon niet schrijven -> referentie blijft -> abort, dup blijft
        assert res.exit_code != 0
        assert (root / "organisaties" / "ministeries" / "dup.yaml").exists()
    finally:
        postf.chmod(0o644)
