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
   `data/_staging/<bron>-<key>.json` hebben. Roep voor elke ontbrekende file de
   bestaande `scripts/parse_<bron>_local.sh` aan (claude `-p` Sonnet 4.6).
2. **Resolve**. Voor elke `<bron>-*.json` zonder `.resolved.json` companion roep
   `scripts/resolve_staging_local.sh` aan.
3. **Apply**. `polder.apply.plan_apply` + `execute_apply` met de gekozen
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
# Dry-run: laat zien wat zou gebeuren, geen subprocess-calls.
uv run polder ingest --dry-run

# Eén bron, hogere drempel, lokaal toepassen, niet committen.
uv run polder ingest --source abd-nieuws --threshold 0.95

# Volledig automatisch (dagelijkse cron-run).
uv run polder ingest --commit --push

# Beperk parse-jobs (handig voor cost control bij eerste run).
uv run polder ingest --source abd-nieuws --limit 50 --commit
```

## Parallel uitvoeren (`--parallel`)

Parse en resolve draaien per default met 5 worker-threads. Iedere thread doet
één `subprocess.run` op de claude-binary; omdat de Python-thread vrijwel alleen
op IO wacht is een `ThreadPoolExecutor` daarvoor genoeg en goedkoper dan een
`ProcessPoolExecutor`. De apply-fase blijft single-threaded omdat die naar
`data/` schrijft.

```bash
# Snelle sanity-check met klein budget en 8 parallelle workers.
uv run polder ingest --parallel 8 --max-claude-calls 500 --dry-run

# Dagelijkse run met de default van 5.
uv run polder ingest --parallel 5 --commit
```

Voor de eerste full-run over 2906 abd-nieuws HTMLs (sequentieel ~24 uur, ~30s
per parse-call): met `--parallel 8` zit je rond 3.5 uur. Verhoog niet ongebreid;
elke worker schiet een claude-subproces aan en je ANTHROPIC-rate-limit zit
rond 5-10 concurrent calls. Boven 8 zie je throttling-errors.

Budget-cap blijft scherp: jobs die buiten `--max-claude-calls` vallen worden
vooraf weggeknipt, niet halverwege gestopt. Geen race-conditions op de teller.

## Idempotentie

Een tweede run zonder nieuwe input doet niets. `plan_parse` skipt files met
bestaande staging-output en `plan_resolve` skipt files met een
`.resolved.json` companion. `apply-staging` is intrinsiek idempotent.

## Failure-modes

- Parse of resolve faalt voor één file: pipeline gaat door, `parse_failed` of
  `resolve_failed` wordt verhoogd. Geen blocker.
- Apply gooit een exception: pipeline stopt voor die bron, `apply_failed=True`.
  Geen build, geen commit, exit-code 2.
- Validate fail: pipeline stopt globaal, geen build/commit/push, exit-code 2.
- Build fail: geen commit, exit-code 3.
- Niets gewijzigd in `data/` + `dist/` na apply: `commit_changes` returnt
  `None`, geen lege commit.

## CI-integratie

`.github/workflows/daily-update.yml` heeft een aparte job `ingest` die na de
fetch-job draait. De job:

- Skipt zichzelf als `secrets.ANTHROPIC_API_KEY` leeg is.
- Skipt op `pull_request`-triggers (te kostelijk).
- Draait alleen op cron en `workflow_dispatch`.
- Pusht naar de `daily-update/<run-id>` branch die de fetch-job al gebruikte.

## Budget-cap (`--max-claude-calls`)

Hard plafond op het totaal aantal LLM-calls (parse + resolve, alle bronnen
samen). Default unlimited.

```bash
# nooit meer dan 100 LLM-calls deze run
uv run polder ingest --max-claude-calls 100

# helemaal uitschakelen (apply + validate draaien wel)
uv run polder ingest --max-claude-calls 0
```

Zodra de teller op het maximum staat stopt de huidige fase voor de huidige
bron en gaat de pipeline door naar apply met wat er al staat. Het result
heeft dan `budget_hit=True`. Andere bronnen verderop in de run worden ook
gecapt; het budget is gedeeld over de hele invocatie.

Programmeerinterface:

```python
from polder.ingest import IngestBudget, ingest_source

budget = IngestBudget(max_claude_calls=50)
result = ingest_source("abd-nieuws", repo_root=Path("."), budget=budget)
print(budget.used_calls, budget.cost_estimate_usd)
```

## Kostenraming

`polder.ingest.estimate_cost(parse_jobs, resolve_jobs)` retourneert een ruwe
schatting in USD. Aannames per modelfamilie:

| model        | $/call (gemiddeld) |
| ------------ | ------------------ |
| `sonnet-4-6` | 0.025              |
| `haiku-4-5`  | 0.005              |
| `opus-4-7`   | 0.10               |

Resolve-jobs rekenen ~1.5x mee omdat `lookup-person` regelmatig binnen de
resolve-skill wordt aangeroepen. Voor 50 parse + 50 resolve zit je rond
$2.20.

`--dry-run` print een per-bron breakdown plus totaal:

```
Ingest dry-run analyse:

[abd-nieuws]
  Phase 1 parse: 2906 jobs, ~$72.65 (Sonnet 4.6)
  Phase 2 resolve: 12 staging-files unresolved, ~$0.45
  Phase 3 apply: ~28 records auto-mergeable boven threshold 0.85, ~12 needs-review

[staatscourant]
  Phase 1 parse: 568 jobs, ~$14.20 (Sonnet 4.6)
  Phase 2 resolve: 0 staging-files unresolved, ~$0.00
  Phase 3 apply: ~6 records auto-mergeable boven threshold 0.85, ~3 needs-review

Totale geschatte kosten: ~$87.30. Wall-clock parallel=5: ~3.5-6.5 uur.
Totaal: 34 auto-mergeable, 15 needs-review.
Run zonder --dry-run om de pipeline echt te starten.
```

Combineer met `--max-claude-calls` om te zien wat een budget zou opleveren:

```bash
uv run polder ingest --dry-run --max-claude-calls 200
```

Begin met `--limit 50` of `--limit 100` om de kosten van de eerste runs per
bron te beperken; `--max-claude-calls` cap't over alle bronnen.

## Reproductie

```bash
cd ~/polder
uv run pytest tests/test_ingest.py -v
uv run polder ingest --dry-run --parallel 8
uv run polder ingest --source abd-nieuws --limit 5 --parallel 5 --commit --push
```
