"""
Build a PDF practice booklet from sqe1.db.

Mirrors make_anki.py: same filters, same randomisation (questions in random
order, options shuffled per question so the correct letter moves). Each
question gets two pages — a question page, then an answer/explanation page.

Usage
-----
    python make_pdf.py                                   # all 580 questions
    python make_pdf.py --count 30                        # 30 random questions
    python make_pdf.py --flk FLK1 --count 90             # FLK1 mock
    python make_pdf.py --area BLP,DR --count 40
    python make_pdf.py --area CL --field "Formation"
    python make_pdf.py --subject "Value Added Tax"
    python make_pdf.py --output mock.pdf --seed 42
    python make_pdf.py --qids-file sqe1_qids.csv --output wrong.pdf

Install once:
    pip install reportlab
"""

from __future__ import annotations

import argparse
import csv
import html as htmllib
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "sqe1.db"

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:
    sys.exit("Install ReportLab first:  pip install reportlab")


# ─────────────────────────────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────────────────────────────

_base = getSampleStyleSheet()["Normal"]

STYLES = {
    "meta": ParagraphStyle(
        "meta", parent=_base,
        fontName="Helvetica", fontSize=8.5, textColor=colors.HexColor("#666"),
        leading=11, spaceAfter=2 * mm, alignment=TA_LEFT,
    ),
    "header": ParagraphStyle(
        "header", parent=_base,
        fontName="Helvetica-Bold", fontSize=13, textColor=colors.HexColor("#111"),
        leading=16, spaceAfter=4 * mm,
    ),
    "stem": ParagraphStyle(
        "stem", parent=_base,
        fontName="Helvetica", fontSize=10.5,
        leading=14, spaceAfter=2.5 * mm,
    ),
    "leadin": ParagraphStyle(
        "leadin", parent=_base,
        fontName="Helvetica-Bold", fontSize=10.5,
        leading=14, spaceBefore=3 * mm, spaceAfter=3 * mm,
    ),
    "option": ParagraphStyle(
        "option", parent=_base,
        fontName="Helvetica", fontSize=10.5,
        leading=13, leftIndent=10 * mm, firstLineIndent=-10 * mm,
        spaceAfter=2 * mm,
    ),
    "answer_banner": ParagraphStyle(
        "answer_banner", parent=_base,
        fontName="Helvetica-Bold", fontSize=11,
        leading=14, spaceAfter=4 * mm, textColor=colors.HexColor("#155724"),
        backColor=colors.HexColor("#d4edda"),
        borderPadding=(4, 8, 4, 8),
    ),
    "expl_label_correct": ParagraphStyle(
        "expl_label_correct", parent=_base,
        fontName="Helvetica-Bold", fontSize=10,
        leading=13, textColor=colors.HexColor("#1e7e34"),
        spaceBefore=3 * mm, spaceAfter=1 * mm,
    ),
    "expl_label": ParagraphStyle(
        "expl_label", parent=_base,
        fontName="Helvetica-Bold", fontSize=10, leading=13,
        spaceBefore=3 * mm, spaceAfter=1 * mm,
    ),
    "expl_body": ParagraphStyle(
        "expl_body", parent=_base,
        fontName="Helvetica", fontSize=9.5,
        leading=12.5, leftIndent=8 * mm, spaceAfter=1 * mm,
    ),
    "footer": ParagraphStyle(
        "footer", parent=_base,
        fontName="Helvetica-Oblique", fontSize=8, textColor=colors.HexColor("#888"),
        leading=10, spaceBefore=6 * mm,
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Text helpers (escape for ReportLab Paragraph mini-HTML)
# ─────────────────────────────────────────────────────────────────────

def esc(text: str) -> str:
    """Escape &, <, > for ReportLab's Paragraph; preserve single line breaks."""
    return htmllib.escape(text or "").replace("\n", "<br/>")


def text_paragraphs(text: str, style: ParagraphStyle) -> list[Paragraph]:
    """Split on blank lines, return one Paragraph per paragraph."""
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [Paragraph(esc(p), style) for p in parts]


# ─────────────────────────────────────────────────────────────────────
# Question selection (same as make_anki.py)
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
# Build the two pages for one question
# ─────────────────────────────────────────────────────────────────────

def render_question(
    conn: sqlite3.Connection, qid: int, qnum: int, total: int
) -> list:
    """Return a list of flowables for ONE question (question page + answer page)."""

    q = conn.execute(
        "SELECT flk, source_file, source_question_num, stem, lead_in "
        "  FROM questions WHERE question_id = ?",
        (qid,),
    ).fetchone()
    flk, source_file, src_num, stem, lead_in = q

    opts = conn.execute(
        "SELECT label, text, is_correct, explanation FROM options "
        " WHERE question_id = ? ORDER BY label",
        (qid,),
    ).fetchall()

    shuffled = list(opts)
    random.shuffle(shuffled)

    correct_letter = None
    correct_text = ""
    for new_letter, (_orig, text, is_correct, _expl) in zip("ABCDE", shuffled):
        if is_correct:
            correct_letter = new_letter
            correct_text = text

    # Taxonomy path + classification reasoning
    topic_rows = conn.execute(
        "SELECT t.code, t.name, t.level, qt.reasoning FROM question_topics qt "
        "  JOIN topics t ON t.topic_id = qt.topic_id "
        " WHERE qt.question_id = ? ORDER BY t.level",
        (qid,),
    ).fetchall()
    topic_str = " › ".join(t[0] or t[1] for t in topic_rows) if topic_rows else "(unclassified)"
    topic_reasonings = [r[3] for r in topic_rows if r[3]]

    meta_q = (
        f"FLK: {esc(flk)} &nbsp;·&nbsp; "
        f"Source: {esc(source_file)} Q{src_num} &nbsp;·&nbsp; QID: {qid}"
    )
    meta_a = (
        f"FLK: {esc(flk)} &nbsp;·&nbsp; "
        f"Source: {esc(source_file)} Q{src_num} &nbsp;·&nbsp; "
        f"Topic: {esc(topic_str)} &nbsp;·&nbsp; QID: {qid}"
    )

    story: list = []

    # ── Question page ─────────────────────────────────────────────
    story.append(Paragraph(f"Question {qnum} of {total}", STYLES["header"]))
    story.append(Paragraph(meta_q, STYLES["meta"]))
    story.append(Spacer(1, 3 * mm))

    story += text_paragraphs(stem, STYLES["stem"])
    story.append(Paragraph(esc(lead_in), STYLES["leadin"]))

    for new_letter, (_orig, text, _is_correct, _expl) in zip("ABCDE", shuffled):
        story.append(
            Paragraph(f"<b>{new_letter}.</b>&nbsp;&nbsp;{esc(text)}", STYLES["option"])
        )

    story.append(PageBreak())

    # ── Answer page ───────────────────────────────────────────────
    story.append(Paragraph(f"Answer — Question {qnum} of {total}", STYLES["header"]))
    story.append(Paragraph(meta_a, STYLES["meta"]))
    story.append(Spacer(1, 3 * mm))

    # Green banner: "Correct answer: X — text"
    banner = Table(
        [[Paragraph(
            f"Correct answer: <b>{correct_letter}</b> &nbsp;—&nbsp; {esc(correct_text)}",
            STYLES["answer_banner"],
        )]],
        colWidths=[16 * cm],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#d4edda")),
        ("LINEBEFORE", (0, 0), (-1, -1), 3, colors.HexColor("#28a745")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(banner)
    story.append(Spacer(1, 4 * mm))

    if topic_reasonings:
        for r in topic_reasonings:
            story.append(Paragraph(
                f"<b>Topic reasoning:</b> <i>{esc(r)}</i>",
                STYLES["expl_body"],
            ))
        story.append(Spacer(1, 3 * mm))

    for new_letter, (_orig, text, is_correct, explanation) in zip("ABCDE", shuffled):
        label_style = STYLES["expl_label_correct"] if is_correct else STYLES["expl_label"]
        suffix = "  ✓" if is_correct else ""
        story.append(
            Paragraph(
                f"{new_letter}.{suffix} <i>{esc(text)}</i>",
                label_style,
            )
        )
        if explanation:
            story += text_paragraphs(explanation, STYLES["expl_body"])
        else:
            story.append(Paragraph(
                "<i>[no explanation yet — run fill_explanations.py]</i>",
                STYLES["expl_body"],
            ))

    story.append(PageBreak())
    return story


# ─────────────────────────────────────────────────────────────────────
# Page header / footer
# ─────────────────────────────────────────────────────────────────────

def make_page_decorator(deck_title: str):
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.setFillColor(colors.HexColor("#888"))
        canvas.drawString(2 * cm, 1.3 * cm, deck_title)
        canvas.drawRightString(A4[0] - 2 * cm, 1.3 * cm, f"Page {doc.page}")
        canvas.restoreState()
    return on_page


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def default_title(args) -> str:
    parts = ["SQE1"]
    if args.qids_file:
        parts.append(f"Wrong answers ({args.qids_file.stem})")
    else:
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
        description="Build a PDF practice booklet from sqe1.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--count", type=int, help="Number of questions (default: all matching)")
    p.add_argument("--flk", choices=["FLK1", "FLK2"])
    p.add_argument("--area", help="Comma-separated area codes (e.g. BLP,DR)")
    p.add_argument("--field", help="Field name (taxonomy level 2)")
    p.add_argument("--subject", help="Subject matter (level 3)")
    p.add_argument("--submatter", help="Sub-matter (level 4)")
    p.add_argument("--qids-file", type=Path,
                   help="CSV file with a 'qid' column — only those questions are included")
    p.add_argument("--output", type=Path, default=Path("sqe1.pdf"),
                   help="Output PDF path (default: sqe1.pdf)")
    p.add_argument("--title", help="Booklet title (default: derived from filters)")
    p.add_argument("--seed", type=int, help="Random seed for reproducible booklets")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}. Run `python ingest.py init` first.")

    if args.seed is not None:
        random.seed(args.seed)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    # --qids-file takes priority: load QIDs directly from the CSV
    if args.qids_file:
        if not args.qids_file.exists():
            sys.exit(f"QIDs file not found: {args.qids_file}")
        with args.qids_file.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "qid" not in (reader.fieldnames or []):
                sys.exit(f"CSV must have a 'qid' column (found: {reader.fieldnames})")
            qids = [int(row["qid"]) for row in reader if row["qid"].strip()]
        # Always shuffle so file order (often QID order) doesn't leak through.
        # --seed controls the shuffle deterministically.
        random.shuffle(qids)
        if args.count:
            qids = qids[:args.count]
    else:
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

    title = args.title or default_title(args)

    doc = SimpleDocTemplate(
        str(args.output),
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=title,
    )

    story: list = []
    n_no_expl = 0
    for i, qid in enumerate(qids, 1):
        flowables = render_question(conn, qid, i, len(qids))
        # detect missing explanations by checking any UPDATE-able NULL in DB
        if any(
            e is None for (e,) in conn.execute(
                "SELECT explanation FROM options WHERE question_id=?", (qid,)
            )
        ):
            n_no_expl += 1
        story.extend(flowables)

    deco = make_page_decorator(title)
    doc.build(story, onFirstPage=deco, onLaterPages=deco)

    print(f"  ✓ wrote {args.output}  ({len(qids)} questions, ~{len(qids)*2} pages)")
    print(f"    title: {title!r}")
    if n_no_expl:
        print(f"    note: {n_no_expl} question(s) have at least one missing"
              " explanation — run fill_explanations.py to fill them")


if __name__ == "__main__":
    main()
