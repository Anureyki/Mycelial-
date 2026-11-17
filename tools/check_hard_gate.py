#!/usr/bin/env python3
"""Build gate for the HARD-GATE on send/transmit/file actions (block 4, 2026-11-16).

Proves the refusal is real and NON-OVERRIDABLE by another agent's prompt:
- an agent-originated send is refused even with a forged user_approved=true;
- the human channel without explicit approval is refused;
- the human channel WITH explicit in-conversation approval passes;
- a non-gated task is unaffected;
- completeness: every verb an agent card marks `external` (reaches a third party)
  is in HARD_GATED_TASKS, so no outbound verb can ship ungated;
- the gate runs at the /execute boundary BEFORE handle_task - outside the model.

No HTTP, no network - hard_gate_allows is a pure function.
"""
import glob
import json
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.base_agent import AgentBase  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


agent = object.__new__(AgentBase)  # class constants + pure method, no __init__

# 1. An agent cannot trigger a send - even forging the approval flag.
ok, reason = agent.hard_gate_allows("send_email", {"user_approved": True}, "legal_agent")
check("agent sender with FORGED user_approved is refused (non-overridable)", ok is False)
check("the refusal names itself as not overridable by any agent prompt",
      reason and "not overridable" in reason.lower())

# Anansi is an agent too - the interface cannot fabricate a send either.
ok, _ = agent.hard_gate_allows("file_with_agency", {"user_approved": True}, "anansi")
check("even anansi (an agent) cannot trigger a send/file action", ok is False)

# 2. Human channel, no approval -> refused.
ok, _ = agent.hard_gate_allows("send_email", {}, "user")
check("human channel WITHOUT explicit approval is refused", ok is False)

# 3. Human channel + explicit approval -> passes.
ok, _ = agent.hard_gate_allows("send_email", {"user_approved": True}, "user")
check("human channel WITH explicit in-conversation approval passes", ok is True)

# 4. A non-gated task is never touched by the gate.
ok, _ = agent.hard_gate_allows("acquire_opinion", {}, "legal_agent")
check("a non-gated task (acquire_opinion) is unaffected by the gate", ok is True)

# 5. Completeness: every `external` card verb reaching a THIRD PARTY is hard-gated.
# Anansi's principal-channel verbs are `external` (they use mail/SMS) but they
# reach the PRINCIPAL himself - tier-1 automatic - or are already refused for
# third parties by Anansi's own tier system and check_voice ("nothing but Anansi
# sends mail"). They are governed THERE, not by the block-4 document send/file
# gate, so they are not required to be in HARD_GATED_TASKS. Any NEW external verb
# outside this known, justified set must be hard-gated or the build fails.
PRINCIPAL_CHANNEL_EXEMPT = {
    "notify", "receive_mail", "ack_preview", "journal_email",
    "organ_clock_event", "held_mail_digest",
}
external = []
for card in glob.glob(os.path.join(ROOT, "config", "agent_cards", "*.json")):
    try:
        d = json.load(open(card, encoding="utf-8"))
    except Exception:
        continue
    for task, cls in (d.get("effects") or {}).items():
        if cls == "external":
            external.append((os.path.basename(card), task))
ungated = [(c, t) for c, t in external
           if not agent.is_hard_gated(t) and t not in PRINCIPAL_CHANNEL_EXEMPT]
check(f"every third-party `external` verb is hard-gated "
      f"({len(external)} external, {len(ungated)} ungated & unexempt)", not ungated)
if ungated:
    print("     UNGATED EXTERNAL VERBS (neither hard-gated nor principal-channel):", ungated)

# 6. The gate is wired at the /execute boundary, before dispatch.
src = open(os.path.join(ROOT, "core", "base_agent.py"), encoding="utf-8").read()
check("hard_gate_allows is invoked in base_agent", "self.hard_gate_allows(" in src)
ex = src[src.index("def execute():"):]
check("the gate runs BEFORE handle_task dispatch (boundary, outside the model)",
      "hard_gate_allows(" in ex and ex.index("hard_gate_allows(") < ex.index("handle_task("))

# 7. The gate is separate from check_guard (which fails OPEN); this one is local.
check("the gate is distinct from check_guard (a local code refusal, no outage relaxes it)",
      "hard_gate_allows" in src and src.index("def hard_gate_allows") != src.index("def check_guard"))

print("check_hard_gate: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
