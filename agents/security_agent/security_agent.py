#!/usr/bin/env python3
import sys
import os
import time
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class SecurityAgentAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="security_agent",
            port=9003,
            capabilities=[],
            role="agent"
        )
        self.log("security_agent initialized.")

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")
        # Add your custom logic here
        return f"Task {task} executed by security_agent"

if __name__ == "__main__":
    agent = SecurityAgentAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
