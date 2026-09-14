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

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
