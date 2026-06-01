"""
Export two topic-coverage reports from sqe1.db:

  1. topic_frequency.csv  — topics at L2/L3/L4 with question counts (descending),
                            with explicit ancestor columns for each level.
  2. topic_taxonomy.csv   — full taxonomy tree (every node at L2/L3/L4, including
                            untested ones) with explicit ancestor columns.

Terminal output shows an indented tree of tested topics only.

Usage
-----
    python topic_breakdown.py
    python topic_breakdown.py --flk FLK1
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sqe1.db"
LEVEL_LABELS = {1: "area", 2: "field", 3: "subject", 4: "sub-matter"}


def get_topic_counts(conn: sqlite3.Connection, flk: str | None) -> dict[int, int]:
    """Direct question count per topic (questions tagged to that exact node)."""
    flk_filter = "AND q.flk = ?" if flk else ""
    params = [flk] if flk else []
    rows = conn.execute(f"""
        SELECT qt.topic_id, COUNT(DISTINCT qt.question_id)
        FROM question_topics qt
        JOIN questions q ON q.question_id = qt.question_id
        WHERE 1=1 {flk_filter}
        GROUP BY qt.topic_id
    """, params).fetchall()
    return dict(rows)


def build_subtree_counts(by_id: dict, direct: dict[int, int]) -> dict[int, int]:
    """Subtree question count per topic (direct + all descendants, deduplicated)."""
    children: dict[int, list[int]] = {}
    for tid, t in by_id.items():
        if t["parent_id"] is not None:
            children.setdefault(t["parent_id"], []).append(tid)

    cache: dict[int, int] = {}

    def subtree(tid: int) -> int:
        if tid in cache:
            return cache[tid]
        total = direct.get(tid, 0)
        for child in children.get(tid, []):
            total += subtree(child)
        cache[tid] = total
        return total

    return {tid: subtree(tid) for tid in by_id}


def get_all_topics(conn: sqlite3.Connection, flk: str | None) -> dict[int, dict]:
    flk_filter = "AND flk = ?" if flk else ""
    params = [flk] if flk else []
    rows = conn.execute(f"""
        SELECT topic_id, parent_id, level, code, name, flk
        FROM topics WHERE 1=1 {flk_filter}
        ORDER BY topic_id
    """, params).fetchall()
    return {r[0]: {"topic_id": r[0], "parent_id": r[1], "level": r[2],
                   "code": r[3], "name": r[4], "flk": r[5]} for r in rows}


def ancestor_levels(by_id: dict, topic_id: int) -> dict[int, str]:
    """Return {level: name_or_code} for every ancestor including self."""
    result = {}
    tid = topic_id
    while tid is not None:
        t = by_id.get(tid)
        if not t:
            break
        result[t["level"]] = t["code"] or t["name"]
        tid = t["parent_id"]
    return result


def build_row(t: dict, by_id: dict, direct: dict, subtree: dict) -> dict:
    anc = ancestor_levels(by_id, t["topic_id"])
    d = direct.get(t["topic_id"], 0)
    s = subtree.get(t["topic_id"], 0)
    return {
        "flk":            t["flk"],
        "level":          t["level"],
        "level_label":    LEVEL_LABELS[t["level"]],
        "lvl1_area":      anc.get(1, ""),
        "lvl2_field":     anc.get(2, ""),
        "lvl3_subject":   anc.get(3, ""),
        "lvl4_submatter": anc.get(4, ""),
        "direct_count":   d,
        "subtree_count":  s,
        "tested":         "yes" if s > 0 else "no",
    }


FIELDNAMES = ["flk", "level", "level_label",
              "lvl1_area", "lvl2_field", "lvl3_subject", "lvl4_submatter",
              "direct_count", "subtree_count", "tested"]


def print_tree(by_id: dict, counts: dict, flk_filter: str | None) -> None:
    """Print an indented tree of all tested topics grouped by area."""
    # Build children map
    children: dict[int | None, list] = {}
    for tid, t in by_id.items():
        children.setdefault(t["parent_id"], []).append(t)
    for kids in children.values():
        kids.sort(key=lambda t: t["name"])

    def subtree_count(tid: int) -> int:
        total = counts.get(tid, 0)
        for child in children.get(tid, []):
            total += subtree_count(child["topic_id"])
        return total

    def walk(parent_id: int | None, indent: int) -> None:
        for t in children.get(parent_id, []):
            tid = t["topic_id"]
            level = t["level"]
            if level == 1:
                total = subtree_count(tid)
                if total == 0:
                    continue
                print(f"\n{'  ' * indent}{t['code']} — {t['name']}  [{t['flk']}]  ({total} Qs)")
                walk(tid, indent + 1)
            else:
                own = counts.get(tid, 0)
                sub = subtree_count(tid) - own
                if own == 0 and sub == 0:
                    continue
                label = LEVEL_LABELS[level]
                qs_str = f"  ({own} Qs)" if own > 0 else ""
                sub_str = f"  +{sub} in subtopics" if sub > 0 and own == 0 else ""
                print(f"{'  ' * indent}[{label}] {t['name']}{qs_str}{sub_str}")
                walk(tid, indent + 1)

    walk(None, 0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flk", choices=["FLK1", "FLK2"])
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    direct = get_topic_counts(conn, args.flk)
    by_id = get_all_topics(conn, args.flk)
    subtree = build_subtree_counts(by_id, direct)

    suffix = f"_{args.flk}" if args.flk else ""

    all_rows = [build_row(t, by_id, direct, subtree) for t in by_id.values() if t["level"] >= 2]

    # ── Report 1: frequency (sorted by level then subtree count) ─────────
    freq_rows = sorted(
        all_rows,
        key=lambda r: (r["level"], -r["subtree_count"], r["lvl1_area"], r["lvl2_field"])
    )
    freq_path = DB_PATH.parent / f"topic_frequency{suffix}.csv"
    with freq_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(freq_rows)

    # ── Report 2: full taxonomy (all nodes, sorted by hierarchy) ─────────
    tax_rows = sorted(
        all_rows,
        key=lambda r: (r["flk"], r["lvl1_area"], r["lvl2_field"],
                       r["lvl3_subject"], r["lvl4_submatter"])
    )
    tax_path = DB_PATH.parent / f"topic_taxonomy{suffix}.csv"
    with tax_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(tax_rows)

    # ── Terminal: indented tree ───────────────────────────────────────────
    title = f"Tested topics — {args.flk if args.flk else 'All'}"
    print()
    print(title)
    print("=" * len(title))
    print_tree(by_id, direct, args.flk)

    # Summary
    print()
    print("Taxonomy coverage summary")
    print("-" * 40)
    for level in (2, 3, 4):
        subset = [r for r in all_rows if r["level"] == level]
        tested = sum(1 for r in subset if r["subtree_count"] > 0)
        total_qs = sum(r["direct_count"] for r in subset)
        print(f"  Level {level} ({LEVEL_LABELS[level]:<12}):  "
              f"{len(subset):>3} nodes   {tested:>3} tested   {total_qs:>4} Qs")

    print()
    print(f"  -> {freq_path.name}  (sorted by frequency)")
    print(f"  -> {tax_path.name}  (full tree, includes untested nodes)")
    print()


if __name__ == "__main__":
    main()
