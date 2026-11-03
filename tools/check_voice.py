#!/usr/bin/env python3
"""Build gate for Anansi's persona (config/anansi_voice.json, agents/anansi/voice.py).

The persona the principal set on 2026-11-03: Anansi is the only voice the user
hears; it leads with what matters, then gives the numbers; accuracy is
non-negotiable. This proves the parts that can be proved:

  - the persona text is in the config, so it can change without a code edit
  - point first: a telling that opens with raw data and carries a practical
    point leads with the point - with its label, so it keeps its subject
  - reordering never drops, adds or alters a number, date, unit or citation
  - a point leaning on "it" with no label to carry it is left in place
  - a telling that already leads with narrative is not reordered
  - only Anansi can reach the principal's inbox: nothing else under agents/,
    core/ or services/ sends mail
"""
import os
import sys

os.environ["MYCELIAL_EVENT_ORIGIN"] = "test"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agents.anansi.voice import Voice, facts_in  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


v = Voice(log=lambda *a, **k: None)
cfg = v.cfg
check("persona text is held in config", "only voice the user hears" in (cfg.get("persona") or {}).get("text", ""))

water = ("GSC-2: Projected about 14.2 L now, falling 3.1 L a day - reaches 12 L in about 18 h. "
         "Top up only to hold until it is changed.")
out = v.tell(water)
check("point first, label carried: the instruction leads with its plant", out.startswith("GSC-2: Top up only"))
check("every number survives the reordering", facts_in(water) == facts_in(out))

rent = "Obligation rent: $550 due 2026-11-05 from the trust account. Pay it from the trust account, not personal funds."
out = v.tell(rent)
check("a point leaning on 'it' moves only with the label that names it",
      out.startswith("Obligation rent: Pay it") and facts_in(rent) == facts_in(out))

bare = "Read 896 ppm / EC 1799 / pH 6.06 at 16 L. Top it up with plain water."
check("no label to carry: a point leaning on 'it' stays in place", v.tell(bare).startswith("Read 896 ppm"))

story = "The reservoir is drifting and pH has moved 0.3 since Monday. Check the probe before dosing anything."
check("a telling that already leads with narrative is not reordered", v.tell(story).startswith("The reservoir"))

senders = []
for top in ("agents", "core", "services"):
    for dp, dn, fs in os.walk(os.path.join(ROOT, top)):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fs:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(dp, f), ROOT)
                src = open(os.path.join(dp, f), encoding="utf-8", errors="replace").read()
                if rel != "core/effects.py" and any(k in src for k in ("agentmail.to", "import smtplib", "sendmail(")):
                    senders.append(rel)
check("only Anansi can send to the principal" + (f" (found: {senders})" if senders != ["agents/anansi/Anansi.py"] else ""),
      senders == ["agents/anansi/Anansi.py"])

# The email authorization on Anansi's card (principal, 2026-11-03): recipient
# fixed to NOTIFY_TO, third-party sends refused. A declaration the code could
# drift away from is a claim; this keeps it a fact.
import re  # noqa: E402
src = open(os.path.join(ROOT, "agents", "anansi", "Anansi.py"), encoding="utf-8").read()
recips = re.findall(r'(?:"to"\s*:|\["To"\]\s*=)\s*([^,\n}]+)', src)
check("every Anansi send names NOTIFY_TO as its recipient, and nothing else"
      + (f" (found: {recips})" if any("NOTIFY_TO" not in r for r in recips) else ""),
      recips and all('os.environ["NOTIFY_TO"]' in r for r in recips))
check("one AgentMail send path and one SMTP path, both in _deliver",
      src.count("/messages/send") == 1 and src.count("smtplib.SMTP(") == 1)
import json as _json  # noqa: E402
card = _json.load(open(os.path.join(ROOT, "config", "agent_cards", "anansi.json"), encoding="utf-8"))
ext = sorted(v for v, k in (card.get("effects") or {}).items() if k == "external")
check("each external Anansi verb is gated by the email authorization",
      ext and all("email_authorization" in str((card.get("effects_gated_by") or {}).get(v)) for v in ext)
      and (card.get("email_authorization") or {}).get("recipient") == "fixed to NOTIFY_TO")

print("check_voice: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
