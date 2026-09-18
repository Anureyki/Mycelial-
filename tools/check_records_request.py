#!/usr/bin/env python3
"""The lane routes a records claim to a custodian and never classifies it;
"no such record" requires a name and a date.

    python3 tools/check_records_request.py
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


def main():
    from core import records_request as R
    R.STORE = tempfile.mkdtemp()

    print("it classifies no claim - there is no verdict vocabulary in it")
    src = open(os.path.join(ROOT, "core", "records_request.py"), encoding="utf-8").read()
    body = src.split('"""', 2)[2]          # past the module docstring
    for word in ("frivolous", "sovereign", "debunk", "pseudo", "nonsense", "baseless"):
        ck(f"the code does not judge a claim as {word!r}", word not in body.lower())
    ck("and there is no verdict field at all",
       "verdict" not in body and "plausible" not in body)

    print("a claim about a record routes to whoever would hold it")
    for subject, expect in (("security_or_cusip", "cusip_lookup"),
                            ("bond_or_surety", "court_clerk"),
                            ("disclosure_accounting", "disclosure_accounting"),
                            ("consumer_file", "cra_file_disclosure"),
                            ("account_accounting", "ucc_accounting")):
        got = [m["mechanism"] for m in R.route(subject)["mechanisms"]]
        ck(f"{subject} reaches {expect}", expect in got, str(got))
    ck("an unknown subject is refused rather than guessed at",
       _refuses(lambda: R.route("vibes"), R))
    ck("every mechanism states its custodian and what it compels",
       all(m.get("custodian") and m.get("compels") for m in R.MECHANISMS.values()))
    ck("a statutory clock carries its basis",
       all(m.get("clock_basis") for m in R.MECHANISMS.values() if m.get("clock_days")))

    print("a request has to be answerable")
    ck("a mechanism that does not reach the subject is refused",
       _refuses(lambda: R.open_request("r", "consumer_file", "court_clerk", "clerk",
                                       "everything"), R))
    ck("'everything' is not a request",
       _refuses(lambda: R.open_request("r", "court_file", "court_clerk", "clerk", "  "), R))
    req = R.open_request("r1", "agency_record", "foia", "Treasury",
                         "any bond or undertaking referencing docket 25-cv-1", sent_on="2026-09-18")
    ck("a FOIA clock is counted in BUSINESS days, not calendar",
       req["response_due"] == "2026-10-16", req["response_due"])
    ck("an unsent request is not_checked, which is not not_found",
       R.open_request("r2", "court_file", "court_clerk", "clerk", "register of actions"
                      )["state"] == "not_checked")
    ck("it sends nothing", req["sends"] is False)

    print("'no such record' needs a custodian and a date")
    ck("not_found with no date is refused",
       _refuses(lambda: R.record_response(dict(req), "not_found",
                                          custodian_said="none"), R))
    ck("not_found with no words from the custodian is refused",
       _refuses(lambda: R.record_response(dict(req), "not_found", on="2026-10-01"), R))
    r2 = R.record_response(dict(req), "not_found", on="2026-10-01",
                           custodian_said="No responsive records were located.")
    ck("with both, it is a finding sourced to them",
       r2["response"]["state"] == "not_found"
       and "sourced to them" in r2["response"]["means"])
    ck("an unanswered request stays unknown, never not_found",
       R.record_response(dict(req), "unknown", on="2026-10-01")["response"]["state"] == "unknown")
    ck("a produced document makes it cited rather than stated",
       R.record_response(dict(req), "not_read", on="2026-10-01",
                         doc_id="foia-1")["response"]["evidence_state"] == "cited")
    ck("a bad absence state is refused",
       _refuses(lambda: R.record_response(dict(req), "nonexistent"), R))

    print("the register")
    R.save(req)
    s = R.summary(R.load_all(), today="2026-11-01")
    ck("an unanswered request past its clock is overdue", "r1" in s["overdue"])
    ck("and the rule is stated on the register",
       "Only a custodian can say a record is not there" in s["rule"])
    p = R._path("r1")
    ck("a request file is written 0600", (os.stat(p).st_mode & 0o777) == 0o600)
    ck("a request carrying an identifier is refused before it is written",
       _refuses(lambda: R.save(R.open_request("r9", "tax_record", "irs_transcript", "IRS",
                                              "transcript for SSN 123-45-6789")), R))

    print()
    if fails:
        print(f"FAIL: {len(fails)} check(s): " + "; ".join(fails[:6]))
        return 1
    print("ALL PASS")
    return 0


def _refuses(fn, R):
    try:
        fn()
        return False
    except R.Refused:
        return True


if __name__ == "__main__":
    sys.exit(main())
