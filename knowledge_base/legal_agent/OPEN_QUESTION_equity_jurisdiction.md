# Open line: equity jurisdiction — evidence still being gathered

**Status: OPEN. More evidence expected.** This is not a settled question in this
corpus and should not be treated as one. The principal's stated aim is the
*correct* course of action, explicitly not sovereign-citizen theory, and the
working method is that where sources disagree, the answer is more evidence on
both sides rather than a verdict.

## What is in the corpus now

| Work | Standing | Reaches |
|------|----------|---------|
| Pomeroy, *Equity Jurisprudence* vol. 1 (1886) | treatise, public domain | The canonical American classification: **exclusive / concurrent / auxiliary** jurisdiction. 187 sections, 731 subject terms, 3,937 cases |
| Maitland, *Equity: A Course of Lectures* (1916) | treatise, public domain | Why equity exists as a distinct body and what it does that law cannot |
| Chandler, *Express Trusts Under the Common Law* (1912) | commentary, public domain | The business trust as a distinct mode of administration |
| Delaware Statutory Trust Act, 12 Del. C. ch. 38 | statute, public domain | What Chandler's argument became in force today |
| Federal Rules of Civil Procedure | rules | Procedure after the 1938 merger |
| "Private Ordering" (SPC University, 2026) | commentary | Private ordering as a permission the law grants, not an exemption |
| Claimed steps to invoke equity jurisdiction | advocacy, evaluated | A chatbot-generated filing recipe, recorded WITH its evaluation |

## The live disagreement, stated fairly

- **The merger claim.** Law and equity merged procedurally in 1938; there is one
  civil action, and no separate chancery side to be admitted to in most courts.
- **The exclusive-jurisdiction claim.** Merger of PROCEDURE did not abolish the
  substantive distinction. Some rights exist only in equity - a trust
  beneficiary holds an equitable estate that law does not recognise at all.
  Pomeroy's exclusive jurisdiction is precisely this category.

Both are true and they are not in conflict once separated. Claude initially
stated the first in a way that flattened the second, and the principal
corrected it. The correction is the useful part and is why this file exists.

## Known gaps — what to look for next

- **Pomeroy vols. 2 and 3.** Vol. 1 carries the jurisdiction taxonomy; the
  remedies are in the later volumes. Archive has them.
- **Story, *Commentaries on Equity Jurisprudence*.** Foundational American
  treatment, several public-domain editions.
- **Modern doctrine is missing entirely.** Scott & Ascher on Trusts, Bogert,
  and the Restatement (Third) of Trusts are the current authorities and are all
  in copyright - the principal supplies a licensed copy, `tools/ingest_pdf.py`
  takes it.
- **Case law.** CourtListener is wired to the Legal Agent and nothing here has
  been checked against how courts actually rule. Codified doctrine is the
  floor; what courts do is the evidence.
- **State-specific.** Whether a given state retained a separate chancery court
  decides half of this and is not in the corpus for any state but Delaware.

## How to reach it

Doctrine terms address the treatises directly - `lookup "exclusive
jurisdiction"` returns Pomeroy's passages, not a page number to go find.
Retrieval is by exact term, case name, or page; never by similarity, per
CLAUDE.md, because the CAG cache ranks boilerplate over on-point passages.
