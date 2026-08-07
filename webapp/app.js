const log = document.getElementById('log');
const form = document.getElementById('composer');
const promptInput = document.getElementById('promptInput');
const sendBtn = document.getElementById('sendBtn');
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const serverUrlInput = document.getElementById('serverUrl');
const saveSettingsBtn = document.getElementById('saveSettings');
const chatTabBtn = document.getElementById('chatTabBtn');
const dashboardTabBtn = document.getElementById('dashboardTabBtn');
const dashboard = document.getElementById('dashboard');
const refreshDashboardBtn = document.getElementById('refreshDashboard');

const STORAGE_KEY = 'mycelial.serverUrl';

function getServerUrl() {
  return localStorage.getItem(STORAGE_KEY) || '';
}

function setServerUrl(url) {
  localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ''));
}

function addMessage(text, cls) {
  const el = document.createElement('div');
  el.className = `msg ${cls}`;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function extractResult(payload) {
  let node = payload;
  for (let i = 0; i < 6 && node && typeof node === 'object'; i++) {
    if (typeof node.result === 'string') return node.result;
    if (node.result && typeof node.result === 'object') { node = node.result; continue; }
    break;
  }
  return JSON.stringify(payload);
}

async function sendPrompt(text) {
  const serverUrl = getServerUrl();
  if (!serverUrl) {
    addMessage('No server URL set. Open settings (gear icon) and set your Anansi server URL first.', 'error');
    openSettings();
    return;
  }

  addMessage(text, 'user');
  const pending = addMessage('...', 'pending');
  sendBtn.disabled = true;

  try {
    const res = await fetch(`${serverUrl}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: 'process_request', args: [text] }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pending.remove();
    addMessage(extractResult(data), 'agent');
  } catch (err) {
    pending.remove();
    addMessage(`Request failed: ${err.message}`, 'error');
  } finally {
    sendBtn.disabled = false;
  }
}

async function fetchNarration(promptText) {
  const serverUrl = getServerUrl();
  if (!serverUrl) return 'No server URL set - open settings first.';
  try {
    const res = await fetch(`${serverUrl}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: 'process_request', args: [promptText] }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return extractResult(data);
  } catch (err) {
    return `Couldn't load this right now (${err.message}).`;
  }
}

async function refreshDashboard() {
  const cards = [
    { id: 'systemCard', prompt: 'system status' },
    { id: 'growCard', prompt: 'how is my plant' },
    { id: 'progressCard', prompt: 'catch me up on progress' },
  ];
  for (const { id, prompt } of cards) {
    const body = document.querySelector(`#${id} .card-body`);
    if (body) body.textContent = 'Loading...';
  }
  // Sequential, not parallel - these route through the same shared local
  // inference backend, so firing all three at once would just make them
  // queue behind each other anyway.
  for (const { id, prompt } of cards) {
    const body = document.querySelector(`#${id} .card-body`);
    const text = await fetchNarration(prompt);
    if (body) body.textContent = text;
  }
}

function showChat() {
  chatTabBtn.classList.add('active');
  dashboardTabBtn.classList.remove('active');
  log.classList.remove('hidden');
  dashboard.classList.add('hidden');
  form.classList.remove('hidden');
}

function showDashboard() {
  dashboardTabBtn.classList.add('active');
  chatTabBtn.classList.remove('active');
  dashboard.classList.remove('hidden');
  log.classList.add('hidden');
  form.classList.add('hidden');
  refreshDashboard();
}

chatTabBtn.addEventListener('click', showChat);
dashboardTabBtn.addEventListener('click', showDashboard);
refreshDashboardBtn.addEventListener('click', refreshDashboard);

function openSettings() {
  serverUrlInput.value = getServerUrl();
  settingsPanel.classList.remove('hidden');
}

settingsBtn.addEventListener('click', () => {
  settingsPanel.classList.toggle('hidden');
  if (!settingsPanel.classList.contains('hidden')) {
    serverUrlInput.value = getServerUrl();
  }
});

saveSettingsBtn.addEventListener('click', () => {
  const url = serverUrlInput.value.trim();
  if (!url) return;
  setServerUrl(url);
  settingsPanel.classList.add('hidden');
  addMessage(`Server set to ${url}`, 'agent');
});

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = promptInput.value.trim();
  if (!text) return;
  promptInput.value = '';
  promptInput.style.height = 'auto';
  sendPrompt(text);
});

promptInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

promptInput.addEventListener('input', () => {
  promptInput.style.height = 'auto';
  promptInput.style.height = `${promptInput.scrollHeight}px`;
});

if (!getServerUrl()) {
  openSettings();
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('service-worker.js').catch(() => {});
  });
}
