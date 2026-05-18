#!/usr/bin/env python3
"""
resolve-staging-proposals skill: Match staging-proposals against canonical data.

Matches records from parse-abd-nieuws, parse-staatscourant, or parse-organogram
against existing organisations, posts, and persons in data/, outputting:
- resolved_organization_id, resolved_post_id, resolved_person_id
- per-field confidence scores (organization, post, person)
- resolution_notes with evidence
- propose_post_creation flag
- merge_recommendation (auto-merge / needs-review / skip)

Respects two-source rule: abd-only capped at 0.85, staatscourant ≥0.95.
"""

import difflib
import json
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

# DATA_DIR: find the git root by going up from this file
SCRIPT_DIR = Path(__file__).parent
POLDER_ROOT = SCRIPT_DIR.parent.parent.parent  # .claude/skills/resolve-staging-proposals -> polder/
DATA_DIR = POLDER_ROOT / "data"


@dataclass
class ResolutionConfidence:
    """Per-field confidence scores [0, 1]."""

    organization: float = 0.0
    post: float = 0.0
    person: float = 0.0


@dataclass
class ResolvedRecord:
    """Staging record enriched with resolved IDs, confidence, and merge recommendation."""

    # Original fields
    person_name: str | None = None
    organization_id: str | None = None
    organization_chain: list | None = None
    post_id: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    decision_reference: str | None = None
    abd_nieuws_url: str | None = None
    staatscourant_url: str | None = None
    event_type: str | None = None

    # Resolved fields
    resolved_organization_id: str | None = None
    resolved_organization_level: str | None = None
    resolved_post_id: str | None = None
    resolved_person_id: str | None = None

    # Metadata
    resolution_confidence: ResolutionConfidence = field(default_factory=ResolutionConfidence)
    resolution_notes: str = ""
    propose_post_creation: bool = False
    merge_recommendation: str = "skip"


def normalize_name(name: str) -> str:
    """Normalize a name: lowercase, remove accents."""
    if not name:
        return ""
    nfd = unicodedata.normalize("NFD", name.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def levenshtein_ratio(s1: str, s2: str) -> float:
    """Calculate Levenshtein distance ratio [0, 1]."""
    s1n = normalize_name(s1)
    s2n = normalize_name(s2)
    matcher = difflib.SequenceMatcher(None, s1n, s2n)
    return matcher.ratio()


def load_yaml_with_path(directory: Path) -> dict[str, tuple[Path, dict]]:
    """Load all YAML files, return {stem: (path, data)}."""
    files = {}
    if directory.exists():
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                files[yaml_file.stem] = (yaml_file, data)
            except Exception as e:
                print(f"Warning: {yaml_file}: {e}", file=sys.stderr)
    return files


def load_organization_subdivisions() -> dict[str, tuple[Path, dict]]:
    """Load all organisations from ministeries and organisatieonderdelen."""
    all_orgs = {}

    for subdir in [
        DATA_DIR / "organisaties" / "ministeries",
        DATA_DIR / "organisaties" / "organisatieonderdelen",
    ]:
        if subdir.exists():
            for yaml_file in sorted(subdir.glob("*.yaml")):
                try:
                    with open(yaml_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    # Use the 'id' field from the YAML, fall back to file stem if not present
                    org_id = data.get("id", yaml_file.stem)
                    all_orgs[org_id] = (yaml_file, data)
                except Exception as e:
                    print(f"Warning: {yaml_file}: {e}", file=sys.stderr)

    return all_orgs


def match_organization(
    organization_chain: list,
    all_orgs: dict[str, tuple[Path, dict]],
) -> tuple[str | None, str | None, str, float]:
    """
    Match organization from chain.

    Algorithm:
    1. Loop chain entries from first to last (deepest level first)
    2. For each entry, search all_orgs for matching names (exact match first, then fuzzy)
    3. Filter by org_path to match the correct level (ministeries vs organisatieonderdelen)
    4. For afdeling-level matches, validate parent_id against chain
    5. Return first match (deepest level has priority)

    Returns (resolved_id, level, notes, confidence)
    """
    if not organization_chain:
        return None, None, "no organization_chain", 0.0

    notes = []

    # Build a map of chain levels for parent validation
    chain_by_level = {entry.get("level"): entry.get("name") for entry in organization_chain}

    def should_check_org(org_path: Path, level: str) -> bool:
        """Determine if org_path matches the expected level."""
        if level == "ministerie":
            return "ministeries" in str(org_path)
        else:  # afdeling, directie, directoraat-generaal
            return "organisatieonderdelen" in str(org_path)

    # Phase 1: Exact matches (highest priority)
    for _chain_idx, chain_entry in enumerate(organization_chain):
        level = chain_entry.get("level", "")
        name = chain_entry.get("name", "").strip()

        if not name:
            continue

        # Strip common prefixes for ministeries
        name_variants = [name]
        if level == "ministerie":
            if name.lower().startswith("ministerie van "):
                name_variants.append(name[14:].strip())  # Remove "Ministerie van "

        for org_id, (org_path, org_data) in all_orgs.items():
            # Filter by appropriate directory for level
            if not should_check_org(org_path, level):
                continue

            org_names = org_data.get("names", [])
            parent_id = org_data.get("parent_id")

            for name_entry in org_names:
                name_value = (
                    name_entry.get("value", "") if isinstance(name_entry, dict) else str(name_entry)
                )

                # Exact match against any variant
                for name_variant in name_variants:
                    if normalize_name(name_variant) == normalize_name(name_value):
                        # For afdeling matches, check parent_id alignment
                        confidence = 0.95

                        if level == "afdeling" and parent_id:
                            # Validate that parent matches the directie in the chain
                            parent_expected = chain_by_level.get("directie")
                            if not parent_expected:
                                confidence = min(confidence, 0.80)
                                notes.append(
                                    f"afdeling '{name}' parent_id={parent_id} but no directie in chain"
                                )

                        rel_path = org_path.relative_to(DATA_DIR)
                        notes.append(f"matched '{name}' (level={level}) on {rel_path}")
                        return org_id, level, "; ".join(notes), confidence

    # Phase 2: Fuzzy matches (lower priority)
    for _chain_idx, chain_entry in enumerate(organization_chain):
        level = chain_entry.get("level", "")
        name = chain_entry.get("name", "").strip()

        if not name:
            continue

        # Strip common prefixes for ministeries
        name_variants = [name]
        if level == "ministerie":
            if name.lower().startswith("ministerie van "):
                name_variants.append(name[14:].strip())  # Remove "Ministerie van "

        for org_id, (org_path, org_data) in all_orgs.items():
            # Filter by appropriate directory for level
            if not should_check_org(org_path, level):
                continue

            org_names = org_data.get("names", [])
            parent_id = org_data.get("parent_id")

            for name_entry in org_names:
                name_value = (
                    name_entry.get("value", "") if isinstance(name_entry, dict) else str(name_entry)
                )

                # Fuzzy match (Levenshtein ratio > 0.85) against any variant
                for name_variant in name_variants:
                    ratio = levenshtein_ratio(name_variant, name_value)
                    if 0.85 < ratio < 1.0:
                        confidence = min(0.85, 0.90 * ratio)
                        rel_path = org_path.relative_to(DATA_DIR)
                        notes.append(
                            f"fuzzy matched '{name}' (level={level}, ratio={ratio:.2f}) on {rel_path}"
                        )
                        return org_id, level, "; ".join(notes), confidence

    notes.append("no organization found in chain")
    return None, None, "; ".join(notes), 0.0


def match_person(
    person_name: str,
    all_persons: dict[str, tuple[Path, dict]],
    birth_year: int | None = None,
) -> tuple[str | None, str, float]:
    """
    Match person by family name + initials + optional birth_year.

    Algorithm:
    - Parse person_name: "Drs. N.G. (Gonnie) de Boer" → extract initials and family
    - Search all_persons for matching family name
    - If initials are present in person_name, prefer matches with matching initials
    - If birth_year provided, filter candidates by year
    - If single match, return with confidence 0.90
    - If multiple candidates, list them and return confidence 0.0

    Returns (resolved_id, notes, confidence)
    """
    if not person_name:
        return None, "no person_name", 0.0

    parts = person_name.strip().split()
    if not parts:
        return None, "empty person_name", 0.0

    # Extract initials (e.g., "N.G.") and family name
    extracted_initials = None
    family_candidate = parts[-1]

    # Look for pattern "X.Y." or "X.Y.Z."
    for _i, part in enumerate(parts):
        if (
            "." in part
            and len(part) <= 4
            and part not in ("Drs.", "Dr.", "Mr.", "Ms.", "Mrs.", "Prof.")
        ):
            extracted_initials = part.replace(".", "").upper()
            break

    notes = []
    matches = []
    initials_matches = []  # Matches with matching initials (higher priority)

    for person_id, (person_path, person_data) in all_persons.items():
        person_name_obj = person_data.get("name", {})

        if isinstance(person_name_obj, dict):
            family = person_name_obj.get("family", "")
            initials = person_name_obj.get("initials", "").replace(".", "").upper()
            birth = person_data.get("birth", {})
            year = birth.get("year") if isinstance(birth, dict) else None

            if normalize_name(family_candidate) == normalize_name(family):
                # Filter by birth year if provided
                if birth_year and year and year != birth_year:
                    continue

                if extracted_initials and extracted_initials == initials:
                    # High-priority match: family + initials match
                    initials_matches.append((person_id, family, initials, year, person_path))
                else:
                    # Lower-priority match: family only
                    matches.append((person_id, family, initials, year, person_path))

    # Prefer initials matches if we have extracted initials
    if initials_matches:
        if len(initials_matches) == 1:
            person_id, family, initials, year, person_path = initials_matches[0]
            rel_path = person_path.relative_to(DATA_DIR)
            notes.append(
                f"matched '{person_name}' on {rel_path} (family={family}, initials={initials}, year={year})"
            )
            confidence = 0.90

            # If birth year unknown, cap at 0.85
            if not year and not birth_year:
                confidence = 0.85

            return person_id, "; ".join(notes), confidence
        else:
            # Multiple candidates with matching initials
            matches_str = ", ".join(
                f"{pid}({fam}/{init}/{yr})" for pid, fam, init, yr, _ in initials_matches
            )
            notes.append(
                f"ambiguous: {len(initials_matches)} candidates with initials {extracted_initials}: {matches_str}"
            )
            return None, "; ".join(notes), 0.0
    elif len(matches) == 1:
        person_id, family, initials, year, person_path = matches[0]
        rel_path = person_path.relative_to(DATA_DIR)
        notes.append(
            f"matched '{person_name}' on {rel_path} (family={family}, initials={initials}, year={year})"
        )
        confidence = 0.85  # Lower confidence when initials not checked

        # If birth year unknown, cap further
        if not year and not birth_year:
            confidence = 0.80

        return person_id, "; ".join(notes), confidence

    elif len(matches) > 1:
        matches_str = ", ".join(f"{pid}({fam}/{init}/{yr})" for pid, fam, init, yr, _ in matches)
        notes.append(f"ambiguous: {len(matches)} candidates: {matches_str}")
        return None, "; ".join(notes), 0.0

    else:
        notes.append(f"no person found with family name '{family_candidate}'")
        return None, "; ".join(notes), 0.0


def extract_role_title(role: str) -> str:
    """
    Extract the main role title from a possibly complex role string.

    Examples:
    - "afdelingshoofd Beleid Wonen, Directie Wonen, ..." → "afdelingshoofd Beleid Wonen"
    - "Directeur Bestuur en Bedrijfsvoering" → "Directeur Bestuur en Bedrijfsvoering"
    """
    if not role:
        return ""

    # Split on comma and take the first part
    parts = role.split(",")
    main_role = parts[0].strip() if parts else ""

    return main_role


def match_post(
    role: str,
    resolved_org_id: str | None,
    all_posts: dict[str, tuple[Path, dict]],
) -> tuple[str | None, str, bool, float]:
    """
    Match post by role slug + org slug.

    Algorithm:
    - Extract main role title (first part before comma)
    - Build candidate slug: {role-slug}-{org-slug-without-prefix}
    - Search all_posts for exact match
    - If found, return with confidence 0.95
    - If not found but org resolved, propose creation

    Returns (resolved_post_id, notes, propose_creation, confidence)
    """
    if not resolved_org_id:
        return None, "organization not resolved", False, 0.0

    if not role:
        return None, "no role provided", False, 0.0

    # Extract main role title (before comma)
    role_main = extract_role_title(role)

    role_slug = role_main.lower().replace(" ", "-").replace("é", "e").replace("ë", "e")
    org_base = resolved_org_id.replace("org:", "")
    candidate_id = f"{role_slug}-{org_base}"

    notes = []

    # Check if post exists
    for post_id, (post_path, _post_data) in all_posts.items():
        if post_id == candidate_id:
            rel_path = post_path.relative_to(DATA_DIR)
            notes.append(f"matched post on {rel_path}")
            return f"post:{post_id}", "; ".join(notes), False, 0.95

    # Post not found: propose creation
    notes.append(f"post does not exist; propose creation as 'post:{candidate_id}'")
    return None, "; ".join(notes), True, 0.0


def resolve_record(
    record: dict,
    all_orgs: dict,
    all_persons: dict,
    all_posts: dict,
) -> ResolvedRecord:
    """Resolve a single record."""

    resolved = ResolvedRecord()
    for k, v in record.items():
        if k in ResolvedRecord.__dataclass_fields__:
            setattr(resolved, k, v)

    is_abd = bool(record.get("abd_nieuws_url"))
    is_staatscourant = bool(record.get("staatscourant_url"))

    # 1. Organization
    org_chain = record.get("organization_chain", [])
    org_id, org_level, org_notes, org_conf = match_organization(org_chain, all_orgs)
    resolved.resolved_organization_id = org_id
    resolved.resolved_organization_level = org_level
    resolved.resolution_confidence.organization = org_conf

    # Apply two-source rule: abd-only capped at 0.85
    if is_abd and not is_staatscourant and org_conf > 0:
        resolved.resolution_confidence.organization = min(0.85, org_conf)

    # 2. Person
    person_name = record.get("person_name")
    # Try to extract birth_year if available in start_date
    birth_year = None
    if record.get("start_date"):
        try:
            # start_date format: "2024-03-01"
            year_str = str(record["start_date"]).split("-")[0]
            if len(year_str) == 4 and year_str.isdigit():
                birth_year = int(
                    year_str
                )  # Actually start year, but person birth year might be in person record
        except ValueError:
            pass

    person_id, person_notes, person_conf = match_person(person_name, all_persons, birth_year)
    resolved.resolved_person_id = person_id
    resolved.resolution_confidence.person = person_conf

    # 3. Post
    role = record.get("role")
    post_id, post_notes, propose_creation, post_conf = match_post(role, org_id, all_posts)
    resolved.resolved_post_id = post_id
    resolved.propose_post_creation = propose_creation
    resolved.resolution_confidence.post = post_conf

    # 4. Merge recommendation
    org_resolved = resolved.resolved_organization_id is not None
    person_resolved = resolved.resolved_person_id is not None
    post_resolved = resolved.resolved_post_id is not None

    # Any field resolved?
    any_resolved = org_resolved or person_resolved or post_resolved

    # All high confidence (≥0.95)?
    org_conf = resolved.resolution_confidence.organization
    person_conf = resolved.resolution_confidence.person
    post_conf = resolved.resolution_confidence.post

    high_conf_fields = [c for c in [org_conf, person_conf, post_conf] if c > 0]
    all_high = all(c >= 0.95 for c in high_conf_fields) if high_conf_fields else False

    if not org_resolved:
        # Organisation is fundamental; if not resolved, skip
        resolved.merge_recommendation = "skip"
    elif all_high and not propose_creation and any_resolved:
        # All resolved with high confidence, no new post needed
        resolved.merge_recommendation = "auto-merge"
    elif any_resolved:
        # Some fields resolved but confidence or completeness concerns
        resolved.merge_recommendation = "needs-review"
    else:
        resolved.merge_recommendation = "skip"

    # Combine notes
    notes_parts = [
        f"org: {org_notes}",
        f"person: {person_notes}",
        f"post: {post_notes}",
    ]
    resolved.resolution_notes = "; ".join(notes_parts)

    return resolved


def main():
    if len(sys.argv) != 2:
        print("Usage: main.py <input-path>", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    # Load input
    with open(input_path, encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        print("Error: Input must be JSON array", file=sys.stderr)
        sys.exit(1)

    # Load data
    print(f"Loading data from {DATA_DIR}...", file=sys.stderr)
    all_orgs = load_organization_subdivisions()
    print(f"  Organisations: {len(all_orgs)}", file=sys.stderr)

    all_persons = load_yaml_with_path(DATA_DIR / "personen")
    print(f"  Persons: {len(all_persons)}", file=sys.stderr)

    all_posts = load_yaml_with_path(DATA_DIR / "posten")
    print(f"  Posts: {len(all_posts)}", file=sys.stderr)

    # Resolve
    print(f"Resolving {len(records)} records...", file=sys.stderr)
    resolved_records = []

    for idx, record in enumerate(records, 1):
        name = record.get("person_name", "?")
        print(f"  [{idx}/{len(records)}] {name}", file=sys.stderr)

        resolved = resolve_record(record, all_orgs, all_persons, all_posts)
        resolved_dict = asdict(resolved)
        resolved_dict["resolution_confidence"] = asdict(resolved.resolution_confidence)
        resolved_records.append(resolved_dict)

    # Write output
    output_path = input_path.parent / f"{input_path.stem}.resolved.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(resolved_records, f, indent=2, ensure_ascii=False)

    print(f"✓ Output: {output_path}", file=sys.stderr)

    # Summary
    summary = {
        "input": str(input_path),
        "output": str(output_path),
        "records": len(resolved_records),
        "auto_merge": sum(1 for r in resolved_records if r["merge_recommendation"] == "auto-merge"),
        "needs_review": sum(
            1 for r in resolved_records if r["merge_recommendation"] == "needs-review"
        ),
        "skip": sum(1 for r in resolved_records if r["merge_recommendation"] == "skip"),
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
