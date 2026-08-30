#!/usr/bin/env python3
import sys
import os
import re
import time
import json
import subprocess
import requests
from datetime import datetime, timedelta

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Processes that must never be suggested for shutdown - the swarm cannot
# function without them, regardless of how idle they look.
CORE_PROCESSES = (
    "registry_service", "memory/service", "logging_auditing",
    "boss_agent", "anansi", "hermes",
)

# Services with no caller anywhere in the codebase. Verified by grepping for
# their ports across agents/ and core/: nothing references them, so they start
# at boot, pass health checks, and do nothing. Listed rather than detected
# because "no caller" is a static fact about the code, not a runtime one.
KNOWN_IDLE_SERVICES = {
    "training/service": "no caller anywhere in agents/ or core/",
    "evaluation/service": "no caller anywhere in agents/ or core/",
    "data_engineering/service": "no caller anywhere in agents/ or core/",
}

# A process using less than this is not worth reporting as a memory concern.
RAM_REPORT_FLOOR_MB = 25.0

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
    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        # reclaiming resources - the verb, which is what people actually say
        "clean ?up", "cleanup", "free ?up", "reclaim", "recover", "release",
        "shrink", "trim", "purge", "prune", "wasted?", "wasting", "hogging",
        "bloat", "idle", "unused", "not being used", "doing nothing",
        # what is being reclaimed. Bare "memory" is deliberately absent: in this
        # system it also means the Memory Service and Hermes storage, so
        # "store that in memory" must not read as a request to free RAM.
        "ram", "memory usage", "memory footprint", "free memory", "much memory",
        "resident", "memory hog", "disk", "storage", "drive", "docker",
        "disk space", "df\\b", "inode",
        # health of the machine itself
        "system health", "uptime", "load average", "cpu usage", "temperature",
        "log rotation", "rotate logs", "stale logs", "update", "upgrade",
    )

    def __init__(self):
        super().__init__(
            agent_id="maintenance_agent",
            port=8003,
            capabilities=[
                "check_disk", "clean_logs", "check_updates",
                "apply_updates", "rollback", "check_errors",
                "check_container_updates", "update_container",
                "sample_telemetry", "get_telemetry_history", "predict_disk_full",
                "scan_unused_docker_resources", "run_cleanup_routine",
                "analyze_memory_usage"
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

    # Reclaim language, and the things that hold a resource. This lived in
    # Boss, which is how "recover 39 mb idle space" reached a CODE model and
    # came back with invented Windows and macOS instructions - the 39 MB was
    # RAM held by idle services, a figure this agent had produced itself.
    _RECLAIM = ("clean ?up", "cleanup", "free ?up", "clear", "reclaim", "recover",
                "release", "reduce", "shrink", "idle", "unused", "wasted", "waste",
                "wasting", "hogging", "eating", "bloat", "trim",
                "not being used", "doing nothing")
    # Bare "memory" cannot trigger this: here it also means the Memory Service
    # and Hermes storage, so "store that in memory" must not read as free RAM.
    _MEMORY = ("ram", "memory usage", "memory footprint", "wasting memory",
               "eating memory", "hogging memory", "free memory", "resident",
               "memory hog", "using memory", "much memory", "footprint")
    _DISK = ("disk", "storage", "drive", "logs", "docker")
    _HOLDERS = ("services?", "process(es)?", "agents?")

    def describe(self, task, result):
        """Say what this agent found, in plain language.

        These sentences were written inside Boss, which meant the orchestrator
        held a second copy of this domain's language - and it drifted from the
        checks that produce the numbers."""
        if result is None:
            return None
        if task == "resource_reclaim":
            def _peel(x, depth=3):
                for _ in range(depth):
                    if isinstance(x, dict) and "result" in x:
                        x = x["result"]
                    else:
                        break
                return x if isinstance(x, dict) else {}
            lines = []
            mem = _peel(result.get("memory"))
            if mem:
                total = mem.get("swarm_total_mb")
                avail = mem.get("system_available_mb")
                if total:
                    lines.append(f"The swarm is holding {total:.0f} MB; "
                                 f"{avail:.0f} MB free on the machine.")
                for f in (mem.get("findings") or [])[:3]:
                    lines.append(f)
                idle = mem.get("idle_services") or []
                if idle:
                    lines.append("")
                    for c in idle[:8]:
                        # services/evaluation/service.py - the meaningful
                        # part is the directory, not the filename, which is
                        # "service" for every one of them.
                        parts = [x for x in c.get("cmd", "?").split() if "/" in x or x.endswith(".py")]
                        path = parts[-1] if parts else c.get("cmd", "?")
                        seg = [x for x in path.replace(".py", "").split("/") if x]
                        name = seg[-2] if len(seg) > 1 and seg[-1] == "service" else seg[-1]
                        lines.append(f"  - {name}: {c.get('rss_mb','?')} MB - {c.get('reason','idle')}")
                    # Stopping a process is an action, not a report - it goes
                    # through authorisation like any other actuation.
                    lines.append(f"\nThat is {mem.get('reclaimable_mb', 0):.0f} MB reclaimable. "
                                 "Stopping them needs your OK - say the word and I'll "
                                 "route it through authorisation.")
                elif total:
                    lines.append("Nothing is sitting idle right now.")
            disk = _peel(result.get("disk"))
            if disk:
                freed = disk.get("freed_mb") or disk.get("reclaimed_mb")
                lines.append(f"Disk cleanup: {freed} MB reclaimed." if freed
                             else "Disk cleanup ran; nothing significant to reclaim.")
            return "\n".join(lines) if lines else "Nothing reclaimable was found."
        return None

    def answer(self, prompt, **_):
        """Decide which of this agent's own checks the question needs.

        Boss used to make this choice from a keyword table it held itself. It
        does not run the machine; this agent does."""
        lp = (prompt or "").lower()

        def hit(group):
            return any(re.search(r"\b" + t, lp) for t in group)

        if not hit(self._RECLAIM) or not (hit(self._MEMORY) or hit(self._DISK)
                                          or hit(self._HOLDERS) or "space" in lp):
            return None
        want_mem = hit(self._MEMORY) or hit(self._HOLDERS)
        want_disk = hit(self._DISK)
        # "space" alone is genuinely ambiguous between RAM and disk. Report
        # both rather than guessing and acting on the wrong one.
        if not want_mem and not want_disk:
            want_mem = want_disk = True

        gathered = {}
        if want_mem:
            gathered["memory"] = self.handle_task("analyze_memory_usage", {}, self.agent_id)
        if want_disk:
            gathered["disk"] = self.handle_task("run_cleanup_routine", {}, self.agent_id)
        text = self.describe("resource_reclaim", gathered)
        if not text:
            return None
        return {"answered_as": "resource_reclaim", "text": text, "facts": gathered}

    def recent_changes(self, limit=10):
        """The last N things that actually changed, as headlines.

        The dashboard was showing a narrated paragraph built from three session
        log entries - prose about work, at the moment the grower wanted a list
        of what changed. CHANGELOG.md already holds exactly that, one dated
        headline per change, and it is the file this project treats as the
        record of what happened. Read it rather than re-describing it.

        Headlines only. The body of an entry explains WHY a change was made,
        which is worth having and is not what a status card is for."""
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "CHANGELOG.md")
        if not os.path.exists(path):
            return {"error": f"No changelog at {path}", "entries": []}
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except Exception as exc:
            return {"error": f"Could not read the changelog: {exc}", "entries": []}

        entries = []
        for line in lines:
            m = re.match(r"^###\s+(\d{4}-\d{2}-\d{2})\s*[-\u2014:]*\s*(.+?)\s*$", line)
            if m:
                entries.append({"date": m.group(1), "headline": m.group(2)})
        if not entries:
            return {"error": "The changelog has no dated ### entries to read.",
                    "entries": [], "source": path}
        try:
            n = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            n = 10
        # Newest first: a status card is read from the top.
        recent = list(reversed(entries[-n:]))
        return {"entries": recent, "count": len(recent),
                "total_recorded": len(entries), "source": "CHANGELOG.md"}

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "recent_changes":
            payload = args if isinstance(args, dict) else {}
            return self.recent_changes(limit=payload.get("limit", 10))

        if task == "check_disk":
            path = args.get("path", "/")
            expanded = os.path.expanduser(path)
            result = self._execute_local(f"df -h {expanded}")
            return {"result": result.get("stdout", "No output")}

        elif task == "analyze_memory_usage":
            # Per-process RAM for the swarm, cross-referenced against whether
            # anything actually calls each service. Recommends, never kills -
            # stopping a process is an action that belongs behind the same
            # authorization boundary as any other.
            if not PSUTIL_AVAILABLE:
                return {"error": "psutil not installed - cannot sample per-process memory"}

            vm = psutil.virtual_memory()
            procs = []
            for pr in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time"]):
                try:
                    cmdline = pr.info.get("cmdline") or []
                    cmd = " ".join(cmdline)
                    # Must be a python process actually running an agent module or
                    # a service file. Matching "mycelial" anywhere in the command
                    # line also caught shell wrappers and this very scan, which
                    # reported nine 1MB "services" that were bash invocations.
                    exe = os.path.basename(cmdline[0]) if cmdline else ""
                    if not exe.startswith("python"):
                        continue
                    is_agent = any(a.startswith("agents.") for a in cmdline)
                    is_service = any(a.endswith("service.py") or a.endswith("service_manager/service.py")
                                     or "/services/" in a for a in cmdline)
                    if not (is_agent or is_service):
                        continue
                    rss_mb = pr.info["memory_info"].rss / (1024 * 1024)
                    procs.append({"pid": pr.info["pid"], "rss_mb": round(rss_mb, 1), "cmd": cmd})
                except Exception:
                    continue

            # Recent activity per agent, from the audit trail.
            active_agents = set()
            try:
                resp = requests.get("http://localhost:8009/logs", params={"limit": 500}, timeout=10)
                if resp.status_code == 200:
                    for e in resp.json().get("entries", []):
                        if e.get("agent_id"):
                            active_agents.add(e["agent_id"])
            except Exception:
                pass

            core, idle, other = [], [], []
            for pinfo in procs:
                cmd = pinfo["cmd"]
                idle_reason = next((why for svc, why in KNOWN_IDLE_SERVICES.items() if svc in cmd), None)
                # A known-idle service is reported regardless of size. The floor
                # exists to keep noise out of the "other" list, and applying it
                # first hid three no-caller services at ~13MB each and reported
                # 0MB reclaimable while they were running.
                if idle_reason:
                    pinfo["classification"] = "idle_service"
                    pinfo["reason"] = idle_reason
                    idle.append(pinfo)
                    continue
                if pinfo["rss_mb"] < RAM_REPORT_FLOOR_MB:
                    continue
                if any(c in cmd for c in CORE_PROCESSES):
                    pinfo["classification"] = "core"
                    core.append(pinfo)
                    continue
                # An agent with no audit activity in the recent window is a
                # candidate, not a verdict - it may simply not have been asked
                # anything yet.
                agent_id = None
                for part in cmd.split():
                    if part.startswith("agents."):
                        agent_id = part.split(".")[-1]
                if agent_id and agent_id not in active_agents:
                    pinfo["classification"] = "no_recent_activity"
                    pinfo["reason"] = "no entries in the last 500 audit events - may simply be unused rather than stuck"
                    other.append(pinfo)
                else:
                    pinfo["classification"] = "active"
                    other.append(pinfo)

            reclaimable = round(sum(p["rss_mb"] for p in idle), 1)
            swarm_total = round(sum(p["rss_mb"] for p in procs), 1)
            findings = []
            if idle:
                names = ", ".join(sorted({next(s for s in KNOWN_IDLE_SERVICES if s in p["cmd"]) for p in idle}))
                findings.append(
                    f"{len(idle)} service(s) with no caller anywhere in the codebase are holding "
                    f"{reclaimable:.0f}MB ({names}). They start at boot, pass health checks, and "
                    "do nothing. Starting them on demand instead would return that memory."
                )
            quiet = [p for p in other if p.get("classification") == "no_recent_activity"]
            if quiet:
                findings.append(
                    f"{len(quiet)} agent(s) show no recent audit activity. That is not proof they are "
                    "idle - an agent nobody has asked anything looks identical to one that is stuck."
                )
            if procs:
                biggest = max(procs, key=lambda x: x["rss_mb"])
                share = biggest["rss_mb"] / swarm_total * 100 if swarm_total else 0
                if share > 35:
                    name = next((c for c in biggest["cmd"].split() if "agents." in c or "service" in c), "a process")
                    findings.append(
                        f"{name} alone holds {biggest['rss_mb']:.0f}MB, {share:.0f}% of the swarm. "
                        "Agents that load ML models keep them resident for the process lifetime, so "
                        "the cost is paid whether or not the model is being used."
                    )
            if vm.percent > 85:
                findings.append(f"System memory at {vm.percent:.0f}% - headroom is thin.")

            recommendation = self._make_recommendation(
                f"Swarm using {swarm_total:.0f}MB across {len(procs)} processes; system at {vm.percent:.0f}%.",
                " ".join(findings) if findings else "Nothing obviously wasteful.",
                (f"Up to {reclaimable:.0f}MB is reclaimable by not starting the no-caller services at boot. "
                 "Recommendation only - no process is stopped automatically.") if reclaimable
                else "No action needed.",
                "high" if (reclaimable > 100 or vm.percent > 85) else "medium",
            ) if hasattr(self, "_make_recommendation") else {
                "observation": f"Swarm using {swarm_total:.0f}MB across {len(procs)} processes",
                "findings": findings,
            }

            return {"result": {
                "system_percent": vm.percent,
                "system_available_mb": round(vm.available / (1024 * 1024), 1),
                "swarm_total_mb": swarm_total,
                "reclaimable_mb": reclaimable,
                "core": sorted(core, key=lambda x: -x["rss_mb"]),
                "idle_services": sorted(idle, key=lambda x: -x["rss_mb"]),
                "other": sorted(other, key=lambda x: -x["rss_mb"])[:12],
                "findings": findings,
                "recommendation": recommendation,
                "note": "Recommendations only. Stopping a process is an authorized action, not a maintenance side effect.",
            }}

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
