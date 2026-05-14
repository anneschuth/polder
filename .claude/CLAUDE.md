# Polder project context

Project: `polder`. Een git-versioned, CC0-gelicenseerde dataset van Nederlandse overheidsorganisaties, posten, personen en mandaten. Source-of-truth in YAML, gevalideerd met JSON Schema.

## Harde regels (data)

- **Geen BSN, ooit.** Niet in tekstvelden, niet in identifiers, niet als afgeleide. Validator regex'eert hierop.
- **Geboortedata alleen als jaartal.** Veld `birth.year`. Geen maand of dag, ook niet als die publiek bekend zijn.
- **Geen records ooit verwijderen.** Bij opheffing of einde mandaat zet je `valid_until`. Het record blijft staan.
- **Elk record heeft `sources[]`** met minstens één entry. Schema enforced.
- **Geen privé-contactgegevens.** Alleen functionele contactvelden van de organisatie zelf.
- **AVG-cut-off honoreren.** Groen/geel/rood lijst staat in README. Rood publiceren we niet, ook niet als de bron het toelaat.

## Harde regels (LLM)

- **Quote-or-die.** Elk LLM-proposal met een claim heeft een `evidence_snippet` dat een letterlijke substring is van de bron. Validator faalt anders. Geen paraphrase.
- **Two-source rule voor benoemingen.** Staatscourant plus minstens één andere bron, OF Staatscourant met `confidence ≥ 0.98` en 7-daagse human-on-the-loop window voor merge.
- **Diff-only mode.** LLM krijgt de delta plus relevante bestaande records. Nooit een open "vul aan naar beste vermogen"-prompt.
- **LLM schrijft alleen naar `data/_staging/`.** Nooit direct naar `data/organisaties/`, `data/personen/`, etc.
- **Confidence per claim** als float [0, 1] met `confidence_reasoning` als string.

## Code style

- Python 3.11+, type hints overal, `ruff format` voor formatting, `ruff check` voor linting.
- `uv` voor package management. Alle scripts via `uv run ...`. Geen `pip install` direct.
- Pydantic v2 voor model-validatie in code, JSON Schema voor data-validatie op disk.
- httpx (niet requests). lxml voor XML. PyYAML met `safe_load` en `safe_dump`.

## Pad-conventies

- Organisaties: `data/organisaties/<type>/<slug>.yaml` waar `<type>` een van `{ministeries, zbo, agentschappen, rwt, hoge-colleges, gemeenten, provincies, waterschappen, gemeenschappelijke-regelingen, adviescolleges, inspecties, rechterlijke-macht, politie-om, caribisch-nederland}`.
- Personen: `data/personen/<slug>.yaml`. Vlak, geen current/historisch-split. Of een persoon "current" is volgt uit de mandaten (`end_date is None` → lopend).
- Posten: `data/posten/<slug>.yaml`.
- Schemas: `schemas/<entity>.schema.json`. JSON Schema 2020-12. `additionalProperties: false`.
- Slug-conventie organisatie: `org:<slug>`. Persoon: `person:<familienaam>-<initialen-lower>-<geboortejaar>`. Post: `post:<rol>-<org-slug>`.

## Workflow

- Daily update via GitHub Actions (`.github/workflows/daily-update.yml`). Cron 05:17 UTC.
- Auto-merge alleen als alle wijzigingen `confidence ≥ 0.95` hebben en geen "rood"-veld raken.
- Anders label `needs-review` en wachten op handmatige merge.

## Audit-bevindingen reviewen

`polder audit` rapporteert categorieën zoals `quasi_dup_family_birth`,
`start_after_end`, `mandaat_org_post_mismatch` etc. Wanneer een finding
een **legitiem geval** is (twee echt-verschillende personen met dezelfde
familienaam + geboortejaar, een aangekondigde benoeming in de toekomst,
een verleende uitzondering op de hiërarchie-regel) is dat geen bug —
markeer die expliciet als geverifieerd.

Mechanisme: `data/_audit/verified.yaml` met entries van
`{category, key, note, verified_at, verified_by}`. `polder audit` filtert
geverifieerde findings standaard uit (toon ze met `--include-verified`).

CLI om te markeren:

```bash
polder audit verify <category> <key> --note "korte uitleg"
```

Voorbeelden:
- `polder audit verify quasi_dup_family_birth "van dijk|1971" --note "Diederik en Jasper zijn verschillende politici"`
- `polder audit verify start_in_future "person:foo-1980|mandate-bar-2026-09-01" --note "aangekondigde benoeming via ABD-nieuws"`

Werkwijze: **niet alles als geverifieerd wegklikken** — onderzoek elke
finding eerst. Echte duplicaten of fouten in de data moeten gefixt
worden (mandaat verplaatsen, person merge via aparte tool, datum
corrigeren). Alleen wat na onderzoek een legitiem geval blijkt krijgt
een verified-entry met onderbouwing in de `note`.

## Tasktracking

Gebruik GitHub Issues op `anneschuth/polder` als bron-van-waarheid voor takentracking. Niet Claude's TaskCreate-tool of losse plan-files.

- Werk op te pakken: `gh issue list --repo anneschuth/polder --state open`
- Issue oppakken: `gh issue edit <nr> --add-assignee anneschuth` plus `--add-label in-progress`
- Issue afsluiten: in commit-message `Closes #<nr>` of via `gh issue close <nr>`
- Nieuw werk: `gh issue create --repo anneschuth/polder --title "..." --body "..." --label <label> --milestone "..."`
- Milestones: Week 2 t/m Week 6 voor MVP; "Iteratie 2" voor maand 2-5; geen milestone voor backlog.
- Labels: `fetcher`, `schema`, `skill`, `infra`, `data-quality`, `avg`, `bron-kwaliteit`, `auto-merge`, `needs-review`.

Claude's interne TaskCreate is alleen voor binnen-een-sessie tracking; persistente taken horen op GitHub.
