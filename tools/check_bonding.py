#!/usr/bin/env python3
"""Bonding rules come from the held FAR, and say what the FAR says.

    python3 tools/check_bonding.py

Runs against the committed corpus (reference/legal_agent). Thresholds must be
READ (FAR 2.101, 28.102-1), never recalled; a solicitation is classified by
its substance (K-Con, 908 F.3d 719); the FAR 28.106-5 consent-of-surety gate
fires on the right triggers.
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
    import core.bonding as B
    from agents.legal_agent.legal_agent import LegalAgent
    ag = LegalAgent.__new__(LegalAgent)
    ag.agent_id = "legal_agent"
    ag._refdocs = None
    ag.log = lambda *a, **k: None
    ag.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
    res = ag._resolve_authority

    print("thresholds are read from the corpus")
    th = B.thresholds(res)
    ck("SAT read from FAR 2.101", th["sat"] is not None and th["basis"]["sat"] == "FAR 2.101", str(th))
    ck("Bonds-statute thresholds read from FAR 28.102-1",
       th["miller_full"] is not None and th["miller_alt_floor"] is not None)
    blind = B.thresholds(lambda c: None)
    ck("an unreadable corpus gives unknown, not a recalled number",
       blind["sat"] is None and blind["miller_full"] is None)

    print("classification by substance")
    c = B.classify_solicitation("Facility services", "commercial services",
                                "Install pre-engineered metal buildings on a concrete slab at the site")
    ck("a 'services' label with building installation scope is mixed, with the K-Con finding",
       c["classification"] == "mixed" and "K-Con" in (c.get("label_vs_substance") or ""))
    ck("janitorial services are not construction",
       B.classify_solicitation("Janitorial services", "services",
                               "Daily cleaning of offices")["classification"] == "non_construction")

    print("which bonds")
    r = B.bond_requirements(res, "construction", value=th["miller_full"] + 1)
    ck("construction over the Bonds-statute threshold requires performance and payment bonds",
       any("performance and payment" in x["bond"] for x in r["required"]))
    r = B.bond_requirements(res, "non_construction", value=2_000_000)
    ck("non-construction with no 28.103-2 situation: no bond basis",
       not r["permitted"] and any("shall not" in p["rule"] for p in r["prohibited"]))
    r = B.bond_requirements(res, "non_construction", value=2_000_000,
                            situations=["progress_payments_before_delivery"])
    ck("a named 28.103-2 situation permits SF 1418, SF 1416 and an annual bond",
       {f for x in r["permitted"] for f in x.get("forms", [])} >= {"SF 1418", "SF 1416", "SF 35"})
    r = B.bond_requirements(res, "non_construction", value=th["sat"] - 1,
                            situations=["progress_payments_before_delivery"])
    ck("at or under the SAT, no non-construction performance bond",
       not any(x.get("bond") == "performance bond" for x in r["permitted"]))
    r = B.bond_requirements(res, "non_construction", value=500_000, gsa=True,
                            building_service=True, abilityone=True)
    ck("GSA building services to AbilityOne: prohibited, cited as agency guidance",
       any("AbilityOne" in p["rule"] and "guidance" in p.get("standing", "") for p in r["prohibited"]))

    print("post-award bond terms")
    f = B.check_bond_terms("The Government reserves the right to require a performance bond "
                           "at any time after award.")["flags"]
    ck("a reserved post-award bond right is flagged as unsupported, not as a violation",
       f and f[0]["finding"] == "unsupported")

    print("bond forms come from the FAR's own table")
    fm = {f["form"] for f in B.forms(res)["forms"]}
    ck("28.106-1 lists all 15, incl. SF 1414/1415/1416/1418 and OF 91",
       len(fm) == 15 and {"SF 1414", "SF 1415", "SF 1416", "SF 1418", "OF 91", "SF 24", "SF 28"} <= fm,
       str(sorted(fm)))

    print("consent of surety (FAR 28.106-5)")
    g = B.modification_gate(1_000_000, 1_060_000)
    ck("a $60,000 increase with no scope change needs SF 1414", g["consent_required"])
    g = B.modification_gate(1_000_000, 1_040_000)
    ck("a $40,000 / 4% change does not", not g["consent_required"])
    g = B.modification_gate(100_000, 130_000)
    ck("a 30% change on a small contract does", g["consent_required"])
    ck("scope expansion does", B.modification_gate(1_000_000, scope_expands=True)["consent_required"])
    ck("a 28.204 alternative security needs none",
       not B.modification_gate(1_000_000, 2_000_000, alternative_security=True)["consent_required"])

    print("accounting: the money side, across the portfolio")
    import json
    from agents.accounting_agent.accounting_agent import AccountingAgent
    ac = AccountingAgent.__new__(AccountingAgent)
    ac.agent_id = "accounting_agent"
    ac.log = lambda *a, **k: None
    store = {}
    ac._jget = lambda k, d: json.loads(store[k]) if k in store else d
    ac.store_own_memory = lambda k, v, pin=False: store.__setitem__(k, v) or {"success": True}
    ac._legal_section = lambda c: res(c)
    ac.record_bond({"bond_type": "performance", "contract": "C1", "penal_sum": 400000,
                    "premium": 8000, "indemnitors": ["owner"]})
    ac.record_bond({"bond_type": "annual_performance", "penal_sum": 1000000, "indemnitors": ["owner"],
                    "covered_contracts": [{"contract": "C2", "penal_sum": 600000},
                                          {"contract": "C3", "penal_sum": 380000}]})
    pf = ac.bond_portfolio()
    ck("indemnity exposure sums every bond the owner signed for",
       pf["indemnity_exposure"].get("owner") == 1400000.0, str(pf["indemnity_exposure"]))
    ck("an annual bond 98% obligated needs an additional bond (FAR 28.104(b))",
       pf["annual_bonds"][0]["additional_bond_needed"], str(pf["annual_bonds"][0]["used_pct"]))
    ck("an unrecorded premium is counted as unrecorded, not as zero", pf["premiums_unrecorded"] == 1)
    e = ac.sba_guarantee_eligibility({"contract_amount": 2000000, "veteran_owned": True})
    ck("SBA limits read from 13 CFR 115.10; veteran-owned gets 90% (115.31)",
       e.get("eligible") is True and e.get("guarantee_pct") == 90 and e["limits"]["base"], str(e.get("limits")))
    e = ac.sba_guarantee_eligibility({"contract_amount": 12000000})
    ck("between the base and certified limits needs a CO certification",
       e.get("eligible") == "with_co_certification")
    ck("the guarantee fee is unknown, never a guessed rate", "unknown" in e.get("guarantee_fee", ""))

    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - bonding rules are read from the FAR and say what it says.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
