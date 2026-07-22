#!/usr/bin/env python3
"""
DGTA Agent – Data Gathering & Tracking Agent
Collects, locates, and verifies data from sensors, APIs, and external sources.
"""

import os
import sys
import json
import subprocess
import argparse
import requests
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
STATE_FILE = os.path.join(BASE, "state", "dgta_agent.json")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")
SOURCE_OF_TRUTH = os.path.join(BASE, "README.md")

def log(msg):
    ts = datetime.now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} | dgta_agent | {msg}\n")
    print(msg)

def read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_task": None, "errors": []}

def write_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def run_hook(hook_path, *args):
    if not hook_path or not os.path.exists(hook_path):
        return True, ""
    cmd = [hook_path] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr

def read_source_of_truth():
    if os.path.exists(SOURCE_OF_TRUTH):
        with open(SOURCE_OF_TRUTH, "r") as f:
            return f.read()
    log("⚠️ Source of truth not found.")
    return ""

# ---------- SEARCH FUNCTIONS ----------
def search_github(query):
    """Search GitHub for repos related to the query."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": "updated", "order": "desc", "per_page": 10}
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("items", [])
            return [{"name": r["name"], "url": r["html_url"], "description": r.get("description", "")} for r in results]
        else:
            log(f"⚠️ GitHub API error: {resp.status_code}")
            return []
    except Exception as e:
        log(f"❌ GitHub search error: {e}")
        return []

def search_nvd(query):
    """Search National Vulnerability Database for CVEs."""
    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {"keywordSearch": query, "resultsPerPage": 10}
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("vulnerabilities", [])
            return [{"id": v["cve"]["id"], "description": v["cve"]["descriptions"][0]["value"] if v["cve"]["descriptions"] else ""} for v in results]
        else:
            log(f"⚠️ NVD API error: {resp.status_code}")
            return []
    except Exception as e:
        log(f"❌ NVD search error: {e}")
        return []

def search_all(query):
    """Search all sources for a query."""
    log(f"🔍 Searching all sources for: {query}")
    results = {
        "github": search_github(query),
        "nvd": search_nvd(query),
        "darkweb": []  # Placeholder for future Intelligence X integration
    }
    return results

# ---------- UPDATE FUNCTIONS ----------
def check_updates():
    log("📡 Checking for updates...")
    hook = os.path.join(BASE, "hooks", "check_updates.sh")
    success, output = run_hook(hook, "all")
    if success:
        log("✅ Update check completed.")
    else:
        log(f"❌ Update check failed: {output}")
    return success

# ---------- MAIN ----------
def main():
    parser = argparse.ArgumentParser(description="DGTA Agent")
    parser.add_argument("--task", required=True, help="Task to execute")
    parser.add_argument("--args", nargs="*", help="Arguments for the task")
    args = parser.parse_args()

    log(f"Task: {args.task}")
    state = read_state()
    state["last_task"] = args.task
    state["last_run"] = datetime.now().isoformat()

    # ---------- Global pre-action hook ----------
    agent_name = os.path.basename(__file__).replace(".py", "")
    success, output = run_hook(os.path.join(BASE, "hooks", "pre_action.sh"), agent_name)
    if not success:
        log("❌ Pre-action hook failed. Aborting.")
        sys.exit(1)

    task = args.task
    success = False

    try:
        if task == "check_updates":
            success = check_updates()
        elif task == "search":
            if not args.args:
                log("❌ No search query provided.")
                sys.exit(1)
            query = " ".join(args.args)
            results = search_all(query)
            print(json.dumps(results, indent=2))
            search_dir = os.path.join(BASE, "state", "searches")
            os.makedirs(search_dir, exist_ok=True)
            search_path = os.path.join(search_dir, f"search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(search_path, 'w') as f:
                json.dump(results, f, indent=2)
            log(f"📄 Search results saved to {search_path}")
            # Trigger Security Agent to scan the results
            sec_script = os.path.join(BASE, "agents", "security_agent", "security_agent.py")
            if os.path.exists(sec_script):
                subprocess.run(
                    [sys.executable, sec_script, "--scan", search_path],
                    capture_output=True, text=True
                )
                log("🛡️ Security Agent triggered to scan search results.")
            success = True
        else:
            log(f"⚠️ Unknown task: {task}")
            success = False
    except Exception as e:
        log(f"Error: {e}")
        success = False

    state["last_result"] = success
    write_state(state)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

# ---------- FETCH DOCUMENT (PDF, Text, HTML) ----------
def fetch_document(url):
    """Fetch a document (PDF, HTML, or text) from a URL."""
    log(f"📄 Fetching document from: {url}")
    try:
        import requests
        resp = requests.get(url, timeout=60)
        content_type = resp.headers.get('content-type', '')

        if 'application/pdf' in content_type:
            # Save PDF locally and extract text
            import PyPDF2
            from io import BytesIO
            pdf_file = BytesIO(resp.content)
            reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return {"type": "pdf", "content": text, "pages": len(reader.pages)}

        elif 'text/html' in content_type:
            # For HTML, return the raw text (could use BeautifulSoup to clean)
            return {"type": "html", "content": resp.text}

        else:
            # Treat as plain text
            return {"type": "text", "content": resp.text}

    except Exception as e:
        log(f"❌ Document fetch error: {e}")
        return {"type": "error", "content": str(e)}

# ---------- TOR / DARK WEB FETCH ----------
def fetch_darkweb(query):
    """Search the dark web via Tor using Ahmia or similar .onion search engines."""
    log(f"🌑 Dark web search: {query}")
    import requests
    session = requests.Session()
    session.proxies = {
        'http': 'socks5h://127.0.0.1:9050',
        'https': 'socks5h://127.0.0.1:9050'
    }
    session.headers = {'User-Agent': 'MycelialTorScout/1.0'}

    # Use Ahmia (a public .onion search engine) – you can also use Tor66, etc.
    # Ahmia's .onion URL (you can also use their clearnet gateway if .onion is blocked)
    # For .onion access: http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=query
    # For now, use the clearnet gateway (but you can switch to .onion later)
    url = f"https://ahmia.fi/search/?q={query}"
    try:
        resp = session.get(url, timeout=60)
        if resp.status_code == 200:
            # Parse HTML to extract results – for now, just return the raw text
            return {"type": "html", "content": resp.text[:5000], "source": "ahmia"}
        else:
            log(f"⚠️ Dark web search error: {resp.status_code}")
            return {"type": "error", "content": f"HTTP {resp.status_code}"}
    except Exception as e:
        log(f"❌ Tor request error: {e}")
        return {"type": "error", "content": str(e)}

# Add to main dispatch
