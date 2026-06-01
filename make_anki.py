"""
Build an Anki .apkg deck from sqe1.db for SQE1 practice.

Within each card, the five options are shuffled — the letter of the correct
answer is therefore randomised, so the position of the right answer can never
be memorised. Questions selected from the database are also shuffled.

Usage
-----
    python make_anki.py                                   # all 580 questions
    python make_anki.py --count 50                        # 50 random questions
    python make_anki.py --flk FLK1 --count 90             # only FLK1
    python make_anki.py --area BLP,DR --count 40          # only BLP + DR
    python make_anki.py --area CL --field "Formation"     # narrower
    python make_anki.py --subject "Value Added Tax"
    python make_anki.py --output my_deck.apkg --deck-name "SQE1 Practice"

Filters (any combination):
    --flk FLK1|FLK2                Restrict to one assessment
    --area  CODE[,CODE]            Comma-separated area codes (BLP, DR, PLP, …)
    --field NAME                   Field name (level 2 in the taxonomy)
    --subject NAME                 Subject matter (level 3)
    --submatter NAME               Sub-matter (level 4)

The area/field/subject/submatter filters traverse the taxonomy tree, so a
filter at any level also picks up everything beneath it. Questions with no
taxonomy link (e.g. the SRA docx questions before classification) are excluded
whenever any topic-based filter is set; they're included when only --flk (or no
filter) is used.

Install once:
    pip install genanki
"""

from __future__ import annotations

import argparse
import html as htmllib
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "sqe1.db"

try:
    import genanki
except ImportError:
    sys.exit("Install genanki first:  pip install genanki")


# Stable IDs so re-imports update existing cards instead of duplicating
MODEL_ID = 1872310194
DECK_ID_BASE = 1872310200

CSS = """
.card { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 16px; line-height: 1.55; color: #222; max-width: 720px;
        margin: 0 auto; padding: 16px; text-align: left; }
.stem p { margin: 0 0 10px; }
.lead-in { font-weight: 600; margin: 18px 0 12px; }
.options { padding-left: 24px; margin: 0; }
.options li { margin: 8px 0; }
hr#answer { margin: 24px 0; border: 0; border-top: 2px solid #ccc; }
.correct { background: #d4edda; padding: 10px 14px; border-left: 4px solid #28a745;
           margin: 0 0 16px; font-weight: 600; border-radius: 3px; }
.topic-reason { font-size: 13px; color: #555; margin: 0 0 16px;
                padding: 8px 12px; background: #f6f8fa; border-left: 3px solid #bbb;
                border-radius: 3px; }
.explanations { font-size: 14px; }
.explanations .opt { margin-top: 12px; }
.explanations .opt .letter { font-weight: 700; }
.explanations .opt.correct-opt .letter { color: #28a745; }
.explanations .opt-text { color: #555; font-style: italic; margin-left: 6px; }
.explanations .opt-expl { margin: 4px 0 0 24px; }
.no-expl { color: #aaa; font-style: italic; }
.meta { font-size: 11px; color: #888; margin-top: 22px; padding-top: 10px;
        border-top: 1px solid #eee; }
"""

MODEL = genanki.Model(
    MODEL_ID,
    "SQE1 SBA MCQ",
    fields=[
        {"name": "Question"},
        {"name": "Answer"},
        {"name": "Metadata"},
    ],
    templates=[{
        "name": "MCQ",
        "qfmt": "{{Question}}",
        "afmt": "{{FrontSide}}<hr id='answer'>{{Answer}}<div class='meta'>{{Metadata}}</div>",
    }],
    css=CSS,
)


# ─────────────────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────────────────

def inline(text: str) -> str:
    """Escape for HTML, preserve single line breaks as <br>."""
    return htmllib.escape(text or "").replace("\n", "<br>")


def block(text: str) -> str:
    """Escape for HTML, split paragraphs on blank lines."""
    if not text:
        return ""
    parts = [p.strip() for p in htmllib.escape(text).split("\n\n") if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in parts)


def slugify_tag(s: str) -> str:
    """Anki tags can't contain spaces."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_") or "tag"


# ─────────────────────────────────────────────────────────────────────
# Question selection
# ─────────────────────────────────────────────────────────────────────

def fetch_question_ids(
    conn: sqlite3.Connection,
    *,
    flk: str | None,
    area_codes: list[str] | None,
    field_name: str | None,
    subject_name: str | None,
    submatter_name: str | None,
    count: int | None,
) -> list[int]:
    # Build a CTE that expands a seed of matching topic_ids to all descendants
    seed_sql: str | None = None
    seed_params: list = []
    if area_codes:
        seed_sql = (
            "SELECT topic_id FROM topics WHERE code IN ("
            + ",".join("?" for _ in area_codes)
            + ")"
        )
        seed_params = list(area_codes)
    elif field_name:
        seed_sql = "SELECT topic_id FROM topics WHERE level = 2 AND name = ?"
        seed_params = [field_name]
    elif subject_name:
        seed_sql = "SELECT topic_id FROM topics WHERE level = 3 AND name = ?"
        seed_params = [subject_name]
    elif submatter_name:
        seed_sql = "SELECT topic_id FROM topics WHERE level = 4 AND name = ?"
        seed_params = [submatter_name]

    cte_sql = ""
    cte_params: list = []
    where = []
    where_params: list = []

    if flk:
        where.append("q.flk = ?")
        where_params.append(flk)

    if seed_sql is not None:
        cte_sql = f"""
        WITH RECURSIVE matched_topics(topic_id) AS (
            {seed_sql}
            UNION
            SELECT t.topic_id FROM topics t
            JOIN matched_topics m ON t.parent_id = m.topic_id
        )
        """
        cte_params = seed_params
        where.append(
            "EXISTS (SELECT 1 FROM question_topics qt "
            " WHERE qt.question_id = q.question_id "
            "   AND qt.topic_id IN matched_topics)"
        )

    where_sql = " AND ".join(where) if where else "1=1"
    sql = f"""
        {cte_sql}
        SELECT q.question_id FROM questions q
        WHERE {where_sql}
        ORDER BY q.question_id
    """
    # Selection + ordering done in Python so --seed controls them deterministically
    # (SQLite's RANDOM() can't be seeded from Python's random module).
    all_qids = [r[0] for r in conn.execute(sql, cte_params + where_params).fetchall()]
    random.shuffle(all_qids)
    if count is not None:
        all_qids = all_qids[:int(count)]
    return all_qids


# ─────────────────────────────────────────────────────────────────────
# Per-question card rendering (option order randomised here)
# ─────────────────────────────────────────────────────────────────────

def render_card(conn: sqlite3.Connection, qid: int) -> tuple[str, str, str, list[str]]:
    q = conn.execute(
        "SELECT flk, source_file, source_question_num, stem, lead_in "
        "  FROM questions WHERE question_id=?",
        (qid,),
    ).fetchone()
    flk, source_file, src_num, stem, lead_in = q

    opts = conn.execute(
        "SELECT label, text, is_correct, explanation FROM options "
        " WHERE question_id=? ORDER BY label",
        (qid,),
    ).fetchall()

    shuffled = list(opts)
    random.shuffle(shuffled)
    new_letters = "ABCDE"

    options_li = []
    answer_items = []
    correct_letter = None
    correct_text = ""

    for new_letter, (_orig_label, text, is_correct, explanation) in zip(new_letters, shuffled):
        if is_correct:
            correct_letter = new_letter
            correct_text = text

        options_li.append(f"<li>{inline(text)}</li>")

        cls = "opt correct-opt" if is_correct else "opt"
        if explanation:
            expl_html = block(explanation)
        else:
            expl_html = '<span class="no-expl">[no explanation yet — run fill_explanations.py]</span>'

        answer_items.append(
            f'<div class="{cls}">'
            f'<span class="letter">{new_letter}.</span>'
            f'<span class="opt-text">{inline(text)}</span>'
            f'<div class="opt-expl">{expl_html}</div>'
            f'</div>'
        )

    question_html = (
        f'<div class="stem">{block(stem)}</div>'
        f'<div class="lead-in">{inline(lead_in)}</div>'
        f'<ol class="options" type="A">{"".join(options_li)}</ol>'
    )

    # Metadata + tags
    topics = conn.execute(
        "SELECT t.code, t.name, t.level, qt.reasoning FROM question_topics qt "
        "  JOIN topics t ON t.topic_id = qt.topic_id "
        " WHERE qt.question_id = ? "
        " ORDER BY t.level",
        (qid,),
    ).fetchall()
    if topics:
        topic_str = " › ".join(t[0] or t[1] for t in topics)
    else:
        topic_str = "(unclassified)"

    reasoning_html = ""
    for _c, _n, _l, r in topics:
        if r:
            reasoning_html += f'<div class="topic-reason"><b>Topic reasoning:</b> <i>{inline(r)}</i></div>'

    answer_html = (
        f'<div class="correct">Correct answer: '
        f'<strong>{correct_letter}</strong> — {inline(correct_text)}</div>'
        f'{reasoning_html}'
        f'<div class="explanations">{"".join(answer_items)}</div>'
    )

    metadata_html = (
        f"FLK: {flk} &middot; "
        f"Source: {htmllib.escape(source_file)} Q{src_num} &middot; "
        f"Topic: {htmllib.escape(topic_str)} &middot; "
        f"QID: {qid}"
    )

    tags = [flk, f"QID_{qid}"]
    for code, name, _level, _r in topics:
        tags.append(slugify_tag(code or name))

    return question_html, answer_html, metadata_html, tags


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def default_deck_name(args) -> str:
    parts = ["SQE1"]
    if args.flk:
        parts.append(args.flk)
    if args.area:
        parts.append(args.area)
    if args.field:
        parts.append(args.field)
    if args.subject:
        parts.append(args.subject)
    if args.submatter:
        parts.append(args.submatter)
    if len(parts) == 1:
        parts.append("All")
    return " — ".join(parts)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build an Anki .apkg deck from sqe1.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--count", type=int, help="Number of cards (default: all matching)")
    p.add_argument("--flk", choices=["FLK1", "FLK2"], help="Restrict to one assessment")
    p.add_argument("--area", help="Comma-separated area codes (e.g. BLP,DR,CL)")
    p.add_argument("--field", help="Field name (taxonomy level 2)")
    p.add_argument("--subject", help="Subject matter (taxonomy level 3)")
    p.add_argument("--submatter", help="Sub-matter (taxonomy level 4)")
    p.add_argument("--output", type=Path, default=Path("sqe1.apkg"),
                   help="Output .apkg path (default: sqe1.apkg)")
    p.add_argument("--deck-name", help="Anki deck name (default: derived from filters)")
    p.add_argument("--seed", type=int, help="Random seed for reproducible decks")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}. Run `python ingest.py init` first.")

    if args.seed is not None:
        random.seed(args.seed)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    area_codes = [s.strip() for s in args.area.split(",")] if args.area else None
    qids = fetch_question_ids(
        conn,
        flk=args.flk,
        area_codes=area_codes,
        field_name=args.field,
        subject_name=args.subject,
        submatter_name=args.submatter,
        count=args.count,
    )

    if not qids:
        sys.exit("No questions match the supplied filters.")

    deck_name = args.deck_name or default_deck_name(args)
    deck_id = DECK_ID_BASE + abs(hash(deck_name)) % 1_000_000
    deck = genanki.Deck(deck_id, deck_name)

    n_no_expl = 0
    for qid in qids:
        question_html, answer_html, metadata_html, tags = render_card(conn, qid)
        if "[no explanation yet" in answer_html:
            n_no_expl += 1
        note = genanki.Note(
            model=MODEL,
            fields=[question_html, answer_html, metadata_html],
            tags=tags,
        )
        deck.add_note(note)

    genanki.Package(deck).write_to_file(str(args.output))

    print(f"  ✓ wrote {args.output}  ({len(qids)} cards)")
    print(f"    deck: {deck_name!r}")
    if n_no_expl:
        print(f"    note: {n_no_expl} card(s) have at least one missing explanation"
              " — run fill_explanations.py to fill them")


if __name__ == "__main__":
    main()
