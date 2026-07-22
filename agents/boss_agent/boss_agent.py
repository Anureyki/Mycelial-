#!/usr/bin/env python3
import sys
import os
import time
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class BossAgentAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="boss_agent",
            port=9002,
            capabilities=[],
            role="agent"
        )
        self.log("boss_agent initialized.")

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")
        # Add your custom logic here
        return f"Task {task} executed by boss_agent"

if __name__ == "__main__":
    agent = BossAgentAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
