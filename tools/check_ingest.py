#!/usr/bin/env python3
"""Uncertainty must SURVIVE ingestion. Tested against real-world mess.

    python3 tools/check_ingest.py

The purpose of Phase 1B is not to make the system know more. It is to make it
able to say why it believes what it believes, with a page number - and to keep
saying "I do not know" where that is the truth, through a pipeline whose whole
tendency is to turn documents into confident rows.

Every fixture is synthetic. No real institution, party, instrument or
identifier appears, and every store is a temp file.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails, skips = [], []


def ck(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}   {why}")
    skips.append(name)


def refuses(fn, name, detail=""):
    from core.asset_registry import Refused
    from core.financial_taxonomy import Unclassifiable
    try:
        fn()
    except (Refused, Unclassifiable) as exc:
        ck(name, True, detail or str(exc)[:52])
        return
    except Exception as exc:                        # noqa: BLE001
        ck(name, False, f"raised {type(exc).__name__}: {exc}")
        return
    ck(name, False, "it was ACCEPTED")


def main():
    import core.evidence_ingest as ei
    from core.evidence_ingest import (register_document, extract, correct,
                                      supersede, conflicts, open_question,
                                      absence, provenance, facts_for, ABSENCE)
    from core.financial_taxonomy import (classify_movement, settle,
                                         commodity_position, COMMODITY_FACETS)

    T = os.path.join(tempfile.mkdtemp(), "ev.json")

    print("\n  1. a fact cannot exist without a source and a location")
    register_document("stmt_jul", "statement", path=T, source_date="2026-07-31",
                      pages=4)
    refuses(lambda: extract("stmt_jul", "acct", "balance", 100, None,
                            "ext", "1.0", path=T),
            "a fact with no location is refused",
            "'because the model said so' is not provenance")
    refuses(lambda: extract("never_registered", "acct", "balance", 100, "p.1",
                            "ext", "1.0", path=T,
                            effective_date="2026-07-31"),
            "a fact from an unregistered document is refused",
            "with a valid effective_date, so it fails for the reason named")
    refuses(lambda: extract("stmt_jul", "acct", "balance", 100, "p.1",
                            "ext", None, path=T, effective_date="2026-07-31"),
            "a fact with no extractor version is refused",
            "a later version reading differently must be visible as a version")

    print("\n  2. DOCUMENT vs MEANING - the layer that must not collapse")
    f1 = extract("stmt_jul", "acct_1", "holder", "Example Bank", "p.2 line 4",
                 "fixture_extractor", "1.0", path=T,
                 effective_date="2026-07-31", observed_date="2026-08-01T00:00:00",
                 original_text="Account holder: Example Bank")
    ck("the fact records what the document SAYS",
       f1["value"] == "Example Bank" and f1["location"] == "p.2 line 4")
    ck("and explicitly declines to say what it MEANS",
       "means_nothing_yet" in f1,
       "'holder' is not 'owner' until a domain says which")
    ck("interpret() does not exist in the ingestion layer",
       not hasattr(ei, "interpret"),
       "an ingestion layer that answered would be a lawyer with a regex")
    q = open_question("acct_1", "is 'holder' owner, custodian or creditor?",
                      "legal_agent", "ingest", path=T, fact_ids=[f1["fact_id"]])
    ck("the domain question is routed, not answered",
       q["for_domain"] == "legal_agent" and q["answered"] is False)

    print("\n  3. seven kinds of not-knowing, kept apart")
    ck("all seven absence states are declared",
       set(ABSENCE) == {"not_checked", "not_found", "not_read", "unreadable",
                        "unknown", "conflicting", "not_applicable"},
       str(sorted(ABSENCE)))
    for st in ("not_checked", "not_found", "unreadable"):
        a = absence("acct_1", f"probe_{st}", st, f"synthetic {st}", path=T)
        ck(f"{st} is recorded as itself", a["absence_state"] == st
           and a["is_permission"] is False)
    refuses(lambda: absence("acct_1", "x", "probably_fine", "y", path=T),
            "an undeclared absence state is refused")
    refuses(lambda: absence("acct_1", "x", "unknown", "", path=T),
            "an absence with no reason is refused", "otherwise it is a blank")

    print("\n  4. two statements disagreeing is a CONFLICT, not a winner")
    register_document("stmt_aug", "statement", path=T, source_date="2026-08-31")
    extract("stmt_aug", "acct_1", "holder", "Other Bank", "p.1",
            "fixture_extractor", "1.0", path=T, effective_date="2026-08-31",
            observed_date="2026-09-01T00:00:00")
    c = conflicts("acct_1", path=T)
    ck("the disagreement is surfaced", len(c) == 1 and c[0]["field"] == "holder")
    ck("both sources are preserved", len(c[0]["sources"]) == 2,
       "the later one does not silently win")
    ck("and it is NOT resolved automatically", c[0]["resolved"] is False,
       "the authority hierarchy is not universal and is not assumed here")

    print("\n  5. supersession keeps history; correction keeps the original")
    f2 = facts_for("acct_1", "holder", path=T)[-1]
    refuses(lambda: supersede(f1["fact_id"], f2["fact_id"], "", path=T),
            "supersession with no reason is refused")
    supersede(f1["fact_id"], f2["fact_id"], "August statement is later",
              path=T)
    live = facts_for("acct_1", "holder", path=T)
    ck("the superseded fact leaves the live view", len(live) == 1)
    ck("but is still on file",
       len(facts_for("acct_1", "holder", path=T, include_superseded=True)) == 2,
       "'what did the system believe at time T' must stay answerable")
    refuses(lambda: correct(f2["fact_id"], "X", "", "someone", path=T),
            "a correction with no reason is refused")
    refuses(lambda: correct(f2["fact_id"], "X", "typo", "", path=T),
            "a correction with no author is refused")
    correct(f2["fact_id"], "Other Bank, N.A.", "OCR dropped the suffix",
            "principal", path=T)
    p = provenance(f2["fact_id"], path=T)
    ck("the correction preserves what was originally extracted",
       p["corrections"][0]["from"] == "Other Bank",
       "an extraction that was wrong is evidence about the extractor")
    ck("provenance answers 'why do you believe this' with a location",
       p["because"]["location"] == "p.1"
       and p["because"]["extractor"].startswith("fixture_extractor"))
    ck("and records no interpretation", p["interpretation"] is None)

    print("\n  6. the sensitive-data boundary reaches the ingestion path")
    refuses(lambda: extract("stmt_jul", "acct_1", "note",
                            "SSN 123-45-6789", "p.3", "ext", "1.0", path=T,
                            effective_date="2026-07-31"),
            "an identifier in an extracted VALUE is refused")
    refuses(lambda: extract("stmt_jul", "acct_1", "note", "ok", "p.3", "ext",
                            "1.0", path=T, effective_date="2026-07-31",
                            original_text="card 4111111111111111"),
            "an identifier in the ORIGINAL TEXT is refused",
            "the quoted source is stored too, so it is scanned too")

    print("\n  7. money moving is not a conclusion")
    refuses(lambda: classify_movement(100000, "in"),
            "an unexplained deposit is refused",
            "income, borrowing and a collateral release look identical")
    refuses(lambda: classify_movement(100000, "in",
                                      funding_source="loan_proceeds",
                                      evidence="derived", citation="x"),
            "a derived funding source is refused")
    r = classify_movement(100000, "in", funding_source="loan_proceeds",
                          evidence="cited", citation="note p.1")
    ck("loan proceeds are marked as creating a liability",
       r["creates_liability"] and not r["is_own_resources"],
       "a balance that rose because a facility was drawn is not a balance "
       "that rose because something was earned")

    print("\n  8. paying a debt is not refinancing it")
    paid = settle("paid", obligation_id="o1", amount=10000,
                  source_of_funds="earned_income")
    refi = settle("refinanced", obligation_id="o1", amount=10000,
                  new_obligation_id="o2", new_creditor="cp_b")
    ck("paid reduces net obligation", paid["net_obligation_change"] == "-amount")
    ck("refinanced leaves net obligation UNCHANGED",
       refi["net_obligation_change"] == 0 and refi["new_obligation_created"],
       "the obligation moved; recording it as payment makes a balance sheet "
       "look settled")
    refuses(lambda: settle("refinanced", obligation_id="o1", amount=10000),
            "a refinancing with no new obligation is refused")
    conv = settle("converted", obligation_id="o1", amount=10000,
                  asset_transferred="synthetic_asset", valuation=9000,
                  valuation_date="2026-01-01", accepted_as_full=False)
    ck("a conversion not accepted as full flags a deficiency",
       conv.get("deficiency_possible") is True
       and not conv["obligation_extinguished"],
       "accepted_as_full=False is an ANSWER, not a missing field")

    print("\n  9. 'gold' is not one financial object")
    refuses(lambda: commodity_position(legal_form="gold"),
            "legal_form='gold' is refused",
            "gold is what the thing is made of, not a legal form")
    un = commodity_position(legal_form="unallocated_claim", quantity=10,
                            unit="oz")
    ck("an unallocated claim is marked as creditor exposure",
       un["creditor_exposure"] is True,
       "no bars with your name on them; in an insolvency an unsecured claim")
    eq = commodity_position(legal_form="equity_exposure", quantity=100,
                            unit="shares")
    ck("shares in a miner are NOT holding metal", eq["holds_metal"] is False)
    prem = commodity_position(legal_form="premium_component", quantity=1,
                              unit="usd")
    ck("the premium is a price component, not a position",
       prem["is_position"] is False)
    ck("every unestablished facet is named",
       set(un["unresolved"]) < set(COMMODITY_FACETS) and un["unresolved"],
       f"{len(un['unresolved'])} unresolved of {len(COMMODITY_FACETS)}")
    ck("and the position reports itself incomplete",
       un["absence_state"] == "incomplete")

    print("\n 10. TEMPORAL INTEGRITY - what did it know, and when")
    # The principal's requirement, and the reason it is mandatory rather than
    # a field somebody might fill: a system storing only current truth is a
    # filing cabinet with good handwriting. It can say what the account looks
    # like today and cannot say what it believed last March, on what evidence,
    # under what authority - which is the question when somebody asks why a
    # decision was made.
    from core.evidence_ingest import (as_of, knowledge_at, record_authority)
    T3 = os.path.join(tempfile.mkdtemp(), "t.json")
    refuses(lambda: extract("m", "s", "f", "v", "p.1", "e", "1.0", path=T3),
            "a fact with no effective_date is refused",
            "(and the document is unregistered too - both are required)")
    register_document("mar", "statement", path=T3, source_date="2026-03-31")
    refuses(lambda: extract("mar", "acct", "balance", 100, "p.1", "e", "1.0",
                            path=T3),
            "effective_date is MANDATORY",
            "a document's date is not always when a fact became true")
    refuses(lambda: extract("mar", "acct", "balance", 100, "p.1", "e", "1.0",
                            path=T3, effective_date="not_applicable"),
            "a bare not_applicable is refused", "it needs its reason")
    m = extract("mar", "acct", "balance", 100, "p.1", "e", "1.0", path=T3,
                effective_date="2026-03-31", observed_date="2026-04-01T00:00:00")
    record_authority("acct", "read_only", "principal", "initial", path=T3,
                     at="2026-04-01T00:00:00")
    register_document("jun", "statement", path=T3, source_date="2026-06-30")
    j = extract("jun", "acct", "balance", 250, "p.1", "e", "1.0", path=T3,
                effective_date="2026-06-30", observed_date="2026-07-01T00:00:00")
    supersede(m["fact_id"], j["fact_id"], "June statement is later", path=T3,
              at="2026-07-01T00:00:00")
    record_authority("acct", "prepare_allowed", "principal", "after review",
                     path=T3, at="2026-07-15T00:00:00")

    k_may = knowledge_at("acct", "2026-05-01T00:00:00", path=T3)
    k_jul = knowledge_at("acct", "2026-07-10T00:00:00", path=T3)
    k_aug = knowledge_at("acct", "2026-08-01T00:00:00", path=T3)
    ck("in May the system believed the March figure",
       [b["value"] for b in k_may["believed"]] == [100])
    ck("in July it believed the June figure, and only that",
       [b["value"] for b in k_jul["believed"]] == [250],
       "today's knowledge must not be reconstructed into the past")
    ck("authority in force is reconstructed separately from belief",
       k_may["authority_in_force"]["state"] == "read_only"
       and k_aug["authority_in_force"]["state"] == "prepare_allowed",
       "what was believed, on what evidence, and what was PERMITTED")
    ck("the evidence behind each past belief is reconstructable",
       k_may["on_this_evidence"][0]["location"] == "p.1"
       and k_may["on_this_evidence"][0]["document"] == "mar")
    ck("nothing was deleted to achieve it",
       len(facts_for("acct", path=T3, include_superseded=True)) == 2)
    ck("a supersession carries the time it HAPPENED, not when it was typed",
       [b["value"] for b in
        knowledge_at("acct", "2026-06-15T00:00:00", path=T3)["believed"]] == [100],
       "stamping every supersession 'now' makes stale facts read as live for "
       "every past query")
    ck("and it says what it CANNOT reconstruct",
       "what_is_not_captured" in k_may,
       "an empty authority section reads as 'nothing was happening', which is "
       "a different claim from 'this was not recorded'")

    print("\n 11. determinism and the messy-document fixtures")
    T2 = os.path.join(tempfile.mkdtemp(), "ev2.json")
    register_document("dup", "statement", path=T2, source_date="2026-07-31")
    a = extract("dup", "s", "f", "v", "p.1", "ext", "1.0", path=T2,
                effective_date="2026-07-31")
    b = extract("dup", "s", "f", "v", "p.1", "ext", "1.0", path=T2,
                effective_date="2026-07-31")
    ck("the same extraction twice yields identical values",
       a["value"] == b["value"] and a["location"] == b["location"])
    ck("but two distinct fact records, each with its own provenance",
       a["fact_id"] != b["fact_id"],
       "deduplication is a judgement; recording both is not")
    v2 = extract("dup", "s", "f", "different", "p.1", "ext", "2.0", path=T2,
                 effective_date="2026-07-31")
    ck("a different extractor VERSION is visible on the fact",
       v2["extractor_version"] == "2.0" and a["extractor_version"] == "1.0",
       "a version that reads differently must not look like history changing")
    register_document("unreadable_pdf", "statement", path=T2)
    absence("doc_unreadable", "text", "unreadable",
            "synthetic fixture: the PDF has no text layer", path=T2)
    absence("missing_page", "p.3", "not_read",
            "synthetic fixture: page 3 absent from the scan", path=T2)
    ck("an unreadable source and an unread page are different records",
       len([x for x in ei.load(T2).get("absences", [])]) == 2)
    d = register_document("nohash", "correspondence", path=T2)
    ck("a document with no file says why it has no hash",
       d["sha256"] is None and "hash_absent_because" in d,
       "cannot-verify-later is a property of the evidence, not a blank field")

    print()
    if skips:
        print(f"  {len(skips)} SKIPPED: {skips}")
    if fails:
        print(f"  {len(fails)} FAILURE(S): {fails}")
        return 1
    print("  uncertainty survives ingestion, and every fact can say why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
