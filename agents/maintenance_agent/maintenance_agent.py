#!/usr/bin/env python3
import sys
import os
import time
import json
import subprocess

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

# Docker-compose-managed containers this agent is allowed to maintain.
# Deliberately a fixed whitelist (not a caller-supplied path) so update_container
# can't be used to run compose against an arbitrary directory.
CONTAINER_REGISTRY = {
    "pihole-unbound": "/opt/pihole",
}

class MaintenanceAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="maintenance_agent",
            port=8003,
            capabilities=[
                "check_disk", "clean_logs", "check_updates",
                "apply_updates", "rollback", "check_errors",
                "check_container_updates", "update_container"
            ],
            role="system_health"
        )
        self.log("🛠️ Maintenance agent started with real system commands.")

    def _compose(self, container, *compose_args):
        """Run `docker compose <compose_args>` in a whitelisted container's directory."""
        compose_dir = CONTAINER_REGISTRY.get(container)
        if not compose_dir:
            return {"error": f"Unknown container: {container}"}
        try:
            result = subprocess.run(
                ["docker", "compose", *compose_args],
                cwd=compose_dir, capture_output=True, text=True, timeout=180
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

    def _execute_local(self, command):
        """Execute a command locally with a safe prefix whitelist."""
        safe_prefixes = ('df', 'du', 'ls', 'cat', 'echo', 'find', 'grep', 'wc', 'apt', 'crontab')
        if not any(command.startswith(p) for p in safe_prefixes):
            self.log(f"⚠️ Command not allowed: {command[:50]}")
            return {"stdout": "", "stderr": "Command not allowed", "returncode": 1}
        try:
            result = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=30
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "check_disk":
            path = args.get("path", "/")
            expanded = os.path.expanduser(path)
            result = self._execute_local(f"df -h {expanded}")
            return {"result": result.get("stdout", "No output")}

        elif task == "clean_logs":
            log_dir = args.get("log_dir", os.path.expanduser("~/mycelial/logs"))
            max_age_days = args.get("max_age_days", 7)
            cmd = f"find {log_dir} -name '*.log' -type f -mtime +{max_age_days} -delete 2>/dev/null"
            result = self._execute_local(cmd)
            return {"result": "Cleaned old logs" if result.get("returncode") == 0 else "Failed"}

        elif task == "check_updates":
            result = self._execute_local("apt list --upgradable 2>/dev/null")
            lines = result.get("stdout", "").splitlines()
            upgradable = [l for l in lines if l and "upgradable" in l]
            return {"upgradable_count": len(upgradable), "packages": upgradable}

        elif task == "apply_updates":
            if args.get("confirm") != True:
                return {"error": "Confirmation required (confirm=true)"}
            result = self._execute_local("apt update && apt upgrade -y")
            return {"result": "System updated" if result.get("returncode") == 0 else "Failed"}

        elif task == "rollback":
            return {"result": "Rollback not implemented"}

        elif task == "check_container_updates":
            container = args.get("container")
            if container not in CONTAINER_REGISTRY:
                return {"error": f"Unknown container: {container}", "known": list(CONTAINER_REGISTRY)}
            before = self._compose(container, "images", "-q")
            pull = self._compose(container, "pull")
            after = self._compose(container, "images", "-q")
            changed = before.get("stdout") != after.get("stdout")
            return {
                "container": container,
                "update_available": changed,
                "pull_output": pull.get("stdout", "") + pull.get("stderr", "")
            }

        elif task == "update_container":
            if args.get("confirm") != True:
                return {"error": "Confirmation required (confirm=true)"}
            container = args.get("container")
            if container not in CONTAINER_REGISTRY:
                return {"error": f"Unknown container: {container}", "known": list(CONTAINER_REGISTRY)}
            pull = self._compose(container, "pull")
            if pull.get("returncode") != 0:
                return {"error": "Pull failed", "detail": pull.get("stderr")}
            up = self._compose(container, "up", "-d")
            self.log_to_audit("CONTAINER_UPDATE", f"Updated {container}",
                              metadata={"pull": pull.get("stdout", "")[:200]})
            return {
                "container": container,
                "result": "updated" if up.get("returncode") == 0 else "failed",
                "detail": up.get("stdout", "") + up.get("stderr", "")
            }

        elif task == "check_errors":
            org = args.get("org")
            project = args.get("project")
            if not org or not project:
                return {"error": "Missing org or project"}

            self.log(f"Checking Sentry errors for {org}/{project}")
            tool_names = ["list_issues", "get_issues", "list-issues", "listIssues"]
            args_variants = [
                {"organization": org, "project": project},
                {"orgSlug": org, "projectSlug": project},
                {"org": org, "project": project},
            ]
            result = None
            for tool in tool_names:
                for variant in args_variants:
                    try:
                        res = self.call_tool("sentry", tool, variant)
                        if res and not (isinstance(res, dict) and res.get("error")):
                            result = res
                            break
                    except:
                        continue
                if result:
                    break
            if not result:
                return {"error": "Sentry check failed"}
            self.log_to_audit("SENTRY_CHECK", f"Checked errors for {org}/{project}",
                              metadata={"result": str(result)[:200]})
            return {"result": result, "org": org, "project": project}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = MaintenanceAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
