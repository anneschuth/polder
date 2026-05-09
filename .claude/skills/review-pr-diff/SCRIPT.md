# Helper-script-instructies (optioneel)

Deze skill draait primair als prompt voor de LLM. Voor reproduceerbaarheid kan een Python-helper nuttig zijn. Onderstaande pseudocode toont de structuur die de LLM kan volgen of die later in `src/polder/` geimplementeerd kan worden.

## Pseudocode

```python
import json
from pathlib import Path
from datetime import date

def load_inputs(diff_path: Path, proposals_path: Path | None, llm_path: Path | None):
    diffs = json.loads(diff_path.read_text(encoding="utf-8"))
    proposals = json.loads(proposals_path.read_text(encoding="utf-8")) if proposals_path and proposals_path.exists() else []
    llm = json.loads(llm_path.read_text(encoding="utf-8")) if llm_path and llm_path.exists() else []
    return diffs, proposals, llm


def org_key(path: str) -> str:
    # data/organisaties/<type>/<slug>.yaml -> <type>/<slug>
    parts = path.split("/")
    if "organisaties" in parts:
        i = parts.index("organisaties")
        return "/".join(parts[i + 1 : i + 3])
    return parts[-1].replace(".yaml", "")


def get_path_value(record: dict, jsonpath: str):
    # Naive resolver voor "names[0].abbr" of "valid_until" of "mandaten[0].end_date"
    cur = record
    token = ""
    in_index = False
    for ch in jsonpath:
        if ch == ".":
            if token:
                cur = cur.get(token) if isinstance(cur, dict) else None
                token = ""
        elif ch == "[":
            if token:
                cur = cur.get(token) if isinstance(cur, dict) else None
                token = ""
            in_index = True
        elif ch == "]":
            cur = cur[int(token)] if isinstance(cur, list) else None
            token = ""
            in_index = False
        else:
            token += ch
    if token:
        cur = cur.get(token) if isinstance(cur, dict) else None
    return cur


RED_AVG_PREFIXES = ("birth.", "personal_email", "personal_phone", "home_address", "bsn", "social_security")


def is_red_avg(field: str) -> bool:
    return any(field.startswith(p) or p in field for p in RED_AVG_PREFIXES)
```

## Wanneer gebruiken

- LLM in CI: gebruik de prompt-instructies in `SKILL.md` direct, geen helper nodig.
- Lokale debug: een Python-script met bovenstaande logica produceert deterministische output zonder model-call.
- Toekomstige uitbreiding: implementeer als `polder-review-diff` console-script in `src/polder/review.py` en roep aan vanuit de workflow.
