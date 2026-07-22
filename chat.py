#!/usr/bin/env python3
"""Chat with Boss Agent - Uses DeepSeek/Qwen locally"""
import subprocess
import sys
import json
import readline  # For arrow keys, history

def ask_boss(prompt):
    """Send prompt to Boss Agent with local LLM"""
    try:
        # Use Boss's reasoning with DeepSeek
        result = subprocess.run(
            ["/home/anureyki/AgTechAI/venv/bin/python", 
             "/home/anureyki/mycelial/agents/boss_agent/boss_agent.py",
             "--task", "think",
             "--args", prompt],
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "⏰ Boss is thinking too long... try again"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def chat_loop():
    print("\n🍄 Mycelial Network Chat")
    print("=" * 40)
    print("Talk to Boss Agent (uses DeepSeek locally)")
    print("Commands: /exit, /health, /agents, /help")
    print("=" * 40)
    
    while True:
        try:
            user_input = input("\n🧑 You: ").strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ["/exit", "/quit", "exit", "quit"]:
                print("👋 Goodbye!")
                break
                
            if user_input.lower() == "/health":
                print("🏥 Running health check...")
                result = subprocess.run(
                    ["/home/anureyki/mycelial/agents/boss_agent/boss_agent.py", "--task", "health_check"],
                    capture_output=True,
                    text=True
                )
                print(result.stdout)
                continue
                
            if user_input.lower() == "/agents":
                print("🤖 Available Agents:")
                agents = ["boss_agent", "codingagent", "security_agent", "dgta_agent", "pqa_agent"]
                for a in agents:
                    print(f"  - {a}")
                continue
                
            print("\n🧠 Boss: ", end="")
            response = ask_boss(user_input)
            print(response)
            
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    chat_loop()
