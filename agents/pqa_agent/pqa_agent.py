#!/usr/bin/env python3
import sys
import os
import time
import json

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class PqaAgent(AgentBase):
    # Words that claim a request for this agent. Declared here, not in
    # Boss - the orchestrator holds no domain vocabulary.
    ROUTING_TERMS = (
        "search", "look ?up", "google", "web search", "find me",
        "what does the internet", "latest news", "current price",
    )

    def __init__(self):
        super().__init__(
            agent_id="pqa_agent",
            port=9007,
            capabilities=["search_web", "search", "browse", "screenshot"],
            role="public_query"
        )
        self.log("🔍 PQA started with SearXNG + Puppeteer.")

    def handle_task(self, task, args, sender):
        self.log(f"Task: {task} from {sender}")

        if task == "search" or task == "search_web":
            query = args.get("query") if isinstance(args, dict) else args[0] if args else None
            if not query:
                return {"error": "Missing query"}

            self.log(f"Searching via SearXNG: {query}")
            result = self.call_tool("searxng", "search", {"query": query})

            try:
                self.store_own_memory(f"search_{int(time.time())}", {"query": query, "result": result})
            except Exception as e:
                self.log(f"Memory store failed: {e}")

            return {"result": result, "query": query}

        elif task == "browse" or task == "fetch_page":
            url = args.get("url") if isinstance(args, dict) else args[0] if args else None
            if not url:
                return {"error": "Missing URL"}

            self.log(f"Navigating to: {url}")
            nav_result = self.call_tool("puppeteer", "puppeteer_navigate", {"url": url})
            screenshot = self.call_tool("puppeteer", "puppeteer_screenshot", {})

            try:
                self.store_own_memory(f"browse_{int(time.time())}", {"url": url, "result": nav_result})
            except Exception as e:
                self.log(f"Memory store failed: {e}")

            return {
                "navigation": nav_result,
                "screenshot": screenshot,
                "url": url
            }

        else:
            return {"error": f"Unknown task: {task}"}

if __name__ == "__main__":
    agent = PqaAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
