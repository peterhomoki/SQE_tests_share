"""
Ingest SQE1 sample-question docx files + the FLK1/FLK2 taxonomy into SQLite.

Usage
-----
    python ingest.py init                          # create DB, load taxonomy + questions
    python ingest.py dump-explanations FILE.md     # write a template for Claude to fill
    python ingest.py load-explanations FILE.md     # write explanations from template into DB

Explanations template format (one block per question):

    ## QID 17

    - A: <why incorrect / correct>
    - B: <why incorrect / correct>
    - C: <why incorrect / correct>
    - D: <why incorrect / correct>
    - E: <why incorrect / correct>

Anything outside `## QID N` blocks is ignored, so you can add prose freely.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from docx import Document

ROOT = Path(__file__).parent
DB_PATH = ROOT / "sqe1.db"
SCHEMA_PATH = ROOT / "schema.sql"

SPEC_FILES = {
    "FLK1": ROOT / "flk1_specification.md",
    "FLK2": ROOT / "flk2_specification.md",
}

# Source-question files are auto-discovered from ./sources_of_questions/.
# A file's FLK is inferred from its filename: anything matching the regex
# below as a whole word ("flk1" / "FLK 1" / "flk-1" etc.) maps to FLK1,
# the same for FLK2. Files with no FLK in the name are skipped with a
# warning. Both .docx and .txt files are supported (see the .txt format
# description in parse_questions_txt below).
SOURCE_DIR  = ROOT / "sources_of_questions"
FLK_RE      = re.compile(r"flk[\s_\-]?([12])", re.IGNORECASE)


def discover_source_files() -> list[tuple[Path, str]]:
    """Return [(path, flk), …] for every .docx / .txt under SOURCE_DIR
    whose filename contains an FLK1 / FLK2 marker."""
    if not SOURCE_DIR.exists():
        return []
    found = []
    for p in sorted(SOURCE_DIR.iterdir()):
        if p.suffix.lower() not in (".docx", ".txt"):
            continue
        m = FLK_RE.search(p.name)
        if not m:
            print(f"  ! source file without FLK marker, skipped: {p.name}",
                  file=sys.stderr)
            continue
        found.append((p, f"FLK{m.group(1)}"))
    return found


# ─────────────────────────────────────────────────────────────────────
# DB bootstrap
# ─────────────────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Taxonomy ingest (markdown spec → topics table)
# ─────────────────────────────────────────────────────────────────────

H1 = re.compile(r"^#\s+(\S.*)$")
H2 = re.compile(r"^##\s+(\S.*)$")
H3 = re.compile(r"^###\s+(\S.*)$")
H4 = re.compile(r"^####\s+(\S.*)$")


def load_taxonomy(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for flk, path in SPEC_FILES.items():
        if not path.exists():
            print(f"  ! spec missing: {path.name}", file=sys.stderr)
            continue
        # parent_ids[level] = topic_id of current open node at that level
        parent_ids: dict[int, int | None] = {1: None, 2: None, 3: None}

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip()
            for level, pat in ((4, H4), (3, H3), (2, H2), (1, H1)):
                m = pat.match(line)
                if not m:
                    continue
                name = m.group(1).strip()
                code = name if level == 1 else None
                parent = None if level == 1 else parent_ids[level - 1]
                cur.execute(
                    "INSERT INTO topics(parent_id, level, code, name, flk) "
                    "VALUES (?,?,?,?,?)",
                    (parent, level, code, name, flk),
                )
                tid = cur.lastrowid
                if level < 4:
                    parent_ids[level] = tid
                    # invalidate any deeper open node
                    for deeper in range(level + 1, 4):
                        parent_ids[deeper] = None
                break  # matched one heading level — move to next line
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Docx parsing
# ─────────────────────────────────────────────────────────────────────

OPTION_RE   = re.compile(r"^([A-E])\.\s*(.+)$", re.DOTALL)
QUESTION_RE = re.compile(r"^Question\s+(\d+)\b", re.IGNORECASE)
# Decorative separators that appear between SRA questions
SEP_HINTS = ("•", "«", "»", "*«", "Solicitors Regulation Authority")


def is_separator(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    return any(h in t for h in SEP_HINTS) and not OPTION_RE.match(t)


def parse_answer_tables(doc: Document) -> dict[int, str]:
    """Return {question_num: letter} from answer-key tables."""
    answers: dict[int, str] = {}
    for table in doc.tables:
        ncols = len(table.columns)
        if ncols not in (2, 6):
            continue
        for row in table.rows[1:]:  # skip header
            cells = [c.text.strip() for c in row.cells]
            groups = [cells] if ncols == 2 else [cells[:3], cells[3:]]
            for g in groups:
                if not g or not g[0]:
                    continue
                try:
                    qnum = int(g[0])
                except ValueError:
                    continue
                letter = (g[1] if len(g) > 1 else "").strip()
                if letter not in ("A", "B", "C", "D", "E"):
                    continue
                answers[qnum] = letter
    return answers


def _strip_label(label: str, line: str) -> str:
    """Strip a leading "A.", "A\t", or "A " label from a positionally-known option line."""
    for pat in (rf"^{label}\.\s*", rf"^{label}\t\s*", rf"^{label}\s+"):
        m = re.match(pat, line)
        if m:
            return line[m.end():].strip()
    return line.strip()


def parse_questions_docx(path: Path) -> list[dict]:
    doc = Document(str(path))
    paras = [p.text for p in doc.paragraphs]
    answers = parse_answer_tables(doc)

    out: list[dict] = []
    i, n = 0, len(paras)
    while i < n:
        m = QUESTION_RE.match(paras[i].strip())
        if not m:
            i += 1
            continue
        qnum = int(m.group(1))
        i += 1
        content: list[str] = []
        while i < n and not QUESTION_RE.match(paras[i].strip()):
            t = paras[i].strip()
            if t and not is_separator(t):
                content.append(t)
            i += 1

        # Strict path: 5 lines that all match "X.<text>" — common case.
        strict = {
            m2.group(1): m2.group(2).strip()
            for line in content
            if (m2 := OPTION_RE.match(line))
        }
        if len(strict) == 5:
            first_opt = next(j for j, ln in enumerate(content) if OPTION_RE.match(ln))
            options = strict
            lead_in = content[first_opt - 1]
            stem = "\n\n".join(content[: first_opt - 1])
        else:
            # Positional fallback for malformed labels (eg. SRA FLK2 Q57/Q87):
            # lead-in is the last "?"-ending paragraph; next 5 lines are A–E.
            lead_idx = next(
                (
                    j
                    for j in range(len(content) - 1, -1, -1)
                    if content[j].rstrip().endswith("?")
                ),
                None,
            )
            if lead_idx is None or lead_idx + 5 >= len(content):
                print(
                    f"  ! skipped malformed Q{qnum} in {path.name}",
                    file=sys.stderr,
                )
                continue
            lead_in = content[lead_idx]
            stem = "\n\n".join(content[:lead_idx])
            opt_lines = content[lead_idx + 1 : lead_idx + 6]
            options = {
                label: _strip_label(label, ln)
                for label, ln in zip("ABCDE", opt_lines)
            }

        letter = answers.get(qnum)
        if letter is None:
            print(f"  ! no answer key entry for Q{qnum} in {path.name}", file=sys.stderr)
            continue

        out.append(
            {
                "qnum": qnum,
                "stem": stem,
                "lead_in": lead_in,
                "options": options,
                "correct": letter,
            }
        )
    return out


def parse_questions_txt(path: Path) -> list[dict]:
    """Parse a plain-text source file.

    Expected format (UTF-8, blank lines separate blocks):

        Question 1
        <one or more lines of stem>
        <lead-in line, typically ending with "?">
        A. <option A text>
        B. <option B text>
        C. <option C text>
        D. <option D text>
        E. <option E text>
        Answer: B

        Question 2
        ...

    "Answer:" is required. Anything between a "Question N" line and the
    option block is treated as stem + lead-in, with the lead-in being
    the line immediately before the first option line.
    """
    text  = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip() for ln in text.splitlines()]

    out: list[dict] = []
    i, n = 0, len(lines)
    answer_re = re.compile(r"^Answer\s*[:\-]\s*([A-E])\b", re.IGNORECASE)

    while i < n:
        m = QUESTION_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        qnum = int(m.group(1))
        i += 1
        content: list[str] = []
        correct: str | None = None
        while i < n and not QUESTION_RE.match(lines[i].strip()):
            t = lines[i].strip()
            if not t:
                i += 1
                continue
            ma = answer_re.match(t)
            if ma:
                correct = ma.group(1).upper()
                i += 1
                continue
            content.append(t)
            i += 1

        opts = {m2.group(1): m2.group(2).strip()
                for ln in content if (m2 := OPTION_RE.match(ln))}
        if len(opts) != 5 or correct is None:
            print(f"  ! skipped Q{qnum} in {path.name} "
                  f"(options={len(opts)}, answer={correct})", file=sys.stderr)
            continue

        first_opt = next(j for j, ln in enumerate(content) if OPTION_RE.match(ln))
        if first_opt == 0:
            print(f"  ! Q{qnum} in {path.name} has no stem/lead-in",
                  file=sys.stderr)
            continue
        lead_in = content[first_opt - 1]
        stem    = "\n\n".join(content[: first_opt - 1])

        out.append({"qnum": qnum, "stem": stem, "lead_in": lead_in,
                    "options": opts, "correct": correct})
    return out


def parse_source_file(path: Path) -> list[dict]:
    """Dispatch on file extension."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_questions_docx(path)
    if suffix == ".txt":
        return parse_questions_txt(path)
    raise ValueError(f"Unsupported source extension: {suffix}")


def load_questions(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    sources = discover_source_files()
    if not sources:
        print(f"  ! no source files found in {SOURCE_DIR} "
              "(supported: .docx, .txt with FLK1/FLK2 in filename)",
              file=sys.stderr)
        return
    for path, flk in sources:
        try:
            questions = parse_source_file(path)
        except Exception as e:
            print(f"  ! failed to parse {path.name}: {e}", file=sys.stderr)
            continue
        for q in questions:
            cur.execute(
                "INSERT INTO questions"
                " (flk, source_file, source_question_num, stem, lead_in)"
                " VALUES (?,?,?,?,?)",
                (
                    flk,
                    path.name,
                    q["qnum"],
                    q["stem"],
                    q["lead_in"],
                ),
            )
            qid = cur.lastrowid
            for label in ("A", "B", "C", "D", "E"):
                cur.execute(
                    "INSERT INTO options(question_id, label, text, is_correct)"
                    " VALUES (?,?,?,?)",
                    (qid, label, q["options"][label], int(label == q["correct"])),
                )
        print(f"  • {path.name}: {len(questions)} questions ingested")
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Explanations template — dump / load
# ─────────────────────────────────────────────────────────────────────

EXPL_HEADER_RE = re.compile(r"^##\s+QID\s+(\d+)\b", re.IGNORECASE)
EXPL_BULLET_RE = re.compile(r"^[-*]\s*\*?\*?([A-E])\*?\*?\s*:\s*(.*)$")


def dump_explanations(conn: sqlite3.Connection, out_path: Path) -> None:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT q.question_id, q.flk, q.source_file, q.source_question_num,"
        "       q.stem, q.lead_in"
        "  FROM questions q ORDER BY q.question_id"
    ).fetchall()

    lines: list[str] = [
        "# SQE1 question explanations",
        "",
        "Fill in each `- A:` / `- B:` … bullet with a short explanation:",
        "if the option is incorrect, say *why* it is wrong;",
        "if it is the correct answer, say *why* it is best.",
        "",
        "---",
        "",
    ]
    for qid, flk, src, qnum, stem, lead in rows:
        opts = cur.execute(
            "SELECT label, text, is_correct FROM options"
            "  WHERE question_id = ? ORDER BY label",
            (qid,),
        ).fetchall()
        correct = next((lbl for lbl, _, c in opts if c), "?")
        lines.append(f"## QID {qid}  ({flk} · {src} Q{qnum})")
        lines.append("")
        lines.append(f"**Stem:** {stem}")
        lines.append("")
        lines.append(f"**Lead-in:** {lead}")
        lines.append("")
        for lbl, text, _ in opts:
            star = " ✓" if lbl == correct else ""
            lines.append(f"- **{lbl}.**{star} {text}")
        lines.append("")
        lines.append(f"**Correct: {correct}**")
        lines.append("")
        for lbl, _, _ in opts:
            lines.append(f"- {lbl}: ")
        lines.append("")
        lines.append("---")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  • wrote {len(rows)} blocks to {out_path}")


def load_explanations(conn: sqlite3.Connection, in_path: Path) -> None:
    cur = conn.cursor()
    text = in_path.read_text(encoding="utf-8")

    current_qid: int | None = None
    parsed: dict[int, dict[str, str]] = {}

    for raw in text.splitlines():
        m = EXPL_HEADER_RE.match(raw.strip())
        if m:
            current_qid = int(m.group(1))
            parsed.setdefault(current_qid, {})
            continue
        if current_qid is None:
            continue
        # Only bullets that LOOK like explanation bullets, and have a non-empty body,
        # are taken as explanations. This skips the option-listing bullets which
        # carry the option *text* rather than an explanation.
        m2 = EXPL_BULLET_RE.match(raw.strip())
        if not m2:
            continue
        label, body = m2.group(1), m2.group(2).strip()
        if not body:
            continue
        # The template dumps option text as "- **A.** text" (note the period and
        # bold), which the regex above also matches. To distinguish, we require
        # the bullet *not* to use the bolded "**A.**" form.
        if raw.strip().startswith(f"- **{label}.**") or raw.strip().startswith(f"* **{label}.**"):
            continue
        parsed[current_qid][label] = body

    updated = 0
    for qid, expls in parsed.items():
        for label, expl in expls.items():
            cur.execute(
                "UPDATE options SET explanation = ?"
                " WHERE question_id = ? AND label = ?",
                (expl, qid, label),
            )
            updated += cur.rowcount
    conn.commit()
    print(f"  • updated {updated} explanations from {in_path}")


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def cmd_init() -> None:
    if DB_PATH.exists():
        print(f"  • removing existing {DB_PATH.name}")
        DB_PATH.unlink()
    conn = open_db()
    print("  • creating schema")
    create_schema(conn)
    print("  • loading taxonomy")
    load_taxonomy(conn)
    n_topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    print(f"    → {n_topics} topics")
    print("  • loading questions")
    load_questions(conn)
    n_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    n_o = conn.execute("SELECT COUNT(*) FROM options").fetchone()[0]
    print(f"  ✓ done — {n_q} questions, {n_o} options")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create DB, load taxonomy + questions")
    d = sub.add_parser("dump-explanations", help="write a fill-in template")
    d.add_argument("path", type=Path)
    l = sub.add_parser("load-explanations", help="load filled template into DB")
    l.add_argument("path", type=Path)
    args = p.parse_args()

    if args.cmd == "init":
        cmd_init()
    elif args.cmd == "dump-explanations":
        conn = open_db()
        dump_explanations(conn, args.path)
        conn.close()
    elif args.cmd == "load-explanations":
        conn = open_db()
        load_explanations(conn, args.path)
        conn.close()


if __name__ == "__main__":
    main()
