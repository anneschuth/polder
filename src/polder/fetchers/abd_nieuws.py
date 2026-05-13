"""Fetcher voor de ABD-nieuws feed op algemenebestuursdienst.nl.

Bron: Algemene Bestuursdienst (BZK).
Endpoint: https://www.algemenebestuursdienst.nl/actueel (rendering) +
sitemap-index https://www.algemenebestuursdienst.nl/sitemap.xml met
sub-sitemap https://www.algemenebestuursdienst.nl/news/sitemap.xml en
nummerede sitemaps `sitemap/N.xml` voor backfill.
Formaat: HTML-artikelen met een server-rendered `<h1>`, `og:title`,
`og:description` en `<meta name="DCTERMS.modified">`. De homepage `/actueel`
linkt naar de laatste 12 berichten; de sitemap dekt alles tot 2010.
Update: meerdere keren per week (benoemingen, ontslagen, jaarverslagen).
Licentie: open (publieke webpagina rijksoverheid).

ROL VAN DEZE FETCHER: lever RUWE artikel-HTML aan in `_cache/abd-nieuws/`
plus een index-JSON met titel, datum en URL per artikel. Het LLM-werk (parsen
naar Membership-proposals) gebeurt in de skill ``parse-abd-nieuws``. Schrijf
NOOIT direct naar `data/personen/` of `data/organisaties/`.

Snelheidsvoordeel: ABD plaatst benoemingen vaak op deze feed voordat het KB
in de Staatscourant verschijnt. Daarom is dit een early-warning bron, met
two-source rule die nog steeds een tweede bevestiging eist voor merge.

Tracking issue: https://github.com/anneschuth/polder/issues/15
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from lxml import etree

logger = logging.getLogger("polder.fetchers.abd_nieuws")

__all__ = [
    "ABD_BASE",
    "CACHE_DIR",
    "NEWS_SITEMAP_URL",
    "NEWS_URL_PATTERN",
    "SITEMAP_INDEX_URL",
    "ArticleIndexEntry",
    "discover_index",
    "fetch_article",
    "fetch_index_html",
    "main",
    "parse_index_metadata",
    "parse_news_sitemap",
    "parse_sitemap_index",
    "slug_for_article",
    "url_to_article_date",
    "write_index_json",
]

ABD_BASE = "https://www.algemenebestuursdienst.nl"
SITEMAP_INDEX_URL = f"{ABD_BASE}/sitemap.xml"
NEWS_SITEMAP_URL = f"{ABD_BASE}/news/sitemap.xml"
HTTP_TIMEOUT = 60.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; polder/0.0.1; "
    "+https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)
CACHE_DIR = Path("_cache/abd-nieuws")

# `/actueel/nieuws/YYYY/MM/DD/<slug>` (slug: lowercase, koppels en cijfers).
NEWS_URL_PATTERN = re.compile(
    r"^https://www\.algemenebestuursdienst\.nl/actueel/nieuws/"
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>[a-z0-9][a-z0-9-]*)/?$"
)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------


@dataclass
class ArticleIndexEntry:
    """Een nieuwsbericht zoals het in de index/sitemap voorkomt.

    `title` en `summary` zijn pas gevuld na een fetch + parse op het artikel
    zelf. De sitemap geeft alleen URL en `lastmod`.
    """

    url: str
    article_date: str  # ISO `YYYY-MM-DD` uit de URL.
    slug: str
    lastmod: str | None = None  # ISO timestamp uit sitemap, kan None zijn.
    title: str | None = None
    summary: str | None = None
    local_path: str | None = None  # zet na fetch_article.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def url_to_article_date(url: str) -> tuple[str, str] | None:
    """`https://...nl/actueel/nieuws/2026/05/08/foo` → `("2026-05-08", "foo")`.

    Returnt None als de URL niet matcht.
    """
    m = NEWS_URL_PATTERN.match(url.rstrip("/"))
    if not m:
        return None
    iso = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
    return iso, m.group("slug")


def slug_for_article(url: str) -> str:
    """Cache-naam voor een artikel: `<slug>-<YYYY-MM-DD>` (URL-gevalideerd)."""
    parsed = url_to_article_date(url)
    if parsed is None:
        # Fallback: gebruik laatste pad-segment plus host-hash om collisions te
        # vermijden. We forceren netcase via hostname-check elders.
        path = urlparse(url).path.strip("/")
        return path.replace("/", "_") or "artikel"
    iso, slug = parsed
    return f"{slug}-{iso}"


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------


def parse_sitemap_index(xml_text: str) -> list[str]:
    """Pluk alle `<sitemap><loc>` URLs uit de sitemap-index."""
    root = etree.fromstring(xml_text.encode("utf-8"))
    out: list[str] = []
    for loc in root.findall(f"{_SITEMAP_NS}sitemap/{_SITEMAP_NS}loc"):
        if loc.text:
            out.append(loc.text.strip())
    return out


def parse_news_sitemap(xml_text: str) -> list[ArticleIndexEntry]:
    """Pluk alle `<url>` items uit een sitemap. Filter op nieuws-URL-pattern.

    Werkt voor zowel `news/sitemap.xml` (alleen recente nieuws-items) als de
    nummerede `sitemap/N.xml` (mix van pagina's, magazines, vacatures, nieuws);
    alles wat niet matcht op `NEWS_URL_PATTERN` valt eruit.
    """
    root = etree.fromstring(xml_text.encode("utf-8"))
    entries: list[ArticleIndexEntry] = []
    seen: set[str] = set()
    for url_el in root.findall(f"{_SITEMAP_NS}url"):
        loc_el = url_el.find(f"{_SITEMAP_NS}loc")
        if loc_el is None or not loc_el.text:
            continue
        url = loc_el.text.strip()
        parsed = url_to_article_date(url)
        if parsed is None:
            continue
        if url in seen:
            continue
        seen.add(url)
        iso_date, slug = parsed
        lastmod_el = url_el.find(f"{_SITEMAP_NS}lastmod")
        lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
        entries.append(
            ArticleIndexEntry(
                url=url,
                article_date=iso_date,
                slug=slug,
                lastmod=lastmod,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _today() -> date:
    return date.today()


def _http_get(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> str:
    headers = {"User-Agent": USER_AGENT}
    if client is not None:
        response = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    else:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def fetch_index_html(
    *,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> str:
    """Fetch de homepage `/actueel`. Vooral nuttig voor live-test.

    De feitelijke index-discovery loopt via `discover_index()` (sitemap).
    """
    today_str = (today or _today()).isoformat()
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"actueel-{today_str}.html"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    html = _http_get(f"{ABD_BASE}/actueel", client=client)
    if cache_dir is not None:
        (cache_dir / f"actueel-{today_str}.html").write_text(html, encoding="utf-8")
    return html


def discover_index(
    *,
    since: date | None = None,
    limit: int | None = None,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
    today: date | None = None,
    deep: bool | None = None,
) -> list[ArticleIndexEntry]:
    """Vind alle nieuws-artikel-URLs vanaf `since` (default: 30 dagen terug).

    Strategie:
    1. Lees `news/sitemap.xml` (recente items, 5 tot 20).
    2. Als `since` ouder is dan de oudste entry in die sitemap, of `deep=True`,
       loop ook de nummerede `sitemap/N.xml` af tot we genoeg dekking hebben.

    De sitemap-index zelf bevat tientallen `sitemap/N.xml` files met een
    mengeling van content-types; alleen URLs die op `NEWS_URL_PATTERN` matchen
    tellen mee.
    """
    today_obj = today or _today()
    threshold = since or (today_obj - timedelta(days=30))

    # Stap 1: news/sitemap.xml (recent).
    news_xml = _fetch_sitemap_text(NEWS_SITEMAP_URL, cache_dir=cache_dir, client=client)
    entries = parse_news_sitemap(news_xml)

    need_deep = deep is True
    if deep is None and entries:
        oldest = min((e.article_date for e in entries), default=None)
        if oldest is not None and oldest > threshold.isoformat():
            need_deep = True

    if need_deep:
        index_xml = _fetch_sitemap_text(SITEMAP_INDEX_URL, cache_dir=cache_dir, client=client)
        sub_sitemaps = parse_sitemap_index(index_xml)
        seen_urls = {e.url for e in entries}
        for sm_url in sub_sitemaps:
            if sm_url == NEWS_SITEMAP_URL:
                continue
            try:
                sm_xml = _fetch_sitemap_text(sm_url, cache_dir=cache_dir, client=client)
            except httpx.HTTPError as exc:
                logger.warning("Kon sitemap %s niet ophalen: %s", sm_url, exc)
                continue
            for entry in parse_news_sitemap(sm_xml):
                if entry.url in seen_urls:
                    continue
                seen_urls.add(entry.url)
                entries.append(entry)

    # Filter op `since` (date-grens) en sorteer descending op datum.
    threshold_iso = threshold.isoformat()
    filtered = [e for e in entries if e.article_date >= threshold_iso]
    filtered.sort(key=lambda e: (e.article_date, e.url), reverse=True)

    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def _fetch_sitemap_text(
    url: str,
    *,
    cache_dir: Path | None,
    client: httpx.Client | None,
) -> str:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        slug = urlparse(url).path.strip("/").replace("/", "_").removesuffix(".xml") or "sitemap"
        cached = cache_dir / f"{slug}.xml"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
    text = _http_get(url, client=client)
    if cache_dir is not None:
        slug = urlparse(url).path.strip("/").replace("/", "_").removesuffix(".xml") or "sitemap"
        (cache_dir / f"{slug}.xml").write_text(text, encoding="utf-8")
    return text


def fetch_article(
    url: str,
    *,
    cache_dir: Path,
    client: httpx.Client | None = None,
) -> Path:
    """Download een nieuwsbericht en cache als `<slug>-<YYYY-MM-DD>.html`.

    Idempotent: bij hit op de cache geen netwerk-call. Returnt het pad.
    """
    parsed = url_to_article_date(url)
    if parsed is None:
        raise ValueError(f"URL matcht geen ABD-nieuws-pattern: {url}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{slug_for_article(url)}.html"
    if target.exists():
        return target
    html = _http_get(url, client=client)
    target.write_text(html, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Article parsing (deterministic metadata; geen LLM)
# ---------------------------------------------------------------------------


_NIEUWSBERICHT_DATE_RE = re.compile(
    r"Nieuwsbericht\s+(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{4})"
)


def parse_index_metadata(html: str) -> dict[str, Any]:
    """Extract titel, datum, summary uit een artikel-HTML zonder LLM.

    Bronnen, in volgorde van voorkeur:
    - `<meta property="og:title">` met het site-suffix afgehakt.
    - `<h1>` als og:title leeg is.
    - `<meta property="og:description">` (== description) voor de summary.
    - `<meta name="DCTERMS.modified">` voor `lastmod`.
    - `Nieuwsbericht DD-MM-YYYY` regex op de body voor `article_date`.

    Returnt een dict met (alle optioneel): `title`, `summary`, `article_date`,
    `lastmod`, `canonical_url`. `article_date` is ISO; lastmod is de raw string.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = str(og_title["content"]).strip()
        # Knip " | Algemene Bestuursdienst"-suffix af.
        title = re.sub(r"\s*\|\s*Algemene Bestuursdienst\s*$", "", title)
        out["title"] = title
    else:
        h1 = soup.find("h1")
        if h1:
            out["title"] = h1.get_text(" ", strip=True)

    og_desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
        "meta", attrs={"name": "description"}
    )
    if og_desc and og_desc.get("content"):
        out["summary"] = str(og_desc["content"]).strip()

    modified = soup.find("meta", attrs={"name": "DCTERMS.modified"})
    if modified and modified.get("content"):
        out["lastmod"] = str(modified["content"]).strip()

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href"):
        out["canonical_url"] = str(canonical["href"]).strip()
    else:
        identifier = soup.find("meta", attrs={"name": "DCTERMS.identifier"})
        if identifier and identifier.get("content"):
            out["canonical_url"] = str(identifier["content"]).strip()

    # Body-datum: "Nieuwsbericht 08-05-2026 | 13:58".
    body_text = soup.get_text(" ", strip=False)
    m = _NIEUWSBERICHT_DATE_RE.search(body_text)
    if m:
        out["article_date"] = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"

    return out


# ---------------------------------------------------------------------------
# Index JSON
# ---------------------------------------------------------------------------


def write_index_json(
    entries: list[ArticleIndexEntry],
    *,
    cache_dir: Path,
    today: date | None = None,
) -> Path:
    """Schrijf `_cache/abd-nieuws/index.json` met alle entries.

    Bevat retrieved-datum als top-level metadata zodat downstream skills een
    timestamp hebben voor `sources[].retrieved`.
    """
    today_str = (today or _today()).isoformat()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "index.json"
    payload: dict[str, Any] = {
        "version": 1,
        "generator": "polder.fetchers.abd_nieuws",
        "retrieved": today_str,
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(value: str) -> date:
    return date.fromisoformat(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-abd-nieuws",
        description=(
            "Verzamel nieuwsberichten van algemenebestuursdienst.nl/actueel. "
            "Schrijft HTML naar _cache/abd-nieuws/ plus een index.json. "
            "Het LLM-werk loopt via de parse-abd-nieuws skill."
        ),
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="Ondergrens artikel-datum, ISO `YYYY-MM-DD` (default: 30 dagen terug).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max aantal artikelen.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Cache-root (default: _cache).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override volledige cache-pad (default: <cache>/abd-nieuws).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Geaccepteerd voor CLI-uniformiteit; output gaat naar de cache-dir.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Forceer sitemap-index walk (backfill modus).",
    )
    parser.add_argument(
        "--no-articles",
        action="store_true",
        help="Skip het downloaden van individuele artikelen; alleen index.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Geen netwerkcalls of writes; print plan en exit 0.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def _resolve_cache_dir(args: argparse.Namespace) -> Path:
    if args.cache_dir is not None:
        return args.cache_dir
    if args.cache is not None:
        return args.cache / "abd-nieuws"
    return CACHE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    today_obj = _today()
    since = args.since or (today_obj - timedelta(days=30))
    cache_dir = _resolve_cache_dir(args)

    if args.dry_run:
        print(
            f"[dry-run] would scan ABD-nieuws sinds {since.isoformat()} "
            f"(deep={args.deep}, limit={args.limit})",
            file=sys.stderr,
        )
        print(f"[dry-run] would cache to {cache_dir}", file=sys.stderr)
        print(
            f"[dry-run] would write {cache_dir}/index.json",
            file=sys.stderr,
        )
        return 0

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        entries = discover_index(
            since=since,
            limit=args.limit,
            cache_dir=cache_dir,
            client=client,
            today=today_obj,
            deep=args.deep or None,
        )
        logger.info("Discover: %d nieuws-items vanaf %s", len(entries), since.isoformat())

        if not args.no_articles:
            for entry in entries:
                try:
                    path = fetch_article(entry.url, cache_dir=cache_dir, client=client)
                    entry.local_path = str(path)
                except httpx.HTTPError as exc:
                    logger.warning("Kon artikel %s niet ophalen: %s", entry.url, exc)
                    continue
                # Verrijk titel/summary uit gedownloade HTML.
                try:
                    meta = parse_index_metadata(Path(path).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    logger.warning("Kon %s niet parsen: %s", path, exc)
                    continue
                if meta.get("title") and not entry.title:
                    entry.title = meta["title"]
                if meta.get("summary") and not entry.summary:
                    entry.summary = meta["summary"]
                if meta.get("lastmod") and not entry.lastmod:
                    entry.lastmod = meta["lastmod"]

    target = write_index_json(entries, cache_dir=cache_dir, today=today_obj)
    print(
        f"Wrote ABD-nieuws index met {len(entries)} items -> {target}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
