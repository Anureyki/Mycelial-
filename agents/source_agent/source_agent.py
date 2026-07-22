#!/usr/bin/env python3
import sys
import os
import time
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class SourceAgentAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="source_agent",
            port=9005,
            capabilities=[],
            role="agent"
        )
        self.log("source_agent initialized.")

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")
        # Add your custom logic here
        return f"Task {task} executed by source_agent"

if __name__ == "__main__":
    agent = SourceAgentAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
