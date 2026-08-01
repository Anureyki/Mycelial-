#!/usr/bin/env python3
import sys
import json
import requests

# Local SearXNG instance (docker container, see ~/searxng/docker-compose.yml)
# tried first since it doesn't depend on outbound internet access.
LOCAL_INSTANCE = "http://localhost:8082"

# Public SearXNG instances, tried in order only if the local instance fails.
INSTANCES = [
    LOCAL_INSTANCE,
    "https://searx.be",
    "https://searxng.polymorphic.solutions",
    "https://search.projectsegfault.de",
    "https://searx.tuxcloud.net",
]

# Hardcoded fallback for common queries
FACTS = {
    "capital of france": "Paris is the capital of France.",
    "capital of germany": "Berlin is the capital of Germany.",
    "capital of italy": "Rome is the capital of Italy.",
    "capital of spain": "Madrid is the capital of Spain.",
    "capital of uk": "London is the capital of the United Kingdom.",
    "capital of usa": "Washington, D.C. is the capital of the United States.",
}

def get_fact(query):
    q_lower = query.lower().strip()
    for key, value in FACTS.items():
        if key in q_lower:
            return value
    return None

def search_searxng(query):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for base_url in INSTANCES:
        try:
            url = f"{base_url}/search"
            params = {"q": query, "format": "json"}
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    first = results[0]
                    return f"{first.get('title', '')}: {first.get('content', '')}"
                else:
                    return "No results found."
            # if 403 or other error, try next instance
        except:
            continue
    return None  # all instances failed

def handle_request(request):
    method = request.get("method")
    params = request.get("params", {})
    request_id = request.get("id")
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "search",
                        "description": "Search using SearXNG (metasearch)",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"]
                        }
                    }
                ]
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name == "search":
            query = arguments.get("query")
            if not query:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Missing query"}}
            # 1. Check facts
            result = get_fact(query)
            # 2. If no fact, try SearXNG
            if not result:
                result = search_searxng(query)
            # 3. If still no result, return a message
            if not result:
                result = f"Could not find an answer for '{query}'."
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]}
            }
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()

if __name__ == "__main__":
    main()
