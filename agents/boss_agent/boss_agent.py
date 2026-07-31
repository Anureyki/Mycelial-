#!/usr/bin/env python3
import sys
import os
import time
import json
import requests
import re
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class BossAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="boss_agent",
            port=8000,
            capabilities=[
                "think", "store_memory", "retrieve_memory", "delegate",
                "process_request", "call_tool", "alert", "check_errors",
                "process_recommendations"
            ],
            role="orchestrator"
        )
        self.log("👑 Boss orchestrator started with Sentry integration.")
        self.default_org = os.getenv("SENTRY_ORG", "your-org")
        self.default_project = os.getenv("SENTRY_PROJECT", "your-project")

    def _trigger_reconcile(self):
        try:
            resp = requests.post("http://localhost:8014/reconcile", timeout=5)
            if resp.status_code == 200:
                self.log("Reconciliation triggered successfully.")
                return True
            else:
                self.log(f"Reconciliation failed: {resp.status_code}")
                return False
        except Exception as e:
            self.log(f"Reconciliation error: {e}")
            return False

    def _format_response(self, task, result, sender):
        if result is None:
            return "The request did not return a result."
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if "error" in result:
                return f"Error: {result['error']}"
            if task == "evaluate":
                issues = result.get("issues_found", 0)
                files = result.get("python_files", 0)
                if issues == 0:
                    return f"Codebase evaluation complete: {files} Python files, no issues found."
                else:
                    return f"Codebase evaluation complete: found {issues} issues in {files} Python files. First few: {', '.join(result.get('details', [])[:3])}"
            elif task == "reason" or task == "think":
                return result.get("result", "No specific result provided.")
            elif task == "check_errors":
                errors = result.get("result", {})
                if isinstance(errors, dict) and "error" in errors:
                    return f"Sentry check failed: {errors['error']}"
                return f"Sentry check completed. Details: {json.dumps(errors)[:200]}"
            elif task == "call_tool":
                return f"Tool call result: {json.dumps(result.get('result', result))}"
            elif task == "search" or task == "search_web":
                return result.get("result", "Search completed.")
            elif task == "fix_code":
                if result.get("success"):
                    return f"Code fixed successfully: {result.get('fixed_code', '')[:200]}"
                else:
                    return f"Code fix failed. Verification: {result.get('verification', 'unknown error')}"
            elif task == "generate_recommendations":
                recs = result.get("recommendations", [])
                if not recs:
                    return "No issues found. The system is healthy."
                msg = f"Analysis complete: found {len(recs)} recommendations.\n"
                for rec in recs[:3]:
                    msg += f"- {rec.get('issue')} (criticality: {rec.get('criticality')})\n"
                if len(recs) > 3:
                    msg += f"... and {len(recs)-3} more."
                return msg
            elif task == "fetch_repo":
                if "result" in result:
                    return result["result"]
                else:
                    return "Repo summary available."
            else:
                return json.dumps(result, indent=2)
        if isinstance(result, list):
            if len(result) == 0:
                return "No results returned."
            return "\n".join([str(item) for item in result[:5]]) + (f"\n... and {len(result)-5} more" if len(result) > 5 else "")
        return str(result)

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender} with args: {args}")

        if task == "think":
            thought = args.get("thought", "")
            self.store_own_memory("last_thought", thought)
            self.log_to_audit("THOUGHT", f"Thought: {thought}", level="info")
            return {"result": f"Thought stored: {thought}"}

        elif task == "store_memory":
            key = args.get("key")
            value = args.get("value")
            pin = args.get("pin", False)
            if not key or value is None:
                return {"error": "Missing key or value"}
            self.store_own_memory(key, value, pin=pin)
            return {"result": f"Stored {key}"}

        elif task == "retrieve_memory":
            key = args.get("key")
            if not key:
                return {"error": "Missing key"}
            value = self.retrieve_own_memory(key)
            return {"result": value}

        elif task == "delegate":
            target = args.get("target")
            subtask = args.get("task")
            subargs = args.get("args", {})
            if not target or not subtask:
                return {"error": "Missing target or task"}
            self.log(f"Delegating {subtask} to {target}")
            response = self.send_a2a(target, subtask, subargs)
            return {"delegated": True, "response": response}

        elif task == "process_request":
            if isinstance(args, dict):
                prompt = args.get("prompt", "")
                metadata = args.get("metadata", {})
            elif isinstance(args, list) and len(args) > 0:
                first = args[0]
                if isinstance(first, str) and first.startswith('{'):
                    try:
                        payload = json.loads(first)
                        prompt = payload.get("prompt", "")
                        metadata = payload.get("metadata", {})
                    except:
                        prompt = first
                        metadata = {}
                else:
                    prompt = str(first)
                    metadata = {}
            else:
                return {"error": "Invalid args format"}

            if not prompt:
                return {"error": "Missing prompt"}

            self.log(f"Received user prompt: {prompt[:80]}...")

            # --- README / documentation ---
            if "readme" in prompt.lower() or "documentation" in prompt.lower():
                self.log("User asking about README – reading and summarizing")
                content = self.send_a2a("coding_agent", "read_file", {"path": "~/mycelial/README.md"})
                if isinstance(content, dict) and "result" in content:
                    summary_prompt = f"Summarize the following README content in plain text, without the ASCII architecture diagram. Focus on the purpose, core agents, and services:\n\n{content['result']}"
                    summary = self.send_a2a("coding_agent", "reason", {"prompt": summary_prompt})
                    text = self._format_response("reason", summary, "coding_agent")
                    return {"result": text}
                else:
                    return {"result": "Could not read README."}

            # --- GitHub repo ---
            if "github" in prompt.lower() or "repo" in prompt.lower() or "repository" in prompt.lower():
                self.log("User asking about a GitHub repo – delegating to coding_agent.fetch_repo")
                url_match = re.search(r'https?://github\.com/[^\s]+', prompt)
                if not url_match:
                    return {"result": "Please provide a GitHub URL."}
                url = url_match.group(0)
                response = self.send_a2a("coding_agent", "fetch_repo", {"url": url})
                text = self._format_response("fetch_repo", response, "coding_agent")
                return {"result": text}

            # --- Evaluation / Lint / Analyze code ---
            if any(keyword in prompt.lower() for keyword in ("evaluate", "lint", "analyze", "check code", "quality")):
                self.log("User asking for code evaluation – delegating to coding_agent")
                response = self.send_a2a("coding_agent", "evaluate", {"path": "~/mycelial"})
                text = self._format_response("evaluate", response, "coding_agent")
                return {"result": text}

            # --- Analyze outcomes / recommendations ---
            if any(keyword in prompt.lower() for keyword in ("analyze outcomes", "analyze", "recommendations", "report")):
                self.log("User asking for analysis – delegating to analyzer_agent")
                response = self.send_a2a("analyzer_agent", "generate_recommendations", {})
                text = self._format_response("generate_recommendations", response, "analyzer_agent")
                return {"result": text}

            # --- FIX / DEBUG (moved before error check) ---
            if any(keyword in prompt.lower() for keyword in ("fix", "debug", "troubleshoot", "what is the cause")):
                self.log("User asking for debugging help – delegating to coding_agent")
                response = self.send_a2a("coding_agent", "reason", {"prompt": prompt})
                text = self._format_response("reason", response, "coding_agent")
                return {"result": text}

            # --- Error / Sentry checks ---
            if "error" in prompt.lower() or "sentry" in prompt.lower():
                self.log("User asking about errors – delegating to maintenance_agent")
                org = metadata.get("org", self.default_org)
                project = metadata.get("project", self.default_project)
                match = re.search(r'for\s+([\w-]+)', prompt, re.IGNORECASE)
                if match:
                    project = match.group(1)
                match = re.search(r'org\s+([\w-]+)', prompt, re.IGNORECASE)
                if match:
                    org = match.group(1)
                response = self.send_a2a("maintenance_agent", "check_errors", {"org": org, "project": project})
                text = self._format_response("check_errors", response, "maintenance_agent")
                return {"result": text}

            # --- Web search ---
            if any(keyword in prompt.lower() for keyword in ("search", "find", "look up", "google")):
                self.log("User asking for search – delegating to PQA (or tool)")
                response = self.send_a2a("pqa_agent", "search", {"query": prompt})
                if response and not isinstance(response, dict) or response.get("error"):
                    tool_result = self.call_tool("searxng", "search", {"query": prompt})
                    text = self._format_response("call_tool", tool_result, "tool")
                else:
                    text = self._format_response("search", response, "pqa_agent")
                return {"result": text}

            # --- Default: delegate to Coding Agent for reasoning ---
            self.log("Delegating to coding_agent for reasoning...")
            response = self.send_a2a("coding_agent", "reason", {"prompt": prompt})
            text = self._format_response("reason", response, "coding_agent")
            self.store_own_memory(f"request_{int(time.time())}", prompt)
            self.store_own_memory(f"response_{int(time.time())}", text)
            return {"result": text}

        elif task == "call_tool":
            server = args.get("server")
            tool_name = args.get("tool_name")
            tool_args = args.get("tool_args", {})
            if not server or not tool_name:
                return {"error": "Missing server or tool_name"}
            result = self.call_tool(server, tool_name, tool_args)
            text = self._format_response("call_tool", result, "tool")
            return {"result": text}

        elif task == "alert":
            message = args.get("message", "")
            recommendations = args.get("recommendations", [])
            report_path = args.get("report_path", "")
            self.log_to_audit("ALERT", message, level="warning")
            self.log(f"Alert received: {message}")
            if recommendations:
                self.store_own_memory("last_recommendations", json.dumps(recommendations))
                self.log(f"Stored {len(recommendations)} recommendations.")
                high_critical = any(r.get("criticality") == "high" for r in recommendations)
                if high_critical:
                    self.log("High criticality recommendations detected. Triggering reconciliation...")
                    if self._trigger_reconcile():
                        return {"result": "Alert logged, reconciliation triggered", "recommendations": len(recommendations)}
                    else:
                        return {"result": "Alert logged, but reconciliation failed", "recommendations": len(recommendations)}
            return {"result": "Alert logged", "recommendations": len(recommendations)}

        elif task == "process_recommendations":
            recs = self.retrieve_own_memory("last_recommendations")
            if not recs:
                return {"error": "No recommendations found."}
            try:
                recs = json.loads(recs)
            except:
                return {"error": "Invalid recommendations format."}
            if not isinstance(recs, list):
                return {"error": "Recommendations not a list."}
            result = {"processed": 0, "actions": []}
            for rec in recs:
                agent = rec.get("agent")
                issue = rec.get("issue")
                suggestion = rec.get("suggestion")
                if agent == "coding_agent" and "hook" in suggestion.lower():
                    self.log(f"Delegating to coding_agent to apply suggestion: {suggestion[:50]}...")
                    resp = self.send_a2a("coding_agent", "edit_file", {
                        "path": f"~/mycelial/agents/{agent}/{agent}.py",
                        "content": "# Placeholder for adding hook logic"
                    })
                    result["actions"].append({"agent": agent, "action": "edit_file", "response": resp})
                    result["processed"] += 1
            return {"result": result}

        elif task == "check_errors":
            org = args.get("org", self.default_org)
            project = args.get("project", self.default_project)
            self.log(f"Checking Sentry errors for {org}/{project}")
            response = self.send_a2a("maintenance_agent", "check_errors", {"org": org, "project": project})
            text = self._format_response("check_errors", response, "maintenance_agent")
            return {"result": text}

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = BossAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
