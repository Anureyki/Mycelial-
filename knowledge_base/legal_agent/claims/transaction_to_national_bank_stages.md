# "Arizona Transaction to National Bank" — a staged-inquiry framework

**Standing: COMMENTARY — evaluated, adopted with corrections.**

Source: 12-slide Instagram carousel, account `joshua.aaron369719`, self-labelled
**"AI content"** by the account itself. Reviewed 2026-08-29 in full (all 12
slides read before classification).

## Why this is commentary and not advocacy

It advances no legal conclusion and asks the reader to win nothing. Every slide
is a *stage plus an inquiry* — "who transferred the receivable, to whom, and
what rights actually moved?" The later stages are phrased conditionally
throughout: "may continue to apply", "can govern", "if the receivable is
securitized". That is the grammar of a checklist for locating authority, not of
a theory.

The citations it gives are accurate where checkable: 12 CFR 1002 is Regulation
B, 1026 is Regulation Z, 220 is Regulation T, 221 is Regulation U, A.R.S.
§ 47-9203 is genuinely Arizona's enactment of UCC § 9-203, and 12 U.S.C. § 25b
is the Dodd-Frank preemption standard that codifies the *Barnett Bank* test.

## What is right about it

The central insight is that **a financed transaction is not one legal event but
a sequence of them**, and a different body of law attaches at each. Confusing
the stages is the most common error in this area: attachment gets conflated with
perfection, assignment with novation, and the dealer's funding with the buyer's
obligation. Slide 4 says so explicitly — *distinguish attachment from later
perfection, assignment, or enforcement* — and it is right.

Slide 6 is the sharpest of the twelve. **Who paid or credited the dealer is a
separate question from what the buyer owes.** Those are two distinct events with
two distinct sets of records, and treating them as one is how a chain of title
becomes unauditable. Its closing line — *transaction ledgers and assignment
records matter more than labels* — is the same rule this system already runs on.

## Three corrections

**1. The numbering oversells the sequence.** Stages 1–12 read as a pipeline every
transaction traverses. Most do not. Stages 9–11 in particular:

- **Regulation T** (12 CFR 220) governs credit extended *by a broker-dealer* to
  finance or carry securities. An auto or consumer receivable assigned to a bank
  does not touch it.
- **Regulation U** (12 CFR 221) requires *both* purpose credit *and* margin
  stock as direct or indirect security. A receivable secured by a vehicle is
  neither.
- **Securitization** operates on the *pool* and on the certificates issued
  against it. It does not convert an individual obligor's contract into a
  security, and it does not extinguish the obligation or the holder's right to
  enforce it.

**2. Stage 6 is the doorway to a bad argument, and the slide does not say so.**
"What asset or account was used to fund or settle the dealer" is a legitimate
accounting question with a boring answer — a warehouse line, a purchase of
chattel paper, a deposit credit. It is also the precise entry point for the
"the bank created the money from your signature, so no consideration passed"
theory, which courts reject uniformly. The inquiry is sound; that conclusion
does not follow from it. Legal should ask the question and refuse the leap.

**3. Preemption is not categorical.** Slide 12 gestures at the National Bank Act
displacing state law. Since Dodd-Frank, 12 U.S.C. § 25b requires a rule-by-rule
determination against the *Barnett Bank* "prevent or significantly interfere"
standard. A national bank holder does not sweep away the state layer wholesale.

## The Arizona problem, and what was done about it

**The state layer is Arizona in every slide. The principal operates in Texas.**
Arizona's citations do not transfer to any other state — but the *uniform
section numbers* do, because every state enacted Article 9 from the same text
and then renumbered it into its own code.

The four conventions verified on 2026-08-29 show why this cannot be templated:

| State | UCC 9-203 as enacted |
|-------|----------------------|
| Arizona | `A.R.S. § 47-9203` — article fused to section |
| Texas | `Tex. Bus. & Com. Code § 9.203` — hyphen becomes a period |
| California | `Cal. Com. Code § 9203` — separator dropped |
| Florida | `Fla. Stat. § 679.2031` — article renumbered **and a digit appended** |

Florida is the reason this is a verified table rather than a format string.
A naive pattern yields `679.203`, which is a different provision.

So the framework was ingested **structurally, not jurisdictionally**:

- `reference/legal_agent/transaction_layers.json` — the 12 stages, with the
  federal layer separated from the state layer, and `applies` / `caution`
  fields carrying the corrections above. Reasons in **uniform** section numbers.
- `reference/legal_agent/jurisdictions.json` — 51 jurisdictions; 4 verified
  against the states' own published text, 47 carrying labelled candidates.
- `transaction_layers` and `cite_in_jurisdiction` resolve the state layer to
  whatever jurisdiction is on record, so the same framework renders in Texas
  citations today and Florida citations after a move.

## What was NOT ingested

Arizona law. A.R.S. Title 44 and Title 47 are not in the corpus and should not
be — they are one state's enactment of provisions the agent already reasons
about uniformly, and holding them would invite citing Arizona at a Texas
problem.

## The federal layer, which IS uniform, was ingested

`12 CFR` Parts **1002** (Reg B), **1026** (Reg Z), **220** (Reg T), **221**
(Reg U), **7** and **34** (OCC) — 1,283 sections, public domain, from eCFR.
These do not vary by state, which is exactly why they belong in the corpus while
the state layer belongs in a resolver.

Related: [[perfection_by_control_super_priority]] — same body of law, and the
control sections (9-104, 9-106) are now in the uniform-section index.
