#!/usr/bin/env python3
import sys
import os
import sqlite3
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

    # Where a change LANDED decides what kind of change it was. Classifying by
    # the words in a commit subject would be the same keyword-guessing this
    # architecture exists to avoid - "fix the reservoir volume field" reads
    # domain and touches core. The files are evidence; the subject is a claim.
    SCOPE_PATHS = (
        ("platform", ("core/", "services/", "start_all.sh", "Dockerfile",
                      "docker-compose", "requirements", "config/guards.json")),
        ("interface", ("webapp/", "agents/anansi/")),
        ("corpus", ("reference/", "knowledge_base/", "tools/ingest")),
        ("docs", ("README.md", "CHANGELOG.md", "DEPLOYMENT_PROGRESS.md", "CLAUDE.md",
                  "docs/")),
    )

    def _scope_of(self, paths):
        """The scopes a commit touched, widest first. A commit is 'platform' if
        it changed anything under core/ or services/ - those are inherited by
        every agent, so a change there is a change to all of them."""
        scopes = []
        for name, prefixes in self.SCOPE_PATHS:
            if any(pp in f for f in paths for pp in prefixes):
                scopes.append(name)
        agents = sorted({f.split("/")[1] for f in paths
                         if f.startswith("agents/") and len(f.split("/")) > 2
                         and not f.startswith("agents/anansi/")})
        for a in agents:
            scopes.append(f"agent:{a}")
        return scopes or ["other"]

    def recent_changes(self, limit=10, scope=None, include_domain=False):
        """What changed, classified by where it landed.

        Reads git rather than the changelog prose. Every commit carries the
        files it touched, and that is what decides whether a change was to the
        PLATFORM - core/ and services/, inherited by every agent - or to one
        agent's own domain work. The changelog headline is a claim about a
        change; the paths are evidence of it.

        The distinction matters because the two answer different questions.
        'How is the system evolving' is the platform and interface story. A
        week of Grow bugfixes is real work and belongs in the plant's history,
        not in the answer to what the system now does that it could not before.
        Mixing them buries the second under the volume of the first."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            n = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            n = 10
        try:
            raw = subprocess.run(
                ["git", "-C", repo, "log", "-n", "300", "--no-merges",
                 "--date=short", "--name-only",
                 "--pretty=format:%x00%H%x1f%ad%x1f%s"],
                capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return {"error": f"Could not read git history: {exc}", "entries": []}
        if raw.returncode != 0:
            return {"error": f"git log failed: {raw.stderr.strip()[:200]}", "entries": []}

        commits = []
        for block in raw.stdout.split("\x00"):
            if not block.strip():
                continue
            head, _, rest = block.partition("\n")
            parts = head.split("\x1f")
            if len(parts) < 3:
                continue
            files = [ln.strip() for ln in rest.splitlines() if ln.strip()]
            commits.append({"sha": parts[0][:7], "date": parts[1], "headline": parts[2],
                            "scopes": self._scope_of(files), "files_changed": len(files)})

        wanted = scope if isinstance(scope, list) else ([scope] if scope else None)
        if wanted:
            sel = [c for c in commits
                   if any(w in sc for sc in c["scopes"] for w in wanted)]
        elif include_domain:
            sel = commits
        else:
            # The default question is how the SYSTEM is evolving, so a commit
            # that only touched one agent's own domain work is left out - and
            # the count of what was left out is reported, because silently
            # filtered history is indistinguishable from history that does not
            # exist.
            sel = [c for c in commits
                   if any(sc in ("platform", "interface", "corpus") for sc in c["scopes"])]
        omitted = len(commits) - len(sel)
        return {"entries": sel[:n], "count": len(sel[:n]),
                "domain_only_omitted": omitted if not (wanted or include_domain) else 0,
                "total_scanned": len(commits), "source": "git log"}

    def _declared_ports(self):
        """Port per agent, from the configs that declare them.

        This is the CLAIM half. A config saying an agent listens on 9012 is an
        assertion; whether anything answers there is the observation, and
        system_graph checks it separately. Keeping them apart is the point -
        conflating them is how a registry row got believed over a silent port."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = {}
        cfg_dir = os.path.join(repo, "config", "agent_configs")
        if not os.path.isdir(cfg_dir):
            return out
        for fn in os.listdir(cfg_dir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(cfg_dir, fn), encoding="utf-8") as fh:
                    d = json.load(fh)
            except Exception:
                continue
            aid, port = d.get("agent_id"), d.get("port")
            if aid and isinstance(port, int):
                out[aid] = port
        return out

    def system_graph(self, hours=24, min_calls=1, include_knowledge=True):
        """Who actually talked to whom, drawn from the audit log.

        The knowledge graph in `state/graph.db` was wired up and is not this.
        It holds 38 nodes that are entirely TEST FIXTURES - John Doe, Alice
        Corp, `determinism_test` - written on 2026-08-07 and 08-22 while the
        legal and accounting pipelines were being built. Drawing those on a
        dashboard would show the principal a picture of nothing that happened,
        which is worse than showing no picture: it looks like a system map and
        is a screenshot of a unit test.

        The real interaction record is the audit log, where every completed task
        carries the agent that ran it and the `sender` that asked. That is an
        observation of what the system DID, which outranks a stored assertion
        about what it contains - the same rule as a port outranking a registry
        row."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        audit = os.path.join(repo, "state", "audit.db")
        if not os.path.exists(audit):
            return {"error": f"No audit log at {audit}", "nodes": [], "edges": []}
        try:
            h = max(1, min(int(hours), 24 * 90))
        except (TypeError, ValueError):
            h = 24
        since = (datetime.now() - timedelta(hours=h)).isoformat()

        nodes, edges = {}, {}
        try:
            conn = sqlite3.connect(f"file:{audit}?mode=ro", uri=True, timeout=10)
            rows = conn.execute(
                "SELECT agent_id, task, metadata FROM logs "
                "WHERE event_type='TASK_COMPLETED' AND timestamp >= ? "
                "AND metadata IS NOT NULL AND metadata != ''", (since,)).fetchall()
            conn.close()
        except Exception as exc:
            return {"error": f"Could not read the audit log: {exc}", "nodes": [], "edges": []}

        for agent_id, task, meta in rows:
            try:
                m = json.loads(meta)
            except Exception:
                continue
            sender = m.get("sender")
            if not agent_id:
                continue
            nodes.setdefault(agent_id, {"id": agent_id, "handled": 0, "asked": 0})
            nodes[agent_id]["handled"] += 1
            # "unknown" is an inbound call with no named caller - the webapp and
            # curl both arrive that way. Kept as a node rather than dropped,
            # because traffic entering from outside is part of the picture.
            src = sender or "external"
            nodes.setdefault(src, {"id": src, "handled": 0, "asked": 0})
            nodes[src]["asked"] += 1
            if src == agent_id:
                continue
            key = f"{src}->{agent_id}"
            e = edges.setdefault(key, {"from": src, "to": agent_id, "calls": 0, "tasks": {}})
            e["calls"] += 1
            if task:
                e["tasks"][task] = e["tasks"].get(task, 0) + 1

        try:
            floor = max(1, int(min_calls))
        except (TypeError, ValueError):
            floor = 1
        kept = [e for e in edges.values() if e["calls"] >= floor]
        for e in kept:
            e["top_tasks"] = sorted(e["tasks"].items(), key=lambda kv: -kv[1])[:3]
            del e["tasks"]
        seen = {e["from"] for e in kept} | {e["to"] for e in kept}

        # Liveness is read from the port, never from the log. An agent can be
        # all over the history and not be running now, and that difference is
        # the single most useful thing a system map can show.
        ports = self._declared_ports()
        live = {}
        for nid in seen:
            port = ports.get(nid)
            if port is None:
                live[nid] = None
                continue
            try:
                r = requests.get(f"http://127.0.0.1:{port}/health", timeout=1.5)
                live[nid] = (r.status_code == 200)
            except Exception:
                live[nid] = False

        out_nodes = []
        for nid in sorted(seen):
            n = dict(nodes[nid])
            n["port"] = ports.get(nid)
            n["live"] = live.get(nid)
            n["kind"] = ("external" if nid == "external"
                         else "service" if (n["port"] or 0) and n["port"] < 8080
                         else "agent")
            out_nodes.append(n)

        return {"nodes": out_nodes,
                "edges": sorted(kept, key=lambda e: -e["calls"]),
                "window_hours": h, "min_calls": floor,
                "edges_below_threshold": len(edges) - len(kept),
                "source": "audit log (observed traffic)",
                "knowledge_graph": self._knowledge_graph_summary() if include_knowledge else None}

    def _knowledge_graph_summary(self):
        """What the KAG holds, and whether any of it is real.

        Reported as a summary with a `looks_like_test_data` flag rather than
        drawn, because a graph of fixtures rendered beside real traffic would be
        indistinguishable from real content."""
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        gdb = os.path.join(repo, "state", "graph.db")
        if not os.path.exists(gdb):
            return {"present": False}
        try:
            conn = sqlite3.connect(f"file:{gdb}?mode=ro", uri=True, timeout=10)
            types = dict(conn.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall())
            rels = dict(conn.execute(
                "SELECT rel_type, COUNT(*) FROM edges GROUP BY rel_type").fetchall())
            names = [r[0] for r in conn.execute(
                "SELECT id FROM nodes WHERE type IN ('entity','project')").fetchall()]
            newest = conn.execute("SELECT MAX(created_at) FROM nodes").fetchone()[0]
            conn.close()
        except Exception as exc:
            return {"present": True, "error": str(exc)}
        FIXTURES = ("john doe", "alice corp", "bob llc", "xyz inc", "abc corp", "acme")
        hits = [n for n in names
                if any(f in n.lower() for f in FIXTURES) or "test" in n.lower()]
        return {"present": True, "node_types": types, "edge_types": rels,
                "newest_node": newest, "named_entities": len(names),
                "fixture_matches": hits[:8],
                "looks_like_test_data": len(hits) >= max(2, len(names) // 3),
                "note": ("Named entities matching known fixtures or containing 'test'. "
                         "Drawn nowhere until real work is written into it - a graph of "
                         "unit-test data on a dashboard looks exactly like a system map.")}

    def phase_status(self):
        """Where the roadmap actually stands, read from its own table.

        DEPLOYMENT_PROGRESS.md holds what is planned, and its table is the one
        place that says which phase is done. Kept as a read of that file rather
        than a second copy, because a duplicated status is a status that drifts
        - and the one that drifts is always the copy nothing edits."""
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "DEPLOYMENT_PROGRESS.md")
        if not os.path.exists(path):
            return {"error": f"No roadmap at {path}", "phases": []}
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except Exception as exc:
            return {"error": f"Could not read the roadmap: {exc}", "phases": []}

        phases, seen_header = [], False
        for line in text.splitlines():
            if not line.strip().startswith("|"):
                if seen_header and phases:
                    break
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            if cells[0] == "#" or set(cells[0]) <= {"-", ":"}:
                seen_header = True
                continue
            if not seen_header:
                continue
            num, name, status = cells[0], cells[1], cells[2]
            low = status.lower()
            state = ("done" if ("done" in low or "\u2705" in status)
                     else "in_progress" if ("\u25d0" in status or "remaining" in low
                                            or "progress" in low)
                     else "not_started" if "not started" in low
                     else "not_scheduled" if "not scheduled" in low
                     else "unknown")
            phases.append({"number": num, "name": name, "status_text": status,
                           "state": state})
        if not phases:
            return {"error": "The roadmap table could not be parsed.", "phases": []}

        # The table is a summary of the sections below it, and a summary drifts.
        # Phase 6 sat at "not started" in the table while its own section
        # recorded nginx TLS, the Security Agent and the retirement of port 8090
        # as done - which is exactly how work gets repeated. So the table is
        # checked against the headings rather than trusted, and any disagreement
        # is reported instead of one side silently winning.
        heads = {}
        for line in text.splitlines():
            m = re.match(r"^##\s+Phase\s+(\d+)\s*[-\u2014]\s*(.+?)\s*$", line)
            if m:
                heads[m.group(1)] = m.group(2)
        conflicts = []
        for ph in phases:
            head = heads.get(ph["number"])
            if not head:
                continue
            hl = head.lower()
            hstate = ("done" if ("\u2705" in head or " done" in hl)
                      else "in_progress" if ("\u25d0" in head or "remaining" in hl)
                      else "not_started" if "not started" in hl
                      else "unknown")
            if hstate != "unknown" and hstate != ph["state"]:
                conflicts.append({"number": ph["number"], "name": ph["name"],
                                  "table_says": ph["state"], "section_says": hstate})
                # The detailed section is the one that gets edited while work is
                # happening, so where they disagree it is the better evidence.
                ph["state"] = hstate
                ph["state_source"] = "section heading (disagreed with table)"

        numbered = [p for p in phases if p["number"].isdigit()]
        done = [p for p in numbered if p["state"] == "done"]
        active = [p for p in numbered if p["state"] == "in_progress"]
        # The NEXT phase is the lowest-numbered one not finished. Reporting a
        # count of completions without it answers "how much" and not "what now".
        nxt = next((p for p in numbered if p["state"] not in ("done",)), None)
        return {"phases": phases, "table_section_conflicts": conflicts,
                "done": len(done), "total_numbered": len(numbered),
                "in_progress": [p["name"] for p in active],
                "next": ({"number": nxt["number"], "name": nxt["name"],
                          "status_text": nxt["status_text"]} if nxt else None),
                "source": "DEPLOYMENT_PROGRESS.md"}

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "recent_changes":
            payload = args if isinstance(args, dict) else {}
            return self.recent_changes(limit=payload.get("limit", 10),
                                       scope=payload.get("scope"),
                                       include_domain=bool(payload.get("include_domain")))

        if task == "system_graph":
            payload = args if isinstance(args, dict) else {}
            return self.system_graph(hours=payload.get("hours", 24),
                                     min_calls=payload.get("min_calls", 1),
                                     include_knowledge=payload.get("include_knowledge", True))

        if task == "phase_status":
            return self.phase_status()

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
