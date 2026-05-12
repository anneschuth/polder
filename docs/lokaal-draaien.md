# Polder lokaal draaien

Alles wat polder via GitHub Actions doet, kan ook op je eigen machine via
`uv run polder ...`. Handig voor ontwikkeling, debugging en runs zonder
runner-quota.

## Vereisten

- [`uv`](https://docs.astral.sh/uv/) voor Python en deps.
- [Claude Code CLI](https://claude.com/claude-code) versie 2.x. Verifieer met
  `claude --version`.
- `git`. Voor de daily-update flow ook een schone werkdirectory.

Optioneel: zet `CLAUDE_BIN` als de `claude` binary niet in je `PATH` staat.

## Standaard-commando's

```bash
uv run polder daily-update                           # fetchers + validate + diff + review-pr-diff
uv run polder ingest                                 # parse + resolve + apply per bron
uv run polder ingest --commit --push                 # idem, plus git commit + push
uv run polder skill parse-staatscourant <xml>        # losse skill-aanroep
uv run polder skill parse-abd-nieuws <html>
uv run polder skill parse-organogram <pdf> <min-slug>
uv run polder backfill abd-nieuws --since 2024-01-01 # historische re-parse
uv run polder backfill staatscourant --since 2024-01-01
```

`polder backfill` accepteert `--limit N`, `--filter REGEX`, `--parallel N`,
`--no-cache` en `--max-cost-usd 5.0`. Het vervangt de oude reparse-routes.

## Hoe het werkt

Skills draaien in-process via `polder.llm.runner.run_skill`, die per
worker-thread één `claude -p --input-format stream-json --output-format
stream-json` proces openhoudt (`polder.llm.session.SkillSession`). Dat proces
ontvangt de prompts achter elkaar, zodat Anthropic's prompt-cache de ~40K
default-context maar één keer per sessie hoeft te schrijven. Call 1 betaalt de
cache-write, calls 2..N lezen die cache voor 10% van de input-prijs.

In de praktijk: voor 3000 abd-nieuws artikelen zit je rond $15 in plaats van
~$120 zonder sessie-hergebruik.

## Response-cache

Geslaagde LLM-responses landen in `_cache/llm-responses/<skill_name>/<hash>.json`
(gitignored). De cache-key bevat de sha256 van de SKILL.md-inhoud, dus zodra
je een skill aanpast worden oude responses automatisch gemist en regenereert
de pipeline ze.

Handmatig leegmaken:

```bash
rm -rf _cache/llm-responses/parse-abd-nieuws/
# of programmatisch
uv run python -c 'from polder.llm.cache import clear; clear("parse-abd-nieuws")'
```

Wil je een specifieke run zonder cache: `--no-cache` op `polder backfill`.

## Geen API-key nodig

De pipeline draait op je Claude Code subscription. Geen `ANTHROPIC_API_KEY`
ergens in env, secrets of CI nodig; `claude -p` gebruikt de auth uit je
lokale keychain.

## Troubleshooting

- `claude: command not found`: zet `export CLAUDE_BIN=/pad/naar/claude` of
  voeg `claude` aan je `PATH` toe.
- `claude -p` blijft hangen: log eerst interactief in met `claude` zodat de
  CLI je auth in de keychain bewaart.
- Validator faalt na een staging-write: dat is by design. LLM-proposals gaan
  altijd eerst naar `data/_staging/`. Pas na manuele review en de two-source
  rule mogen ze naar `data/personen/` of `data/organisaties/`.
