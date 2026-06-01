"""
Run SQE1 benchmark against one or more LLMs using a test input file.

Usage:
  python run_llm_test.py --input test_all.json --model claude-opus-4-5
  python run_llm_test.py --input test_all.json --models claude-opus-4-5,deepseek-v4-pro
  python run_llm_test.py --input test_all.json --all-models
  python run_llm_test.py --input test_all.json --model claude-opus-4-5 --max-questions 20 --dry-run

Environment variables required:
  ANTHROPIC_API_KEY   — for Claude models
  DEEPSEEK_API_KEY    — for DeepSeek models

NOTE: Verify model IDs against provider documentation before running.
      DeepSeek model IDs (deepseek-v4-flash / deepseek-v4-pro) should be
      confirmed at https://platform.deepseek.com/docs.
"""

import argparse, json, os, re, time
from pathlib import Path as _Path
# Load .env from the project directory before anything else
_env_file = _Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Model registry ────────────────────────────────────────────────────────────
# provider: "anthropic" | "deepseek"
# api_id: exact string sent to the API
MODELS = {
    # Anthropic baselines (no thinking)
    "claude-opus-4-8":            {"provider": "anthropic", "api_id": "claude-opus-4-8",            "max_tokens": 16},
    "claude-opus-4-5":            {"provider": "anthropic", "api_id": "claude-opus-4-5",            "max_tokens": 16},
    "claude-opus-4-0":            {"provider": "anthropic", "api_id": "claude-opus-4-0",            "max_tokens": 16},
    "claude-sonnet-4-6":          {"provider": "anthropic", "api_id": "claude-sonnet-4-6",          "max_tokens": 16},
    "claude-haiku-4-5":           {"provider": "anthropic", "api_id": "claude-haiku-4-5-20251001",  "max_tokens": 16},

    # Anthropic with extended thinking — three preset budgets per model
    "claude-opus-4-8-think2k":    {"provider": "anthropic", "api_id": "claude-opus-4-8",            "max_tokens":  2112, "thinking_budget":  2048},
    "claude-opus-4-8-think4k":    {"provider": "anthropic", "api_id": "claude-opus-4-8",            "max_tokens":  4160, "thinking_budget":  4096},
    "claude-opus-4-8-think16k":   {"provider": "anthropic", "api_id": "claude-opus-4-8",            "max_tokens": 16448, "thinking_budget": 16384},
    "claude-sonnet-4-6-think2k":  {"provider": "anthropic", "api_id": "claude-sonnet-4-6",          "max_tokens":  2112, "thinking_budget":  2048},
    "claude-sonnet-4-6-think4k":  {"provider": "anthropic", "api_id": "claude-sonnet-4-6",          "max_tokens":  4160, "thinking_budget":  4096},
    "claude-sonnet-4-6-think16k": {"provider": "anthropic", "api_id": "claude-sonnet-4-6",          "max_tokens": 16448, "thinking_budget": 16384},
    "claude-haiku-4-5-think2k":   {"provider": "anthropic", "api_id": "claude-haiku-4-5-20251001",  "max_tokens":  2112, "thinking_budget":  2048},
    "claude-haiku-4-5-think4k":   {"provider": "anthropic", "api_id": "claude-haiku-4-5-20251001",  "max_tokens":  4160, "thinking_budget":  4096},
    "claude-haiku-4-5-think16k":  {"provider": "anthropic", "api_id": "claude-haiku-4-5-20251001",  "max_tokens": 16448, "thinking_budget": 16384},

    # DeepSeek reasoning models via Anthropic-compatible API
    "deepseek-v4-flash":          {"provider": "deepseek",  "api_id": "deepseek-v4-flash",          "max_tokens":  4096},
    "deepseek-v4-pro":            {"provider": "deepseek",  "api_id": "deepseek-v4-pro",            "max_tokens":  4096},
    # Higher-headroom DeepSeek — same models, larger max_tokens (16K) so reasoning is never truncated
    "deepseek-v4-flash-16K":      {"provider": "deepseek",  "api_id": "deepseek-v4-flash",          "max_tokens": 16384},
    "deepseek-v4-pro-16K":        {"provider": "deepseek",  "api_id": "deepseek-v4-pro",            "max_tokens": 16384},
    # Non-reasoning DeepSeek baseline (V3 chat)
    "deepseek-chat":              {"provider": "deepseek",  "api_id": "deepseek-chat",              "max_tokens":    16},
}

SYSTEM_PROMPT = (
    "You are sitting the SQE1 (Solicitors Qualifying Examination Part 1), "
    "a multiple-choice assessment for trainee solicitors in England and Wales. "
    "For each question you will be given a scenario followed by five options labelled A to E. "
    "Respond with only the single capital letter of the best answer. "
    "Do not explain your reasoning."
)

RETRY_DELAYS = [5, 15, 30, 60]   # seconds between retries on rate-limit


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(q: dict) -> str:
    lines = [q["stem"], "", q["lead_in"], ""]
    for opt in q["options"]:
        lines.append(f"{opt['label']}. {opt['text']}")
    return "\n".join(lines)


def parse_answer(raw: str) -> str:
    """Extract a single A–E letter from the model's response."""
    raw = raw.strip()
    if raw and raw[0].upper() in "ABCDE":
        return raw[0].upper()
    m = re.search(r"\b([A-E])\b", raw.upper())
    return m.group(1) if m else "INVALID"


# ── API clients (lazy-initialised) ───────────────────────────────────────────

def get_anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_deepseek_client():
    import anthropic
    return anthropic.Anthropic(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/anthropic",
    )


def call_anthropic(client, api_id: str, prompt: str, dry_run: bool,
                   max_tokens: int = 16, thinking_budget: int = 0):
    if dry_run:
        return "A", "", 0, 0
    import anthropic
    kwargs = dict(
        model=api_id,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    if thinking_budget > 0:
        # Anthropic requires max_tokens > budget_tokens; bump if needed.
        if max_tokens <= thinking_budget:
            kwargs["max_tokens"] = thinking_budget + 64
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            resp = client.messages.create(**kwargs)
            # content may be [ThinkingBlock, TextBlock] for reasoning models
            raw_text = next((b.text for b in resp.content if hasattr(b, "text")), "")
            if not raw_text:
                # Reasoner ran out of max_tokens before emitting content.
                # Salvage from the tail of the thinking block where the
                # model usually states its conclusion.
                thinking = next(
                    (getattr(b, "thinking", "") for b in resp.content if hasattr(b, "thinking")),
                    "",
                )
                raw_text = thinking[-300:] if thinking else ""
            tokens_in  = resp.usage.input_tokens
            tokens_out = resp.usage.output_tokens
            return parse_answer(raw_text), raw_text, tokens_in, tokens_out
        except anthropic.RateLimitError:
            if attempt < len(RETRY_DELAYS):
                print(f"    Rate-limited. Retrying in {RETRY_DELAYS[attempt]}s…")
            else:
                raise
        except anthropic.BadRequestError as e:
            print(f"    Bad request: {e}. Skipping question.")
            return "ERROR", "", 0, 0
        except anthropic.APIStatusError as e:
            if e.status_code == 529:   # overloaded
                if attempt < len(RETRY_DELAYS):
                    print(f"    API overloaded. Retrying in {RETRY_DELAYS[attempt]}s…")
                else:
                    raise
            else:
                raise




# ── Per-model test run ────────────────────────────────────────────────────────

def run_model(model_key: str, questions: list, output_dir: Path,
              max_questions, dry_run: bool, delay_s: float,
              thinking_budget: int = 0, output_suffix: str = ""):

    meta       = MODELS[model_key]
    provider   = meta["provider"]
    api_id     = meta["api_id"]
    max_tokens = meta.get("max_tokens", 16)

    # CLI --thinking-budget overrides; otherwise use the model's preset (if any)
    effective_budget = thinking_budget if thinking_budget > 0 else meta.get("thinking_budget", 0)

    print(f"\n{'='*70}")
    print(f"Model : {model_key}  ({api_id})")
    print(f"Questions: {min(max_questions or len(questions), len(questions))}"
          + ("  [DRY RUN]" if dry_run else ""))
    if effective_budget > 0:
        print(f"Extended thinking enabled — budget {effective_budget:,} tokens")
    print("="*70)

    client = get_anthropic_client() if provider == "anthropic" else get_deepseek_client()
    call_fn = call_anthropic

    records = []
    total_in = total_out = 0
    start_time = time.time()
    run_start_ts = datetime.now(timezone.utc).isoformat()

    subset = questions[:max_questions] if max_questions else questions

    for i, q in enumerate(subset, 1):
        qid     = q["qid"]
        correct = q["correct_answer"]
        prompt  = build_prompt(q)

        ts_start = datetime.now(timezone.utc).isoformat()
        try:
            answer, raw_response, tok_in, tok_out = call_fn(
                client, api_id, prompt, dry_run, max_tokens,
                thinking_budget=effective_budget if provider == "anthropic" else 0,
            )
        except Exception as e:
            print(f"\n  FATAL error on QID {qid}: {e}")
            print("  Saving partial results and stopping this model.")
            break
        ts_end = datetime.now(timezone.utc).isoformat()

        is_correct = (answer == correct)
        total_in  += tok_in
        total_out += tok_out

        status = "✓" if is_correct else f"✗ (gave {answer}, correct {correct})"
        elapsed = time.time() - start_time
        rate = (total_in + total_out) / max(elapsed, 1)
        print(f"  [{i:4d}/{len(subset)}] QID {qid:4d}  {status}"
              f"  | tokens used: {total_in+total_out:,}  ({rate:.0f} tok/s)")

        records.append({
            "qid":          qid,
            "answer":       answer,
            "raw_response": raw_response,
            "correct":      correct,
            "is_correct":   is_correct,
            "ts_start":     ts_start,
            "ts_end":       ts_end,
            "topic":        q.get("topic", {}),
        })

        if delay_s > 0 and not dry_run:
            time.sleep(delay_s)

    # ── Scoring ──────────────────────────────────────────────────────────────
    total     = len(records)
    n_correct = sum(1 for r in records if r["is_correct"])
    n_wrong   = total - n_correct
    success   = n_correct / total * 100 if total else 0.0

    wrong_qids = [r["qid"] for r in records if not r["is_correct"]]

    # Area breakdown
    by_area: dict = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0})
    by_field: dict = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0})
    by_subject: dict = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0})

    for r in records:
        t = r["topic"]
        l1 = t.get("lvl1") or "Unknown"
        l2 = t.get("lvl2") or ""
        l3 = t.get("lvl3") or ""

        for bucket, key in [
            (by_area,    l1),
            (by_field,   f"{l1} > {l2}" if l2 else None),
            (by_subject, f"{l1} > {l2} > {l3}" if l3 else None),
        ]:
            if key:
                bucket[key]["total"]   += 1
                bucket[key]["correct"] += int(r["is_correct"])
                bucket[key]["wrong"]   += int(not r["is_correct"])

    def area_summary(d, min_q=3):
        return sorted(
            [
                {
                    "topic": k,
                    "total": v["total"],
                    "correct": v["correct"],
                    "wrong": v["wrong"],
                    "success_pct": round(v["correct"] / v["total"] * 100, 1),
                }
                for k, v in d.items()
                if v["total"] >= min_q
            ],
            key=lambda x: x["success_pct"],
        )

    results = {
        "model":         model_key,
        "api_id":        api_id,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "run_start":     run_start_ts,
        "run_end":       datetime.now(timezone.utc).isoformat(),
        "dry_run":       dry_run,
        "total":         total,
        "correct":       n_correct,
        "wrong":         n_wrong,
        "success_pct":   round(success, 1),
        "tokens_in":     total_in,
        "tokens_out":    total_out,
        "wrong_qids":    wrong_qids,
        "area_breakdown":    area_summary(by_area,    min_q=2),
        "field_breakdown":   area_summary(by_field,   min_q=3),
        "subject_breakdown": area_summary(by_subject, min_q=3),
        "records":       records,
    }

    # ── Save JSON ─────────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = model_key.replace("/", "_") + (output_suffix or "")
    json_path = output_dir / f"{safe_name}_{ts}_results.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Save human-readable summary ───────────────────────────────────────────
    txt_path = output_dir / f"{safe_name}_{ts}_summary.txt"
    lines = [
        f"SQE1 Benchmark — {model_key}",
        f"Run at: {results['timestamp']}",
        f"{'DRY RUN  ' if dry_run else ''}",
        "=" * 60,
        f"Total questions : {total}",
        f"Correct         : {n_correct}",
        f"Wrong           : {n_wrong}",
        f"Success rate    : {success:.1f}%",
        f"Tokens in/out   : {total_in:,} / {total_out:,}",
        "",
        "WRONG QIDs:",
        ", ".join(str(q) for q in wrong_qids) if wrong_qids else "  (none)",
        "",
        "WEAKEST AREAS (lvl1, min 2 questions):",
    ]
    for a in results["area_breakdown"][:10]:
        lines.append(f"  {a['success_pct']:5.1f}%  {a['correct']:3d}/{a['total']:3d}  {a['topic']}")
    lines += ["", "WEAKEST FIELDS (lvl2, min 3 questions):"]
    for a in results["field_breakdown"][:10]:
        lines.append(f"  {a['success_pct']:5.1f}%  {a['correct']:3d}/{a['total']:3d}  {a['topic']}")
    lines += ["", "WEAKEST SUBJECTS (lvl3, min 3 questions):"]
    for a in results["subject_breakdown"][:10]:
        lines.append(f"  {a['success_pct']:5.1f}%  {a['correct']:3d}/{a['total']:3d}  {a['topic']}")

    txt_path.write_text("\n".join(lines), encoding="utf-8")

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n  Result : {n_correct}/{total} correct  ({success:.1f}%)")
    print(f"  Tokens : {total_in:,} in / {total_out:,} out")
    print(f"  Saved  : {json_path.name}")
    print(f"           {txt_path.name}")

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark LLMs on SQE1 questions")
    parser.add_argument("--input", required=True, help="Test input JSON (from generate_test_input.py)")

    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--model", help="Single model key, e.g. claude-opus-4-5")
    grp.add_argument("--models", help="Comma-separated model keys")
    grp.add_argument("--all-models", action="store_true", help="Run all registered models")

    parser.add_argument("--max-questions", type=int, help="Stop after N questions (for cost control)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse prompts but do not call APIs; answer fixed as A")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between API calls (default 0.5)")
    parser.add_argument("--output-dir", default="results", help="Directory for output files (default: results/)")
    # Question-subset selection (at most one of these)
    sub = parser.add_mutually_exclusive_group()
    sub.add_argument("--rerun-wrong-from", metavar="RESULTS_JSON",
                     help="Re-run only the QIDs that were wrong in a prior results JSON. "
                          "Useful with --thinking-budget for a deeper second pass.")
    sub.add_argument("--qids", metavar="LIST",
                     help="Comma-separated list of QIDs to run (e.g. '4,17,42').")
    sub.add_argument("--qids-file", metavar="CSV",
                     help="CSV file with a 'qid' column — only those QIDs are run.")

    parser.add_argument("--thinking-budget", type=int, default=0, metavar="N",
                        help="Enable Anthropic extended thinking with N reasoning tokens budget "
                             "(0=off, typical 2048-8192). Ignored for DeepSeek.")
    args = parser.parse_args()

    # Resolve model list
    if args.all_models:
        model_keys = list(MODELS.keys())
    elif args.models:
        model_keys = [m.strip() for m in args.models.split(",")]
    else:
        model_keys = [args.model]

    unknown = [m for m in model_keys if m not in MODELS]
    if unknown:
        parser.error(f"Unknown model(s): {unknown}. Known: {list(MODELS.keys())}")

    # Load input
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    questions = data["questions"]
    print(f"Loaded {len(questions)} questions from {args.input}")

    # Optional: filter to a chosen subset of QIDs
    prior_records_by_qid = {}
    target_qids = None      # None = no filter; set = filter
    filter_label = ""       # short label for log + filename

    if args.rerun_wrong_from:
        prior_path = Path(args.rerun_wrong_from)
        if not prior_path.exists():
            parser.error(f"--rerun-wrong-from file not found: {prior_path}")
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        target_qids = set(prior.get("wrong_qids", []))
        prior_records_by_qid = {r["qid"]: r for r in prior.get("records", [])}
        filter_label = f"wrong in {prior_path.name}"
    elif args.qids:
        try:
            target_qids = {int(x.strip()) for x in args.qids.split(",") if x.strip()}
        except ValueError as e:
            parser.error(f"--qids must be a comma-separated list of integers: {e}")
        filter_label = f"--qids ({len(target_qids)} QIDs)"
    elif args.qids_file:
        qf = Path(args.qids_file)
        if not qf.exists():
            parser.error(f"--qids-file not found: {qf}")
        import csv
        with qf.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if "qid" not in (reader.fieldnames or []):
                parser.error(f"CSV must have a 'qid' column (found: {reader.fieldnames})")
            target_qids = {int(row["qid"]) for row in reader if row["qid"].strip()}
        filter_label = f"{qf.name} ({len(target_qids)} QIDs)"

    if target_qids is not None:
        before = len(questions)
        questions = [q for q in questions if q["qid"] in target_qids]
        print(f"Subset mode: filtered to {len(questions)} of {before} questions  ({filter_label})")
        if not questions:
            sys.exit("No matching QIDs found in the input file.")

    # Check API keys
    if not args.dry_run:
        needs_anthropic = any(MODELS[m]["provider"] == "anthropic" for m in model_keys)
        needs_deepseek  = any(MODELS[m]["provider"] == "deepseek"  for m in model_keys)
        if needs_anthropic and not os.environ.get("ANTHROPIC_API_KEY"):
            parser.error("ANTHROPIC_API_KEY not set")
        if needs_deepseek and not os.environ.get("DEEPSEEK_API_KEY"):
            parser.error("DEEPSEEK_API_KEY not set")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build the suffix for output files so reruns / subsets are obvious
    suffix = ""
    if args.rerun_wrong_from:
        suffix += "_rerun"
    elif args.qids or args.qids_file:
        suffix += "_qids"
    if args.thinking_budget > 0:
        suffix += f"_think{args.thinking_budget}"

    all_results = []
    for model_key in model_keys:
        result = run_model(
            model_key=model_key,
            questions=questions,
            output_dir=output_dir,
            max_questions=args.max_questions,
            dry_run=args.dry_run,
            delay_s=args.delay,
            thinking_budget=args.thinking_budget,
            output_suffix=suffix,
        )
        all_results.append(result)

        # Flip report: how did rerun answers differ from the prior run?
        if prior_records_by_qid:
            flipped_right = flipped_wrong = stayed_wrong = 0
            flip_details = []
            for r in result["records"]:
                prior = prior_records_by_qid.get(r["qid"])
                if not prior: continue
                prior_correct = prior.get("is_correct", False)
                if r["is_correct"] and not prior_correct:
                    flipped_right += 1
                    flip_details.append(f"  ↑ QID {r['qid']:4d}  was {prior['answer']} → now {r['answer']} ✓")
                elif not r["is_correct"] and prior_correct:
                    flipped_wrong += 1
                    flip_details.append(f"  ↓ QID {r['qid']:4d}  was {prior['answer']} ✓ → now {r['answer']}")
                elif not r["is_correct"]:
                    stayed_wrong += 1
            print(f"\n{'─'*50}")
            print(f"FLIP REPORT for {model_key}:")
            print(f"  Wrong → right (rescued)  : {flipped_right}")
            print(f"  Right → wrong (regressed): {flipped_wrong}")
            print(f"  Still wrong              : {stayed_wrong}")
            if flip_details:
                for line in flip_details[:30]:
                    print(line)
                if len(flip_details) > 30:
                    print(f"  ... and {len(flip_details)-30} more")

    # ── Cross-model comparison ─────────────────────────────────────────────
    if len(all_results) > 1:
        print(f"\n{'='*70}")
        print("COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Model':<24}  {'Correct':>8}  {'Success%':>9}")
        print(f"  {'-'*24}  {'-'*8}  {'-'*9}")
        for r in sorted(all_results, key=lambda x: -x["success_pct"]):
            print(f"  {r['model']:<24}  {r['correct']:>3}/{r['total']:<4}  {r['success_pct']:>8.1f}%")

        comp_path = output_dir / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        comp_lines = ["SQE1 LLM Comparison", "=" * 60,
                      f"{'Model':<24}  {'Correct':>8}  {'Success%':>9}"]
        for r in sorted(all_results, key=lambda x: -x["success_pct"]):
            comp_lines.append(
                f"{r['model']:<24}  {r['correct']:>3}/{r['total']:<4}  {r['success_pct']:>8.1f}%"
            )
        comp_path.write_text("\n".join(comp_lines), encoding="utf-8")
        print(f"\n  Comparison saved → {comp_path}")


if __name__ == "__main__":
    main()
