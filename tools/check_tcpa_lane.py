#!/usr/bin/env python3
"""The TCPA lane counts calls, not headlines: Duguid gates the ATDS route,
each clock runs per call, and the forfeiture figure is not recoverable.

    python3 tools/check_tcpa_lane.py

Synthetic log; temp store.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def base(T):
    c = T.open_claim("t1", "Consumer v. Caller", my_number_is_cell=True,
                     registry_listed_on="2026-01-01", residential_subscriber=True,
                     stop_request_on="2026-06-01")
    T.set_consent(c, "none")
    T.set_received(c, True)
    return c


def main():
    from core import tcpa_lane as T
    T.STORE = tempfile.mkdtemp()

    print("Duguid gates the ATDS route and only that route")
    ck("a stored/targeted list is NOT an ATDS",
       T.atds_read("dialed from an uploaded CRM list")["state"] == "not_atds")
    ck("a random or sequential generator is asserted, not proved",
       T.atds_read("random number generator")["state"] == "atds_asserted")
    ck("no description -> unknown, and it says the prerecorded route does not need one",
       T.atds_read("")["state"] == "unknown"
       and "prerecorded route does not" in T.atds_read("")["why"])
    c = base(T)
    T.add_call(c, "2026-07-10", "voice_call", system_description="uploaded list",
               is_solicitation=True)
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    ck("a non-ATDS live call is refused on (b)",
       r["theories"]["b_automated_to_cell"]["calls_counted"] == 0)
    c = base(T)
    T.add_call(c, "2026-07-10", "prerecorded_voice", system_description="uploaded list",
               is_solicitation=True)
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    ck("the SAME system with a prerecorded voice counts - the routes are independent",
       r["theories"]["b_automated_to_cell"]["calls_counted"] == 1)

    print("each theory has its own gate")
    c = base(T)
    T.add_call(c, "2026-01-15", "voice_call", is_solicitation=True)     # within 31 days
    T.add_call(c, "2026-07-10", "voice_call", is_solicitation=True)     # after stop request
    r = T.assess(c, today="2026-09-16")
    dnc = r["theories"]["c_national_dnc"]
    ck("a call within 31 days of the registry listing is refused",
       any("31 days" in w for x in dnc["refused"] for w in x["why_refused"]))
    internal = r["theories"]["d_internal_dnc"]
    ck("a call before the stop request is refused on internal DNC",
       any("not after the stop request" in w for x in internal["refused"]
           for w in x["why_refused"]))
    c = base(T)
    T.add_call(c, "2026-07-10", "voice_call", is_solicitation=False)
    r = T.assess(c, today="2026-09-16", theory="c_national_dnc")
    ck("a non-solicitation is refused on the DNC theories",
       r["theories"]["c_national_dnc"]["calls_counted"] == 0)

    print("the clock runs per call")
    s = T.limitations("2021-02-01", today="2026-09-16")
    ck("a 2021 call is time-barred at 4 years", s["state"] == "expired" and s["deadline"] == "2025-02-01")
    c = base(T)
    T.add_call(c, "2021-02-01", "prerecorded_voice", is_solicitation=True)
    T.add_call(c, "2026-07-10", "prerecorded_voice", is_solicitation=True)
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    b = r["theories"]["b_automated_to_cell"]
    ck("an old call is dropped while the rest survive",
       b["calls_counted"] == 1 and any("time-barred" in w for x in b["refused"]
                                       for w in x["why_refused"]))

    print("consent decides by date, not by label")
    c = base(T)
    T.add_call(c, "2026-07-10", "prerecorded_voice", is_solicitation=True)
    T.set_consent(c, "given")
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    ck("consent given and not revoked refuses every call",
       r["theories"]["b_automated_to_cell"]["calls_counted"] == 0 and not r["cite_ready"])
    c = base(T)
    T.add_call(c, "2026-05-01", "prerecorded_voice", is_solicitation=True)
    T.add_call(c, "2026-07-10", "prerecorded_voice", is_solicitation=True)
    T.set_consent(c, "revoked", revoked_on="2026-06-01")
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    ck("after revocation counts, before it does not",
       r["theories"]["b_automated_to_cell"]["calls_counted"] == 1)
    c = base(T)
    T.add_call(c, "2026-07-10", "prerecorded_voice", is_solicitation=True)
    T.set_consent(c, "unknown")
    r = T.assess(c, today="2026-09-16")
    ck("unknown consent counts the call but refuses cite_ready",
       r["theories"]["b_automated_to_cell"]["calls_counted"] == 1 and not r["cite_ready"]
       and any("consent is unknown" in g for g in r["gaps"]))

    print("the money is a sum, and the forfeiture is not recoverable")
    c = base(T)
    for d in ("2026-07-10", "2026-07-11", "2026-07-12"):
        T.add_call(c, d, "prerecorded_voice", is_solicitation=True)
    r = T.assess(c, today="2026-09-16", theory="b_automated_to_cell")
    b = r["theories"]["b_automated_to_cell"]
    ck("three counted calls -> $1,500.00, not a headline",
       b["statutory_damages"] == "$1,500.00" and b["treble_ceiling_if_willful"] == "$4,500.00")
    ck("treble is labelled a ceiling and discretionary",
       "up to" in b["damages_note"] and "discretion" in b["damages_note"])
    ck("the 43,792 forfeiture is named as NOT recoverable privately",
       "43,792" in b["not_recoverable"] and "does not collect" in b["not_recoverable"])

    print("refusals")
    for bad, why in ((lambda: T.open_claim("", "x"), "no claim_id"),
                     (lambda: T.add_call(base(T), "2026-07-10", "smoke_signal"), "unknown channel"),
                     (lambda: T.add_call(base(T), "July 10", "voice_call"), "bad date"),
                     (lambda: T.set_consent(base(T), "maybe"), "unknown consent state")):
        try:
            bad()
            ck(f"refuses: {why}", False)
        except T.Refused:
            ck(f"refuses: {why}", True)

    print("the store")
    c = base(T)
    T.add_call(c, "2026-07-10", "prerecorded_voice", is_solicitation=True)
    p = T.save(T.assess(c, today="2026-09-16"))
    ck("a claim file is written 0600", (os.stat(p).st_mode & 0o777) == 0o600)
    try:
        bad = base(T)
        T.add_call(bad, "2026-07-10", "voice_call", note="called about SSN 123-45-6789")
        T.save(bad)
        ck("a call note carrying an identifier is refused before it is written", False)
    except T.Refused as exc:
        ck("a call note carrying an identifier is refused before it is written",
           "9-digit" in str(exc) or "ssn" in str(exc).lower())

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
