# `polder apply-staging`

Pas resolver-output uit `data/_staging/*.resolved.json` automatisch toe op
`data/`. Default is een dry-run; met `--apply` worden YAML-records echt
geschreven en wordt daarna `polder validate` uitgevoerd.

## Wanneer gebruik je dit?

Na het draaien van de `parse-*` skills (parse-abd-nieuws, parse-staatscourant,
parse-organogram) en de `resolve-staging-proposals` skill staat er een
verrijkte `*.resolved.json` in `data/_staging/`. Voorheen werden die records
handmatig gemerged in `data/`. `apply-staging` automatiseert dat voor de
proposals waarvan we zeker genoeg zijn.

## Aanroep

```bash
# Dry-run op één bestand
uv run polder apply-staging data/_staging/abd-nieuws-kewal-2023-11-27.resolved.json

# Dry-run op de hele staging-map
uv run polder apply-staging data/_staging/

# Echt toepassen
uv run polder apply-staging data/_staging/ --apply

# Alleen orgs en posts, geen personen
uv run polder apply-staging data/_staging/ --apply --skip-persons

# Alleen records met confidence >= 0.95
uv run polder apply-staging data/_staging/ --apply --only-high-confidence
```

## Auto-merge regels

| Soort record | Conditie voor auto-create |
| --- | --- |
| Nieuw `organisatieonderdeel` (afdeling/directie) | parent uit `organization_chain` bestaat in `data/organisaties/`, slug volgt conventie, niet rood-AVG |
| Nieuwe `post` | `organization_id` bestaat (eventueel net aangemaakt), classification afleidbaar uit role, niet rood-AVG |
| Nieuwe `persoon` | `confidence >= 0.85`, geboortejaar bekend OF geen kandidaat-conflict in `data/personen/` |
| Mandaat op bestaande persoon | `resolved_person_id` bekend en `organization_id` + `post_id` bestaan |
| Mandaat op net-aangemaakte persoon | mandaat inline in nieuwe persoon-yaml |

Classification-mapping voor posten gebeurt op rolwoord:

- `secretaris-generaal`, `directeur-generaal`, `inspecteur-generaal` -> `abd-tmg`
- `directeur` -> `abd-directeur`
- `afdelingshoofd` -> `abd-afdelingshoofd`
- `projectleider` -> `abd-projectleider`
- `minister`, `staatssecretaris` -> `bewindspersoon`

## Skip-cases

Een proposal wordt overgeslagen (en handmatig review vereist) bij:

- `confidence < 0.85` (of `< 0.95` met `--only-high-confidence`)
- rood-AVG niveau (beleidsmedewerker, secretaresse, stagiair, etc.)
- chain-entry zonder `slug_proposal`
- chain-entry met ontbrekende parent
- `organization_id` niet in `data/` en niet via chain aangemaakt
- ontbrekende `post_id` in proposal
- post-classification niet afleidbaar uit role-tekst
- nieuwe persoon zonder geboortejaar terwijl er kandidaat-conflict is op familienaam
- mandaat met dezelfde post + start_date bestaat al

## Source-attribution

Elk record dat `apply-staging` aanmaakt krijgt een `sources[]`-entry met:

- `id`: `abd_nieuws`, `staatscourant` of `organogram` (afgeleid uit URL of
  bestandsnaam-prefix)
- `url`: de oorspronkelijke bron-URL uit de proposal
- `retrieved`: vandaag
- `fields`: `["applied_via:apply-staging"]` als toelichting in de tree

## Idempotentie

Een tweede `apply-staging`-run zonder externe wijzigingen schrijft 0 records:
bestaande org-/post-IDs en persoon-slugs worden gedetecteerd en (bij gelijke
post + start_date) leidt dat niet tot duplicate-mandaten.

## Reproductie

```bash
# Full-batch over alle resolved bestanden in data/_staging/
polder apply-staging data/_staging/ --apply
```
