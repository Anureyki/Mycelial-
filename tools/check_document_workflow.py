#!/usr/bin/env python3
"""Level-4 workflow evaluation for the DOCUMENT/retrieval workflow. The real test
(principal, 2026-11-14): can Mycelial RETRIEVE THE CORRECT INFORMATION, PRESERVE ITS
SOURCE, and PROVIDE IT TO THE APPROPRIATE AGENT?

This exercises the REAL code on controlled fixtures - it does NOT stub the behaviour and
does NOT write to the live knowledge base or corpus:
  - core.document_intake.segment  - keeps each clause's own ref (source within the doc)
  - core.document_pipeline.ROUTE  - sends a declared document type to its owning agent
  - AgentBase.query_cache         - the CAG retrieval: scores by overlap, returns the hit's
                                    source path, returns [] when nothing scores, and filters
                                    placeholder fixtures out of 'reference material'
and sequences them through core.workflow_eval so a non-match FAILS the workflow rather than
returning a wrong clause.
"""
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"  # importing core must not emit real events
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.workflow_eval import Stage, Workflow            # noqa: E402
from core.document_intake import segment, analyse         # noqa: E402
from core.document_pipeline import ROUTE                  # noqa: E402
from core.base_agent import AgentBase                      # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


# ISOLATION BOUNDARY: snapshot the live knowledge base and CAG store before and after, and
# assert nothing was created or modified. The eval must not touch real user records.
LIVE_DIRS = [os.path.join(ROOT, "knowledge_base"), os.path.join(ROOT, "state", "cag")]


def _snapshot():
    snap = {}
    for d in LIVE_DIRS:
        if not os.path.isdir(d):
            continue
        for dp, _dirs, fns in os.walk(d):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                    snap[p] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
    return snap


_SNAP0 = _snapshot()


# A real AgentBase instance (no __init__) with a FIXTURE cache - the live store is untouched.
AG = object.__new__(AgentBase)


def _doc(did, category, content, path):
    return {"id": did, "category": category, "content": content,
            "tokens": AG._tokenize(content) | AG._tokenize(did), "path": path}


AG.cache = {
    "usc1346": _doc("28_usc_1346", "reference",
                    "The district courts shall have exclusive jurisdiction of civil actions on "
                    "claims against the United States for money damages for injury caused by a "
                    "government employee.", "reference/legal_agent/28_u_s_c_1346.json"),
    "calmag": _doc("calmag_veg", "grow",
                   "Cal-Mag supplement dosing for vegetative growth is three to five millilitres "
                   "per gallon of reservoir water.", "knowledge_base/grow_agent/calmag.txt"),
    "placeholder": _doc("sample_placeholder", "reference",
                        "[PLACEHOLDER - not from Black's Law Dictionary] custodian means an "
                        "invented definition that must never be cited as authority.",
                        "knowledge_base/legal_agent/sample_placeholder.txt"),
    # a doc that scores but carries NO source path - its retrieval must be rejected, not accepted
    "noprov": _doc("no_source_doc", "reference",
                   "The zorbic widget tolerance threshold is forty two microns under sustained load.",
                   ""),
}

# ---- Property 1: retrieve the CORRECT information (and refuse to fabricate) ----
hits = AG.query_cache("jurisdiction over claims against the United States for money damages")
check("retrieves the correct clause for a real query (28 USC 1346 ranks first)",
      bool(hits) and hits[0]["id"] == "28_usc_1346")
none = AG.query_cache("xyzzy nonexistent quantum flux capacitor")
check("returns NOTHING when nothing scores - a wrong clause is never fabricated",
      none == [])
ph = AG.query_cache("custodian invented definition placeholder")
check("a placeholder fixture is filtered out, never surfaced as reference material",
      all(h["id"] != "sample_placeholder" for h in ph))

# ---- Property 2: PRESERVE the source ----
check("every retrieval hit carries its source path (provenance survives the retrieval hop)",
      bool(hits) and hits[0].get("path") == "reference/legal_agent/28_u_s_c_1346.json")
STAT = ("1. This Act may be cited as the Example Act for the purposes of the demonstration.\n"
        "2. The contractor shall deliver the goods within thirty days of the order date given.\n"
        "3. Payment of five hundred dollars is due upon delivery of the goods to the buyer.\n")
clauses = segment(STAT)
check("segmentation keeps each clause's own ref (its source within the document)",
      len(clauses) == 3 and clauses[0]["ref"] == "1" and clauses[2]["ref"] == "3"
      and all(c.get("text") for c in clauses))
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
    fh.write(STAT)
    _tmp = fh.name
rep = analyse(_tmp)
os.unlink(_tmp)
check("analyse preserves the document as the source and keeps clause refs",
      rep.get("source", "").endswith(".txt") and rep.get("clauses") == 3
      and all("ref" in it for it in rep.get("items", [])))

# ---- Property 3: provide it to the APPROPRIATE agent (by DECLARED type, not by reading) ----
check("declared document types route to their owning agent",
      ROUTE["statement"][0] == "accounting_agent" and ROUTE["invoice"][0] == "accounting_agent"
      and ROUTE["contract"][0] == "legal_agent" and ROUTE["certificate"][0] == "trust_agent")
check("routing is by the DECLARED type, not by reading content (a contract about money still goes to Legal)",
      ROUTE["contract"][0] == "legal_agent")

# ---- The three properties sequenced as one workflow (non-match must FAIL, not fabricate) ----
def s_segment(state, ctx):
    cl = segment(ctx["text"])
    if not cl:
        return False, state, "no clauses segmented"
    state["clauses"] = cl
    return True, state, f"{len(cl)} clauses with refs"


def s_route(state, ctx):
    t = ctx["declared_type"]
    if t not in ROUTE:
        return False, state, f"unknown declared type {t!r}"
    state["routed_to"] = ROUTE[t][0]
    return True, state, f"routed to {ROUTE[t][0]} by declared type"


def s_retrieve(state, ctx):
    h = AG.query_cache(ctx["query"])
    if not h:
        return False, state, "no clause scored - returning nothing rather than a wrong clause"
    state["retrieved"] = h[0]
    return True, state, f"top hit {h[0]['id']}"


def s_validate(state, ctx):
    h = state.get("retrieved") or {}
    if not h.get("path"):
        return False, state, "retrieved clause has no source - provenance lost"
    if ctx.get("expect_id") and h["id"] != ctx["expect_id"]:
        return False, state, f"wrong clause: {h['id']}"
    state["validated"] = True
    return True, state, f"correct clause, source preserved: {h['path']}"


WF = Workflow("document_retrieval", [
    Stage("segment", "document_intake", s_segment),
    Stage("route", "document_pipeline", s_route),
    Stage("retrieve", "cag", s_retrieve),
    Stage("validate", "requesting_agent", s_validate),
])

r = WF.evaluate({"text": STAT, "declared_type": "contract",
                 "query": "jurisdiction over claims against the United States for money damages",
                 "expect_id": "28_usc_1346"})
check("end-to-end: segment -> route -> retrieve -> validate completes, correct clause, source kept, agent chosen",
      r["completed"] is True and r["state"]["validated"] is True
      and r["state"]["routed_to"] == "legal_agent"
      and r["state"]["retrieved"]["path"].endswith("28_u_s_c_1346.json"))

r2 = WF.evaluate({"text": STAT, "declared_type": "contract",
                  "query": "xyzzy nonexistent quantum flux", "expect_id": "28_usc_1346"})
check("end-to-end: a non-match FAILS the workflow at retrieve rather than returning a wrong clause",
      r2["completed"] is False and r2["stopped_at"] == "retrieve")

# ---- NEGATIVE: wrong source type must not route anywhere (no incorrect routing) ----
check("an unknown/wrong declared source type maps to no agent (ROUTE does not default to a wrong owner)",
      ROUTE.get("counterfeit_type") is None)
rw = WF.evaluate({"text": STAT, "declared_type": "counterfeit_type",
                  "query": "jurisdiction over claims against the United States for money damages",
                  "expect_id": "28_usc_1346"})
check("wrong source type FAILS at route; no agent chosen and retrieve/validate never run (order enforced)",
      rw["completed"] is False and rw["stopped_at"] == "route"
      and "routed_to" not in rw["state"] and "retrieve" not in rw["stages_run"])

# ---- NEGATIVE: a retrieved clause with MISSING provenance is rejected, not accepted as evidence ----
rp = WF.evaluate({"text": STAT, "declared_type": "contract",
                  "query": "zorbic widget tolerance threshold under sustained load"})
check("a match with MISSING provenance is rejected at validate - no source means no evidence",
      rp["completed"] is False and rp["stopped_at"] == "validate" and "no source" in rp["reason"])

# ---- ORDER: workflow_eval establishes the stages ran TOGETHER IN ORDER ----
check("the stages ran in the correct order through workflow_eval, not as four separate passes",
      [t["stage"] for t in r["trace"]] == ["segment", "route", "retrieve", "validate"])

# ---- ISOLATION: the live store was neither written nor contaminated ----
check("ISOLATION: the live knowledge base and CAG store are byte-for-byte unchanged by the eval",
      _snapshot() == _SNAP0)

print("check_document_workflow: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
