"""Fetcher voor ABD-management organogrammen op rijksoverheid.nl.

Bron: rijksoverheid.nl per ministerie.
Endpoint-template: https://www.rijksoverheid.nl/ministeries/<ministerie>/organisatie
Formaat: HTML, regelmatig met embedded PDF en/of PNG-organogrammen.
Update: onregelmatig (bij organisatiewijziging of personele mutatie).
Licentie: open (rijksoverheidsdata).
Dekking: ABD-management onder TMG (Topmanagementgroep): directeuren, plv-directeuren,
programmadirecteuren, afdelingshoofden, MT-leden, projectleiders, kwartiermakers.
TMG zelf (SG, DG, IG, NCTV-coordinator) komt primair uit benoemings-KB's via KOOP SRU.

ROL VAN DEZE FETCHER: lever RUWE pagina's (HTML + bijbehorende PDF/PNG-bestanden)
aan in `_cache/abd-organogrammen/` plus een manifest in `data/_staging/`. Vision-extractie
van organogrammen doet de ``parse-organogram``-skill. Schrijf NOOIT direct naar
`data/personen/` of `data/posten/`.

Classification-mapping (gebruikt door de skill, zie schemas/post.schema.json):
- ``abd-tmg``: SG, DG, IG, hoofd NCTV, hoofd AIVD/MIVD (TMG-functies; meestal niet
  uit organogram maar uit benoemings-KB).
- ``abd-directeur``: directeur of plv-directeur.
- ``abd-afdelingshoofd``: afdelingshoofd of MT-lid op vergelijkbaar niveau.
- ``abd-projectleider``: projectleider of kwartiermaker met ABD-status.

Tracking issue: https://github.com/anneschuth/polder/issues/14
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("polder.fetchers.abd_organogrammen")

__all__ = [
    "CACHE_DIR",
    "MINISTERIES",
    "RIJKSOVERHEID_BASE",
    "SHARED_ORGANOGRAM",
    "STAGING_DIR",
    "MinisterieResult",
    "OrganogramAsset",
    "build_manifest",
    "discover_organisatie_subpath",
    "discover_organogram_assets",
    "discover_publicatie_links",
    "discover_subpages",
    "download_asset",
    "extract_inline_text",
    "fetch_organisatie_pagina",
    "main",
    "organisatie_url",
    "resolve_publicatie_assets",
    "write_manifest",
]

RIJKSOVERHEID_BASE = "https://www.rijksoverheid.nl"
STAGING_DIR = Path("data/_staging")
CACHE_DIR = Path("_cache/abd-organogrammen")
HTTP_TIMEOUT = 60.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; polder/0.0.1; "
    "+https://github.com/anneschuth/polder; anne.schuth@gmail.com)"
)

# Mapping van interne ministerie-slug (data/organisaties/ministeries/<slug>.yaml)
# naar rijksoverheid.nl URL-slug. URL-slug heeft "ministerie-van-" prefix.
#
# Niet elk kabinet-ministerie heeft een eigen rijksoverheid.nl-pagina: rijksoverheid
# loopt achter op de departementale herindeling. AENM, KGG en VRO delen hun pagina
# met respectievelijk JenV, EZK en BZK. Die staan in SHARED_ORGANOGRAM hieronder
# en worden niet apart gescrapt.
MINISTERIES: dict[str, str] = {
    "min-az": "ministerie-van-algemene-zaken",
    "min-bzk": "ministerie-van-binnenlandse-zaken-en-koninkrijksrelaties",
    "min-bz": "ministerie-van-buitenlandse-zaken",
    "min-def": "ministerie-van-defensie",
    "min-ezk": "ministerie-van-economische-zaken-en-klimaat",
    "min-fin": "ministerie-van-financien",
    "min-ienw": "ministerie-van-infrastructuur-en-waterstaat",
    "min-jenv": "ministerie-van-justitie-en-veiligheid",
    "min-lvvn": "ministerie-van-landbouw-visserij-voedselzekerheid-en-natuur",
    "min-ocw": "ministerie-van-onderwijs-cultuur-en-wetenschap",
    "min-szw": "ministerie-van-sociale-zaken-en-werkgelegenheid",
    "min-vws": "ministerie-van-volksgezondheid-welzijn-en-sport",
}

# Ministeries die rijksoverheid.nl niet apart publiceert. Het organogram waar
# hun ABD onder valt staat op de pagina van het ministerie hier rechts.
SHARED_ORGANOGRAM: dict[str, str] = {
    "min-aenm": "min-jenv",
    "min-kgg": "min-ezk",
    "min-vro": "min-bzk",
}

# Asset-extensies die we zien als organogram-bron.
_ASSET_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")

# Open Overheid file-pattern (rijksoverheid host hun PDFs daar tegenwoordig).
_OPEN_OVERHEID_HOSTS = ("open.overheid.nl",)


# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------


@dataclass
class OrganogramAsset:
    """Een ruwe organogram-asset (PDF, PNG, etc.) gevonden op een pagina."""

    url: str
    content_type_hint: str  # "pdf" | "image" | "html"
    link_text: str = ""
    alt_text: str = ""
    source_page: str = ""
    local_path: str | None = None  # pad in CACHE_DIR, set na download

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MinisterieResult:
    """Resultaat van het scrapen van één ministerie-organisatiepagina."""

    ministerie_slug: str  # interne slug, bijv. "min-bzk"
    url_slug: str  # rijksoverheid URL-slug
    organisatie_url: str
    organogram_subpage_url: str | None = None
    assets: list[OrganogramAsset] = field(default_factory=list)
    inline_text: str | None = None
    directie_subpages: list[str] = field(default_factory=list)
    shared_with: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ministerie_slug": self.ministerie_slug,
            "url_slug": self.url_slug,
            "organisatie_url": self.organisatie_url,
            "organogram_subpage_url": self.organogram_subpage_url,
            "assets": [a.to_dict() for a in self.assets],
            "inline_text": self.inline_text,
            "directie_subpages": list(self.directie_subpages),
            "shared_with": self.shared_with,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def organisatie_url(url_slug: str) -> str:
    """Bouw de canonical organisatiepagina-URL voor een rijksoverheid url-slug."""
    return f"{RIJKSOVERHEID_BASE}/ministeries/{url_slug}/organisatie"


def ministerie_root_url(url_slug: str) -> str:
    """Bouw de root ministerie-URL (zonder /organisatie)."""
    return f"{RIJKSOVERHEID_BASE}/ministeries/{url_slug}"


def discover_organisatie_subpath(html: str, *, base_url: str) -> str | None:
    """Zoek op de root ministerie-pagina de link naar 'Organisatie'.

    Sommige ministeries gebruiken `/organisatie-<afk>` in plaats van het canonieke
    `/organisatie` (zo heeft IenW bijvoorbeeld `/organisatie-ienw`). Deze functie
    leest de root-pagina en geeft de URL terug die in de navigatie als "Organisatie"
    gelabeld staat. Returnt None als geen match.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_path = urlparse(base_url).path.rstrip("/")
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ") or "").strip().lower()
        if text != "organisatie":
            continue
        href = str(a["href"]).strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        full_path = urlparse(full).path.rstrip("/")
        # Alleen sub-paths onder dit ministerie accepteren.
        if full_path.startswith(base_path + "/"):
            return full
    return None


def _is_asset_url(url: str) -> bool:
    """Heuristiek: PDF/PNG/JPG of open.overheid.nl document."""
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in _ASSET_EXTENSIONS):
        return True
    host = urlparse(url).netloc.lower()
    if host in _OPEN_OVERHEID_HOSTS and "/documenten/" in path and path.endswith("/file"):
        return True
    return False


def _content_type_hint(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf") or path.endswith("/file"):
        # open.overheid /file-endpoints serveren overwegend PDF.
        return "pdf"
    if any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg")):
        return "image"
    return "html"


# ---------------------------------------------------------------------------
# Cache + fetch
# ---------------------------------------------------------------------------


def _cache_dir_for(cache_root: Path, ministerie_slug: str) -> Path:
    out = cache_root / ministerie_slug
    out.mkdir(parents=True, exist_ok=True)
    return out


def _today() -> str:
    return date.today().isoformat()


def fetch_organisatie_pagina(
    url_slug: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    cache_root: Path | None = None,
    ministerie_slug: str | None = None,
    today: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Haal de organisatie-pagina van een ministerie op, cache lokaal."""
    today_str = today or _today()
    url = organisatie_url(url_slug)

    if cache_root is not None and ministerie_slug is not None:
        cache_dir = _cache_dir_for(cache_root, ministerie_slug)
        cached = cache_dir / f"organisatie-{today_str}.html"
        if cached.exists():
            return cached.read_text(encoding="utf-8")

    headers = {"User-Agent": USER_AGENT}
    if client is not None:
        response = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    else:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    text = response.text

    if cache_root is not None and ministerie_slug is not None:
        cache_dir = _cache_dir_for(cache_root, ministerie_slug)
        cached = cache_dir / f"organisatie-{today_str}.html"
        cached.write_text(text, encoding="utf-8")
    return text


def _fetch_html(
    url: str,
    *,
    cache_path: Path | None = None,
    client: httpx.Client | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> str:
    if cache_path is not None and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    headers = {"User-Agent": USER_AGENT}
    if client is not None:
        response = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    else:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    text = response.text
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    return text


def download_asset(
    asset: OrganogramAsset,
    *,
    cache_root: Path,
    ministerie_slug: str,
    client: httpx.Client | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> OrganogramAsset:
    """Download een asset naar `_cache/abd-organogrammen/<slug>/assets/`.

    Idempotent: bij hit op de cache wordt geen netwerk-call gedaan.
    Set `asset.local_path` bij succes; raised bij netwerkfouten.
    """
    cache_dir = _cache_dir_for(cache_root, ministerie_slug) / "assets"
    cache_dir.mkdir(parents=True, exist_ok=True)

    parsed = urlparse(asset.url)
    safe_name = parsed.path.strip("/").replace("/", "_") or "asset"
    if asset.content_type_hint == "pdf" and not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    target = cache_dir / safe_name

    if target.exists():
        asset.local_path = str(target)
        return asset

    headers = {"User-Agent": USER_AGENT}
    if client is not None:
        response = client.get(asset.url, headers=headers, timeout=timeout, follow_redirects=True)
    else:
        response = httpx.get(asset.url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    target.write_bytes(response.content)
    asset.local_path = str(target)
    return asset


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _looks_like_organogram(text: str) -> bool:
    return "organogram" in text.lower()


def discover_subpages(html: str, *, base_url: str) -> tuple[str | None, list[str]]:
    """Vind de organogram-subpagina + sub-directie pagina's.

    Returnt (organogram_subpage_url, lijst van directie/sub-organogram-URLs).
    Sub-pagina's zijn alle hrefs die op `/organisatie/organogram/...` eindigen
    (de directie-detail pagina's met namen op directieniveau).
    """
    soup = BeautifulSoup(html, "html.parser")
    organogram_root: str | None = None
    subpages: list[str] = []
    seen: set[str] = set()

    base_path = urlparse(base_url).path.rstrip("/")
    organogram_path = f"{base_path}/organogram"

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        full_path = urlparse(full).path

        if full_path == organogram_path or full_path == organogram_path + "/":
            organogram_root = full
        elif full_path.startswith(organogram_path + "/") and full not in seen:
            seen.add(full)
            subpages.append(full)

    return organogram_root, subpages


def discover_publicatie_links(html: str, *, base_url: str) -> list[str]:
    """Vind links naar publicatie-pagina's met organogram-PDF's.

    Op rijksoverheid.nl hebben publicaties de URL-vorm
    `/ministeries/<x>/documenten/publicaties/<jaar>/<mm>/<dd>/<slug>` waarbij
    de slug "organogram" bevat.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if "/documenten/publicaties/" not in href:
            continue
        link_text = (a.get_text(" ") or "").strip()
        if not (_looks_like_organogram(href) or _looks_like_organogram(link_text)):
            continue
        full = urljoin(base_url, href)
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def discover_organogram_assets(html: str, *, base_url: str) -> list[OrganogramAsset]:
    """Vind organogram-bestanden (PDF/PNG/JPG) op een pagina.

    Zoekt:
    - <a href> waar href een PDF/PNG/JPG is en link-tekst of href "organogram"
      bevat, OF de href is een open.overheid.nl `/file`-endpoint.
    - <img src> waar src een PNG/JPG is en alt of src "organogram" bevat.
    - Resolveert relatieve URL's via base_url.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[OrganogramAsset] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        if full in seen:
            continue
        if not _is_asset_url(full):
            continue
        link_text = (a.get_text(" ") or "").strip()
        host = urlparse(full).netloc.lower()
        is_open_overheid = host in _OPEN_OVERHEID_HOSTS
        if not (
            _looks_like_organogram(full) or _looks_like_organogram(link_text) or is_open_overheid
        ):
            continue
        seen.add(full)
        out.append(
            OrganogramAsset(
                url=full,
                content_type_hint=_content_type_hint(full),
                link_text=link_text,
                source_page=base_url,
            )
        )

    for img in soup.find_all("img"):
        src = str(img.get("src") or "").strip()
        if not src:
            continue
        full = urljoin(base_url, src)
        if full in seen:
            continue
        path = urlparse(full).path.lower()
        if not any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg")):
            continue
        alt = str(img.get("alt") or "").strip()
        if not (_looks_like_organogram(src) or _looks_like_organogram(alt)):
            continue
        seen.add(full)
        out.append(
            OrganogramAsset(
                url=full,
                content_type_hint="image",
                alt_text=alt,
                source_page=base_url,
            )
        )

    return out


def extract_inline_text(html: str) -> str | None:
    """Trek het organogram-tekstblok uit `<main>` als die er is.

    Sommige ministeries hebben hun organogram als tekst op de organogram-
    subpagina (h2/h3 met directienamen). Dit levert die ruwe tekst.
    Returnt None als de tekst leeg of onbruikbaar is.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    main = soup.find("div", id="main_content_wrapper") or soup.find("main") or soup
    text = " ".join((main.get_text(" ") or "").split())
    if not text or len(text) < 40:
        return None
    return text


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _discover_alt_organisatie_url(
    url_slug: str,
    *,
    cache_root: Path | None,
    ministerie_slug: str,
    today: str,
    client: httpx.Client | None,
) -> str | None:
    """Fallback: lees de root ministerie-pagina en vind de organisatie-link."""
    root = ministerie_root_url(url_slug)
    cache_path = None
    if cache_root is not None:
        cache_path = _cache_dir_for(cache_root, ministerie_slug) / f"root-{today}.html"
    try:
        root_html = _fetch_html(root, cache_path=cache_path, client=client)
    except httpx.HTTPError:
        return None
    return discover_organisatie_subpath(root_html, base_url=root)


def resolve_publicatie_assets(
    publicatie_urls: list[str],
    *,
    cache_root: Path | None,
    ministerie_slug: str,
    client: httpx.Client | None = None,
    today: str | None = None,
) -> list[OrganogramAsset]:
    """Volg publicatie-pagina's en haal de echte PDF/PNG-links eruit.

    Een publicatie-pagina op rijksoverheid.nl bevat metadata plus een link naar
    `https://open.overheid.nl/documenten/<uuid>/file` (de eigenlijke PDF).
    """
    today_str = today or _today()
    assets: list[OrganogramAsset] = []
    for url in publicatie_urls:
        cache_path = None
        if cache_root is not None:
            cache_dir = _cache_dir_for(cache_root, ministerie_slug)
            slug = urlparse(url).path.strip("/").replace("/", "_")
            cache_path = cache_dir / f"publicatie-{slug}-{today_str}.html"
        try:
            html = _fetch_html(url, cache_path=cache_path, client=client)
        except httpx.HTTPError as exc:
            logger.warning("Kon publicatie %s niet ophalen: %s", url, exc)
            continue
        assets.extend(discover_organogram_assets(html, base_url=url))
    return assets


def scrape_ministerie(
    ministerie_slug: str,
    url_slug: str,
    *,
    cache_root: Path | None,
    client: httpx.Client | None = None,
    today: str | None = None,
    download_pdfs: bool = True,
) -> MinisterieResult:
    """Volledige flow voor één ministerie: organisatie → organogram → assets."""
    today_str = today or _today()
    result = MinisterieResult(
        ministerie_slug=ministerie_slug,
        url_slug=url_slug,
        organisatie_url=organisatie_url(url_slug),
    )
    try:
        organisatie_html = fetch_organisatie_pagina(
            url_slug,
            cache_root=cache_root,
            ministerie_slug=ministerie_slug,
            today=today_str,
            client=client,
        )
    except httpx.HTTPError as exc:
        # Canonieke /organisatie gaf een fout. Probeer de root en zoek daar de
        # "Organisatie"-link (IenW heeft bijvoorbeeld /organisatie-ienw).
        alt_url = _discover_alt_organisatie_url(
            url_slug,
            cache_root=cache_root,
            ministerie_slug=ministerie_slug,
            today=today_str,
            client=client,
        )
        if alt_url is None:
            result.error = f"organisatie-fetch failed: {exc}"
            return result
        try:
            organisatie_html = _fetch_html(
                alt_url,
                cache_path=(
                    _cache_dir_for(cache_root, ministerie_slug) / f"organisatie-{today_str}.html"
                    if cache_root is not None
                    else None
                ),
                client=client,
            )
        except httpx.HTTPError as exc2:
            result.error = f"organisatie-fetch failed: {exc2}"
            return result
        result.organisatie_url = alt_url

    organogram_root, _ = discover_subpages(organisatie_html, base_url=result.organisatie_url)
    result.organogram_subpage_url = organogram_root

    # Publicatie-links + assets op de organisatie-pagina zelf.
    publicatie_urls = list(
        discover_publicatie_links(organisatie_html, base_url=result.organisatie_url)
    )
    direct_assets = list(
        discover_organogram_assets(organisatie_html, base_url=result.organisatie_url)
    )

    # Organogram-subpagina (vaak hier de echte content).
    if organogram_root is not None:
        cache_path = None
        if cache_root is not None:
            cache_dir = _cache_dir_for(cache_root, ministerie_slug)
            cache_path = cache_dir / f"organogram-{today_str}.html"
        try:
            organogram_html = _fetch_html(organogram_root, cache_path=cache_path, client=client)
        except httpx.HTTPError as exc:
            logger.warning("Kon organogram-subpage %s niet ophalen: %s", organogram_root, exc)
            organogram_html = ""
        if organogram_html:
            publicatie_urls.extend(
                discover_publicatie_links(organogram_html, base_url=organogram_root)
            )
            direct_assets.extend(
                discover_organogram_assets(organogram_html, base_url=organogram_root)
            )
            _, subpages = discover_subpages(organogram_html, base_url=result.organisatie_url)
            result.directie_subpages = subpages
            result.inline_text = extract_inline_text(organogram_html)

    # Resolve publicatie-pagina's naar PDF-assets.
    publicatie_urls_dedup: list[str] = []
    seen_pub: set[str] = set()
    for url in publicatie_urls:
        if url in seen_pub:
            continue
        seen_pub.add(url)
        publicatie_urls_dedup.append(url)

    pub_assets = resolve_publicatie_assets(
        publicatie_urls_dedup,
        cache_root=cache_root,
        ministerie_slug=ministerie_slug,
        client=client,
        today=today_str,
    )

    # Combineer + dedup op URL.
    seen_urls: set[str] = set()
    all_assets: list[OrganogramAsset] = []
    for a in (*direct_assets, *pub_assets):
        if a.url in seen_urls:
            continue
        seen_urls.add(a.url)
        all_assets.append(a)

    # Download PDFs naar cache (als opgevraagd).
    if download_pdfs and cache_root is not None:
        for asset in all_assets:
            if asset.content_type_hint not in ("pdf", "image"):
                continue
            try:
                download_asset(
                    asset,
                    cache_root=cache_root,
                    ministerie_slug=ministerie_slug,
                    client=client,
                )
            except httpx.HTTPError as exc:
                logger.warning("Download faalde voor %s: %s", asset.url, exc)

    result.assets = all_assets
    return result


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    results: list[MinisterieResult],
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """Bouw het manifest-dict (JSON-serializeable)."""
    today_str = today or _today()
    return {
        "version": 1,
        "generator": "polder.fetchers.abd_organogrammen",
        "retrieved": today_str,
        "ministeries": [r.to_dict() for r in results],
    }


def write_manifest(
    manifest: dict[str, Any],
    *,
    staging_dir: Path = STAGING_DIR,
    today: str | None = None,
) -> Path:
    """Schrijf manifest naar `data/_staging/abd-manifest-{date}.json`."""
    today_str = today or manifest.get("retrieved") or _today()
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / f"abd-manifest-{today_str}.json"
    target.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polder-fetch-abd",
        description=(
            "Verzamel ruwe organogram-bronnen per ministerie van rijksoverheid.nl. "
            "Schrijft een manifest naar data/_staging/ en cachet PDF/HTML lokaal."
        ),
    )
    parser.add_argument(
        "--ministerie",
        type=str,
        default=None,
        help="Slug van één ministerie (bijv. 'min-bzk').",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Loop alle ministeries (~15) af.",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=CACHE_DIR,
        help="Root voor PDF/HTML-cache (default: _cache/abd-organogrammen).",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=STAGING_DIR,
        help="Output-directory voor manifest (default: data/_staging).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Geen netwerkcalls of writes; print plan en exit 0.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Sla het downloaden van PDF-assets over (alleen URL's vastleggen).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.all and args.ministerie is None:
        parser.error("Geef --ministerie <slug> of --all op.")

    known = set(MINISTERIES) | set(SHARED_ORGANOGRAM)
    if args.ministerie is not None:
        if args.ministerie not in known:
            parser.error(
                f"Onbekende ministerie-slug: {args.ministerie!r}. "
                f"Bekend: {', '.join(sorted(known))}"
            )
        if args.ministerie in SHARED_ORGANOGRAM:
            targets: dict[str, str] = {}
        else:
            targets = {args.ministerie: MINISTERIES[args.ministerie]}
    else:
        targets = dict(MINISTERIES)

    if args.dry_run:
        for slug, url_slug in targets.items():
            print(
                f"[dry-run] would fetch {slug} -> {organisatie_url(url_slug)}",
                file=sys.stderr,
            )
        print(
            f"[dry-run] would write manifest to {args.staging_dir}/abd-manifest-{_today()}.json",
            file=sys.stderr,
        )
        return 0

    today_str = _today()
    results: list[MinisterieResult] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        for slug, url_slug in targets.items():
            logger.info("Scrape %s (%s)", slug, url_slug)
            result = scrape_ministerie(
                slug,
                url_slug,
                cache_root=args.cache_root,
                client=client,
                today=today_str,
                download_pdfs=not args.no_download,
            )
            if result.error:
                logger.warning("%s: %s", slug, result.error)
            else:
                logger.info(
                    "%s: %d assets, %d directie-subpages",
                    slug,
                    len(result.assets),
                    len(result.directie_subpages),
                )
            results.append(result)

    # Voeg shared-organogram-stubs toe bij --all, óf wanneer expliciet om een
    # shared-ministerie is gevraagd. Geen netwerk: alleen een verwijzing.
    if args.all or (args.ministerie in SHARED_ORGANOGRAM):
        shared_slugs = (
            SHARED_ORGANOGRAM if args.all else {args.ministerie: SHARED_ORGANOGRAM[args.ministerie]}
        )
        for slug, parent_slug in shared_slugs.items():
            parent_url_slug = MINISTERIES[parent_slug]
            results.append(
                MinisterieResult(
                    ministerie_slug=slug,
                    url_slug=parent_url_slug,
                    organisatie_url=organisatie_url(parent_url_slug),
                    shared_with=parent_slug,
                )
            )
            logger.info("%s: shared organogram met %s", slug, parent_slug)

    manifest = build_manifest(results, today=today_str)
    target = write_manifest(manifest, staging_dir=args.staging_dir, today=today_str)
    n_assets = sum(len(r.assets) for r in results)
    n_errors = sum(1 for r in results if r.error)
    print(
        f"Wrote manifest with {len(results)} ministeries, {n_assets} assets, "
        f"{n_errors} errors -> {target}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
