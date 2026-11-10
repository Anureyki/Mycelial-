#!/usr/bin/env python3
"""Build gate: Legal's governance lives in Hermes's register (principal, 2026-11-10,
option A increment 3) - config/governance/legal_agent.json, served by Hermes.

Proves: the register is well formed (every action names a rule that exists, every
decision is allow / refuse / handoff); Hermes answers a named action from it, refuses an
unnamed one and a missing register; Legal's disclaimer comes from the register and no
copy of it is left in Legal's code; an unreachable register is HELD for the principal,
never allowed; and the three governed points carry Hermes's ruling. No live store is
touched - Hermes's methods are called directly and A2A is stubbed.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


reg = json.load(open(os.path.join(ROOT, "config", "governance", "legal_agent.json"), encoding="utf-8"))
check("the register names its disclaimer and scope", bool(reg.get("disclaimer")) and bool(reg.get("scope")))
check("every action names a rule that exists, with a valid decision",
      all(a["rule"] in reg["rules"] and a["decision"] in ("allow", "refuse", "handoff") for a in reg["actions"].values()))
check("sending to a third party and filing with a court are refused",
      reg["actions"]["send_to_third_party"]["decision"] == "refuse" and reg["actions"]["file_with_court"]["decision"] == "refuse")

from agents.hermes.hermes_interface import HermesInterface  # noqa: E402
h = HermesInterface.__new__(HermesInterface)
h.log = lambda *a, **k: None
r = h.governance_check("legal_agent", "deliver_dispute_letter")
check("Hermes answers a named action from the register, with the rule's text",
      r["decision"] == "handoff" and r["rule"] == "principal_signs_and_sends" and "Nothing is sent" in r["text"])
check("an action the register does not name is refused",
      h.governance_check("legal_agent", "wire_the_settlement")["decision"] == "refuse")
check("a domain with no register is refused, not allowed",
      h.governance_check("no_such_dept", "anything")["decision"] == "refuse")
check("a malformed domain name is rejected", "error" in h.governance_register("../etc/passwd"))

import agents.legal_agent.legal_agent as LA  # noqa: E402
src = open(os.path.join(ROOT, "agents", "legal_agent", "legal_agent.py"), encoding="utf-8").read()
check("no copy of the disclaimer is left in Legal's code", reg["disclaimer"][:60] not in src)
a = LA.LegalAgent.__new__(LA.LegalAgent)
a.log = lambda *x, **k: None
a.send_a2a = lambda agent, task, args, **k: {"result": getattr(h, task)(**args)}
LA.DISCLAIMER = LA.GOVERNANCE_UNREACHABLE
a._load_governance()
check("Legal's disclaimer is the register's, read through Hermes", LA.DISCLAIMER == reg["disclaimer"])
check("Legal's handoff for a dispute letter is Hermes's ruling", a._handoff("deliver_dispute_letter")["rule"] == "principal_signs_and_sends")


def boom(*x, **k):
    raise ConnectionError("hermes down")


a.send_a2a = boom
held = a._handoff("deliver_dispute_letter")
check("an unreachable register is held for the principal, never allowed",
      held["decision"] == "handoff" and held["rule"] == "governance_unreachable")
LA.DISCLAIMER = LA.GOVERNANCE_UNREACHABLE
a._load_governance()
check("and the disclaimer says the register was not reached, not a second copy",
      LA.DISCLAIMER == LA.GOVERNANCE_UNREACHABLE and "not been reached" in LA.DISCLAIMER)
check("the three governed points carry Hermes's ruling",
      '"governance": self._handoff("deliver_dispute_letter")' in src
      and 'self._handoff("draft_ssa_demand")' in src and 'self._handoff("deliver_ssa_demand")' in src
      and 'result["governance"] = self._handoff("filing_with_block")' in src)
print("check_governance: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
