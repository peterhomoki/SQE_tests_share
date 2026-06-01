"""
Print topic coverage statistics for sqe1.db.

Usage
-----
    python stats.py
    python stats.py --flk FLK1
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sqe1.db"

BAR_WIDTH = 30


def bar(count: int, total: int, width: int = BAR_WIDTH) -> str:
    filled = round(width * count / total) if total else 0
    return f"[{'#' * filled:<{width}}] {count:>4} / {total:<4} ({100 * count / total:5.1f}%)" if total else f"[{'':<{width}}]     /      "


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flk", choices=["FLK1", "FLK2"], help="Restrict to one assessment")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    flk_filter = "AND q.flk = ?" if args.flk else ""
    flk_params = [args.flk] if args.flk else []

    # ── Overall ───────────────────────────────────────────────────────
    total_q = conn.execute(
        f"SELECT COUNT(*) FROM questions q WHERE 1=1 {flk_filter}", flk_params
    ).fetchone()[0]

    classified = conn.execute(f"""
        SELECT COUNT(DISTINCT q.question_id) FROM questions q
        JOIN question_topics qt ON qt.question_id = q.question_id
        JOIN topics t ON t.topic_id = qt.topic_id
        WHERE t.level > 1 {flk_filter}
    """, flk_params).fetchone()[0]

    area_only = conn.execute(f"""
        SELECT COUNT(DISTINCT q.question_id) FROM questions q
        WHERE EXISTS (
            SELECT 1 FROM question_topics qt WHERE qt.question_id = q.question_id
        )
        AND NOT EXISTS (
            SELECT 1 FROM question_topics qt
            JOIN topics t ON t.topic_id = qt.topic_id
            WHERE qt.question_id = q.question_id AND t.level > 1
        )
        {flk_filter}
    """, flk_params).fetchone()[0]

    unlinked = total_q - classified - area_only

    with_reasoning = conn.execute(f"""
        SELECT COUNT(DISTINCT q.question_id) FROM questions q
        JOIN question_topics qt ON qt.question_id = q.question_id
        WHERE qt.reasoning IS NOT NULL {flk_filter}
    """, flk_params).fetchone()[0]

    title = f"SQE1 Topic Statistics — {args.flk if args.flk else 'All'}"
    print()
    print(title)
    print("=" * len(title))
    print(f"  Total questions : {total_q}")
    print(f"  Classified (L>1): {bar(classified, total_q)}")
    print(f"  Area-only (L=1) : {bar(area_only,  total_q)}")
    print(f"  Unlinked        : {bar(unlinked,   total_q)}")
    print(f"  With reasoning  : {bar(with_reasoning, total_q)}")

    # ── Per FLK ───────────────────────────────────────────────────────
    if not args.flk:
        print()
        print("By assessment")
        print("-" * 40)
        for flk in ("FLK1", "FLK2"):
            n = conn.execute(
                "SELECT COUNT(*) FROM questions WHERE flk = ?", (flk,)
            ).fetchone()[0]
            cl = conn.execute("""
                SELECT COUNT(DISTINCT q.question_id) FROM questions q
                JOIN question_topics qt ON qt.question_id = q.question_id
                JOIN topics t ON t.topic_id = qt.topic_id
                WHERE t.level > 1 AND q.flk = ?
            """, (flk,)).fetchone()[0]
            print(f"  {flk}  {n:>3} questions  classified: {bar(cl, n)}")

    # ── By area (level 1) ─────────────────────────────────────────────
    print()
    print("Questions per area  (classified L>1 / total in area)")
    print("-" * 70)

    areas = conn.execute(
        f"SELECT topic_id, code, name FROM topics WHERE level = 1 "
        f"{'AND flk = ?' if args.flk else ''} ORDER BY code",
        flk_params,
    ).fetchall()

    area_rows = []
    for area_id, code, name in areas:
        total_in_area, deep = conn.execute(f"""
            WITH RECURSIVE subtree(tid) AS (
                SELECT ? UNION
                SELECT t.topic_id FROM topics t JOIN subtree s ON t.parent_id = s.tid
            )
            SELECT
                COUNT(DISTINCT qt.question_id),
                COUNT(DISTINCT CASE WHEN t2.level > 1 THEN qt.question_id END)
            FROM question_topics qt
            JOIN topics t2 ON t2.topic_id = qt.topic_id
            JOIN questions q ON q.question_id = qt.question_id
            WHERE qt.topic_id IN subtree {flk_filter}
        """, [area_id] + flk_params).fetchone()
        area_rows.append((code, name, total_in_area or 0, deep or 0))

    area_rows.sort(key=lambda r: r[2], reverse=True)
    for code, name, total_in_area, deep in area_rows:
        if total_in_area == 0:
            continue
        label = f"{code} — {name}"
        pct = f"{100 * deep / total_in_area:.0f}%" if total_in_area else "  —"
        print(f"  {label:<35} {deep:>3}/{total_in_area:<3} classified  ({pct})")

    # ── Classification depth breakdown ───────────────────────────────
    print()
    print("Classification depth  (deepest level per question)")
    print("-" * 55)
    depth_rows = conn.execute(f"""
        SELECT max_level, COUNT(*) AS cnt FROM (
            SELECT q.question_id, MAX(t.level) AS max_level
            FROM questions q
            LEFT JOIN question_topics qt ON qt.question_id = q.question_id
            LEFT JOIN topics t ON t.topic_id = qt.topic_id
            WHERE 1=1 {flk_filter}
            GROUP BY q.question_id
        )
        GROUP BY max_level
        ORDER BY max_level
    """, flk_params).fetchall()

    level_labels = {None: "unlinked", 1: "area only", 2: "field", 3: "subject", 4: "sub-matter"}
    for level, cnt in depth_rows:
        label = level_labels.get(level, str(level))
        print(f"  Level {str(level) + ' — ' + label if level else '— ' + label:<25} {bar(cnt, total_q)}")

    # ── Top 15 most-used specific topics ─────────────────────────────
    print()
    print("Top 15 topics  (level 2–4, by question count)")
    print("-" * 65)
    top_rows = conn.execute(f"""
        SELECT t.level, t.code, t.name, COUNT(DISTINCT qt.question_id) AS cnt
        FROM topics t
        JOIN question_topics qt ON qt.topic_id = t.topic_id
        JOIN questions q ON q.question_id = qt.question_id
        WHERE t.level > 1 {flk_filter}
        GROUP BY t.topic_id
        ORDER BY cnt DESC
        LIMIT 15
    """, flk_params).fetchall()

    level_short = {2: "field", 3: "subj", 4: "sub"}
    for level, code, name, cnt in top_rows:
        label = name if not code else f"{code} — {name}"
        ltype = level_short.get(level, str(level))
        print(f"  {cnt:>3}  [{ltype}] {label}")

    print()
    conn.close()


if __name__ == "__main__":
    main()
