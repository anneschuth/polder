# `polder ingest`

Vol-automatische staging-pipeline. Eén commando draait per bron parse, resolve,
apply, validate, build, commit en push.

## Wanneer gebruiken

`polder ingest` is bedoeld voor de dagelijkse run die nieuwe records uit de
HTML/XML/PDF-cache verwerkt zonder dat een mens elk tussenresultaat hoeft te
bekijken. De drempel staat op 0.85; alleen records die `apply-staging`
auto-mergeable acht komen in `data/`.

Voor handmatige runs of inhoudelijke review blijft het bestaande pad bruikbaar
(`polder skill parse-abd-nieuws ...`, `polder skill resolve-staging ...`,
`polder apply-staging ... --apply`).

## Wat doet het

Per bron (`abd-nieuws`, `staatscourant`, `organogram`):

1. **Parse**. Scan `_cache/<bron>/` op input-files die nog geen
   `data/_staging/<bron>-<key>.json` hebben. Voor abd-nieuws en staatscourant
   filtert een deterministisch voorfilter eerst HTML/XML zonder benoemings-
   markers eruit (zie *Pre-filter* hieronder). De rest gaat via
   `polder.llm.runner.run_skill` naar de parse-skill. De runner houdt per
   worker-thread één `claude -p --input-format stream-json` proces open en
   hergebruikt Anthropic's prompt-cache binnen die sessie.
2. **Resolve**. Voor elke `<bron>-*.json` zonder `.resolved.json` companion
   roept de pipeline `polder.llm.runner.run_skill` aan voor `resolve-staging`.
   Zelfde sessie-mechanisme.
3. **Apply**. `polder.apply.plan_apply` plus `execute_apply` met de gekozen
   threshold. Records onder de drempel komen op de skip-stack.
4. **Validate**. `polder.validate.run_all_checks`. Bij errors stopt de pipeline
   en wordt er niet gebouwd, gecommit of gepusht.

Per bron, na succes:

5. **Commit per bron** (met `--commit`). Stage alleen `data/`, commit met
   message `Daily ingest <bron> <YYYY-MM-DD>: +N records (M needs-review)`.
   Aparte commit per bron zodat een verkeerde bron-run terug te draaien is
   zonder de andere te raken.

Daarna eenmalig over alle bronnen:

6. **Build**. `polder build all` (SQLite, CSV, datapackage). Alleen als er
   tenminste één record is geapplieerd.
7. **Build-commit** (met `--commit`). Stage `dist/` en `datapackage.json`,
   commit met message `Daily build <YYYY-MM-DD>: dist/ + datapackage`.
8. **Push** (met `--push`, impliceert `--commit`). `git push origin <branch>`,
   default `main`.

## Gebruik

```bash
# Dry-run: laat zien wat zou gebeuren, geen LLM-calls.
uv run polder ingest --dry-run

# Eén bron, hogere drempel, lokaal toepassen, niet committen.
uv run polder ingest --source abd-nieuws --threshold 0.95

# Volledig automatisch (dagelijkse cron-run).
uv run polder ingest --commit --push

# Beperk parse-jobs (handig voor cost control bij eerste run).
uv run polder ingest --source abd-nieuws --limit 50 --commit
```

## Parallel uitvoeren (`--parallel`)

Parse en resolve draaien per default met 5 worker-threads. Elke thread houdt
zijn eigen `SkillSession` (één `claude -p` proces) en doet daar achter elkaar
calls op. De apply-fase blijft single-threaded omdat die naar `data/` schrijft.

```bash
# Snelle sanity-check met klein budget en 8 parallelle workers.
uv run polder ingest --parallel 8 --max-claude-calls 500 --dry-run

# Dagelijkse run met de default van 5.
uv run polder ingest --parallel 5 --commit
```

Voor de eerste full-run over 2906 abd-nieuws HTMLs: met `--parallel 8` zit je
rond 3.5 uur. Verhoog niet ongebreid; elke worker houdt een claude-subproces
open en je rate-limit zit rond 5-10 concurrent sessies. Boven 8 zie je
throttling.

Budget-cap blijft scherp: jobs die buiten `--max-claude-calls` of
`--max-cost-usd` vallen worden vooraf weggeknipt, niet halverwege gestopt. Geen
race-conditions op de teller.

## Response-cache

Skill-responses worden gecached in `_cache/llm-responses/`. Cache-key bevat de
sha256 van de bijhorende SKILL.md, dus skill-tweaks invalideren automatisch.
Zie `docs/lokaal-draaien.md` voor het handmatig legen van een cache-bucket.

## Idempotentie

Een tweede run zonder nieuwe input doet niets. `plan_parse` skipt files met
bestaande staging-output, `plan_resolve` skipt files met een `.resolved.json`
companion, en de response-cache vangt herhaalde calls met identieke input op
nul kosten. `apply-staging` is intrinsiek idempotent.

## Failure-modes

- Parse of resolve faalt voor één file: pipeline gaat door, `parse_failed` of
  `resolve_failed` wordt verhoogd. Geen blocker.
- Apply gooit een exception: pipeline stopt voor die bron, `apply_failed=True`.
  Geen build, geen commit, exit-code 2.
- Validate fail: pipeline stopt globaal, geen build/commit/push, exit-code 2.
- Build fail: geen commit, exit-code 3.
- Niets gewijzigd in `data/` plus `dist/` na apply: `commit_changes` returnt
  `None`, geen lege commit.

## CI-integratie

`.github/workflows/daily-update.yml` heeft een aparte job `ingest` die na de
fetch-job draait. De job:

- Draait alleen op cron en `workflow_dispatch`.
- Skipt op `pull_request`-triggers (te kostelijk).
- Pusht naar de `daily-update/<run-id>` branch die de fetch-job al gebruikte.

## Budget-caps

Twee plafonds, beide hard:

```bash
# Cap op aantal LLM-calls (parse + resolve, alle bronnen samen).
uv run polder ingest --max-claude-calls 100

# Cap op echte USD-kosten gerekend uit stream-json token-counts.
uv run polder ingest --max-cost-usd 5.0

# Helemaal uit (apply + validate draaien wel).
uv run polder ingest --max-claude-calls 0
```

`--max-cost-usd` gebruikt de echte input/output/cache-read/cache-write tokens
uit elke `claude -p --output-format stream-json` call. `IngestBudget.cost_actual_usd`
is dus geen schatting meer maar een running total uit de daadwerkelijke
responses. Zodra een teller de cap raakt stopt de huidige fase netjes, gaat
door naar apply met wat er al staat, en zet `budget_hit=True` op het result.
Andere bronnen verderop in de run worden ook gecapt; beide budgetten zijn
gedeeld over de hele invocatie.

Live geverifieerd in een recente run: call 1 kost $0.018 (cache-write), call 2
kost $0.022 met 36K cache-read tokens. Daarna stabiliseert het.

Programmeerinterface:

```python
from polder.ingest import IngestBudget, ingest_source

budget = IngestBudget(max_claude_calls=50, max_cost_usd=2.5)
result = ingest_source("abd-nieuws", repo_root=Path("."), budget=budget)
print(budget.used_calls, budget.cost_actual_usd)
```

## Modelkeuze (`--model`)

Default is `claude-haiku-4-5`. Sonnet 4.6 tikt vaak de daily rate-limit aan en
is 5x duurder; Haiku haalt voor extraction-skills vergelijkbare nauwkeurigheid.
De vision-skill `parse-organogram` overruled zelf naar Opus 4.7 omdat een
hierarchisch organogram met Haiku te onstabiel is.

```bash
# Default: Haiku 4.5
uv run polder ingest --source abd-nieuws --commit

# Forceer Opus voor één run
uv run polder ingest --model claude-opus-4-7 --source abd-nieuws --limit 10
```

## Pre-filter

Voor `abd-nieuws` en `staatscourant` skipt een lichte regex-check de LLM-call
als de input geen benoemings-marker bevat:

| bron           | wat wordt gecheckt                              |
| -------------- | ----------------------------------------------- |
| abd-nieuws     | strip-HTML body op markers als `wordt benoemd`, |
|                | `directeur`, `secretaris-generaal`, etc.        |
| staatscourant  | KB-titel (`officiele-titel`/`citeertitel`/etc.) |
|                | op `benoeming`, `ontslag`, `verlenging`, etc.   |

Geïmplementeerd in `polder.llm.prefilters.abd_nieuws_has_signal` en
`staatscourant_has_signal`. Pre-filter-skips schrijven `[]` naar de
staging-output zodat de file als "verwerkt" telt voor de volgende
`plan_parse`. Bespaart ~30-50% van de calls in een typische ABD-feed.

## Rate-limit afbreken (`--abort-on-rate-limit`)

Als `claude -p` een rate-limit-melding teruggeeft (`Claude AI usage limit
reached`, HTTP 429, of soortgelijk), retourneert de runner een specifieke
foutcode en `polder ingest` breekt de huidige fase af zodat de overige bronnen
worden overgeslagen. Apply plus validate van de getroffen bron draaien wel nog
op het reeds gestagete materiaal.

Zet uit met `--no-abort-on-rate-limit` als je per ongeluk een rate-limited
sessie wilt blijven proberen (niet aanbevolen; je krijgt corrupt JSON en
verspilt tokens).

De daily rate-limit van Sonnet 4.6 reset om 22:00 Europe/Amsterdam. Tussendoor
schakel je over op Haiku of Opus.

## Kostenraming

`polder.ingest.estimate_cost(parse_jobs, resolve_jobs)` retourneert een ruwe
schatting in USD. Aannames per modelfamilie:

| model        | $/call (gemiddeld) |
| ------------ | ------------------ |
| `sonnet-4-6` | 0.025              |
| `haiku-4-5`  | 0.005              |
| `opus-4-7`   | 0.10               |

Resolve-jobs rekenen ~1.5x mee omdat `lookup-person` regelmatig binnen de
resolve-skill wordt aangeroepen. Sessie-hergebruik plus prompt-caching drukt
de effectieve cost per call binnen één run flink naar beneden; de raming
hierboven is een conservatieve bovengrens. De `IngestBudget.cost_actual_usd`
tijdens en na de run is de echte stand.

`--dry-run` print een per-bron breakdown plus totaal:

```
Ingest dry-run analyse:

[abd-nieuws]
  Phase 1 parse: 2906 jobs, ~$14.53 (claude-haiku-4-5)
  Phase 2 resolve: 12 staging-files unresolved, ~$0.09
  Phase 3 apply: ~28 records auto-mergeable boven threshold 0.85, ~12 needs-review

[staatscourant]
  Phase 1 parse: 568 jobs, ~$2.84 (claude-haiku-4-5)
  Phase 2 resolve: 0 staging-files unresolved, ~$0.00
  Phase 3 apply: ~6 records auto-mergeable boven threshold 0.85, ~3 needs-review

Totale geschatte kosten: ~$17.46. Wall-clock parallel=5: ~3.5-6.5 uur.
Totaal: 34 auto-mergeable, 15 needs-review.
Run zonder --dry-run om de pipeline echt te starten.
```

Met pre-filter (~30-50% skip) plus sessie-caching zakt de werkelijke kost
nog verder. Gebruik `--limit`, `--max-claude-calls` of `--max-cost-usd` voor
de eerste runs om het te bewaken.

```bash
uv run polder ingest --dry-run --max-claude-calls 200
```

## Reproductie

```bash
cd ~/polder
uv run pytest tests/test_ingest.py -v
uv run polder ingest --dry-run --parallel 8
uv run polder ingest --source abd-nieuws --limit 5 --parallel 5 --commit --push
```
