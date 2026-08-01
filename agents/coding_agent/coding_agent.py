#!/usr/bin/env python3
import sys
import os
import time
import json
import requests
import re
import subprocess

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class CodingAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="coding_agent",
            port=8001,
            capabilities=[
                "read_file", "edit_file", "run_command", "run_code",
                "crontab", "reason", "reason_and_act", "fix_code", "evaluate",
                "fetch_repo", "web_search"
            ],
            role="software_engineering"
        )
        self.log("💻 Coding agent ready (local execution, return-code verification).")

    def _get_model_for_task(self, task_type="code_fix"):
        requirements = {}
        if task_type == "code_fix":
            requirements = {"domain": "code", "specialization": "code", "speed": "fast"}
        elif task_type == "verification":
            requirements = {"domain": "general", "specialization": "general", "quality": "medium"}
        else:
            requirements = {"domain": "general", "specialization": "general", "speed": "fast"}
        try:
            resp = requests.post(
                "http://localhost:8006/models/select",
                json={"requirements": requirements},
                timeout=3
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("model", "qwen2.5:1.5b")
            return "qwen2.5:1.5b"
        except:
            return "qwen2.5:1.5b"

    def _call_inference(self, prompt, model_name=None):
        if model_name is None:
            model_name = self._get_model_for_task("reasoning")
        try:
            resp = requests.post(
                "http://localhost:8005/reason",
                json={"prompt": prompt, "model": model_name},
                timeout=120
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("result", "")
                else:
                    return f"Inference error: {data.get('message', 'unknown')}"
            else:
                return f"Inference HTTP error: {resp.status_code}"
        except Exception as e:
            return f"Inference failed: {e}"

    def _execute_local(self, command, cwd=None):
        """Execute a command locally with a safe prefix whitelist."""
        safe_prefixes = (
            'ls', 'cat', 'echo', 'python3', 'node', 'bash', 'sh', 'df', 'free', 'ps',
            'find', 'pylint', 'flake8', 'pytest', 'mypy', 'grep', 'awk', 'sed', 'wc',
            'head', 'tail', 'sort', 'uniq', 'which', 'git', 'make', 'cmake', 'gcc',
            'clang', 'pip', 'pip3', 'npm', 'yarn', 'curl'
        )
        if not any(command.startswith(p) for p in safe_prefixes):
            self.log(f"⚠️ Command not in safe list: {command[:50]}")
            return {"stdout": "", "stderr": "Command not allowed", "returncode": 1}
        try:
            result = subprocess.run(
                command, shell=True, cwd=cwd or os.getcwd(),
                capture_output=True, text=True, timeout=120
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

    def _write_file_local(self, path, content):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "read_file":
            path = args.get("path")
            if not path:
                return {"error": "Missing path"}
            try:
                with open(os.path.expanduser(path), "r") as f:
                    content = f.read()
                return {"result": content}
            except Exception as e:
                return {"error": str(e)}

        elif task == "edit_file":
            path = args.get("path")
            content = args.get("content")
            if not path or content is None:
                return {"error": "Missing path or content"}
            try:
                with open(os.path.expanduser(path), "w") as f:
                    f.write(content)
                return {"result": f"File {path} updated"}
            except Exception as e:
                return {"error": str(e)}

        elif task == "run_command":
            command = args.get("command")
            cwd = args.get("cwd", os.getcwd())
            if not command:
                return {"error": "Missing command"}
            result = self._execute_local(command, cwd)
            return {"result": result}

        elif task == "run_code":
            code = args.get("code")
            language = args.get("language", "python")
            if not code:
                return {"error": "Missing code"}
            temp_file = f"/tmp/code_{int(time.time())}.{language}"
            self._write_file_local(temp_file, code)
            if language == "python":
                cmd = f"python3 {temp_file}"
            elif language == "javascript":
                cmd = f"node {temp_file}"
            else:
                cmd = f"bash {temp_file}"
            result = self._execute_local(cmd)
            os.remove(temp_file)
            return {"result": result}

        elif task == "crontab":
            action = args.get("action")
            if action == "list":
                result = self._execute_local("crontab -l")
                return {"result": result.get("stdout", "")}
            elif action == "add":
                line = args.get("line")
                if not line:
                    return {"error": "Missing cron line"}
                cmd = f'(crontab -l 2>/dev/null; echo "{line}") | crontab -'
                result = self._execute_local(cmd)
                return {"result": "Cron job added" if result.get("returncode") == 0 else "Failed"}
            else:
                return {"error": f"Unknown crontab action: {action}"}

        elif task == "reason":
            prompt = args.get("prompt", "")
            if not prompt:
                return {"error": "Missing prompt"}
            model = self._get_model_for_task("reasoning")
            self.log(f"Selected model for reasoning: {model}")
            result = self._call_inference(prompt, model)
            self.store_own_memory(f"reasoning_{int(time.time())}", result)
            return {"result": result, "model_used": model}

        elif task == "reason_and_act":
            prompt = args.get("prompt", "")
            if not prompt:
                return {"error": "Missing prompt"}
            reason_prompt = f"Given the request '{prompt}', generate a single shell command (or action) to fulfill it. Return only the command."
            model = self._get_model_for_task("reasoning")
            plan = self._call_inference(reason_prompt, model)
            self.log(f"Generated plan: {plan}")
            lines = plan.strip().split('\n')
            command = lines[0].strip()
            if command and not command.startswith(('rm', 'dd', 'mkfs', 'sudo')):
                self.log(f"Executing: {command}")
                result = self._execute_local(command)
                return {
                    "plan": plan,
                    "command": command,
                    "stdout": result.get("stdout"),
                    "stderr": result.get("stderr"),
                    "returncode": result.get("returncode"),
                    "result": result.get("stdout") or result.get("stderr") or "Command executed with no output"
                }
            else:
                self.log("Plan was not a safe command, returning reasoning only.")
                return {
                    "plan": plan,
                    "reasoning": "Action deferred – plan requires manual review.",
                    "command": command if command else "None"
                }

        elif task == "fix_code":
            code = args.get("code")
            error = args.get("error")
            language = args.get("language", "python")
            if not code or not error:
                return {"error": "Missing code or error"}

            self.log(f"Fixing {language} code with error: {error[:80]}...")
            fix_prompt = (
                f"The following {language} code has an error: {error}\n\n"
                f"Code:\n{code}\n\n"
                f"Provide the corrected code only, no explanations."
            )
            model_fix = self._get_model_for_task("code_fix")
            self.log(f"Using model for fix: {model_fix}")
            fixed_code = self._call_inference(fix_prompt, model_fix)
            fixed_code = re.sub(r'```[\w]*\n?', '', fixed_code).strip()

            temp_file = f"/tmp/fixed_code_{int(time.time())}.{language}"
            self._write_file_local(temp_file, fixed_code)
            if language == "python":
                run_cmd = f"python3 {temp_file}"
            elif language == "javascript":
                run_cmd = f"node {temp_file}"
            elif language == "bash":
                run_cmd = f"bash {temp_file}"
            else:
                run_cmd = f"cat {temp_file}"
            execution_result = self._execute_local(run_cmd)
            os.remove(temp_file)

            success = execution_result.get("returncode") == 0
            verification = f"Return code: {execution_result.get('returncode')} – {'Success' if success else 'Failure'}"
            self.store_own_memory(f"fix_{int(time.time())}", {
                "original_error": error,
                "fixed_code": fixed_code,
                "execution": execution_result,
                "verification": verification,
                "success": success
            })
            return {
                "original_error": error,
                "fixed_code": fixed_code,
                "execution_output": execution_result,
                "verification": verification,
                "success": success
            }

        elif task == "evaluate":
            path = args.get("path", "~/mycelial")
            expanded_path = os.path.expanduser(path)
            self.log(f"Evaluating codebase at {expanded_path}")

            cmd = f"find {expanded_path} -name '*.py' -exec flake8 {{}} \\; 2>/dev/null"
            result = self._execute_local(cmd)
            lines = result.get("stdout", "").splitlines()
            issues = len(lines)

            count_cmd = f"find {expanded_path} -name '*.py' | wc -l"
            count_result = self._execute_local(count_cmd)
            file_count = int(count_result.get("stdout", "0").strip() or 0)

            return {
                "path": expanded_path,
                "python_files": file_count,
                "issues_found": issues,
                "details": lines[:50],
                "summary": f"Found {issues} issues in {file_count} Python files."
            }

        # ----- NEW: fetch_repo - read a GitHub repo README -----
        elif task == "fetch_repo":
            url = args.get("url")
            if not url:
                return {"error": "Missing GitHub URL"}
            # Convert GitHub URL to raw README URL
            match = re.match(r'https?://github\.com/([^/]+)/([^/]+)', url)
            if not match:
                return {"error": "Invalid GitHub URL"}
            user, repo = match.group(1), match.group(2)
            self.log(f"Fetching README for {user}/{repo}")
            content = None
            for branch in ["main", "master"]:
                raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/README.md"
                cmd = f"curl -s {raw_url}"
                result = self._execute_local(cmd)
                if result.get("returncode") == 0 and result.get("stdout"):
                    content = result.get("stdout")
                    break
            if not content:
                return {"error": "Could not fetch README from GitHub"}

            # Summarise the content
            summary_prompt = f"Summarize the following README content in plain text, focusing on architecture, components, and purpose:\n\n{content}"
            summary = self._call_inference(summary_prompt)
            return {
                "result": summary,
                "raw_content": content[:1000]  # trim for display
            }

        elif task == "web_search":
            query = args.get("query") if isinstance(args, dict) else args[0] if args else None
            if not query:
                return {"error": "Missing query"}
            return self.search_public(query)

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = CodingAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
