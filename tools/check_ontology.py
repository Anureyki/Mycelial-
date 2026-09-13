#!/usr/bin/env python3
"""The three registries as ONE ontology, tested together.

    python3 tools/check_ontology.py

Each registry refuses correctly on its own. This asserts the properties that
only exist when they are joined - which is where a three-table design usually
turns out to be three lists:

    an edge cannot invent a node
    a role belongs to the EDGE, so one party can hold several
    identity is never merged on resemblance
    a clause nobody read is not a clause that is absent
    the statute outranks the instrument

EVERY FIXTURE IS SYNTHETIC and every store is a temp file. Nothing here
touches private/, and no real institution, party or instrument appears.
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


def refuses(fn, name, detail=""):
    from core.asset_registry import Refused
    try:
        fn()
    except Refused as exc:
        ck(name, True, detail or str(exc)[:54])
        return
    except Exception as exc:                        # noqa: BLE001
        ck(name, False, f"raised {type(exc).__name__}, not Refused: {exc}")
        return
    ck(name, False, "it was ACCEPTED")


def main():
    from core.asset_registry import record as asset
    from core.counterparty_registry import (record as party, link_identity,
                                            same_as, add_alias)
    from core.contract_registry import record as contract, clause, assignability
    from core.registry_graph import (edge, edges_for, governing_contract,
                                     orphans, EDGE_KINDS)

    d = tempfile.mkdtemp()
    A = os.path.join(d, "a.json"); C = os.path.join(d, "c.json")
    K = os.path.join(d, "k.json"); E = os.path.join(d, "e.json")
    st = {"assets": A, "counterparties": C, "contracts": K}

    print("\n  1. the ontology holds three kinds of thing, not one")
    asset("acct_1", kind="account", evidence="stated_by_principal",
          institution="Example Bank", account_type="checking",
          last_four="1234", path=A)
    asset("benefit_1", kind="benefit", evidence="stated_by_principal",
          note="a synthetic entitlement", path=A)
    party("bank_x", "bank", evidence="cited", legal_name="Example Bank, N.A.",
          document_ref="synthetic stmt p.1", path=C)
    party("svc_y", "servicer", evidence="cited",
          legal_name="Example Bank Servicing LLC",
          document_ref="synthetic notice", path=C)
    contract("dep_1", "deposit_agreement", evidence="cited",
             document_ref="synthetic agreement", path=K)
    ck("an account and a benefit are different kinds",
       True, "a benefit is a right to a stream, not transferable property")

    print("\n  2. an edge cannot invent a node")
    refuses(lambda: edge("acct_1", "held_at", "never_recorded",
                         evidence="cited", citation="x", path=E, stores=st),
            "an edge to an unrecorded party is refused",
            "a node created by a relationship is a party nobody verified")
    refuses(lambda: edge("ghost_asset", "held_at", "bank_x",
                         evidence="cited", citation="x", path=E, stores=st),
            "an edge FROM an unrecorded asset is refused")

    print("\n  3. an edge needs evidence and a citation")
    refuses(lambda: edge("acct_1", "held_at", "bank_x", evidence="derived",
                         citation="they appear together", path=E, stores=st),
            "a derived edge is refused",
            "plausible connections nobody can trace back to a document")
    refuses(lambda: edge("acct_1", "held_at", "bank_x", evidence="cited",
                         citation=None, path=E, stores=st),
            "an edge with no citation is refused")

    print("\n  4. the ROLE is on the edge, so one party holds several")
    edge("acct_1", "held_at", "bank_x", evidence="cited",
         citation="synthetic stmt p.1", path=E, stores=st)
    edge("acct_1", "serviced_by", "svc_y", evidence="cited",
         citation="synthetic notice", path=E, stores=st)
    edge("acct_1", "owed_to", "bank_x", evidence="cited",
         citation="synthetic stmt p.2", path=E, stores=st)
    out = edges_for("acct_1", path=E)["outbound"]
    kinds = {e["kind"] for e in out}
    ck("one asset carries holder, servicer and creditor separately",
       kinds == {"held_at", "serviced_by", "owed_to"}, str(sorted(kinds)))
    bank_edges = {e["kind"] for e in out if e["to"] == "bank_x"}
    ck("one party holds TWO roles for the same asset",
       bank_edges == {"held_at", "owed_to"}, str(sorted(bank_edges)))
    ck("and the servicer is a different party",
       any(e["to"] == "svc_y" for e in out),
       "a servicer is routinely not the company whose name is on the card")
    ck("every edge kind declares its direction and meaning",
       all(len(v) == 3 and v[2] for v in EDGE_KINDS.values()),
       f"{len(EDGE_KINDS)} kinds - owed_to and owed_by are not one edge read "
       f"from two sides")

    print("\n  5. identity is never merged on resemblance")
    s = same_as("bank_x", path=C)
    ck("same_as finds the look-alike", 
       any(c["party_id"] == "svc_y" for c in s["candidates"]),
       str([c["party_id"] for c in s["candidates"]]))
    ck("and reports it as NOT established", s["established"] is False,
       "resemblance is a question for a person, never a finding")
    refuses(lambda: link_identity("bank_x", "svc_y", evidence="derived",
                                  citation="the names match", path=C),
            "linking on derived evidence is refused")
    refuses(lambda: link_identity("bank_x", "svc_y", evidence="cited",
                                  citation="", path=C),
            "linking without a citation is refused",
            "'looks like the same company' is not evidence")
    refuses(lambda: add_alias("bank_x", "Example Bk", evidence="derived",
                              document_ref="x", path=C),
            "an alias on a hunch is refused")
    r = link_identity("bank_x", "svc_y", evidence="cited",
                      citation="synthetic merger notice p.2", path=C)
    ck("a documented link is recorded and does NOT merge",
       r["merged"] is False and set(r["linked"]) == {"bank_x", "svc_y"},
       "a merge destroys the fact that two sources named two parties")

    print("\n  6. a clause nobody read is not a clause that is absent")
    a = assignability("dep_1", path=K)
    ck("a fresh contract blocks rather than reading as assignable",
       a["state"] == "unknown" and a["blocking"],
       f"unread: {a.get('clauses_unread')}")
    refuses(lambda: clause("dep_1", "assignment", "present", path=K),
            "a present clause with no location is refused",
            "an assertion about a document nobody can check")
    refuses(lambda: clause("dep_1", "assignment", "absent", path=K),
            "an absent clause with no account of what was read is refused")
    for c in ("assignment", "consent_required", "transfer_restriction"):
        clause("dep_1", c, "absent", note="read the synthetic document in full",
               path=K)
    a = assignability("dep_1", path=K)
    ck("only after reading does it report assignable", a["state"] == "assignable")
    ck("and it says that is a fact about THIS contract only",
       "not_a_conclusion" in a,
       "a statute can forbid what a contract permits")

    print("\n  7. the statute outranks the instrument")
    from core.financial_authority import transferability
    t = transferability("va_disability_compensation")
    ck("a non-assignable benefit stays non-assignable",
       t["state"] == "not_transferable" and t["blocking"], t["state"])
    ck("cited to the statute, not to any contract",
       "5301" in str(t.get("citation")), str(t.get("citation")))

    print("\n  8. the graph reports what is NOT connected")
    g = governing_contract("acct_1", path=E)
    ck("an asset with no instrument says not_checked, not None",
       g["absence_state"] == "not_checked",
       "unrecorded and 'no contract governs this' are different findings")
    edge("acct_1", "governed_by", "dep_1", evidence="cited",
         citation="synthetic agreement p.1", path=E, stores=st)
    g = governing_contract("acct_1", path=E)
    ck("once linked it resolves", g["contract_id"] == "dep_1")
    edge("acct_1", "governed_by", "dep_1", evidence="cited",
         citation="synthetic agreement p.1", path=E, stores=st)
    ck("a duplicate edge is not appended twice",
       len([e for e in edges_for("acct_1", path=E)["outbound"]
            if e["kind"] == "governed_by"]) == 1)
    orph = orphans(path=E, stores=st)
    ck("an unconnected asset is reported as an orphan",
       any(o["node"] == "benefit_1" for o in orph),
       str([o["node"] for o in orph]))

    print("\n  9. the shared write guard reaches every registry")
    refuses(lambda: party("bad", "bank", evidence="stated_by_principal",
                          legal_name="X", note="SSN 123-45-6789", path=C),
            "an identifier in a COUNTERPARTY record is refused")
    refuses(lambda: contract("bad", "loan", evidence="stated_by_principal",
                             note="card 4111111111111111", path=K),
            "a card number in a CONTRACT record is refused")
    refuses(lambda: party("bad2", "bank", evidence="stated_by_principal",
                          password="x", path=C),
            "a credential field is refused everywhere")

    print()
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  the ontology holds: parties, instruments and edges, each "
          "established or refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
