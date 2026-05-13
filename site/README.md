# Polder organogram

Statische, interactieve visualisatie van de Nederlandse overheid op basis van de
polder-dataset. Wordt via GitHub Pages gepubliceerd vanuit `dist/site/`.

## Lokaal draaien

```bash
uv run polder build viz
python3 -m http.server -d dist/site 8765
open http://localhost:8765/
```

## Bundeling

- `data/index.json` — top-level tegels + bestuurslagen.
- `data/org/<slug>.json` — boom per ministerie en per category-tree member,
  inclusief posten en mandaten.
- `data/cat/<slug>.json` — platte lijst voor gemeenten, waterschappen, ZBO's,
  RWT's, adviescolleges.
- `data/person/<slug>.json` — mandaat-historie per persoon, geladen door het
  side-panel.

## URL-hash deep-link

```
#org=min-bzk/onderdeel-dgdoo&date=2025-06-01&p=person:jansen-a-1970
```

- `org`: pad van slugs vanaf root.
- `date`: ISO-datum voor de tijdslider.
- `p`: persoon-id voor het side-panel.
- `q`: laatste zoekterm.
