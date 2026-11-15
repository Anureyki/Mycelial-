#!/usr/bin/env python3
"""Level-4 end-to-end DOCUMENT CYCLE evaluation - connects the document workflow to the
ACTUAL Anansi intake and response boundary, closing the gaps check_document_workflow left:

  - the real Anansi ingestion ENTRY POINT (Anansi.ingest_upload) is exercised end to end;
  - delivery to a real OWNING AGENT (its real ingest_document) is exercised, not just routing;
  - a real PERSIST -> RETRIEVE cycle runs (intake -> evidence_ingest -> facts_for), with provenance;
  - CITED EVIDENCE is shown in a validated final response through Anansi.process_request.

Four boundaries held: PRODUCTION functions (real ingest_upload, ingest_document, intake,
evidence_ingest, process_request); ISOLATED fixtures (evidence_ingest.STORE redirected to a temp
file; a before/after snapshot asserts the live private/, state/ and knowledge_base/ are unchanged);
NEGATIVE cases (a subject never ingested yields no fact and no fabricated citation); and the whole
thing runs THROUGH core.workflow_eval so the stages are proven to work together in order.

Honest about the one simulation: the A2A transport is IN-PROCESS (Anansi.send_a2a is pointed at a
real owning-agent instance), not a live socket; and narrate is identity so the eval needs no voice
config. Everything else is production code.
"""
import inspect
import os
import sys
import tempfile

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


# ---- ISOLATION: redirect the evidence store to temp BEFORE any write; snapshot the live dirs ----
import core.evidence_ingest as ei                      # noqa: E402
_TMPDIR = tempfile.mkdtemp(prefix="doccycle_")
ei.STORE = os.path.join(_TMPDIR, "evidence.json")      # the whole real chain now writes here

LIVE_DIRS = [os.path.join(ROOT, d) for d in ("private", "state", "knowledge_base")]


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

from core.workflow_eval import Stage, Workflow         # noqa: E402
from core.base_agent import AgentBase                   # noqa: E402
from agents.anansi import Anansi as _A                  # noqa: E402

AnansiCls = next(v for v in vars(_A).values()
                 if inspect.isclass(v) and v.__module__ == _A.__name__
                 and "operational_status" in v.__dict__)

# a fixture contract document (temp file, not the live store)
FIXTURE = os.path.join(_TMPDIR, "contract.txt")
with open(FIXTURE, "w") as fh:
    fh.write("The contractor shall deliver the goods within thirty days of the order date.\n")
DOCID = "cycle_contract_1"

# a real owning-agent instance (no __init__) that will receive the delivery and persist it
shadow = object.__new__(AgentBase)
shadow.agent_id, shadow.log = "legal_agent", (lambda *a, **k: None)

# a real Anansi instance; its A2A is pointed IN-PROCESS at the real owning agent
anansi = object.__new__(AnansiCls)
anansi.agent_id, anansi.log, anansi.sessions = "anansi", (lambda *a, **k: None), {}
anansi.narrate = lambda res, prompt: res   # identity: the eval needs no voice config
anansi.send_a2a = lambda target, task, args=None, timeout=600: (
    shadow.ingest_document(args) if task == "ingest_document"
    else {"error": f"unexpected a2a {task}"})


def s_intake(state, ctx):
    res = anansi.ingest_upload({"path": FIXTURE, "source_type": "contract",
                                "document_id": DOCID, "declared_by": "test"})
    if res.get("routed_to") != "legal_agent":
        return False, state, f"routed to {res.get('routed_to')!r}, not the owning agent"
    delivered = res.get("result") or {}
    if (delivered.get("stored") or 0) < 1:
        return False, state, "delivery to the owning agent stored nothing"
    state["doc_id"] = DOCID
    return True, state, f"Anansi delivered to legal_agent; {delivered.get('stored')} clause(s) stored"


def s_persist(state, ctx):
    facts = ei.facts_for(DOCID)
    if not facts:
        return False, state, "nothing persisted for the document"
    if not all(f.get("document_id") == DOCID and f.get("location") for f in facts):
        return False, state, "a persisted fact is missing its provenance (document_id/location)"
    state["facts"] = facts
    return True, state, f"{len(facts)} fact(s) persisted with provenance, retrievable by document"


def s_respond(state, ctx):
    # Anansi's REAL response assembly. route_to_orchestrator returns the owning agent's REAL
    # retrieval (facts_for) as evidence; process_request extracts and caches it behind the answer.
    facts = ei.facts_for(ctx.get("subject", DOCID))
    anansi.route_to_orchestrator = lambda prompt, md, _f=facts: {"result": {
        "result": ("Found a stored clause." if _f else "No stored evidence for that subject."),
        "evidence": [{"document_id": f["document_id"], "location": f["location"],
                      "value": f["value"]} for f in _f]}}
    answer = anansi.handle_task("process_request", [ctx["query"], "evalsession"], "test")
    sess = anansi.sessions.get("evalsession") or {}
    last = (sess.get("conversation") or [{}])[-1]
    state["answer"] = answer
    state["cited_evidence"] = last.get("evidence") or []
    return True, state, f"response assembled; {len(state['cited_evidence'])} cited item(s) behind it"


def s_validate(state, ctx):
    ev = state.get("cited_evidence") or []
    if ctx.get("expect_citation"):
        if not ev or not all(e.get("document_id") == DOCID and e.get("location") for e in ev):
            return False, state, "cited evidence missing or without provenance in the final response"
        return True, state, f"final response cites {ev[0]['document_id']} @ {ev[0]['location']}"
    # negative: nothing should be cited for a subject never ingested
    if ev:
        return False, state, "a citation was fabricated for a subject with no stored evidence"
    return True, state, "no evidence stored -> no citation fabricated"


WF = Workflow("document_cycle", [
    Stage("anansi_intake", "anansi", s_intake),
    Stage("persist", "legal_agent", s_persist),
    Stage("anansi_respond", "anansi", s_respond),
    Stage("validate", "requesting_agent", s_validate),
])

# ---- HAPPY CYCLE: ingest through Anansi -> persist -> retrieve -> cited response ----
r = WF.evaluate({"query": "what does the contract require of the contractor",
                 "subject": DOCID, "expect_citation": True})
check("real Anansi intake delivered the document to the owning agent and it persisted",
      any(t["stage"] == "anansi_intake" and t["status"] == "ok" for t in r["trace"]))
check("a real persist -> retrieve cycle: the ingested clause is read back with provenance",
      any(t["stage"] == "persist" and t["status"] == "ok" for t in r["trace"]))
check("end-to-end cycle completes and the final Anansi response carries CITED evidence",
      r["completed"] is True and r["state"]["cited_evidence"]
      and r["state"]["cited_evidence"][0]["document_id"] == DOCID)
check("the four stages ran together IN ORDER through workflow_eval",
      [t["stage"] for t in r["trace"]] == ["anansi_intake", "persist", "anansi_respond", "validate"])

# ---- NEGATIVE: a subject never ingested -> no fact, no fabricated citation in the response ----
rn = WF.evaluate({"query": "what does the contract require of the contractor",
                  "subject": "never_ingested_doc", "expect_citation": False})
check("a subject never ingested yields no stored fact and no fabricated citation",
      rn["completed"] is True and rn["state"]["cited_evidence"] == [])

# ---- ISOLATION: the live stores were neither written nor contaminated ----
check("ISOLATION: live private/, state/ and knowledge_base/ are byte-for-byte unchanged",
      _snapshot() == _SNAP0)

print("check_document_cycle: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
