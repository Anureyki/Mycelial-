#!/usr/bin/env python3
"""Build gate: a held action is released only by the principal's phone signature.

End to end, in temp directories (spool, records, hold log, enrolled key) so nothing
touches the live stores: a simulated phone key (P-256, raw r||s signatures exactly as
WebCrypto produces them), the real Tool Service app against a loopback fake MCP
server, the real Security Agent verbs, then harness ingest and the drift scan.

Proves, each as a refusal that leaves the hold open and sends nothing:
  no key enrolled; a second enrolment; a wrong key; a signature for another hold;
  a signature over different arguments; an expired or over-long release; a replay;
  an envelope not signed by Security; a genuine Security envelope carrying a bad
  principal signature (Tool Service checks him itself).
And that the one valid path releases exactly one hold - in dry run, still sending
nothing - and that the drift monitor raises refused releases, self-release, an
unread queue and an enrolment, and stays silent on a clean history.
"""
import base64
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "tool"))
TMP = tempfile.mkdtemp()
os.environ["MYCELIAL_EVENT_ORIGIN"] = "production"   # isolated temp stores; see below

import core.security_events as se            # noqa: E402
se.SPOOL = os.path.join(TMP, "spool")
import core.principal_release as pr          # noqa: E402
pr.KEY_PATH = os.path.join(TMP, "principal_key.json")
import service as ts                         # noqa: E402
ts.HELD_LOG = os.path.join(TMP, "held.jsonl")
ts.log_to_audit = lambda *a, **k: None
ts.store_memory = lambda *a, **k: None
from cryptography.hazmat.primitives import hashes, serialization          # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec                  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa: E402
from core.sandbox.signing import sign        # noqa: E402

fails, seen = [], []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        fails.append(name)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        seen.append(body.get("method"))
        out = {"jsonrpc": "2.0", "id": body.get("id"), "result": {"content": [{"type": "text", "text": "ok"}]}}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode())


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["GATE_KEY"] = "k"
ts.servers = {"fake": {"url": f"http://127.0.0.1:{srv.server_address[1]}/mcp",
                       "headers": {"Authorization": "Bearer ${GATE_KEY}"},
                       "guard": {"mode": "dry_run", "principal_phone_env": "NOPE", "read_tools": [],
                                 "write_tools": [], "contact_targets": {}, "contact_untargeted": [],
                                 "held_tools": {"delete": ["delete_agent"]}}}}
client = ts.app.test_client()


def tool(task, args, sender="security_agent"):
    return client.post("/execute", json={"task": task, "args": args, "sender": sender}).get_json()


from agents.security_agent.security_agent import SecurityAgent  # noqa: E402
sec = object.__new__(SecurityAgent)
sec.log = lambda *a, **k: None
sec._tool = lambda task, args: tool(task, args)


def phone():
    k = ec.generate_private_key(ec.SECP256R1())
    spki = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.DER,
                            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    return k, spki


def raw_sign(k, payload):
    r, s = decode_dss_signature(k.sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def release_payload(h, minutes=10, **over):
    now = datetime.now(timezone.utc)
    p = {"kind": "hold_release", "hold_id": h["id"], "server": h["server"], "tool": h["tool"],
         "args_sha256": h["args_sha256"], "issued_at": now.isoformat(),
         "expires_at": (now + timedelta(minutes=minutes)).isoformat()}
    p.update(over)
    return json.dumps(p)


def hold(args):
    r = tool("call_tool", ["fake", "delete_agent", json.dumps(args)], sender="anansi")["result"]
    return next(h for h in tool("list_held", [])["result"] if h["id"] == r["id"])


def open_ids():
    return {h["id"] for h in tool("list_held", [])["result"]}


him, him_spki = phone()
thief, thief_spki = phone()
A = hold({"id": "agent-a"})
B = hold({"id": "agent-b"})
n0 = len(seen)

r = sec.release_hold({"payload": release_payload(A), "signature": raw_sign(him, release_payload(A))})
check("with no key enrolled, nothing can be released", not r["released"] and "no principal key" in r["why"])
check("enrolment accepts his phone's public key", sec.enroll_principal_key.__func__ and
      pr.enroll(him_spki, "phone").get("enrolled"))
check("a second enrolment is refused - write-once", "write-once" in (pr.enroll(thief_spki).get("error") or ""))

p = release_payload(A)
r = sec.release_hold({"payload": p, "signature": raw_sign(thief, p)})
check("a release signed by any other key is refused", not r["released"] and "does not verify" in r["why"])
pb = release_payload(B)
r = sec.release_hold({"payload": pb.replace(B["id"], A["id"]), "signature": raw_sign(him, pb)})
check("his signature for hold B cannot release hold A", not r["released"])
p = release_payload(A, args_sha256="0" * 64)
r = sec.release_hold({"payload": p, "signature": raw_sign(him, p)})
check("his signature over different arguments is refused", not r["released"] and "args_sha256" in r["why"])
p = release_payload(A, minutes=-1)
r = sec.release_hold({"payload": p, "signature": raw_sign(him, p)})
check("an expired release is refused", not r["released"] and "expired" in r["why"])
p = release_payload(A, minutes=60)
r = sec.release_hold({"payload": p, "signature": raw_sign(him, p)})
check("a release valid for an hour is refused", not r["released"] and "longer than" in r["why"])

p = release_payload(A)
forged = sign("anansi", {"hold_id": A["id"], "principal_payload": p, "principal_signature": raw_sign(him, p)},
              recipient="tool_service", kind="hold_release", ttl=300)
r = tool("resolve_held", [A["id"], "approve", forged], sender="anansi")
check("Tool Service refuses an envelope not signed by Security", "approve refused" in (r.get("error") or ""))
p = release_payload(A)
bad = sign("security_agent", {"hold_id": A["id"], "principal_payload": p, "principal_signature": raw_sign(thief, p)},
           recipient="tool_service", kind="hold_release", ttl=300)
r = tool("resolve_held", [A["id"], "approve", bad])
check("Tool Service refuses a genuine Security envelope with a bad principal signature - it checks him itself",
      "principal's signature" in (r.get("error") or ""))
r = tool("resolve_held", [A["id"], "approve"], sender="principal")
check("a bare approve with a self-reported sender is refused", "approve refused" in (r.get("error") or ""))
check("every refusal left both holds open and sent nothing", {A["id"], B["id"]} <= open_ids() and len(seen) == n0)

p = release_payload(A)
r = sec.release_hold({"payload": p, "signature": raw_sign(him, p), "_relayed_by": "anansi"})
check("his phone's signature releases the hold", r["released"] and r["hold_id"] == A["id"])
check("in dry run the released action still sends nothing", (r.get("outcome") or {}).get("dry_run") and len(seen) == n0)
check("only that hold closed", A["id"] not in open_ids() and B["id"] in open_ids())
r = sec.release_hold({"payload": p, "signature": raw_sign(him, p)})
check("replaying the same release is refused", not r["released"])

r = sec.deny_hold({"hold_id": B["id"]})
check("deny needs no signature and closes the hold", r["denied"] and B["id"] not in open_ids())
srv.shutdown()

# ---- the harness records it, and the drift monitor reads it ----
from tools.eval_harness import ingest  # noqa: E402
from core.drift_monitor import scan    # noqa: E402
recs = os.path.join(TMP, "records")
ingest(spool=se.SPOOL, records=recs, quarantine=True)
rows = []
for f in os.listdir(recs):
    if f.endswith(".jsonl"):
        rows += [json.loads(x) for x in open(os.path.join(recs, f)) if x.strip()]
kinds = {r.get("event_type") for r in rows}
check("holds, refusals, the release and the deny all reach the record",
      {"hold_created", "hold_release_refused", "hold_released", "hold_denied"} <= kinds)
check("who requested and who attempted travel as fields",
      any((r.get("extra") or {}).get("requested_by") == "anansi" for r in rows if r.get("event_type") == "hold_created"))
alerts = {a["kind"] for a in scan(records=recs)["alerts"]}
check("the drift monitor raises the refused release attempts", "unbound_release_attempt" in alerts)
sr = [a for a in scan(records=recs)["alerts"] if a["kind"] == "self_release"]
check("the forged envelope relayed by the requester itself is caught as self-release, naming it",
      sr and sr[0]["detail"]["agents"] == ["anansi"])

# A clean history: holds created, released by Security on a valid signature, denied - no alerts.
clean_spool, clean_recs = os.path.join(TMP, "s2"), os.path.join(TMP, "r2")
for i in range(3):
    rec = {"id": f"c{i}", "server": "fake", "tool": "delete_agent", "requested_by": "anansi"}
    se.emit("hold_created", agent="anansi", resource="hold:fake.delete_agent", decision="held",
            reason="held, not run: delete needs a yes", extra={"hold_id": rec["id"], "requested_by": "anansi"},
            spool=clean_spool)
    se.emit("hold_released" if i % 2 else "hold_denied", agent="security_agent", resource="hold:fake.delete_agent",
            decision="allowed" if i % 2 else "denied", reason="resolved by the principal on his phone",
            extra={"hold_id": rec["id"], "requested_by": "anansi", "attempted_by": "security_agent"},
            spool=clean_spool)
ingest(spool=clean_spool, records=clean_recs, quarantine=True)
hold_kinds = {"unbound_release_attempt", "self_release", "hold_queue_unread", "principal_key_enrolled"}
check("silent on a clean history of holds", not ({a["kind"] for a in scan(records=clean_recs)["alerts"]} & hold_kinds))

# Self-release, an unread queue, and an enrolment, each on its own case.
bad_spool, bad_recs = os.path.join(TMP, "s3"), os.path.join(TMP, "r3")
for i in range(5):
    se.emit("hold_created", agent="anansi", resource="hold:fake.send_call", decision="held",
            reason="held, not run: third-party contact", extra={"hold_id": f"q{i}", "requested_by": "anansi"},
            spool=bad_spool)
se.emit("hold_release_refused", agent="anansi", resource="hold:fake.send_call", decision="denied",
        reason="release refused: no release was presented",
        extra={"hold_id": "q0", "requested_by": "anansi", "attempted_by": "anansi"}, spool=bad_spool)
se.emit("principal_key_enrolled", agent="security_agent", resource="principal_key", decision="allowed",
        reason="a release key was enrolled, fingerprint ABCD", extra={"fingerprint": "ABCD"}, spool=bad_spool)
ingest(spool=bad_spool, records=bad_recs, quarantine=True)
got = {a["kind"] for a in scan(records=bad_recs)["alerts"]}
check("the agent that asked trying to release its own hold is critical", "self_release" in got)
check("five open holds raise an unread-queue alert", "hold_queue_unread" in got)
check("an enrolment is raised so he can confirm it was him", "principal_key_enrolled" in got)

print("check_hold_release: " + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
