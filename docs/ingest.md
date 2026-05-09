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

Daarna eenmalig over alle bronnen:

5. **Build**. `polder build all` (SQLite, CSV, datapackage). Alleen als er
   tenminste één record is geapplieerd.
6. **Commit** (met `--commit`). Stage `data/`, `dist/` en `datapackage.json`,
   commit met message `Daily ingest <YYYY-MM-DD>: +N records (<bron> +M, ...)`.
7. **Push** (met `--push`, impliceert `--commit`). `git push origin <branch>`,
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

## Kostenraming

`polder.ingest.estimate_cost(parse_jobs, resolve_jobs)` retourneert een ruwe
schatting in USD op basis van de aanname dat elke parse-call ~$0.025 kost
(Sonnet 4.6, ~5-10K input tokens). Voor 50 parse-jobs + 50 resolve-jobs zit je
rond $1.90.

Begin met `--limit 50` of `--limit 100` om de kosten van de eerste runs te
beperken.

## Reproductie

```bash
cd ~/polder
uv run pytest tests/test_ingest.py -v
uv run polder ingest --dry-run
uv run polder ingest --source abd-nieuws --limit 5 --commit --push
```
