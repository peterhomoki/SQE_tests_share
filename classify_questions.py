"""
Classify questions in sqe1.db by assigning the most specific matching topic_id
from the FLK taxonomy, using Claude Opus 4.7.

For each pending question the full taxonomy for the question's FLK is sent
alongside the question content (stem, lead-in, all five options, correct answer).
Claude returns the single most specific topic_id that best matches, plus brief
reasoning. The result is written to question_topics.

By default only questions with NO topic link are processed.
Use --reclassify to also re-process questions that have only an area-level
(level 1) link — the area link is replaced by the more specific result.

Usage
-----
    python classify_questions.py                    # all unlinked questions
    python classify_questions.py --reclassify       # also reclassify area-only links
    python classify_questions.py --limit 10
    python classify_questions.py --question-id 42
    python classify_questions.py --dry-run
    python classify_questions.py --sleep 0.5
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "sqe1.db"
ENV_PATH = ROOT / ".env"

MODEL = "claude-opus-4-7"

PRICE_IN_PER_1M = 5.00
PRICE_OUT_PER_1M = 25.00


# ─────────────────────────────────────────────────────────────────────
# .env loader
# ─────────────────────────────────────────────────────────────────────

def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

try:
    import anthropic
except ImportError:
    sys.exit("Install the SDK first:  pip install anthropic")

if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit(
        "ANTHROPIC_API_KEY not found. Put it in .env next to this script:\n"
        "    ANTHROPIC_API_KEY=sk-ant-..."
    )


# ─────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are classifying SQE1 (Solicitors Qualifying Examination Part 1) multiple-choice questions against the SRA's published taxonomy for the law of England and Wales.

You will be given:
1. A taxonomy listing topic IDs with their names, levels (area / field / subject / sub-matter), and hierarchy
2. A question (stem, lead-in, all five options, and the correct answer)

Your task: return the single most specific topic_id from the taxonomy that best describes the primary legal knowledge being tested by the question.

Rules:
- Choose the deepest (most specific) level you can justify confidently. If the question clearly tests a named sub-matter (level 4), return that. If only the subject-matter (level 3) fits, return that. Never guess at a deeper level than the question content supports.
- Return exactly ONE topic_id — the single best match.
- If the question spans two areas, pick the dominant one (the area whose rules resolve the question).
- Never return a topic_id that is not present in the provided taxonomy list.
- Provide brief reasoning (1–2 sentences) citing the specific rule, section, or principle that locates the question in that topic."""


def build_taxonomy_text(conn: sqlite3.Connection, flk: str) -> str:
    """Flat, indented taxonomy listing suitable for an LLM prompt."""
    rows = conn.execute(
        "SELECT topic_id, parent_id, level, code, name "
        "FROM topics WHERE flk = ? ORDER BY topic_id",
        (flk,),
    ).fetchall()

    # parent → children
    children: dict[int | None, list] = {}
    for row in rows:
        children.setdefault(row[1], []).append(row)

    level_labels = {1: "area", 2: "field", 3: "subject", 4: "sub-matter"}
    lines = [f"TAXONOMY ({flk}):", ""]

    def walk(parent_id: int | None) -> None:
        for tid, pid, level, code, name in children.get(parent_id, []):
            indent = "  " * (level - 1)
            display = f"{code} — {name}" if code else name
            lines.append(f"{indent}[{tid}] ({level_labels[level]}) {display}")
            walk(tid)

    walk(None)
    return "\n".join(lines)


def build_schema() -> dict:
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "topic_id": {
                    "type": "integer",
                    "description": "topic_id of the most specific matching topic from the taxonomy.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "1–2 sentences explaining the choice.",
                },
            },
            "required": ["topic_id", "reasoning"],
            "additionalProperties": False,
        },
    }


def build_user_prompt(taxonomy_text: str, q: dict) -> str:
    lines = [
        taxonomy_text,
        "",
        "---",
        "",
        "## Question",
        "",
        q["stem"],
        "",
        f"**Lead-in:** {q['lead_in']}",
        "",
        "## Options",
        "",
    ]
    for label in "ABCDE":
        marker = "  ← CORRECT" if label == q["correct_label"] else ""
        lines.append(f"**{label}.** {q['options'][label]}{marker}")
    lines += [
        "",
        f"The correct answer is option **{q['correct_label']}**.",
        "",
        "Return the topic_id and reasoning.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────

def pending_question_ids(
    conn: sqlite3.Connection,
    *,
    reclassify: bool,
    limit: int | None,
    question_id: int | None,
) -> list[int]:
    if question_id is not None:
        return [question_id]

    if reclassify:
        # No topic link at all, OR only area-level (level 1) links
        sql = """
            SELECT q.question_id FROM questions q
            WHERE NOT EXISTS (
                SELECT 1 FROM question_topics qt
                JOIN topics t ON t.topic_id = qt.topic_id
                WHERE qt.question_id = q.question_id AND t.level > 1
            )
            ORDER BY q.question_id
        """
    else:
        # No topic link at all
        sql = """
            SELECT q.question_id FROM questions q
            WHERE NOT EXISTS (
                SELECT 1 FROM question_topics qt
                WHERE qt.question_id = q.question_id
            )
            ORDER BY q.question_id
        """

    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(sql).fetchall()]


def fetch_question(conn: sqlite3.Connection, qid: int) -> dict | None:
    q = conn.execute(
        "SELECT flk, stem, lead_in FROM questions WHERE question_id = ?", (qid,)
    ).fetchone()
    if not q:
        return None
    opts = conn.execute(
        "SELECT label, text, is_correct FROM options "
        "WHERE question_id = ? ORDER BY label",
        (qid,),
    ).fetchall()
    return {
        "flk": q[0],
        "stem": q[1],
        "lead_in": q[2],
        "options": {r[0]: r[1] for r in opts},
        "correct_label": next(r[0] for r in opts if r[2]),
    }


def topic_name(conn: sqlite3.Connection, topic_id: int) -> str:
    row = conn.execute(
        "SELECT code, name, level FROM topics WHERE topic_id = ?", (topic_id,)
    ).fetchone()
    if not row:
        return str(topic_id)
    code, name, level = row
    level_labels = {1: "area", 2: "field", 3: "subject", 4: "sub-matter"}
    label = f"{code} — {name}" if code else name
    return f"{label} ({level_labels.get(level, level)})"


# ─────────────────────────────────────────────────────────────────────
# Per-question processing
# ─────────────────────────────────────────────────────────────────────

def process_question(
    client: "anthropic.Anthropic",
    conn: sqlite3.Connection,
    qid: int,
    taxonomy_cache: dict[str, str],
    reclassify: bool,
) -> tuple[bool, object | None, str | None]:
    q = fetch_question(conn, qid)
    if q is None:
        return False, None, "no such question"

    flk = q["flk"]
    if flk not in taxonomy_cache:
        taxonomy_cache[flk] = build_taxonomy_text(conn, flk)

    user_prompt = build_user_prompt(taxonomy_cache[flk], q)

    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": build_schema()},
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        return False, response.usage, "empty response"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return False, response.usage, f"JSON parse failed: {e}"

    tid = parsed.get("topic_id")
    reasoning = parsed.get("reasoning", "")

    if not isinstance(tid, int):
        return False, response.usage, f"invalid topic_id: {tid!r}"

    valid = conn.execute(
        "SELECT 1 FROM topics WHERE topic_id = ? AND flk = ?", (tid, flk)
    ).fetchone()
    if not valid:
        return False, response.usage, f"topic_id {tid} not found in {flk} taxonomy"

    # In reclassify mode, remove area-level-only links before inserting the
    # specific one (only when the question has no deeper links yet).
    if reclassify:
        has_specific = conn.execute(
            "SELECT 1 FROM question_topics qt "
            "JOIN topics t ON t.topic_id = qt.topic_id "
            "WHERE qt.question_id = ? AND t.level > 1",
            (qid,),
        ).fetchone()
        if not has_specific:
            conn.execute(
                "DELETE FROM question_topics WHERE question_id = ? "
                "AND topic_id IN (SELECT topic_id FROM topics WHERE level = 1)",
                (qid,),
            )

    reasoning_clean = (reasoning or "").strip()[:1000]
    conn.execute(
        "INSERT INTO question_topics(question_id, topic_id, flk, reasoning) VALUES (?,?,?,?) "
        "ON CONFLICT(question_id, topic_id) DO UPDATE SET reasoning = excluded.reasoning",
        (qid, tid, flk, reasoning_clean or None),
    )
    conn.commit()

    name = topic_name(conn, tid)
    return True, response.usage, f"[{tid}] {name} — {reasoning}"


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Assign taxonomy topic_ids to questions in sqe1.db via Claude Opus 4.7.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--reclassify", action="store_true",
                   help="Also reclassify questions that only have an area-level (level 1) link")
    p.add_argument("--limit", type=int, help="Process at most N questions")
    p.add_argument("--question-id", type=int, help="Process only this question_id")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="Seconds to sleep between requests (default 0)")
    p.add_argument("--dry-run", action="store_true",
                   help="List pending question_ids and exit without calling the API")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}. Run `python ingest.py init` first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    qids = pending_question_ids(
        conn,
        reclassify=args.reclassify,
        limit=args.limit,
        question_id=args.question_id,
    )

    if not qids:
        print("Nothing to do — all questions are already classified.")
        return

    if args.dry_run:
        mode = "reclassify (area-only included)" if args.reclassify else "unlinked only"
        print(f"{len(qids)} question(s) pending ({mode}):")
        for qid in qids:
            print(f"  Q{qid}")
        return

    client = anthropic.Anthropic()
    mode = "reclassify (area-only included)" if args.reclassify else "unlinked only"
    print(f"Model: {MODEL}")
    print(f"Pending: {len(qids)} question(s)  ({mode})")

    taxonomy_cache: dict[str, str] = {}
    ok = skipped = fail = 0
    total_in = total_out = 0
    started = time.monotonic()

    for i, qid in enumerate(qids, 1):
        try:
            success, usage, info = process_question(
                client, conn, qid, taxonomy_cache, args.reclassify
            )
        except anthropic.BadRequestError as e:
            fail += 1
            print(f"  [{i:>4}/{len(qids)}] ✗ Q{qid}: bad request — {e.message}")
            continue
        except anthropic.AuthenticationError:
            sys.exit("\nAuthentication failed — check ANTHROPIC_API_KEY in .env")
        except anthropic.RateLimitError as e:
            wait = int(e.response.headers.get("retry-after", "30"))
            print(f"  [{i:>4}/{len(qids)}] ⏸ rate limited — sleeping {wait}s")
            time.sleep(wait)
            fail += 1
            continue
        except anthropic.APIStatusError as e:
            fail += 1
            print(f"  [{i:>4}/{len(qids)}] ✗ Q{qid}: API {e.status_code} — {e.message}")
            continue
        except anthropic.APIConnectionError as e:
            fail += 1
            print(f"  [{i:>4}/{len(qids)}] ✗ Q{qid}: connection — {e}")
            continue

        if usage is not None:
            total_in += usage.input_tokens
            total_out += usage.output_tokens

        if success:
            ok += 1
            print(
                f"  [{i:>4}/{len(qids)}] ✓ Q{qid}"
                f"  in={usage.input_tokens:>5}  out={usage.output_tokens:>4}"
                f"  {info}"
            )
        else:
            skipped += 1
            print(f"  [{i:>4}/{len(qids)}] · Q{qid}: {info}")

        if args.sleep:
            time.sleep(args.sleep)

    elapsed = time.monotonic() - started
    cost = total_in / 1_000_000 * PRICE_IN_PER_1M + total_out / 1_000_000 * PRICE_OUT_PER_1M

    print()
    print(f"Done in {elapsed:0.1f}s.  ok={ok}  skipped={skipped}  fail={fail}")
    print(f"Tokens: input={total_in:,}  output={total_out:,}")
    print(f"≈ cost: ${cost:.2f}  (Opus 4.7 list — ${PRICE_IN_PER_1M}/${PRICE_OUT_PER_1M} per 1M)")


if __name__ == "__main__":
    main()
