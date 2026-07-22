#!/usr/bin/env python3
"""
Mycelial Dashboard – Minimal sovereign UI for your agents.
No AGPL, no heavy frameworks. Just Flask + HTMX.
"""

import os
import json
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
BASE = os.path.expanduser("~/mycelial")

@app.route('/')
def index():
    """Dashboard home – shows agent status and health."""
    return render_template('index.html')

@app.route('/api/status')
def status():
    """Return agent status from state files."""
    agents = ['boss_agent', 'dgta_agent', 'security_agent', 'codingagent']
    status_data = {}
    for agent in agents:
        state_file = os.path.join(BASE, 'state', f'{agent}.json')
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                data = json.load(f)
                status_data[agent] = {
                    'last_task': data.get('last_task', 'Never'),
                    'last_run': data.get('last_run', 'Never'),
                    'errors': len(data.get('errors', []))
                }
        else:
            status_data[agent] = {'last_task': 'No state', 'last_run': 'N/A', 'errors': 0}
    return jsonify(status_data)

@app.route('/api/health')
def health():
    """Run Boss health check and return result."""
    result = subprocess.run(
        [os.path.join(BASE, 'agents', 'boss_agent', 'boss_agent.py'), '--task', 'health_check'],
        capture_output=True, text=True
    )
    return jsonify({'output': result.stdout, 'errors': result.stderr})

@app.route('/api/trigger', methods=['POST'])
def trigger():
    """Trigger a Boss task (e.g., fl_train, check_updates)."""
    task = request.json.get('task')
    if not task:
        return jsonify({'error': 'No task specified'}), 400
    result = subprocess.run(
        [os.path.join(BASE, 'agents', 'boss_agent', 'boss_agent.py'), '--task', task],
        capture_output=True, text=True
    )
    return jsonify({'output': result.stdout, 'errors': result.stderr})

@app.route('/api/trigger_with_args', methods=['POST'])
def trigger_with_args():
    """Trigger a Boss task with arguments."""
    task = request.json.get('task')
    args = request.json.get('args', [])
    if not task:
        return jsonify({'error': 'No task specified'}), 400
    cmd = [os.path.join(BASE, 'agents', 'boss_agent', 'boss_agent.py'), '--task', task]
    if args:
        cmd.extend(['--args'] + args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return jsonify({'output': result.stdout, 'errors': result.stderr})

@app.route('/api/search', methods=['POST'])
def search():
    """Trigger DGTA search."""
    query = request.json.get('query')
    if not query:
        return jsonify({'error': 'No query specified'}), 400
    result = subprocess.run(
        [os.path.join(BASE, 'agents', 'dgta_agent', 'dgta_agent.py'), '--task', 'search', '--args', query],
        capture_output=True, text=True
    )
    return jsonify({'output': result.stdout, 'errors': result.stderr})

@app.route('/api/knowledge')
def knowledge():
    """List recent knowledge/outcome files."""
    knowledge_dir = os.path.join(BASE, 'knowledge')
    files = []
    if os.path.exists(knowledge_dir):
        for f in sorted(os.listdir(knowledge_dir), reverse=True)[:20]:
            if f.endswith('.json'):
                path = os.path.join(knowledge_dir, f)
                with open(path, 'r') as fp:
                    try:
                        data = json.load(fp)
                        files.append({'name': f, 'data': data})
                    except:
                        files.append({'name': f, 'data': 'Corrupted'})
    return jsonify(files)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7001, debug=False)

# ---------- VAULT ----------
UPLOAD_FOLDER = os.path.join(BASE, "vault", "incoming")
PROCESSED_FOLDER = os.path.join(BASE, "vault", "processed")
ARCHIVE_FOLDER = os.path.join(BASE, "vault", "archive")
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'csv', 'txt', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/vault')
def vault():
    """Render the vault page."""
    return render_template('vault.html')

@app.route('/api/vault/list')
def vault_list():
    """List documents in the vault (all folders)."""
    docs = []
    for folder, status in [(UPLOAD_FOLDER, 'incoming'), (PROCESSED_FOLDER, 'processed'), (ARCHIVE_FOLDER, 'archive')]:
        if os.path.exists(folder):
            for f in os.listdir(folder):
                path = os.path.join(folder, f)
                docs.append({
                    'name': f,
                    'status': status,
                    'size': os.path.getsize(path),
                    'modified': datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
                })
    return jsonify(docs)

@app.route('/api/vault/upload', methods=['POST'])
def vault_upload():
    """Upload a document to the vault."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed'}), 400
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(path)
    # Log the upload
    with open(os.path.join(BASE, "logs", "audit.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | dashboard | uploaded {file.filename} to vault\n")
    return jsonify({'status': 'success', 'filename': file.filename, 'path': path})

@app.route('/api/vault/process/<filename>', methods=['POST'])
def vault_process(filename):
    """Move a document from incoming to processed and notify Legal Agent."""
    src = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(src):
        return jsonify({'error': 'File not found'}), 404
    dst = os.path.join(PROCESSED_FOLDER, filename)
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)
    os.rename(src, dst)
    # Notify Legal Agent (or Trustee) via a hook
    subprocess.run([
        os.path.join(BASE, "hooks", "vault_notify.sh"),
        filename
    ], capture_output=True)
    return jsonify({'status': 'processed', 'filename': filename})

@app.route('/api/vault/archive/<filename>', methods=['POST'])
def vault_archive(filename):
    """Move a document from processed to archive."""
    src = os.path.join(PROCESSED_FOLDER, filename)
    if not os.path.exists(src):
        return jsonify({'error': 'File not found'}), 404
    dst = os.path.join(ARCHIVE_FOLDER, filename)
    os.makedirs(ARCHIVE_FOLDER, exist_ok=True)
    os.rename(src, dst)
    return jsonify({'status': 'archived', 'filename': filename})

@app.route('/api/vault/delete/<filename>', methods=['DELETE'])
def vault_delete(filename):
    """Delete a document from the vault."""
    for folder in [UPLOAD_FOLDER, PROCESSED_FOLDER, ARCHIVE_FOLDER]:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            os.remove(path)
            return jsonify({'status': 'deleted', 'filename': filename})
    return jsonify({'error': 'File not found'}), 404
