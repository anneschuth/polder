"""Fetcher voor verkiezingsuitslagen en kandidaatlijsten van de Kiesraad.

Bron: Kiesraad, gepubliceerd via data.overheid.nl (CKAN).
Endpoint: https://data.overheid.nl/data/api/3/action/package_search
URI-stelsel: https://data.overheid.nl/dataset/<dataset-name>
Formaat: per verkiezing een dataset met CSV en/of EML-XML
(Election Markup Language, NL-profiel).
Update: per verkiezing (TK, EK, EP, PS, GR, WS-bestuur, BES-eilandsraden,
referenda).
Licentie: open (CC0 / Public Domain Mark op de meeste datasets).
Dekking: officiele uitslagen, kandidaatlijsten, samenstelling van vertegenwoordigende
organen direct na de uitslag. Per verkiezing aparte dataset; geen unified API.

ROL VAN DEZE FETCHER: download CSV/EML-resources naar `_cache/kiesraad/<dataset>/`.
Mapping naar polder-records (kandidaten als personen, gekozenen als mandaten) doet
een vervolg-PR via een aparte mapping-pass.

Tracking issue: https://github.com/anneschuth/polder/issues/TODO-kiesraad
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("polder.fetchers.kiesraad")

__all__ = [
    "CACHE_DIR",
    "DATA_OVERHEID_API",
    "KIESRAAD_AUTHORITY",
    "fetch_dataset",
    "list_datasets",
    "main",
]

DATA_OVERHEID_API = "https://data.overheid.nl/data/api/3/action"
KIESRAAD_AUTHORITY = "http://standaarden.overheid.nl/owms/terms/Kiesraad"
CACHE_DIR = Path("_cache/kiesraad")
HTTP_TIMEOUT = 60.0
USER_AGENT = "polder-bot/0.1 (https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
PAGE_SIZE = 200


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _http_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if client is None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as inner:
            return inner.get(url, params=params, headers=headers)
    return client.get(url, params=params, headers=headers)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_datasets(
    *,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Lijst Kiesraad-datasets via CKAN ``package_search``.

    Filtert op ``authority:"<KIESRAAD_AUTHORITY>"`` en pageert door tot er geen
    resultaten meer komen (of tot ``max_pages * PAGE_SIZE``).
    """
    url = f"{DATA_OVERHEID_API}/package_search"
    results: list[dict[str, Any]] = []
    for page in range(max_pages):
        params = {
            "fq": f'authority:"{KIESRAAD_AUTHORITY}"',
            "rows": str(PAGE_SIZE),
            "start": str(page * PAGE_SIZE),
        }
        response = _http_get(url, params=params, timeout=timeout, client=client)
        response.raise_for_status()
        payload = response.json()
        page_results = list(payload.get("result", {}).get("results", []))
        if not page_results:
            break
        results.extend(page_results)
        if len(page_results) < PAGE_SIZE:
            break
    return results


_RESOURCE_EXT_RE = re.compile(r"\.(csv|xml|eml|xlsx?|json|zip|ods)$", re.IGNORECASE)


def _resource_filename(resource: dict[str, Any], fallback: str) -> str:
    """Bepaal een veilige bestandsnaam voor een CKAN-resource."""
    name = resource.get("name") or ""
    url = resource.get("url") or ""
    fmt = (resource.get("format") or "").lower()
    last = url.rstrip("/").split("/")[-1] if url else ""
    if last and _RESOURCE_EXT_RE.search(last):
        candidate = last
    elif name and _RESOURCE_EXT_RE.search(name):
        candidate = name
    else:
        ext = fmt if fmt and fmt.isalnum() else "bin"
        base = name or last or fallback
        candidate = f"{base}.{ext}"
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-")
    return candidate or f"{fallback}.bin"


def fetch_dataset(
    dataset_id: str,
    *,
    cache_dir: Path | None = None,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> Path:
    """Download alle resources van een dataset naar `<cache>/<dataset_id>/`.

    Args:
        dataset_id: dataset-name op data.overheid.nl, bijv.
            ``"verkiezingsuitslag-tweede-kamer-2023"``.

    Returnt het pad naar de dataset-directory. Resources die al bestaan worden niet
    opnieuw gedownload.
    """
    base = (cache_dir or CACHE_DIR) / dataset_id
    show_url = f"{DATA_OVERHEID_API}/package_show"
    response = _http_get(
        show_url,
        params={"id": dataset_id},
        timeout=timeout,
        client=client,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success", False):
        raise ValueError(f"CKAN package_show faalde voor {dataset_id!r}: {payload!r}")
    resources = payload.get("result", {}).get("resources") or []

    if not dry_run:
        base.mkdir(parents=True, exist_ok=True)

    for idx, resource in enumerate(resources):
        url = resource.get("url")
        if not url:
            continue
        filename = _resource_filename(resource, fallback=f"resource-{idx:03d}")
        target = base / filename
        if target.exists() and not dry_run:
            logger.debug("Cache hit: %s", target)
            continue
        if dry_run:
            logger.info("DRY-RUN zou downloaden: %s -> %s", url, target)
            continue
        try:
            r = _http_get(url, timeout=timeout, client=client)
            r.raise_for_status()
            target.write_bytes(r.content)
        except httpx.HTTPError as exc:
            logger.warning("Resource-download faalde (%s): %s", url, exc)
            continue
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-kiesraad",
        description=(
            "Download Kiesraad-datasets (kandidaatlijsten en uitslagen) via de "
            "data.overheid.nl CKAN-API naar _cache/kiesraad/."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Lijst alle Kiesraad-datasets op stdout (name + title).",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Dataset-ID (CKAN ``name``) om te downloaden.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=CACHE_DIR,
        help=f"Cache-directory (default: {CACHE_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Toon alleen welke datasets/resources opgehaald zouden worden.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.list and not args.dataset:
        parser.error("geef --list of --dataset <id> mee")

    try:
        if args.list:
            datasets = list_datasets()
            for ds in datasets:
                name = ds.get("name") or "?"
                title = ds.get("title") or ""
                print(f"{name}\t{title}")
            print(
                f"Kiesraad: {len(datasets)} datasets gevonden",
                file=sys.stderr,
            )
        if args.dataset:
            target = fetch_dataset(
                args.dataset,
                cache_dir=args.cache,
                dry_run=args.dry_run,
            )
            suffix = " (dry-run)" if args.dry_run else ""
            print(f"Kiesraad: dataset gecached in {target}{suffix}", file=sys.stderr)
    except httpx.HTTPError as exc:
        print(f"polder-fetch-kiesraad: HTTP-fout: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
