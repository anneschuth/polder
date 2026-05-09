"""Read-only repositories over de YAML-tree.

Lazy: `all()` parseert on-demand en cached resultaten. `get(id)` ontsluit een
LRU-cache voor herhaalde lookups op grote datasets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Generic, TypeVar

from polder.lib.models import (
    InlineMandaat,
    Mandaat,
    Organisatie,
    Persoon,
    Post,
)

T = TypeVar("T")


class Repo(Generic[T]):
    """Generic file-backed repository.

    `model.from_yaml(path)` wordt op elk YAML-bestand aangeroepen.
    """

    def __init__(self, directory: Path, model: type[T]) -> None:
        self._dir = Path(directory)
        self._model = model
        self._cache: dict[str, T] | None = None

    # --- internal -----------------------------------------------------

    def _iter_files(self) -> Iterator[Path]:
        if not self._dir.exists():
            return
        for path in sorted(self._dir.rglob("*.yaml")):
            if any(part == "_staging" for part in path.parts):
                continue
            yield path

    def _load_all(self) -> dict[str, T]:
        if self._cache is not None:
            return self._cache
        cache: dict[str, T] = {}
        for path in self._iter_files():
            obj = self._model.from_yaml(path)  # type: ignore[attr-defined]
            obj_id = getattr(obj, "id", None)
            if obj_id is None:
                continue
            cache[obj_id] = obj
        self._cache = cache
        return cache

    # --- public API ---------------------------------------------------

    def all(self) -> Iterator[T]:
        yield from self._load_all().values()

    def get(self, id: str) -> T | None:
        return self._load_all().get(id)

    def where(self, predicate: Callable[[T], bool]) -> list[T]:
        return [obj for obj in self.all() if predicate(obj)]

    def __len__(self) -> int:
        return len(self._load_all())

    def __iter__(self) -> Iterator[T]:
        return self.all()

    def __contains__(self, id: object) -> bool:
        if not isinstance(id, str):
            return False
        return id in self._load_all()

    def reload(self) -> None:
        self._cache = None


def _is_active_on(valid_from: date | None, valid_until: date | None, on_date: date) -> bool:
    if valid_from and valid_from > on_date:
        return False
    if valid_until and valid_until < on_date:
        return False
    return True


class OrgRepo(Repo[Organisatie]):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory, Organisatie)

    def by_type(self, type: str) -> list[Organisatie]:
        return [o for o in self.all() if o.type == type]

    def by_classification(self, classification: str) -> list[Organisatie]:
        return [o for o in self.all() if o.classification == classification]

    def with_identifier(self, kind: str, value: str) -> Organisatie | None:
        for org in self.all():
            if org.identifiers is None:
                continue
            current = getattr(org.identifiers, kind, None)
            if current == value:
                return org
        return None

    def active_on(self, on_date: date) -> list[Organisatie]:
        return [o for o in self.all() if _is_active_on(o.valid_from, o.valid_until, on_date)]


class PersoonRepo(Repo[Persoon]):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory, Persoon)

    def with_identifier(self, kind: str, value: str) -> Persoon | None:
        for p in self.all():
            if p.identifiers is None:
                continue
            current = getattr(p.identifiers, kind, None)
            if current == value:
                return p
        return None

    def current(self) -> list[Persoon]:
        """Iedereen met >= 1 lopend mandaat (geen einddatum)."""
        return [p for p in self.all() if _has_open_mandaat(p)]

    def with_classification(
        self,
        classification: str,
        on_date: date | None = None,
    ) -> list[Persoon]:
        """Personen met een mandaat waar de bijhorende post die classification heeft.

        Resolutie van post -> classification gebeurt buiten dit object; voor MVP
        matchen we op `mandaat.role` containment, wat een redelijke proxy is en
        geen post-lookup vereist.
        """
        target = on_date or date.today()
        out: list[Persoon] = []
        for p in self.all():
            for m in p.mandaten or []:
                if m.start_date > target:
                    continue
                if m.end_date and m.end_date < target:
                    continue
                if classification in m.role.lower() or classification == m.role.lower():
                    out.append(p)
                    break
        return out


def _has_open_mandaat(p: Persoon) -> bool:
    if not p.mandaten:
        return False
    return any(m.end_date is None for m in p.mandaten)


class PostRepo(Repo[Post]):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory, Post)

    def at_organization(self, organization_id: str) -> list[Post]:
        return [p for p in self.all() if p.organization_id == organization_id]

    def by_classification(self, classification: str) -> list[Post]:
        return [p for p in self.all() if p.classification == classification]


class MandaatRepo:
    """Mandaten zitten inline op Persoon. Deze repo verzamelt ze.

    Standalone mandaat-files worden ook geladen als de directory bestaat en
    bestanden bevat.
    """

    def __init__(self, person_repo: PersoonRepo, mandaat_dir: Path | None = None) -> None:
        self._persons = person_repo
        self._mandaat_dir = Path(mandaat_dir) if mandaat_dir else None
        self._cache: list[Mandaat] | None = None

    def _load(self) -> list[Mandaat]:
        if self._cache is not None:
            return self._cache
        out: list[Mandaat] = []
        for person in self._persons.all():
            for inline in person.mandaten or []:
                if isinstance(inline, InlineMandaat):
                    out.append(inline.to_mandaat(person_id=person.id))
        if self._mandaat_dir and self._mandaat_dir.exists():
            for path in sorted(self._mandaat_dir.rglob("*.yaml")):
                if any(part == "_staging" for part in path.parts):
                    continue
                out.append(Mandaat.from_yaml(path))
        self._cache = out
        return out

    def all(self) -> Iterator[Mandaat]:
        yield from self._load()

    def get(self, id: str) -> Mandaat | None:
        for m in self._load():
            if m.id == id:
                return m
        return None

    def where(self, predicate: Callable[[Mandaat], bool]) -> list[Mandaat]:
        return [m for m in self._load() if predicate(m)]

    def during(self, start: date, end: date) -> list[Mandaat]:
        out: list[Mandaat] = []
        for m in self._load():
            m_end = m.end_date or date.max
            if m.start_date <= end and m_end >= start:
                out.append(m)
        return out

    def at_organization(self, organization_id: str) -> list[Mandaat]:
        return [m for m in self._load() if m.organization_id == organization_id]

    def for_post(self, post_id: str) -> list[Mandaat]:
        return [m for m in self._load() if m.post_id == post_id]

    def for_person(self, person_id: str) -> list[Mandaat]:
        return [m for m in self._load() if m.person_id == person_id]

    def __iter__(self) -> Iterator[Mandaat]:
        return self.all()

    def __len__(self) -> int:
        return len(self._load())

    def reload(self) -> None:
        self._cache = None


__all__ = ["MandaatRepo", "OrgRepo", "PersoonRepo", "PostRepo", "Repo"]
