"""
Analyse a CSV of wrong-answer QIDs to identify weakest areas / fields / subjects.

The CSV must have a `qid` column. Other columns are ignored.

Standalone usage:
  python analyse_errors.py wrong_qids.csv
  python analyse_errors.py wrong_qids.csv --output errors_summary.txt
  python analyse_errors.py wrong_qids.csv --top 5

Importable: `analyse(qids: list[int]) -> dict` returns the structured result;
`format_text(result) -> str` produces the human-readable summary.
"""
from __future__ import annotations
import argparse, csv, sqlite3, sys
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent / "sqe1.db"


def get_topic_ancestors(conn, topic_id):
    """Walk up the taxonomy from topic_id, returning {level: (code, name)}."""
    chain = {}
    tid = topic_id
    while tid is not None:
        row = conn.execute(
            "SELECT topic_id, parent_id, level, code, name FROM topics WHERE topic_id=?",
            (tid,)).fetchone()
        if not row:
            break
        _, parent, level, code, name = row
        chain[level] = (code, name)
        tid = parent
    return chain


def analyse(qids):
    """Return {total, missing_qids, by_area, by_field, by_subject, by_submatter}."""
    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    by_area = Counter()       # lvl1 — "BLP" / "Business Law and Practice"
    by_field = Counter()      # lvl2 — "BLP > Corporate governance"
    by_subject = Counter()    # lvl3
    by_submatter = Counter()  # lvl4

    missing = []
    for qid in qids:
        row = conn.execute(
            "SELECT qt.topic_id FROM question_topics qt WHERE qt.question_id=? "
            "ORDER BY (SELECT level FROM topics WHERE topic_id=qt.topic_id) DESC",
            (qid,)).fetchone()
        if not row:
            missing.append(qid)
            continue
        chain = get_topic_ancestors(conn, row[0])
        area    = chain.get(1)
        field   = chain.get(2)
        subject = chain.get(3)
        sub     = chain.get(4)
        # Build a label that doesn't repeat the code if name == code (e.g. BLP / BLP)
        def area_label(a):
            code, name = a
            if code and name and code != name:
                return f"{code} — {name}"
            return code or name
        if area:
            by_area[area_label(area)] += 1
        if field and area:
            by_field[f"{area_label(area)} › {field[1]}"] += 1
        if subject and area:
            by_subject[f"{area_label(area)} › {field[1] if field else '?'} › {subject[1]}"] += 1
        if sub and area:
            by_submatter[f"{area_label(area)} › {field[1] if field else '?'} › {subject[1] if subject else '?'} › {sub[1]}"] += 1

    conn.close()
    return {
        "total":         len(qids),
        "missing_qids":  missing,
        "by_area":       by_area,
        "by_field":      by_field,
        "by_subject":    by_subject,
        "by_submatter":  by_submatter,
    }


def format_text(result, top=10):
    """Human-readable summary."""
    out = []
    total   = result["total"]
    missing = result["missing_qids"]
    out.append(f"Analysis of {total} wrong-answer QIDs")
    out.append("=" * 60)
    out.append("")
    out.append(f"  Total wrong QIDs analysed : {total}")
    out.append(f"  QIDs with no topic link   : {len(missing)}"
               + (f"  ({sorted(missing)})" if missing else ""))
    out.append("")

    def section(title, counter):
        out.append(f"### {title}")
        out.append("")
        if not counter:
            out.append("(no data)")
            out.append("")
            return
        max_label = max(len(k) for k, _ in counter.most_common(top))
        out.append(f"{'count':>6}  {'label':<{max_label}}")
        out.append(f"{'-'*6}  {'-'*max_label}")
        for label, count in counter.most_common(top):
            out.append(f"{count:>6}  {label:<{max_label}}")
        out.append("")

    section(f"Top {top} weakest AREAS (lvl 1)",     result["by_area"])
    section(f"Top {top} weakest FIELDS (lvl 2)",    result["by_field"])
    section(f"Top {top} weakest SUBJECTS (lvl 3)",  result["by_subject"])
    section(f"Top {top} weakest SUB-MATTERS (lvl 4)", result["by_submatter"])

    # Actionable guidance
    out.append("### Recommended focus")
    out.append("")
    if result["by_area"]:
        worst_area, worst_count = result["by_area"].most_common(1)[0]
        out.append(f"- Biggest area: {worst_area} ({worst_count} errors)")
    if result["by_subject"]:
        worst_subject, c = result["by_subject"].most_common(1)[0]
        out.append(f"- Single most-missed subject: {worst_subject} ({c} errors)")
    if result["by_submatter"]:
        worst_sub, c = result["by_submatter"].most_common(1)[0]
        out.append(f"- Single most-missed sub-matter: {worst_sub} ({c} errors)")
    out.append("")
    return "\n".join(out)


def read_qids_from_csv(path):
    qids = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "qid" not in (reader.fieldnames or []):
            sys.exit(f"CSV must have a 'qid' column (found: {reader.fieldnames})")
        for row in reader:
            v = (row.get("qid") or "").strip()
            if not v: continue
            try:
                qids.append(int(v))
            except ValueError:
                pass
    return qids


def main():
    p = argparse.ArgumentParser(description="Analyse wrong-answer QIDs against the taxonomy")
    p.add_argument("csv", help="CSV file with a 'qid' column")
    p.add_argument("--top", type=int, default=10, help="Top-N rows per section (default 10)")
    p.add_argument("--output", help="Write the summary to this file as well as stdout")
    args = p.parse_args()

    qids = read_qids_from_csv(args.csv)
    if not qids:
        sys.exit("No QIDs found in the CSV.")

    result = analyse(qids)
    text = format_text(result, top=args.top)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"\nSummary written to {args.output}")


if __name__ == "__main__":
    main()
