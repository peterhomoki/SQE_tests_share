---
name: sqe1-sba-mcq-drafting
description: Draft, review or critique SQE1-style Single Best Answer (SBA) multiple choice questions for the Solicitors Qualifying Examination. Use when the user asks to write, draft, generate, produce, review, edit, critique or improve an SQE1 MCQ / SBA / single best answer question, or asks about SQE1 question structure (stem, lead in, options, distractors, cover test). Aligns with the SRA's guidance for SQE1 question writers.
---

# Drafting SQE1 Single Best Answer (SBA) MCQs

This skill captures the SRA's published guidance for writing SQE1 single best answer multiple choice questions. Apply these rules whenever drafting, reviewing or critiquing an SQE1-style question.

## What an SBA MCQ is

A Single Best Answer MCQ requires the candidate to select the **single most accurate** option from a list, based on:

- **Stem** — the fact pattern / scenario
- **Lead in** — the specific question being asked
- **Options** — five answers labelled A–E, of which exactly one is correct; the four incorrect options are **distractors**

Distractors must be **plausible**. They can be partially or wholly incorrect and are often based on common misconceptions, faulty reasoning, or partial correctness against the lead in.

## Level: pitch every question at a competent newly qualified solicitor

- Cover **core legal principles and rules**, not matters of detail a newly qualified solicitor would look up.
- Use the SRA's **Statement of Solicitor Competence** and the **Threshold Standard at level 3** as the benchmark.
- Test **application** of knowledge, not surface recall.

## 1. The Stem

**Must do:**

- Provide a realistic scenario a newly qualified solicitor might plausibly encounter.
- Contain **all information** needed to evaluate every option (correct + distractors). Never introduce new facts in the options.
- Use clear standard English (many candidates do not have English as a first language) — but use legal terminology where the subject requires.
- Use **general descriptors** ("the man", "the woman", "a solicitor", "the buyer") rather than proper nouns. Names can confuse, distract, or skew familiarity.
- Use the **minimum number of characters/actors** needed.
- Keep an appropriate balance of characters across the question bank.

**Must avoid:**

- Irrelevant detail or background — apply the **"who cares?" test**: if a fact does not support a correct or distractor option, cut it.
- Stereotyping or bias.
- Sensitive or sensational topics where possible.
- Excessive word count — candidates have limited time. Vary length to need, but stay tight.

## 2. The Lead In

**Must do:**

- Be **focused on one specific task**. Ask one thing.
- Make clear that **only one option** is to be selected.
- Aim to pass the **cover test**: if the candidate covered the options after reading the stem and lead in, could they predict roughly what the correct answer should be? If yes, the question is testing application — not pattern-matching against options.

**Must avoid:**

- **Negative lead ins** (e.g. "What should the man *not* do?", "Which is *not* an incorrect statement?") — research shows these are significantly harder and unfair.
- Multi-part lead ins (e.g. "what resolutions are needed *and* what disclosures *and* what filings…"). Split into separate questions or narrow the focus.
- Pure recall lead ins (e.g. "What is the definition of X?").

**Acceptable exception to the cover test:**

A lead in like *"Which of the following best describes…"* fails the cover test but is acceptable where distractors are very plausible or partially correct and the correct answer is superior — reflecting real-world choice between viable options.

**Positive/negative answer alignment:**

If any option could legitimately be "none" or "no [X]", phrase the lead in to permit it, e.g. *"What searches, **if any**, should the solicitor undertake?"* Then an option can read "No searches" or "None" rather than just "No".

## 3. The Options

**Must do:**

- Have **exactly one** correct (or single best) answer.
- Make all four distractors **plausible** — likely to be selected by some candidates. Draw on:
  - Faulty reasoning
  - Common misconceptions among practitioners or law students
  - Being partially correct against the lead in
- Ensure every option **directly answers the lead in**. An option that doesn't address the question can be eliminated without applying knowledge.
- Aim for **consistent length** and **parallel language/grammar** across options. If five fully parallel options is impossible, group as 3+2 or similar — the goal is to prevent candidates inferring the answer from drafting clues.
- Keep options **short and simple**. Overly long options force guessing on time grounds.

**Must avoid:**

- Introducing **new facts or conditionality** in options (watch for "if" / "unless" clauses that add information beyond the stem).
- **Overlapping** options (candidates can eliminate both).
- **Cueing** — drafting quirks that flag the correct answer.
- **Absolute terms** in distractors (*always*, *never*) — they are easy to dismiss.
- **Vague terms** (*seldom*, *often*) — same effect, plus ambiguity.

## Drafting workflow

When drafting a new SQE1 SBA MCQ, work in this order:

1. **Pick the FLK point** being tested. State to yourself the single legal principle or rule the question targets.
2. **Draft the lead in first.** This forces focus on one task. Sanity-check against the cover test.
3. **Draft the correct answer.** Make sure it directly answers the lead in.
4. **Draft four plausible distractors.** For each, identify *why* a candidate would pick it (misconception, faulty reasoning, partial correctness).
5. **Draft the stem** containing every fact needed to discriminate between the five options — and nothing more. Apply the who-cares test to every sentence.
6. **Self-review against this checklist** (below).
7. **Where possible, blind-review by a non-subject expert** for clarity, ambiguity, stereotyping, and cueing.

## Self-review checklist

Stem:
- [ ] Realistic scenario for a newly qualified solicitor
- [ ] Contains all information needed for every option
- [ ] No information in options that isn't in the stem
- [ ] No irrelevant detail (who-cares test passed)
- [ ] General descriptors not names; minimum actors
- [ ] Clear standard English; no colloquialisms; legal terms only where required
- [ ] No stereotyping, bias, sensational content
- [ ] Tight word count

Lead in:
- [ ] One specific task
- [ ] Clear that only one option is to be selected
- [ ] Not negative
- [ ] Passes the cover test (or is a justified "best describes" form)
- [ ] If "none" is a possible answer, lead in says "if any"

Options:
- [ ] Exactly one correct/best answer
- [ ] All four distractors plausible with identifiable rationale
- [ ] All options answer the lead in
- [ ] Consistent length and parallel language (or 3+2 grouping)
- [ ] Short and simple
- [ ] No new facts, no conditionality
- [ ] No overlap, no cueing
- [ ] No absolute or vague terms in distractors
- [ ] Pitched at competent newly qualified solicitor (application, not detail recall)

## Output format

When producing a question for the user, present it as:

```
**Stem**

[scenario paragraphs]

**Lead in**

[the question, in bold]

A. [option]
B. [option]
C. [option]
D. [option]
E. [option]

**Correct answer:** [letter]

**Rationale:**
- Correct: [why the correct option is best]
- A/B/C/D/E (each distractor): [the specific misconception or partial reasoning that makes it plausible but wrong]
```

Always include the rationale section — it makes the question reviewable and forces you to verify each distractor is genuinely plausible.

## When reviewing an existing question

Walk it through the self-review checklist above. For each failure, suggest a concrete rewrite rather than a vague critique. Flag in particular:

- Stem facts that support no option (cut them)
- Options containing new facts (move to stem or remove)
- Distractors that are obviously wrong / not plausible (rewrite around a real misconception)
- Negative or multi-task lead ins (reframe)
- Absolute or vague language in distractors (rephrase)
- Inconsistent option length or grammar (re-balance)
