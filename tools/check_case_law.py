#!/usr/bin/env python3
"""A decision on the shelf is reachable by its caption, and the review reads
what the court wrote.

    python3 tools/check_case_law.py

Four district-court decisions were shelved on 2026-09-14 through Legal's
acquire_opinion, and three of them were refused first for reasons that had
nothing to do with the court: a filter that wanted the word "opinion" when
Florida titles rulings "Order"; a subject window sized for a statute's first
line when a ruling's subject is on page two; a throttled API reported as an
empty archive; an untitled docket entry skipped because its name was blank;
and a ruling refused as a party's filing because it names the motion it
decides. Each of those is a shape, not an instance, and this file holds the
shape so the next decision does not pay for it again.

No network. The shelved fixtures are the corpus files committed under
reference/legal_agent; the ruling/filing tests are inline synthetic text.
"""
import glob
import json
import os
import re
import sys

# Anything this gate causes to be emitted is a test fixture, never a
# training example. See tools/check_eval.py on why every gate says so.
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    from agents.legal_agent.legal_agent import LegalAgent
    reads = LegalAgent._reads_as_ruling

    print("a document names itself, and the first name wins")
    ruling = ("Case 1:25-cv-03044-BAH Document 17 Filed 08/12/26 Page 1 of 11 IN THE UNITED "
              "STATES DISTRICT COURT * PLAINTIFF, * v. * DEFENDANT. * MEMORANDUM OPINION "
              "Plaintiff brought suit. Pending before the Court is the motion to dismiss "
              "(the \"Motion\"). For the reasons below, the Motion is DENIED.")
    ck("memorandum opinion that names the motion it decides reads as a ruling", reads(ruling))
    complaint = ("Case 1:25-cv-03044 Document 1 Filed 09/15/25 Page 1 of 9 IN THE UNITED STATES "
                 "DISTRICT COURT * PLAINTIFF, * v. * DEFENDANT. * COMPLAINT AND JURY DEMAND "
                 "Plaintiff alleges the defendant violated an order of this court.")
    ck("complaint that mentions an order reads as a party's filing", not reads(complaint))
    brief = ("MEMORANDUM OF LAW IN SUPPORT OF MOTION TO DISMISS. The Court's prior ORDER "
             "held that ...")
    ck("memorandum of law reads as a party's filing", not reads(brief))
    ck("text with no self-name is not a ruling", not reads("Exhibit A. Account statement. Balance due."))

    print("the subject window reaches past a caption")
    from core.authority_acquisition import check_subject
    body = "CAPTION " * 120 + " This is an action under the Fair Debt Collection Practices Act."
    ck("700-character window misses a subject stated after a long caption",
       not check_subject(body, "debt collection")["verified"])
    ck("8000-character window reaches it",
       check_subject(body, "debt collection", window=8000)["verified"])

    print("a caption names several documents; only the one on subject is shelved")
    cert_grant = ("Petition for writ of certiorari to the United States Court of Appeals "
                  "for the Fourth Circuit granted.")
    ck("a cert-grant order fails a subject check for the holding",
       not check_subject(cert_grant, "debt collector owed another", window=8000)["verified"])
    merits = ("HENSON v. SANTANDER CONSUMER USA INC. " + "x " * 60 +
              "The question is whether a company is a debt collector when it purchases a "
              "debt and seeks to collect it for itself, rather than one owed another.")
    ck("the merits opinion passes it",
       check_subject(merits, "debt collector owed another", window=8000)["verified"])

    print("every shelved decision is reachable by its own caption")
    shelf = os.path.join(ROOT, "reference", "legal_agent")
    cases = []
    for f in sorted(glob.glob(os.path.join(shelf, "*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        if d.get("authority_class") == "case_law" and d.get("sections"):
            cases.append(d)
    if not cases:
        print("  SKIP  no case_law works on Legal's shelf - nothing to check")
    else:
        agent = LegalAgent.__new__(LegalAgent)
        agent.agent_id = "legal_agent"
        agent._refdocs = None
        agent.log = lambda *a, **k: None
        agent.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
        for d in cases:
            caption = re.split(r"\s*\(", d["title"], maxsplit=1)[0].strip()
            hits = agent._lookup_reference_raw(caption)
            ck(f"{caption[:50]} resolves by caption", bool(hits),
               f"{len(hits)} hit(s), {len(d['sections'])} pages, class {d['authority_class']}")
            ck(f"{caption[:50]} carries claim_layer, not None",
               d.get("claim_layer") is not None, str(d.get("claim_layer")))

        print("the review is extractive and names the corpus gap")
        for d in cases:
            caption = re.split(r"\s*\(", d["title"], maxsplit=1)[0].strip()
            r = LegalAgent.review_consumer_protections(agent, {"case": caption})
            ok = "protections_engaged" in r and "holdings" in r and "corpus_coverage" in r
            ck(f"{caption[:50]} review returns the three findings", ok, r.get("verdict", ""))
            if not ok:
                continue
            text = "\n".join(s.get("text") or "" for s in d["sections"])
            quoted = all(h["text"][:60] in re.sub(r"\s+", " ", text) or
                         h["text"][:60] in re.sub(r"\s+", " ", re.sub(
                             r"Case\s+\S+\s+Document\s+\d+\s+Filed\s+\S+\s+Page\s+\d+\s+of\s+\d+(?:\s+PageID:?\s*\d+)?",
                             " ", text))
                         for h in r["holdings"])
            ck(f"{caption[:50]} every holding is the court's own sentence", quoted,
               f"{len(r['holdings'])} sentence(s)")
            cov = r["corpus_coverage"]
            ck(f"{caption[:50]} coverage partitions applied sections",
               set(cov["on_this_shelf"]).isdisjoint(cov["not_on_this_shelf"]),
               f"{len(cov['on_this_shelf'])} on shelf, {len(cov['not_on_this_shelf'])} missing")
            ck(f"{caption[:50]} does not score who it favours",
               "who_it_favours" not in r and "outcome" not in r)

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
