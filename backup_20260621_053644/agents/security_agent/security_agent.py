#!/usr/bin/env python3
"""
Security Agent – Mycelial Network
Scans, quarantines, eliminates threats, audits npm packages, and handles threats.
"""

import os, sys, json, subprocess, argparse, hashlib
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
STATE_FILE = os.path.join(BASE, "state", "security_agent.json")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
QUARANTINE_DIR = os.path.join(BASE, "quarantine")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | security_agent | {msg}\n")
    print(msg)

def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"scans": [], "quarantined": [], "eliminated": []}

def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def run_hook(hook_path, *args):
    if not os.path.exists(hook_path):
        log(f"⚠️ Hook {hook_path} not found.")
        return True, ""
    cmd = [hook_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        log(f"✅ Hook {hook_path} passed.")
        return True, output
    else:
        log(f"❌ Hook {hook_path} failed:\n{output}")
        return False, output

# ---------- NPM AUDIT ----------
def npm_audit():
    """Audit globally installed npm packages for known malicious packages."""
    log("🔍 Auditing npm packages...")
    infected = ["@squawk", "@tstack", "@uipath", "@tallyui", "@beproduct", "@mistralai", "@draftlab", "@taskflow-corp", "@tolk"]
    found = []
    try:
        result = subprocess.run(
            ["npm", "list", "-g", "--depth=0"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            installed = result.stdout
            for pkg in infected:
                if pkg in installed:
                    found.append(pkg)
            if found:
                log(f"⚠️ INFECTED PACKAGES FOUND: {found}")
                with open(os.path.join(BASE, "logs", "npm_audit.log"), "a") as f:
                    f.write(f"{datetime.now().isoformat()} | INFECTED: {found}\n")
                return {"status": "infected", "packages": found}
            else:
                log("✅ No infected packages found.")
                return {"status": "clean", "packages": []}
        else:
            log("❌ npm audit failed.")
            return {"status": "error", "packages": []}
    except Exception as e:
        log(f"❌ npm audit error: {e}")
        return {"status": "error", "packages": []}

# ---------- THREAT INTELLIGENCE ----------
def fetch_threat_intel():
    """Fetch known threat signatures from trusted sources."""
    threats = {
        "npm_worm_2026": {
            "packages": ["@squawk", "@tstack", "@uipath", "@tallyui", "@beproduct", "@mistralai", "@draftlab", "@taskflow-corp", "@tolk"],
            "type": "supply_chain_attack",
            "source": "social_media_reports",
            "verified": False,
            "action": "audit"
        }
    }
    return threats

def verify_threat(threat_name):
    """Verify a threat against official sources before acting."""
    log(f"🔍 Verifying threat: {threat_name}")
    try:
        result = subprocess.run(["npm", "audit", "--json"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            audit_data = json.loads(result.stdout)
            threat = fetch_threat_intel().get(threat_name)
            if threat:
                found = []
                advisories = audit_data.get("advisories", {})
                for adv_id, adv_data in advisories.items():
                    pkg_name = adv_data.get("package", "")
                    if pkg_name in threat.get("packages", []):
                        found.append(pkg_name)
                if found:
                    log(f"✅ Threat verified: {found} found in npm audit")
                    return {"verified": True, "packages": found}
    except Exception as e:
        log(f"⚠️ Verification error: {e}")
    log("⚠️ Threat not verified by official sources.")
    return {"verified": False, "packages": []}

def handle_threat(threat_name):
    """Handle a verified threat."""
    log(f"🛡️ Handling threat: {threat_name}")
    threat_info = fetch_threat_intel().get(threat_name)
    if not threat_info:
        return "Threat not found."

    verification = verify_threat(threat_name)
    if not verification["verified"]:
        log("⚠️ Threat unverified. No action taken.")
        return "Threat unverified. No action taken."

    packages = verification.get("packages", [])
    removed = []
    for pkg in packages:
        log(f"🚨 Infected package detected: {pkg}")
        result = subprocess.run(["npm", "uninstall", "-g", pkg], capture_output=True, text=True)
        removed.append(pkg)
        log(f"📦 Uninstalled {pkg}: {result.stdout}")

    with open(os.path.join(BASE, "logs", "threat_incidents.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | THREAT_HANDLED | {threat_name} | packages: {packages}\n")

    return f"✅ Threat {threat_name} handled. Packages removed: {removed}"

# ---------- SCAN, QUARANTINE, ELIMINATE ----------
def scan_file(filepath):
    log(f"🔍 Scanning {filepath}")
    success, _ = run_hook(os.path.join(BASE, "hooks", "pre_scan.sh"), filepath)
    if not success:
        return False
    log("✅ Scan passed (simulated).")
    run_hook(os.path.join(BASE, "hooks", "post_scan.sh"), filepath, "CLEAN", "No threats found")
    return True

def quarantine_file(filepath, reason):
    log(f"⚠️ Quarantining {filepath}: {reason}")
    hook = os.path.join(BASE, "hooks", "quarantine.sh")
    return run_hook(hook, filepath, reason)[0]

def eliminate_file(filepath, reason, source):
    log(f"⚠️ Elimination requested for {filepath}: {reason}")
    hook = os.path.join(BASE, "hooks", "eliminate.sh")
    return run_hook(hook, filepath, reason, source)[0]

# ---------- MAIN ----------
def main():
    parser = argparse.ArgumentParser(description="Mycelial Security Agent")
    parser.add_argument("--task", help="Task to perform")
    parser.add_argument("--scan", help="File to scan")
    parser.add_argument("--quarantine", help="File to quarantine")
    parser.add_argument("--eliminate", help="File to eliminate")
    parser.add_argument("--audit", action="store_true", help="Run system audit")
    parser.add_argument("--npm_audit", action="store_true", help="Audit npm packages")
    parser.add_argument("--handle_threat", help="Threat name to handle")
    parser.add_argument("--reason", default="No reason provided")
    parser.add_argument("--source", default="unknown")
    args = parser.parse_args()

    log("🛡️ Security Agent started.")
    state = read_state()
    success = False

    # Global pre-action hook
    agent_name = os.path.basename(__file__).replace(".py", "")
    ok, _ = run_hook(os.path.join(BASE, "hooks", "pre_action.sh"), agent_name)
    if not ok:
        log("❌ Pre-action hook failed. Aborting.")
        sys.exit(1)

    if args.task == "npm_audit" or args.npm_audit:
        result = npm_audit()
        print(json.dumps(result, indent=2))
        success = True
    elif args.task == "handle_threat":
        if not args.handle_threat:
            log("❌ No threat name provided.")
            success = False
        else:
            result = handle_threat(args.handle_threat)
            print(result)
            success = True
    elif args.scan:
        success = scan_file(args.scan)
    elif args.quarantine:
        success = quarantine_file(args.quarantine, args.reason)
    elif args.eliminate:
        success = eliminate_file(args.eliminate, args.reason, args.source)
    elif args.audit:
        log("📋 System audit not yet implemented.")
        success = True
    else:
        log("⚠️ No action specified. Use --task npm_audit, --scan, --quarantine, or --eliminate.")
        success = False

    write_state(state)
    log("🛡️ Security Agent finished.")
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
