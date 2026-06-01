# SQE1 Specification Taxonomy

This file defines the structure used in `flk1_specification.md` and `flk2_specification.md` for representing the SRA's SQE1 assessment specifications. Use it whenever generating, parsing, or cross-referencing these files.

## Source

- **FLK1** — https://sqe.sra.org.uk/assessments/sqe1-assessments/sqe1-specification/flk1
- **FLK2** — https://sqe.sra.org.uk/assessments/sqe1-assessments/sqe1-specification/flk2

Each specification is rendered as one markdown file using the four-level hierarchy described below.

## Hierarchy

| Level | Markdown | Meaning | Example |
|---|---|---|---|
| 1 | `#` | **Area of law** — top-level subject, identified by an abbreviation only | `# BLP` |
| 2 | `##` | **Field** — a major topic group within the area | `## Taxation - business` |
| 3 | `###` | **Subject matter** — a discrete legal topic within the field | `### Value Added Tax` |
| 4 | `####` | **Sub-matter** — a specific point/sub-topic within the subject matter | `#### key principles relating to scope, supply, input and output tax` |

The deepest level is `####`. Where the SRA source has bullets nested more than four levels deep, the parent label is prefixed onto each `####` entry (separated by " — ") so that all detail is preserved without exceeding four levels. Example from FLK2 Property Law:

```
### Security of tenure under a business lease
#### Landlord and Tenant Act 1954 (Part II) — application of 1954 Act
#### Landlord and Tenant Act 1954 (Part II) — renewal lease by the tenant
```

## Area abbreviations

### FLK1 (file: `flk1_specification.md`)

| Code | Area of law |
|---|---|
| BLP | Business Law and Practice |
| DR | Dispute Resolution |
| CL | Contract Law |
| T | Tort |
| LSy | Legal System (of England and Wales, Sources of law, Constitutional & Administrative law, EU law) |
| LeSe | Legal Services (regulatory role of the SRA, funding options) |

### FLK2 (file: `flk2_specification.md`)

| Code | Area of law |
|---|---|
| PLP | Property Law and Practice |
| WAE | Wills and the Administration of Estates |
| SA | Solicitors Accounts |
| LL | Land Law |
| TL | Trusts Law |
| CrL | Criminal Liability |
| CrLP | Criminal Law and Practice |
| Ethics | SRA professional conduct (FLK2 ethics questions) |

> **Note on `Ethics` (FLK2):** The SRA Standards and Regulations apply across all practice areas and are examined in both FLK1 and FLK2. FLK1 uses the `LeSe` area for this material. FLK2 has no dedicated ethics area in the SRA specification, so questions in FLK2 scenarios that are primarily about SRA professional conduct obligations (rather than the substantive law of the scenario's practice area) are classified here rather than being misattributed to PLP, SA, or another substantive area.

## Conventions

- `#` headings contain **only** the abbreviation (no full name, no punctuation).
- Areas are separated by a horizontal rule (`---`).
- Subject-matter headings drop the trailing colon used on the SRA site (e.g. "Value Added Tax", not "Value Added Tax:").
- Where the SRA source has a `##`-level paragraph of prose (e.g. an exclusion notice or framing sentence with no bullets), it appears as plain text immediately under the `##` heading rather than promoted to a heading of its own.
- An entry that has no bullets beneath it on the SRA site is rendered as a `###` heading with no `####` children — never as a `####` orphan.

## How to use this taxonomy

When asked to:

- **Locate a topic** — search by area code first (`# CrLP`), then field (`##`), then subject matter (`###`).
- **Draft an MCQ targeting a specific point** — cite the full path, e.g. `BLP → Insolvency → claw-back of assets for creditors – preferences, transactions at an undervalue, fraudulent and wrongful trading, setting aside a floating charge`.
- **Add a new entry** — match the existing nesting depth; do not introduce `#####`. If the source bullet is deeper than four levels, flatten via the "Parent — child" prefix convention shown above.
- **Cross-reference between FLK1 and FLK2** — use the area code prefix to disambiguate (e.g. VAT appears in both `BLP → Taxation - business → Value Added Tax` and `PLP → Taxation – property → Value Added Tax`; they are different subject matters).
