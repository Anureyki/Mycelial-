#!/usr/bin/env python3
# agents/security_agent/security_agent.py
import os, json, time, secrets, uuid, fnmatch, shutil
from datetime import datetime
from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
SECRET_FILE = os.path.join(BASE, "config", ".security_bootstrap_secret")
FINDINGS_FILE = os.path.join(BASE, "state", "security_findings.json")
GUARDS_FILE = os.path.join(BASE, "config", "guards.json")
LOCK_FILE = os.path.join(BASE, "state", "LOCKED")
QUARANTINE_DIR = os.path.join(BASE, "quarantine")
BLOCKLIST_FILE = os.path.join(BASE, "state", "blocklist.txt")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")
VALID_SEVERITIES = ("low", "medium", "high", "critical")

class SecurityAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="security_agent",
            port=9010,
            capabilities=["authenticate", "authorize", "audit", "issue_token",
                          "flag_finding", "list_findings", "resolve_finding",
                          "check_guard", "reload_guards", "quarantine", "eliminate"],
            role="security"
        )
        self.tokens = {}  # simple in-memory token store (persist later)
        self.bootstrap_secret = self._load_or_create_bootstrap_secret()
        self.guards = self._load_guards()
        self.policies = {
            "coding_agent": ["run_command", "edit_file", "read_file"],
            "grow_agent": ["log_reading", "transition_stage"],
            # KAG / relationship graph: only Boss and the relationship-modeling
            # agents may write to the graph. query_graph is read-only (and is
            # further restricted server-side in core/graph_manager.py to SELECT
            # statements against a read-only connection regardless of policy),
            # so it's granted broadly to anything that needs to reason over the
            # graph. Fine-grained per-project/per-entity visibility is NOT
            # implemented yet - that needs a project-ownership model this
            # system doesn't have - so treat this as "can touch the graph at
            # all", not "can see every relationship in it".
            "boss_agent": ["update_graph", "query_graph"],
            "legal_agent": ["update_graph", "query_graph"],
            "accounting_agent": ["update_graph", "query_graph"],
            "trust_agent": ["update_graph", "query_graph"],
            # ... more policies
        }
        self.log("🔐 Security Agent started.")

    def _load_or_create_bootstrap_secret(self):
        """Only callers who can read this local, 0600 file may mint tokens.
        Prevents any network caller from self-issuing a token for an
        arbitrary agent_id and having authorize() approve it."""
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, "r") as f:
                return f.read().strip()
        secret = secrets.token_urlsafe(32)
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secret)
        self.log(f"Generated new bootstrap secret at {SECRET_FILE}")
        return secret

    def _issue_token(self, agent_id, ttl=3600):
        token = secrets.token_urlsafe(32)
        expiry = time.time() + ttl
        self.tokens[token] = {"agent_id": agent_id, "expiry": expiry}
        return token

    def _validate_token(self, token):
        if token not in self.tokens:
            return None
        if time.time() > self.tokens[token]["expiry"]:
            del self.tokens[token]
            return None
        return self.tokens[token]["agent_id"]

    def _load_guards(self):
        """Resource guards, replacing the old hooks/pre_*.sh scripts.

        Deliberately separate from self.policies: policies is an *allowlist*
        keyed by agent_id, and most agents aren't in it, so using it to gate
        every request would deny the majority of the swarm. Guards are a
        *denylist* - anything with no matching rule is allowed through."""
        if os.path.exists(GUARDS_FILE):
            try:
                with open(GUARDS_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                # A malformed guards file must not take the swarm down, but it
                # also must not silently disable every guard - say so loudly.
                self.log(f"⚠️  Could not read {GUARDS_FILE} ({e}); running with NO deny rules.")
                return {"deny": []}
        return {"deny": []}

    def _guard_matches(self, rule, agent, action, target):
        """A rule matches when every field it specifies matches. Absent fields
        and "*" both mean 'any'."""
        if not fnmatch.fnmatch(agent, rule.get("agent", "*")):
            return False
        if not fnmatch.fnmatch(action, rule.get("task", "*")):
            return False
        target_glob = rule.get("target_glob")
        if target_glob:
            # A rule scoped to a path can only fire when we were given one.
            if not target:
                return False
            if not fnmatch.fnmatch(target, target_glob):
                return False
        return True

    def _load_findings(self):
        if os.path.exists(FINDINGS_FILE):
            with open(FINDINGS_FILE, "r") as f:
                return json.load(f)
        return []

    def _save_findings(self, findings):
        os.makedirs(os.path.dirname(FINDINGS_FILE), exist_ok=True)
        with open(FINDINGS_FILE, "w") as f:
            json.dump(findings, f, indent=2)

    def handle_task(self, task, args, sender):
        if task == "issue_token":
            agent_id = args.get("agent_id")
            bootstrap_secret = args.get("bootstrap_secret")
            if not agent_id:
                return {"error": "Missing agent_id"}
            if bootstrap_secret != self.bootstrap_secret:
                self.log(f"Rejected issue_token for {agent_id}: bad or missing bootstrap secret")
                return {"error": "Invalid or missing bootstrap secret"}
            token = self._issue_token(agent_id)
            expiry = self.tokens[token]["expiry"]
            self.store_own_memory(f"token_{token[:8]}", {"agent_id": agent_id, "expiry": expiry})
            return {"token": token, "expiry": expiry}

        elif task == "authenticate":
            token = args.get("token")
            agent_id = self._validate_token(token)
            if agent_id:
                return {"authenticated": True, "agent_id": agent_id}
            return {"authenticated": False, "error": "Invalid or expired token"}

        elif task == "authorize":
            token = args.get("token")
            action = args.get("action")
            agent_id = self._validate_token(token)
            if not agent_id:
                return {"authorized": False, "error": "Authentication required"}
            allowed = self.policies.get(agent_id, [])
            return {"authorized": action in allowed}

        elif task == "check_guard":
            # Tokenless on purpose: this is called by core.base_agent on every
            # inbound /execute, before the caller has done anything. Requiring a
            # token here would mean every agent needs the bootstrap secret just
            # to serve a request. Authorization-by-capability stays in
            # "authorize" above; this is the resource guard layer.
            agent = args.get("agent", "unknown")
            action = args.get("task", "")
            target = args.get("target", "") or ""

            # Global kill switch. The old hooks/pre_action.sh wrote and read a
            # state/LOCKED file that no Python ever honoured; now it's real.
            if os.path.exists(LOCK_FILE):
                return {"allowed": False, "reason": "System is LOCKED pending owner review"}

            for rule in self.guards.get("deny", []):
                if self._guard_matches(rule, agent, action, target):
                    reason = rule.get("reason", "denied by guard")
                    self.log(f"GUARD DENY {agent}/{action} on {target or '-'}: {reason}")
                    return {"allowed": False, "reason": reason}

            return {"allowed": True, "reason": "no matching deny rule"}

        elif task == "reload_guards":
            # config/guards.json is the kind of file that goes stale silently.
            # Being able to reload it without restarting the swarm is what keeps
            # it maintained rather than abandoned.
            self.guards = self._load_guards()
            count = len(self.guards.get("deny", []))
            self.log(f"Reloaded guards: {count} deny rule(s)")
            return {"result": "Guards reloaded", "deny_rules": count}

        elif task == "quarantine":
            # Ported from the retired hooks/quarantine.sh.
            file_path = args.get("file")
            reason = args.get("reason")
            if not file_path or not reason:
                return {"error": "quarantine requires 'file' and 'reason'"}
            file_path = os.path.expanduser(file_path)
            if not os.path.isfile(file_path):
                return {"error": f"File {file_path} does not exist"}

            os.makedirs(QUARANTINE_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            dest = os.path.join(QUARANTINE_DIR, f"{os.path.basename(file_path)}_{stamp}")
            shutil.move(file_path, dest)
            os.chmod(dest, 0o444)  # read-only: quarantined, not executable

            with open(f"{dest}.meta", "w") as f:
                json.dump({
                    "original_path": file_path,
                    "reason": reason,
                    "quarantined_at": datetime.now().isoformat(),
                    "quarantined_by": sender,
                }, f, indent=2)

            self.log_to_audit("quarantine", f"{file_path} -> {dest}", level="warning",
                              event_type="QUARANTINE",
                              metadata={"file": file_path, "reason": reason, "destination": dest})
            return {"result": "File quarantined", "destination": dest}

        elif task == "eliminate":
            # Ported from hooks/eliminate.sh, which used an interactive `read -r`
            # double-confirm. A service has no TTY, so approval moves to a file in
            # state/pending_requests/ that a human must flip to "approved".
            # Nothing currently reads that directory, so the safe default is to
            # REFUSE and wait rather than assume consent.
            file_path = args.get("file")
            reason = args.get("reason")
            if not file_path or not reason:
                return {"error": "eliminate requires 'file' and 'reason'"}
            file_path = os.path.expanduser(file_path)
            if not os.path.isfile(file_path):
                return {"error": f"File {file_path} does not exist"}

            approval_id = args.get("approval_id")
            if not approval_id:
                request_id = self.request_permission(
                    target="owner", task="eliminate",
                    args={"file": file_path, "reason": reason,
                          "source_url": args.get("source_url", "unknown")})
                return {
                    "allowed": False,
                    "result": "Approval required - nothing was deleted",
                    "approval_id": request_id,
                    "instructions": (
                        f"Set \"status\": \"approved\" in state/pending_requests/{request_id}.json, "
                        f"then call eliminate again with approval_id={request_id}"
                    ),
                }

            approval_file = os.path.join(PENDING_DIR, f"{approval_id}.json")
            if not os.path.exists(approval_file):
                return {"error": f"No such approval request: {approval_id}"}
            with open(approval_file, "r") as f:
                approval = json.load(f)
            if approval.get("status") != "approved":
                return {"allowed": False,
                        "error": f"Request {approval_id} is '{approval.get('status')}', not 'approved'"}
            if approval.get("args", {}).get("file") != file_path:
                # Stops an approval for one file being replayed against another.
                return {"error": "Approval does not match the requested file"}

            source_url = args.get("source_url", approval.get("args", {}).get("source_url", "unknown"))
            if source_url and source_url != "unknown":
                os.makedirs(os.path.dirname(BLOCKLIST_FILE), exist_ok=True)
                with open(BLOCKLIST_FILE, "a") as f:
                    f.write(f"{source_url}\n")

            os.remove(file_path)
            approval["status"] = "executed"
            with open(approval_file, "w") as f:
                json.dump(approval, f, indent=2)

            self.log_to_audit("eliminate", f"Deleted {file_path}", level="warning",
                              event_type="ELIMINATE",
                              metadata={"file": file_path, "reason": reason,
                                        "source": source_url, "approval_id": approval_id})
            return {"result": "File eliminated", "file": file_path, "blocklisted": source_url}

        elif task == "audit":
            # log an audit event (store in Hermes)
            entry = {
                "timestamp": time.time(),
                "agent": args.get("agent"),
                "action": args.get("action"),
                "result": args.get("result"),
                "details": args.get("details", {})
            }
            self.store_own_memory(f"audit_{int(time.time())}", json.dumps(entry))
            return {"result": "Audit logged"}

        elif task == "flag_finding":
            # Lets any agent (or a human/Claude reviewing the codebase) record
            # a security/config issue once, persistently, so it doesn't need
            # to be rediscovered by re-auditing the repo from scratch next time.
            summary = args.get("summary")
            if not summary:
                return {"error": "Missing summary"}
            severity = args.get("severity", "medium")
            if severity not in VALID_SEVERITIES:
                return {"error": f"severity must be one of {VALID_SEVERITIES}"}
            finding = {
                "id": uuid.uuid4().hex[:12],
                "severity": severity,
                "summary": summary,
                "location": args.get("location"),
                "recommendation": args.get("recommendation"),
                "reporter": args.get("reporter", sender),
                "status": "open",
                "flagged_at": time.time(),
                "resolved_at": None,
                "resolution_note": None,
            }
            findings = self._load_findings()
            findings.append(finding)
            self._save_findings(findings)
            self.log_to_audit("SECURITY_FINDING", summary, level="warning" if severity in ("high", "critical") else "info",
                               metadata=finding)
            return {"result": "Finding flagged", "finding": finding}

        elif task == "list_findings":
            status = args.get("status", "open")  # "open" | "resolved" | "all"
            severity = args.get("severity")
            findings = self._load_findings()
            if status != "all":
                findings = [f for f in findings if f["status"] == status]
            if severity:
                findings = [f for f in findings if f["severity"] == severity]
            return {"findings": findings, "count": len(findings)}

        elif task == "resolve_finding":
            finding_id = args.get("finding_id")
            if not finding_id:
                return {"error": "Missing finding_id"}
            findings = self._load_findings()
            for f in findings:
                if f["id"] == finding_id:
                    f["status"] = "resolved"
                    f["resolved_at"] = time.time()
                    f["resolution_note"] = args.get("resolution_note")
                    self._save_findings(findings)
                    return {"result": "Finding resolved", "finding": f}
            return {"error": f"No finding with id {finding_id}"}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = SecurityAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
