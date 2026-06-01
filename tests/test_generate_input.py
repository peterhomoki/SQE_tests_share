"""Tests for generate_test_input.py — the helper that produces the JSON
input consumed by run_llm_test.py."""
import sqlite3


def test_fetch_questions_basic(fixture_db):
    import generate_test_input as gti
    conn = sqlite3.connect(fixture_db)
    qs = gti.fetch_questions(conn, [1, 2, 3])
    assert len(qs) == 3
    for q in qs:
        assert "qid" in q and "stem" in q and "lead_in" in q
        assert q["correct_answer"] == "A"            # always A in the fixture
        assert len(q["options"]) == 5
        assert {o["label"] for o in q["options"]} == set("ABCDE")
        # Exactly one is_correct=True
        assert sum(1 for o in q["options"] if o["is_correct"]) == 1


def test_fetch_questions_topic_chain(fixture_db):
    import generate_test_input as gti
    conn = sqlite3.connect(fixture_db)
    qs = gti.fetch_questions(conn, [1])
    topic = qs[0]["topic"]
    assert topic["lvl1"] == "Business Law and Practice"
    assert topic["lvl2"] == "Corporate governance"


def test_get_subtree_includes_descendants(fixture_db):
    import generate_test_input as gti
    conn = sqlite3.connect(fixture_db)
    subtree = gti.get_subtree_ids(conn, 1)       # BLP area
    # Should include the area itself (1) and its lvl2 child (3)
    assert {1, 3}.issubset(subtree)
    # Should NOT include topics under the other area
    assert 2 not in subtree
    assert 4 not in subtree


def test_get_topic_chain(fixture_db):
    import generate_test_input as gti
    conn = sqlite3.connect(fixture_db)
    chain = gti.get_topic_chain(conn, 3)         # Corporate governance
    assert chain["topic_id"] == 3
    assert chain["lvl1"] == "Business Law and Practice"
    assert chain["lvl2"] == "Corporate governance"
    assert chain["lvl3"] is None
    assert chain["lvl4"] is None
