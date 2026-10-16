#!/usr/bin/env python3
"""SAM compliance says what the FAR says, and never pays for registration.

    python3 tools/check_sam.py
"""
import json
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
    import core.sam as S
    print("the refusal")
    for msg in ("Your SAM.gov registration is expiring. Renew now for $499.",
                "We can set up your UEI and SAM registration - service fee applies",
                "Invoice: annual SAM entity registration maintenance"):
        r = S.screen_service_offer(msg)
        ck(f"refused: {msg[:45]}", r["decision"] == "REFUSED" and r["pay"] is False)
    r = S.screen_service_offer("Your SAM registration expires in 30 days; renew at sam.gov.")
    ck("a reminder with no money in it is not refused", r["decision"] == "no_payment_requested")
    ck("unrelated invoices are not swept in",
       S.screen_service_offer("Invoice for janitorial supplies, $499")["decision"] == "not_a_sam_registration_offer")
    print("identifiers and clocks")
    v = S.validate_ids("ABC123DEF456", "1A2B3")
    ck("12-char UEI and 5-char CAGE pass format", v["uei"]["format_ok"] and v["cage"]["format_ok"])
    ck("format is labelled reference, not regulation", "not regulation" in v["basis"])
    ck("a short UEI fails", not S.validate_ids("ABC123")["uei"]["format_ok"])
    ck("renewal is 365 days from the last update", S.renewal_due("2026-01-15") == "2027-01-15")
    reg = {"status": "active", "expires": "2026-11-30"}
    ck("active at offer and award passes", S.offer_check(reg, "2026-11-01", "2026-11-20")["ok"])
    ck("lapsing before award fails (52.204-7(b)(1))", not S.offer_check(reg, "2026-11-01", "2026-12-05")["ok"])
    print("the sources are on the shelf")
    from agents.legal_agent.legal_agent import LegalAgent
    ag = LegalAgent.__new__(LegalAgent)
    ag.agent_id = "legal_agent"
    ag._refdocs = None
    ag.log = lambda *a, **k: None
    ag.SHARED_CORPORA = getattr(LegalAgent, "SHARED_CORPORA", ())
    for c in ("48 CFR 52.204-7", "48 CFR 52.204-13", "48 CFR 4.1102", "GSA-SAM-2023-05-04"):
        ck(f"{c} resolves", bool((ag._resolve_authority(c) or {}).get("text")))
    t = (ag._resolve_authority("GSA-SAM-2023-05-04") or {}).get("text") or ""
    ck("the refusal's source says registration is always free", "always free" in t)
    if fails:
        print(f"\nFAIL: {len(fails)} check(s): {fails}")
        return 1
    print("\nOK - SAM rules match the FAR, and registration is never paid for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
