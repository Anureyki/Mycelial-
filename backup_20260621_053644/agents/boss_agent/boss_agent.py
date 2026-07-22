#!/usr/bin/env python3
"""Boss Agent - Orchestrator, Router, Q-Learning"""
import os
import sys
import subprocess
import json
import argparse
from datetime import datetime

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} | boss | {msg}\n")

def health_check():
    log("Running health check...")
    return "✅ All systems healthy!"

def think(prompt):
    log(f"Thinking about: {prompt}")
    # Use DeepSeek for reasoning
    result = subprocess.run(
        ["ollama", "run", "deepseek-coder:6.7b", prompt],
        capture_output=True, text=True
    )
    return result.stdout

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--args", nargs="*")
    args = parser.parse_args()
    
    if args.task == "health_check":
        print(health_check())
    elif args.task == "think":
        if args.args:
            print(think(" ".join(args.args)))
        else:
            print("❌ No prompt provided")
    elif args.task == "ag_dqn_decision":
        # Delegate to AG Agent
        ag_script = os.path.join(BASE, "agents", "ag_agent", "agriculture_agent.py")
        result = subprocess.run(
            [sys.executable, ag_script, "--task", "dqn_decide"],
            capture_output=True, text=True
        )
        print(result.stdout)
    elif args.task == "ag_dqn_train":
        ag_script = os.path.join(BASE, "agents", "ag_agent", "agriculture_agent.py")
        result = subprocess.run(
            [sys.executable, ag_script, "--task", "dqn_train"],
            capture_output=True, text=True
        )
        print(result.stdout)
    else:
        print(f"❌ Unknown task: {args.task}")

if __name__ == "__main__":
    main()
