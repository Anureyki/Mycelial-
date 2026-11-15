#!/usr/bin/env python3
"""Level-4 workflow evaluation for the security-patch LIFECYCLE - the whole operation,
not each agent alone:

  1 Maintenance detects available packages   (read-only)
  2 Security correlates advisories           (read-only)
  3 Anansi presents the findings             (read-only)
  4 the human AUTHORIZES                      (authorization boundary)
  5 Maintenance applies the update           (IRREVERSIBLE - never performed in eval)
  6 Security verifies the result             (measures the post-state, never assumes)
  7 Anansi reports completion                (only on a VERIFIED result)

Stage runners are injected and deterministic - no live agents, no network, no apt. The
irreversible apply is always simulated; the evaluation performs no real change. This
tests the SEQUENCE, the FAILURE-RECOVERY path, and the AUTHORIZATION BOUNDARY.
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"  # importing core must not emit real events
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.workflow_eval import Stage, Workflow  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


CANDIDATE = "2.43-2ubuntu2.7"


def build(behavior):
    """Build the 7-stage patch workflow. `behavior` tunes individual stages per scenario.
    No stage here runs a shell command or any real privileged action - apply only sets a
    simulated post-state, so the evaluation itself can never change the system."""
    def detect(state, ctx):
        if not behavior.get("detect_ok", True):
            return False, state, "Maintenance detection unavailable"
        state["pending"] = [{"package": "libc6", "candidate": CANDIDATE, "security": True}]
        return True, state, "1 security package detected"

    def correlate(state, ctx):
        if not behavior.get("correlate_ok", True):
            return False, state, "advisory source unavailable"
        state["advisories"] = ["USN-9065-1"]
        return True, state, "advisory correlated"

    def present(state, ctx):
        state["presented"] = True
        return True, state, "surfaced to the operator via operational_status"

    def authorize(state, ctx):
        # The human decision. Without it the workflow stops here - it cannot self-authorize.
        if not ctx.get("authorized"):
            return False, state, "awaiting explicit principal authorization"
        state["authorized_at"] = "simulated"
        return True, state, "principal authorized"

    def apply_update(state, ctx):
        # SIMULATED only. A real apply needs root and is Maintenance's, gated. In eval it
        # never touches the package manager; it just sets the post-state the behavior dictates.
        if not behavior.get("apply_ok", True):
            return False, state, "apply failed (simulated apt failure)"
        if behavior.get("apply_claims_success_without_change"):
            state["applied_version"] = None   # claims done, changed nothing -> step 6 must catch it
            return True, state, "apply reported success (claim)"
        state["applied_version"] = CANDIDATE
        return True, state, "apply simulated: version advanced"

    def verify(state, ctx):
        # Security MEASURES the post-state; self-reported success is a claim, not evidence.
        if state.get("applied_version") != CANDIDATE:
            return False, state, "verification FAILED: version did not change after apply"
        state["verified"] = True
        return True, state, "verified: installed version now the candidate"

    def report(state, ctx):
        if not state.get("verified"):
            return False, state, "refusing to report completion without verification"
        return True, state, "completion reported (verified)"

    return Workflow("security_patch_lifecycle", [
        Stage("detect", "maintenance_agent", detect),
        Stage("correlate", "security_agent", correlate),
        Stage("present", "anansi", present),
        Stage("authorize", "principal", authorize, authorization_gated=False),
        Stage("apply", "maintenance_agent", apply_update, authorization_gated=True, irreversible=True),
        Stage("verify", "security_agent", verify),
        Stage("report", "anansi", report),
    ])


SIM = {"authorized": True, "allow_irreversible_simulation": True}

# 1) HAPPY PATH - authorized, apply simulated OK, verified completion
r = build({}).evaluate(SIM)
check("happy path: all 7 stages complete and completion is VERIFIED",
      r["completed"] is True and r["state"].get("verified") is True
      and r["stages_run"] == ["detect", "correlate", "present", "authorize", "apply", "verify", "report"])

# 2) AUTHORIZATION BOUNDARY - not authorized: stops at authorize, apply never runs
r = build({}).evaluate({"allow_irreversible_simulation": True})  # no 'authorized'
check("authorization boundary: without authorization the workflow stops and apply NEVER runs",
      r["completed"] is False and r["stopped_at"] == "authorize"
      and "apply" not in r["stages_run"])

# 2b) even if reached, the apply stage is gated AND irreversible - never run unauthorized
r = build({}).evaluate({"authorized": False, "allow_irreversible_simulation": True})
check("apply is authorization-gated: it is blocked, not executed, when unauthorized",
      r["completed"] is False and "apply" not in r["stages_run"])

# 3) IRREVERSIBLE WITHHELD - authorized but no simulation opt-in: apply is not executed
r = build({}).evaluate({"authorized": True})  # no allow_irreversible_simulation
check("irreversible apply is withheld by default - the evaluation performs no real change",
      r["completed"] is False and r["stopped_at"] == "apply" and r["reason"] == "irreversible stage withheld")

# 4) FAILURE RECOVERY - apply fails: workflow stops, completion is NOT reported
r = build({"apply_ok": False}).evaluate(SIM)
check("failure recovery: a failed apply stops the workflow and does NOT report completion",
      r["completed"] is False and r["stopped_at"] == "apply"
      and "report" not in r["stages_run"])

# 5) FALSE SUCCESS CAUGHT - apply claims success but changed nothing: verify catches it
r = build({"apply_claims_success_without_change": True}).evaluate(SIM)
check("self-reported apply success with no real change is caught at verify, not reported",
      r["completed"] is False and r["stopped_at"] == "verify" and "report" not in r["stages_run"])

# 6) UPSTREAM FAILURE - detection unavailable: nothing downstream runs
r = build({"detect_ok": False}).evaluate(SIM)
check("detection unavailable: workflow stops at detect, nothing downstream runs",
      r["completed"] is False and r["stopped_at"] == "detect"
      and r["stages_run"] == ["detect"])

# 7) STRUCTURAL - the apply stage really is gated + irreversible, and runs no apt/subprocess
wf = build({})
apply_stage = next(s for s in wf.stages if s.name == "apply")
check("the apply stage is declared authorization-gated AND irreversible",
      apply_stage.authorization_gated is True and apply_stage.irreversible is True)
src = open(os.path.join(ROOT, "tools", "check_patch_workflow.py"), encoding="utf-8").read()
runners = src[src.index("def build(behavior)"):src.index("SIM = {")]  # the stage definitions only
check("the eval performs no real privileged action (no apt/subprocess/sudo in any stage runner)",
      not any(tok in runners for tok in ("subprocess", "apt-get", "sudo", "Popen", "os.system")))

print("check_patch_workflow: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
