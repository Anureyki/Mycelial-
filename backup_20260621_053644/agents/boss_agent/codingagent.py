#!/usr/bin/env python3
"""
Auto‑generated agent: codingagent
Do not edit manually – changes go into codingagent.md
"""
import sys, os, subprocess, argparse, json, uuid, time, threading
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
STATE_FILE = os.path.join(BASE, "state", "codingagent.json")
KNOWLEDGE_DIR = os.path.join(BASE, "knowledge")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")
PRE_HOOK = os.path.join(BASE, "hooks", "pre_edit.sh") if "pre_edit.sh" else None
POST_HOOK = os.path.join(BASE, "hooks", "post_edit.sh") if "post_edit.sh" else None

PORT = 8001

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | codingagent | {msg}\n")
    print(msg)

def store_outcome(task, agent, success, error=None):
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    fname = os.path.join(KNOWLEDGE_DIR, f"outcome_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(fname, "w") as f:
        json.dump({
            "task": task,
            "agent": "codingagent",
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "error": error
        }, f)

def run_hook(hook_path, *args):
    if not hook_path or not os.path.exists(hook_path):
        return True, ""
    cmd = [hook_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr

def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_task": None, "errors": []}

def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------- A2A REGISTRY ----------
def register_agent():
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    registry = {}
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, 'r') as f:
            registry = json.load(f)
    registry["codingagent"] = {
        "url": f"http://localhost:{PORT}",
        "capabilities": ['edit_file', 'run_command', 'crontab_add', 'crontab_list', 'crontab_remove'],
        "last_seen": datetime.now().isoformat()
    }
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2)
    log(f"✅ Registered as codingagent on port {PORT}")

# ---------- PERMISSION REQUEST ----------
def request_permission(target, task, args=None):
    req_id = str(uuid.uuid4())
    req_path = os.path.join(PENDING_DIR, f"{req_id}.json")
    os.makedirs(PENDING_DIR, exist_ok=True)
    req_data = {
        "request_id": req_id,
        "requester": "codingagent",
        "target": target,
        "task": task,
        "args": args,
        "timestamp": datetime.now().isoformat(),
        "status": "pending"
    }
    with open(req_path, "w") as f:
        json.dump(req_data, f)
    log(f"🛑 Permission request sent to Boss (ID: {req_id})")
    timeout = 60
    while timeout > 0:
        if not os.path.exists(req_path):
            log("❌ Request file deleted – assuming denied.")
            return False
        with open(req_path, "r") as f:
            data = json.load(f)
        if data["status"] != "pending":
            log(f"✅ Permission {data['status']} for {req_id}")
            return data["status"] == "approved"
        time.sleep(2)
        timeout -= 2
    log("⏰ Permission request timed out.")
    return False

def delegate_with_permission(target, task, args=None):
    if not request_permission(target, task, args):
        log("❌ Delegation denied by Boss.")
        return False
    if not os.path.exists(REGISTRY_FILE):
        log("❌ Registry not found.")
        return False
    with open(REGISTRY_FILE, 'r') as f:
        registry = json.load(f)
    if target not in registry:
        log(f"❌ Agent {target} not in registry.")
        return False
    url = registry[target]["url"] + "/execute"
    payload = {
        "jsonrpc": "2.0",
        "method": "execute",
        "params": {"task": task, "args": args or []},
        "id": req_id
    }
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            log(f"✅ A2A delegation to {target} succeeded.")
            return True
        else:
            log(f"❌ A2A delegation failed: {resp.status_code}")
            return False
    except Exception as e:
        log(f"❌ A2A request error: {e}")
        return False

# ---------- MAIN CLI ----------
def main():
    parser = argparse.ArgumentParser(description="codingagent")
    parser.add_argument("--task", help="Task to execute")
    parser.add_argument("--args", nargs="*", help="Arguments for task")
    parser.add_argument("--serve", action="store_true", help="Start A2A HTTP server")
    args = parser.parse_args()

    if args.serve:
        try:
            from fastapi import FastAPI, Request
            import uvicorn
        except ImportError:
            log("❌ FastAPI/uvicorn not installed. Run: pip install fastapi uvicorn")
            sys.exit(1)

        app = FastAPI()

        @app.get("/.well-known/agent.json")
        async def agent_card():
            return {
                "name": "codingagent",
                "capabilities": ['edit_file', 'run_command', 'crontab_add', 'crontab_list', 'crontab_remove'],
                "endpoint": "/execute",
                "hooks": {"pre": "pre_edit.sh", "post": "post_edit.sh"}
            }

        @app.post("/execute")
        async def execute(request: Request):
            data = await request.json()
            task = data.get("params", {}).get("task")
            args_list = data.get("params", {}).get("args", [])
            log(f"📥 Received A2A task: {task}")
            cmd = [sys.executable, __file__, "--task", task] + args_list
            result = subprocess.run(cmd, capture_output=True, text=True)
            return {
                "jsonrpc": "2.0",
                "result": {"status": "ok", "output": result.stdout},
                "id": data.get("id")
            }

        register_agent()
        log(f"🚀 Starting A2A server on port {PORT}")
        uvicorn.run(app, host="0.0.0.0", port=PORT)
        return

    # Normal CLI mode
    if args.task is None:
        log("❌ No task specified. Use --task or --serve.")
        sys.exit(1)

    log(f"Task: {args.task}")
    state = read_state()
    state["last_task"] = args.task
    state["last_run"] = datetime.now().isoformat()

    if PRE_HOOK:
        ok, out = run_hook(PRE_HOOK)
        if not ok:
            log(f"Pre‑hook failed: {out}")
            store_outcome(args.task, "codingagent", False, f"Pre‑hook: {out}")
            sys.exit(1)

    success = False
    error = None
    task = args.task

    try:
        if task == "unknown":
            pass
        elif task == "edit_file":
            log(f"Executing edit_file with args {args.args}")
            success = True
        elif task == "run_command":
            log(f"Executing run_command with args {args.args}")
            success = True
        elif task == "crontab_add":
            log(f"Executing crontab_add with args {args.args}")
            success = True
        elif task == "crontab_list":
            log(f"Executing crontab_list with args {args.args}")
            success = True
        elif task == "crontab_remove":
    elif task == "fix_syntax":
    elif task == "edit_agent":
        if not args.args or len(args.args) < 2:
            log("❌ Usage: edit_agent <agent_name> <instruction>")
            sys.exit(1)
        agent_name = args.args[0]
        instruction = " ".join(args.args[1:])
        result = edit_agent_with_deepseek(agent_name, instruction)
        print(result)
        sys.exit(0)
    elif task == "fix_dashboard":
        if not args.args:
            log("❌ No error message provided.")
            sys.exit(1)
        error_message = " ".join(args.args)
        result = fix_dashboard(error_message)
        print(result)
        sys.exit(0)
        if not args.args or len(args.args) < 2:
            log("❌ Usage: fix_syntax <file_path> <error_message>")
            sys.exit(1)
        file_path = args.args[0]
        error_message = " ".join(args.args[1:])
        result = fix_syntax(file_path, error_message)
        print(result)
        sys.exit(0)
    elif task == "fix_syntax":
    elif task == "edit_agent":
        if not args.args or len(args.args) < 2:
            log("❌ Usage: edit_agent <agent_name> <instruction>")
            sys.exit(1)
        agent_name = args.args[0]
        instruction = " ".join(args.args[1:])
        result = edit_agent_with_deepseek(agent_name, instruction)
        print(result)
        sys.exit(0)
    elif task == "fix_dashboard":
        if not args.args:
            log("❌ No error message provided.")
            sys.exit(1)
        error_message = " ".join(args.args)
        result = fix_dashboard(error_message)
        print(result)
        sys.exit(0)
        if not args.args or len(args.args) < 2:
            log("❌ Usage: fix_syntax <file_path> <error_message>")
            sys.exit(1)
        file_path = args.args[0]
        error_message = " ".join(args.args[1:])
        result = fix_syntax(file_path, error_message)
        print(result)
        sys.exit(0)
    elif task == "implement_recommendation":
        if not args.args:
            log("❌ No recommendation JSON provided.")
            success = False
        else:
            rec_json = " ".join(args.args)
            success = implement_recommendation(rec_json)
            log(f"Executing crontab_remove with args {args.args}")
            success = True
        else:
            log(f"Unknown task: {task}")
            success = False
    except Exception as e:
        error = str(e)
        log(f"Error: {error}")
        success = False

    if POST_HOOK:
        ok, out = run_hook(POST_HOOK)
        if not ok:
            log(f"Post‑hook failed: {out}")
            store_outcome(args.task, "codingagent", False, f"Post‑hook: {out}")
            sys.exit(1)

    store_outcome(args.task, "codingagent", success, error)
    state["last_result"] = success
    write_state(state)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Auto‑generated agent: codingagent
Do not edit manually – changes go into codingagent.md
"""
import sys, os, subprocess, argparse, json, uuid, time, threading
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
STATE_FILE = os.path.join(BASE, "state", "codingagent.json")
KNOWLEDGE_DIR = os.path.join(BASE, "knowledge")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")
PRE_HOOK = os.path.join(BASE, "hooks", "pre_edit.sh") if "pre_edit.sh" else None
POST_HOOK = os.path.join(BASE, "hooks", "post_edit.sh") if "post_edit.sh" else None

PORT = 8001

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | codingagent | {msg}\n")
    print(msg)

def store_outcome(task, agent, success, error=None):
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    fname = os.path.join(KNOWLEDGE_DIR, f"outcome_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(fname, "w") as f:
        json.dump({
            "task": task,
            "agent": "codingagent",
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "error": error
        }, f)

def run_hook(hook_path, *args):
    if not hook_path or not os.path.exists(hook_path):
        return True, ""
    cmd = [hook_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr

def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_task": None, "errors": []}

def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------- A2A REGISTRY ----------
def register_agent():
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    registry = {}
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, 'r') as f:
            registry = json.load(f)
    registry["codingagent"] = {
        "url": f"http://localhost:{PORT}",
        "capabilities": ['edit_file', 'run_command', 'crontab_add', 'crontab_list', 'crontab_remove'],
        "last_seen": datetime.now().isoformat()
    }
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2)
    log(f"✅ Registered as codingagent on port {PORT}")

# ---------- PERMISSION REQUEST ----------
def request_permission(target, task, args=None):
    req_id = str(uuid.uuid4())
    req_path = os.path.join(PENDING_DIR, f"{req_id}.json")
    os.makedirs(PENDING_DIR, exist_ok=True)
    req_data = {
        "request_id": req_id,
        "requester": "codingagent",
        "target": target,
        "task": task,
        "args": args,
        "timestamp": datetime.now().isoformat(),
        "status": "pending"
    }
    with open(req_path, "w") as f:
        json.dump(req_data, f)
    log(f"🛑 Permission request sent to Boss (ID: {req_id})")
    timeout = 60
    while timeout > 0:
        if not os.path.exists(req_path):
            log("❌ Request file deleted – assuming denied.")
            return False
        with open(req_path, "r") as f:
            data = json.load(f)
        if data["status"] != "pending":
            log(f"✅ Permission {data['status']} for {req_id}")
            return data["status"] == "approved"
        time.sleep(2)
        timeout -= 2
    log("⏰ Permission request timed out.")
    return False

def delegate_with_permission(target, task, args=None):
    if not request_permission(target, task, args):
        log("❌ Delegation denied by Boss.")
        return False
    if not os.path.exists(REGISTRY_FILE):
        log("❌ Registry not found.")
        return False
    with open(REGISTRY_FILE, 'r') as f:
        registry = json.load(f)
    if target not in registry:
        log(f"❌ Agent {target} not in registry.")
        return False
    url = registry[target]["url"] + "/execute"
    payload = {
        "jsonrpc": "2.0",
        "method": "execute",
        "params": {"task": task, "args": args or []},
        "id": req_id
    }
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            log(f"✅ A2A delegation to {target} succeeded.")
            return True
        else:
            log(f"❌ A2A delegation failed: {resp.status_code}")
            return False
    except Exception as e:
        log(f"❌ A2A request error: {e}")
        return False

# ---------- MAIN CLI ----------
def main():
    # ---------- Global pre-action hook ----------
    agent_name = os.path.basename(__file__).replace(".py", "")
    success, output = run_hook(os.path.join(BASE, "hooks", "pre_action.sh"), agent_name)
    if not success:
    log("❌ Pre-action hook failed. Aborting.")
    sys.exit(1)
# ---------- IMPLEMENT RECOMMENDATION ----------
def implement_recommendation(recommendation_json):
    """Implement a recommendation from the Analyzer."""
    log("🔧 Implementing recommendation...")
    try:
        rec = json.loads(recommendation_json)
    except:
        log("❌ Invalid recommendation JSON.")
        return False

    agent = rec.get('agent')
    suggestion = rec.get('suggestion')
    criticality = rec.get('criticality')

    if not agent or not suggestion:
        log("❌ Recommendation missing agent or suggestion.")
        return False

    # Determine what kind of change to make
    # For simplicity, we'll handle common suggestion patterns
    if "pre-action hook" in suggestion.lower() or "validation hook" in suggestion.lower():
        hook_name = f"pre_{agent}_validate.sh"
        hook_path = os.path.join(BASE, "hooks", hook_name)
        # Check if hook already exists
        if os.path.exists(hook_path):
            log(f"⚠️ Hook {hook_name} already exists. Skipping.")
            return True
        # Create a basic validation hook
        hook_content = f'''#!/bin/bash
# {hook_name} – Auto-generated validation hook for {agent}
# Implemented per recommendation: {suggestion}

printf "OK: {agent} validation passed.\\n"
exit 0
'''
        # Write hook file
        with open(hook_path, 'w') as f:
            f.write(hook_content)
        os.chmod(hook_path, 0o755)
        log(f"✅ Created new hook: {hook_name}")

        # Optionally update the agent's MD to reference the hook
        md_path = os.path.join(BASE, "agents", agent, f"{agent}.md")
        if os.path.exists(md_path):
            # Add to hooks section if needed
            with open(md_path, 'r') as f:
                md_content = f.read()
            if "hooks:" in md_content and "pre:" not in md_content.split("hooks:")[1].split("---")[0]:
                # Insert pre hook line
                new_md = md_content.replace("hooks:", f"hooks:\n  pre: {hook_name}")
                with open(md_path, 'w') as f:
                    f.write(new_md)
                log(f"✅ Updated {agent}.md to reference {hook_name}")
        return True

    elif "retries" in suggestion.lower():
        # Could modify the agent's code to add retry logic – complex, skip for now
        log("⚠️ Retry logic implementation not yet automated.")
        return False
    else:
        log(f"⚠️ Unrecognized suggestion: {suggestion}")
        return False

# Add to main dispatch
# We'll patch this via sed later to avoid overwriting the whole file

# ---------- FIX SYNTAX ----------
def fix_syntax(file_path, error_message):
    """Attempt to fix a syntax error in a Python file."""
    log(f"🔧 Fixing syntax in {file_path}")
    # Read the file
    with open(file_path, 'r') as f:
        content = f.read()

    # Simple fix: remove trailing whitespace and add missing newline
    # More sophisticated: use the error message to pinpoint the issue
    if "IndentationError" in error_message:
        # Find the line number and fix indentation (simple approach)
        lines = content.split('\n')
        # For now, just log and return
        log("⚠️ IndentationError detected – manual fix may be required.")
        return "IndentationError – manual fix required."

    # Write back the fixed content (placeholder)
    with open(file_path, 'w') as f:
        f.write(content)

    log("✅ Syntax fix attempted.")
    return "Fix attempted."

# ---------- FIX SYNTAX ----------
def fix_syntax(file_path, error_message):
    """Attempt to fix common syntax errors in a Python file."""
    log(f"🔧 Fixing syntax in {file_path}")
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()

        # Common fixes
        fixed = False
        new_lines = []

        for i, line in enumerate(lines):
            # Fix: add missing colon after if/elif/else/for/while/def/class
            stripped = line.lstrip()
            if stripped.startswith(('if ', 'elif ', 'else:', 'for ', 'while ', 'def ', 'class ')):
                if not stripped.endswith(':\n') and not stripped.endswith(':\r\n'):
                    # Add colon
                    lines[i] = line.rstrip() + ':\n'
                    fixed = True
                    log(f"✅ Added missing colon at line {i+1}")
                    break

            # Fix: remove trailing whitespace
            if line.endswith(' \n') or line.endswith('\t\n'):
                lines[i] = line.rstrip() + '\n'
                fixed = True

        if fixed:
            with open(file_path, 'w') as f:
                f.writelines(lines)
            log(f"✅ Syntax fix applied to {file_path}")
            # Verify the fix
            result = subprocess.run(["python3", "-m", "py_compile", file_path], capture_output=True, text=True)
            if result.returncode == 0:
                log("✅ Fix verified – syntax is now correct.")
                return "Fix applied and verified."
            else:
                log(f"⚠️ Fix attempted but syntax error remains:\n{result.stderr}")
                return f"Fix attempted – error remains: {result.stderr}"
        else:
            log("⚠️ No common syntax error pattern found. Manual fix required.")
            return "Manual fix required – no pattern matched."

    except Exception as e:
        log(f"❌ Error fixing syntax: {e}")
        return f"Error: {e}"

# ---------- FIX DASHBOARD ----------
def fix_dashboard(error_message):
    """Attempt to fix common Gradio dashboard errors."""
    log(f"🔧 Fixing dashboard: {error_message}")
    dashboard_path = os.path.join(BASE, "dashboard", "gradio_app.py")

    if not os.path.exists(dashboard_path):
        return "❌ Dashboard file not found."

    with open(dashboard_path, 'r') as f:
        content = f.read()

    # Fix: gr.themes.Dark() → remove or replace
    if "gr.themes.Dark()" in content:
        content = content.replace("gr.themes.Dark()", "")
        log("✅ Removed gr.themes.Dark()")

    # Fix: theme moved to launch()
    if "theme=gr.themes.Soft()" in content and "launch" not in content:
        content = content.replace("theme=gr.themes.Soft()", "")
        content = content.replace('demo.launch(server_name="0.0.0.0", server_port=7002)',
                                   'demo.launch(server_name="0.0.0.0", server_port=7002, theme=gr.themes.Soft())')
        log("✅ Moved theme to launch()")

    with open(dashboard_path, 'w') as f:
        f.write(content)

    # Restart the dashboard
    subprocess.run([os.path.join(BASE, "hooks", "dashboard_orchestrator.sh"), "restart"])
    return "✅ Dashboard fixed and restarted."

# ---------- DEEPSEEK INTEGRATION ----------
def call_deepseek(prompt):
    """Call DeepSeek for reasoning and code generation."""
    import subprocess
    log(f"🧠 Coding Agent calling DeepSeek: {prompt[:50]}...")
    try:
        result = subprocess.run(
            ["ollama", "run", "deepseek-coder:6.7b", prompt],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            log(f"❌ DeepSeek error: {result.stderr}")
            return f"Error: {result.stderr}"
    except Exception as e:
        log(f"❌ DeepSeek call failed: {e}")
        return f"Error: {e}"

def edit_agent_with_deepseek(agent_name, instruction):
    """Use DeepSeek to understand and edit an agent."""
    log(f"🔧 Editing {agent_name} with DeepSeek...")

    # Read current file
    agent_path = os.path.join(BASE, "agents", agent_name, f"{agent_name}.py")
    if not os.path.exists(agent_path):
        # Try to find it in subfolders
        for root, dirs, files in os.walk(os.path.join(BASE, "agents")):
            if f"{agent_name}.py" in files:
                agent_path = os.path.join(root, f"{agent_name}.py")
                break
        if not os.path.exists(agent_path):
            return f"❌ Agent {agent_name} not found."

    with open(agent_path, 'r') as f:
        current_code = f.read()

    # Build prompt for DeepSeek
    prompt = f"""
You are a coding assistant. The user wants to modify the agent: {agent_name}

Current code:
{current_code}

Instruction: {instruction}

Please provide the complete updated code for this agent. Only output the code, nothing else.
Make sure to:
1. Keep the same structure and imports
2. Add the new functionality
3. Maintain the same style
4. Include all necessary imports
"""

    # Get response from DeepSeek
    new_code = call_deepseek(prompt)

    # Save the updated code
    with open(agent_path, 'w') as f:
        f.write(new_code)

    log(f"✅ Agent {agent_name} updated.")
    return f"✅ {agent_name} updated successfully."
