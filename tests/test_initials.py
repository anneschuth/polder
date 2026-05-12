"""Tests voor polder.lib.initials."""

from __future__ import annotations

import pytest

from polder.lib.initials import compact_initials, format_initials, merge_initials


@pytest.mark.parametrize(
    "input_value,expected",
    [
        ("M.P.", "M.P."),
        ("M.P", "M.P."),
        ("M P", "M.P."),
        ("MP", "M.P."),
        ("mp", "M.P."),
        ("m.p.", "M.P."),
        ("W.E.A.", "W.E.A."),
        ("Z", "Z."),
        ("", None),
        (None, None),
        ("   ", None),
        ("123", None),  # geen letters
    ],
)
def test_format_initials(input_value: str | None, expected: str | None) -> None:
    assert format_initials(input_value) == expected


@pytest.mark.parametrize(
    "input_value,expected",
    [
        ("M.P.", "mp"),
        ("M.P", "mp"),
        ("M P", "mp"),
        ("MP", "mp"),
        ("mp", "mp"),
        ("W.E.A.", "wea"),
        ("Z", "z"),
        ("", ""),
        (None, ""),
        ("123", ""),
    ],
)
def test_compact_initials(input_value: str | None, expected: str) -> None:
    assert compact_initials(input_value) == expected


def test_merge_initials_prefix_wins() -> None:
    """W. is prefix van W.B. → langste wint."""
    assert merge_initials("W.", "W.B.") == "W.B."
    assert merge_initials("W.B.", "W.") == "W.B."


def test_merge_initials_equal() -> None:
    assert merge_initials("M.P.", "M.P.") == "M.P."


def test_merge_initials_disjoint() -> None:
    """W.B. en J.J. zijn niet prefix-related → eerste arg wint."""
    assert merge_initials("W.B.", "J.J.") == "W.B."


def test_merge_initials_none() -> None:
    assert merge_initials(None, None) is None
    assert merge_initials("M.", None) == "M."
    assert merge_initials(None, "M.") == "M."


def test_format_strips_accents() -> None:
    """Diakrieten worden weggegooid."""
    assert format_initials("é") == "E."


def test_compact_strips_accents() -> None:
    assert compact_initials("é") == "e"
