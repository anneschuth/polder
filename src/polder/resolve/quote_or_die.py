"""Quote-or-die-verifier voor LLM-output in `polder resolve --enrich-llm`.

Checkt dat een door de LLM aangeleverd `evidence_snippet` een letterlijke
substring is van de inhoud van `evidence_source_url`. Geen substring → reject,
de skill mag dan niet door als bron voor een birth_year of identiteit-claim.

Cache: response-cache in `_cache/quote-or-die/<sha256>.txt` zodat re-runs
hetzelfde URL niet nogmaals fetchen. URL-allowlist beperkt risico: we fetchen
alleen autoritatieve bronnen, niet random sites.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("polder.resolve.quote_or_die")

_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "www.wikidata.org",
        "wikidata.org",
        "nl.wikipedia.org",
        "en.wikipedia.org",
        "www.rijksoverheid.nl",
        "rijksoverheid.nl",
        "www.algemenebestuursdienst.nl",
        "algemenebestuursdienst.nl",
        "zoek.officielebekendmakingen.nl",
        "www.parlement.com",
        "www.eerstekamer.nl",
        "www.tweedekamer.nl",
    }
)

_USER_AGENT = (
    "polder/0.1 (https://github.com/anneschuth/polder; quote-or-die verifier)"
)

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, accenten weg, witruimte collapsed. Tags eruit voor HTML."""
    text = _TAG_RX.sub(" ", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _WS_RX.sub(" ", text)
    return text.strip()


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.txt"


def _fetch(url: str, *, cache_dir: Path, timeout: float) -> str | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, url)
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
            response = client.get(url)
            response.raise_for_status()
            body = response.text
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Fetch faalde voor %s: %s", url, exc)
        return None
    path.write_text(body, encoding="utf-8")
    return body


def make_verifier(
    *,
    cache_dir: Path | None = None,
    allowed_hosts: frozenset[str] | None = None,
    timeout: float = 10.0,
):
    """Bouw een `(snippet, url) -> bool` callable voor `enrich_resolved`.

    Een snippet matched als zijn genormaliseerde vorm voorkomt in de
    genormaliseerde body van de URL. Niet-allowed hosts geven False
    (strikt: we weigeren bronnen die we niet verifieerbaar achten).
    """
    cache_dir = cache_dir or Path("_cache") / "quote-or-die"
    hosts = allowed_hosts or _ALLOWED_HOSTS

    def verify(snippet: str, url: str) -> bool:
        if not snippet or not url:
            return False
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            return False
        if host.lower() not in hosts:
            logger.info("Host %s niet in allowlist; reject voor URL=%s", host, url)
            return False
        body = _fetch(url, cache_dir=cache_dir, timeout=timeout)
        if body is None:
            return False
        haystack = _normalize(body)
        needle = _normalize(snippet)
        if not needle:
            return False
        return needle in haystack

    return verify


__all__ = ["make_verifier"]
