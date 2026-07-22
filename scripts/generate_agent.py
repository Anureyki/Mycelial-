#!/usr/bin/env python3
"""
generate_agent.py – Generate agent Python scripts from .md definitions.
Includes A2A server (HTTP/JSON‑RPC) for inter‑agent communication.
"""

import os, sys, yaml, json, subprocess, argparse, uuid, time
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
AGENTS_DIR = os.path.join(BASE, "agents")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")

def parse_md(md_path):
    with open(md_path, 'r') as f:
        content = f.read()
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            rest = parts[2]
            return frontmatter, rest
    return {}, content

def generate_py(agent_name, data):
    caps = data.get('capabilities', [])
    pre_hook = data.get('hooks', {}).get('pre', '')
    post_hook = data.get('hooks', {}).get('post', '')
    permissions = data.get('permissions', [])

    # Build the if/elif/else chain with proper indentation inside try
    cap_handlers = []
    for cap in caps:
        cap_handlers.append(f'    elif task == "{cap}":\n        log(f"Executing {cap} with args {{args.args}}")\n        success = True')
    if cap_handlers:
        cap_switch = '\n'.join(cap_handlers)
        cap_switch += '\n    else:\n        log(f"Unknown task: {task}")\n        success = False'
    else:
        cap_switch = '    else:\n        log(f"Unknown task: {task}")\n        success = False'

    port_map = {
        "codingagent": 8001,
        "security_agent": 8002,
        "datagatherer": 8003,
        "agriculture_agent": 8004,
        "boss_agent": 8005
    }
    port = port_map.get(agent_name, 8000)

    template = f'''#!/usr/bin/env python3
"""
Auto‑generated agent: {agent_name}
Do not edit manually – changes go into {agent_name}.md
"""
import sys, os, subprocess, argparse, json, uuid, time, threading
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
STATE_FILE = os.path.join(BASE, "state", "{agent_name}.json")
KNOWLEDGE_DIR = os.path.join(BASE, "knowledge")
REGISTRY_FILE = os.path.join(BASE, "state", "registry.json")
PENDING_DIR = os.path.join(BASE, "state", "pending_requests")
PRE_HOOK = os.path.join(BASE, "hooks", "{pre_hook}") if "{pre_hook}" else None
POST_HOOK = os.path.join(BASE, "hooks", "{post_hook}") if "{post_hook}" else None

PORT = {port}

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{{ts}} | {agent_name} | {{msg}}\\n")
    print(msg)

def store_outcome(task, agent, success, error=None):
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    fname = os.path.join(KNOWLEDGE_DIR, f"outcome_{{datetime.now().strftime('%Y%m%d_%H%M%S')}}.json")
    with open(fname, "w") as f:
        json.dump({{
            "task": task,
            "agent": "{agent_name}",
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "error": error
        }}, f)

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
    return {{"last_task": None, "errors": []}}

def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------- A2A REGISTRY ----------
def register_agent():
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    registry = {{}}
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, 'r') as f:
            registry = json.load(f)
    registry["{agent_name}"] = {{
        "url": f"http://localhost:{{PORT}}",
        "capabilities": {caps},
        "last_seen": datetime.now().isoformat()
    }}
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2)
    log(f"✅ Registered as {agent_name} on port {{PORT}}")

# ---------- PERMISSION REQUEST ----------
def request_permission(target, task, args=None):
    req_id = str(uuid.uuid4())
    req_path = os.path.join(PENDING_DIR, f"{{req_id}}.json")
    os.makedirs(PENDING_DIR, exist_ok=True)
    req_data = {{
        "request_id": req_id,
        "requester": "{agent_name}",
        "target": target,
        "task": task,
        "args": args,
        "timestamp": datetime.now().isoformat(),
        "status": "pending"
    }}
    with open(req_path, "w") as f:
        json.dump(req_data, f)
    log(f"🛑 Permission request sent to Boss (ID: {{req_id}})")
    timeout = 60
    while timeout > 0:
        if not os.path.exists(req_path):
            log("❌ Request file deleted – assuming denied.")
            return False
        with open(req_path, "r") as f:
            data = json.load(f)
        if data["status"] != "pending":
            log(f"✅ Permission {{data['status']}} for {{req_id}}")
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
        log(f"❌ Agent {{target}} not in registry.")
        return False
    url = registry[target]["url"] + "/execute"
    payload = {{
        "jsonrpc": "2.0",
        "method": "execute",
        "params": {{"task": task, "args": args or []}},
        "id": req_id
    }}
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            log(f"✅ A2A delegation to {{target}} succeeded.")
            return True
        else:
            log(f"❌ A2A delegation failed: {{resp.status_code}}")
            return False
    except Exception as e:
        log(f"❌ A2A request error: {{e}}")
        return False

# ---------- MAIN CLI ----------
def main():
    parser = argparse.ArgumentParser(description="{agent_name}")
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
            return {{
                "name": "{agent_name}",
                "capabilities": {caps},
                "endpoint": "/execute",
                "hooks": {{"pre": "{pre_hook}", "post": "{post_hook}"}}
            }}

        @app.post("/execute")
        async def execute(request: Request):
            data = await request.json()
            task = data.get("params", {{}}).get("task")
            args_list = data.get("params", {{}}).get("args", [])
            log(f"📥 Received A2A task: {{task}}")
            cmd = [sys.executable, __file__, "--task", task] + args_list
            result = subprocess.run(cmd, capture_output=True, text=True)
            return {{
                "jsonrpc": "2.0",
                "result": {{"status": "ok", "output": result.stdout}},
                "id": data.get("id")
            }}

        register_agent()
        log(f"🚀 Starting A2A server on port {{PORT}}")
        uvicorn.run(app, host="0.0.0.0", port=PORT)
        return

    # Normal CLI mode
    if args.task is None:
        log("❌ No task specified. Use --task or --serve.")
        sys.exit(1)

    log(f"Task: {{args.task}}")
    state = read_state()
    state["last_task"] = args.task
    state["last_run"] = datetime.now().isoformat()

    # ---------- Global pre-action hook ----------
    agent_name = os.path.basename(__file__).replace(".py", "")
    success, output = run_hook(os.path.join(BASE, "hooks", "pre_action.sh"), agent_name)
    if not success:
        log("❌ Pre-action hook failed. Aborting.")
        sys.exit(1)

    if PRE_HOOK:
        ok, out = run_hook(PRE_HOOK)
        if not ok:
            log(f"Pre‑hook failed: {{out}}")
            store_outcome(args.task, "{agent_name}", False, f"Pre‑hook: {{out}}")
            sys.exit(1)

    success = False
    error = None
    task = args.task

    try:
        if task == "unknown":
            pass
{cap_switch}
    except Exception as e:
        error = str(e)
        log(f"Error: {{error}}")
        success = False

    if POST_HOOK:
        ok, out = run_hook(POST_HOOK)
        if not ok:
            log(f"Post‑hook failed: {{out}}")
            store_outcome(args.task, "{agent_name}", False, f"Post‑hook: {{out}}")
            sys.exit(1)

    store_outcome(args.task, "{agent_name}", success, error)
    state["last_result"] = success
    write_state(state)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
'''
    return template

def main():
    if not os.path.exists(AGENTS_DIR):
        print(f"❌ Agents directory not found: {AGENTS_DIR}")
        sys.exit(1)
    generated = 0
    for md_file in os.listdir(AGENTS_DIR):
        if md_file.endswith('.md') and md_file != 'README.md':
            agent_name = md_file.replace('.md', '')
            md_path = os.path.join(AGENTS_DIR, md_file)
            data, _ = parse_md(md_path)
            py_path = os.path.join(AGENTS_DIR, f"{agent_name}.py")
            with open(py_path, 'w') as f:
                f.write(generate_py(agent_name, data))
            os.chmod(py_path, 0o755)
            print(f"✅ Generated {agent_name}.py")
            generated += 1
    print(f"🎉 Regenerated {generated} agent scripts.")

if __name__ == "__main__":
    main()
