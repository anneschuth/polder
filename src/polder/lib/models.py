"""Pydantic v2 models, handgeschreven op basis van de JSON Schemas in `schemas/`.

Veldnamen volgen het schema 1-op-1. Lees deze module met `schemas/*.schema.json`
ernaast als referentie. Bij schema-wijzigingen update beide.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

OrgType = Literal[
    "ministerie",
    "agentschap",
    "zbo",
    "rwt",
    "hoge-college",
    "gemeente",
    "provincie",
    "waterschap",
    "gemeenschappelijke-regeling",
    "adviescollege",
    "inspectie",
    "rechterlijke-instantie",
    "politie",
    "openbaar-ministerie",
    "caribisch-openbaar-lichaam",
    "organisatieonderdeel",
]

PostClassification = Literal[
    "bewindspersoon",
    "abd-tmg",
    "abd-directeur",
    "abd-afdelingshoofd",
    "abd-projectleider",
    "gemeentesecretaris",
    "provinciesecretaris",
    "kamerlid",
    "statenlid",
    "raadslid",
    "commissaris-vd-koning",
    "gedeputeerde",
    "wethouder",
    "burgemeester",
    "dijkgraaf",
    "db-waterschap",
    "ab-waterschap",
    "voorzitter-hcs",
    "lid-hcs",
    "rvb-zbo",
    "rechter",
    "officier-van-justitie",
    "gezaghebber",
    "griffier",
    "overig",
]

EventType = Literal[
    "organization-renamed",
    "organization-merged",
    "organization-split",
    "organization-dissolved",
    "post-created",
    "post-abolished",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Source(_Base):
    id: str
    url: str
    retrieved: date
    fields: list[str] | None = None


class NameVariant(_Base):
    value: str
    abbr: str | None = None
    valid_from: date
    valid_until: date | None = None


class Contact(_Base):
    website: str | None = None
    bezoekadres: str | None = None
    postadres: str | None = None
    email: str | None = None


class OrgIdentifiers(_Base):
    oin: str | None = None
    tooi: str | None = None
    wikidata: str | None = None
    roo_id: str | None = None
    kvk: str | None = None
    rsin: str | None = None


class PersonIdentifiers(_Base):
    wikidata: str | None = None
    tk_persoon_id: str | None = None
    abd_id: str | None = None
    allmanak_id: str | None = None


class PersonName(_Base):
    full: str
    family: str
    tussenvoegsel: str | None = None
    given: str | None = None
    initials: str | None = None
    honorifics_pre: list[str] | None = None
    honorifics_post: list[str] | None = None


class Birth(_Base):
    year: int = Field(ge=1850, le=2030)


class Appointment(_Base):
    decision: str | None = None
    staatscourant_url: str | None = None
    kb_nummer: str | None = None


class Mandaat(_Base):
    """Standalone mandaat record. Inline mandaten op Persoon delen dit type
    afgezien van het ontbreken van `person_id` (zie `InlineMandaat`)."""

    id: str
    person_id: str
    organization_id: str
    post_id: str
    role: str
    start_date: date
    end_date: date | None = None
    appointment: Appointment | None = None
    sources: list[Source]
    confidence: float | None = Field(default=None, ge=0, le=1)


class InlineMandaat(_Base):
    """Variant zoals in `persoon.schema.json#properties.mandaten.items`,
    zonder `person_id` (komt van de bevattende persoon)."""

    id: str
    organization_id: str
    post_id: str
    role: str
    start_date: date
    end_date: date | None = None
    appointment: Appointment | None = None
    sources: list[Source]
    confidence: float | None = Field(default=None, ge=0, le=1)

    def to_mandaat(self, person_id: str) -> Mandaat:
        """Promoot tot een volwaardig Mandaat met person_id."""
        return Mandaat(person_id=person_id, **self.model_dump(exclude_none=True))


class Organisatie(_Base):
    id: str = Field(pattern=r"^org:[a-z0-9-]+$")
    type: OrgType
    classification: str | None = None
    parent_id: str | None = None
    identifiers: OrgIdentifiers | None = None
    names: list[NameVariant]
    contact: Contact | None = None
    valid_from: date
    valid_until: date | None = None
    successor_id: str | None = Field(default=None, pattern=r"^org:[a-z0-9-]+$")
    predecessor_id: list[str] | None = Field(default=None)
    sources: list[Source]

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        return _model_from_yaml(cls, path)

    def to_yaml(self, path: Path) -> None:
        _model_to_yaml(self, path)


class Persoon(_Base):
    id: str = Field(
        pattern=r"^person:([a-z][a-z0-9-]*-)?([0-9]{4}|[0-9]{7,}|[0-9a-f]{8})$"
    )
    identifiers: PersonIdentifiers | None = None
    name: PersonName
    birth: Birth | None = None
    gender: Literal["m", "f", "x"] | None = None
    mandaten: list[InlineMandaat] | None = None
    sources: list[Source]

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        return _model_from_yaml(cls, path)

    def to_yaml(self, path: Path) -> None:
        _model_to_yaml(self, path)


class Post(_Base):
    id: str = Field(pattern=r"^post:[a-z0-9-]+$")
    organization_id: str = Field(pattern=r"^org:[a-z0-9-]+$")
    label: str
    classification: PostClassification
    seat_count: int | None = Field(default=None, ge=1)
    valid_from: date
    valid_until: date | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        return _model_from_yaml(cls, path)

    def to_yaml(self, path: Path) -> None:
        _model_to_yaml(self, path)


class Event(_Base):
    id: str
    type: EventType
    date: date
    affected_org_ids: list[str]
    description: str
    sources: list[Source]

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        return _model_from_yaml(cls, path)

    def to_yaml(self, path: Path) -> None:
        _model_to_yaml(self, path)


# Allow Mandaat to also load standalone YAML.
Mandaat.from_yaml = classmethod(lambda cls, path: _model_from_yaml(cls, path))  # type: ignore[assignment]
Mandaat.to_yaml = lambda self, path: _model_to_yaml(self, path)  # type: ignore[assignment]


def _model_from_yaml(cls: type[BaseModel], path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return cls.model_validate(data)


def _model_to_yaml(model: BaseModel, path: Path) -> None:
    data = model.model_dump(mode="json", exclude_none=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


__all__ = [
    "Appointment",
    "Birth",
    "Contact",
    "Event",
    "EventType",
    "InlineMandaat",
    "Mandaat",
    "NameVariant",
    "OrgIdentifiers",
    "OrgType",
    "Organisatie",
    "PersonIdentifiers",
    "PersonName",
    "Persoon",
    "Post",
    "PostClassification",
    "Source",
]
