#!/usr/bin/env python3
"""Source Health Dashboard - Shows all trusted sources and their status"""
import gradio as gr
import subprocess
import json
import os
from datetime import datetime

def get_latest_report():
    state_dir = os.path.expanduser("~/mycelial/state/source_monitor")
    if not os.path.exists(state_dir):
        return None
    
    reports = [f for f in os.listdir(state_dir) if f.startswith("report_")]
    if not reports:
        return None
    
    latest = sorted(reports)[-1]
    with open(os.path.join(state_dir, latest), 'r') as f:
        return json.load(f)

def display_sources():
    report = get_latest_report()
    if not report:
        return "❌ No source reports found. Run source monitor first."
    
    html = f"<h2>📡 Source Health Report</h2>"
    html += f"<p><strong>Last updated:</strong> {report['timestamp']}</p>"
    html += "<table border='1' style='width:100%'>"
    html += "<tr><th>Source</th><th>Status</th><th>URL</th></tr>"
    
    for source in report['sources']:
        status = source['status']
        emoji = "✅" if status == "healthy" else "⚠️" if status == "updated" else "❌"
        html += f"<tr><td>{source['name']}</td><td>{emoji} {status}</td><td>{source['url'][:50]}...</td></tr>"
    
    html += "</table>"
    return html

def run_monitor():
    result = subprocess.run(
        ["~/mycelial/agents/source_monitor.py", "--task", "monitor"],
        capture_output=True, text=True, shell=True
    )
    if result.returncode == 0:
        return "✅ Source monitor completed!\n\n" + display_sources()
    else:
        return f"❌ Error running monitor:\n{result.stderr}"

# Gradio UI
with gr.Blocks(title="Source Monitor") as demo:
    gr.Markdown("# 📡 Trusted Source Monitor")
    gr.Markdown("## Track health and freshness of all trusted sources")
    
    with gr.Row():
        refresh_btn = gr.Button("🔄 Refresh Sources")
        monitor_btn = gr.Button("🔍 Run Full Monitor")
    
    status_display = gr.HTML(value=display_sources())
    
    refresh_btn.click(display_sources, outputs=status_display)
    monitor_btn.click(run_monitor, outputs=status_display)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7002)
