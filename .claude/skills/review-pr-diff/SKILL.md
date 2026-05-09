---
name: review-pr-diff
description: Genereer Nederlandstalige PR-summary uit diff.json, gegroepeerd per organisatie, met confidence-flags en high-stakes markers. Gebruik wanneer de gebruiker zegt 'review diff', 'maak PR-body', 'samenvatting diff', 'genereer PR-summary', of in English 'review diff', 'generate PR body', 'summarise daily run', of in CI na een dagelijkse fetcher-run.
version: 0.2.0
triggers:
  - review diff
  - maak PR-body
  - genereer PR-summary
  - samenvatting diff
  - generate PR body
  - summarise daily run
---

# review-pr-diff

## Doel

Lees `diff.json` (en optioneel `proposals.json`) van een dagelijkse fetcher-run en produceer een Nederlandstalige PR-body als markdown. De skill samenvat per organisatie, markeert high-stakes wijzigingen, en flagt wijzigingen die menselijke review vereisen.

## Input

- Verplicht: `diff.json` (output van `polder-diff`). Een JSON-array van entries met velden:
  - `path` (string, bijv. `data/organisaties/ministeries/bzk.yaml`)
  - `type` (`modified` of `new`)
  - `changed_fields` (lijst van JSONPath-strings)
  - `high_stakes` (bool)
  - `before` en `after` (volledige records)
- Optioneel: `proposals.json` met nieuwe records (`type: new`, `record: {...}`).
- Optioneel: `proposals_llm.json` met LLM-proposals plus `confidence` en `confidence_reasoning` per claim.

## Output

Markdown-body. Schrijf naar stdout, of naar het pad in `--out` (bijv. `pr-body.md`).

## Stappen voor de LLM

1. Laad `diff.json`. Als het bestand leeg of `[]` is, schrijf een eenregelige body: "Geen wijzigingen vandaag." en stop.
2. Laad `proposals.json` als die bestaat; voeg de entries toe aan de set "nieuwe records".
3. Laad `proposals_llm.json` als die bestaat; gebruik `confidence`-waarden voor de confidence-tabel.
4. Groepeer entries per organisatie. Bepaal de organisatie-key uit `path`:
   - Voor `data/organisaties/<type>/<slug>.yaml`: `<type>/<slug>` (de eerste twee path-segments na `data/organisaties/`).
   - Voor `data/personen/...` of `data/posten/...`: gebruik `before.organization_id` of `after.organization_id` indien aanwezig, anders een aparte sectie "Personen" of "Posten".
   - Mandaten staan binnen een organisatie-record en horen automatisch onder die organisatie.
5. Bouw markdown met deze structuur:

   ```markdown
   # Dagelijkse update {datum-iso}

   **Samenvatting**: {N} organisaties gewijzigd, {M} nieuwe records, {K} high-stakes flags.

   ## High-stakes wijzigingen

   | Organisatie | Veld | Voor | Na | Bron |
   |---|---|---|---|---|
   | org:min-bzk | valid_until | null | 2026-05-01 | roo |

   ## Wijzigingen per organisatie

   ### Ministerie van BZK (`org:min-bzk`)

   - `valid_until`: `null` -> `2026-05-01` (organisatie opgeheven, bron-check vereist)
   - `names[0].abbr`: `BZK` -> `BZK&KR`

   ## Nieuwe records

   - `org:gemeente-amsterdam` (gemeenten, source: roo)

   ## Confidence-tabel (LLM-proposals)

   | Proposal | Confidence | Reden |
   |---|---|---|
   | Benoeming P. de Vries (BZK) | 0.91 | Onder drempel 0.95 |

   ## Vlaggen voor menselijke review

   - 3 wijzigingen met confidence < 0.95
   - 1 wijziging op rood-AVG-veld
   - 2 high-stakes wijzigingen (mandaten of opheffing)
   ```

6. Voor elke wijziging in een sectie: print het JSONPath uit `changed_fields`, en de waarde uit `before` en `after` voor dat pad. Render `null` als `null`. Strings binnen backticks.
7. Voor "high-stakes": elke entry met `high_stakes: true` krijgt een rij in de high-stakes-tabel. Veld = eerste high-stakes pad (`mandaten...` of `valid_until`). Bron = `after.sources[0].id` indien aanwezig.
8. Voor de confidence-tabel: alleen entries met `confidence < 0.95` of die een rood-AVG-veld raken (zie hieronder).
9. Schrijf het resultaat naar `--out` als die optie meegegeven is, anders naar stdout.

## Rood-AVG-velden

Volgens `.claude/CLAUDE.md` van het project. Behandel deze velden als rood en flag elke wijziging:

- `birth.year` (en alles onder `birth.*`)
- `personal_email`, `personal_phone`, `home_address`, en alle persoonlijke contactvelden
- Alles dat een BSN zou kunnen bevatten: pad bevat `bsn` of `social_security`

## Harde regels

1. Niet zelf data wijzigen. Alleen samenvatten wat in `diff.json`, `proposals.json` en `proposals_llm.json` staat.
2. Wijzigingen met `confidence < 0.95` of op een rood-AVG-veld komen verplicht in de confidence-tabel en in de Vlaggen-sectie.
3. Schrijf in het Nederlands. Korte zinnen, varieer ritme. Geen em-dashes; gebruik komma, punt of haakjes.
4. Per wijziging een directe verwijzing naar `sources[].id` of `sources[].url` indien aanwezig in `after`.
5. High-stakes wijzigingen (`high_stakes: true`) altijd in de eigen tabel bovenaan, ook als ze ook elders genoemd worden.

## Voorbeeld

Zie `example_diff.json` voor input en `example_output.md` voor de bijbehorende output. Optionele helper-instructies voor het bouwen van de output staan in `SCRIPT.md`.

## Aanroep in workflow

```yaml
- name: Run Claude Code review
  uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    prompt: |
      Lees diff.json (en proposals.json indien aanwezig) en genereer een PR-body
      met de review-pr-diff skill. Schrijf de body naar pr-body.md.
    claude_args: "--model claude-haiku-4-5 --max-turns 5"
```

## Aanroep vanuit Claude Code CLI

```bash
claude "Gebruik de review-pr-diff skill op diff.json en schrijf het resultaat naar pr-body.md"
```

## Status

Actief, versie 0.2.0. Eerste skill die in CI draait.
