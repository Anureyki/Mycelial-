#!/usr/bin/env python3
# agents/security_agent/security_agent.py
import os, json, time, secrets, uuid
from core.base_agent import AgentBase

BASE = os.path.expanduser("~/mycelial")
SECRET_FILE = os.path.join(BASE, "config", ".security_bootstrap_secret")
FINDINGS_FILE = os.path.join(BASE, "state", "security_findings.json")
VALID_SEVERITIES = ("low", "medium", "high", "critical")

class SecurityAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="security_agent",
            port=9010,
            capabilities=["authenticate", "authorize", "audit", "issue_token",
                          "flag_finding", "list_findings", "resolve_finding"],
            role="security"
        )
        self.tokens = {}  # simple in-memory token store (persist later)
        self.bootstrap_secret = self._load_or_create_bootstrap_secret()
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
