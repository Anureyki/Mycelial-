#!/usr/bin/env python3
"""No clause ships without a citation that opens; two agents draft alike;
a rule the shelf contradicts is reported and not applied.

    python3 tools/check_contract_engine.py

Fixtures are synthetic. The shelf is the committed corpus under
reference/legal_agent; nothing here fetches.
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


FACTS = {"buyer_name": "Test Buyer", "seller_name": "Test Seller",
         "vehicle_description": "2019 Example Sedan", "vin": "1EXAMPLEVIN000000",
         "odometer": "41,200", "price_usd": "$9,500.00",
         "seller_affirmations": "one owner", "seller_is_merchant": True,
         "delivery_date": "2026-10-01"}


def layers(debt="none", debt_evidence="stated_by_principal"):
    return {"holder": {"party": "Test Buyer", "evidence": "stated_by_principal"},
            "servicer": {"party": "none", "evidence": "stated_by_principal"},
            "debt_holder": {"party": debt, "evidence": debt_evidence},
            "benefit": {"party": "Test Buyer", "evidence": "stated_by_principal"}}


def main():
    from core.contract_engine import draft, Refused, FAMILIES, INSTRUMENT_CLAUSES
    from core.instrument_rules import (classify_instrument, Unclassifiable,
                                       discharge_by_instrument, contradiction,
                                       doctrine_report, inversion_pair)
    from agents.legal_agent.legal_agent import LegalAgent

    def agent(aid="legal_agent"):
        a = LegalAgent.__new__(LegalAgent)
        a.agent_id = aid
        a._refdocs = None
        a.log = lambda *x, **k: None
        a.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
        return a

    own = agent()._resolve_authority

    print("every clause in every family names at least one authority")
    for fam, clauses in FAMILIES.items():
        ck(f"{fam}: no clause without a citation", all(c["authority"] for c in clauses))
    for kind, clauses in INSTRUMENT_CLAUSES.items():
        ck(f"instrument {kind}: no clause without a citation", all(c["authority"] for c in clauses))

    print("the car purchase drafts complete against the shelf")
    r = draft("car_purchase", FACTS, layers(), own, instrument={"kind": "check"})
    ck("complete", r["complete"], str(r["refused"]))
    doc = r["document"]
    ck("names the four layers", all(k in doc for k in
       ("Title holder on delivery", "Servicer of any obligation", "Carrier of any debt", "Beneficiary")))
    ck("warranty cites UCC Article 2 (§ 2.313)",
       any(e["citation"].endswith("§ 2.313") for e in r["schedule"]))
    ck("discharge cites § 3.311", any(e["citation"].endswith("§ 3.311") for e in r["schedule"]))
    ck("title cites the Certificate of Title Act",
       any("501.071" in e["citation"] for e in r["schedule"]))
    ck("no argument in the body: citations live in Schedule A only",
       "§" not in doc.split("SCHEDULE A")[0])
    ck("every schedule entry hashes real text", all(len(e["text_sha256"]) == 64 for e in r["schedule"]))

    print("determinism")
    r2 = draft("car_purchase", dict(reversed(list(FACTS.items()))), layers(), own,
               instrument={"kind": "check"})
    ck("same facts in another order -> same bytes", r2["document"] == doc)

    def borrowed(cite):
        e = own(cite)
        if e is None:
            return None
        e = dict(e)
        e["held_by"] = "legal_agent"          # what ask_peer_corpus stamps
        e["knowledge_class"] = "secondary"
        return e
    r3 = draft("car_purchase", FACTS, layers(), borrowed, instrument={"kind": "check"})
    ck("a borrowing agent produces the same bytes", r3["document"] == doc)
    ck("and reports where it resolved from, outside the document",
       r3["resolved_from"] == ["legal_agent"] and "legal_agent" not in doc)

    print("the refusal gate")
    r = draft("car_purchase", FACTS, layers(), own, instrument={"kind": "wire"})
    ck("an instrument whose authority is not on the shelf is refused by name",
       not r["complete"] and all(x["reason"] == "authority_not_in_corpus" for x in r["refused"])
       and any("4A" in x["detail"] for x in r["refused"]))
    f = dict(FACTS, seller_claims_to_carry_debt=True, financed_usd="$5,000")
    r = draft("car_purchase", f, layers(debt="Test Buyer"), own, instrument={"kind": "cashiers_check"})
    ck("seller claims the debt, map says buyer carries it -> financing clause refused",
       any(x["reason"] == "layer_conflict" for x in r["refused"]))
    try:
        draft("car_purchase", FACTS, layers(debt_evidence="unknown"), own, instrument={"kind": "check"})
        ck("an unknown layer blocks the whole draft", False)
    except Refused as exc:
        ck("an unknown layer blocks the whole draft", "unknown blocks" in str(exc))
    try:
        draft("car_purchase", FACTS, layers(), own, instrument={"kind": "voucher", "value": 400000})
        ck("a value with no maturity does not ship", False)
    except Unclassifiable:
        ck("a value with no maturity does not ship", True)
    r = draft("house_purchase", {"buyer_name": "B", "seller_name": "S",
                                 "property_address": "x", "legal_description": "y"}, layers(), own)
    ck("a family whose authority is unshelved refuses every clause, and names them",
       not r["complete"] and r["clauses_shipped"] == [] and
       all("Tex. Prop. Code" in x["detail"] for x in r["refused"]))

    print("instrument doctrine, tested against the shelf")
    rep = {x["id"]: x for x in doctrine_report(lambda c: [e for e in [own(c)] if e])}
    ck("'the funds don't have to be good' is CONTESTED by § 3.311(a)(3) and not applied",
       rep["tender_is_payment"]["evidence"] == "contested" and not rep["tender_is_payment"]["applied"]
       and any("obtained payment" in q for q in rep["tender_is_payment"]["corpus_says"]))
    ck("'the discharge stands' is cited, with the 90-day exception quoted",
       rep["discharge_stands"]["evidence"] == "cited" and
       any("90 days" in q for q in rep["discharge_stands"]["corpus_says"]))
    ck("'every download is a violation' is contested: the text says 'sent by mail'",
       rep["ssn_transmission"]["evidence"] == "contested" and
       any("SENT BY MAIL" in q.upper() for q in rep["ssn_transmission"]["corpus_says"]))
    ck("'cashing is conversion' is unsupported by the corpus and not applied",
       rep["cashing_is_conversion"]["evidence"] == "unsupported_by_corpus"
       and not rep["cashing_is_conversion"]["applied"])
    ck("a rule the shelf is silent on is held as the principal's, and applied",
       rep["flag_is_not_fact"]["evidence"] == "stated_by_principal" and rep["flag_is_not_fact"]["applied"])
    ck("SGLI is cited once 38 U.S.C. 1970 is on the shelf",
       rep["sgli_contingent"]["evidence"] in ("cited", "stated_by_principal"),
       rep["sgli_contingent"]["evidence"])

    print("the verbs")
    ck("SGLI classifies contingent, not spendable now",
       classify_instrument("sgli", value=400000)["spendable_now"] is False)
    ck("an award letter is a present credit", classify_instrument("award_letter")["maturity"] == "present")
    d = discharge_by_instrument({"good_faith_tender": True, "full_satisfaction_statement_conspicuous": True,
                                 "claim_unliquidated_or_disputed": True, "claimant_obtained_payment": False})
    ck("a dishonoured tender is not a discharge", d["verdict"] == "not_discharged" and "3.311(a)(3)" in d["why"])
    d = discharge_by_instrument({"good_faith_tender": True, "full_satisfaction_statement_conspicuous": True,
                                 "claim_unliquidated_or_disputed": True, "claimant_obtained_payment": True})
    ck("paid, conspicuous, disputed, not repaid -> discharged", d["verdict"] == "discharged")
    d = discharge_by_instrument({"good_faith_tender": True})
    ck("elements nobody answered -> not_established, never assumed", d["verdict"] == "not_established")
    ck("'bounced' against a statement showing paid is a contradiction",
       contradiction("it bounced", "statement: check paid")["contradiction"] is True)
    ck("a contradiction with one side missing is refused",
       contradiction("it bounced", None)["contradiction"] is None)
    try:
        inversion_pair("SSN 123-45-6789 owes", "x", "y", "z", path="/dev/null")
        ck("a pair carrying an identifier is refused", False)
    except ValueError:
        ck("a pair carrying an identifier is refused", True)

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
