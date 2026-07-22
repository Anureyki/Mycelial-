#!/usr/bin/env python3
"""Mycelial Network Dashboard"""
import gradio as gr
import subprocess

def health_check():
    try:
        result = subprocess.run(
            ["/home/anureyki/mycelial/agents/boss_agent/boss_agent.py", "--task", "health_check"],
            capture_output=True,
            text=True
        )
        return f"✅ System Health:\n{result.stdout}"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def run_agent(agent, task):
    try:
        cmd = f"/home/anureyki/mycelial/agents/{agent}/{agent}.py --task {task}"
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        return f"📊 Output:\n{result.stdout}\n{result.stderr}"
    except Exception as e:
        return f"❌ Error: {str(e)}"

with gr.Blocks(title="Mycelial Network") as demo:
    gr.Markdown("# 🍄 Mycelial Network Dashboard")
    gr.Markdown("## Sovereign AI Ecosystem – Privacy First")
    
    with gr.Tab("Health"):
        health_btn = gr.Button("Run Health Check")
        health_out = gr.Textbox(label="Status", lines=10)
        health_btn.click(health_check, outputs=health_out)
    
    with gr.Tab("Agent Control"):
        agent_dropdown = gr.Dropdown(
            choices=["boss_agent", "codingagent", "security_agent", "dgta_agent", "pqa_agent"],
            label="Select Agent"
        )
        task_input = gr.Textbox(label="Task", placeholder="e.g., health_check")
        run_btn = gr.Button("Run Agent")
        agent_out = gr.Textbox(label="Output", lines=10)
        run_btn.click(run_agent, inputs=[agent_dropdown, task_input], outputs=agent_out)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7001)
