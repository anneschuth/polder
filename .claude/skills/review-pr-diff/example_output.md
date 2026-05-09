# Dagelijkse update 2026-05-09

**Samenvatting**: 3 organisaties gewijzigd, 1 nieuw record, 2 high-stakes flags.

## High-stakes wijzigingen

| Organisatie | Veld | Voor | Na | Bron |
|---|---|---|---|---|
| `org:min-lnv` | `valid_until` | `null` | `2026-05-01` | roo |
| `org:min-fin` | `mandaten[0].end_date` | `null` | `2026-05-31` | stcrt |

## Wijzigingen per organisatie

### Ministerie van BZK (`org:min-bzk`)

- `names[0].abbr`: `BZK` -> `BZK&KR` (bron: roo)

### Ministerie van LNV (`org:min-lnv`)

- `valid_until`: `null` -> `2026-05-01` (organisatie opgeheven, bron-check vereist) (bron: roo)

### Ministerie van Financien (`org:min-fin`)

- `mandaten[0].end_date`: `null` -> `2026-05-31` (mandaat secretaris-generaal beeindigd, person:bakker-j-1968) (bron: stcrt, stcrt-2026-12345)

## Nieuwe records

- `org:gemeente-amsterdam` (gemeenten, source: roo)

## Confidence-tabel (LLM-proposals)

Geen LLM-proposals in deze run.

## Vlaggen voor menselijke review

- 2 high-stakes wijzigingen (mandaat-einde plus opheffing organisatie)
- 0 wijzigingen met confidence < 0.95
- 0 wijzigingen op rood-AVG-veld
