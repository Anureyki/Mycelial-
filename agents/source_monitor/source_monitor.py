#!/usr/bin/env python3
import sys
import os
import time
from datetime import datetime

# Add project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

class SourceMonitorAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="source_monitor",
            port=9004,
            capabilities=[],
            role="agent"
        )
        self.log("source_monitor initialized.")

    def handle_task(self, task, args, sender):
        self.log(f"Task {task} from {sender}")
        # Add your custom logic here
        return f"Task {task} executed by source_monitor"

if __name__ == "__main__":
    agent = SourceMonitorAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
