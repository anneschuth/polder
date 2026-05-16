"""Tests voor de gedeelde cost-cap in parallelle backfill-runs (#36).

De bug: `max_cost_usd` werd alleen gecheckt in het `parallel <= 1`-pad.
Bij `parallel > 1` werkten N workers door zonder ooit de cumulatieve
kosten te checken — een echte run spendeerde $11953 tegen een $1500-cap.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from polder.backfill import abd_nieuws, staatscourant
from polder.backfill._budget import CostBudget


def test_cost_budget_no_limit_never_exceeds() -> None:
    b = CostBudget(None)
    b.add(10_000.0)
    assert b.exceeded() is False
    assert b.spent_usd == 10_000.0
    assert b.limit_usd is None


def test_cost_budget_trips_at_limit() -> None:
    b = CostBudget(1.0)
    assert b.exceeded() is False
    b.add(0.4)
    assert b.exceeded() is False
    b.add(0.6)
    assert b.exceeded() is True  # 1.0 >= 1.0


def test_cost_budget_thread_safe_accumulation() -> None:
    b = CostBudget(None)

    def worker() -> None:
        for _ in range(1000):
            b.add(0.001)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 8 threads * 1000 * 0.001 = 8.0, geen lost updates
    assert b.spent_usd == pytest.approx(8.0, rel=1e-6)


def _stub_process_one(cost_per_call: float):
    """Geef een _process_one-vervanger die elke call `cost_per_call` rekent."""

    def _impl(path: Path, staging_dir: Path, *, use_cache: bool, model):
        deltas = abd_nieuws.BackfillResult(source="stub")
        deltas.parsed = 1
        deltas.cost_usd = cost_per_call
        return "ok", deltas

    return _impl


def test_abd_backfill_parallel_respects_cost_cap(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "_cache" / "abd-nieuws"
    cache_dir.mkdir(parents=True)
    # 100 kandidaat-files, elk $1. Cap = $5.
    for i in range(100):
        (cache_dir / f"art-{i:03d}.html").write_text("x", encoding="utf-8")

    monkeypatch.setattr(abd_nieuws, "_process_one", _stub_process_one(1.0))
    monkeypatch.setattr(
        abd_nieuws, "list_candidates", lambda *a, **k: sorted(cache_dir.glob("*.html"))
    )

    result = abd_nieuws.backfill(tmp_path, parallel=4, max_cost_usd=5.0)

    assert result.cost_capped is True
    # Bounded overspend: hooguit `parallel` extra in-flight calls voorbij de
    # cap. Zonder de fix zou dit ~$100 zijn (alle 100 files).
    assert result.cost_usd <= 5.0 + 4
    assert result.parsed < 100
    assert any("cost-cap" in n for n in result.notes)


def test_abd_backfill_serial_respects_cost_cap(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "_cache" / "abd-nieuws"
    cache_dir.mkdir(parents=True)
    for i in range(20):
        (cache_dir / f"art-{i:03d}.html").write_text("x", encoding="utf-8")

    monkeypatch.setattr(abd_nieuws, "_process_one", _stub_process_one(1.0))
    monkeypatch.setattr(
        abd_nieuws, "list_candidates", lambda *a, **k: sorted(cache_dir.glob("*.html"))
    )

    result = abd_nieuws.backfill(tmp_path, parallel=1, max_cost_usd=3.0)

    # Serieel: stopt zodra spent >= cap. 3 calls van $1, 4e ziet exceeded().
    assert result.parsed == 3
    assert any("cost-cap" in n for n in result.notes)


def _stub_process_one_stc(cost_per_call: float):
    def _impl(path: Path, staging_dir: Path, *, use_cache: bool, model):
        deltas = staatscourant.BackfillResult(source="stub")
        deltas.parsed = 1
        deltas.cost_usd = cost_per_call
        return "ok", deltas

    return _impl


def test_staatscourant_backfill_parallel_respects_cost_cap(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "_cache" / "staatscourant"
    cache_dir.mkdir(parents=True)
    for i in range(60):
        (cache_dir / f"kb-{i:03d}.xml").write_text("x", encoding="utf-8")

    monkeypatch.setattr(staatscourant, "_process_one", _stub_process_one_stc(1.0))
    monkeypatch.setattr(
        staatscourant, "list_candidates", lambda *a, **k: sorted(cache_dir.glob("*.xml"))
    )

    result = staatscourant.backfill(tmp_path, parallel=4, max_cost_usd=5.0)

    assert result.cost_capped is True
    assert result.cost_usd <= 5.0 + 4
    assert result.parsed < 60
