"""
Shared pytest fixtures.

The fixture DB has:
- 2 lvl1 topics (BLP / FLK1, CrL / FLK2)
- 2 lvl2 topics underneath them
- 10 questions: 5 in FLK1/BLP, 5 in FLK2/CrL
- Each question has 5 options labelled A-E; option A is always the correct one
- Each option has a non-null explanation
- Each question is linked to the lvl2 topic of its area

Tests treat the fixture as read-only — it is built once per pytest session.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

# Make the share-root modules (app.py, make_pdf.py, …) importable from tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_fixture_db(path):
    schema_sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(path)
    conn.executescript(schema_sql)

    # ── Taxonomy ────────────────────────────────────────────────────
    conn.executemany(
        "INSERT INTO topics (topic_id, parent_id, level, code, name, flk) "
        "VALUES (?,?,?,?,?,?)",
        [
            (1, None, 1, "BLP", "Business Law and Practice", "FLK1"),
            (2, None, 1, "CrL", "Criminal Law",              "FLK2"),
            (3, 1,    2, None,  "Corporate governance",      "FLK1"),
            (4, 2,    2, None,  "Specified offences",        "FLK2"),
        ],
    )

    # ── Questions + options + topic links ──────────────────────────
    for qid in range(1, 11):
        flk = "FLK1" if qid <= 5 else "FLK2"
        conn.execute(
            "INSERT INTO questions "
            "(question_id, flk, source_file, source_question_num, stem, lead_in) "
            "VALUES (?,?,?,?,?,?)",
            (qid, flk, "test_fixture", qid,
             f"Test stem for question {qid}.",
             f"Test lead-in {qid}?"),
        )
        for label in "ABCDE":
            conn.execute(
                "INSERT INTO options "
                "(question_id, label, text, is_correct, explanation) "
                "VALUES (?,?,?,?,?)",
                (qid, label, f"Option {label} text for Q{qid}",
                 1 if label == "A" else 0,
                 f"Explanation for option {label} of Q{qid}."),
            )
        topic_id = 3 if flk == "FLK1" else 4
        conn.execute(
            "INSERT INTO question_topics (question_id, topic_id, flk, reasoning) "
            "VALUES (?,?,?,?)",
            (qid, topic_id, flk, "Test reasoning"),
        )
    conn.commit()
    conn.close()


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "sqe1.db"
    build_fixture_db(db_path)
    return db_path
