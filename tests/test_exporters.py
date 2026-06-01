"""Tests for the shared selection logic in make_pdf.py and make_anki.py.

The recently-applied fix moved selection + ordering from SQL to Python so
that --seed actually controls reproducibility. These tests verify that fix
holds for both exporters and that filters work.
"""
import random
import sqlite3

import pytest


def _all_kwargs(**overrides):
    """Build the kwargs that fetch_question_ids() requires."""
    base = dict(
        flk=None, area_codes=None, field_name=None,
        subject_name=None, submatter_name=None, count=None,
    )
    base.update(overrides)
    return base


@pytest.fixture(params=["make_pdf", "make_anki"])
def exporter(request):
    """Run the same test against both make_pdf and make_anki."""
    return __import__(request.param)


# ─────────────────────────────────────────────────────────────────────
# Reproducibility under --seed

def test_same_seed_same_result(exporter, fixture_db):
    conn = sqlite3.connect(fixture_db)
    random.seed(42)
    a = exporter.fetch_question_ids(conn, **_all_kwargs(count=5))
    random.seed(42)
    b = exporter.fetch_question_ids(conn, **_all_kwargs(count=5))
    assert a == b
    assert len(a) == 5


def test_full_selection_also_shuffles(exporter, fixture_db):
    """No --count → all 10 questions in shuffled order."""
    conn = sqlite3.connect(fixture_db)
    random.seed(42)
    qids = exporter.fetch_question_ids(conn, **_all_kwargs())
    assert sorted(qids) == list(range(1, 11))
    # Shuffled (with seed 42 on 10 items the chance of being already sorted
    # is 1/10! = vanishingly small, so this is a meaningful assertion)
    assert qids != list(range(1, 11))


# ─────────────────────────────────────────────────────────────────────
# Filters

def test_flk_filter(exporter, fixture_db):
    conn = sqlite3.connect(fixture_db)
    qids = exporter.fetch_question_ids(conn, **_all_kwargs(flk="FLK1"))
    assert sorted(qids) == [1, 2, 3, 4, 5]


def test_area_filter(exporter, fixture_db):
    conn = sqlite3.connect(fixture_db)
    qids = exporter.fetch_question_ids(conn, **_all_kwargs(area_codes=["CrL"]))
    assert sorted(qids) == [6, 7, 8, 9, 10]


def test_count_cap(exporter, fixture_db):
    conn = sqlite3.connect(fixture_db)
    random.seed(7)
    qids = exporter.fetch_question_ids(conn, **_all_kwargs(count=3))
    assert len(qids) == 3
    # All distinct, all in 1..10
    assert len(set(qids)) == 3
    assert all(1 <= q <= 10 for q in qids)


def test_flk_and_area_intersect(exporter, fixture_db):
    conn = sqlite3.connect(fixture_db)
    # CrL is FLK2; combining with FLK1 must produce empty
    qids = exporter.fetch_question_ids(
        conn, **_all_kwargs(flk="FLK1", area_codes=["CrL"])
    )
    assert qids == []
