"""
Generate a JSON test input file from sqe1.db for LLM benchmarking.

Usage:
  python generate_test_input.py --mode all --output test_all.json
  python generate_test_input.py --mode random --n 100 --seed 42 --output test_random.json
  python generate_test_input.py --mode topics --topic-ids 1,48,132 --output test_topics.json
  python generate_test_input.py --mode all --flk FLK1 --output test_flk1.json
"""
import argparse, json, random, sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "sqe1.db"


def get_topic_chain(conn, topic_id):
    chain = {}
    tid = topic_id
    while tid:
        r = conn.execute(
            "SELECT topic_id, name, level, parent_id FROM topics WHERE topic_id=?", (tid,)
        ).fetchone()
        if not r:
            break
        chain[r[2]] = r[1]
        tid = r[3]
    return {
        "topic_id": topic_id,
        "lvl1": chain.get(1),
        "lvl2": chain.get(2),
        "lvl3": chain.get(3),
        "lvl4": chain.get(4),
    }


def get_subtree_ids(conn, topic_id):
    rows = conn.execute("""
        WITH RECURSIVE sub(tid) AS (
            SELECT ?
            UNION ALL
            SELECT t.topic_id FROM topics t JOIN sub s ON t.parent_id = s.tid
        )
        SELECT tid FROM sub
    """, (topic_id,)).fetchall()
    return {r[0] for r in rows}


def fetch_questions(conn, qids):
    placeholders = ",".join("?" * len(qids))
    q_rows = conn.execute(
        f"SELECT question_id, flk, source_file, source_question_num, stem, lead_in "
        f"FROM questions WHERE question_id IN ({placeholders}) ORDER BY question_id",
        qids,
    ).fetchall()

    questions = []
    for qid, flk, source_file, src_num, stem, lead_in in q_rows:
        opts = conn.execute(
            "SELECT label, text, is_correct FROM options "
            "WHERE question_id=? ORDER BY label",
            (qid,),
        ).fetchall()
        correct = next((o[0] for o in opts if o[2]), None)

        t = conn.execute(
            "SELECT topic_id FROM question_topics WHERE question_id=?", (qid,)
        ).fetchone()
        topic = get_topic_chain(conn, t[0]) if t else {}

        questions.append({
            "qid": qid,
            "flk": flk,
            "source_file": source_file,
            "source_question_num": src_num,
            "stem": stem,
            "lead_in": lead_in,
            "options": [
                {"label": o[0], "text": o[1], "is_correct": bool(o[2])}
                for o in opts
            ],
            "correct_answer": correct,
            "topic": topic,
        })
    return questions


def main():
    parser = argparse.ArgumentParser(description="Generate LLM test input from sqe1.db")
    parser.add_argument("--mode", choices=["all", "random", "topics"], required=True)
    parser.add_argument("--n", type=int, help="Number of questions (random mode)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default 42)")
    parser.add_argument(
        "--topic-ids",
        help="Comma-separated topic IDs (topics mode); entire subtrees are included",
    )
    parser.add_argument("--flk", choices=["FLK1", "FLK2"], help="Restrict to one FLK")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    conn = sqlite3.connect(DB)

    if args.mode == "all":
        qids = [r[0] for r in conn.execute("SELECT question_id FROM questions").fetchall()]

    elif args.mode == "random":
        if not args.n:
            parser.error("--n is required for random mode")
        all_ids = [r[0] for r in conn.execute("SELECT question_id FROM questions").fetchall()]
        random.seed(args.seed)
        qids = sorted(random.sample(all_ids, min(args.n, len(all_ids))))

    elif args.mode == "topics":
        if not args.topic_ids:
            parser.error("--topic-ids is required for topics mode")
        root_ids = [int(x.strip()) for x in args.topic_ids.split(",")]
        subtree = set()
        for root_id in root_ids:
            subtree |= get_subtree_ids(conn, root_id)
        rows = conn.execute(
            f"SELECT DISTINCT qt.question_id FROM question_topics qt "
            f"WHERE qt.topic_id IN ({','.join('?'*len(subtree))})",
            list(subtree),
        ).fetchall()
        qids = sorted(r[0] for r in rows)

    if args.flk:
        valid = {
            r[0]
            for r in conn.execute(
                "SELECT question_id FROM questions WHERE flk=?", (args.flk,)
            ).fetchall()
        }
        qids = [q for q in qids if q in valid]

    questions = fetch_questions(conn, qids)
    conn.close()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "seed": args.seed if args.mode == "random" else None,
        "flk_filter": args.flk,
        "total": len(questions),
        "questions": questions,
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(questions)} questions → {out_path}")


if __name__ == "__main__":
    main()
