#!/usr/bin/env python3
"""A dispute letter cites what it asserts, carries no identifier, and never
sends.

    python3 tools/check_dispute_letters.py

Fixtures are synthetic; the shelf is the committed corpus under
reference/legal_agent.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main():
    import core.dispute_letters as dl
    from agents.legal_agent.legal_agent import LegalAgent
    ag = LegalAgent.__new__(LegalAgent)
    ag.agent_id = "legal_agent"
    ag._refdocs = None
    ag.log = lambda *a, **k: None
    ag.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
    own = ag._resolve_authority

    print("every paragraph that asserts a duty names its authority")
    for kind, (_, paras, _) in dl.KINDS.items():
        asserting = [p for p in paras if p[0] != "subject"
                     and any(w in p[1].lower() for w in ("must", "cease", "do not", "under the"))]
        ck(f"{kind}: asserting paragraphs cite", all(p[2] for p in asserting),
           str([p[0] for p in asserting if not p[2]]))

    print("all four kinds draft complete against the shelf")
    facts = {"fcra_dispute": {"furnisher": "Example Furnisher LLC", "account_last4": "1234",
                              "date_first_reported": "2026-03-01", "what_is_wrong": "charged off",
                              "what_is_true": "paid as agreed"},
             "fdcpa_validation": {"mailing_address": "[mailing address]"},
             "combined": {"furnisher": "Example Collector Inc", "account_last4": "1234",
                          "mailing_address": "[mailing address]"},
             "portal_short": {}}
    docs = {}
    for kind, f in facts.items():
        r = dl.draft(kind, f, own)
        docs[kind] = r
        ck(f"{kind} complete", r["complete"], str(r["refused"]))
        ck(f"{kind} citations live after the letter, not in it",
           "AUTHORITIES" in r["letter"] and "[sha256" not in r["letter"].split("---")[0])
        ck(f"{kind} does not send", r["sends"] is False)
    ck("the VA benefit bullet rests on 38 U.S.C. 5301",
       any(e["citation"] == "38 U.S.C. 5301" for e in docs["fdcpa_validation"]["schedule"]))
    ck("the validation demand rests on Regulation F § 1006.34",
       any(e["citation"] == "12 CFR 1006.34" for e in docs["fdcpa_validation"]["schedule"]))

    print("determinism")
    r2 = dl.draft("fcra_dispute", dict(reversed(list(facts["fcra_dispute"].items()))), own)
    ck("same facts in another order -> same bytes", r2["letter"] == docs["fcra_dispute"]["letter"])
    def borrowed(c):
        e = own(c)
        if e:
            e = dict(e); e["held_by"] = "legal_agent"
        return e
    r3 = dl.draft("fcra_dispute", facts["fcra_dispute"], borrowed)
    ck("a borrowing agent -> same bytes, provenance outside", r3["letter"] == docs["fcra_dispute"]["letter"]
       and r3["resolved_from"] == ["legal_agent"])

    print("no identifier goes into a letter")
    def refuses(kind, f, name):
        try:
            dl.draft(kind, f, own)
            ck(name, False)
        except dl.Refused as exc:
            ck(name, True, str(exc)[:70])
    refuses("fcra_dispute", dict(facts["fcra_dispute"], account_last4="12345678"),
            "a full account number in the last-four field is refused")
    refuses("fdcpa_validation", {"mailing_address": "SSN 123-45-6789"},
            "an SSN anywhere in the facts is refused")
    refuses("fcra_dispute", dict(facts["fcra_dispute"], what_is_wrong="card 4111 1111 1111 1111"),
            "a card number in prose is refused")
    refuses("fcra_dispute", dict(facts["fcra_dispute"], enclosures="DD-214, ID"),
            "an unredacted DD-214 enclosure is refused")
    r = dl.draft("fcra_dispute", dict(facts["fcra_dispute"], enclosures="redacted DD-214, ID"), own)
    ck("a redacted DD-214 enclosure ships", r["complete"] and "redacted DD-214" in r["letter"])
    r = dl.draft("fcra_dispute", dict(facts["fcra_dispute"], enclosures="DD-214, ID",
                                      dd214_is_the_misused_document=True), own)
    ck("an unredacted DD-214 ships only when it is the misused document, and the guidance says redact",
       r["complete"] and "redact the SSN" in r["letter"])

    print("a paragraph whose authority is unshelved is refused by name")
    def missing(c):
        return None if "1006.34" in c else own(c)
    r = dl.draft("fdcpa_validation", facts["fdcpa_validation"], missing)
    ck("paragraphs resting on the missing section are refused, the rest ship",
       not r["complete"] and {x["paragraph"] for x in r["refused"]} == {"dispute", "validation"}
       and "cease" in r["paragraphs_shipped"])

    print("a plain request reaches the letter, and never invents the substance")
    from core.dispute_letters import parse_request
    full = ("Draft a dispute letter to the bureau. creditor: Example Furnisher LLC. "
            "account ending 1234. first reported: 2026-03-01. wrong: reported as "
            "charge-off, 120 days late. truth: never late, paid as agreed")
    r = parse_request(full)
    ck("a labelled value stops at the next label, not the end of the line",
       r["facts"]["furnisher"] == "Example Furnisher LLC" and not r["missing"], str(r["facts"]))
    ck("the tradeline language is his, verbatim",
       r["facts"]["what_is_wrong"] == "reported as charge-off, 120 days late")
    ck("what is true is his, verbatim", r["facts"]["what_is_true"] == "never late, paid as agreed")
    r = parse_request('the tradeline says "Charge-off - 120 days late" and it is wrong')
    ck("a quoted tradeline is read as what is wrong",
       r["facts"].get("what_is_wrong") == "Charge-off - 120 days late")
    ck("and the creditor is NOT guessed from the sentence", "furnisher" not in r["facts"])
    for text, kind in (("the collector keeps calling, I want a validation demand", "fdcpa_validation"),
                       ("they collect and report, send the dual notice", "combined"),
                       ("short version for the CFPB portal", "portal_short"),
                       ("dispute this tradeline with the credit bureau", "fcra_dispute")):
        ck(f"{kind} is chosen from plain words", parse_request(text)["kind"] == kind, text)
    r = parse_request("write a dispute letter")
    ck("a request naming no letter asks which, and drafts nothing",
       r["kind"] is None and r["missing"] == ["kind"])

    ag2 = LegalAgent.__new__(LegalAgent)
    ag2.agent_id = "legal_agent"
    ag2._refdocs = None
    ag2.log = lambda *a, **k: None
    ag2.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
    a = ag2.answer("I need a dispute letter for the credit bureau")
    ck("answer() asks for the missing facts rather than drafting",
       a["answered_as"] == "dispute_letter_needs_facts" and a["facts"]["drafted"] is False)
    ck("and names every missing field", all(w in a["text"] for w in
       ("creditor", "last four", "first reported", "tradeline", "actually true")))
    a = ag2.answer(full)
    ck("answer() drafts a complete request", a["answered_as"] == "dispute_letter"
       and "Example Furnisher LLC" in a["text"])
    ck("and says nothing was sent", "Nothing has been sent" in a["text"])

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
