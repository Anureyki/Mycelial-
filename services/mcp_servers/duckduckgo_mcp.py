#!/usr/bin/env python3
"""
Simple MCP server for DuckDuckGo search with Wikipedia fallback.
Handles 202 responses gracefully.
"""
import sys
import json
import requests
import re

def search_duckduckgo(query):
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("AbstractText"):
                return data["AbstractText"]
            elif data.get("Definition"):
                return data["Definition"]
            elif data.get("RelatedTopics"):
                for topic in data["RelatedTopics"]:
                    if "Text" in topic:
                        return topic["Text"]
            return None
        elif resp.status_code == 202:
            # DuckDuckGo redirects to a disambiguation page – fall back to Wikipedia
            return None
        else:
            return f"Search failed: {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"

def search_wikipedia(query):
    try:
        # Try the query as-is, then simplified
        for attempt in range(2):
            search_resp = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query, "format": "json"},
                timeout=10
            )
            if search_resp.status_code != 200:
                continue
            results = search_resp.json().get("query", {}).get("search", [])
            if results:
                title = results[0]["title"]
                summary_resp = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
                    timeout=10
                )
                if summary_resp.status_code == 200:
                    data = summary_resp.json()
                    if data.get("extract"):
                        return data["extract"]
            # Simplify query: remove common words
            if attempt == 0:
                simplified = re.sub(r'\b(what|is|the|of|capital|country|state|city)\b', '', query, flags=re.IGNORECASE)
                simplified = simplified.strip()
                if simplified and simplified != query:
                    query = simplified
                    continue
                else:
                    break
        return None
    except Exception as e:
        return None

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
                        "description": "Search the web (DuckDuckGo with Wikipedia fallback)",
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
            # Try DuckDuckGo
            result = search_duckduckgo(query)
            if not result:
                # Fall back to Wikipedia
                result = search_wikipedia(query)
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
