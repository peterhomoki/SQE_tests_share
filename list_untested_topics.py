"""
List taxonomy topics with no questions (anywhere in their subtree) and write
them to untested_topics.csv for use by a question-creator script.

Only the most specific actionable level is listed per gap:
  - A lvl4 sub-matter with no questions  → listed (leaf node)
  - A lvl3 subject with no questions AND no lvl4 children  → listed (leaf)
  - A lvl3 subject whose lvl4 children are all empty  → NOT listed here
    (the lvl4 children appear instead, keeping the list at the finest grain)
  - A lvl2 field with no questions AND no deeper children  → listed (leaf)

The CSV includes all context a creator script needs to call the Opus API:
  topic_id, flk, level, level_label,
  lvl1_area, lvl2_field, lvl3_subject, lvl4_submatter,
  full_path, is_leaf

Usage
-----
    python list_untested_topics.py
    python list_untested_topics.py --flk FLK1
    python list_untested_topics.py --all-levels   # include non-leaf empty nodes too
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sqe1.db"
ABBREV_PATH = Path(__file__).parent / "area_abbreviations.csv"
LEVEL_LABELS = {1: "area", 2: "field", 3: "subject", 4: "sub-matter"}


def load_area_names() -> dict[str, str]:
    """Return {code: full_name} from area_abbreviations.csv."""
    if not ABBREV_PATH.exists():
        return {}
    with ABBREV_PATH.open(encoding="utf-8-sig") as f:
        return {r["code"]: r["full_name"] for r in csv.DictReader(f)}

FIELDNAMES = [
    "topic_id", "flk", "level", "level_label",
    "lvl1_area", "lvl2_field", "lvl3_subject", "lvl4_submatter",
    "full_path", "is_leaf",
]


def get_direct_counts(conn: sqlite3.Connection, flk: str | None) -> dict[int, int]:
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


def build_children(by_id: dict) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for tid, t in by_id.items():
        if t["parent_id"] is not None:
            children.setdefault(t["parent_id"], []).append(tid)
    return children


def build_subtree_counts(by_id: dict, direct: dict[int, int],
                         children: dict[int, list[int]]) -> dict[int, int]:
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


def ancestor_levels(by_id: dict, topic_id: int) -> dict[int, str]:
    result = {}
    tid = topic_id
    while tid is not None:
        t = by_id.get(tid)
        if not t:
            break
        result[t["level"]] = t["code"] or t["name"]
        tid = t["parent_id"]
    return result


def full_path(by_id: dict, topic_id: int) -> str:
    parts = []
    tid = topic_id
    while tid is not None:
        t = by_id.get(tid)
        if not t:
            break
        parts.append(t["code"] or t["name"])
        tid = t["parent_id"]
    parts.reverse()
    return " > ".join(parts)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--flk", choices=["FLK1", "FLK2"])
    p.add_argument("--all-levels", action="store_true",
                   help="Include non-leaf empty nodes (parent nodes whose children are also all empty)")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    direct = get_direct_counts(conn, args.flk)
    by_id = get_all_topics(conn, args.flk)
    children = build_children(by_id)
    subtree = build_subtree_counts(by_id, direct, children)
    area_names = load_area_names()

    rows = []
    for tid, t in by_id.items():
        if t["level"] < 2:
            continue
        if subtree.get(tid, 0) > 0:
            continue  # has questions somewhere in subtree — skip

        is_leaf = tid not in children  # no children in taxonomy tree

        if not args.all_levels and not is_leaf:
            continue  # skip non-leaf empty nodes unless --all-levels

        anc = ancestor_levels(by_id, tid)
        area_code = anc.get(1, "")
        rows.append({
            "topic_id":       tid,
            "flk":            t["flk"],
            "level":          t["level"],
            "level_label":    LEVEL_LABELS[t["level"]],
            "lvl1_area":      area_names.get(area_code, area_code),
            "lvl2_field":     anc.get(2, ""),
            "lvl3_subject":   anc.get(3, ""),
            "lvl4_submatter": anc.get(4, ""),
            "full_path":      full_path(by_id, tid),
            "is_leaf":        "yes" if is_leaf else "no",
        })

    rows.sort(key=lambda r: (r["flk"], r["lvl1_area"], r["lvl2_field"],
                             r["lvl3_subject"], r["lvl4_submatter"]))

    suffix = f"_{args.flk}" if args.flk else ""
    out_path = DB_PATH.parent / f"untested_topics{suffix}.csv"
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    # ── Terminal summary ──────────────────────────────────────────────
    print(f"\nUntested topics  ({args.flk if args.flk else 'All'})")
    print("=" * 50)

    current_area = None
    for r in rows:
        area = f"{r['lvl1_area']}  [{r['flk']}]"
        if area != current_area:
            print(f"\n  {area}")
            current_area = area
        indent = "    " + "  " * (r["level"] - 2)
        label = LEVEL_LABELS[r["level"]]
        name = r["lvl4_submatter"] or r["lvl3_subject"] or r["lvl2_field"]
        leaf_marker = "" if r["is_leaf"] == "yes" else "  [+children]"
        print(f"{indent}[{label}] {name}{leaf_marker}")

    print()
    print(f"Total untested topics: {len(rows)}")
    by_level = {}
    for r in rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
    for lvl in sorted(by_level):
        print(f"  Level {lvl} ({LEVEL_LABELS[lvl]}): {by_level[lvl]}")
    print(f"\n  -> saved: {out_path.name}")
    print()


if __name__ == "__main__":
    main()
