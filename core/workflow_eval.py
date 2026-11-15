#!/usr/bin/env python3
"""Level-4 workflow evaluation - test a multi-agent OPERATION end to end, not each
agent in isolation. Testing every agent alone does not establish that the sequence
between them works: handoffs drop state, a boundary can be crossed, a failed step can
be reported as success.

A workflow is an ordered list of stages. Each stage names its OWNER, whether it is
AUTHORIZATION-GATED (must not run without an explicit human authorization), and whether
it is IRREVERSIBLE (a real-world change the evaluation must never perform itself). Stage
runners are INJECTED, so an evaluation can drive the read-only stages live while the
irreversible ones are simulated - the evaluator itself performs no privileged action.

The evaluator:
  - runs stages in order, carrying state forward;
  - stops at the first failure, recording WHERE and WHY (no false completion);
  - refuses to run an authorization-gated stage unless ctx['authorized'] is true
    (the boundary is held, not crossed);
  - refuses to run an irreversible stage unless the caller opts into a simulated
    transition (ctx['allow_irreversible_simulation']), so a real apply never happens
    inside an evaluation;
  - treats a stage that raises as a failure, not a crash.

This is the false-success doctrine at the workflow level: a sequence that reports
completion is a CLAIM; this verifies each handoff produced the expected state, and that
the authorization boundary and the failure-recovery path actually hold.
"""
from dataclasses import dataclass
from typing import Callable, List


@dataclass
class Stage:
    name: str
    owner: str
    run: Callable                       # (state: dict, ctx: dict) -> (ok: bool, state: dict, detail: str)
    authorization_gated: bool = False   # must not execute without ctx['authorized']
    irreversible: bool = False          # a real-world change; never executed, only simulated, in eval


@dataclass
class Workflow:
    name: str
    stages: List[Stage]

    def evaluate(self, ctx=None):
        ctx = dict(ctx or {})
        state, trace = {}, []
        for st in self.stages:
            if st.authorization_gated and not ctx.get("authorized"):
                trace.append({"stage": st.name, "owner": st.owner, "status": "blocked",
                              "detail": "authorization required - boundary held, not crossed"})
                return _result(False, st.name, "authorization boundary", trace, state)
            if st.irreversible and not ctx.get("allow_irreversible_simulation"):
                trace.append({"stage": st.name, "owner": st.owner, "status": "withheld",
                              "detail": "irreversible stage not executed in evaluation"})
                return _result(False, st.name, "irreversible stage withheld", trace, state)
            try:
                ok, state, detail = st.run(state, ctx)
            except Exception as e:  # a raising stage is a failure, never a crash of the evaluator
                trace.append({"stage": st.name, "owner": st.owner, "status": "error", "detail": repr(e)})
                return _result(False, st.name, f"stage raised: {e!r}", trace, state)
            trace.append({"stage": st.name, "owner": st.owner,
                          "status": "ok" if ok else "failed", "detail": detail})
            if not ok:
                return _result(False, st.name, detail, trace, state)
        return _result(True, None, "all stages completed and verified", trace, state)


def _result(completed, stopped_at, reason, trace, state):
    return {"completed": completed, "stopped_at": stopped_at, "reason": reason,
            "trace": trace, "state": state,
            "stages_run": [t["stage"] for t in trace if t["status"] in ("ok", "failed")]}
