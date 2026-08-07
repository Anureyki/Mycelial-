#!/usr/bin/env python3
import sys
import os
import time
import json
import subprocess
from datetime import datetime, timedelta

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Below this many days-to-full at the current trend, a disk-space predictive
# check flags it as worth attention now rather than waiting for it to
# actually fill.
DISK_FULL_WARNING_DAYS = 14

# Docker-compose-managed containers this agent is allowed to maintain.
# Deliberately a fixed whitelist (not a caller-supplied path) so update_container
# can't be used to run compose against an arbitrary directory.
CONTAINER_REGISTRY = {
    "pihole-unbound": "/opt/pihole",
}

# Docker cleanup: anything whose tag mentions the platform itself, or that's
# referenced by this repo's own compose/Dockerfile, counts as "major infra or
# an active project" and gets held for confirmation rather than auto-cleared -
# even with zero running containers (e.g. a rebuildable-but-not-currently-
# running deployment image). Pure build byproducts (dangling <none> layers,
# build cache) are never "the thing itself," so those are always safe to clear.
KNOWN_PROJECT_MARKERS = ("mycelial",)
REPO_ROOT = os.path.expanduser("~/mycelial")
PROJECT_REFERENCE_FILES = ("docker-compose.yml", "Dockerfile")

class MaintenanceAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="maintenance_agent",
            port=8003,
            capabilities=[
                "check_disk", "clean_logs", "check_updates",
                "apply_updates", "rollback", "check_errors",
                "check_container_updates", "update_container",
                "sample_telemetry", "get_telemetry_history", "predict_disk_full",
                "scan_unused_docker_resources", "run_cleanup_routine"
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

    # ---------- Telemetry storage ----------
    def _unwrap_value(self, retrieval_result):
        if not isinstance(retrieval_result, dict):
            return None
        result = retrieval_result.get("result")
        if not isinstance(result, dict):
            return None
        entry = result.get("entry")
        if not isinstance(entry, dict):
            return None
        return entry.get("value")

    def _load_telemetry_index(self):
        raw = self._unwrap_value(self.retrieve_own_memory("telemetry_index"))
        if not raw:
            return []
        try:
            index = json.loads(raw)
            return index if isinstance(index, list) else []
        except Exception:
            return []

    def _get_telemetry_history(self, limit=None):
        samples = []
        for key in self._load_telemetry_index():
            raw = self._unwrap_value(self.retrieve_own_memory(key))
            if not raw:
                continue
            try:
                samples.append(json.loads(raw))
            except Exception:
                pass
        samples.sort(key=lambda s: s.get("timestamp", ""))
        return samples[-limit:] if limit else samples

    # ---------- Docker cleanup: classify before clearing ----------
    def _docker_images(self):
        try:
            result = subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}"],
                capture_output=True, text=True, timeout=30
            )
            images = []
            for line in result.stdout.splitlines():
                parts = line.split("|")
                if len(parts) == 3:
                    images.append({"tag": parts[0], "id": parts[1], "size": parts[2]})
            return images
        except Exception as e:
            self.log(f"docker images failed: {e}")
            return []

    def _docker_containers_using(self, image_id):
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"ancestor={image_id}", "--format", "{{.ID}}"],
                capture_output=True, text=True, timeout=15
            )
            return [c for c in result.stdout.splitlines() if c.strip()]
        except Exception:
            return []

    def _is_project_referenced(self, tag):
        lowered = tag.lower()
        if any(marker in lowered for marker in KNOWN_PROJECT_MARKERS):
            return True
        repo_name = tag.split(":")[0]
        if repo_name in CONTAINER_REGISTRY:
            return True
        for fname in PROJECT_REFERENCE_FILES:
            fpath = os.path.join(REPO_ROOT, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath) as f:
                        if repo_name in f.read():
                            return True
                except Exception:
                    pass
        return False

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "check_disk":
            path = args.get("path", "/")
            expanded = os.path.expanduser(path)
            result = self._execute_local(f"df -h {expanded}")
            return {"result": result.get("stdout", "No output")}

        elif task == "sample_telemetry":
            if not PSUTIL_AVAILABLE:
                return {"error": "psutil not installed"}
            path = args.get("path", "/") if isinstance(args, dict) else "/"
            disk = psutil.disk_usage(os.path.expanduser(path))
            sample = {
                "timestamp": datetime.now().isoformat(),
                "path": path,
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024 ** 3), 2),
                "disk_total_gb": round(disk.total / (1024 ** 3), 2),
            }
            key = f"telemetry_{int(time.time())}"
            self.store_own_memory(key, json.dumps(sample))
            index = self._load_telemetry_index()
            index.append(key)
            self.store_own_memory("telemetry_index", json.dumps(index))
            return {"result": sample}

        elif task == "get_telemetry_history":
            limit = args.get("limit", 20) if isinstance(args, dict) else 20
            return {"result": self._get_telemetry_history(limit=limit)}

        elif task == "predict_disk_full":
            # Deterministic trend scan, not a point-in-time reading - projects
            # days-to-full from the change in free space across stored samples.
            path = args.get("path", "/") if isinstance(args, dict) else "/"
            history = [s for s in self._get_telemetry_history() if s.get("path") == path]
            if len(history) < 2:
                return {
                    "result": {
                        "path": path,
                        "days_to_full": None,
                        "note": "Not enough telemetry samples yet to compute a trend - need at least 2 (call sample_telemetry periodically).",
                    }
                }
            first, last = history[0], history[-1]
            elapsed_days = (datetime.fromisoformat(last["timestamp"]) - datetime.fromisoformat(first["timestamp"])).total_seconds() / 86400
            free_change_gb = first["disk_free_gb"] - last["disk_free_gb"]  # positive = shrinking
            if elapsed_days <= 0 or free_change_gb <= 0:
                return {
                    "result": {
                        "path": path,
                        "current_disk_percent": last["disk_percent"],
                        "days_to_full": None,
                        "warning": False,
                        "note": "Disk usage isn't trending toward full based on recent samples.",
                    }
                }
            shrink_rate_gb_per_day = free_change_gb / elapsed_days
            days_to_full = last["disk_free_gb"] / shrink_rate_gb_per_day
            warning = days_to_full <= DISK_FULL_WARNING_DAYS
            return {
                "result": {
                    "path": path,
                    "current_disk_percent": last["disk_percent"],
                    "days_to_full": round(days_to_full, 1),
                    "warning": warning,
                    "note": f"At the current rate ({shrink_rate_gb_per_day:.2f} GB/day), disk will be full in ~{days_to_full:.1f} days.",
                }
            }

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

        elif task == "scan_unused_docker_resources":
            images = self._docker_images()
            safe_to_clear = []
            needs_confirmation = []
            for img in images:
                if img["tag"] == "<none>:<none>":
                    safe_to_clear.append(img)
                    continue
                if self._docker_containers_using(img["id"]):
                    continue  # actively used - leave alone entirely
                if self._is_project_referenced(img["tag"]):
                    needs_confirmation.append(img)
                else:
                    safe_to_clear.append(img)
            return {
                "result": {
                    "safe_to_clear": safe_to_clear,
                    "needs_confirmation": needs_confirmation,
                    "note": (
                        f"{len(safe_to_clear)} unused item(s) look like disposable test/build artifacts; "
                        f"{len(needs_confirmation)} reference known project infrastructure and need a go-ahead."
                    ),
                }
            }

        elif task == "run_cleanup_routine":
            scan = self.handle_task("scan_unused_docker_resources", {}, sender).get("result", {})
            cleared = []
            for img in scan.get("safe_to_clear", []):
                try:
                    subprocess.run(["docker", "image", "rm", img["id"]], capture_output=True, timeout=30)
                    cleared.append(img["tag"])
                except Exception as e:
                    self.log(f"Failed to clear {img['tag']}: {e}")
            try:
                subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True, timeout=60)
            except Exception as e:
                self.log(f"Builder prune failed: {e}")

            needs_confirmation = scan.get("needs_confirmation", [])
            self.log_to_audit(
                "CLEANUP_ROUTINE", f"Auto-cleared {len(cleared)} unused image(s); {len(needs_confirmation)} held for confirmation",
                metadata={"cleared": cleared}
            )
            return {
                "result": {
                    "cleared": cleared,
                    "requires_escalation": len(needs_confirmation) > 0,
                    "needs_confirmation": needs_confirmation,
                    "note": (
                        f"Cleared {len(cleared)} disposable item(s)." +
                        (f" {len(needs_confirmation)} more reference known project infrastructure - let me know if you want those removed too."
                         if needs_confirmation else "")
                    ),
                }
            }

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
