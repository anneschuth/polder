"""Diepe data-audit op `data/`.

Detecteert inconsistenties die de JSON-Schema-validator niet vangt:
chronologische impossibilities, orphan-refs, implausible birth-years,
placeholder-strings, quasi-duplicate personen, sentinel-dates uit Wikidata,
en meer.

Twee severity-niveaus:
- `error`: altijd een bug die opgelost moet worden.
- `review`: signaal dat mogelijk legitiem is, vraagt om menselijke check.

Bevindingen kunnen worden afgevinkt in `data/_audit/verified.yaml`. Een
geverifieerde finding wordt standaard uit de output gefilterd; gebruik
`--include-verified` om hem alsnog te tonen.

Eén entrypoint: `run_audit(data_dir)`. Geen IO buiten YAML-lezen; printing
doet de CLI.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import yaml

from polder.validate import _is_organisatie_bsn_safe_field

Severity = Literal["error", "review"]


@dataclass(frozen=True)
class Category:
    """Metadata over een audit-categorie."""

    name: str
    severity: Severity
    help: str


# Alle categorieën die `run_audit` kan retourneren. Gebruikt door
# `polder audit --explain` en bepaalt de sortering van de CLI-output.
CATEGORIES: dict[str, Category] = {
    # Errors: structurele data-bugs
    "yaml_parse_error": Category("yaml_parse_error", "error", "Bestand is geen geldig YAML."),
    "dup_id_orgs": Category("dup_id_orgs", "error", "Twee organisatie-records met dezelfde ID."),
    "dup_id_posts": Category("dup_id_posts", "error", "Twee post-records met dezelfde ID."),
    "dup_id_persons": Category("dup_id_persons", "error", "Twee person-records met dezelfde ID."),
    "start_after_end": Category(
        "start_after_end",
        "error",
        "Mandaat met start_date later dan end_date (chronologisch onmogelijk).",
    ),
    "orphan_org_ref": Category(
        "orphan_org_ref", "error", "Mandaat verwijst naar een niet-bestaande organization_id."
    ),
    "orphan_post_ref": Category(
        "orphan_post_ref", "error", "Mandaat verwijst naar een niet-bestaande post_id."
    ),
    "post_orphan_org": Category(
        "post_orphan_org", "error", "Post verwijst naar een niet-bestaande organization_id."
    ),
    "implausible_birth_year": Category(
        "implausible_birth_year",
        "error",
        "Geboortejaar buiten plausibele range (< 1700 of jonger dan 14 jaar).",
    ),
    "birth_year_not_int": Category("birth_year_not_int", "error", "Geboortejaar is geen integer."),
    "no_sources_orgs": Category("no_sources_orgs", "error", "Organisatie zonder sources[] entry."),
    "no_sources_persons": Category(
        "no_sources_persons", "error", "Persoon zonder sources[] entry."
    ),
    # Geen `no_sources_posts`: posts zijn afgeleide entiteiten zonder eigen bron.
    # Het schema vereist geen sources op posts.
    "source_no_url_orgs": Category(
        "source_no_url_orgs", "error", "Source-entry in organisatie zonder url."
    ),
    "source_no_url_persons": Category(
        "source_no_url_persons", "error", "Source-entry in persoon zonder url."
    ),
    "source_no_retrieved_orgs": Category(
        "source_no_retrieved_orgs", "error", "Source-entry in organisatie zonder retrieved-datum."
    ),
    "source_no_retrieved_persons": Category(
        "source_no_retrieved_persons", "error", "Source-entry in persoon zonder retrieved-datum."
    ),
    "person_no_family_name": Category(
        "person_no_family_name", "error", "Persoon zonder name.family."
    ),
    "placeholder_in_orgs": Category(
        "placeholder_in_orgs",
        "error",
        "Placeholder-string (onbekend/unknown/null/todo) in organisatie-veld.",
    ),
    "placeholder_in_posts": Category(
        "placeholder_in_posts", "error", "Placeholder-string in post-veld."
    ),
    "placeholder_in_persons": Category(
        "placeholder_in_persons", "error", "Placeholder-string in persoon-veld."
    ),
    "confidence_out_of_range": Category(
        "confidence_out_of_range", "error", "Mandaat-confidence buiten [0, 1] range."
    ),
    "mandaat_org_post_mismatch": Category(
        "mandaat_org_post_mismatch",
        "error",
        "Mandaat-organization_id strookt niet met post.organization_id.",
    ),
    "mandaat_before_age_18": Category(
        "mandaat_before_age_18",
        "error",
        "Mandaat begint voordat de persoon 18 was. Vaak Wikidata-sentinel 1945-01-01.",
    ),
    "mandaat_no_sources": Category(
        "mandaat_no_sources", "error", "Mandaat zonder sources[] entry."
    ),
    "bsn_in_text": Category(
        "bsn_in_text",
        "error",
        "9-cijferige reeks in een tekstveld die op een BSN lijkt. Polder's harde regel.",
    ),
    "mandaat_evidence_missing": Category(
        "mandaat_evidence_missing",
        "error",
        "Apply-staging mandaat zonder appointment of evidence_snippet. Schendt quote-or-die.",
    ),
    "cyclic_parent_org": Category(
        "cyclic_parent_org", "error", "Cyclus in parent_id-keten van organisaties."
    ),
    "successor_predecessor_mismatch": Category(
        "successor_predecessor_mismatch",
        "error",
        "Org X heeft successor_id=Y, maar Y's predecessor_id bevat X niet (of vice versa).",
    ),
    "dead_person_active_mandate": Category(
        "dead_person_active_mandate",
        "error",
        "Persoon heeft death.year vóór een end_date, of overleden persoon heeft actief mandaat.",
    ),
    # Review: mogelijk legitiem, vraagt om menselijke check
    "mandaat_after_age_100": Category(
        "mandaat_after_age_100",
        "review",
        "Mandaat begint na de 100e verjaardag. Mogelijk een data-fout.",
    ),
    "start_in_future": Category(
        "start_in_future",
        "review",
        "Mandaat-start_date ligt in de toekomst. Kan valide zijn (aangekondigde benoeming).",
    ),
    "quasi_dup_family_birth": Category(
        "quasi_dup_family_birth",
        "review",
        "Twee personen met dezelfde family-name en birth-year (kan naamgenoten zijn).",
    ),
    "quasi_dup_family_no_birth": Category(
        "quasi_dup_family_no_birth",
        "review",
        "Twee personen met dezelfde family-name waarvan minstens een zonder birth-year.",
    ),
    "quasi_dup_initials_prefix": Category(
        "quasi_dup_initials_prefix",
        "review",
        "Twee personen met dezelfde family-name waarvan initialen prefix van elkaar zijn.",
    ),
    "mandaat_longer_than_30y": Category(
        "mandaat_longer_than_30y",
        "review",
        "Mandaat van meer dan 30 jaar als enkele entry. Mogelijk een vergeten end_date.",
    ),
    "post_parent_level_mismatch": Category(
        "post_parent_level_mismatch",
        "review",
        "Post-classification past niet bij parent-organisatie-type. Een "
        "abd-directeur hoort onder een directie of agentschap, geen "
        "ministerie. Bewindspersonen onder ministeries.",
    ),
    "overlapping_open_mandates_different_orgs": Category(
        "overlapping_open_mandates_different_orgs",
        "review",
        "Persoon heeft twee open mandaten (end_date=null) bij organisaties "
        "die niet in dezelfde parent-keten zitten. Vaak een vergeten "
        "end_date op het eerste mandaat toen de persoon naar een nieuwe "
        "functie ging. Soms legitiem (parallelle rollen).",
    ),
    "single_seat_both_open": Category(
        "single_seat_both_open",
        "review",
        "Twee personen hebben beide een open mandaat (end_date=null) op "
        "dezelfde single-seat post (bv. burgemeester, DG, directeur, SG). "
        "Bijna altijd een vergeten end_date op de uitgaande ambtenaar, "
        "of twee person-records voor dezelfde persoon (dup).",
    ),
    "dup_org_identifier": Category(
        "dup_org_identifier",
        "review",
        "Twee organisatie-records delen een tooi/oin/roo_id maar hebben "
        "verschillende `id`. Bijna altijd dezelfde organisatie twee keer "
        "ingelezen door verschillende fetchers (scraper-slug vs "
        "ROO-naam-slug vs afkorting-slug). Merge naar één canoniek record.",
    ),
    "dup_post_role_org": Category(
        "dup_post_role_org",
        "review",
        "Twee post-records met dezelfde organization_id en (genormaliseerd) "
        "dezelfde rol/label. Vaak één functie meerdere keren aangemaakt door "
        "verschillende bronnen. Soms legitiem (echt twee zetels).",
    ),
    "dup_mandate": Category(
        "dup_mandate",
        "review",
        "Persoon heeft twee mandaten die identiek zijn op "
        "(organization_id, start_date, end_date) en op post of rol. "
        "Vrijwel altijd hetzelfde dienstverband dubbel ingevoerd door "
        "twee bronnen (bv. Wikidata + tk_odata) onder twee post-ids.",
    ),
    # ROO-superset checks (Phase 5). Deze checks vergelijken polder-data met
    # het laatste ROO-export-XML in `_cache/`. Ze worden geskipt als er geen
    # cache-bestand staat — dan run je `polder roo fetch` eerst.
    "roo_missing_org": Category(
        "roo_missing_org",
        "error",
        "Organisatie staat in ROO-export-XML maar niet in data/organisaties/. "
        "Polder claimt een ROO-superset te zijn; dit zou niet voor mogen komen.",
    ),
    "roo_field_drift": Category(
        "roo_field_drift",
        "review",
        "Organisatie's `last_mutation` is ouder bij ons dan in ROO. "
        "Betekent dat er een daily fetcher-run gemist is.",
    ),
    "roo_stale_appointment": Category(
        "roo_stale_appointment",
        "review",
        "ROO noemt een medewerker die volgens polder al uit functie is "
        "(end_date in mandaat). Vereist een geresolvde staging-file "
        "(`polder roo functies` + `polder roo resolve`); "
        "checker leest `data/_staging/roo-functies-*.resolved.json`.",
    ),
}


@dataclass(frozen=True)
class Finding:
    """Eén bevinding uit de audit."""

    category: str
    key: str
    message: str

    @property
    def severity(self) -> Severity:
        cat = CATEGORIES.get(self.category)
        return cat.severity if cat else "error"


@dataclass
class AuditReport:
    """Resultaat van een complete audit-run."""

    findings: list[Finding] = field(default_factory=list)
    verified_skipped: int = 0

    def by_category(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            out[f.category].append(f)
        return dict(out)

    def by_severity(self) -> dict[Severity, list[Finding]]:
        out: dict[Severity, list[Finding]] = defaultdict(list)
        for f in self.findings:
            out[f.severity].append(f)
        return dict(out)


PLACEHOLDER_RX = re.compile(
    r"^(onbekend|unknown|null|none|todo|tbd|n\.?\s*v\.?\s*t\.?|nvt)$",
    re.IGNORECASE,
)

# Reeks van precies 9 cijfers die niet grenst aan een ander
# alfanumeriek teken. Een echte BSN staat op zichzelf; 9 cijfers
# midden in een hex-GUID (`...b88f-802843249aee1`) of langere code is
# geen BSN.
BSN_RX = re.compile(r"(?<![0-9A-Za-z])\d{9}(?![0-9A-Za-z])")

# Velden die we voor BSN scannen. Heuristisch: korte naam-velden, niet
# identifiers (waar OIN-achtige 18-cijfer-codes wel mogen).
BSN_SCAN_FIELDS = ("name", "label", "role", "note", "notes", "comment", "comments")


def _safe_load_yaml(path: Path) -> tuple[dict | None, str | None]:
    """Lees YAML, geef (data, error_message) terug."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data, None
    except yaml.YAMLError as e:
        return None, str(e)
    except OSError as e:
        return None, str(e)


def _load_all(
    data_dir: Path,
) -> tuple[
    list[tuple[Path, dict]],
    list[tuple[Path, dict]],
    list[tuple[Path, dict]],
    list[tuple[Path, str]],
]:
    orgs, posts, persons = [], [], []
    parse_errors: list[tuple[Path, str]] = []

    for glob, target in (
        ("organisaties/**/*.yaml", orgs),
        ("posten/**/*.yaml", posts),
        ("personen/**/*.yaml", persons),
    ):
        for p in data_dir.glob(glob):
            if any(part == "_audit" for part in p.parts):
                continue
            data, err = _safe_load_yaml(p)
            if err:
                parse_errors.append((p, err))
                continue
            if not isinstance(data, dict):
                continue
            target.append((p, data))

    return orgs, posts, persons, parse_errors


# ---------------------------------------------------------------------------
# Verified-findings whitelist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedEntry:
    category: str
    key: str
    note: str | None = None
    verified_at: str | None = None
    verified_by: str | None = None


def load_verified(data_dir: Path) -> set[tuple[str, str]]:
    """Lees `data/_audit/verified.yaml` en geef set van (category, key) terug."""
    path = data_dir / "_audit" / "verified.yaml"
    if not path.exists():
        return set()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    entries = raw.get("verified") or []
    out: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cat = entry.get("category")
        key = entry.get("key")
        if cat and key:
            out.add((str(cat), str(key)))
    return out


# ---------------------------------------------------------------------------
# Run audit
# ---------------------------------------------------------------------------


def run_audit(
    data_dir: Path,
    *,
    today: str | None = None,
    apply_whitelist: bool = True,
) -> AuditReport:
    """Voer de hele audit uit en retourneer een AuditReport."""
    today = today or date.today().isoformat()
    orgs, posts, persons, parse_errors = _load_all(data_dir)

    findings: list[Finding] = []

    for path, err in parse_errors:
        findings.append(Finding("yaml_parse_error", path.name, f"{path.name}: {err}"))

    all_org_ids: set[str] = set()
    all_post_ids: set[str] = set()
    all_person_ids: set[str] = set()
    post_to_org: dict[str, str] = {}
    org_parent: dict[str, str] = {}
    for _, d in orgs:
        oid = d.get("id")
        if oid:
            all_org_ids.add(oid)
            if d.get("parent_id"):
                org_parent[oid] = d["parent_id"]
    for _, d in posts:
        if d.get("id"):
            all_post_ids.add(d["id"])
            if d.get("organization_id"):
                post_to_org[d["id"]] = d["organization_id"]
    for _, d in persons:
        if d.get("id"):
            all_person_ids.add(d["id"])

    _check_dup_ids(orgs, "orgs", findings)
    _check_dup_ids(posts, "posts", findings)
    _check_dup_ids(persons, "persons", findings)

    for p, d in persons:
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            _check_mandaat(p, d, m, today, all_org_ids, all_post_ids, post_to_org, findings)

    for p, d in posts:
        org = d.get("organization_id")
        if org and org not in all_org_ids:
            findings.append(Finding("post_orphan_org", p.name, f"{p.name}: org_id={org}"))

    _check_birth_year(persons, findings)
    _check_sources(orgs, "orgs", findings)
    _check_sources(persons, "persons", findings)
    # Posts hebben geen sources volgens schema: ze zijn afgeleid uit
    # mandaten en organisaties. _check_sources slaan we daarom over.
    _check_person_name(persons, findings)
    _check_placeholders(orgs, "orgs", findings)
    _check_placeholders(posts, "posts", findings)
    _check_placeholders(persons, "persons", findings)
    _check_bsn(persons, "persons", findings)
    _check_bsn(orgs, "orgs", findings)
    _check_bsn(posts, "posts", findings)

    _check_quasi_dup_persons(persons, findings)
    _check_cyclic_parents(org_parent, findings)
    _check_successor_predecessor(orgs, findings)
    _check_dead_persons(persons, findings)
    _check_mandate_length(persons, findings)
    _check_mandate_evidence(persons, findings)
    _check_post_parent_level(orgs, posts, findings)
    _check_ministerie_direct_children(orgs, findings)
    _check_ministerie_direct_posts(posts, findings)
    _check_overlapping_open_mandates(persons, org_parent, findings)
    _check_single_seat_both_open(persons, posts, findings)
    _check_dup_org_identifier(orgs, findings)
    _check_dup_post_role_org(posts, findings)
    _check_dup_mandate(persons, findings)

    # ROO-superset checks (Phase 5). Skip stilletjes als geen XML-cache.
    cache = _latest_roo_cache(data_dir.parent)
    if cache is not None:
        _check_roo_superset(orgs, cache, findings)
    # Stale-appointment check leest een resolved staging-file (Phase 3).
    _check_roo_stale_appointments(persons, data_dir / "_staging", findings)

    report = AuditReport(findings=findings)

    if apply_whitelist:
        verified = load_verified(data_dir)
        if verified:
            kept: list[Finding] = []
            skipped = 0
            for f in report.findings:
                if (f.category, f.key) in verified:
                    skipped += 1
                    continue
                kept.append(f)
            report = AuditReport(findings=kept, verified_skipped=skipped)

    return report


# ---------------------------------------------------------------------------
# Per-check helpers
# ---------------------------------------------------------------------------


def _check_dup_ids(items: list[tuple[Path, dict]], label: str, findings: list[Finding]) -> None:
    c: Counter[str] = Counter()
    for _, d in items:
        if d.get("id"):
            c[d["id"]] += 1
    cat = f"dup_id_{label}"
    for dup_id, n in c.items():
        if n > 1:
            findings.append(Finding(cat, dup_id, f"{dup_id} ({n}x)"))


def _normalize_label(text: str | None) -> str:
    """Lowercase, strip non-alphanumerics. 'minister van X' en 'Minister voor
    X' collapsen niet (van/voor blijft), maar casing/punctuatie wel."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _check_dup_org_identifier(orgs: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Twee org-records met verschillende `id` maar een gedeelde
    organisatie-specifieke identifier (tooi/roo_id/owms).

    Verschillende fetchers (scraper, ROO-naam, afkorting) maken elk een eigen
    slug; deze check vangt dat de `dup_id_*`-check mist omdat de ids verschillen.

    Bewust *niet* op oin/rsin/kvk: dat zijn rechtspersoon-identifiers, geen
    organisatie-identifiers. In het Nederlandse overheidsmodel valt een
    adviescollege, inspectie, RWT of onderdeel juridisch onder de
    rechtspersoon van zijn moederorganisatie en deelt daarom diens
    oin/rsin/kvk (ROO modelleert dit zo: AWTI/Inspectie Onderwijs delen de
    OCW-OIN, Werkse! deelt de OIN van gemeente Delft). Dat is geen duplicaat.
    Een gedeelde tooi-code (oorg####/gm####/mnre####/ws####) of roo_id wijst
    wél op hetzelfde organisatie-record onder twee slugs. owms valt af: er
    staat een placeholder-waarde "XXXXX" in tientallen GR-records, en owms
    is sowieso een naam-slug die niet uniek per organisatie hoeft te zijn.
    """
    # (identifier-veld, waarde) -> lijst van (org_id, bestandsnaam)
    by_ident: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for p, d in orgs:
        oid = d.get("id")
        idents = d.get("identifiers")
        if not oid or not isinstance(idents, dict):
            continue
        for field_name in ("tooi", "roo_id"):
            val = idents.get(field_name)
            if val:
                by_ident[(field_name, str(val))].append((oid, p.name))
    for (field_name, val), members in sorted(by_ident.items()):
        distinct_ids = {oid for oid, _ in members}
        if len(distinct_ids) > 1:
            ids_str = ", ".join(sorted(distinct_ids))
            findings.append(
                Finding(
                    "dup_org_identifier",
                    f"{field_name}|{val}",
                    f"{field_name}={val} gedeeld door {len(distinct_ids)} org-records: {ids_str}",
                )
            )


def _check_dup_post_role_org(posts: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Twee post-records, zelfde organization_id, genormaliseerd dezelfde
    rol/label. Eén functie dubbel aangemaakt door verschillende bronnen."""
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for _, d in posts:
        pid = d.get("id")
        org = d.get("organization_id")
        label = _normalize_label(d.get("label") or d.get("role"))
        if not pid or not org or not label:
            continue
        by_key[(str(org), label)].append(str(pid))
    for (org, label), pids in sorted(by_key.items()):
        distinct = sorted(set(pids))
        if len(distinct) > 1:
            findings.append(
                Finding(
                    "dup_post_role_org",
                    f"{org}|{label}",
                    f"{org} heeft {len(distinct)} posts met zelfde rol: {', '.join(distinct)}",
                )
            )


def _check_dup_mandate(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Twee mandaten binnen één persoon die identiek zijn op
    (organization_id, start_date, end_date) plus post of rol. Hetzelfde
    dienstverband dubbel ingevoerd onder twee post-ids."""
    for p, d in persons:
        mandates = d.get("mandates")
        if not isinstance(mandates, list):
            continue
        seen: dict[tuple, str] = {}
        for m in mandates:
            if not isinstance(m, dict):
                continue
            org = m.get("organization_id")
            start = m.get("start_date")
            end = m.get("end_date")
            post = m.get("post_id")
            role = _normalize_label(m.get("role"))
            if not org or not start:
                continue
            # Match op org+periode+post, en (los daarvan) op org+periode+rol.
            for variant in ((org, start, end, "post", post), (org, start, end, "role", role)):
                if variant[-1] in (None, ""):
                    continue
                if variant in seen:
                    findings.append(
                        Finding(
                            "dup_mandate",
                            f"{d.get('id', p.name)}|{org}|{start}",
                            f"{p.name}: dubbel mandaat {org} {start}->{end} "
                            f"({variant[3]}={variant[4]})",
                        )
                    )
                else:
                    seen[variant] = m.get("id", "?")


def _check_mandaat(
    p: Path,
    d: dict,
    m: dict,
    today: str,
    all_org_ids: set[str],
    all_post_ids: set[str],
    post_to_org: dict[str, str],
    findings: list[Finding],
) -> None:
    s = m.get("start_date")
    e = m.get("end_date")
    mid = m.get("id", "?")
    pid = d.get("id", p.name)
    key = f"{pid}|{mid}"

    if isinstance(s, str) and isinstance(e, str) and s > e:
        findings.append(Finding("start_after_end", key, f"{p.name}: {mid} start={s} end={e}"))

    if isinstance(s, str) and s > today:
        findings.append(Finding("start_in_future", key, f"{p.name}: {mid} start={s}"))

    birth = d.get("birth")
    if isinstance(birth, dict) and isinstance(birth.get("year"), int) and isinstance(s, str):
        try:
            start_year = int(s[:4])
            age = start_year - birth["year"]
            if age < 18:
                findings.append(
                    Finding(
                        "mandaat_before_age_18",
                        key,
                        f"{p.name}: birth={birth['year']} start={s} ({age}y old)",
                    )
                )
            elif age > 100:
                findings.append(
                    Finding(
                        "mandaat_after_age_100",
                        key,
                        f"{p.name}: birth={birth['year']} start={s} ({age}y old)",
                    )
                )
        except (ValueError, TypeError):
            pass

    org = m.get("organization_id")
    post = m.get("post_id")
    if org and org not in all_org_ids:
        findings.append(Finding("orphan_org_ref", key, f"{p.name}: org_id={org}"))
    if post and post not in all_post_ids:
        findings.append(Finding("orphan_post_ref", key, f"{p.name}: post_id={post}"))

    if org and post and post in post_to_org and post_to_org[post] != org:
        findings.append(
            Finding(
                "mandaat_org_post_mismatch",
                key,
                f"{p.name}: m.org={org} post.org={post_to_org[post]} post={post}",
            )
        )

    c = m.get("confidence")
    if isinstance(c, int | float) and (c < 0 or c > 1):
        findings.append(Finding("confidence_out_of_range", key, f"{p.name}: confidence={c}"))

    if not (m.get("sources") or []):
        findings.append(Finding("mandaat_no_sources", key, f"{p.name}: {mid}"))


def _check_birth_year(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    # Plausibele bovengrens: vandaag - 14 jaar (jongste mogelijke politiek-actieve
    # leeftijd met marge). Ondergrens 1700 (vroege Nederlandse Republiek; ouder
    # is bijna zeker dataset-corruptie of een verkeerde verwerking van een
    # eeuwoud Wikidata-feit).
    max_year = date.today().year - 14
    for p, d in persons:
        birth = d.get("birth")
        if not isinstance(birth, dict) or birth.get("year") is None:
            continue
        y = birth["year"]
        pid = d.get("id", p.name)
        if not isinstance(y, int):
            findings.append(Finding("birth_year_not_int", pid, f"{p.name}: birth.year={y!r}"))
            continue
        if y < 1700 or y > max_year:
            findings.append(Finding("implausible_birth_year", pid, f"{p.name}: birth.year={y}"))


def _check_sources(items: list[tuple[Path, dict]], label: str, findings: list[Finding]) -> None:
    cat_no = f"no_sources_{label}"
    cat_url = f"source_no_url_{label}"
    cat_ret = f"source_no_retrieved_{label}"
    for p, d in items:
        rid = d.get("id", p.name)
        srcs = d.get("sources") or []
        if not srcs:
            findings.append(Finding(cat_no, rid, p.name))
        for src in srcs:
            if not isinstance(src, dict):
                continue
            src_id = src.get("id", "?")
            if not src.get("url"):
                findings.append(Finding(cat_url, f"{rid}|{src_id}", f"{p.name}: {src_id}"))
            if not src.get("retrieved"):
                findings.append(Finding(cat_ret, f"{rid}|{src_id}", f"{p.name}: {src_id}"))


def _check_person_name(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    for p, d in persons:
        name = d.get("name")
        if not isinstance(name, dict) or not name.get("family"):
            findings.append(Finding("person_no_family_name", d.get("id", p.name), p.name))


def _check_placeholders(
    items: list[tuple[Path, dict]], label: str, findings: list[Finding]
) -> None:
    cat = f"placeholder_in_{label}"
    for p, d in items:
        rid = d.get("id", p.name)
        for field_name in ("id", "name", "label", "organization_id", "post_id"):
            val = d.get(field_name)
            if isinstance(val, str) and PLACEHOLDER_RX.match(val.strip()):
                findings.append(
                    Finding(cat, f"{rid}|{field_name}", f"{p.name}: {field_name}={val!r}")
                )
        if isinstance(d.get("name"), dict):
            for nk, nv in d["name"].items():
                if isinstance(nv, str) and PLACEHOLDER_RX.match(nv.strip()):
                    findings.append(Finding(cat, f"{rid}|name.{nk}", f"{p.name}: name.{nk}={nv!r}"))


def _scan_for_bsn(value: object, path: str = "", *, is_org: bool = False) -> list[tuple[str, str]]:
    """Yield (field-path, matched-9-digit-string) voor strings die op BSN lijken.

    Skip velden waar 9-cijferige reeksen legitiem zijn:
    - `identifiers.*` (OIN, KvK, RSIN, tk_persoon_id, etc)
    - `sources[].url` (URLs bevatten vaak ORI-ID's etc)
    - `sources[].id` en `sources[].retrieved`
    - `appointment.staatscourant_url` etc

    Voor organisatie-records geldt daarnaast dezelfde structurele
    allowlist als de validator (`_is_organisatie_bsn_safe_field`):
    telefoonnummers, wettelijke grondslagen, gr_meta, afspraak, woo.
    Persoon- en post-records worden onverkort gescand (echte
    BSN-bescherming).
    """
    hits: list[tuple[str, str]] = []
    # Skip hele subtrees onder bekende identifier/URL-velden
    skipped_keys = {"identifiers", "url", "id", "retrieved", "kvk", "oin", "rsin"}
    if isinstance(value, str):
        if not (is_org and _is_organisatie_bsn_safe_field(path)):
            for m in BSN_RX.finditer(value):
                hits.append((path, m.group(0)))
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in skipped_keys:
                continue
            sub = f"{path}.{k}" if path else str(k)
            hits.extend(_scan_for_bsn(v, sub, is_org=is_org))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            hits.extend(_scan_for_bsn(item, f"{path}[{i}]", is_org=is_org))
    return hits


def _check_bsn(items: list[tuple[Path, dict]], label: str, findings: list[Finding]) -> None:
    is_org = label == "orgs"
    for p, d in items:
        rid = d.get("id", p.name)
        for field_path, match in _scan_for_bsn(d, is_org=is_org):
            findings.append(
                Finding(
                    "bsn_in_text",
                    f"{rid}|{field_path}",
                    f"{p.name}: {field_path}={match!r}",
                )
            )


def _check_quasi_dup_persons(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Drie quasi-dup-checks: family+birth, family-only, initials-prefix.

    Voor `family_no_birth` zijn we strenger geworden: alleen rapporteren als
    er échte ambiguïteit is over of records dezelfde persoon zijn. Vier
    duidelijk-verschillende voornamen onder dezelfde family triggert niets
    (zes verschillende Janssens in een gemeenteraad zijn duidelijk
    verschillende personen).
    """
    # Indexen
    by_family: dict[str, list[tuple[str, str | None, str | None, int | None]]] = defaultdict(list)
    for p, d in persons:
        name = d.get("name") or {}
        birth = d.get("birth") or {}
        family = name.get("family")
        if not family:
            continue
        initials = name.get("initials")
        given = name.get("given")
        y = birth.get("year") if isinstance(birth.get("year"), int) else None
        by_family[str(family).lower()].append((p.name, initials, given, y))

    for family, entries in by_family.items():
        if len(entries) < 2:
            continue

        # 1. family + birth-year: zelfde sleutel
        fam_year_groups: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
        for fname, initials, _given, y in entries:
            if y is not None:
                fam_year_groups[y].append((fname, initials))
        for y, group in fam_year_groups.items():
            if len(group) > 1:
                key = f"{family}|{y}"
                desc = ", ".join(f"{f} (initials={i!r})" for f, i in group[:4])
                findings.append(
                    Finding(
                        "quasi_dup_family_birth",
                        key,
                        f"({family!r}, {y}): {len(group)} records -> {desc}",
                    )
                )

        # 2. family-only met ambigue voornamen.
        # Voor records waar minstens 1 geen birth-year heeft EN minstens 1 paar
        # ambigue is (zelfde given, prefix-match, of een ervan zonder given).
        # Zonder ambiguïteit (alle voornamen duidelijk verschillend) niet rapporteren.
        without_year = [e for e in entries if e[3] is None]
        if without_year:
            ambiguous_pair = _find_ambiguous_given_pair(entries)
            if ambiguous_pair:
                files = sorted({e[0] for e in entries})
                key = f"{family}|{'|'.join(files)}"
                desc = ", ".join(f"{f} (initials={i!r}, given={g!r})" for f, i, g, _ in entries[:4])
                findings.append(
                    Finding(
                        "quasi_dup_family_no_birth",
                        key,
                        f"({family!r}, mixed birth): {len(entries)} records -> {desc}",
                    )
                )

        # 3. initials-prefix-conflict: A's initialen zijn prefix van B's, of vice versa.
        # Skip als de given-names duidelijk verschillen (geen prefix-match).
        normalized = [
            (fname, _normalize_initials_compact(initials), given)
            for fname, initials, given, _ in entries
            if initials
        ]
        for i, (fa, ia, ga) in enumerate(normalized):
            for fb, ib, gb in normalized[i + 1 :]:
                if not ia or not ib or ia == ib:
                    continue
                if not (ia.startswith(ib) or ib.startswith(ia)):
                    continue
                # Initialen-prefix-match. Maar als beide given-names bekend zijn
                # en duidelijk verschillen, is het waarschijnlijk geen dup.
                if ga and gb and not _given_names_compatible(ga, gb):
                    continue
                files = sorted([fa, fb])
                key = f"{family}|{'|'.join(files)}"
                findings.append(
                    Finding(
                        "quasi_dup_initials_prefix",
                        key,
                        f"({family!r}): {fa} (initials={ia!r}) vs {fb} (initials={ib!r})",
                    )
                )


def _given_compact(given: str | None) -> str:
    if not given:
        return ""
    return re.sub(r"[^a-z]+", "", given.lower())


def _given_names_compatible(a: str, b: str) -> bool:
    """True als a en b waarschijnlijk dezelfde voornaam beschrijven.

    Compatibel:
    - Exact gelijk na normalisatie.
    - Een is prefix van de ander (bv. "W." vs "Wopke", "Wim" vs "Wim H.").
    """
    ca = _given_compact(a)
    cb = _given_compact(b)
    if not ca or not cb:
        return True  # onbekend = mogelijk compatibel
    if ca == cb:
        return True
    if ca.startswith(cb) or cb.startswith(ca):
        return True
    return False


def _find_ambiguous_given_pair(
    entries: list[tuple[str, str | None, str | None, int | None]],
) -> tuple[str, str] | None:
    """Geef een paar (fname_a, fname_b) terug dat ambigue is qua voornaam.

    Ambigu = zelfde compact-given, of prefix-match, of een ervan heeft geen
    given-name. Records zonder family-name filtert _check_quasi_dup_persons
    al weg, dus die zien we hier niet.
    """
    from itertools import combinations

    for (fa, _ia, ga, _ya), (fb, _ib, gb, _yb) in combinations(entries, 2):
        ca = _given_compact(ga)
        cb = _given_compact(gb)
        if not ca or not cb:
            return (fa, fb)
        if ca == cb:
            return (fa, fb)
        if ca.startswith(cb) or cb.startswith(ca):
            return (fa, fb)
    return None


def _normalize_initials_compact(initials: str | None) -> str:
    """Verwijder punten en spaces, lowercase. 'W.B.' -> 'wb'."""
    if not initials:
        return ""
    return re.sub(r"[^a-zA-Z]", "", initials).lower()


def _check_cyclic_parents(org_parent: dict[str, str], findings: list[Finding]) -> None:
    """Detecteer cycli in parent_id-keten.

    Rapporteert elke cycle één keer, gefingerprint op de gesorteerde set van
    knopen in de cycle zelf (niet op approach-paden die naar de cycle leiden).
    Zo krijgen we voor `A -> B -> C -> C` één finding op {C} (self-loop), niet
    drie findings op A, B en C.
    """
    reported: set[frozenset[str]] = set()
    for start in org_parent:
        seen = []
        current = start
        steps = 0
        while current in org_parent and steps < 100:
            seen.append(current)
            current = org_parent[current]
            if current in seen:
                cycle_nodes = [*seen[seen.index(current) :], current]
                fingerprint = frozenset(cycle_nodes)
                if fingerprint in reported:
                    break
                reported.add(fingerprint)
                cycle_str = " -> ".join(cycle_nodes)
                # Key is alphabetisch eerste knoop in de cycle voor stabiliteit
                key = sorted(fingerprint)[0]
                findings.append(Finding("cyclic_parent_org", key, f"cycle: {cycle_str}"))
                break
            steps += 1


def _check_successor_predecessor(orgs: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Bidirectionele consistentie: X.successor_id=Y <=> Y.predecessor_id bevat X."""
    by_id: dict[str, dict] = {}
    for _, d in orgs:
        if d.get("id"):
            by_id[d["id"]] = d

    for oid, d in by_id.items():
        succ = d.get("successor_id")
        if succ:
            target = by_id.get(succ)
            if target is None:
                findings.append(
                    Finding(
                        "successor_predecessor_mismatch",
                        f"{oid}|missing-target",
                        f"{oid}: successor_id={succ} bestaat niet",
                    )
                )
            else:
                preds = target.get("predecessor_id") or []
                if oid not in preds:
                    findings.append(
                        Finding(
                            "successor_predecessor_mismatch",
                            f"{oid}|{succ}",
                            f"{oid} -> successor_id={succ}, maar {succ}.predecessor_id={preds}",
                        )
                    )

        preds = d.get("predecessor_id") or []
        for pred in preds:
            target = by_id.get(pred)
            if target is None:
                findings.append(
                    Finding(
                        "successor_predecessor_mismatch",
                        f"{oid}|missing-pred-{pred}",
                        f"{oid}: predecessor_id bevat {pred} maar dat bestaat niet",
                    )
                )
            else:
                if target.get("successor_id") != oid:
                    findings.append(
                        Finding(
                            "successor_predecessor_mismatch",
                            f"{pred}|{oid}",
                            f"{oid}.predecessor_id bevat {pred}, maar {pred}.successor_id={target.get('successor_id')}",
                        )
                    )


def _check_dead_persons(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Persoon met death.year die nog actieve mandaten (end_date=null) heeft, of
    mandaten waarvan de end_date na het sterfjaar ligt."""
    for p, d in persons:
        death = d.get("death")
        if not isinstance(death, dict):
            continue
        death_year = death.get("year")
        if not isinstance(death_year, int):
            continue
        pid = d.get("id", p.name)
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("id", "?")
            e = m.get("end_date")
            if e is None:
                findings.append(
                    Finding(
                        "dead_person_active_mandate",
                        f"{pid}|{mid}",
                        f"{p.name}: overleden {death_year}, mandaat {mid} heeft end_date=null",
                    )
                )
            elif isinstance(e, str):
                try:
                    end_year = int(e[:4])
                    if end_year > death_year:
                        findings.append(
                            Finding(
                                "dead_person_active_mandate",
                                f"{pid}|{mid}",
                                f"{p.name}: overleden {death_year}, mandaat eindigt {e}",
                            )
                        )
                except ValueError:
                    pass


def _check_mandate_length(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Mandaten met (end - start) > 30 jaar zijn verdacht (vergeten end_date?)."""
    for p, d in persons:
        pid = d.get("id", p.name)
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            s = m.get("start_date")
            e = m.get("end_date")
            if not (isinstance(s, str) and isinstance(e, str)):
                continue
            try:
                start_year = int(s[:4])
                end_year = int(e[:4])
            except ValueError:
                continue
            if end_year - start_year > 30:
                mid = m.get("id", "?")
                findings.append(
                    Finding(
                        "mandaat_longer_than_30y",
                        f"{pid}|{mid}",
                        f"{p.name}: {mid} {s}..{e} ({end_year - start_year}y)",
                    )
                )


def _check_mandate_evidence(persons: list[tuple[Path, dict]], findings: list[Finding]) -> None:
    """Apply-staging mandaten moeten verifieerbare evidence hebben.

    Acceptabel:
    - `appointment` veld (besluit-info), of
    - `evidence_snippet` in een source, of
    - source.url die naar een externe bron verwijst (URL bevat een geldig
      domein, niet `example.invalid` of leeg).
    """
    for p, d in persons:
        pid = d.get("id", p.name)
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            applied = False
            has_evidence = False
            has_valid_url = False
            for src in m.get("sources") or []:
                if not isinstance(src, dict):
                    continue
                fields = src.get("fields") or []
                if "applied_via:apply-staging" in fields:
                    applied = True
                if src.get("evidence_snippet"):
                    has_evidence = True
                url = src.get("url") or ""
                if (
                    isinstance(url, str)
                    and url.startswith("http")
                    and "example.invalid" not in url
                    and "example.com" not in url
                ):
                    has_valid_url = True
            if applied and not has_evidence and not m.get("appointment") and not has_valid_url:
                mid = m.get("id", "?")
                findings.append(
                    Finding(
                        "mandaat_evidence_missing",
                        f"{pid}|{mid}",
                        f"{p.name}: {mid}",
                    )
                )


# Welke parent-types verwachten we per post-classification. ABD-directeur
# en -afdelingshoofd horen onder een organisatieonderdeel (directie,
# afdeling, agentschap), niet rechtstreeks onder een ministerie. SG/DG
# (abd-tmg) mogen wel direct onder een ministerie. Bewindspersonen onder
# ministerie. Lijst is conservatief; warnings, geen errors.
_EXPECTED_PARENT_TYPES: dict[str, set[str]] = {
    "abd-tmg": {"ministerie", "agentschap", "zbo", "rwt", "hoge-college", "organisatieonderdeel"},
    "abd-directeur": {
        "organisatieonderdeel",
        "agentschap",
        "zbo",
        "rwt",
        "hoge-college",
        "inspectie",
        "adviescollege",
    },
    "abd-afdelingshoofd": {"organisatieonderdeel", "inspectie", "agentschap"},
    "abd-projectleider": {"organisatieonderdeel", "ministerie", "agentschap", "zbo"},
    "bewindspersoon": {"ministerie"},
}


def _check_post_parent_level(
    orgs: list[tuple[Path, dict]],
    posts: list[tuple[Path, dict]],
    findings: list[Finding],
) -> None:
    """Post-classification moet bij het organisatie-type passen.

    Een ``abd-directeur`` hoort onder een directie/organisatieonderdeel,
    geen ministerie direct. ``abd-afdelingshoofd`` hoort onder een
    afdeling of directie. ``bewindspersoon`` hoort onder een ministerie.

    Mismatch is een review-finding (geen error): bestaande data kan
    historische uitzonderingen bevatten.
    """
    org_type: dict[str, str] = {}
    for _, d in orgs:
        oid = d.get("id")
        if not oid:
            continue
        org_type[oid] = d.get("type") or d.get("classification") or "unknown"

    for p, d in posts:
        pid = d.get("id")
        cls = d.get("classification")
        org_id = d.get("organization_id")
        if not pid or not cls or not org_id:
            continue
        expected = _EXPECTED_PARENT_TYPES.get(cls)
        if not expected:
            continue
        actual = org_type.get(org_id, "unknown")
        if actual not in expected:
            findings.append(
                Finding(
                    "post_parent_level_mismatch",
                    f"{pid}|{actual}",
                    f"{p.name}: {pid} ({cls}) onder {org_id} (type={actual}), "
                    f"verwacht: {sorted(expected)}",
                )
            )


def _check_ministerie_direct_children(
    orgs: list[tuple[Path, dict]],
    findings: list[Finding],
) -> None:
    """Onder ``org:min-<x>`` hangt alleen de SG-cluster als organisatieonderdeel.

    De officiele rijksoverheid-organogrammen tonen onder elk ministerie
    twee dingen: bewindspersonen-posten (via mandaten, niet via parent_id)
    en de SG-cluster (``org:onderdeel-sg-<x>``). Alle DG's, directies en
    afdelingen hangen onder de SG-cluster, niet direct onder het ministerie.

    Uitzonderingen die NIET als organisatieonderdeel onder een ministerie
    horen maar als eigen organisatie-type: ZBO's, agentschappen, RWT's,
    adviescolleges, inspecties, hoge-colleges. Die worden hier niet geflagd
    want hun ``type`` is anders dan ``organisatieonderdeel``.

    Conservatieve check: review-finding, geen error. Bestaande data kan
    legitieme uitzonderingen bevatten (programma-directies, tijdelijke
    commissies).
    """
    for path, d in orgs:
        if d.get("type") != "organisatieonderdeel":
            continue
        org_id = d.get("id")
        parent_id = d.get("parent_id") or ""
        if not org_id or not parent_id.startswith("org:min-"):
            continue
        # Uitzondering: SG-cluster mag direct onder het ministerie hangen.
        if org_id.startswith("org:onderdeel-sg-"):
            continue
        findings.append(
            Finding(
                "ministerie_direct_onderdeel",
                org_id,
                f"{path.name}: {org_id} hangt direct onder {parent_id}; "
                f"verwacht is parent=org:onderdeel-sg-<x> (SG-cluster). "
                f"Uitzondering: reclassificeer naar zbo/agentschap/adviescollege "
                f"als de organisatie eigenstandig is.",
            )
        )


def _check_ministerie_direct_posts(
    posts: list[tuple[Path, dict]],
    findings: list[Finding],
) -> None:
    """Posten direct op ``org:min-<x>`` zijn alleen bewindspersoon-posten.

    Andere posten (SG, DG, directeur, afdelingshoofd) horen onder hun
    eigen organisatieonderdeel. Resolver heeft soms ABD-posten op het
    ministerie zelf geplaatst — die wijzen op een data-fout.
    """
    for path, d in posts:
        org_id = d.get("organization_id") or ""
        cls = d.get("classification") or ""
        pid = d.get("id")
        if not org_id.startswith("org:min-"):
            continue
        if cls == "bewindspersoon":
            continue
        findings.append(
            Finding(
                "ministerie_direct_post",
                pid or path.name,
                f"{path.name}: {pid} ({cls}) heeft organization_id={org_id}; "
                f"alleen bewindspersoon-posten horen direct op een ministerie. "
                f"Verplaats deze post naar het juiste organisatieonderdeel.",
            )
        )


def _org_chain(org_id: str, org_parent: dict[str, str]) -> list[str]:
    """Returns [org_id, parent, grandparent, ..., root]. Stops bij None of cyclus."""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = org_id
    while cur and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = org_parent.get(cur)
    return chain


def _orgs_in_same_chain(a: str, b: str, org_parent: dict[str, str]) -> bool:
    """True als a en b in dezelfde parent-keten zitten (een ancestor van de ander)."""
    if a == b:
        return True
    return a in _org_chain(b, org_parent) or b in _org_chain(a, org_parent)


def _check_overlapping_open_mandates(
    persons: list[tuple[Path, dict]],
    org_parent: dict[str, str],
    findings: list[Finding],
) -> None:
    """Twee open mandaten bij niet-verwante organisaties is verdacht.

    Patroon dat we vangen: persoon X heeft mandaat-1 (start=2020, end=null)
    bij ministerie A en mandaat-2 (start=2023, end=null) bij ministerie B.
    Vaak heeft de fetcher de eind-datum van het oude mandaat niet kunnen
    afleiden uit de aankondiging van het nieuwe.

    Skip: open mandaten bij organisaties in dezelfde parent-keten
    (sub-onderdeel naast hoofd-onderdeel) — daar zijn parallelle rollen
    realistisch.
    """
    for path, d in persons:
        pid = d.get("id", path.name)
        open_mandates = [
            m
            for m in d.get("mandaten") or []
            if isinstance(m, dict) and m.get("end_date") is None and m.get("organization_id")
        ]
        if len(open_mandates) < 2:
            continue
        # Pair-wise check. Voor de meeste personen zijn dit 2 mandaten,
        # dus geen kwadratische blow-up.
        for i in range(len(open_mandates)):
            for j in range(i + 1, len(open_mandates)):
                a, b = open_mandates[i], open_mandates[j]
                a_org = a["organization_id"]
                b_org = b["organization_id"]
                if _orgs_in_same_chain(a_org, b_org, org_parent):
                    continue
                # Stable key: sorted org-pair zodat we elke paar maar één keer flaggen.
                key = f"{pid}|{min(a_org, b_org)}|{max(a_org, b_org)}"
                findings.append(
                    Finding(
                        "overlapping_open_mandates_different_orgs",
                        key,
                        f"{path.name}: open mandate bij {a_org} (start={a.get('start_date')}) "
                        f"en bij {b_org} (start={b.get('start_date')}); "
                        f"check of eerste een vergeten end_date heeft",
                    )
                )


def _check_single_seat_both_open(
    persons: list[tuple[Path, dict]],
    posts: list[tuple[Path, dict]],
    findings: list[Finding],
) -> None:
    """Twee personen met beide een OPEN mandaat op dezelfde single-seat post.

    Sterker patroon dan de validate-WARN voor algemene overlap: als beide
    end_date=null is, kan er bijna nooit een legitieme verklaring zijn —
    een van de twee is meestal een vergeten end_date of een dup-person.

    Single-seat: ``post.seat_count == 1`` (expliciet in de YAML).
    Posts met ``seat_count: null`` of ``> 1`` worden niet geflagd.
    """
    single_seat_posts: set[str] = set()
    for _, d in posts:
        pid = d.get("id")
        if not pid:
            continue
        if d.get("seat_count") == 1:
            single_seat_posts.add(pid)

    open_holders: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path, d in persons:
        pid = d.get("id", path.name)
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            if m.get("end_date") is not None:
                continue
            post_id = m.get("post_id")
            if not post_id or post_id not in single_seat_posts:
                continue
            open_holders[post_id].append((pid, m.get("start_date") or "?", path.name))

    for post_id, holders in open_holders.items():
        if len(holders) < 2:
            continue
        # Stable key: post + sorted persons.
        persons_sorted = sorted(holders, key=lambda t: t[0])
        person_keys = "|".join(t[0] for t in persons_sorted)
        descriptions = ", ".join(f"{t[0]} (start={t[1]})" for t in persons_sorted)
        findings.append(
            Finding(
                "single_seat_both_open",
                f"{post_id}|{person_keys}",
                f"{post_id}: {len(holders)} personen met open mandaat — {descriptions}",
            )
        )


def _latest_roo_cache(repo_root: Path) -> Path | None:
    """Vind het meest recente `roo-export-*.xml` in `_cache/`."""
    cache_dir = repo_root / "_cache"
    if not cache_dir.exists():
        return None
    candidates = sorted(cache_dir.glob("roo-export-*.xml"))
    return candidates[-1] if candidates else None


def _check_roo_stale_appointments(
    persons: list[tuple[Path, dict]],
    staging_dir: Path,
    findings: list[Finding],
) -> None:
    """Lees `data/_staging/roo-functies-*.resolved.json` en flag medewerkers
    waarvan ROO ze nog actief noemt maar polder een `end_date` heeft.

    Skipt stilletjes als geen resolved staging-file bestaat.
    """
    import json as _json

    if not staging_dir.exists():
        return
    resolved_files = sorted(staging_dir.glob("roo-functies-*.resolved.json"))
    if not resolved_files:
        return
    latest = resolved_files[-1]
    try:
        with latest.open("r", encoding="utf-8") as fh:
            payload = _json.load(fh)
    except (OSError, _json.JSONDecodeError):
        return

    proposals = payload.get("proposals") if isinstance(payload, dict) else payload
    if not isinstance(proposals, list):
        return

    # Index polder-mandaten op (person_id, post_id) → end_date.
    closed_mandaten: dict[tuple[str, str], str] = {}
    for _, d in persons:
        pid = d.get("id")
        if not isinstance(pid, str):
            continue
        for m in d.get("mandaten") or []:
            if not isinstance(m, dict):
                continue
            post = m.get("post_id")
            end = m.get("end_date")
            if isinstance(post, str) and isinstance(end, str):
                closed_mandaten[(pid, post)] = end

    for prop in proposals:
        if not isinstance(prop, dict):
            continue
        post_id = prop.get("resolved_post_id")
        if not isinstance(post_id, str):
            continue
        for med in prop.get("medewerkers") or []:
            if not isinstance(med, dict):
                continue
            person_id = med.get("resolved_person_id")
            if not isinstance(person_id, str):
                continue
            end = closed_mandaten.get((person_id, post_id))
            if end is None:
                continue
            roo_end = med.get("end_date")
            if roo_end and roo_end <= end:
                # ROO weet ook dat de persoon weg is, geen drift.
                continue
            findings.append(
                Finding(
                    "roo_stale_appointment",
                    f"{person_id}|{post_id}",
                    f"ROO noemt {med.get('naam')!r} nog actief op {post_id}, "
                    f"maar polder-mandaat heeft end_date={end}",
                )
            )


def _check_roo_superset(
    orgs: list[tuple[Path, dict]],
    cache_path: Path,
    findings: list[Finding],
) -> None:
    """Vergelijk polder-orgs met laatste ROO-export-XML.

    Twee categorieën:
    - `roo_missing_org`: ROO heeft een organisatie die wij niet hebben.
    - `roo_field_drift`: ROO's `<datumMutatie>` is nieuwer dan onze
      `last_mutation` — betekent dat de daily fetcher achterloopt.
    """
    # Lazy import: lxml is een fetcher-dep en mag de hot path van
    # `polder audit` (zonder cache) niet vertragen.
    from lxml import etree

    from polder.fetchers.roo import _attr_systeemid, _localname

    try:
        tree = etree.parse(str(cache_path))
    except (OSError, etree.XMLSyntaxError):
        return
    root = tree.getroot()

    # Index polder-orgs op roo_id.
    polder_by_roo_id: dict[str, tuple[Path, dict]] = {}
    for path, d in orgs:
        rid = (d.get("identifiers") or {}).get("roo_id")
        if rid:
            polder_by_roo_id[str(rid)] = (path, d)

    for org_node in root.iter():
        local = _localname(org_node.tag).lower()
        if local not in ("organisatie", "regeling"):
            continue
        sysid = _attr_systeemid(org_node)
        if not sysid:
            continue

        # Naam voor de error-message.
        naam = ""
        for c in org_node:
            if _localname(c.tag).lower() in ("naam", "titel") and (c.text or "").strip():
                naam = c.text.strip()
                break

        if sysid not in polder_by_roo_id:
            findings.append(
                Finding(
                    "roo_missing_org",
                    f"roo:{sysid}",
                    f"ROO bevat {naam!r} (roo_id={sysid}) maar polder niet",
                )
            )
            continue

        # Field-drift: vergelijk datumMutatie.
        roo_mutation = ""
        for c in org_node:
            if _localname(c.tag).lower() == "datummutatie" and (c.text or "").strip():
                roo_mutation = c.text.strip()
                break
        if not roo_mutation:
            continue
        path, polder_d = polder_by_roo_id[sysid]
        polder_mutation = polder_d.get("last_mutation")
        if polder_mutation and roo_mutation > polder_mutation:
            findings.append(
                Finding(
                    "roo_field_drift",
                    f"roo:{sysid}",
                    f"{path.name}: polder last_mutation={polder_mutation} "
                    f"maar ROO datumMutatie={roo_mutation}",
                )
            )


def summary(report: AuditReport) -> tuple[int, int]:
    """Geef (totaal_categorieën, totaal_findings) terug."""
    cats = {f.category for f in report.findings}
    return len(cats), len(report.findings)
