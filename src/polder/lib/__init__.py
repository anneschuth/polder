"""Publieke library-API.

Importeer `Polder` als ingang naar de dataset:

>>> from polder.lib import Polder  # doctest: +SKIP
>>> p = Polder.local("./polder")  # doctest: +SKIP
"""

from polder.lib.dataset import Polder
from polder.lib.initials import compact_initials, format_initials, merge_initials
from polder.lib.models import (
    Appointment,
    Birth,
    Contact,
    Event,
    InlineMandaat,
    Mandaat,
    NameVariant,
    Organisatie,
    OrgIdentifiers,
    PersonIdentifiers,
    PersonName,
    Persoon,
    Post,
    Source,
)
from polder.lib.repository import MandaatRepo, OrgRepo, PersoonRepo, PostRepo, Repo

__all__ = [
    "Appointment",
    "Birth",
    "Contact",
    "Event",
    "InlineMandaat",
    "Mandaat",
    "MandaatRepo",
    "NameVariant",
    "OrgIdentifiers",
    "OrgRepo",
    "Organisatie",
    "PersonIdentifiers",
    "PersonName",
    "Persoon",
    "PersoonRepo",
    "Polder",
    "Post",
    "PostRepo",
    "Repo",
    "Source",
    "compact_initials",
    "format_initials",
    "merge_initials",
]
