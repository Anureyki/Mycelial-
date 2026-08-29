# "Super-priority status" — perfection by control vs a filed UCC-1

**Stance: ADVOCACY — evaluated. The underlying doctrine is REAL; the claim as
stated is overgeneralised.** Source: an Instagram reel (paths2frdm2022,
captured 2026-08-29) asserting that a lender perfecting by control has absolute
priority over one who merely filed a paper UCC-1 even if the filing came first,
and that "filing a UCC-1 is NO LONGER ENOUGH".

**Why that stance:** it states a rule that is true for *some* collateral as
though it were true for all, and its conclusion ("filing is no longer enough")
is false for the collateral most security interests actually cover. It cites no
section of Article 9.

**The statute is NOT yet in this corpus.** Everything below is doctrine stated
from general knowledge and must be checked against the enacted text before it
is relied on. See "What to ingest" at the end.

---

## Where the claim is right

Perfection **by control** is a real Article 9 method, and for the collateral
where it applies, control genuinely does outrank a filing — including a filing
made first. The categories where control is available:

| Collateral | Control provision | Priority provision |
|------------|-------------------|--------------------|
| Deposit accounts | § 9-104 | § 9-327 — control beats non-control |
| Investment property | § 9-106 | § 9-328 — control beats filing |
| Letter-of-credit rights | § 9-107 | § 9-329 |
| Electronic chattel paper | § 9-105 | § 9-330 |
| Controllable electronic records | Article 12 (2022 amendments) | § 9-326A / Art. 12 |

For a **deposit account** as original collateral, control is not merely better -
it is the ONLY method. A financing statement does not perfect it at all.

## Where the claim is wrong

For ordinary collateral - equipment, inventory, accounts, general intangibles -
**control is not an available method**. Filing is the method, and § 9-322's
first-to-file-or-perfect rule governs. "Filing a UCC-1 is no longer enough" is
false for the collateral most security interests cover; for that collateral
filing is exactly and only what is required.

So the accurate statement is: *control beats filing where control is
available.* Stated without that qualifier it invites someone to skip a filing
that is the sole route to perfection.

## The distinction that matters most here

Article 9 control is a **secured party** concept. It describes how a LENDER
perfects a security interest in a DEBTOR's collateral - by being the depositary
bank, by a control agreement with it, or by becoming its customer.

The principal's question was how to gain control over *their own* financial
instruments, assets and equitable interest. That is a different posture
entirely. You do not perfect a security interest in your own property; you
already own it. Article 9 answers "whose claim wins when two creditors want the
same collateral", not "how do I hold my own assets more securely".

If the real question is protecting an equitable interest the principal holds,
the doctrines that reach it are the ones in
`OPEN_QUESTION_equity_jurisdiction.md` - constructive trust, equitable lien,
subrogation - not Article 9 perfection. If the real question is a security
interest the principal has GIVEN or TAKEN, then Article 9 is the right frame
and the sections above are where to start.

## Note on the source

"Filing a UCC-1" content circulates heavily in redemption / straw-man material,
where people file financing statements against themselves or against government
officers. That use has failed consistently and draws sanctions. The doctrine
above is ordinary commercial law and unrelated to it; the overlap is in
vocabulary only. Judge this reel on the two errors identified, not on the
company its subject keeps.

## What to ingest

Texas enacted Article 9 = **Tex. Bus. & Com. Code, Chapter 9**. State statute,
public domain. `statutes.capitol.texas.gov` serves an Angular app rather than
the text, so automated retrieval failed on 2026-08-29 - the PDF and HTML URLs
both return the same 250KB application shell. Download it manually from that
site and `tools/ingest_pdf.py` will take it. Until then this file is doctrine
without its statute, which is the weakest kind of reference material this
corpus holds.
