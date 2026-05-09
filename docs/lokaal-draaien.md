# Polder lokaal draaien

Alles wat polder via GitHub Actions doet, kan ook 1-op-1 op je eigen machine
draaien via de Claude Code CLI (`claude -p`). Geen `ANTHROPIC_API_KEY`-secret
nodig: `claude -p` gebruikt je lokale Claude Code subscription en auth. Handig
voor ontwikkeling, debugging en runs zonder runner-quota.

## Vereisten

- [`uv`](https://docs.astral.sh/uv/) voor Python en deps.
- [Claude Code CLI](https://claude.com/claude-code) versie 2.x. Verifieer met
  `claude --version`.
- `git`. Voor de daily-update flow ook een schone werkdirectory.

Optioneel: zet `CLAUDE_BIN` als de `claude` binary niet in je `PATH` staat.
Default valt het terug op `/Users/anneschuth/.local/bin/claude` als die bestaat.

## Wat zit waar

| Workflow (CI)                       | Lokale equivalent                             |
| ----------------------------------- | --------------------------------------------- |
| `.github/workflows/daily-update.yml`| `make daily-update-local`                     |
| claude-review job in daily-update   | `make review-diff` (of via daily-update-local)|
| parse-staatscourant skill in CI     | `make parse-stcrt XML=...`                    |
| parse-organogram skill in CI        | `make parse-organogram PDF=... MIN=...`       |
| willekeurige skill                  | `bash scripts/run_skill.sh <name> <input>`    |

## Dagelijkse update lokaal

```bash
make daily-update-local
```

Dit doet:

1. Alle deterministische fetchers (ROO, Logius COR, Wikidata orgs, TK OData,
   EK scrape, AR RWT). Failures worden gelogd en het script gaat door.
2. `polder-validate`. Hard fail bij errors.
3. `polder-diff` schrijft `diff.json` en `proposals.json` in de repo-root.
4. Label-bepaling: `auto-merge` als alle wijzigingen confidence 0.95+ hebben
   en geen `high_stakes` flag dragen, anders `needs-review`. Geschreven naar
   `dist/pr-label.txt`.
5. `review-pr-diff` skill via `claude -p` schrijft `dist/pr-body.md`.

Geen git-commits, geen PR. Je reviewt zelf en commit handmatig.

## Per skill een voorbeeld

### parse-staatscourant

Haal eerst een paar KB's op uit de KOOP SRU-feed:

```bash
uv run polder-fetch-koop --since 2026-04-01 --limit 5
```

Dan voor elk gevonden XML-bestand:

```bash
make parse-stcrt XML=_cache/staatscourant/2026-04-12-stcrt-12345.xml
```

Output landt in `data/_staging/staatscourant-<basename>-<datum>.json`. Die file
is een lijst Membership-proposals met evidence-snippets en confidence per
claim. Validator faalt als een claim geen letterlijke substring uit de bron
quote.

### parse-organogram

Na een ABD-organogram-fetch:

```bash
make parse-organogram \
  PDF=_cache/abd-organogrammen/min-bzk/assets/2026-q1-organogram.pdf \
  MIN=min-bzk
```

Output: `data/_staging/organogram-min-bzk-<datum>.json`.

De skill leest de PDF zelf via de Read-tool van Claude Code; het script geeft
alleen het absolute pad door.

### review-pr-diff

Na een fetcher-run met diff.json in de root:

```bash
make review-diff
# of expliciet
bash scripts/review_pr_diff_local.sh diff.json dist/pr-body.md
```

Output is een Nederlandstalige PR-body als markdown, gegroepeerd per
organisatie, met confidence-flags en high-stakes markers. Plak die in een PR
of review hem eerst.

### entity-resolution of een andere skill

Voor elke skill onder `.claude/skills/<naam>/` werkt de generieke runner:

```bash
bash scripts/run_skill.sh entity-resolution input.json output.json
```

Argumenten: skill-naam, input (file-pad of letterlijke string), optionele
output-pad. Voor binaire bestanden (PDF, PNG, JPG) geeft de runner het pad
door; voor tekst-bestanden de inhoud.

## Wanneer GitHub Actions wel

Lokaal is voor ontwikkeling, debugging en ad-hoc runs. GitHub Actions blijft de
plek voor:

- Dagelijkse cron (`05:17 UTC`).
- Auto-PR via `peter-evans/create-pull-request`.
- Auto-merge via labels.
- Reproduceerbare run-history per commit.

Als je de runner-quota op wilt sparen of een fetcher offline wilt debuggen,
draai de pipeline dan lokaal en push pas als de diff er goed uitziet.

## Troubleshooting

- `claude: command not found` in de scripts: zet `export
  CLAUDE_BIN=/pad/naar/claude` of voeg `claude` toe aan je `PATH`.
- `claude -p` blijft hangen: controleer of je ingelogd bent met `claude` in
  een interactieve sessie. De CLI bewaart auth in de keychain.
- Skill faalt op binaire input: check dat het pad bestaat en absoluut is. De
  runner converteert relatieve paden waar mogelijk.
- Validator faalt na een staging-write: dat is by design. LLM-proposals gaan
  altijd eerst naar `data/_staging/`. Pas na manuele review en de two-source
  rule mogen ze naar `data/personen/` of `data/organisaties/`.
