"""
Fill in missing `options.explanation` rows in sqe1.db using Claude Opus 4.7.

One API call per question. The model is sent the stem, lead-in, all five options,
and which one is correct; it returns a succinct explanation for each requested
option via a structured-output tool call. If the correct option's explanation
already exists (e.g. from the Mark Thomas detailed answers ingest), only the four
incorrect options are requested and the existing correct-answer explanation is
included in the prompt for context.

Setup
-----
1. Put your API key in a .env file alongside this script:

       ANTHROPIC_API_KEY=sk-ant-...

   (Get one at https://platform.claude.com/settings/keys.)

2. Install the SDK:

       pip install anthropic

3. Run:

       python fill_explanations.py                       # process all pending
       python fill_explanations.py --limit 5             # only 5 questions
       python fill_explanations.py --question-id 42      # one specific question
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

# Opus 4.7 list price (USD per 1M tokens); used only for the end-of-run estimate
PRICE_IN_PER_1M = 5.00
PRICE_OUT_PER_1M = 25.00


# ─────────────────────────────────────────────────────────────────────
# .env loader (no python-dotenv dependency)
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

SYSTEM_PROMPT = """You are an experienced English solicitor and legal educator producing study explanations for SQE1 — the Solicitors Qualifying Examination Part 1, which assesses the Functioning Legal Knowledge required to qualify as a solicitor in England and Wales.

For each single best answer multiple choice question, write SUCCINCT explanations:
- For the correct option: state why it is the best answer, citing the relevant rule, statute, case, or principle of the law of England and Wales.
- For each incorrect option: state specifically why it is wrong — identify the misconception, faulty reasoning, or partial truth that makes it plausible but incorrect.
- Each explanation should be 1–3 sentences. Use clear, precise legal English at the level of a competent newly qualified solicitor.
- Refer only to the law of England and Wales unless the question requires another jurisdiction.
- Do not restate the option text. No filler, no generic statements, no preamble.
- Be accurate. Where authority is helpful and well known, cite it briefly (statute section, case short name); do not fabricate references.

Return one explanation per requested option using the structured-output schema. Plain prose only — no markdown, no bullet points inside each explanation."""


def build_user_prompt(
    stem: str,
    lead_in: str,
    options: dict[str, str],
    correct_label: str,
    existing_correct_explanation: str | None,
    needed_labels: list[str],
) -> str:
    lines = [
        "## Question",
        "",
        stem,
        "",
        f"**Lead-in:** {lead_in}",
        "",
        "## Options",
        "",
    ]
    for label in "ABCDE":
        marker = "  ← CORRECT ANSWER" if label == correct_label else ""
        lines.append(f"**{label}.** {options[label]}{marker}")
    lines += ["", f"The correct answer is **option {correct_label}**.", ""]

    if existing_correct_explanation:
        others = [l for l in "ABCDE" if l != correct_label]
        lines += [
            "An explanation for the correct option is already on file:",
            "",
            f'"""{existing_correct_explanation}"""',
            "",
            f"Produce succinct explanations ONLY for the four incorrect options "
            f"({', '.join(others)}). For each, explain specifically why it is wrong. "
            f"Do NOT include an explanation for option {correct_label}.",
        ]
    else:
        lines.append(
            f"Produce a succinct explanation for each of options "
            f"{', '.join(needed_labels)}. For option {correct_label}, explain why it "
            "is the best answer. For each incorrect option, explain why it is wrong."
        )
    return "\n".join(lines)


def build_schema(needed_labels: list[str]) -> dict:
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                label: {
                    "type": "string",
                    "description": (
                        f"Succinct explanation (1–3 sentences) for option {label}."
                    ),
                }
                for label in needed_labels
            },
            "required": needed_labels,
            "additionalProperties": False,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────

def pending_question_ids(
    conn: sqlite3.Connection,
    limit: int | None = None,
    question_id: int | None = None,
) -> list[int]:
    if question_id is not None:
        return [question_id]
    sql = (
        "SELECT q.question_id FROM questions q "
        "WHERE EXISTS (SELECT 1 FROM options o "
        "              WHERE o.question_id = q.question_id "
        "                AND o.explanation IS NULL) "
        "ORDER BY q.question_id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(sql).fetchall()]


def fetch_question(conn: sqlite3.Connection, qid: int):
    q = conn.execute(
        "SELECT flk, stem, lead_in FROM questions WHERE question_id=?", (qid,)
    ).fetchone()
    if not q:
        return None
    opts = conn.execute(
        "SELECT label, text, is_correct, explanation FROM options "
        " WHERE question_id=? ORDER BY label",
        (qid,),
    ).fetchall()
    options = {r[0]: r[1] for r in opts}
    correct_label = next(r[0] for r in opts if r[2])
    correct_explanation = next((r[3] for r in opts if r[2]), None)
    missing_labels = [r[0] for r in opts if r[3] is None]
    return {
        "flk": q[0],
        "stem": q[1],
        "lead_in": q[2],
        "options": options,
        "correct_label": correct_label,
        "correct_explanation": correct_explanation,
        "missing_labels": missing_labels,
    }


# ─────────────────────────────────────────────────────────────────────
# Per-question processing
# ─────────────────────────────────────────────────────────────────────

def process_question(
    client: "anthropic.Anthropic",
    conn: sqlite3.Connection,
    qid: int,
) -> tuple[bool, object | None, str | None]:
    q = fetch_question(conn, qid)
    if q is None:
        return False, None, "no such question"
    if not q["missing_labels"]:
        return False, None, "already complete"

    correct_label = q["correct_label"]
    skip_correct = q["correct_explanation"] is not None

    if skip_correct:
        # only ask about incorrect missing options (the correct one already has an explanation)
        request_labels = [l for l in q["missing_labels"] if l != correct_label]
        if not request_labels:
            return False, None, "only the correct option was missing, but it already has an explanation"
    else:
        request_labels = q["missing_labels"]

    user_prompt = build_user_prompt(
        stem=q["stem"],
        lead_in=q["lead_in"],
        options=q["options"],
        correct_label=correct_label,
        existing_correct_explanation=q["correct_explanation"] if skip_correct else None,
        needed_labels=request_labels,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": build_schema(request_labels)},
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        return False, response.usage, "empty response"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return False, response.usage, f"JSON parse failed: {e}"

    updated = 0
    for label in request_labels:
        expl = parsed.get(label, "").strip()
        if not expl:
            continue
        conn.execute(
            "UPDATE options SET explanation = ? "
            " WHERE question_id = ? AND label = ? AND explanation IS NULL",
            (expl, qid, label),
        )
        updated += 1
    conn.commit()
    if updated == 0:
        return False, response.usage, "no explanations parsed"
    return True, response.usage, None


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Fill missing options.explanation rows in sqe1.db via Claude Opus 4.7.",
    )
    p.add_argument("--limit", type=int, help="Process at most N questions")
    p.add_argument("--question-id", type=int, help="Process only this question_id")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="Seconds to sleep between requests (default 0)")
    p.add_argument("--dry-run", action="store_true",
                   help="List pending question ids and exit")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found at {DB_PATH}. Run `python ingest.py init` first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    qids = pending_question_ids(conn, limit=args.limit, question_id=args.question_id)
    if not qids:
        print("Nothing to do — every option already has an explanation.")
        return

    if args.dry_run:
        print(f"{len(qids)} question(s) pending:")
        for qid in qids:
            print(f"  Q{qid}")
        return

    client = anthropic.Anthropic()
    print(f"Model: {MODEL}")
    print(f"Pending: {len(qids)} question(s)")

    ok = skipped = fail = 0
    total_in = total_out = 0
    started = time.monotonic()

    for i, qid in enumerate(qids, 1):
        try:
            success, usage, reason = process_question(client, conn, qid)
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
            )
        else:
            skipped += 1
            print(f"  [{i:>4}/{len(qids)}] · Q{qid}: {reason}")

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
