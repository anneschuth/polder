"""De `Polder` klasse: ingang voor de dataset."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from polder.lib.repository import MandaatRepo, OrgRepo, PersoonRepo, PostRepo


class Polder:
    """Read-only dataset-handle.

    >>> p = Polder.local("./polder")  # doctest: +SKIP
    >>> ministeries = p.organisaties.by_type("ministerie")  # doctest: +SKIP
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        data_dir = self.root / "data"
        self._orgs = OrgRepo(data_dir / "organisaties")
        self._personen = PersoonRepo(data_dir / "personen")
        self._posten = PostRepo(data_dir / "posten")
        self._mandaten = MandaatRepo(self._personen, mandaat_dir=data_dir / "mandaten")

    @classmethod
    def local(cls, path: str | Path) -> Self:
        root = Path(path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Polder root bestaat niet: {root}")
        if not (root / "data").exists():
            raise FileNotFoundError(
                f"Geen data/ directory in {root}. "
                "Wijs naar een polder checkout of run `polder pull`."
            )
        return cls(root)

    @property
    def organisaties(self) -> OrgRepo:
        return self._orgs

    @property
    def personen(self) -> PersoonRepo:
        return self._personen

    @property
    def posten(self) -> PostRepo:
        return self._posten

    @property
    def mandaten(self) -> MandaatRepo:
        return self._mandaten

    def reload(self) -> None:
        self._orgs.reload()
        self._personen.reload()
        self._posten.reload()
        self._mandaten.reload()


__all__ = ["Polder"]
