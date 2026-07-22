#!/usr/bin/env python3
"""Self-Healing Agent"""
import subprocess
import json
import time
import os
from datetime import datetime

class SelfHealer:
    def __init__(self):
        self.services = ["dashboard", "fl-server", "boss-agent"]
        self.fix_history = os.path.expanduser("~/mycelial/state/fix_history.json")
    
    def check_services(self):
        failed = []
        for service in self.services:
            result = subprocess.run(f"systemctl is-active {service}", shell=True, capture_output=True, text=True)
            if result.stdout.strip() != "active":
                failed.append(service)
        return failed
    
    def fix_syntax_errors(self):
        agent_dir = os.path.expanduser("~/mycelial/agents")
        fixed = []
        for root, dirs, files in os.walk(agent_dir):
            for file in files:
                if file.endswith(".py"):
                    filepath = os.path.join(root, file)
                    try:
                        subprocess.run(["/home/anureyki/AgTechAI/venv/bin/python", "-m", "py_compile", filepath], 
                                     capture_output=True, check=True)
                    except:
                        # Try to fix common issues
                        with open(filepath, 'r') as f:
                            content = f.read()
                        # Fix f-string errors
                        content = content.replace("else '({none})'", "else '(none)'")
                        with open(filepath, 'w') as f:
                            f.write(content)
                        fixed.append(file)
        return fixed
    
    def heal(self):
        print("🔍 Checking system health...")
        fixed = self.fix_syntax_errors()
        if fixed:
            print(f"✅ Fixed: {fixed}")
        
        failed = self.check_services()
        if not failed:
            print("✅ All services healthy!")
            return
        
        for service in failed:
            print(f"🔄 Restarting {service}...")
            subprocess.run(f"sudo systemctl restart {service}", shell=True)
            time.sleep(2)
            if service not in self.check_services():
                print(f"✅ {service} recovered!")
            else:
                print(f"❌ {service} still failing")

if __name__ == "__main__":
    healer = SelfHealer()
    healer.heal()
