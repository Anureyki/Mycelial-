#!/usr/bin/env python3
"""The registry must not mistake a failed check for a clean one.

    python3 tools/check_asset_registry.py

PASS / SKIP / FAIL ARE THREE OUTCOMES, NOT TWO.

    PASS  the check ran and the property held
    SKIP  the check needs private personal fixtures that are deliberately
          absent here. Counted and printed, never folded into PASS.
    FAIL  a required fixture exists and is invalid, or a property broke

A suite that goes green because sensitive fixtures are missing is reporting on
its own absence. So the skip count is printed at the end and a personal-data
test never silently becomes a generic passing one.

EVERY SENSITIVE VALUE BELOW IS SYNTHETIC. 123-45-6789 is the documented
example SSN; the file numbers and account numbers are made up. The principal's
real identifier was leaked into this repository once already, by a comment in
the scanner built to catch it - it will not be reintroduced as a test fixture.
A regression test that carries the secret it protects is the same bug wearing
a green tick.
"""
import json
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails, skips = [], []

# Synthetic only. Never the real value, in any form, ever.
SYN_SSN = "123-45-6789"
SYN_SSN_SPACED = "123 45 6789"
SYN_FILE_NO = "000000 000"
SYN_ACCOUNT = "4111111111111111"
SYN_ROUTING = "021000021"


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}   {why}")
    skips.append(name)


def refuses(fn, name, detail=""):
    """The whole point: the call must RAISE Refused, not return a status."""
    from core.asset_registry import Refused
    try:
        fn()
    except Refused as exc:
        ck(name, True, detail or str(exc)[:56])
        return
    except Exception as exc:                        # noqa: BLE001
        ck(name, False, f"raised {type(exc).__name__}, not Refused: {exc}")
        return
    ck(name, False, "it was ACCEPTED")


def main():
    import core.asset_registry as ar
    import core.identifier_scan as isc
    from core.asset_registry import record, derive, advance, block, Refused

    tmp = os.path.join(tempfile.mkdtemp(), "assets.json")

    print("\n  1. identifier refusal - synthetic values only, never the real one")
    for label, field in (
            ("a full SSN in prose", {"notes": f"SSN {SYN_SSN}"}),
            ("a spaced SSN", {"notes": f"SSN {SYN_SSN_SPACED}"}),
            ("a VA file number", {"note": f"VA FILE NO {SYN_FILE_NO}"}),
            ("a full account number in last_four", {"last_four": SYN_ACCOUNT}),
            ("a routing number by field name", {"routing_number": SYN_ROUTING}),
            ("a password", {"password": "anything"}),
            ("an MFA secret", {"mfa_secret": "anything"}),
            ("a security answer", {"security_answer": "anything"}),
            ("a long digit run in a free field", {"ref": "9876543210"}),
    ):
        refuses(lambda f=field: record("probe", kind="account",
                                       evidence="stated_by_principal",
                                       path=tmp, **f),
                f"refuses {label}")

    got = record("ok_account", kind="account", evidence="stated_by_principal",
                 path=tmp, institution="Example Bank",
                 account_type="checking", last_four="1234")
    ck("a real last_four of four digits is accepted", got["last_four"] == "1234")
    stored = json.dumps(json.load(open(tmp, encoding="utf-8")))
    ck("nothing sensitive reached the file",
       SYN_SSN not in stored and SYN_ACCOUNT not in stored
       and SYN_ROUTING not in stored,
       "refusal happens before persistence, not after")

    print("\n  2. THE REFUSAL-PATH INVARIANT: a broken control never reads clean")
    # The exact defect that let an SSN through: a field name read that the
    # scanner does not return evaluates to nothing, and nothing reads as clean.
    real = isc.scan
    for label, fake in (
            ("the scanner raises",
             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))),
            ("it returns the wrong type",
             lambda *a, **k: []),
            ("it returns None",
             lambda *a, **k: None),
            ("the contract key is missing (the original bug)",
             lambda *a, **k: {"hits": []}),
            ("findings is not a list",
             lambda *a, **k: {"findings": "nope"}),
            ("a finding is malformed",
             lambda *a, **k: {"findings": [{"kind": "x"}]}),
    ):
        isc.scan = fake
        refuses(lambda: record("contract_probe", kind="account",
                               evidence="cited", path=tmp,
                               institution="Example Bank"),
                f"refuses when {label}")
    isc.scan = real
    ck("the real scanner still permits a clean record",
       bool(record("clean_again", kind="asset", evidence="cited", path=tmp,
                   institution="Example Bank")),
       "a control that refuses everything is also broken")

    print("\n  3. evidence: derived is not verified, unknown blocks")
    from core.asset_registry import EVIDENCE, ESTABLISHED
    ck("all five evidence states are declared",
       set(EVIDENCE) == {"cited", "system_verified", "derived",
                         "stated_by_principal", "unknown"}, str(sorted(EVIDENCE)))
    ck("derived is NOT established", "derived" not in ESTABLISHED,
       "a calculated figure is not an externally confirmed one")
    ck("stated_by_principal is not established either",
       "stated_by_principal" not in ESTABLISHED,
       "his word is a source and is not corroboration")
    refuses(lambda: derive("ok_account", "balance", 4200, [], "sum", path=tmp),
            "a derivation with no inputs is refused")
    d = derive("ok_account", "balance", 4200,
               ["stmt:2026-07", "stmt:2026-08", "stmt:2026-09"],
               "sum of three cited statements", path=tmp)
    ck("a derivation keeps its inputs",
       len(d["derived"]["balance"]["inputs"]) == 3)
    ck("and says it is not verified",
       "not_verified" in d["derived"]["balance"])
    ck("deriving never changes the record's evidence state",
       d["evidence"] != "system_verified", d["evidence"])
    u = record("unknown_thing", kind="claim", evidence="unknown", path=tmp)
    ck("unknown sets a blocking state", "UNKNOWN" in u["blocking"])

    print("\n  4. the state machine: blocked means blocked")
    from core.asset_registry import BLOCKING, LIFECYCLE, IMPLEMENTED_THROUGH
    refuses(lambda: advance("unknown_thing", "IDENTIFIED", path=tmp),
            "a blocked asset cannot advance")
    for reason in ("CONFLICT", "NON_TRANSFERABLE",
                   "PROFESSIONAL_REVIEW_REQUIRED", "AUTHORIZATION_REQUIRED"):
        block("clean_again", reason, path=tmp)
        refuses(lambda: advance("clean_again", "IDENTIFIED", path=tmp),
                f"{reason} blocks advancement")
        ar.load(tmp)["assets"]["clean_again"]["blocking"].clear()
        doc = ar.load(tmp); doc["assets"]["clean_again"]["blocking"] = []
        ar._save(doc, tmp)
    step = "ok_account"
    for nxt in ("IDENTIFIED", "EVIDENCE_PENDING", "VERIFIED", "CLASSIFIED",
                "RESTRICTION_CHECKED", "ACTIONABLE"):
        advance(step, nxt, path=tmp)
    ck("an unblocked asset reaches ACTIONABLE",
       ar.get(step, tmp)["lifecycle"] == "ACTIONABLE")
    refuses(lambda: advance(step, "AUTHORIZED", path=tmp),
            "it cannot skip a step")
    refuses(lambda: advance(step, "PREPARED", path=tmp),
            f"and cannot pass {IMPLEMENTED_THROUGH} in this increment")
    ck("every blocking state is declared",
       set(BLOCKING) >= {"UNKNOWN", "CONFLICT", "RESTRICTED",
                         "NON_TRANSFERABLE", "PROFESSIONAL_REVIEW_REQUIRED",
                         "AUTHORIZATION_REQUIRED"})

    print("\n  5. the VA restriction is statute-backed, from the PUBLIC fixture")
    from core.financial_authority import transferability, may_transfer
    t = transferability("va_disability_compensation")
    ck("the generic VA rule is public and refuses transfer",
       t["state"] == "not_transferable" and t["blocking"], t["state"])
    ck("and it cites the statute rather than returning generic unknown",
       "5301" in str(t.get("citation")), str(t.get("citation")))
    ok, why = may_transfer("va_disability_compensation", "Some Entity LLC")
    ck("may_transfer refuses it", not ok)
    ck("the refusal names the authority, not just the absence",
       "5301" in why, why[:60])

    print("\n  6. the public/private boundary")
    pub = os.path.join(ROOT, "reference", "_shared", "account_layers.json")
    raw = open(pub, encoding="utf-8").read()
    f = isc.scan(raw, context="public_fixture")
    ck("no identifier appears in the PUBLIC account fixture",
       not f.get("findings"), f"{len(f.get('findings') or [])} finding(s)")
    pub_doc = json.load(open(pub, encoding="utf-8"))
    ck("the public fixture keeps the generic VA rule",
       "va_disability_compensation" in pub_doc["accounts"],
       "the rule must be testable by anyone, not only by him")
    priv = os.path.join(ROOT, "private", "account_layers.private.json")
    if not os.path.exists(priv):
        skip("the private overlay loads", "no private overlay here - the "
             "normal clean-clone and CI condition")
    else:
        from core.account_model import load as load_accounts
        with_priv = set(load_accounts()["accounts"])
        without = set(load_accounts(private="/nonexistent")["accounts"])
        ck("the overlay adds entries the public file does not carry",
           with_priv > without, f"{len(with_priv - without)} private entry(ies)")
        ck("and the public file alone still loads",
           bool(without), "CI must work with no private data at all")

    print("\n  7. a MALFORMED overlay is an error, never an absence")
    from core.account_model import load as load_accounts, CorpusUnavailable
    bad = os.path.join(tempfile.mkdtemp(), "broken.json")
    open(bad, "w").write("{ this is not json")
    try:
        load_accounts(private=bad)
        ck("a corrupt overlay raises", False, "it loaded anyway")
    except CorpusUnavailable as exc:
        ck("a corrupt overlay raises", True, str(exc)[:52])
    try:
        load_accounts(private="/definitely/not/here.json")
        ck("a missing overlay does NOT raise", True,
           "absent and broken are opposite situations")
    except Exception as exc:                        # noqa: BLE001
        ck("a missing overlay does NOT raise", False, str(exc)[:52])

    print("\n  8. account_model.load() is the only reader")
    offenders = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in {
            ".git", "venv", "__pycache__", "quarantine", "state", "private",
            "node_modules"} and not d.startswith("backup_")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT)
            if rel in ("core/account_model.py", "tools/check_asset_registry.py"):
                continue
            body = open(os.path.join(dirpath, fn), encoding="utf-8",
                        errors="replace").read()
            for line in body.splitlines():
                st = line.strip()
                if "account_layers" in st and st.startswith(("open(", "with open(",
                                                             "json.load")):
                    offenders.append(f"{rel}: {st[:50]}")
    ck("nothing opens the accounts file directly", not offenders,
       str(offenders[:2]) or "the overlay would be invisible to anyone who did")

    print("\n  9. the SEC contact comes from configuration, never from source")
    import core.ownership_graph as og
    src = open(os.path.join(ROOT, "core", "ownership_graph.py"),
               encoding="utf-8").read()
    ck("no email literal in the source", "@" not in src.split("SEC_UA =")[1][:200]
       if "SEC_UA =" in src else False)
    # IT MUST REFUSE WITHOUT TOUCHING THE NETWORK. The first version of this
    # test could not tell a configuration refusal from an HTTP failure, so a
    # whitespace User-Agent "passed" while actually sending a blank identifier
    # to SEC. urllib is disabled for the duration so a request would raise a
    # distinguishable error instead of quietly succeeding at failing.
    import urllib.request as _u
    saved, real_open = og.SEC_UA, _u.urlopen

    def _no_network(*a, **k):
        raise AssertionError("a request was attempted despite bad config")
    _u.urlopen = _no_network
    try:
        for label, val in (("missing", None), ("empty", ""),
                           ("whitespace-only", "   "),
                           ("no contact address", "Mycelial Research")):
            og.SEC_UA = val
            try:
                og.Edgar()._get("https://www.sec.gov/never-requested")
                ck(f"a {label} User-Agent refuses", False, "it was accepted")
            except og.EdgarError as exc:
                ck(f"a {label} User-Agent refuses", True, str(exc)[:44])
            except AssertionError as exc:
                ck(f"a {label} User-Agent refuses", False, str(exc))
            except Exception as exc:                # noqa: BLE001
                ck(f"a {label} User-Agent refuses", False,
                   f"raised {type(exc).__name__}, not EdgarError")
        # A valid value must NOT be refused at the configuration gate. _get
        # wraps whatever the network layer raises in EdgarError, so the test
        # reads the MESSAGE rather than the type: a config refusal names
        # SEC_USER_AGENT, and anything else means it got past the gate and
        # failed later - which is the correct boundary.
        og.SEC_UA = "Mycelial Research (someone@example.com)"
        try:
            og.Edgar()._get("https://www.sec.gov/never-requested")
            ck("a valid User-Agent gets past configuration", False,
               "no request was attempted at all")
        except Exception as exc:                    # noqa: BLE001
            ck("a valid User-Agent gets past configuration",
               "SEC_USER_AGENT" not in str(exc),
               "reached the network layer" if "SEC_USER_AGENT" not in str(exc)
               else f"refused a good value: {str(exc)[:40]}")
    finally:
        _u.urlopen = real_open
        og.SEC_UA = saved

    print()
    if skips:
        print(f"  {len(skips)} SKIPPED (private fixtures absent): {skips}")
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the registry refuses before it persists, and a broken control "
          "refuses too")
    return 0


if __name__ == "__main__":
    sys.exit(main())
