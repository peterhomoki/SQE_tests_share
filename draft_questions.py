"""
Draft new SQE1 SBA MCQs with Claude Opus 4.8 and insert them into sqe1.db.

For each target topic the model is given:
- the sqe1-sba-mcq-drafting skill (SKILL.md) — drafting rules
- the relevant subject's study material file from toconsider_for_new_questions/
  (heavy, sent with prompt caching so the cost is paid once per subject)
- the topic's full taxonomy chain
- up to 3 existing example questions from the same topic for style match

Output is structured JSON. Every field needed by the DB is filled out:
- questions: flk, source_file, source_question_num, stem, lead_in
- options:   label, text, is_correct, explanation
- question_topics: topic_id, flk, reasoning

Usage
-----
  python draft_questions.py --topic-ids 48,132,234              # 1 question per topic
  python draft_questions.py --topic-id 48 --count 5             # 5 questions for one topic
  python draft_questions.py --topics-file plan.csv              # CSV: topic_id,count
  python draft_questions.py --topic-ids 48 --dry-run            # print, don't insert
"""

from __future__ import annotations

import argparse, csv, json, os, re, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path

ROOT       = Path(__file__).parent
DB_PATH    = ROOT / "sqe1.db"
ENV_PATH   = ROOT / ".env"
SKILL_PATH = ROOT / ".claude" / "skills" / "sqe1-sba-mcq-drafting" / "SKILL.md"
MAT_DIR    = ROOT / "toconsider_stripped" # was: toconsider_for_new_questions

# Drafter registry — provider+api_id+pricing
DRAFTERS = {
    "opus": {
        "provider": "anthropic",
        "api_id":   "claude-opus-4-8",
        "max_tokens": 4096,
        "source_file": "Claude_Opus_4_8_drafted",
        "prices": {"in": 15.00, "out": 75.00, "cw": 18.75, "cr": 1.50},
    },
    "deepseek-pro": {
        "provider": "deepseek",
        "api_id":   "deepseek-v4-pro",
        "max_tokens": 4096,
        "source_file": "DeepSeek_Pro_drafted",
        "prices": {"in": 0.55, "out": 2.19, "cw": 0.55, "cr": 0.14},
    },
    "deepseek-flash": {
        "provider": "deepseek",
        "api_id":   "deepseek-v4-flash",
        "max_tokens": 4096,
        "source_file": "DeepSeek_Flash_drafted",
        "prices": {"in": 0.27, "out": 1.10, "cw": 0.27, "cr": 0.07},
    },
    # 16K-headroom variants — same DeepSeek models, larger max_tokens so the
    # reasoner is never truncated. Same api_id, separate source_file label
    # so DB rows can be distinguished from the standard runs.
    "deepseek-pro-16K": {
        "provider": "deepseek",
        "api_id":   "deepseek-v4-pro",
        "max_tokens": 16384,
        "source_file": "DeepSeek_Pro_16K_drafted",
        "prices": {"in": 0.55, "out": 2.19, "cw": 0.55, "cr": 0.14},
    },
    "deepseek-flash-16K": {
        "provider": "deepseek",
        "api_id":   "deepseek-v4-flash",
        "max_tokens": 16384,
        "source_file": "DeepSeek_Flash_16K_drafted",
        "prices": {"in": 0.27, "out": 1.10, "cw": 0.27, "cr": 0.07},
    },
}

# Light verifiers — Opus 4.8 with optional thinking budgets.
# Picked via --verifier; default 'opus' is one-shot (no thinking).
VERIFIERS = {
    "opus":          {"provider":"anthropic", "api_id":"claude-opus-4-8", "max_tokens": 4096,
                      "thinking_budget": 0,
                      "prices": {"in":15.00, "out":75.00, "cw":18.75, "cr":1.50}},
    "opus-think2k":  {"provider":"anthropic", "api_id":"claude-opus-4-8", "max_tokens": 6144,
                      "thinking_budget": 2048,
                      "prices": {"in":15.00, "out":75.00, "cw":18.75, "cr":1.50}},
    "opus-think4k":  {"provider":"anthropic", "api_id":"claude-opus-4-8", "max_tokens": 8192,
                      "thinking_budget": 4096,
                      "prices": {"in":15.00, "out":75.00, "cw":18.75, "cr":1.50}},
    "opus-think16k": {"provider":"anthropic", "api_id":"claude-opus-4-8", "max_tokens": 20480,
                      "thinking_budget": 16384,
                      "prices": {"in":15.00, "out":75.00, "cw":18.75, "cr":1.50}},
}

# area code → list of supplementary study-material filenames in MAT_DIR.
# Loaded from area_to_files.json next to this script. The shipped JSON
# is effectively empty (only "_comment" / "_example" keys, both ignored
# below). Edit area_to_files.json to add your own area → filename map;
# no code change required.

AREA_TO_FILES_PATH = ROOT / "area_to_files.json"


def _load_area_to_files() -> dict[str, list[str]]:
    import json
    if not AREA_TO_FILES_PATH.exists():
        return {}
    try:
        raw = json.loads(AREA_TO_FILES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ! could not parse {AREA_TO_FILES_PATH.name}: {e}",
              file=sys.stderr)
        return {}
    # Drop documentation keys (anything starting with "_") and any
    # values that aren't lists of strings.
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = v
    return out


AREA_TO_FILES: dict[str, list[str]] = _load_area_to_files()

# ─────────────────────────────────────────────────────────────────────
# .env loader

def load_env():
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

try:
    import anthropic
except ImportError:
    sys.exit("Install the SDK first:  pip install anthropic")

def get_client(provider):
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY not set (use .env)")
        return anthropic.Anthropic()
    elif provider == "deepseek":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            sys.exit("DEEPSEEK_API_KEY not set (use .env)")
        return anthropic.Anthropic(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com/anthropic",
        )
    raise ValueError(f"Unknown provider: {provider}")


# ─────────────────────────────────────────────────────────────────────
# Topic & taxonomy helpers

def get_topic_chain(conn, topic_id):
    chain = {}
    tid = topic_id
    while tid:
        row = conn.execute(
            "SELECT topic_id, name, level, parent_id, flk, code FROM topics WHERE topic_id=?",
            (tid,),
        ).fetchone()
        if not row:
            return None
        chain[row[2]] = {"id": row[0], "name": row[1], "flk": row[4], "code": row[5]}
        tid = row[3]
    return chain


def get_area_code(chain):
    return chain.get(1, {}).get("code")


def get_flk(chain):
    return chain.get(1, {}).get("flk")


def chain_label(chain):
    parts = [chain[lvl]["name"] for lvl in sorted(chain) if chain[lvl].get("name")]
    return " > ".join(parts)


def example_questions(conn, topic_id, max_n=3):
    """A few existing questions on the same topic, for style reference."""
    rows = conn.execute(
        "SELECT q.question_id, q.stem, q.lead_in "
        "FROM questions q JOIN question_topics qt ON q.question_id = qt.question_id "
        "WHERE qt.topic_id = ? ORDER BY RANDOM() LIMIT ?",
        (topic_id, max_n),
    ).fetchall()
    out = []
    for qid, stem, lead in rows:
        opts = conn.execute(
            "SELECT label, text, is_correct FROM options WHERE question_id=? ORDER BY label",
            (qid,),
        ).fetchall()
        correct = next((o[0] for o in opts if o[2]), None)
        out.append({"qid": qid, "stem": stem, "lead_in": lead,
                    "options": [(o[0], o[1]) for o in opts],
                    "correct": correct})
    return out


# ─────────────────────────────────────────────────────────────────────
# Prompt construction

SYSTEM_HEADER = (
    "You are an experienced English solicitor and legal educator drafting "
    "SQE1 Single Best Answer multiple choice questions for the Solicitors "
    "Qualifying Examination Part 1 (England and Wales).\n\n"
    "You will be given:\n"
    "1. The sqe1-sba-mcq-drafting SKILL — strict drafting rules to follow.\n"
    "2. One or more substantive law manuals for the relevant subject — the "
    "factual basis for the question.\n"
    "4. A specific target topic from the SRA taxonomy and a few existing "
    "example questions on the same topic for style reference.\n\n"
    "Draft ONE single best answer MCQ that complies with every rule in the "
    "SKILL. Use the structured-output schema to return all fields needed to "
    "store the question in our database, including per-option explanations."
)


def build_system_blocks(skill_md, area_materials):
    blocks = [
        {"type": "text", "text": SYSTEM_HEADER},
        {"type": "text", "text": "# SKILL: sqe1-sba-mcq-drafting\n\n" + skill_md},
        ]
    # Heavy block — cached so we pay once per subject, not per question.
    if area_materials:
        joined = "\n\n---\n\n".join(area_materials)
        blocks.append({
            "type": "text",
            "text": "# Substantive law manuals\n\n" + joined,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


def build_user_prompt(chain, flk, area_code, examples, count_so_far, count_total):
    lines = [
        f"## Target topic",
        f"- FLK: {flk}",
        f"- Area code: {area_code}",
        f"- Taxonomy chain: {chain_label(chain)}",
        f"- Generating question {count_so_far} of {count_total} for this topic.",
        "",
    ]
    if examples:
        lines.append("## Example questions on this topic (for STYLE reference only — do not copy)")
        for ex in examples:
            lines += [f"### Existing QID {ex['qid']}",
                      f"**Stem:** {ex['stem']}",
                      f"**Lead-in:** {ex['lead_in']}"]
            for lbl, txt in ex["options"]:
                marker = "  ← CORRECT" if lbl == ex["correct"] else ""
                lines.append(f"  {lbl}. {txt}{marker}")
            lines.append("")
    lines += [
        "## Task",
        "Draft ONE new SBA MCQ on this exact topic following the SKILL. "
        "Make sure the new question is substantively different from the examples "
        "(different fact pattern, different sub-element if possible). "
        "Test APPLICATION, not recall.",
        "",
        "Return your answer using the structured output schema. "
        "Every field is required.",
    ]
    return "\n".join(lines)


def _option_schema():
    return {
        "type": "object",
        "properties": {
            "text":        {"type": "string"},
            "is_correct":  {"type": "boolean"},
            "explanation": {"type": "string",
                            "description": "1–3 sentences. Why correct OR which misconception this distractor embodies."},
        },
        "required": ["text", "is_correct", "explanation"],
        "additionalProperties": False,
    }


def _draft_schema():
    return {
        "type": "object",
        "properties": {
            "stem":     {"type": "string", "description": "Scenario (no lead-in)."},
            "lead_in":  {"type": "string", "description": "The single positive question being asked."},
            "option_A": _option_schema(),
            "option_B": _option_schema(),
            "option_C": _option_schema(),
            "option_D": _option_schema(),
            "option_E": _option_schema(),
            "flk_point_tested":        {"type": "string",
                                        "description": "The single legal principle this question targets."},
            "topic_mapping_reasoning": {"type": "string",
                                        "description": "Why this question fits the supplied topic (1–2 sentences)."},
            "difficulty":              {"type": "string", "enum": ["1", "2", "3", "4"],
                                        "description": "1=single element, 2=sub-element boundary, 3=element+defence, 4=competing doctrines."},
        },
        "required": ["stem", "lead_in",
                     "option_A", "option_B", "option_C", "option_D", "option_E",
                     "flk_point_tested", "topic_mapping_reasoning", "difficulty"],
        "additionalProperties": False,
    }


DRAFT_SCHEMA = _draft_schema()

# Verifier returns the FINAL draft (unchanged if compliant, corrected otherwise)
# plus a verdict and brief notes for telemetry.
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fix"],
                    "description": "'pass' if no skill violations; 'fix' if corrections were applied."},
        "notes":   {"type": "string",
                    "description": "Brief description of issues found, or 'no issues' if pass."},
        **{k: v for k, v in _draft_schema()["properties"].items()},
    },
    "required": ["verdict", "notes"] + _draft_schema()["required"],
    "additionalProperties": False,
}


def normalise_options(draft):
    """Convert option_A..E in the model response into a list of dicts with labels.
    Mutates `draft` in place: adds draft['options']."""
    opts = []
    for lbl in "ABCDE":
        o = draft.get(f"option_{lbl}")
        if not isinstance(o, dict):
            opts.append({"label": lbl, "text": "", "is_correct": False, "explanation": ""})
            continue
        opts.append({
            "label":       lbl,
            "text":        o.get("text", ""),
            "is_correct":  bool(o.get("is_correct", False)),
            "explanation": o.get("explanation", ""),
        })
    draft["options"] = opts
    return draft


# Light verifier — sees only the skill (no study materials)
VERIFY_SYSTEM = (
    "You are reviewing an SQE1 SBA MCQ draft against the sqe1-sba-mcq-drafting "
    "SKILL. Apply every rule in the SKILL strictly. Common violations to "
    "catch and correct: negative lead-ins, new facts in options, "
    "inconsistent option length / grammar, pure-recall lead-ins, "
    "missing 'if any' when an option could be 'none', duplicated label "
    "letters baked into option text, distractors that don't directly answer "
    "the lead-in, absolute or vague terms in distractors.\n\n"
    "You will receive the draft as JSON. Use the structured-output tool to "
    "return the FINAL version of the question:\n"
    "- If the draft already complies with every SKILL rule, return verdict "
    "  'pass' and emit the draft unchanged.\n"
    "- If you need to correct anything, return verdict 'fix' and emit the "
    "  corrected version. Limit changes to SKILL compliance — do NOT alter "
    "  substantive law unless an option states an incorrect rule.\n"
    "Provide brief 'notes' (1–2 sentences) describing what changed, or "
    "'no issues' if pass."
)


def build_verify_system_blocks(skill_md):
    return [
        {"type": "text", "text": VERIFY_SYSTEM},
        {
            "type": "text",
            "text": "# SKILL: sqe1-sba-mcq-drafting\n\n" + skill_md,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _extract_json_object(text):
    """Find the outermost {...} JSON object in text and parse it."""
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def call_tool(client, model_cfg, system_blocks, user_prompt, schema, tool_name):
    """Returns (parsed_dict, usage). Picks a strategy by provider:
    - Anthropic: forced tool_choice (strict structured output).
    - DeepSeek: thinking mode forbids forced tool_choice, so we describe the
      schema inline and parse JSON out of the text/thinking response."""
    if model_cfg["provider"] == "anthropic":
        return _call_with_tools(client, model_cfg, system_blocks, user_prompt,
                                schema, tool_name)
    return _call_json_text(client, model_cfg, system_blocks, user_prompt,
                           schema, tool_name)


def _call_with_tools(client, model_cfg, system_blocks, user_prompt, schema, tool_name):
    tools = [{
        "name":        tool_name,
        "description": f"Submit the structured {tool_name} payload.",
        "input_schema": schema,
    }]
    kwargs = dict(
        model       = model_cfg["api_id"],
        max_tokens  = model_cfg.get("max_tokens", 4096),
        system      = system_blocks,
        messages    = [{"role": "user", "content": user_prompt}],
        tools       = tools,
    )
    budget = model_cfg.get("thinking_budget", 0)
    if budget > 0:
        # Anthropic doesn't allow forced tool_choice with extended thinking; let
        # the model choose to use the tool voluntarily (it almost always does).
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        kwargs["tool_choice"] = {"type": "tool", "name": tool_name}
    resp = client.messages.create(**kwargs)
    tool_use = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
    return (tool_use.input if tool_use else None), resp.usage


def _call_json_text(client, model_cfg, system_blocks, user_prompt, schema, tool_name):
    schema_desc = json.dumps(schema, indent=2)
    augmented = (
        user_prompt +
        "\n\n---\n"
        "**OUTPUT FORMAT — strict requirement.**  "
        "Your final response MUST be a single JSON object that conforms exactly "
        "to the schema below. No prose, no markdown fences, no explanation — "
        "just the JSON object as the entire response.\n\n"
        f"Schema:\n```json\n{schema_desc}\n```"
    )
    resp = client.messages.create(
        model      = model_cfg["api_id"],
        max_tokens = model_cfg.get("max_tokens", 4096),
        system     = system_blocks,
        messages   = [{"role": "user", "content": augmented}],
    )
    # Prefer the TextBlock; if reasoner only produced ThinkingBlock, scan its tail
    text = next((b.text for b in resp.content if hasattr(b, "text")), "")
    parsed = _extract_json_object(text)
    if parsed is None:
        thinking = next((getattr(b, "thinking", "") for b in resp.content
                         if hasattr(b, "thinking")), "")
        parsed = _extract_json_object(thinking[-3000:])
    return parsed, resp.usage


# ─────────────────────────────────────────────────────────────────────
# Validation

def validate_draft(d):
    errors = []
    if not d.get("stem", "").strip():    errors.append("empty stem")
    if not d.get("lead_in", "").strip(): errors.append("empty lead_in")

    # negative lead-in check
    if re.search(r"\b(which\s+.*\s+not|is\s+not\b|does\s+not\b|would\s+not\b|except\b)",
                 d.get("lead_in", "").lower()):
        # allow "if any" / "whether" / "why X is not" patterns — those are positive
        if not re.search(r"\bwhether\b|\bif any\b|\bwhy\b", d["lead_in"].lower()):
            errors.append(f"possible negative lead-in: {d['lead_in']!r}")

    opts = d.get("options", [])
    if len(opts) != 5: errors.append(f"need 5 options, got {len(opts)}")
    labels = [o.get("label") for o in opts]
    if sorted(labels) != list("ABCDE"): errors.append(f"labels not A-E: {labels}")

    correct = [o for o in opts if o.get("is_correct")]
    if len(correct) != 1: errors.append(f"need exactly 1 correct option, got {len(correct)}")

    for o in opts:
        if not o.get("text", "").strip():        errors.append(f"option {o.get('label')} empty text")
        if not o.get("explanation", "").strip(): errors.append(f"option {o.get('label')} empty explanation")
        # the bug we just fixed — don't reintroduce it
        if o.get("text", "")[:1] == o.get("label") and o.get("text", "")[1:2].isupper():
            errors.append(f"option {o.get('label')} starts with duplicated label letter")
    return errors


# ─────────────────────────────────────────────────────────────────────
# DB insertion

def next_source_num(conn, source_file):
    row = conn.execute(
        "SELECT COALESCE(MAX(source_question_num), 0) FROM questions WHERE source_file=?",
        (source_file,),
    ).fetchone()
    return row[0] + 1


def insert_question(conn, flk, draft, topic_id, source_file):
    src_num = next_source_num(conn, source_file)
    cur = conn.execute(
        "INSERT INTO questions (flk, source_file, source_question_num, stem, lead_in) "
        "VALUES (?,?,?,?,?)",
        (flk, source_file, src_num, draft["stem"].strip(), draft["lead_in"].strip()),
    )
    qid = cur.lastrowid
    for o in draft["options"]:
        conn.execute(
            "INSERT INTO options (question_id, label, text, is_correct, explanation) "
            "VALUES (?,?,?,?,?)",
            (qid, o["label"], o["text"].strip(), int(bool(o["is_correct"])),
             o["explanation"].strip()),
        )
    reasoning = (
        f"[Drafted as {source_file}] Difficulty={draft.get('difficulty')}; "
        f"FLK point tested: {draft.get('flk_point_tested','').strip()}; "
        f"{draft.get('topic_mapping_reasoning','').strip()}"
    )[:1000]
    conn.execute(
        "INSERT INTO question_topics (question_id, topic_id, flk, reasoning) "
        "VALUES (?,?,?,?)",
        (qid, topic_id, flk, reasoning),
    )
    conn.commit()
    return qid, src_num


# ─────────────────────────────────────────────────────────────────────
# Per-topic processing

def _accumulate(totals, usage):
    totals["in"]  += getattr(usage, "input_tokens", 0)
    totals["out"] += getattr(usage, "output_tokens", 0)
    totals["cw"]  += getattr(usage, "cache_creation_input_tokens", 0) or 0
    totals["cr"]  += getattr(usage, "cache_read_input_tokens", 0) or 0


def process_topic(drafter_client, verifier_client, conn,
                  drafter_cfg, verifier_cfg, skill_md,
                  topic_id, count, dry_run, do_verify,
                  collect_to=None):
    chain = get_topic_chain(conn, topic_id)
    if chain is None:
        print(f"  [SKIP] topic_id {topic_id} not found")
        return [], {"in":0,"out":0,"cw":0,"cr":0}, {"in":0,"out":0,"cw":0,"cr":0}
    area_code = get_area_code(chain)
    flk       = get_flk(chain)

    files = AREA_TO_FILES.get(area_code, [])
    materials = []
    for fn in files:
        p = MAT_DIR / fn
        if p.exists():
            materials.append(f"## {fn}\n\n" + p.read_text(encoding="utf-8", errors="replace"))
        else:
            print(f"  [WARN] material file missing: {fn}")

    draft_sys  = build_system_blocks(skill_md, materials)
    verify_sys = build_verify_system_blocks(skill_md) if do_verify else None

    inserted = []
    draft_tok  = {"in":0,"out":0,"cw":0,"cr":0}
    verify_tok = {"in":0,"out":0,"cw":0,"cr":0}
    examples = example_questions(conn, topic_id, max_n=3)

    for i in range(1, count + 1):
        prompt = build_user_prompt(chain, flk, area_code, examples, i, count)
        print(f"\n  → Drafting with {drafter_cfg['api_id']}  "
              f"(topic {topic_id} '{chain_label(chain)}', {i}/{count})…")

        try:
            draft, usage = call_tool(drafter_client, drafter_cfg,
                                     draft_sys, prompt, DRAFT_SCHEMA,
                                     tool_name="submit_question")
        except anthropic.APIError as e:
            print(f"     API error: {e}")
            continue
        _accumulate(draft_tok, usage)

        if not draft:
            print(f"     drafter returned no usable output")
            continue

        # Optional light verification
        verify_verdict = None
        verify_notes = None
        if do_verify and verifier_cfg is not None:
            print(f"  → Verifying with {verifier_cfg['api_id']} "
                  f"(budget={verifier_cfg.get('thinking_budget',0)})…")
            v_prompt = ("Review this draft against the SKILL and return the "
                        "FINAL version of the question.\n\n```json\n"
                        + json.dumps(draft, indent=2) + "\n```")
            try:
                verified, v_usage = call_tool(verifier_client, verifier_cfg,
                                              verify_sys, v_prompt, VERIFY_SCHEMA,
                                              tool_name="submit_final")
            except anthropic.APIError as e:
                print(f"     verify API error: {e} (keeping original draft)")
                verified = None
            else:
                _accumulate(verify_tok, v_usage)

            if verified and verified.get("verdict") in ("pass", "fix"):
                verify_verdict = verified.get("verdict")
                verify_notes   = (verified.get("notes") or "").strip()
                print(f"     verifier verdict: {verify_verdict}  — {verify_notes[:160]}")
                if verify_verdict == "fix":
                    for k in DRAFT_SCHEMA["required"]:
                        if k in verified:
                            draft[k] = verified[k]
            else:
                print(f"     verifier returned no usable output (keeping original)")

        normalise_options(draft)
        errs = validate_draft(draft)
        if errs:
            print(f"     validation failed: {'; '.join(errs)}")
            print(f"     stem snippet: {draft.get('stem','')[:120]!r}")
            continue

        # Build a flat record we can either insert into the DB or save to file
        record = {
            "topic_id": topic_id,
            "flk":      flk,
            "stem":     draft["stem"].strip(),
            "lead_in":  draft["lead_in"].strip(),
            **{f"option_{lbl}": draft[f"option_{lbl}"] for lbl in "ABCDE"},
            "flk_point_tested":        draft.get("flk_point_tested", ""),
            "topic_mapping_reasoning": draft.get("topic_mapping_reasoning", ""),
            "difficulty":              draft.get("difficulty", ""),
            "verify_verdict":          verify_verdict,
            "verify_notes":            verify_notes,
            "drafter":                 drafter_cfg["source_file"],
        }

        if collect_to is not None:
            collect_to.append(record)
            print(f"     ✓ collected — lead-in: {draft['lead_in'][:80]}")
            inserted.append(record["lead_in"][:40])   # placeholder identifier
        elif dry_run:
            print()
            print(f"     ── STEM ──")
            for line in draft["stem"].split("\n"):
                print(f"     {line}")
            print(f"     ── LEAD-IN ──")
            print(f"     {draft['lead_in']}")
            print(f"     ── OPTIONS ──")
            for o in draft["options"]:
                mark = "✓" if o["is_correct"] else " "
                print(f"     {mark} {o['label']}. {o['text']}")
                print(f"          ↳ {o['explanation']}")
            print(f"     ── META ──")
            print(f"     Difficulty: {draft.get('difficulty')}  | "
                  f"FLK point: {draft.get('flk_point_tested','')[:80]}")
            print(f"     [DRY-RUN] not inserting")
        else:
            qid, src_num = insert_question(conn, flk, draft, topic_id,
                                           source_file=drafter_cfg["source_file"])
            print(f"     ✓ inserted as QID {qid} "
                  f"({drafter_cfg['source_file']} Q{src_num})  "
                  f"lead-in: {draft['lead_in'][:80]}")
            inserted.append(qid)

    return inserted, draft_tok, verify_tok


# ─────────────────────────────────────────────────────────────────────
# CLI

def parse_targets(args):
    """Returns ordered list of (topic_id, count) pairs grouped by area for cache reuse."""
    targets = []
    if args.topics_file:
        # Strip comment lines (#...) and blanks before handing to DictReader
        with open(args.topics_file, encoding="utf-8-sig") as f:
            data_lines = [ln for ln in f
                          if ln.strip() and not ln.lstrip().startswith("#")]
        if not data_lines:
            return []
        reader = csv.DictReader(data_lines)
        for row in reader:
            tid = (row.get("topic_id") or "").strip()
            if not tid: continue
            try:
                targets.append((int(tid), int((row.get("count") or "1").strip())))
            except ValueError:
                continue
    elif args.topic_id:
        targets.append((args.topic_id, args.count))
    elif args.topic_ids:
        for t in args.topic_ids.split(","):
            t = t.strip()
            if t: targets.append((int(t), args.count))
    return targets


def group_by_area(conn, targets):
    """Sort targets so questions for the same area run consecutively → prompt cache hits."""
    keyed = []
    for tid, n in targets:
        chain = get_topic_chain(conn, tid) or {}
        area = (chain.get(1) or {}).get("code") or "ZZZ"
        keyed.append((area, tid, n))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [(t, n) for _, t, n in keyed]


def _cost(totals, prices):
    return (totals["in"]  / 1e6 * prices["in"]
          + totals["out"] / 1e6 * prices["out"]
          + totals["cw"]  / 1e6 * prices["cw"]
          + totals["cr"]  / 1e6 * prices["cr"])


# ─────────────────────────────────────────────────────────────────────
# Drafts-file JSON helpers
# Format:
#   { "format": "sqe1-drafts-v1", "drafter": str, "verifier": str|None,
#     "drafted_at": iso, "verified_at": iso|None, "drafts": [record, ...] }

DRAFTS_FORMAT = "sqe1-drafts-v1"


def write_drafts_file(path, drafts, drafter_name, verifier_name=None):
    from datetime import datetime, timezone
    payload = {
        "format":      DRAFTS_FORMAT,
        "drafter":     drafter_name,
        "verifier":    verifier_name,
        "drafted_at":  datetime.now(timezone.utc).isoformat(),
        "verified_at": datetime.now(timezone.utc).isoformat() if verifier_name else None,
        "drafts":      drafts,
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    print(f"\nWrote {len(drafts)} draft(s) → {path}")


def load_drafts_file(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"Drafts file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("format") != DRAFTS_FORMAT:
        sys.exit(f"Not a {DRAFTS_FORMAT} file: {p}")
    return data


def run_verify_file(in_path, out_path, verifier_cfg, verifier_name, skill_md):
    """Read an existing drafts file, verify each entry, write a new file."""
    data = load_drafts_file(in_path)
    drafts = data["drafts"]
    if not drafts:
        sys.exit("Drafts file is empty.")
    client = get_client(verifier_cfg["provider"])
    sys_blocks = build_verify_system_blocks(skill_md)
    verify_tot = {"in":0,"out":0,"cw":0,"cr":0}

    print(f"Verifying {len(drafts)} draft(s) from {in_path}")
    print(f"Verifier: {verifier_name} ({verifier_cfg['api_id']}, "
          f"budget={verifier_cfg.get('thinking_budget',0)})")
    for i, rec in enumerate(drafts, 1):
        # Build the draft sub-object the verifier expects
        draft_only = {k: rec.get(k) for k in DRAFT_SCHEMA["required"]}
        v_prompt = ("Review this draft against the SKILL and return the "
                    "FINAL version of the question.\n\n```json\n"
                    + json.dumps(draft_only, indent=2) + "\n```")
        print(f"  [{i:3d}/{len(drafts)}] topic_id={rec['topic_id']}  "
              f"lead-in: {rec['lead_in'][:60]}…")
        try:
            verified, usage = call_tool(client, verifier_cfg, sys_blocks,
                                        v_prompt, VERIFY_SCHEMA,
                                        tool_name="submit_final")
        except anthropic.APIError as e:
            print(f"     API error: {e} (keeping original)")
            verified = None
        else:
            _accumulate(verify_tot, usage)

        if verified and verified.get("verdict") in ("pass", "fix"):
            rec["verify_verdict"] = verified["verdict"]
            rec["verify_notes"]   = (verified.get("notes") or "").strip()
            print(f"     verdict={rec['verify_verdict']} — {rec['verify_notes'][:120]}")
            if verified["verdict"] == "fix":
                for k in DRAFT_SCHEMA["required"]:
                    if k in verified:
                        rec[k] = verified[k]
        else:
            print(f"     verifier returned no usable output (kept original)")

    write_drafts_file(out_path, drafts,
                      drafter_name=data.get("drafter", "unknown"),
                      verifier_name=verifier_name)
    cost = _cost(verify_tot, verifier_cfg["prices"])
    print(f"\nVerifier tokens — input: {verify_tot['in']:,}  "
          f"output: {verify_tot['out']:,}  "
          f"cache write: {verify_tot['cw']:,}  cache read: {verify_tot['cr']:,}")
    print(f"Verifier cost: ${cost:.3f}")


def run_import_file(in_path, conn):
    """Read a drafts file and insert each draft into the DB."""
    data = load_drafts_file(in_path)
    drafts = data["drafts"]
    if not drafts:
        sys.exit("Drafts file is empty.")
    drafter_label = data.get("drafter", "imported")
    print(f"Importing {len(drafts)} draft(s) from {in_path}")
    print(f"Source file (DB column) : {drafter_label}")
    print(f"Verifier used           : {data.get('verifier') or 'none'}")

    # Build a draft-shaped dict for insert_question
    inserted = []
    for i, rec in enumerate(drafts, 1):
        # Reuse the validation we already have
        d = {k: rec.get(k) for k in DRAFT_SCHEMA["required"]}
        normalise_options(d)
        errs = validate_draft(d)
        if errs:
            print(f"  [{i:3d}] SKIP (validation: {'; '.join(errs)})")
            continue
        qid, src_num = insert_question(conn, rec["flk"], d, rec["topic_id"],
                                       source_file=drafter_label)
        inserted.append(qid)
        print(f"  [{i:3d}] ✓ QID {qid} ({drafter_label} Q{src_num})  "
              f"lead-in: {rec['lead_in'][:60]}")
    print(f"\nInserted {len(inserted)} of {len(drafts)} drafts.")


def main():
    p = argparse.ArgumentParser(description="Draft / verify / import SQE1 SBA MCQs")

    # Mode: exactly one of draft / verify-file / import-file
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--verify-file", metavar="DRAFTS_JSON",
                      help="Verify-only mode: load an existing drafts file, re-verify, write a new file.")
    mode.add_argument("--import-file", metavar="DRAFTS_JSON",
                      help="Import-only mode: insert all drafts from a JSON file into the DB.")

    # Drafting targets (used in default draft mode)
    p.add_argument("--topic-ids",   help="Comma-separated topic IDs")
    p.add_argument("--topic-id",    type=int, help="Single topic ID")
    p.add_argument("--topics-file", help="CSV with columns: topic_id,count")
    p.add_argument("--count", type=int, default=1, help="Questions per topic (default 1)")

    # Drafter (drafting mode)
    p.add_argument("--drafter", choices=list(DRAFTERS.keys()), default="opus",
                   help="Drafter model (default: opus = claude-opus-4-8)")

    # Verifier
    p.add_argument("--verify", action="store_true",
                   help="Run light verification on each draft (drafting mode).")
    p.add_argument("--verifier", choices=list(VERIFIERS.keys()), default="opus",
                   help="Verifier model. One of: opus (one-shot), "
                        "opus-think2k, opus-think4k, opus-think16k. Default: opus.")

    # Output destinations (drafting mode)
    p.add_argument("--output-file", metavar="DRAFTS_JSON",
                   help="Write drafts to a JSON file instead of inserting into the DB. "
                        "Use --import-file later to load it.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print drafts but do not insert / write (drafting mode).")

    args = p.parse_args()

    if not SKILL_PATH.exists():
        sys.exit(f"Skill not found: {SKILL_PATH}")
    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    skill_md     = SKILL_PATH.read_text(encoding="utf-8")

    # ── Mode A: verify-file ─────────────────────────────────────────────
    if args.verify_file:
        if not args.output_file:
            sys.exit("--verify-file requires --output-file (where to write the result).")
        verifier_cfg = VERIFIERS[args.verifier]
        run_verify_file(args.verify_file, args.output_file, verifier_cfg,
                        args.verifier, skill_md)
        return

    # ── Mode B: import-file ─────────────────────────────────────────────
    if args.import_file:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        run_import_file(args.import_file, conn)
        conn.close()
        return

    # ── Mode C: drafting (with optional verify + optional file output) ──
    if not (args.topic_ids or args.topic_id or args.topics_file):
        sys.exit("Drafting mode requires --topic-id, --topic-ids, or --topics-file.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    targets = group_by_area(conn, parse_targets(args))
    if not targets:
        sys.exit("No targets parsed.")

    drafter_cfg     = DRAFTERS[args.drafter]
    drafter_client  = get_client(drafter_cfg["provider"])
    verifier_cfg    = VERIFIERS[args.verifier] if args.verify else None
    verifier_client = get_client(verifier_cfg["provider"]) if args.verify else None

    total_q = sum(n for _, n in targets)
    print(f"Targets : {len(targets)} topic(s), {total_q} question(s) total")
    print(f"Drafter : {args.drafter} ({drafter_cfg['api_id']})")
    if args.verify:
        print(f"Verify  : {args.verifier} ({verifier_cfg['api_id']}, "
              f"budget={verifier_cfg.get('thinking_budget',0)})")
    else:
        print(f"Verify  : none")
    if args.output_file:
        out_mode = f"file → {args.output_file}"
    elif args.dry_run:
        out_mode = "DRY-RUN"
    else:
        out_mode = "APPLY (insert into DB)"
    print(f"Output  : {out_mode}")

    draft_tot  = {"in":0,"out":0,"cw":0,"cr":0}
    verify_tot = {"in":0,"out":0,"cw":0,"cr":0}
    inserted_all = []
    collected = [] if args.output_file else None

    for tid, n in targets:
        ins, dt, vt = process_topic(drafter_client, verifier_client, conn,
                                    drafter_cfg, verifier_cfg,
                                    skill_md, 
                                    tid, n, args.dry_run, args.verify,
                                    collect_to=collected)
        inserted_all += ins
        for k in draft_tot:  draft_tot[k]  += dt[k]
        for k in verify_tot: verify_tot[k] += vt[k]

    conn.close()

    if args.output_file and collected:
        write_drafts_file(args.output_file, collected,
                          drafter_name=drafter_cfg["source_file"],
                          verifier_name=args.verifier if args.verify else None)

    cost_d = _cost(draft_tot,  drafter_cfg["prices"])
    cost_v = _cost(verify_tot, verifier_cfg["prices"]) if args.verify else 0.0
    print()
    if not args.output_file and not args.dry_run:
        print(f"Inserted: {len(inserted_all)} question(s) — QIDs {inserted_all}"
              if inserted_all else "Inserted: 0")
    print(f"\nDrafter ({args.drafter}) tokens — "
          f"input: {draft_tot['in']:,}  output: {draft_tot['out']:,}  "
          f"cache write: {draft_tot['cw']:,}  cache read: {draft_tot['cr']:,}")
    print(f"Drafter cost: ${cost_d:.3f}")
    if args.verify:
        print(f"\nVerifier ({args.verifier}) tokens — "
              f"input: {verify_tot['in']:,}  output: {verify_tot['out']:,}  "
              f"cache write: {verify_tot['cw']:,}  cache read: {verify_tot['cr']:,}")
        print(f"Verifier cost: ${cost_v:.3f}")
    print(f"\nEstimated total: ${cost_d + cost_v:.3f}  (list-price)")


if __name__ == "__main__":
    main()
