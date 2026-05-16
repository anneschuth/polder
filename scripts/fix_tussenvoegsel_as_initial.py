"""Herstel personen waar een tussenvoegsel als initiaal in `name.given` zit.

De Open Raadsinformatie-fetcher schreef bij sommige records het tussenvoegsel
achter de initialen in `name.given`, met een hoofdletter, en geen apart
`name.tussenvoegsel`. Bijvoorbeeld:

    name.full         = "J.C.M. Van Aelst"
    name.family       = "Aelst"
    name.given        = "J.C.M. Van"        # <- tussenvoegsel in given
    name.initials     = "J.C.M."            # (soms ook fout: "B.V." ipv "B.P.")
    name.tussenvoegsel = (afwezig)

Schema wil `name.family` zonder tussenvoegsel (`Aelst` is dus al correct) en
het tussenvoegsel in `name.tussenvoegsel`. Deze migratie verplaatst het
tussenvoegsel naar `name.tussenvoegsel`, herberekent `name.initials` uit de
overgebleven initiaal-tokens (repareert meteen records waar de
tussenvoegsel-letter in `initials` lekte) en verwijdert `name.given` als er
geen echte roepnaam overblijft. `name.full` en `name.family` blijven
ongemoeid (bron-getrouw, schema-conform).

Waarom niet `parse_person_name(name.full)`: de bron-data heeft het
tussenvoegsel met hoofdletter (`Van`), en `_looks_like_tussenvoegsel`
test op een kleine beginletter. De canonieke parser mis-parset `Van`
daardoor net zoals de bron-bug zelf. Daarom splitsen we expliciet op
`name.given` en lidmaatschap van de tussenvoegsel-set.

Twee varianten van hetzelfde bron-patroon:

  - initialen-kop: `given = "J.C.M. Van"` -> `initials = "J.C.M."`,
    `given` verwijderd, `tussenvoegsel = "van"`.
  - roepnaam-kop:  `given = "Aly van"`   -> `given = "Aly"`,
    `tussenvoegsel = "van"`, `initials` herberekend op de
    roepnaam-letter (`A.`) zodat de gelekte tussenvoegsel-letter
    (`A.V.`) verdwijnt.

Niet in scope: de slug/filename die nog de fout-initiaal draagt
(`aelst-jv`). Dat is cosmetisch en vereist `polder merge person` met
refs-update — aparte vervolg-issue. Ook buiten scope: records met een
partijnaam-stem als family (`den Boon (SGP)`) — ander bug-patroon.

Run: `uv run python scripts/fix_tussenvoegsel_as_initial.py [--dry-run]`.
"""

from __future__ import annotations

import argparse
import re
import sys
from itertools import pairwise
from pathlib import Path

import yaml

# Tussenvoegsels die in `name.given` achter de initialen kunnen lekken.
# Identiek aan scripts/fix_title_as_family_name.py::_TUSSENVOEGSELS plus
# de meerwoord-staarten worden via token-absorptie afgevangen.
TUSSENVOEGSELS = {
    "van",
    "der",
    "den",
    "de",
    "het",
    "ten",
    "ter",
    "te",
    "op",
    "aan",
    "in",
    "tot",
    "'t",
    "'s",
}

# Een initiaal-token is één of meer losse initialen: `J.` of `J.C.M.`.
# Bron-data heeft beide vormen (spatie-gescheiden en aaneengeschreven).
INIT_TOKEN_RX = re.compile(r"^([A-Za-zÀ-ÖØ-Þ]\.)+$")
NICKNAME_RX = re.compile(r"\(([^)]+)\)")


def _is_init_token(tok: str) -> bool:
    return bool(INIT_TOKEN_RX.match(tok))


def _normalize_initials(tokens: list[str]) -> str:
    """`['J.C.', 'M.']` of `['J.', 'C.', 'M.']` -> `J.C.M.`.

    Dotted upper, schema-regex `^([A-ZÀ-ÖØ-Þ]\\.)+$`-proof.
    """
    letters = [c.upper() for tok in tokens for c in tok if c.isalpha()]
    return "".join(f"{c}." for c in letters)


def fix_record(data: dict) -> tuple[bool, dict[str, str] | None]:
    """Geef (changed, info) terug. Wijzigt `data["name"]` in-place.

    Detectie-criterium (CAT A uit issue #46), alle voorwaarden:
      1. `name.tussenvoegsel` afwezig/leeg.
      2. `name.given` = `<≥1 initiaal-token> <≥1 tussenvoegsel-token>`,
         waarbij de tussenvoegsel-tokens een aaneengesloten staart vormen.
      3. Ruis-uitsluiting: `name.family` / `name.full` bevatten geen
         `(...)` (partijnaam) en `name.full` geen verdubbeld tussenvoegsel
         (`van van`) — dat is het andere bug-patroon, buiten scope.
    """
    name = data.get("name")
    if not isinstance(name, dict):
        return False, None

    if (name.get("tussenvoegsel") or "").strip():
        return False, None  # CAT B / al gefixt

    given = (name.get("given") or "").strip()
    full = (name.get("full") or "").strip()
    family = (name.get("family") or "").strip()

    # Ruis-uitsluiting: partijnaam tussen haakjes of verdubbeld tussenvoegsel.
    if "(" in family or "(" in full:
        return False, None
    full_no_paren = NICKNAME_RX.sub("", full)
    full_toks_lc = full_no_paren.lower().split()
    for a, b in pairwise(full_toks_lc):
        if a == b and a in TUSSENVOEGSELS:
            return False, None

    toks = given.split()
    if len(toks) < 2:
        return False, None

    # Absorbeer de aaneengesloten tussenvoegsel-staart van rechts.
    tv_tokens: list[str] = []
    i = len(toks) - 1
    while i >= 0 and toks[i].lower() in TUSSENVOEGSELS:
        tv_tokens.insert(0, toks[i].lower())
        i -= 1
    if not tv_tokens:
        return False, None

    head = toks[: i + 1]
    if not head:
        return False, None

    # Veiligheidscheck: als de echte achternaam zelf een tussenvoegsel
    # lijkt, niet raden maar overslaan.
    first_fam = family.lower().split()[0] if family else ""
    if first_fam in TUSSENVOEGSELS:
        return False, None

    old_given = name.get("given")
    old_initials = name.get("initials")
    new_tussenvoegsel = " ".join(tv_tokens)

    if all(_is_init_token(t) for t in head):
        # Variant 1: kop is een pure initiaal-reeks (`J.C.M. Van`).
        # Roepnaam alleen uit een nickname tussen haakjes in `full`.
        new_initials = _normalize_initials(head)
        nick = NICKNAME_RX.search(full)
        new_given = nick.group(1).strip().title() if nick else None
    elif (
        len(head) == 1
        and head[0].replace("-", "").replace("'", "").isalpha()
        and "-" not in head[0]
    ):
        # Variant 2: kop is één schone roepnaam-token (`Aly van`).
        # `initials` herberekenen op de roepnaam-letter, zodat de
        # gelekte tussenvoegsel-letter (`A.V.`) verdwijnt.
        roepnaam = head[0]
        new_given = roepnaam.title()
        new_initials = f"{roepnaam[0].upper()}."
    else:
        # Samengestelde / rommelige kop (`Marieke Twigt-Van der`,
        # `Bart Vos - van`): niet raden, overslaan.
        return False, None

    name["tussenvoegsel"] = new_tussenvoegsel
    name["initials"] = new_initials
    if new_given:
        name["given"] = new_given
    else:
        name.pop("given", None)

    return True, {
        "given_old": str(old_given),
        "initials_old": str(old_initials),
        "tussenvoegsel": new_tussenvoegsel,
        "initials_new": new_initials,
        "given_new": new_given or "(verwijderd)",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/personen"),
        help="Persons-directory (default: data/personen).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rapporteer alleen, schrijf niet.",
    )
    args = parser.parse_args(argv)

    data_dir = args.data
    if not data_dir.exists():
        print(f"Personen-directory bestaat niet: {data_dir}", file=sys.stderr)
        return 2

    n_total = 0
    n_changed = 0
    samples: list[tuple[str, dict[str, str]]] = []

    for p in sorted(data_dir.glob("*.yaml")):
        n_total += 1
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue

        changed, info = fix_record(data)
        if not changed:
            continue
        n_changed += 1
        if len(samples) < 12 and info is not None:
            samples.append((p.name, info))
        if not args.dry_run:
            with p.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    print(f"Total persons scanned: {n_total}")
    print(f"Fixed: {n_changed}{'  (dry-run, nothing written)' if args.dry_run else ''}")
    print()
    print("Samples:")
    for fname, info in samples:
        print(f"  {fname}: given {info['given_old']!r} initials {info['initials_old']!r}")
        print(
            f"    -> tussenvoegsel {info['tussenvoegsel']!r}, "
            f"initials {info['initials_new']!r}, given {info['given_new']!r}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
