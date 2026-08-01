#!/usr/bin/env python3
"""
CourtListener MCP server (stdio) - case law / RECAP docket search and monitoring.

Auth: set COURTLISTENER_API_TOKEN (either in the shell environment that starts
the Tool Service, or in config/mcp.d/courtlistener.json under "env") to a
CourtListener API token (https://www.courtlistener.com/help/api/rest/#authentication).
Search works without a token (public, rate-limited); creating alerts or
docket-alert subscriptions requires one.
"""
import sys
import os
import json
import requests

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
API_TOKEN = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()

NO_TOKEN_ERROR = (
    "COURTLISTENER_API_TOKEN is not set; this action requires an authenticated "
    "CourtListener account token. See "
    "https://www.courtlistener.com/help/api/rest/#authentication"
)


def _headers():
    headers = {"User-Agent": "mycelial-legal-agent/1.0"}
    if API_TOKEN:
        headers["Authorization"] = f"Token {API_TOKEN}"
    return headers


def search(arguments):
    query = arguments.get("q") or arguments.get("query")
    if not query:
        return {"error": "Missing required 'q' (search query)"}
    params = {"q": query, "type": arguments.get("type") or "r"}
    for key in ("court", "case_name", "party_name", "docket_number",
                "order_by", "filed_after", "filed_before"):
        if arguments.get(key):
            params[key] = arguments[key]
    try:
        resp = requests.get(f"{BASE_URL}/search/", params=params, headers=_headers(), timeout=20)
        if resp.status_code != 200:
            return {"error": f"CourtListener search failed: HTTP {resp.status_code}", "detail": resp.text[:500]}
        data = resp.json()
        results = data.get("results", [])
        return {
            "count": data.get("count", len(results)),
            "results": [
                {
                    "caseName": r.get("caseName"),
                    "court": r.get("court"),
                    "dateFiled": r.get("dateFiled"),
                    "docketNumber": r.get("docketNumber"),
                    "docket_id": r.get("docket_id"),
                    "absolute_url": f"https://www.courtlistener.com{r['absolute_url']}" if r.get("absolute_url") else None,
                    "snippet": (r.get("snippet") or (r.get("text", "")[:300] if r.get("text") else None)),
                }
                for r in results
            ]
        }
    except Exception as e:
        return {"error": str(e)}


def create_alert(arguments):
    if not API_TOKEN:
        return {"error": NO_TOKEN_ERROR}
    name = arguments.get("name")
    query = arguments.get("query")
    rate = arguments.get("rate") or "dly"
    if not name or not query:
        return {"error": "Missing required 'name' and/or 'query'"}
    alert_type = arguments.get("alert_type") or (query.get("type") if isinstance(query, dict) else None) or "r"
    query_string = query if isinstance(query, str) else "&".join(
        f"{k}={v}" for k, v in query.items() if v
    )
    try:
        resp = requests.post(
            f"{BASE_URL}/alerts/",
            json={"name": name, "query": query_string, "rate": rate, "alert_type": alert_type},
            headers=_headers(), timeout=20
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Failed to create alert: HTTP {resp.status_code}", "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def subscribe_docket_alert(arguments):
    if not API_TOKEN:
        return {"error": NO_TOKEN_ERROR}
    docket = arguments.get("docket")
    if not docket:
        return {"error": "Missing required 'docket' (docket ID)"}
    try:
        resp = requests.post(
            f"{BASE_URL}/docket-alerts/",
            json={"docket": docket},
            headers=_headers(), timeout=20
        )
        if resp.status_code not in (200, 201):
            return {"error": f"Failed to subscribe to docket alert: HTTP {resp.status_code}", "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


TOOLS = {
    "search": {
        "description": "Search CourtListener case law, RECAP dockets/filings, or oral arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query, e.g. a person's or party's name"},
                "type": {"type": "string", "description": "o=opinion, r=RECAP docket, rd=RECAP document, d=docket, oa=oral argument. Defaults to 'r'."},
                "court": {"type": "string"},
                "case_name": {"type": "string"},
                "party_name": {"type": "string"},
                "docket_number": {"type": "string"},
                "order_by": {"type": "string"},
                "filed_after": {"type": "string"},
                "filed_before": {"type": "string"},
            },
            "required": ["q"],
        },
        "handler": search,
    },
    "create_alert": {
        "description": "Create a recurring CourtListener search alert (requires COURTLISTENER_API_TOKEN).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "query": {"description": "URL query string or object of search params, e.g. {\"type\": \"r\", \"party_name\": \"Jane Doe\"}"},
                "rate": {"type": "string", "description": "rt|dly|wly|mly|off, default dly"},
                "alert_type": {"type": "string", "description": "o|r|d|oa - defaults to the query's 'type', or 'r'"},
            },
            "required": ["name", "query"],
        },
        "handler": create_alert,
    },
    "subscribe_docket_alert": {
        "description": "Subscribe to alerts for a specific docket ID (requires COURTLISTENER_API_TOKEN).",
        "inputSchema": {
            "type": "object",
            "properties": {"docket": {"type": "integer"}},
            "required": ["docket"],
        },
        "handler": subscribe_docket_alert,
    },
}


def handle_request(req):
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "courtlistener", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": [
                {"name": name, "description": t["description"], "inputSchema": t["inputSchema"]}
                for name, t in TOOLS.items()
            ]},
        }
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        tool = TOOLS.get(tool_name)
        if not tool:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        result = tool["handler"](arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
            resp = handle_request(req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
